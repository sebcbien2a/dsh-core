"""P19 - Caisse d'Epargne LCR remittance slip parser and 1-to-N reconciler.

A ``Bordereau d'effets a l'encaissement`` groups several customer promissory
notes (LCR) under a single bank credit. The parser extracts the transmission
reference, the account, the operation count, the declared credit total and the
detailed drawer lines. :func:`reconcile_ofx_remittance` then links the global
OFX ``REMISE ENCAISSEMENT`` line to the slip and splits it into exact customer
allocations, which removes any combinatorial ambiguity when matching invoices.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from schemas.folder_state_schema import DocumentCategory
from schemas.payment_method_schema import BankPaymentMethod
from services.ofx_bank_statement_parser import (
    OfxTransaction,
    classify_ofx_line,
    parse_ofx_amount,
    parse_ofx_date,
)

DEFAULT_CURRENCY = "EUR"
ACCEPTANCE_CODES: tuple[str, ...] = ("ACC", "NAC", "BOR")
PAYMENT_METHOD_LCR_ENCAISSEMENT_LOT = BankPaymentMethod.LCR_ENCAISSEMENT_LOT.value
CATEGORY_VENTES_CLIENTS = DocumentCategory.VENTES_CLIENTS.value
_CENT = Decimal("0.01")

_ACCEPTANCE = re.compile(r"(?<!\S)(ACC|NAC|BOR)(?!\S)", re.IGNORECASE)
_DATE_FULL = re.compile(r"(?<!\d)(\d{2})[/.-](\d{2})[/.-](\d{4})(?!\d)")
_DATE_SHORT = re.compile(r"(?<!\d)(\d{2})[/.-](\d{2})[/.-](\d{2})(?!\d)")
_DATE_COMPACT = re.compile(r"(?<!\d)(\d{8})(?!\d)")
_AMOUNT_AT_END = re.compile(
    r"(?P<amount>[+-]?\d{1,3}(?:[ \u00a0\u202f.]\d{3})*(?:,\d{2})"
    r"|[+-]?\d+[.,]\d{2})\s*(?:EUR|EUROS|€)?\s*$",
    re.IGNORECASE,
)
_AMOUNT_TOKEN = re.compile(
    r"(?<![\w])[+-]?\d{1,3}(?:[ \u00a0\u202f.]\d{3})*(?:,\d{1,2})?"
    r"|(?<![\w])[+-]?\d+[.,]\d{1,2}(?![\w])"
)
_RANK_TOKEN = re.compile(r"(?<!\S)(\d{1,3})(?!\S)")
_TRANSMISSION = re.compile(
    r"(?:reference\s*(?:de\s*)?transmission|transmission)"
    r"[^\d]{0,24}(\d{8,})",
    re.IGNORECASE,
)
_ACCOUNT = re.compile(
    r"(?:compte|numero\s*de\s*compte|n\s*de\s*compte)"
    r"[^\d]{0,24}((?:\d[ \u00a0]?){11,})",
    re.IGNORECASE,
)
_OPERATION_COUNT = re.compile(
    r"(?:nombre\s*(?:d\s*)?operations?|nb\s*operations?)"
    r"[^\d]{0,12}(\d+)",
    re.IGNORECASE,
)
_OPERATION_COUNT_REVERSED = re.compile(
    r"(\d+)\s*operations?\b", re.IGNORECASE
)
_TOTAL_CREDIT = re.compile(
    r"(?:montant\s*total\s*au\s*credit|total\s*au\s*credit|"
    r"montant\s*total|total\s*credit)",
    re.IGNORECASE,
)


class LcrRemittanceError(ValueError):
    """Invalid remittance slip or reconciliation; ``code`` is stable."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


class LcrReconciliationError(LcrRemittanceError):
    """The OFX line cannot be reconciled with the remittance slip."""


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("\u2019", " ").replace("'", " ")
    return re.sub(r"\s+", " ", text).strip().upper()


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()


def _decode_text(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("cp1252", "replace")


def _coerce_text(value: str | bytes | Path) -> str:
    if isinstance(value, Path):
        return _coerce_text(value.read_bytes())
    if isinstance(value, str):
        return value.lstrip("\ufeff")
    if isinstance(value, (bytes, bytearray)):
        payload = bytes(value)
        if payload.startswith(b"%PDF"):
            try:
                import fitz
            except ImportError as exc:
                raise LcrRemittanceError("PDF_SUPPORT_UNAVAILABLE") from exc
            try:
                with fitz.open(stream=payload, filetype="pdf") as document:
                    return "\n".join(page.get_text("text") for page in document)
            except Exception as exc:  # noqa: BLE001 - normalised error
                raise LcrRemittanceError("PDF_EXTRACTION_FAILED", str(exc)) from exc
        return _decode_text(payload)
    raise LcrRemittanceError("INVALID_CONTENT", type(value).__name__)


def _parse_due_date(value: str) -> date | None:
    match = _DATE_FULL.search(value)
    if match is not None:
        day, month, year = (int(part) for part in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    match = _DATE_SHORT.search(value)
    if match is not None:
        day, month, year = (int(part) for part in match.groups())
        try:
            return date(2000 + year if year < 90 else 1900 + year, month, day)
        except ValueError:
            return None
    match = _DATE_COMPACT.search(value)
    if match is not None:
        try:
            return datetime.strptime(match.group(1), "%Y%m%d").date()
        except ValueError:
            return None
    return None


def _amount_after(folded: str, pattern: re.Pattern[str]) -> Decimal | None:
    match = pattern.search(folded)
    if match is None:
        return None
    tail = folded[match.end() : match.end() + 80]
    amount_match = _AMOUNT_TOKEN.search(tail)
    if amount_match is None:
        return None
    try:
        return parse_ofx_amount(amount_match.group(0))
    except Exception:  # noqa: BLE001 - absent/invalid amount is optional
        return None


@dataclass(frozen=True)
class LcrRemittanceLine:
    """One drawer / promissory note row of the remittance slip."""

    drawer: str
    rank: int | None
    domiciliation: str
    acceptance: str
    due_date: date | None
    amount: Decimal
    raw: str = field(default="", compare=False, repr=False)

    @property
    def credit_amount(self) -> Decimal:
        return self.amount

    @property
    def tire(self) -> str:
        return self.drawer

    @property
    def client(self) -> str:
        return self.drawer

    @property
    def date_echeance(self) -> date | None:
        return self.due_date

    @property
    def montant_credit(self) -> Decimal:
        return self.amount

    def as_reconciliation_line(
        self,
        *,
        fitid: str = "",
        remittance_reference: str = "",
        operation_date: date | None = None,
        currency: str = DEFAULT_CURRENCY,
    ) -> dict[str, Any]:
        rank_text = f"{self.rank:02d}" if self.rank is not None else "NA"
        base = fitid or remittance_reference or "LCR"
        return {
            "line_id": f"{base}:LCR:{rank_text}",
            "operation_date": (operation_date or self.due_date).isoformat()
            if (operation_date or self.due_date)
            else None,
            "label": (
                f"LCR {self.drawer} {self.acceptance}"
                + (f" ECHEANCE {self.due_date.isoformat()}" if self.due_date else "")
            ),
            "debit_amount": None,
            "credit_amount": str(self.amount),
            "currency": currency,
            "counterparty": self.drawer,
            "category": CATEGORY_VENTES_CLIENTS,
            "payment_method": PAYMENT_METHOD_LCR_ENCAISSEMENT_LOT,
            "payment_reference": (
                f"{remittance_reference}:{rank_text}"
                if remittance_reference
                else rank_text
            ),
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "remittance_reference": remittance_reference or None,
            "document_type": "LCR_ALLOCATION",
            "status": "PENDING",
        }


@dataclass(frozen=True)
class LcrRemittance:
    """A parsed ``Bordereau d'effets a l'encaissement``."""

    transmission_reference: str
    account_number: str
    operation_count: int | None
    total_amount: Decimal | None
    currency: str
    lines: tuple[LcrRemittanceLine, ...]
    raw_text: str = field(default="", compare=False, repr=False)

    @property
    def computed_total(self) -> Decimal:
        return sum((line.amount for line in self.lines), Decimal("0.00")).quantize(
            _CENT
        )

    @property
    def reference(self) -> str:
        return self.transmission_reference

    @property
    def compte(self) -> str:
        return self.account_number

    @property
    def nombre_operations(self) -> int | None:
        return self.operation_count

    @property
    def montant_total(self) -> Decimal | None:
        return self.total_amount

    @property
    def line_count(self) -> int:
        return len(self.lines)

    @property
    def balanced(self) -> bool:
        if self.total_amount is None:
            return True
        return self.computed_total == self.total_amount

    @property
    def count_matches(self) -> bool:
        if self.operation_count is None:
            return True
        return self.operation_count == self.line_count

    @property
    def unique_drawers(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for line in self.lines:
            seen.setdefault(line.drawer, None)
        return tuple(seen)

    def by_drawer(self) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = {}
        for line in self.lines:
            totals[line.drawer] = (
                totals.get(line.drawer, Decimal("0.00")) + line.amount
            ).quantize(_CENT)
        return totals

    def as_reconciliation_lines(
        self,
        *,
        fitid: str = "",
        operation_date: date | None = None,
    ) -> list[dict[str, Any]]:
        return [
            line.as_reconciliation_line(
                fitid=fitid,
                remittance_reference=self.transmission_reference,
                operation_date=operation_date,
                currency=self.currency,
            )
            for line in self.lines
        ]


def _line_from_columns(parts: list[str], raw: str) -> LcrRemittanceLine | None:
    if len(parts) < 6:
        return None
    drawer, rank_text, domiciliation, acceptance_text, due_text, amount_text = (
        parts[0],
        parts[1],
        parts[2],
        parts[3],
        parts[4],
        " ".join(parts[5:]),
    )
    acceptance = _fold(acceptance_text)
    if acceptance not in ACCEPTANCE_CODES or not drawer:
        return None
    amount_match = _AMOUNT_TOKEN.search(_fold(amount_text))
    if amount_match is None:
        return None
    try:
        amount = parse_ofx_amount(amount_match.group(0))
    except Exception:  # noqa: BLE001 - invalid row ignored
        return None
    rank = int(rank_text) if rank_text.strip().isdigit() else None
    return LcrRemittanceLine(
        drawer=_clean(drawer),
        rank=rank,
        domiciliation=_clean(domiciliation),
        acceptance=acceptance,
        due_date=_parse_due_date(due_text),
        amount=amount,
        raw=raw,
    )


def _line_from_whitespace(line: str) -> LcrRemittanceLine | None:
    acceptance_match = _ACCEPTANCE.search(line)
    if acceptance_match is None:
        return None
    amount_match = _AMOUNT_AT_END.search(line)
    if amount_match is None:
        return None
    try:
        amount = parse_ofx_amount(amount_match.group("amount"))
    except Exception:  # noqa: BLE001 - invalid row ignored
        return None
    amount_start = amount_match.start()
    acceptance = acceptance_match.group(1).upper()
    before = line[: acceptance_match.start()]
    due_date = _parse_due_date(line[:amount_start])
    if due_date is not None:
        before = _DATE_FULL.sub(" ", before)
        before = _DATE_SHORT.sub(" ", before)
        before = _DATE_COMPACT.sub(" ", before)
    before = re.sub(r"[|;]+", " ", before).strip(" \t-")
    if not before:
        return None
    rank_match: re.Match[str] | None = None
    for candidate in reversed(list(_RANK_TOKEN.finditer(before))):
        if before[candidate.end() :].strip():
            rank_match = candidate
            break
    if rank_match is None:
        drawer = _clean(before)
        domiciliation = ""
        rank = None
    else:
        drawer = _clean(before[: rank_match.start()])
        domiciliation = _clean(before[rank_match.end() :])
        rank = int(rank_match.group(1))
    if not drawer:
        return None
    return LcrRemittanceLine(
        drawer=drawer,
        rank=rank,
        domiciliation=domiciliation,
        acceptance=acceptance,
        due_date=due_date,
        amount=amount,
        raw=line,
    )


def _parse_lines(text: str) -> tuple[LcrRemittanceLine, ...]:
    lines: list[LcrRemittanceLine] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        parsed: LcrRemittanceLine | None = None
        if "|" in line:
            parsed = _line_from_columns(
                [part.strip() for part in line.split("|")], raw
            )
        elif "\t" in line:
            parsed = _line_from_columns(
                [part.strip() for part in line.split("\t")], raw
            )
        if parsed is None:
            parsed = _line_from_whitespace(line)
        if parsed is not None:
            lines.append(parsed)
    return tuple(lines)


def parse_lcr_remittance(
    value: str | bytes | Path,
    *,
    strict: bool = False,
) -> LcrRemittance:
    """Parse a remittance slip from text, PDF bytes or plain-text bytes."""
    text = _coerce_text(value)
    if not text.strip():
        raise LcrRemittanceError("EMPTY_DOCUMENT")
    folded = _fold(text)
    transmission_match = _TRANSMISSION.search(folded)
    account_match = _ACCOUNT.search(folded)
    count_match = _OPERATION_COUNT.search(folded)
    if count_match is None:
        count_match = _OPERATION_COUNT_REVERSED.search(folded)
    total = _amount_after(folded, _TOTAL_CREDIT)
    lines = _parse_lines(text)
    if not lines:
        raise LcrRemittanceError("NO_LINES")
    currency = "EUR"
    if "€" not in text and not re.search(r"\bEUR\b", text, re.IGNORECASE):
        currency_match = re.search(r"\b([A-Z]{3})\b", folded)
        if currency_match is not None and currency_match.group(1) != "EUR":
            currency = currency_match.group(1)
    remittance = LcrRemittance(
        transmission_reference=(
            transmission_match.group(1) if transmission_match is not None else ""
        ),
        account_number=(
            re.sub(r"\D", "", account_match.group(1))
            if account_match is not None
            else ""
        ),
        operation_count=(
            int(count_match.group(1)) if count_match is not None else None
        ),
        total_amount=total.quantize(_CENT) if total is not None else None,
        currency=currency,
        lines=lines,
        raw_text=text,
    )
    if strict:
        if not remittance.transmission_reference:
            raise LcrRemittanceError("MISSING_TRANSMISSION_REFERENCE")
        if not remittance.account_number:
            raise LcrRemittanceError("MISSING_ACCOUNT_NUMBER")
        if remittance.operation_count is None:
            raise LcrRemittanceError("MISSING_OPERATION_COUNT")
        if remittance.total_amount is None:
            raise LcrRemittanceError("MISSING_TOTAL_AMOUNT")
        if not remittance.count_matches:
            raise LcrRemittanceError(
                "OPERATION_COUNT_MISMATCH",
                f"{remittance.operation_count} != {remittance.line_count}",
            )
        if not remittance.balanced:
            raise LcrRemittanceError(
                "TOTAL_MISMATCH",
                f"{remittance.total_amount} != {remittance.computed_total}",
            )
    return remittance


def parse_lcr_remittance_file(
    path: str | Path, *, strict: bool = False
) -> LcrRemittance:
    """Read and parse a remittance slip from disk."""
    try:
        content = Path(path).read_bytes()
    except OSError as exc:
        raise LcrRemittanceError("UNREADABLE_REMITTANCE", str(path)) from exc
    return parse_lcr_remittance(content, strict=strict)


@dataclass(frozen=True)
class LcrAllocation:
    """Exact customer sub-payment derived from one remittance line."""

    drawer: str
    rank: int | None
    domiciliation: str
    acceptance: str
    due_date: date | None
    amount: Decimal
    line_id: str
    ofx_fitid: str
    remittance_reference: str
    operation_date: date | None
    currency: str = DEFAULT_CURRENCY

    @property
    def client(self) -> str:
        return self.drawer

    def as_reconciliation_line(self) -> dict[str, Any]:
        line = LcrRemittanceLine(
            drawer=self.drawer,
            rank=self.rank,
            domiciliation=self.domiciliation,
            acceptance=self.acceptance,
            due_date=self.due_date,
            amount=self.amount,
        ).as_reconciliation_line(
            fitid=self.ofx_fitid,
            remittance_reference=self.remittance_reference,
            operation_date=self.operation_date,
            currency=self.currency,
        )
        line["line_id"] = self.line_id
        line["fitid"] = self.line_id
        return line


@dataclass(frozen=True)
class LcrReconciliationReport:
    """Outcome of linking one OFX credit to one remittance slip."""

    ofx_fitid: str
    ofx_label: str
    ofx_amount: Decimal
    ofx_reference: str
    remittance_reference: str
    remittance_total: Decimal
    allocations: tuple[LcrAllocation, ...]
    currency: str = DEFAULT_CURRENCY

    @property
    def matched(self) -> bool:
        return True

    @property
    def allocation_count(self) -> int:
        return len(self.allocations)

    @property
    def sub_payments(self) -> tuple[LcrAllocation, ...]:
        return self.allocations

    @property
    def allocated_total(self) -> Decimal:
        return sum(
            (allocation.amount for allocation in self.allocations), Decimal("0.00")
        ).quantize(_CENT)

    @property
    def total_allocated(self) -> Decimal:
        return self.allocated_total

    @property
    def balanced(self) -> bool:
        return abs(self.allocated_total - self.ofx_amount) <= _CENT

    def by_drawer(self) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = {}
        for allocation in self.allocations:
            totals[allocation.drawer] = (
                totals.get(allocation.drawer, Decimal("0.00")) + allocation.amount
            ).quantize(_CENT)
        return totals

    def as_reconciliation_lines(self) -> list[dict[str, Any]]:
        return [allocation.as_reconciliation_line() for allocation in self.allocations]


def _transaction_values(transaction: OfxTransaction | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(transaction, OfxTransaction):
        return {
            "fitid": transaction.fitid,
            "label": transaction.label,
            "amount": transaction.amount,
            "payment_method": transaction.payment_method,
            "payment_reference": transaction.payment_reference,
            "operation_date": transaction.operation_date,
            "currency": transaction.currency,
        }
    if not isinstance(transaction, Mapping):
        raise LcrReconciliationError(
            "INVALID_OFX_TRANSACTION", type(transaction).__name__
        )
    credit = transaction.get("credit_amount")
    amount_value = credit if credit is not None else transaction.get("amount")
    try:
        amount = Decimal(str(amount_value))
    except Exception as exc:  # noqa: BLE001 - normalised error
        raise LcrReconciliationError("INVALID_OFX_AMOUNT", repr(amount_value)) from exc
    method = transaction.get("payment_method")
    label = str(transaction.get("label") or "")
    if not method:
        method = classify_ofx_line(label).payment_method
    operation_date = transaction.get("operation_date")
    if isinstance(operation_date, str):
        parsed_date = parse_ofx_date(operation_date)
    elif isinstance(operation_date, date):
        parsed_date = operation_date
    else:
        parsed_date = None
    reference = transaction.get("payment_reference")
    if not reference:
        match = re.search(r"\bN\s*(\d+)\b", label, re.IGNORECASE)
        reference = match.group(1) if match is not None else ""
    return {
        "fitid": str(transaction.get("fitid") or transaction.get("line_id") or ""),
        "label": label,
        "amount": amount,
        "payment_method": method,
        "payment_reference": str(reference or ""),
        "operation_date": parsed_date,
        "currency": str(transaction.get("currency") or DEFAULT_CURRENCY),
    }


def allocate_lcr_remittance(
    remittance: LcrRemittance,
    *,
    fitid: str = "",
    operation_date: date | None = None,
) -> tuple[LcrAllocation, ...]:
    """Split a remittance into one exact allocation per drawer row."""
    if not remittance.lines:
        raise LcrReconciliationError("NO_LINES")
    allocations: list[LcrAllocation] = []
    for index, line in enumerate(remittance.lines, start=1):
        rank = line.rank if line.rank is not None else index
        base = fitid or remittance.transmission_reference or "LCR"
        allocations.append(
            LcrAllocation(
                drawer=line.drawer,
                rank=line.rank,
                domiciliation=line.domiciliation,
                acceptance=line.acceptance,
                due_date=line.due_date,
                amount=line.amount,
                line_id=f"{base}:LCR:{rank:02d}",
                ofx_fitid=fitid,
                remittance_reference=remittance.transmission_reference,
                operation_date=operation_date,
                currency=remittance.currency,
            )
        )
    return tuple(allocations)


def reconcile_ofx_remittance(
    transaction: OfxTransaction | Mapping[str, Any],
    remittance: LcrRemittance,
    *,
    tolerance: Decimal = Decimal("0.01"),
) -> LcrReconciliationReport:
    """Attach one OFX ``REMISE ENCAISSEMENT`` credit to a remittance slip."""
    values = _transaction_values(transaction)
    method = str(values["payment_method"] or "")
    if method != PAYMENT_METHOD_LCR_ENCAISSEMENT_LOT:
        raise LcrReconciliationError("NOT_REMISE_ENCAISSEMENT", method or "unknown")
    if values["amount"] <= 0:
        raise LcrReconciliationError("INVALID_OFX_AMOUNT", str(values["amount"]))
    if not remittance.balanced:
        raise LcrReconciliationError(
            "REMITTANCE_TOTAL_MISMATCH",
            f"{remittance.total_amount} != {remittance.computed_total}",
        )
    remittance_total = remittance.total_amount or remittance.computed_total
    difference = (values["amount"] - remittance_total).copy_abs()
    if difference > tolerance:
        raise LcrReconciliationError(
            "AMOUNT_MISMATCH",
            f"{values['amount']} != {remittance_total}",
        )
    allocations = allocate_lcr_remittance(
        remittance,
        fitid=values["fitid"],
        operation_date=values["operation_date"],
    )
    return LcrReconciliationReport(
        ofx_fitid=values["fitid"],
        ofx_label=values["label"],
        ofx_amount=values["amount"],
        ofx_reference=values["payment_reference"],
        remittance_reference=remittance.transmission_reference,
        remittance_total=remittance_total,
        allocations=allocations,
        currency=values["currency"],
    )


reconcile_lcr_with_ofx = reconcile_ofx_remittance
reconcile_lcr_remittance = reconcile_ofx_remittance
parse_bordereau_remise = parse_lcr_remittance
parse_lcr_bordereau = parse_lcr_remittance


__all__ = [
    "ACCEPTANCE_CODES",
    "CATEGORY_VENTES_CLIENTS",
    "DEFAULT_CURRENCY",
    "LcrAllocation",
    "LcrReconciliationError",
    "LcrReconciliationReport",
    "LcrRemittance",
    "LcrRemittanceError",
    "LcrRemittanceLine",
    "PAYMENT_METHOD_LCR_ENCAISSEMENT_LOT",
    "allocate_lcr_remittance",
    "parse_bordereau_remise",
    "parse_lcr_bordereau",
    "parse_lcr_remittance",
    "parse_lcr_remittance_file",
    "reconcile_lcr_remittance",
    "reconcile_lcr_with_ofx",
    "reconcile_ofx_remittance",
]
