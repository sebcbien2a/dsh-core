"""P21 - Already-settled supplier invoices (Boxtal, Free, SMTRT).

Some suppliers print ``Net à payer 0,00 EUR`` because the invoice was paid at
the same time (credit card or SEPA direct debit). The accounting amount is the
``Total TTC`` printed on the invoice, never the remaining ``Net à payer``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from schemas.folder_state_schema import DocumentCategory
from services.ofx_bank_statement_parser import (
    OfxStatement,
    OfxTransaction,
    parse_ofx_statement,
)
from services.pdf_splitter_and_linker import parse_date_value, sanitize_component
from services.sqlite_accounting_store import validate_tenant_id

BOXTAL = "BOXTAL"
FREE_TELECOM = "FREE TELECOM"
FREE_MOBILE = "FREE MOBILE"
SMTRT = "SMTRT"
CATEGORY_TRANSPORTS = DocumentCategory.TRANSPORTS_LOGISTIQUE.value
CATEGORY_ACHATS_TRANSPORTS = CATEGORY_TRANSPORTS
CATEGORY_FRAIS_GENERAUX = DocumentCategory.FRAIS_GENERAUX.value
SUBCATEGORY_TELECOM = "TELECOM_INTERNET"
PAYMENT_CARTE_BANCAIRE = "CARTE_BANCAIRE"
PAYMENT_PRELEVEMENT = "PRELEVEMENT_SEPA"
PAYMENT_VIREMENT = "VIREMENT"
SESSION_PAID_STATUS = "PAYEE_RAPPROCHEE"

_CENT = Decimal("0.01")
_MONTHS = {
    "janvier": 1,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
}
_MONTH_PATTERN = "|".join(_MONTHS)
_INVOICE_BOXTAL = re.compile(
    r"Facture\s+n[°o]?\s*([0-9]{5,})\s+du\s+"
    r"(\d{1,2})\s+(" + _MONTH_PATTERN + r")\s+(\d{4})",
    re.IGNORECASE,
)
_INVOICE_GENERIC = re.compile(
    r"(?:Facture|Invoice)\s*(?:n[°o]?|N[°o]?)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]{3,})"
    r"(?:\s+du\s+(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}))?",
    re.IGNORECASE,
)
_FREE_INVOICE = re.compile(
    r"Facture\s+Free(?:box|mobile|telecom)?\s+n[°o]?\s*([0-9]{5,})\s+du\s+"
    r"(\d{1,2}\s+[A-Za-zéûôà]+\s+\d{4})",
    re.IGNORECASE,
)
_FREE_TOTAL = re.compile(
    r"Total\s+([0-9][0-9\s.,]*)\s*(?:€|EUR)\s+"
    r"([0-9][0-9\s.,]*)\s*(?:€|EUR)",
    re.IGNORECASE,
)
_FREE_VAT = re.compile(
    r"Dont\s*TVA\s*[0-9.,]+\s*%\s*:\s*([0-9][0-9\s.,]*)\s*(?:€|EUR)",
    re.IGNORECASE,
)
_TOTAL_HT = re.compile(
    r"Total\s*HT\s*[:\-]?\s*([0-9][0-9\s.,]*)\s*(?:€|EUR)?", re.IGNORECASE
)
_TOTAL_TVA = re.compile(
    r"Total\s*TVA\s*[:\-]?\s*([0-9][0-9\s.,]*)\s*(?:€|EUR)?", re.IGNORECASE
)
_TOTAL_TTC = re.compile(
    r"Total\s*TTC\s*[:\-]?\s*([0-9][0-9\s.,]*)\s*(?:€|EUR)?", re.IGNORECASE
)
_NET_TO_PAY = re.compile(
    r"Net\s*(?:à|a)\s*payer\s*[:\-]?\s*(-?[0-9][0-9\s.,]*)\s*(?:€|EUR)?",
    re.IGNORECASE,
)
_SETTLED = re.compile(
    r"R[ée]gl[ée]e?\s*[:\-]?\s*(-?[0-9][0-9\s.,]*)\s*(?:€|EUR)?", re.IGNORECASE
)
_PAYMENT_MEANS = re.compile(
    r"(?:Moyen|R[èe]glement)\s*(?:Carte\s*Bancaire|Pr[ée]l[èe]vement|Virement)"
    r"[^\n\r]*",
    re.IGNORECASE,
)
_FRENCH_DATE = re.compile(
    r"(\d{1,2})\s+(" + _MONTH_PATTERN + r")\s+(\d{4})", re.IGNORECASE
)


class SettledInvoiceError(ValueError):
    """Invalid settled invoice; ``code`` is stable for tests and logs."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class SettledInvoice:
    """One invoice already paid by card, direct debit or transfer."""

    supplier: str
    invoice_number: str
    invoice_date: date | None
    total_ht: Decimal
    total_tva: Decimal
    total_ttc: Decimal
    net_to_pay: Decimal = Decimal("0.00")
    already_paid_on_invoice: bool = True
    payment_method: str = PAYMENT_CARTE_BANCAIRE
    payment_date: date | None = None
    due_date: date | None = None
    category: str = CATEGORY_TRANSPORTS
    expense_subcategory: str | None = None
    source_filename: str = ""
    raw_text: str = field(default="", compare=False, repr=False)

    @property
    def line_id(self) -> str:
        return (
            "SETTLED_"
            f"{sanitize_component(self.supplier, uppercase=True)}_"
            f"{sanitize_component(self.invoice_number, uppercase=True)}"
        )[:128]

    def as_ledger_line(self) -> dict[str, Any]:
        label = (
            f"Facture {self.supplier} {self.invoice_number} - Reglee "
            f"({self.payment_method})"
        )
        return {
            "operation_date": (
                self.invoice_date.isoformat() if self.invoice_date else None
            ),
            "label": label,
            "counterparty": self.supplier,
            "invoice_id": self.invoice_number,
            "category": self.category,
            "expense_subcategory": self.expense_subcategory,
            "amount_ht": str(self.total_ht.quantize(_CENT)),
            "amount_tva": str(self.total_tva.quantize(_CENT)),
            "amount_ttc": str(self.total_ttc.quantize(_CENT)),
            "document_type": "FACTURE",
            "status": "VALIDE",
            "payment_method": self.payment_method,
            "payment_reference": self.payment_method,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "already_paid_on_invoice": "1",
            "balance_due": "0.00",
        }


@dataclass(frozen=True)
class SettledInvoiceMatch:
    """Exact OFX debit matching one already-paid invoice."""

    invoice_number: str
    supplier: str
    amount: Decimal
    fitid: str
    operation_date: date
    label: str
    payment_method: str = PAYMENT_CARTE_BANCAIRE


@dataclass(frozen=True)
class SettledInvoiceImport:
    """Outcome of importing and optionally lettering one settled invoice."""

    invoice: SettledInvoice
    line_id: str
    match: SettledInvoiceMatch | None = None


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().casefold()


def _parse_decimal(value: Any) -> Decimal:
    text = str(value or "").replace("\u00a0", " ").replace("\u202f", " ")
    text = re.sub(r"[^0-9,.-]", "", text.replace(" ", ""))
    if not text:
        raise SettledInvoiceError("INVALID_AMOUNT", repr(value))
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return Decimal(text).quantize(_CENT)
    except InvalidOperation as exc:
        raise SettledInvoiceError("INVALID_AMOUNT", repr(value)) from exc


def _pdf_text(source: str | Path | bytes | bytearray) -> str:
    try:
        import fitz

        if hasattr(fitz, "TOOLS"):
            fitz.TOOLS.mupdf_display_errors(False)
    except ImportError as exc:  # pragma: no cover
        raise SettledInvoiceError("PDF_SUPPORT_UNAVAILABLE") from exc
    try:
        if isinstance(source, (bytes, bytearray)):
            document = fitz.open(stream=bytes(source), filetype="pdf")
        else:
            document = fitz.open(str(source))
    except Exception as exc:  # noqa: BLE001
        raise SettledInvoiceError("PDF_EXTRACTION_FAILED", str(exc)) from exc
    try:
        return "\n".join(page.get_text("text") for page in document)
    finally:
        document.close()


def _french_date(text: str) -> date | None:
    match = _FRENCH_DATE.search(_fold(text))
    if match is None:
        return None
    month = _MONTHS.get(_fold(match.group(2)))
    if month is None:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(1)))
    except ValueError:
        return None


def detect_settled_supplier(text: str, filename: str = "") -> str | None:
    haystack = _fold(f"{filename}\n{text}")
    if "boxtal" in haystack or "489553081" in haystack:
        return BOXTAL
    if "freebox" in haystack or "free telecom" in haystack:
        return FREE_TELECOM
    if "free mobile" in haystack:
        return FREE_MOBILE
    if (
        "facture free" in haystack
        or re.search(r"\bfree\s+sas\b", haystack)
    ):
        return FREE_TELECOM
    if "smtrt" in haystack:
        return SMTRT
    return None


def _payment_method(text: str) -> str:
    folded = _fold(text)
    if "carte bancaire" in folded or re.search(r"\bcb\b", folded):
        return PAYMENT_CARTE_BANCAIRE
    if "prelev" in folded:
        return PAYMENT_PRELEVEMENT
    if "virement" in folded:
        return PAYMENT_VIREMENT
    return PAYMENT_CARTE_BANCAIRE


def parse_settled_invoice(
    source: str | Path | bytes | bytearray,
    *,
    filename: str | None = None,
    supplier: str | None = None,
) -> SettledInvoice:
    """Parse a Boxtal / Free / SMTRT invoice without trusting ``Net à payer``."""
    text = _pdf_text(source)
    name = filename or (
        source.name
        if isinstance(source, Path)
        else (Path(source).name if isinstance(source, str) else "")
    )
    detected = supplier or detect_settled_supplier(text, name)
    if detected is None:
        raise SettledInvoiceError("NOT_SETTLED_INVOICE", name)
    selected = detected.upper()
    invoice_number = ""
    invoice_date: date | None = None
    boxtal = _INVOICE_BOXTAL.search(_fold(text))
    if boxtal is not None:
        invoice_number = boxtal.group(1)
        invoice_date = _french_date(boxtal.group(0))
    if not invoice_number:
        free_invoice = _FREE_INVOICE.search(_fold(text))
        if free_invoice is not None:
            invoice_number = free_invoice.group(1)
            invoice_date = _french_date(free_invoice.group(2))
    if not invoice_number:
        generic = _INVOICE_GENERIC.search(text)
        if generic is not None:
            invoice_number = generic.group(1).upper()
            if generic.group(2):
                invoice_date = parse_date_value(generic.group(2))
    if invoice_date is None:
        invoice_date = _french_date(text)
    if not invoice_number:
        filename_match = re.search(r"(\d{6,})", Path(name).stem)
        if filename_match is not None:
            invoice_number = filename_match.group(1)
    if not invoice_number:
        raise SettledInvoiceError("INVOICE_NUMBER_NOT_FOUND", name)

    ht_match = _TOTAL_HT.search(text)
    tva_match = _TOTAL_TVA.search(text)
    ttc_match = _TOTAL_TTC.search(text)
    total_ht = _parse_decimal(ht_match.group(1)) if ht_match else Decimal("0.00")
    total_tva = _parse_decimal(tva_match.group(1)) if tva_match else Decimal("0.00")
    total_ttc = _parse_decimal(ttc_match.group(1)) if ttc_match else Decimal("0.00")
    if total_ttc == 0:
        free_total = _FREE_TOTAL.search(text)
        if free_total is not None:
            total_ht = _parse_decimal(free_total.group(1))
            total_ttc = _parse_decimal(free_total.group(2))
            if total_tva == 0:
                total_tva = (total_ttc - total_ht).quantize(_CENT)
    if total_tva == 0:
        free_vat = _FREE_VAT.search(text)
        if free_vat is not None:
            total_tva = _parse_decimal(free_vat.group(1))
    if total_ttc == 0 and (total_ht or total_tva):
        total_ttc = (total_ht + total_tva).quantize(_CENT)
    if total_ttc == 0:
        raise SettledInvoiceError("TOTAL_TTC_NOT_FOUND", name)
    net_match = _NET_TO_PAY.search(text)
    net_to_pay = (
        _parse_decimal(net_match.group(1)) if net_match is not None else Decimal("0.00")
    )
    settled_match = _SETTLED.search(text)
    payment_date = invoice_date
    if settled_match is not None:
        payment_date = _french_date(text) or invoice_date

    category = CATEGORY_TRANSPORTS
    subcategory: str | None = None
    if selected.startswith("FREE"):
        category = CATEGORY_FRAIS_GENERAUX
        subcategory = SUBCATEGORY_TELECOM
    return SettledInvoice(
        supplier=selected,
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        total_ht=total_ht,
        total_tva=total_tva,
        total_ttc=total_ttc,
        net_to_pay=net_to_pay,
        already_paid_on_invoice=True,
        payment_method=_payment_method(text),
        payment_date=payment_date,
        due_date=invoice_date,
        category=category,
        expense_subcategory=subcategory,
        source_filename=name,
        raw_text=text,
    )


def _supplier_tokens(supplier: str) -> tuple[str, ...]:
    folded = _fold(supplier)
    if "boxtal" in folded:
        return ("boxtal",)
    if "free" in folded:
        return ("free",)
    if "smtrt" in folded:
        return ("smtrt",)
    return tuple(part for part in folded.split() if len(part) >= 3)


def find_settled_ofx_match(
    invoice: SettledInvoice,
    statement: OfxStatement | str | bytes,
    *,
    tolerance_days: int = 45,
) -> SettledInvoiceMatch | None:
    """Find the exact debit line of an already-paid invoice."""
    parsed = (
        statement
        if isinstance(statement, OfxStatement)
        else parse_ofx_statement(statement)
    )
    return _best_settled_match(invoice, parsed.transactions, tolerance_days)


def find_settled_store_match(
    store: Any,
    tenant_id: str,
    invoice: SettledInvoice,
    *,
    tolerance_days: int = 45,
) -> SettledInvoiceMatch | None:
    """Find the exact debit already persisted by a previous OFX import."""
    validate_tenant_id(tenant_id)
    rows = store.list_master_lines(tenant_id)
    transactions: list[OfxTransaction] = []
    for index, row in enumerate(rows):
        debit = row.get("debit_amount")
        if debit in (None, ""):
            continue
        label = str(row.get("label") or "")
        raw_date = str(row.get("operation_date") or "")
        message = str(row.get("memo") or "")
        try:
            operation_date = date.fromisoformat(raw_date[:10])
        except ValueError:
            continue
        transactions.append(
            OfxTransaction(
                fitid=str(row.get("fitid") or row.get("line_id") or index),
                operation_date=operation_date,
                amount=-_parse_decimal(debit),
                label=label,
                memo=message,
            )
        )
    return _best_settled_match(invoice, transactions, tolerance_days)


def _best_settled_match(
    invoice: SettledInvoice,
    transactions: Iterable[OfxTransaction],
    tolerance_days: int,
) -> SettledInvoiceMatch | None:
    tokens = _supplier_tokens(invoice.supplier)
    expected_date = invoice.invoice_date
    fact_token = (
        expected_date.strftime("%d%m%y") if expected_date is not None else ""
    )
    candidates: list[tuple[int, OfxTransaction]] = []
    for transaction in transactions:
        if transaction.amount >= 0:
            continue
        amount = (-transaction.amount).quantize(_CENT)
        if amount != invoice.total_ttc.quantize(_CENT):
            continue
        label = _fold(f"{transaction.label} {transaction.memo}")
        if not any(token in label for token in tokens):
            continue
        if expected_date is not None:
            delta = abs((transaction.operation_date - expected_date).days)
            if delta > tolerance_days:
                continue
        score = 0
        if fact_token and f"fact {fact_token}" in label:
            score += 3
        if invoice.invoice_number.casefold() in label:
            score += 2
        if expected_date is not None:
            score += max(
                0, 2 - abs((transaction.operation_date - expected_date).days) // 15
            )
        candidates.append((score, transaction))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1].operation_date, item[1].fitid))
    chosen = candidates[0][1]
    return SettledInvoiceMatch(
        invoice_number=invoice.invoice_number,
        supplier=invoice.supplier,
        amount=(-chosen.amount).quantize(_CENT),
        fitid=chosen.fitid,
        operation_date=chosen.operation_date,
        label=chosen.label,
        payment_method=invoice.payment_method,
    )


def import_settled_invoice(
    store: Any,
    tenant_id: str,
    invoice: SettledInvoice,
    *,
    ofx_statement: OfxStatement | str | bytes | None = None,
    piece_pdf: str | None = None,
) -> SettledInvoiceImport:
    """Persist the invoice and letter it against its exact OFX debit."""
    tenant = validate_tenant_id(tenant_id)
    match = (
        find_settled_ofx_match(invoice, ofx_statement)
        if ofx_statement is not None
        else find_settled_store_match(store, tenant, invoice)
    )
    line = invoice.as_ledger_line()
    if piece_pdf:
        line["piece_pdf"] = piece_pdf
    if match is not None:
        line["reconciliation_status"] = SESSION_PAID_STATUS
        line["reconciliation_date"] = match.operation_date.isoformat()
        line["reconciliation_reference"] = match.fitid
        line["reconciliation_amount"] = str(match.amount)
        line["fitid"] = match.fitid
        line["payment_reference"] = match.fitid
    store.upsert_document_line(tenant, invoice.line_id, line)
    return SettledInvoiceImport(invoice=invoice, line_id=invoice.line_id, match=match)


__all__ = [
    "BOXTAL",
    "CATEGORY_ACHATS_TRANSPORTS",
    "CATEGORY_FRAIS_GENERAUX",
    "CATEGORY_TRANSPORTS",
    "FREE_MOBILE",
    "FREE_TELECOM",
    "PAYMENT_CARTE_BANCAIRE",
    "PAYMENT_PRELEVEMENT",
    "PAYMENT_VIREMENT",
    "SESSION_PAID_STATUS",
    "SMTRT",
    "SUBCATEGORY_TELECOM",
    "SettledInvoice",
    "SettledInvoiceError",
    "SettledInvoiceImport",
    "SettledInvoiceMatch",
    "detect_settled_supplier",
    "find_settled_ofx_match",
    "find_settled_store_match",
    "import_settled_invoice",
    "parse_settled_invoice",
]
