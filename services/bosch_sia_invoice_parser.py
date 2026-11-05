"""P21 - Deterministic SAP Robert Bosch France / Sia Abrasives parser.

The same issuer ships invoices, credit notes and "avoir + refacturation"
pairs under opaque filenames (``_data_-_[archived__IC].pdf``,
``Y16V_SD_INVOICE...pdf``). The parser therefore trusts the PDF content, never
the filename: the real document type is read from ``Avoir <num> Du <date>`` /
``Facture <num> Du <date>`` or from ``Montant TTC avoir|débit``.
"""

from __future__ import annotations

import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping

from schemas.folder_state_schema import DocumentCategory
from services.pdf_splitter_and_linker import parse_date_value, sanitize_component
from services.sqlite_accounting_store import validate_tenant_id

BOSCH_SIA_SUPPLIER_NAME = "ROBERT BOSCH - SIA ABRASIVES"
BOSCH_SIA_SIREN = "572067684"
BOSCH_SIA_SIRET = "57206768400017"
BOSCH_SIA_VAT = "FR09572067684"
BOSCH_SIA_CLIENT_NUMBER = "43978631"
BOSCH_SIA_CATEGORY = DocumentCategory.ACHATS_MARCHANDISES.value
BOSCH_SIA_PAYMENT_METHOD = "LCR_FOURNISSEUR_DEBIT"
DOCUMENT_TYPE_FACTURE = "FACTURE"
DOCUMENT_TYPE_AVOIR = "AVOIR"
DOCUMENT_TYPE_REFACTURATION = "REFACTURATION"
REFACTURATION_PAIR_WARNING = "AVOIR ET REFAC CONDITIONS ERRONEES"
ORIGIN_MISSING_WARNING = (
    "ATTENTION : Facture d'origine {number} absente du Grand Livre"
)

_CENT = Decimal("0.01")
_BOSCH_SIREN = re.compile(r"\b572[\s.]*067[\s.]*684\b")
_BOSCH_VAT = re.compile(r"\bFR\s*0?9\s*572[\s.]*067[\s.]*684\b", re.IGNORECASE)
_BOSCH_EMAIL = re.compile(r"@(?:sia-abrasives\.com|bosch\.com)\b", re.IGNORECASE)
_BOSCH_NAME = re.compile(
    r"\b(?:Robert\s+Bosch|ROBERT\s+BOSCH|sia\s+Abrasives|SIA)\b",
    re.IGNORECASE,
)
_CLIENT_NUMBER = re.compile(r"\b43978631\b")
_HEADER = re.compile(
    r"\b(Facture|Avoir)\s+([0-9]{5,})\s+Du\s+"
    r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
    re.IGNORECASE,
)
_AVOIR_TOTAL = re.compile(
    r"Montant\s*TTC\s*avoir\s*([0-9][0-9\s.,]*)\s*EUR", re.IGNORECASE
)
_DEBIT_TOTAL = re.compile(
    r"Montant\s*TTC\s*d[ée]bit\s*([0-9][0-9\s.,]*)\s*EUR", re.IGNORECASE
)
_TOTAL_TRIPLE = re.compile(
    r"(?m)^\s*([0-9][0-9\s.,]*,\d{2})\s+"
    r"([0-9][0-9\s.,]*,\d{2})\s*\n\s*"
    r"([0-9][0-9\s.,]*,\d{2})\s*EUR"
)
_DUE_DATE = re.compile(
    r"Ech\S*ance\s+de\s+paiement\s+(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
    re.IGNORECASE,
)
_ORIGIN = re.compile(
    r"Document\s+d['\u2019]origine\s*:\s*([0-9]{5,})\s*,\s*"
    r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
    re.IGNORECASE,
)
_ORIGIN_REFAC = re.compile(
    r"Remplace\s+la\s+facture\s+no\s+([0-9]{5,})\s+du\s+"
    r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
    re.IGNORECASE,
)
_CLIENT_LABEL = re.compile(r"\bClient\s+livr\S*\s*\(\s*([0-9]+)\s*\)", re.IGNORECASE)


class BoschSiaInvoiceError(ValueError):
    """Invalid Bosch/Sia document; ``code`` is stable for tests and logs."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class BoschSiaInvoice:
    """One normalised Bosch/Sia invoice or credit note."""

    document_type: str
    document_number: str
    document_date: date | None = None
    due_date: date | None = None
    amount_ht: Decimal = Decimal("0.00")
    amount_tva: Decimal = Decimal("0.00")
    amount_ttc: Decimal = Decimal("0.00")
    origin_document_number: str = ""
    origin_document_date: date | None = None
    is_refacturation_pair: bool = False
    supplier: str = BOSCH_SIA_SUPPLIER_NAME
    siret: str = BOSCH_SIA_SIRET
    vat_number: str = BOSCH_SIA_VAT
    client_number: str = BOSCH_SIA_CLIENT_NUMBER
    category: str = BOSCH_SIA_CATEGORY
    payment_method: str = BOSCH_SIA_PAYMENT_METHOD
    source_filename: str = ""
    normalized_filename: str = ""
    warnings: tuple[str, ...] = ()
    raw_text: str = field(default="", compare=False, repr=False)

    @property
    def is_credit_note(self) -> bool:
        return self.document_type == DOCUMENT_TYPE_AVOIR

    @property
    def signed_amount_ht(self) -> Decimal:
        return -self.amount_ht if self.is_credit_note else self.amount_ht

    @property
    def signed_amount_tva(self) -> Decimal:
        return -self.amount_tva if self.is_credit_note else self.amount_tva

    @property
    def signed_amount_ttc(self) -> Decimal:
        return -self.amount_ttc if self.is_credit_note else self.amount_ttc

    @property
    def line_id(self) -> str:
        return (
            "BOSCH_SIA_"
            f"{sanitize_component(self.document_type, uppercase=True)}_"
            f"{sanitize_component(self.document_number, uppercase=True)}"
        )[:128]

    def as_ledger_line(self) -> dict[str, Any]:
        """Return the ``reconciliation_lines`` payload of this document."""
        label = f"{self.document_type} {self.document_number}"
        if self.origin_document_number:
            label += f" (origine facture {self.origin_document_number})"
        if self.is_refacturation_pair:
            label += f" - {REFACTURATION_PAIR_WARNING}"
        for warning in self.warnings:
            label += f" - {warning}"
        return {
            "operation_date": (
                self.document_date.isoformat() if self.document_date else None
            ),
            "label": label,
            "counterparty": self.supplier,
            "invoice_id": self.document_number,
            "invoice_url": self.normalized_filename or None,
            "piece_pdf": self.normalized_filename or None,
            "category": self.category,
            "amount_ht": str(self.signed_amount_ht.quantize(_CENT)),
            "amount_tva": str(self.signed_amount_tva.quantize(_CENT)),
            "amount_ttc": str(self.signed_amount_ttc.quantize(_CENT)),
            "document_type": self.document_type,
            "status": "VALIDE",
            "payment_method": self.payment_method,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "origin_document_number": self.origin_document_number or None,
            "origin_document_date": (
                self.origin_document_date.isoformat()
                if self.origin_document_date
                else None
            ),
            "is_refacturation_pair": "1" if self.is_refacturation_pair else "0",
            "already_paid_on_invoice": "0",
        }


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in text if not unicodedata.combining(char))


def _pdf_text(source: str | Path | bytes | bytearray) -> str:
    try:
        import fitz

        if hasattr(fitz, "TOOLS"):
            fitz.TOOLS.mupdf_display_errors(False)
    except ImportError as exc:  # pragma: no cover
        raise BoschSiaInvoiceError("PDF_SUPPORT_UNAVAILABLE") from exc
    try:
        if isinstance(source, (bytes, bytearray)):
            document = fitz.open(stream=bytes(source), filetype="pdf")
        else:
            document = fitz.open(str(source))
    except Exception as exc:  # noqa: BLE001
        raise BoschSiaInvoiceError("PDF_EXTRACTION_FAILED", str(exc)) from exc
    try:
        return "\n".join(page.get_text("text") for page in document)
    finally:
        document.close()


def is_bosch_sia_document(
    source: str | Path | bytes | bytearray,
    *,
    filename: str | None = None,
    text: str | None = None,
) -> bool:
    """True when the content belongs to Robert Bosch France / Sia Abrasives."""
    haystack = text if text is not None else _pdf_text(source)
    name = filename or (source.name if isinstance(source, Path) else "")
    combined = f"{name}\n{haystack}"
    markers = (
        _BOSCH_SIREN.search(combined),
        _BOSCH_VAT.search(combined),
        _BOSCH_EMAIL.search(combined),
        _CLIENT_NUMBER.search(combined),
    )
    if any(markers):
        return True
    return _BOSCH_NAME.search(combined) is not None and _HEADER.search(combined) is not None


def _parse_decimal(value: str) -> Decimal:
    text = str(value or "").replace("\u00a0", " ").replace("\u202f", " ")
    text = re.sub(r"[^0-9,.-]", "", text.replace(" ", ""))
    if not text:
        raise BoschSiaInvoiceError("INVALID_AMOUNT", repr(value))
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
        raise BoschSiaInvoiceError("INVALID_AMOUNT", repr(value)) from exc


def _totals(text: str, document_type: str) -> tuple[Decimal, Decimal, Decimal]:
    blocks = [match.groups() for match in _TOTAL_TRIPLE.finditer(text)]
    if not blocks:
        amount = Decimal("0.00")
        if document_type == DOCUMENT_TYPE_AVOIR:
            match = _AVOIR_TOTAL.search(text)
        else:
            match = _DEBIT_TOTAL.search(text)
        if match is not None:
            amount = _parse_decimal(match.group(1))
        return Decimal("0.00"), Decimal("0.00"), amount
    ht, tva, ttc = blocks[0]
    return _parse_decimal(ht), _parse_decimal(tva), _parse_decimal(ttc)


def normalized_bosch_sia_filename(document: BoschSiaInvoice) -> str:
    supplier = sanitize_component("BOSCH_SIA", uppercase=True)
    kind = sanitize_component(document.document_type, uppercase=True)
    number = sanitize_component(document.document_number, uppercase=True)
    if document.document_date is None:
        return f"{supplier}_{kind}_{number}.pdf"
    return (
        f"{supplier}_{kind}_{number}_"
        f"{document.document_date.isoformat()}.pdf"
    )


def parse_bosch_sia_invoice(
    source: str | Path | bytes | bytearray,
    *,
    filename: str | None = None,
    known_document_numbers: Iterable[str] | None = None,
) -> BoschSiaInvoice:
    """Parse one Bosch/Sia PDF, trusting content over the filename."""
    text = _pdf_text(source)
    if not is_bosch_sia_document(source, filename=filename, text=text):
        raise BoschSiaInvoiceError("NOT_BOSCH_SIA_DOCUMENT", filename or "")
    header = _HEADER.search(text)
    document_type = DOCUMENT_TYPE_FACTURE
    document_number = ""
    document_date: date | None = None
    if header is not None:
        raw_kind = _fold(header.group(1)).upper()
        document_type = (
            DOCUMENT_TYPE_AVOIR if raw_kind == DOCUMENT_TYPE_AVOIR else DOCUMENT_TYPE_FACTURE
        )
        document_number = header.group(2)
        document_date = parse_date_value(header.group(3))
    else:
        avoir = _AVOIR_TOTAL.search(text)
        debit = _DEBIT_TOTAL.search(text)
        if avoir is not None and debit is None:
            document_type = DOCUMENT_TYPE_AVOIR
        number_match = re.search(r"\bN\S*de\s+document\s+([0-9]{5,})", text, re.IGNORECASE)
        if number_match is not None:
            document_number = number_match.group(1)
    if not document_number:
        raise BoschSiaInvoiceError("DOCUMENT_NUMBER_NOT_FOUND")
    if document_date is None:
        date_match = re.search(
            r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\b", text
        )
        document_date = (
            parse_date_value(date_match.group(1)) if date_match else None
        )
    due = _DUE_DATE.search(text)
    due_date = parse_date_value(due.group(1)) if due is not None else None
    origin = _ORIGIN.search(text) or _ORIGIN_REFAC.search(text)
    origin_number = origin.group(1) if origin is not None else ""
    origin_date = parse_date_value(origin.group(2)) if origin is not None else None
    is_refacturation_pair = REFACTURATION_PAIR_WARNING in _fold(text).upper()
    ht, tva, ttc = _totals(text, document_type)
    client = _CLIENT_LABEL.search(text)
    source_name = (
        filename
        or (source.name if isinstance(source, Path) else "")
        or (Path(source).name if isinstance(source, str) else "")
        or ""
    )
    warnings: list[str] = []
    if origin_number and known_document_numbers is not None:
        known = {str(item).upper() for item in known_document_numbers}
        if origin_number.upper() not in known:
            warnings.append(ORIGIN_MISSING_WARNING.format(number=origin_number))
    document = BoschSiaInvoice(
        document_type=document_type,
        document_number=document_number,
        document_date=document_date,
        due_date=due_date,
        amount_ht=ht,
        amount_tva=tva,
        amount_ttc=ttc,
        origin_document_number=origin_number,
        origin_document_date=origin_date,
        is_refacturation_pair=is_refacturation_pair,
        client_number=client.group(1) if client is not None else BOSCH_SIA_CLIENT_NUMBER,
        source_filename=source_name,
        warnings=tuple(warnings),
        raw_text=text,
    )
    return BoschSiaInvoice(
        **{
            **document.__dict__,
            "normalized_filename": normalized_bosch_sia_filename(document),
        }
    )


def bosch_sia_origin_alert(
    document: BoschSiaInvoice, known_document_numbers: Iterable[str]
) -> str | None:
    """Return the missing-origin warning for one credit note, if any."""
    if not document.origin_document_number:
        return None
    known = {str(item).upper() for item in known_document_numbers}
    if document.origin_document_number.upper() in known:
        return None
    return ORIGIN_MISSING_WARNING.format(number=document.origin_document_number)


def collect_bosch_sia_origin_alerts(
    documents: Iterable[BoschSiaInvoice],
    *,
    known_document_numbers: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Warn on every credit note whose original invoice is absent from the batch."""
    parsed = list(documents)
    known = {
        document.document_number.upper()
        for document in parsed
        if document.document_type == DOCUMENT_TYPE_FACTURE
    }
    if known_document_numbers is not None:
        known.update(str(item).upper() for item in known_document_numbers)
    alerts: list[str] = []
    for document in parsed:
        if not document.is_credit_note:
            continue
        alert = bosch_sia_origin_alert(document, known)
        if alert is not None:
            alerts.append(alert)
    return tuple(alerts)


def rename_bosch_sia_pdf(
    source: str | Path,
    *,
    target_dir: str | Path | None = None,
    copy: bool = False,
) -> Path:
    """Move or copy a generically named Bosch/Sia file to its clean name."""
    source_path = Path(source)
    document = parse_bosch_sia_invoice(source_path)
    destination_dir = Path(target_dir) if target_dir is not None else source_path.parent
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / document.normalized_filename
    if target.exists() and target.resolve() == source_path.resolve():
        return target
    if copy:
        shutil.copy2(source_path, target)
    else:
        source_path.replace(target)
    return target


def import_bosch_sia_document(
    store: Any,
    tenant_id: str,
    document: BoschSiaInvoice,
    *,
    piece_pdf: str | None = None,
) -> str:
    """Persist one Bosch/Sia document, debit for credit notes and credit otherwise."""
    tenant = validate_tenant_id(tenant_id)
    line = document.as_ledger_line()
    if piece_pdf:
        line["piece_pdf"] = piece_pdf
        line["invoice_url"] = piece_pdf
    return store.upsert_document_line(tenant, document.line_id, line)


def import_bosch_sia_pdf(
    store: Any,
    tenant_id: str,
    source: str | Path,
    *,
    known_document_numbers: Iterable[str] | None = None,
    piece_pdf: str | None = None,
) -> BoschSiaInvoice:
    document = parse_bosch_sia_invoice(
        source, known_document_numbers=known_document_numbers
    )
    import_bosch_sia_document(store, tenant_id, document, piece_pdf=piece_pdf)
    return document


__all__ = [
    "BOSCH_SIA_CATEGORY",
    "BOSCH_SIA_CLIENT_NUMBER",
    "BOSCH_SIA_PAYMENT_METHOD",
    "BOSCH_SIA_SIREN",
    "BOSCH_SIA_SIRET",
    "BOSCH_SIA_SUPPLIER_NAME",
    "BOSCH_SIA_VAT",
    "BoschSiaInvoice",
    "BoschSiaInvoiceError",
    "DOCUMENT_TYPE_AVOIR",
    "DOCUMENT_TYPE_FACTURE",
    "DOCUMENT_TYPE_REFACTURATION",
    "ORIGIN_MISSING_WARNING",
    "REFACTURATION_PAIR_WARNING",
    "bosch_sia_origin_alert",
    "collect_bosch_sia_origin_alerts",
    "import_bosch_sia_document",
    "import_bosch_sia_pdf",
    "is_bosch_sia_document",
    "normalized_bosch_sia_filename",
    "parse_bosch_sia_invoice",
    "rename_bosch_sia_pdf",
]
