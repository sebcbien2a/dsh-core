"""P19 - Caisse d'Epargne OFX (SGML) bank statement parser.

The parser is deterministic and offline. It accepts the SGML dialect exported
by the Caisse d'Epargne (``OFXHEADER:100``, leaf tags without closing tags,
``cp1252`` or UTF-8 payloads, comma decimal amounts) as well as OFX 2.0 XML.
Every transaction is classified into a precise payment channel and the
``FITID`` is used as the anti-duplicate identity.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from schemas.folder_state_schema import DocumentCategory
from schemas.payment_method_schema import (
    BankPaymentMethod,
    ExpectedPaymentMethod,
    normalize_bank_payment_method,
)
from services.bank_statement_parser_service import (
    BankStatementError,
    parse_amount,
)

OFX_HEADER = "OFXHEADER:100"
DEFAULT_CURRENCY = "EUR"
CATEGORY_BANQUE_FINANCE = DocumentCategory.BANQUE_FINANCE.value
CATEGORY_VENTES_CLIENTS = DocumentCategory.VENTES_CLIENTS.value

PAYMENT_METHOD_LCR_ENCAISSEMENT_LOT = BankPaymentMethod.LCR_ENCAISSEMENT_LOT.value
PAYMENT_METHOD_LCR_FOURNISSEUR_DEBIT = BankPaymentMethod.LCR_FOURNISSEUR_DEBIT.value
PAYMENT_METHOD_REMISE_CHEQUES = BankPaymentMethod.REMISE_CHEQUES.value
PAYMENT_METHOD_PRELEVEMENT_SEPA = BankPaymentMethod.PRELEVEMENT_SEPA.value
PAYMENT_METHOD_VIREMENT = BankPaymentMethod.VIREMENT.value
PAYMENT_METHOD_CARTE_BANCAIRE = BankPaymentMethod.CARTE_BANCAIRE.value
PAYMENT_METHOD_CHEQUE_EMIS = BankPaymentMethod.CHEQUE_EMIS.value
PAYMENT_METHOD_FRAIS_BANCAIRES = BankPaymentMethod.FRAIS_BANCAIRES.value
PAYMENT_METHOD_AUTRE = BankPaymentMethod.AUTRE.value

_FITID_SAFE = re.compile(r"[^A-Za-z0-9._:-]")
_STMTTRN = re.compile(r"<STMTTRN>(.*?)</STMTTRN>", re.IGNORECASE | re.DOTALL)
_LEAF = re.compile(r"<\s*([A-Za-z0-9_.]+)\s*>([^<]*)", re.DOTALL)
_ACCOUNT_BLOCK = re.compile(
    r"<BANKACCTFROM>(.*?)</BANKACCTFROM>", re.IGNORECASE | re.DOTALL
)
_CARD_DATE = re.compile(r"\bFACT\s+(\d{6})\b")
_CARD_MERCHANT = re.compile(
    r"\bCB\s+(.+?)(?:\s+FACT\s+\d{6})?$", re.IGNORECASE
)
_REFERENCE = re.compile(r"\bN(?:O|°)?\s*(\d{2,})\b")
_LCR_REFERENCE = re.compile(
    r"\bLCR\s+(\d+)\s+RLV\s+(\d+)\b", re.IGNORECASE
)
_FEE_MARKERS = (
    "COMMISSION SUR EFFETS",
    "COMMISSIONS",
    "COMMISSION",
    "*ABO CE",
    "*ABO",
    "* FORFAIT",
    "*FORFAIT",
    "FORFAIT",
)
_ACCOUNT_ONLY = re.compile(r"[\d ]{11,}")


class OfxStatementError(ValueError):
    """Invalid or unsupported OFX statement; ``code`` is stable."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class PaymentClassification:
    """Payment channel and contextual references of one OFX line."""

    payment_method: str = PAYMENT_METHOD_AUTRE
    category: str | None = None
    payment_reference: str | None = None
    purchase_date: date | None = None
    counterparty: str | None = None


@dataclass(frozen=True)
class OfxTransaction:
    """One normalised OFX bank operation."""

    fitid: str
    operation_date: date
    amount: Decimal
    label: str
    memo: str = ""
    transaction_type: str = ""
    payment_method: str = PAYMENT_METHOD_AUTRE
    category: str | None = None
    payment_reference: str | None = None
    purchase_date: date | None = None
    counterparty: str | None = None
    currency: str = DEFAULT_CURRENCY
    account_id: str | None = None
    source: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def is_credit(self) -> bool:
        return self.amount > 0

    @property
    def is_debit(self) -> bool:
        return self.amount < 0

    @property
    def debit_amount(self) -> Decimal | None:
        return -self.amount if self.amount < 0 else None

    @property
    def credit_amount(self) -> Decimal | None:
        return self.amount if self.amount > 0 else None

    @property
    def inline_line_id(self) -> str:
        return ofx_line_id(self.fitid)

    @property
    def line_id(self) -> str:
        return self.inline_line_id

    @property
    def payment_date(self) -> date:
        return self.operation_date

    def as_reconciliation_line(self) -> dict[str, Any]:
        """Return the row consumed by ``SqliteAccountingStore.add_line``."""
        return {
            "line_id": self.inline_line_id,
            "operation_date": self.operation_date.isoformat(),
            "label": self.label,
            "debit_amount": (
                str(self.debit_amount) if self.debit_amount is not None else None
            ),
            "credit_amount": (
                str(self.credit_amount) if self.credit_amount is not None else None
            ),
            "currency": self.currency,
            "counterparty": self.counterparty,
            "category": self.category,
            "payment_method": self.payment_method,
            "payment_reference": self.payment_reference,
            "purchase_date": (
                self.purchase_date.isoformat() if self.purchase_date else None
            ),
            "fitid": self.fitid,
            "document_type": "OFX",
            "status": "PENDING",
        }


@dataclass(frozen=True)
class OfxStatement:
    """A parsed bank statement with its anti-duplicate counters."""

    account_id: str
    currency: str
    transactions: tuple[OfxTransaction, ...]
    bank_id: str = ""
    branch_id: str = ""
    date_start: date | None = None
    date_end: date | None = None
    duplicates: int = 0
    source: str = ""

    @property
    def total_credit(self) -> Decimal:
        return sum(
            (item.amount for item in self.transactions if item.amount > 0),
            Decimal("0.00"),
        )

    @property
    def total_debit(self) -> Decimal:
        return sum(
            (-item.amount for item in self.transactions if item.amount < 0),
            Decimal("0.00"),
        )


@dataclass(frozen=True)
class OfxImportReport:
    """Outcome of importing a parsed OFX statement into the accounting store."""

    created: int
    duplicates: int
    skipped: int
    line_ids: tuple[str, ...] = ()


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().upper()


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()


def decode_ofx(content: str | bytes) -> str:
    """Decode an OFX payload (UTF-8, UTF-8 BOM or cp1252 / 1252 header)."""
    if isinstance(content, str):
        return content.lstrip("\ufeff")
    if not isinstance(content, (bytes, bytearray)):
        raise OfxStatementError("INVALID_CONTENT", type(content).__name__)
    payload = bytes(content)
    header = payload[:4096].decode("ascii", "ignore")
    if re.search(r"(?:ENCODING|CHARSET)\s*:\s*(?:1252|CP1252)", header, re.IGNORECASE):
        return payload.decode("cp1252", "replace").lstrip("\ufeff")
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return payload.decode("cp1252", "replace").lstrip("\ufeff")


def parse_ofx_amount(value: Any) -> Decimal:
    """Parse ``-4173,79``, ``+9240,48`` or ``1 234,56`` into a Decimal."""
    text = _clean(value)
    if not text:
        raise OfxStatementError("MISSING_AMOUNT")
    try:
        amount = parse_amount(text)
    except BankStatementError as exc:
        raise OfxStatementError("INVALID_AMOUNT", text) from exc
    if amount is None:
        raise OfxStatementError("MISSING_AMOUNT")
    return amount


def parse_ofx_date(value: Any) -> date:
    """Extract the calendar date of an OFX ``DTPOSTED`` value."""
    text = _clean(value)
    match = re.match(r"(\d{8})", text)
    if match is None:
        raise OfxStatementError("INVALID_DATE", text[:40])
    raw = match.group(1)
    try:
        return datetime.strptime(raw, "%Y%m%d").date()
    except ValueError as exc:
        raise OfxStatementError("INVALID_DATE", raw) from exc


def _parse_card_date(value: str) -> date | None:
    match = _CARD_DATE.search(_fold(value))
    if match is None:
        return None
    raw = match.group(1)
    day, month, year = int(raw[0:2]), int(raw[2:4]), int(raw[4:6])
    full_year = 2000 + year if year < 90 else 1900 + year
    try:
        return date(full_year, month, day)
    except ValueError:
        return None


def _tails(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, re.IGNORECASE)
    if match is None:
        return None
    tail = _clean(match.group(1))
    return tail or None


def classify_ofx_line(
    name: Any,
    memo: Any = "",
    amount: Decimal | None = None,
) -> PaymentClassification:
    """Classify NAME / MEMO into a precise payment channel."""
    primary = _clean(name)
    secondary = _clean(memo)
    for raw in (primary, secondary):
        if not raw:
            continue
        text = _fold(raw)
        classification = _classify_text(text, raw)
        if classification.payment_method != PAYMENT_METHOD_AUTRE:
            return classification
        if amount is not None and _is_fee(text):
            return PaymentClassification(
                payment_method=PAYMENT_METHOD_FRAIS_BANCAIRES,
                category=CATEGORY_BANQUE_FINANCE,
                counterparty="CAISSE D'EPARGNE",
            )
    return PaymentClassification()


def _is_fee(text: str) -> bool:
    return any(marker in text for marker in _FEE_MARKERS)


def _classify_text(text: str, original: str) -> PaymentClassification:
    if _is_fee(text):
        return PaymentClassification(
            payment_method=PAYMENT_METHOD_FRAIS_BANCAIRES,
            category=CATEGORY_BANQUE_FINANCE,
            counterparty="CAISSE D'EPARGNE",
        )
    if "REMISE ENCAISSEMENT" in text:
        reference_match = _REFERENCE.search(text)
        return PaymentClassification(
            payment_method=PAYMENT_METHOD_LCR_ENCAISSEMENT_LOT,
            category=CATEGORY_VENTES_CLIENTS,
            payment_reference=(
                reference_match.group(1) if reference_match is not None else None
            ),
            counterparty="CLIENTS LCR",
        )
    if re.search(r"REMISE\s+(?:DE\s+)?CHEQUES?", text):
        reference_match = _REFERENCE.search(text)
        return PaymentClassification(
            payment_method=PAYMENT_METHOD_REMISE_CHEQUES,
            category=CATEGORY_VENTES_CLIENTS,
            payment_reference=(
                reference_match.group(1) if reference_match is not None else None
            ),
            counterparty="CLIENTS CHEQUES",
        )
    lcr_match = _LCR_REFERENCE.search(text)
    if lcr_match is not None:
        return PaymentClassification(
            payment_method=PAYMENT_METHOD_LCR_FOURNISSEUR_DEBIT,
            payment_reference=f"{lcr_match.group(1)}/{lcr_match.group(2)}",
        )
    if re.search(r"\b(?:PRLV|PRELEVEMENT)\b", text):
        return PaymentClassification(
            payment_method=PAYMENT_METHOD_PRELEVEMENT_SEPA,
            counterparty=_tails(
                original, r"\b(?:PRLV|PRELEVEMENT)(?:\s+SEPA)?\s+(.+)"
            ),
        )
    if re.search(r"\b(?:VIR\s+SEPA|VIR\s+INST|VIREMENT)\b", text):
        return PaymentClassification(
            payment_method=PAYMENT_METHOD_VIREMENT,
            counterparty=_tails(
                original, r"\b(?:VIR\s+SEPA|VIR\s+INST|VIREMENT)\s+(.+)"
            ),
        )
    if re.search(r"\bCB\b", text):
        merchant_match = _CARD_MERCHANT.search(original)
        return PaymentClassification(
            payment_method=PAYMENT_METHOD_CARTE_BANCAIRE,
            purchase_date=_parse_card_date(original),
            counterparty=(
                _clean(merchant_match.group(1)) if merchant_match is not None else None
            ),
        )
    if re.search(r"\b(?:CHEQUE|CHQ)\b", text):
        reference_match = _REFERENCE.search(text)
        return PaymentClassification(
            payment_method=PAYMENT_METHOD_CHEQUE_EMIS,
            payment_reference=(
                reference_match.group(1) if reference_match is not None else None
            ),
        )
    return PaymentClassification()


def classify_payment_method(name: Any, memo: Any = "") -> str:
    """Return only the canonical payment method of a statement line."""
    return classify_ofx_line(name, memo).payment_method


def _leaf_fields(block: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in _LEAF.finditer(block):
        key = match.group(1).upper()
        value = match.group(2).strip()
        if key and key not in fields:
            fields[key] = value
    return fields


def _certificate_fitid(fields: Mapping[str, str], fallback: str) -> str:
    fitid = _clean(fields.get("FITID"))
    if fitid:
        return fitid
    material = "\x1f".join(
        (
            fallback,
            _clean(fields.get("DTPOSTED")),
            _clean(fields.get("TRNAMT")),
            _clean(fields.get("NAME")),
            _clean(fields.get("MEMO")),
        )
    )
    return "OFX-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def ofx_line_id(fitid: str) -> str:
    """Stable reconciliation line id derived from ``FITID``."""
    cleaned = _FITID_SAFE.sub("_", str(fitid or "").strip())
    if not cleaned:
        cleaned = hashlib.sha256(str(fitid).encode("utf-8")).hexdigest()[:24]
    return f"OFX-{cleaned[:120]}"


def parse_ofx_statement(content: str | bytes) -> OfxStatement:
    """Parse an OFX statement and de-duplicate transactions by ``FITID``."""
    text = decode_ofx(content)
    if not re.search(r"OFXHEADER\s*:\s*\d+", text, re.IGNORECASE) and not re.search(
        r"<OFX\b", text, re.IGNORECASE
    ):
        raise OfxStatementError("UNSUPPORTED_OFX", "missing OFX header")
    blocks = _STMTTRN.findall(text)
    if not blocks:
        raise OfxStatementError("NO_TRANSACTIONS")

    account_id = ""
    bank_id = ""
    branch_id = ""
    account_block = _ACCOUNT_BLOCK.search(text)
    if account_block is not None:
        account_fields = _leaf_fields(account_block.group(1))
        account_id = _clean(account_fields.get("ACCTID"))
        bank_id = _clean(account_fields.get("BANKID"))
        branch_id = _clean(account_fields.get("BRANCHID"))
    if not account_id:
        first_account = re.search(
            r"<ACCTID>\s*([^<\r\n]+)", text, re.IGNORECASE
        )
        account_id = _clean(first_account.group(1)) if first_account else ""
    currency_match = re.search(r"<CURDEF>\s*([^<\r\n]+)", text, re.IGNORECASE)
    currency = (
        _clean(currency_match.group(1)).upper() if currency_match else DEFAULT_CURRENCY
    )
    start_match = re.search(r"<DTSTART>\s*([^<\r\n]+)", text, re.IGNORECASE)
    end_match = re.search(r"<DTEND>\s*([^<\r\n]+)", text, re.IGNORECASE)
    date_start = parse_ofx_date(start_match.group(1)) if start_match else None
    date_end = parse_ofx_date(end_match.group(1)) if end_match else None

    transactions: list[OfxTransaction] = []
    seen: set[str] = set()
    duplicates = 0
    for index, block in enumerate(blocks, start=1):
        fields = _leaf_fields(block)
        label = _clean(fields.get("NAME")) or _clean(fields.get("MEMO"))
        memo = _clean(fields.get("MEMO"))
        if not label:
            raise OfxStatementError("MISSING_LABEL", f"transaction {index}")
        amount = parse_ofx_amount(fields.get("TRNAMT"))
        operation_date = parse_ofx_date(fields.get("DTPOSTED"))
        fitid = _certificate_fitid(fields, fallback=f"{operation_date}{index}")
        if fitid in seen:
            duplicates += 1
            continue
        seen.add(fitid)
        classification = classify_ofx_line(label, memo, amount)
        transaction_type = _clean(fields.get("TRNTYPE")).upper()
        if not transaction_type:
            transaction_type = "CREDIT" if amount > 0 else "DEBIT"
        transactions.append(
            OfxTransaction(
                fitid=fitid,
                operation_date=operation_date,
                amount=amount,
                label=label,
                memo=memo,
                transaction_type=transaction_type,
                payment_method=normalize_bank_payment_method(
                    classification.payment_method
                ),
                category=classification.category,
                payment_reference=classification.payment_reference,
                purchase_date=classification.purchase_date,
                counterparty=classification.counterparty,
                currency=currency,
                account_id=account_id or None,
                source="ofx",
                raw=fields,
            )
        )
    return OfxStatement(
        account_id=account_id,
        currency=currency,
        transactions=tuple(transactions),
        bank_id=bank_id,
        branch_id=branch_id,
        date_start=date_start,
        date_end=date_end,
        duplicates=duplicates,
        source="ofx",
    )


def parse_ofx_file(path: str | Path) -> OfxStatement:
    """Read and parse an ``.ofx`` file from disk."""
    try:
        content = Path(path).read_bytes()
    except OSError as exc:
        raise OfxStatementError("UNREADABLE_OFX", str(path)) from exc
    return parse_ofx_statement(content)


def import_ofx_statement(
    store: Any, tenant_id: str, statement: OfxStatement | str | bytes
) -> OfxImportReport:
    """Persist every statement line, relying on ``FITID`` for idempotency."""
    parsed = statement if isinstance(statement, OfxStatement) else parse_ofx_statement(statement)
    created = duplicates = skipped = 0
    line_ids: list[str] = []
    for transaction in parsed.transactions:
        line = transaction.as_reconciliation_line()
        if line.get("debit_amount") is None and line.get("credit_amount") is None:
            skipped += 1
            continue
        if store.add_line(tenant_id, line["line_id"], line):
            created += 1
            line_ids.append(line["line_id"])
        else:
            duplicates += 1
    return OfxImportReport(
        created=created,
        duplicates=duplicates,
        skipped=skipped,
        line_ids=tuple(line_ids),
    )


def expected_rule_for_method(
    payment_method: str | None,
) -> ExpectedPaymentMethod | None:
    """Return the coarse expected method of an observed channel, if any."""
    from schemas.payment_method_schema import expected_family_for_bank_method

    family = expected_family_for_bank_method(payment_method)
    return ExpectedPaymentMethod(family) if family is not None else None


__all__ = [
    "CATEGORY_BANQUE_FINANCE",
    "CATEGORY_VENTES_CLIENTS",
    "DEFAULT_CURRENCY",
    "OFX_HEADER",
    "OfxImportReport",
    "OfxStatement",
    "OfxStatementError",
    "OfxTransaction",
    "PAYMENT_METHOD_AUTRE",
    "PAYMENT_METHOD_CARTE_BANCAIRE",
    "PAYMENT_METHOD_CHEQUE_EMIS",
    "PAYMENT_METHOD_FRAIS_BANCAIRES",
    "PAYMENT_METHOD_LCR_ENCAISSEMENT_LOT",
    "PAYMENT_METHOD_LCR_FOURNISSEUR_DEBIT",
    "PAYMENT_METHOD_PRELEVEMENT_SEPA",
    "PAYMENT_METHOD_REMISE_CHEQUES",
    "PAYMENT_METHOD_VIREMENT",
    "PaymentClassification",
    "classify_ofx_line",
    "classify_payment_method",
    "decode_ofx",
    "expected_rule_for_method",
    "import_ofx_statement",
    "ofx_line_id",
    "parse_ofx_amount",
    "parse_ofx_date",
    "parse_ofx_file",
    "parse_ofx_statement",
]
