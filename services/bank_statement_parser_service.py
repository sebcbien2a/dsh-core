"""Parse bank statements (CSV / raw JSON rows) into normalized reconciliation lines (P13.2).

Every produced line has: ``line_id``, ``operation_date`` (ISO), ``label``,
``debit_amount`` / ``credit_amount`` (2-decimal strings, exactly one non-zero),
``currency`` (ISO-4217 shape, default EUR), ``counterparty`` and ``status``.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_ROWS = 20_000
MAX_BYTES = 5 * 1024 * 1024
MAX_LABEL_LENGTH = 500
DEFAULT_CURRENCY = "EUR"
ALLOWED_DELIMITERS = frozenset({";", ",", "\t", "|"})

_CENT = Decimal("0.01")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_FR_DATE = re.compile(r"\d{2}/\d{2}/\d{4}")
_CURRENCY = re.compile(r"[A-Z]{3}")
_LINE_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_AMOUNT = re.compile(r"[+-]?(\d+([.,]\d+)*|[.,]\d+)")

_ALIASES: dict[str, tuple[str, ...]] = {
    "operation_date": ("date", "dateoperation", "dateop", "operationdate", "datecomptable"),
    "label": ("libelle", "label", "description", "intitule", "libelleoperation", "motif"),
    "debit": ("debit", "debitamount", "montantdebit", "sortie"),
    "credit": ("credit", "creditamount", "montantcredit", "entree"),
    "amount": ("montant", "amount", "montantsigne"),
    "counterparty": ("tiers", "contrepartie", "counterparty", "beneficiaire", "partie"),
    "currency": ("devise", "currency", "monnaie"),
    "line_id": ("lineid", "id", "idoperation", "operationid"),
}
_ALIAS_TO_FIELD = {alias: field for field, names in _ALIASES.items() for alias in names}


class BankStatementError(ValueError):
    """Invalid statement; ``code`` is stable, ``row`` is the 1-based data row if known."""

    def __init__(self, code: str, message: str = "", *, row: int | None = None) -> None:
        self.code = code
        self.row = row
        parts = [code] + ([f"row {row}"] if row is not None else []) + ([message] if message else [])
        super().__init__(": ".join(parts))


def _normalize_header(name: object) -> str:
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def _clean(value: object) -> str:
    return "" if value is None else str(value).replace("\u00a0", " ").strip()


def parse_date(value: object, *, row: int | None = None) -> str:
    """Accept ``YYYY-MM-DD`` or ``DD/MM/YYYY`` only; return the ISO date."""
    text = _clean(value)
    try:
        if _ISO_DATE.fullmatch(text):
            return date.fromisoformat(text).isoformat()
        if _FR_DATE.fullmatch(text):
            return datetime.strptime(text, "%d/%m/%Y").date().isoformat()
    except ValueError:
        pass
    raise BankStatementError("INVALID_DATE", repr(text[:40]), row=row)


def parse_amount(value: object, *, row: int | None = None) -> Decimal | None:
    """Parse ``1234.56``, ``1 234,56``, ``1.234,56`` or ``1,234.56``; empty gives ``None``."""
    text = re.sub(r"[\s']", "", _clean(value))
    if not text:
        return None
    if not _AMOUNT.fullmatch(text):
        raise BankStatementError("INVALID_AMOUNT", repr(text[:40]), row=row)
    sign = "-" if text.startswith("-") else ""
    text = text.lstrip("+-")
    integer, decimal_part = text, ""
    seps = [ch for ch in text if ch in ",."]
    if seps:
        last = max(text.rfind(","), text.rfind("."))
        decimal_char = text[last]
        if len(set(seps)) == 2 or len(seps) == 1:
            # the last separator is the decimal one and must be unique
            if seps.count(decimal_char) != 1:
                raise BankStatementError("INVALID_AMOUNT", repr(text[:40]), row=row)
            integer, decimal_part = text[:last], text[last + 1 :]
        elif len(text) - last - 1 != 3:
            # a repeated single separator is thousands grouping only
            raise BankStatementError("INVALID_AMOUNT", repr(text[:40]), row=row)
        integer = re.sub(r"[,.]", "", integer)
    if len(decimal_part) > 2:
        raise BankStatementError("INVALID_AMOUNT", "more than 2 decimals", row=row)
    try:
        amount = Decimal(f"{sign}{integer or '0'}.{decimal_part or '0'}")
    except InvalidOperation as exc:
        raise BankStatementError("INVALID_AMOUNT", repr(text[:40]), row=row) from exc
    return amount.quantize(_CENT)


def _make_line_id(
    tenant_id: str, line: Mapping[str, Any], occurrence: int
) -> str:
    material = "\x1f".join(
        (
            tenant_id,
            line["operation_date"],
            line["debit_amount"],
            line["credit_amount"],
            line["currency"],
            line["label"],
            str(occurrence),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def normalize_bank_rows(
    rows: Iterable[Mapping[str, Any]], tenant_id: str = ""
) -> list[dict[str, Any]]:
    """Normalize raw rows (any supported header spelling) into reconciliation lines.

    A missing ``line_id`` is the SHA-256 of tenant, date, amounts, currency, label
    and the occurrence rank of that otherwise identical operation in the batch, so
    re-importing the same statement yields the same ids while genuinely repeated
    operations stay distinct.
    """
    lines: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    occurrences: Counter[tuple[str, ...]] = Counter()
    for index, raw in enumerate(rows, start=1):
        if index > MAX_ROWS:
            raise BankStatementError("TOO_MANY_ROWS", f"max {MAX_ROWS}")
        if not isinstance(raw, Mapping):
            raise BankStatementError("INVALID_ROW", "row must be an object", row=index)
        fields: dict[str, str] = {}
        for key, value in raw.items():
            if key is None:
                if _clean(value if not isinstance(value, list) else "".join(map(str, value))):
                    raise BankStatementError("EXTRA_COLUMNS", row=index)
                continue
            field = _ALIAS_TO_FIELD.get(_normalize_header(key))
            if field is None:
                continue
            if field in fields:
                raise BankStatementError("DUPLICATE_COLUMN", field, row=index)
            fields[field] = _clean(value)
        if not any(fields.values()):
            continue

        operation_date = parse_date(fields.get("operation_date"), row=index)
        label = fields.get("label", "")
        if not label:
            raise BankStatementError("MISSING_LABEL", row=index)
        if len(label) > MAX_LABEL_LENGTH or "\x00" in label:
            raise BankStatementError("INVALID_LABEL", row=index)

        debit = parse_amount(fields.get("debit"), row=index)
        credit = parse_amount(fields.get("credit"), row=index)
        signed = parse_amount(fields.get("amount"), row=index)
        if signed is not None:
            if debit or credit:
                raise BankStatementError("AMBIGUOUS_AMOUNT", row=index)
            debit, credit = (-signed, None) if signed < 0 else (None, signed)
        if (debit is not None and debit < 0) or (credit is not None and credit < 0):
            raise BankStatementError("NEGATIVE_AMOUNT", row=index)
        debit, credit = debit or Decimal("0.00"), credit or Decimal("0.00")
        if bool(debit) == bool(credit):
            raise BankStatementError(
                "INVALID_AMOUNT", "exactly one of debit/credit must be non-zero", row=index
            )

        currency = (fields.get("currency") or DEFAULT_CURRENCY).upper()
        if not _CURRENCY.fullmatch(currency):
            raise BankStatementError("INVALID_CURRENCY", repr(currency[:10]), row=index)

        line: dict[str, Any] = {
            "operation_date": operation_date,
            "label": label,
            "debit_amount": str(debit),
            "credit_amount": str(credit),
            "currency": currency,
            "counterparty": fields.get("counterparty") or None,
            "status": "PENDING",
        }
        line_id = fields.get("line_id", "")
        if line_id:
            if not _LINE_ID.fullmatch(line_id):
                raise BankStatementError("INVALID_LINE_ID", repr(line_id[:40]), row=index)
        else:
            key = (
                operation_date, line["debit_amount"], line["credit_amount"], currency, label
            )
            occurrences[key] += 1
            line_id = _make_line_id(tenant_id, line, occurrences[key])
        if line_id in seen_ids:
            raise BankStatementError("DUPLICATE_LINE_ID", line_id, row=index)
        seen_ids.add(line_id)
        line["line_id"] = line_id
        lines.append(line)
    return lines


def _decode(content: str | bytes) -> str:
    if isinstance(content, str):
        return content
    if len(content) > MAX_BYTES:
        raise BankStatementError("FILE_TOO_LARGE", f"max {MAX_BYTES} bytes")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("cp1252", errors="replace")


def parse_bank_statement_csv(
    content: str | bytes, delimiter: str = ";", *, tenant_id: str = ""
) -> list[dict[str, Any]]:
    """Parse a delimited bank statement with a header row into normalized lines."""
    if delimiter not in ALLOWED_DELIMITERS:
        raise BankStatementError("INVALID_DELIMITER", repr(delimiter))
    text = _decode(content)
    if len(text) > MAX_BYTES:
        raise BankStatementError("FILE_TOO_LARGE", f"max {MAX_BYTES} characters")
    if "\x00" in text:
        raise BankStatementError("INVALID_CONTENT", "NUL byte")
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    try:
        headers = reader.fieldnames
        if not headers:
            raise BankStatementError("EMPTY_STATEMENT")
        known = {_ALIAS_TO_FIELD.get(_normalize_header(h)) for h in headers}
        if "operation_date" not in known or "label" not in known:
            raise BankStatementError("MISSING_COLUMNS", "date and label columns are required")
        if not ({"debit", "credit", "amount"} & known):
            raise BankStatementError("MISSING_COLUMNS", "debit/credit or amount column required")
        return normalize_bank_rows(reader, tenant_id)
    except csv.Error as exc:
        raise BankStatementError("MALFORMED_CSV", str(exc)) from exc
