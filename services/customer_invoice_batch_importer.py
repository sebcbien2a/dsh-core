"""P20 - Bulk customer-invoice importer for the APD accounting flow.

The module ingests the Sage ``Liste des factures clients`` export (CSV, XLSX or
XML) and, when available, the grouped ``factures clients.pdf`` document. The
PDF is never duplicated on disk: pages are logically grouped by invoice number
(``FA\\d+``), the client SIRET / VAT number and the settlement mention printed
in the footer of the last page are extracted, then the payment habit is stored
in ``counterparty_payment_rules``.

Invoices are persisted in :class:`services.sqlite_accounting_store.SqliteAccountingStore`
as ``VENTES_CLIENTS`` lines (collected VAT) and exported to
``04_EXPORT_COMPTA/<tenant>/TABLEAU_VENTES_CLIENTS.xlsx``.
"""

from __future__ import annotations

import csv
import io
import re
import shutil
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from schemas.folder_state_schema import DocumentCategory, FolderState
from schemas.payment_method_schema import (
    normalize_expected_payment_method,
    payment_method_matches,
)
from services.folder_tree_service import ensure_tree
from services.pdf_splitter_and_linker import (
    PIECE_LINK_HEADER,
    SALES_SUBDIR,
    find_piece_for_document,
    relative_link,
    split_customer_invoices_pdf,
)
from services.sqlite_accounting_store import (
    AccountingStoreError,
    get_shared_sqlite_store,
    validate_tenant_id,
)

SAGE_INVOICE_FILENAME = "Liste des factures clients.csv"
MULTI_INVOICE_PDF_FILENAME = "factures clients.pdf"
SALES_WORKBOOK_FILENAME = "TABLEAU_VENTES_CLIENTS.xlsx"
CUSTOMER_INVOICE_DOCUMENT_TYPE = "CUSTOMER_INVOICE"
SALES_LINE_PREFIX = "SALE-"
PAID_STATUS = "PAYEE_RAPPROCHEE"
PENDING_STATUS = "EN_ATTENTE_REGLEMENT"

_CENT = Decimal("0.01")
_CURRENCY = re.compile(r"(?:EUR|EUROS|E|€)\s*$", re.IGNORECASE)
_INVOICE_NUMBER = re.compile(r"\b(FA\d{3,})\b", re.IGNORECASE)
_PDF_TAIL_PAYMENT = re.compile(
    r"\bpar\s+(?:une\s+)?(LCR|VIREMENT|CH(?:E|È)QUE|CHQ)\b",
    re.IGNORECASE,
)
_PDF_PAYMENT_LINE = re.compile(
    r"(?:R[eè]glement\s+)?par\s+(?:une\s+)?(?:LCR|virement|ch[eè]que|chq)[^\n.;]{0,100}",
    re.IGNORECASE,
)
_SIRET = re.compile(r"\bSIRET\s*[:\-]?\s*((?:\d[\s.]*){14})", re.IGNORECASE)
_SIRET_CLIENT = re.compile(
    r"\bSIRET\s*client\s*[:\-]?\s*((?:\d[\s.]*){14})", re.IGNORECASE
)
_VAT = re.compile(
    r"\bTVA\s*(?:intracommunautaire)?\s*(?:n[°o]\s*)?[:\-]?\s*"
    r"(FR\s*[0-9A-Z]{2}\s*(?:\d[\s.]*){9})",
    re.IGNORECASE,
)
_VAT_CLIENT = re.compile(
    r"\bTVA\s*intracom(?:munautaire)?\s*client\s*[:\-]?\s*"
    r"(FR\s*[0-9A-Z]{2}\s*(?:\d[\s.]*){9})",
    re.IGNORECASE,
)
_VAT_GENERIC = re.compile(r"\b(FR\s*[0-9A-Z]{2}\s*(?:\d[\s.]*){9})\b", re.IGNORECASE)
_CLIENT_LABEL = re.compile(
    r"(?<!SIRET )(?<!intracom )\bClient\s*[:\-]\s*([^\n\r]+)",
    re.IGNORECASE,
)
_CLIENT_BLOCK = re.compile(
    r"\bClient\s*[:\-][^\n\r]*(?:\r?\n[^\n\r]*){0,8}", re.IGNORECASE
)
_TOTAL_HT = re.compile(r"\bTotal\s*HT\s*[:\-]?\s*([\d\s\u00a0.,]+)", re.IGNORECASE)
_TOTAL_TVA = re.compile(
    r"\b(?:Total\s*TVA|Montant\s*TVA)\s*[:\-]?\s*([\d\s\u00a0.,]+)",
    re.IGNORECASE,
)
_TOTAL_TTC = re.compile(r"\bTotal\s*TTC\s*[:\-]?\s*([\d\s\u00a0.,]+)", re.IGNORECASE)
_PDF_DATE = re.compile(
    r"\bDate\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
    re.IGNORECASE,
)
_PDF_DUE_DATE = re.compile(
    r"\bFacture\s+payable\s+le\s+(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
    re.IGNORECASE,
)
_SPACES = re.compile(r"\s+")

_SAGE_HEADERS: dict[str, str] = {
    "npiece": "number",
    "numeropiece": "number",
    "nfacture": "number",
    "numerofacture": "number",
    "statut": "sage_status",
    "statutpiece": "sage_status",
    "datepiece": "piece_date",
    "datefacture": "piece_date",
    "client": "customer",
    "nomclient": "customer",
    "societe": "company",
    "totalht": "total_ht",
    "montantht": "total_ht",
    "totaltva": "total_tva",
    "montanttva": "total_tva",
    "totalttc": "total_ttc",
    "montantttc": "total_ttc",
    "solde du": "balance_due",
    "soldedu": "balance_due",
    "solde": "balance_due",
    "dateecheance": "due_date",
    "echeance": "due_date",
}
_REQUIRED_HEADERS = frozenset({"number"})
_KNOWN_HEADERS = frozenset(_SAGE_HEADERS.values())

SALES_WORKBOOK_HEADERS: tuple[str, ...] = (
    "N° Facture",
    "Statut Sage",
    "Date Pièce",
    "Client",
    "Société",
    "Total HT",
    "Total TVA",
    "Total TTC",
    "Solde Dû",
    "Date Échéance",
    "SIRET Client",
    "TVA Client",
    "Mode de Paiement",
    "Conditions de Paiement",
    "Statut Rapprochement Bancaire",
    "Date Rapprochement",
    "Référence OFX",
    "Montant Rapproché",
    "Pièce PDF (Clic)",
)


class CustomerBatchImportError(ValueError):
    """Invalid Sage / PDF customer-invoice batch; ``code`` is stable."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class CustomerInvoice:
    """One customer invoice after Sage and PDF enrichment."""

    number: str
    sage_status: str = ""
    piece_date: date | None = None
    customer: str = ""
    company: str = ""
    total_ht: Decimal = Decimal("0.00")
    total_tva: Decimal = Decimal("0.00")
    total_ttc: Decimal = Decimal("0.00")
    balance_due: Decimal = Decimal("0.00")
    due_date: date | None = None
    client_siret: str = ""
    client_vat_number: str = ""
    payment_method: str = ""
    payment_terms: str = ""
    source: str = "SAGE"
    page_start: int | None = None
    page_end: int | None = None

    @property
    def line_id(self) -> str:
        safe = re.sub(r"[^A-Za-z0-9._:-]", "_", self.number.strip())
        return f"{SALES_LINE_PREFIX}{safe}"[:128]

    @property
    def expected_payment_method(self) -> str:
        if not self.payment_method:
            return ""
        try:
            return normalize_expected_payment_method(self.payment_method)
        except Exception:  # noqa: BLE001 - unknown method stays empty
            return ""


@dataclass(frozen=True)
class PdfInvoiceBatch:
    """Logically split pages of one grouped multi-invoice PDF."""

    invoices: tuple[CustomerInvoice, ...]
    page_count: int
    source: str = "PDF"

    def by_number(self) -> dict[str, CustomerInvoice]:
        return {invoice.number: invoice for invoice in self.invoices}


@dataclass(frozen=True)
class CustomerInvoiceImportReport:
    """Observable outcome of one bulk import."""

    invoices: tuple[CustomerInvoice, ...]
    created: int = 0
    updated: int = 0
    preserved: int = 0
    rules_updated: int = 0
    total_ht: Decimal = Decimal("0.00")
    total_tva: Decimal = Decimal("0.00")
    total_ttc: Decimal = Decimal("0.00")
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class CustomerSalesExportReport:
    """Workbook produced for the manager."""

    path: Path
    row_count: int
    reconciled: int
    pending: int
    total_ht: Decimal
    total_tva: Decimal
    total_ttc: Decimal


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return _SPACES.sub(" ", text).strip()


def _header_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def _clean(value: Any) -> str:
    return _SPACES.sub(" ", str(value or "").replace("\u00a0", " ")).strip()


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value).strip()


def _parse_decimal(value: Any, *, code: str = "INVALID_AMOUNT") -> Decimal:
    if isinstance(value, Decimal):
        return value.quantize(_CENT)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return Decimal(str(value)).quantize(_CENT)
    text = _clean(value)
    text = _CURRENCY.sub("", text).strip()
    text = text.replace(" ", "").replace("'", "")
    if not text:
        return Decimal("0.00")
    sign = "-" if text.startswith("-") else ""
    text = text.lstrip("+-")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return Decimal(f"{sign}{text}").quantize(_CENT)
    except InvalidOperation as exc:
        raise CustomerBatchImportError(code, repr(value)) from exc


def _parse_date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _clean(value)
    if not text:
        return None
    for pattern in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%y"):
        try:
            return datetime.strptime(text[:10], pattern).date()
        except ValueError:
            continue
    return None


def _read_payload(source: str | Path | bytes | bytearray) -> tuple[bytes, str]:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source), ""
    path = Path(source)
    try:
        return path.read_bytes(), path.name
    except OSError as exc:
        raise CustomerBatchImportError("UNREADABLE_SAGE_FILE", str(path)) from exc


def _decode_source(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("cp1252", "replace")


def _detect_delimiter(sample: str) -> str:
    counts = {delimiter: sample.count(delimiter) for delimiter in (";", "\t", ",")}
    delimiter = max(counts, key=counts.get)
    return delimiter if counts[delimiter] else ";"


def _header_indexes(header: Sequence[Any]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for index, cell in enumerate(header):
        key = _header_key(cell)
        field = _SAGE_HEADERS.get(key)
        if field is not None and field not in mapping:
            mapping[field] = index
    return mapping


def _find_header(rows: Sequence[Sequence[Any]]) -> tuple[int, dict[str, int]]:
    for index, row in enumerate(rows[:20]):
        mapping = _header_indexes(row)
        if _REQUIRED_HEADERS.issubset(mapping):
            return index, mapping
    raise CustomerBatchImportError("SAGE_HEADER_NOT_FOUND")


def _rows_to_invoices(rows: Sequence[Sequence[Any]]) -> list[CustomerInvoice]:
    header_index, mapping = _find_header(rows)

    def value(row: Sequence[Any], field: str) -> Any:
        index = mapping.get(field)
        if index is None or index >= len(row):
            return None
        return row[index]

    invoices: list[CustomerInvoice] = []
    for row in rows[header_index + 1 :]:
        number = _clean(value(row, "number"))
        if not number:
            continue
        folded_number = _fold(number).upper()
        if folded_number in {"TOTAL", "TOTAUX", "TOTAL GENERAL"}:
            continue
        customer = _clean(value(row, "customer"))
        company = _clean(value(row, "company"))
        total_ht = _parse_decimal(value(row, "total_ht"), code="INVALID_TOTAL_HT")
        total_tva = _parse_decimal(value(row, "total_tva"), code="INVALID_TOTAL_TVA")
        total_ttc = _parse_decimal(value(row, "total_ttc"), code="INVALID_TOTAL_TTC")
        if total_ttc == 0 and (total_ht or total_tva):
            total_ttc = (total_ht + total_tva).quantize(_CENT)
        balance = _parse_decimal(value(row, "balance_due"), code="INVALID_BALANCE")
        if _clean(value(row, "balance_due")) == "":
            balance = total_ttc
        invoices.append(
            CustomerInvoice(
                number=number.upper(),
                sage_status=_clean(value(row, "sage_status")),
                piece_date=_parse_date_value(value(row, "piece_date")),
                customer=customer or company,
                company=company,
                total_ht=total_ht,
                total_tva=total_tva,
                total_ttc=total_ttc,
                balance_due=balance,
                due_date=_parse_date_value(value(row, "due_date")),
                source="SAGE",
            )
        )
    if not invoices:
        raise CustomerBatchImportError("NO_SAGE_INVOICES")
    return invoices


def parse_sage_customer_invoices(
    source: str | Path | bytes | bytearray,
    *,
    filename: str | None = None,
) -> list[CustomerInvoice]:
    """Parse a Sage customer-invoice export (CSV, XLSX or XML)."""
    payload, detected_name = _read_payload(source)
    name = (filename or detected_name).casefold()
    suffix = Path(name).suffix
    if suffix in {".xlsx", ".xlsm"} or (not suffix and payload.startswith(b"PK")):
        return _parse_sage_xlsx(payload)
    if suffix == ".xml" or payload.lstrip().startswith(b"<"):
        return _parse_sage_xml(payload)
    if suffix in {".xls", ".csv", ""}:
        return _parse_sage_csv(payload)
    raise CustomerBatchImportError("UNSUPPORTED_SAGE_FORMAT", name or suffix)


def _parse_sage_csv(payload: bytes) -> list[CustomerInvoice]:
    text = _decode_source(payload)
    sample = "\n".join(text.splitlines()[:3])
    delimiter = _detect_delimiter(sample)
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    return _rows_to_invoices(rows)


def _parse_sage_xlsx(payload: bytes) -> list[CustomerInvoice]:
    try:
        workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - normalised error
        raise CustomerBatchImportError("INVALID_SAGE_XLSX", str(exc)) from exc
    try:
        sheet = workbook.worksheets[0]
        rows = [list(row) for row in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()
    return _rows_to_invoices(rows)


def _xml_row_values(element: ET.Element) -> dict[str, str]:
    values: dict[str, str] = {}
    for key, value in element.attrib.items():
        field = _SAGE_HEADERS.get(_header_key(key))
        if field is not None:
            values[field] = value
    for child in element:
        if len(child):
            continue
        field = _SAGE_HEADERS.get(_header_key(child.tag))
        if field is not None and field not in values:
            values[field] = child.text or ""
    return values


def _parse_sage_xml(payload: bytes) -> list[CustomerInvoice]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise CustomerBatchImportError("INVALID_SAGE_XML", str(exc)) from exc
    rows: list[list[str]] = []
    for element in root.iter():
        values = _xml_row_values(element)
        if not _REQUIRED_HEADERS.issubset(values):
            continue
        keys = list(_SAGE_HEADERS.keys())
        rows.append([values.get(_SAGE_HEADERS[key], "") for key in keys])
    if not rows:
        raise CustomerBatchImportError("NO_SAGE_INVOICES")
    header = list(_SAGE_HEADERS.keys())
    return _rows_to_invoices([header, *rows])


def _pdf_pages(source: str | Path | bytes | bytearray) -> list[str]:
    try:
        import fitz

        if hasattr(fitz, "TOOLS"):
            fitz.TOOLS.mupdf_display_errors(False)
    except ImportError as exc:  # pragma: no cover - dependency is installed
        raise CustomerBatchImportError("PDF_SUPPORT_UNAVAILABLE") from exc
    try:
        if isinstance(source, (bytes, bytearray)):
            document = fitz.open(stream=bytes(source), filetype="pdf")
        else:
            document = fitz.open(str(source))
    except Exception as exc:  # noqa: BLE001 - normalised error
        raise CustomerBatchImportError("PDF_EXTRACTION_FAILED", str(exc)) from exc
    try:
        return [page.get_text("text") for page in document]
    finally:
        document.close()


def _normalise_digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _normalise_vat(value: str) -> str:
    return re.sub(r"\s+", "", value).upper()


def _extract_payment(text: str) -> tuple[str, str]:
    folded = _fold(text)
    match = _PDF_TAIL_PAYMENT.search(text) or _PDF_TAIL_PAYMENT.search(folded)
    if match is None:
        return "", ""
    raw = _fold(match.group(1)).upper()
    if raw == "LCR":
        method = "LCR"
    elif raw.startswith("VIREMENT"):
        method = "VIREMENT"
    else:
        method = "CHEQUE"
    line_match = _PDF_PAYMENT_LINE.search(text)
    terms = _clean(line_match.group(0)) if line_match is not None else ""
    return method, terms


_ISSUER_VAT = re.compile(
    r"N[°o]\s*TVA\s*intracom\s*:\s*(FR\s*[0-9A-Z]{2}(?:\s*\d){9})",
    re.IGNORECASE,
)
_CLIENT_NAME_STOPWORDS = (
    "total",
    "net ",
    "facture",
    "date",
    "client",
    "code",
    "base",
    "taux",
    "montant",
    "n°",
    "siret",
    "réserve",
    "penalit",
    "pénalit",
    "facture payable",
)


def _extract_client_name(text: str, client_block: str) -> str:
    """Return the printed customer name, not the Sage account code.

    Real Sage PDFs print the issuer VAT block first, then the customer name and
    postal address, then the totals. The customer name is the first non-label
    line after the issuer VAT number. A fallback on an explicit ``Client :``
    label covers alternate templates.
    """
    issuer = _ISSUER_VAT.search(text)
    if issuer is not None:
        for raw_line in text[issuer.end() :].splitlines():
            value = _clean(raw_line)
            if not value:
                continue
            folded = _fold(value).casefold()
            if any(folded.startswith(stop) for stop in _CLIENT_NAME_STOPWORDS):
                continue
            if re.fullmatch(r"[\d\s.,]+", value):
                continue
            return value
    match = _CLIENT_LABEL.search(client_block) or _CLIENT_LABEL.search(text)
    return _clean(match.group(1)) if match is not None else ""


def _extract_pdf_invoice(
    number: str,
    pages: Sequence[str],
    page_start: int,
    page_end: int,
) -> CustomerInvoice:
    joined = "\n".join(pages)
    last_page = pages[-1] if pages else ""
    method, terms = _extract_payment(last_page)
    if not method:
        method, terms = _extract_payment(joined)
    client_block_match = _CLIENT_BLOCK.search(joined)
    client_block = (
        client_block_match.group(0) if client_block_match is not None else joined
    )
    siret_match = (
        _SIRET_CLIENT.search(joined)
        or _SIRET.search(client_block)
        or _SIRET.search(joined)
    )
    vat_match = (
        _VAT_CLIENT.search(joined)
        or _VAT.search(client_block)
        or _VAT_CLIENT.search(client_block)
        or _VAT_GENERIC.search(client_block)
        or _VAT.search(joined)
        or _VAT_GENERIC.search(joined)
    )
    total_ht = _TOTAL_HT.search(joined)
    total_tva = _TOTAL_TVA.search(joined)
    total_ttc = _TOTAL_TTC.search(joined)
    date_match = _PDF_DATE.search(joined)
    due_match = _PDF_DUE_DATE.search(joined)
    return CustomerInvoice(
        number=number.upper(),
        piece_date=(
            _parse_date_value(date_match.group(1)) if date_match is not None else None
        ),
        customer=_extract_client_name(joined, client_block),
        total_ht=_parse_decimal(total_ht.group(1)) if total_ht else Decimal("0.00"),
        total_tva=_parse_decimal(total_tva.group(1)) if total_tva else Decimal("0.00"),
        total_ttc=_parse_decimal(total_ttc.group(1)) if total_ttc else Decimal("0.00"),
        due_date=(
            _parse_date_value(due_match.group(1)) if due_match is not None else None
        ),
        client_siret=_normalise_digits(siret_match.group(1)) if siret_match else "",
        client_vat_number=(
            _normalise_vat(vat_match.group(1)) if vat_match is not None else ""
        ),
        payment_method=method,
        payment_terms=terms,
        source="PDF",
        page_start=page_start,
        page_end=page_end,
    )


def parse_multi_invoice_pdf(
    source: str | Path | bytes | bytearray,
) -> PdfInvoiceBatch:
    """Split a grouped PDF logically by ``FA\\d+`` invoice number."""
    pages = _pdf_pages(source)
    groups: dict[str, list[int]] = {}
    order: list[str] = []
    current: str | None = None
    for page_index, text in enumerate(pages, start=1):
        match = _INVOICE_NUMBER.search(text)
        if match is not None:
            current = match.group(1).upper()
            if current not in groups:
                groups[current] = []
                order.append(current)
            groups[current].append(page_index)
        elif current is not None:
            groups[current].append(page_index)
    invoices: list[CustomerInvoice] = []
    for number in order:
        indexes = groups[number]
        invoice_pages = [pages[index - 1] for index in indexes]
        invoices.append(
            _extract_pdf_invoice(
                number,
                invoice_pages,
                page_start=indexes[0],
                page_end=indexes[-1],
            )
        )
    if not invoices:
        raise CustomerBatchImportError("NO_INVOICES_IN_PDF")
    return PdfInvoiceBatch(invoices=tuple(invoices), page_count=len(pages))


def merge_customer_invoices(
    sage_invoices: Sequence[CustomerInvoice],
    pdf_batch: PdfInvoiceBatch | None = None,
) -> list[CustomerInvoice]:
    """Merge Sage amounts with PDF SIRET / VAT / settlement mentions."""
    pdf_by_number = pdf_batch.by_number() if pdf_batch is not None else {}
    used: set[str] = set()
    merged: list[CustomerInvoice] = []
    for invoice in sage_invoices:
        pdf = pdf_by_number.get(invoice.number)
        if pdf is None:
            merged.append(invoice)
            continue
        used.add(invoice.number)
        total_ht = invoice.total_ht or pdf.total_ht
        total_tva = invoice.total_tva or pdf.total_tva
        total_ttc = invoice.total_ttc or pdf.total_ttc
        if total_ttc == 0 and (total_ht or total_tva):
            total_ttc = (total_ht + total_tva).quantize(_CENT)
        merged.append(
            replace(
                invoice,
                customer=pdf.customer or invoice.customer,
                total_ht=total_ht,
                total_tva=total_tva,
                total_ttc=total_ttc,
                balance_due=(
                    invoice.balance_due
                    if invoice.balance_due != 0
                    else total_ttc
                ),
                client_siret=pdf.client_siret,
                client_vat_number=pdf.client_vat_number,
                payment_method=pdf.payment_method,
                payment_terms=pdf.payment_terms,
                page_start=pdf.page_start,
                page_end=pdf.page_end,
            )
        )
    for number, pdf in pdf_by_number.items():
        if number in used or pdf.total_ttc == 0:
            continue
        merged.append(pdf)
    return merged


def _invoice_line(invoice: CustomerInvoice) -> dict[str, Any]:
    return {
        "operation_date": (
            invoice.piece_date.isoformat() if invoice.piece_date else None
        ),
        "label": _clean(f"Facture {invoice.number} {invoice.customer}"),
        "counterparty": invoice.customer,
        "invoice_id": invoice.number,
        "invoice_url": f"{DocumentCategory.VENTES_CLIENTS.value}/{invoice.number}.pdf",
        "category": DocumentCategory.VENTES_CLIENTS.value,
        "amount_ht": str(invoice.total_ht.quantize(_CENT)),
        "amount_tva": str(invoice.total_tva.quantize(_CENT)),
        "amount_ttc": str(invoice.total_ttc.quantize(_CENT)),
        "document_type": CUSTOMER_INVOICE_DOCUMENT_TYPE,
        "status": "VALIDE",
        "payment_method": invoice.expected_payment_method or None,
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "client_siret": invoice.client_siret or None,
        "client_vat_number": invoice.client_vat_number or None,
        "payment_terms": invoice.payment_terms or None,
        "source_status": invoice.sage_status or None,
        "balance_due": str(invoice.balance_due.quantize(_CENT)),
        "company_name": invoice.company or None,
        "reconciliation_status": PENDING_STATUS,
    }


def _merge_payment_rule(
    existing: Mapping[str, Any] | None,
    new_method: str,
) -> tuple[str, list[str]]:
    """Keep LCR as the main habit and accumulate accepted fallbacks (P20)."""
    if existing is None:
        if new_method == "LCR":
            return "LCR", ["CHEQUE", "VIREMENT"]
        return new_method, []
    current = str(existing.get("expected_payment_method") or "").upper()
    fallbacks = [
        str(item).upper()
        for item in (existing.get("fallback_methods") or [])
        if str(item).strip()
    ]
    if current == "LCR":
        for fallback in ("CHEQUE", "VIREMENT"):
            if fallback not in fallbacks:
                fallbacks.append(fallback)
        if new_method and new_method != "LCR" and new_method not in fallbacks:
            fallbacks.append(new_method)
        return "LCR", fallbacks
    if new_method == "LCR":
        if current and current != "LCR" and current not in fallbacks:
            fallbacks.append(current)
        for fallback in ("CHEQUE", "VIREMENT"):
            if fallback not in fallbacks:
                fallbacks.append(fallback)
        return "LCR", fallbacks
    if current and new_method and current != new_method:
        if current not in fallbacks:
            fallbacks.append(current)
        return new_method, fallbacks
    return current or new_method, fallbacks


def import_customer_invoices(
    store: Any,
    tenant_id: str,
    invoices: Sequence[CustomerInvoice],
    *,
    update_payment_rules: bool = True,
) -> CustomerInvoiceImportReport:
    """Persist every invoice as a ``VENTES_CLIENTS`` line (idempotent)."""
    tenant = validate_tenant_id(tenant_id)
    if not isinstance(invoices, Sequence) or not invoices:
        raise CustomerBatchImportError("NO_INVOICES_TO_IMPORT")
    created = updated = preserved = rules_updated = 0
    errors: list[str] = []
    total_ht = total_tva = total_ttc = Decimal("0.00")
    applied_rules: set[tuple[str, str]] = set()
    for invoice in invoices:
        if invoice.total_ttc == 0 and (
            invoice.total_ht != 0 or invoice.total_tva != 0
        ):
            invoice = replace(
                invoice,
                total_ttc=(invoice.total_ht + invoice.total_tva).quantize(_CENT),
            )
        if invoice.balance_due == 0:
            invoice = replace(invoice, balance_due=invoice.total_ttc)
        line = _invoice_line(invoice)
        try:
            existing = store.get_line(tenant, invoice.line_id)
        except Exception:  # noqa: BLE001 - store errors reported below
            existing = None
        if existing is not None:
            if existing.get("reconciliation_status"):
                line["reconciliation_status"] = existing["reconciliation_status"]
                line["reconciliation_date"] = existing.get("reconciliation_date")
                line["reconciliation_reference"] = existing.get(
                    "reconciliation_reference"
                )
                line["reconciliation_amount"] = existing.get("reconciliation_amount")
            if existing.get("status") == "REJETE":
                line["status"] = "REJETE"
        try:
            outcome = store.upsert_document_line(tenant, invoice.line_id, line)
        except Exception as exc:  # noqa: BLE001 - one invoice never blocks the batch
            code = getattr(exc, "code", type(exc).__name__.upper())
            errors.append(f"{invoice.number}:{code}")
            continue
        if outcome == "created":
            created += 1
        elif outcome == "updated":
            updated += 1
        else:
            preserved += 1
        total_ht += invoice.total_ht
        total_tva += invoice.total_tva
        total_ttc += invoice.total_ttc
        if not update_payment_rules:
            continue
        method = invoice.expected_payment_method
        key = (invoice.customer.casefold(), method)
        if not invoice.customer or not method or key in applied_rules:
            continue
        applied_rules.add(key)
        try:
            existing_rule = store.get_counterparty_payment_rule(
                tenant, invoice.customer
            )
            primary, fallbacks = _merge_payment_rule(existing_rule, method)
            store.set_counterparty_payment_rule(
                tenant,
                invoice.customer,
                primary,
                confidence=Decimal("1.0"),
                source="IMPORT",
                fallback_methods=fallbacks,
            )
            rules_updated += 1
        except Exception as exc:  # noqa: BLE001 - rules never block invoices
            code = getattr(exc, "code", type(exc).__name__.upper())
            errors.append(f"RULE:{invoice.customer}:{code}")
    return CustomerInvoiceImportReport(
        invoices=tuple(invoices),
        created=created,
        updated=updated,
        preserved=preserved,
        rules_updated=rules_updated,
        total_ht=total_ht.quantize(_CENT),
        total_tva=total_tva.quantize(_CENT),
        total_ttc=total_ttc.quantize(_CENT),
        errors=tuple(errors),
    )


def import_customer_invoice_batch(
    store: Any,
    tenant_id: str,
    sage_source: str | Path | bytes | bytearray | None = None,
    *,
    pdf_source: str | Path | bytes | bytearray | None = None,
    filename: str | None = None,
    update_payment_rules: bool = True,
    split_export_dir: str | Path | None = None,
) -> CustomerInvoiceImportReport:
    """Parse Sage and/or grouped PDF, merge and persist the batch.

    ``sage_source`` may be ``None`` when ``pdf_source`` is supplied: the
    grouped multi-page PDF then becomes the primary source. That covers the
    April-October 2026 invoices that are absent from the January-March Sage
    CSV while keeping the merge behaviour when both sources are provided.
    """
    if sage_source is None and pdf_source is None:
        raise CustomerBatchImportError("NO_SOURCE")
    pdf_batch = (
        parse_multi_invoice_pdf(pdf_source) if pdf_source is not None else None
    )
    if sage_source is not None:
        sage_invoices = parse_sage_customer_invoices(sage_source, filename=filename)
        invoices = merge_customer_invoices(sage_invoices, pdf_batch)
    else:
        if pdf_batch is None or not pdf_batch.invoices:
            raise CustomerBatchImportError("NO_INVOICES_IN_PDF")
        invoices = list(pdf_batch.invoices)
    if pdf_source is not None and split_export_dir is not None:
        try:
            split_customer_pdf_for_export(
                pdf_source, split_export_dir, filename=filename
            )
        except Exception:  # noqa: BLE001 - a split never blocks the accounting import
            pass
    return import_customer_invoices(
        store,
        tenant_id,
        invoices,
        update_payment_rules=update_payment_rules,
    )


# ---------------------------------------------------------------------------
# TABLEAU_VENTES_CLIENTS.xlsx
# ---------------------------------------------------------------------------

_HEADER_FILL = "1F4E78"
_BAND_FILL = "EAF1F8"
_PAID_FILL = "C6EFCE"
_PENDING_FILL = "FFF2CC"
_BORDER_COLOR = "BFBFBF"


def _display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    return str(value)


def _atomic_save_workbook(workbook: Workbook, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-sales")
    try:
        workbook.save(temporary)
        try:
            temporary.replace(path)
        except OSError:
            shutil.copy2(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_sales_workbook(
    path: Path,
    rows: Sequence[tuple[Any, ...]],
    *,
    total_row: tuple[Any, ...] | None = None,
) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "TABLEAU_VENTES_CLIENTS"[:31]
    sheet.sheet_view.showGridLines = False
    thin = Side(style="thin", color=_BORDER_COLOR)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor=_HEADER_FILL)
    for column, header in enumerate(SALES_WORKBOOK_HEADERS, start=1):
        cell = sheet.cell(row=1, column=column, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = border
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 34
    status_column = SALES_WORKBOOK_HEADERS.index(
        "Statut Rapprochement Bancaire"
    ) + 1
    link_column = SALES_WORKBOOK_HEADERS.index(PIECE_LINK_HEADER) + 1
    for offset, values in enumerate(rows, start=2):
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=offset, column=column)
            cell.value = float(value) if isinstance(value, Decimal) else value
            cell.border = border
            if column == link_column and isinstance(value, str) and value:
                cell.hyperlink = value
                cell.font = Font(color="0563C1", underline="single")
            if offset % 2 == 0:
                cell.fill = PatternFill("solid", fgColor=_BAND_FILL)
            if isinstance(value, Decimal):
                cell.number_format = "#,##0.00"
                cell.alignment = Alignment(horizontal="right", vertical="center")
            else:
                cell.alignment = Alignment(vertical="center", wrap_text=True)
        status = sheet.cell(row=offset, column=status_column)
        if status.value == PAID_STATUS:
            status.fill = PatternFill("solid", fgColor=_PAID_FILL)
            status.font = Font(bold=True, color="006100")
        elif status.value == PENDING_STATUS:
            status.fill = PatternFill("solid", fgColor=_PENDING_FILL)
            status.font = Font(bold=True, color="9C6500")
    last_data_row = len(rows) + 1
    sheet.auto_filter.ref = (
        f"A1:{get_column_letter(len(SALES_WORKBOOK_HEADERS))}{last_data_row}"
    )
    sheet.freeze_panes = "A2"
    if total_row is not None:
        total_index = last_data_row + 1
        for column, value in enumerate(total_row, start=1):
            cell = sheet.cell(row=total_index, column=column)
            cell.value = float(value) if isinstance(value, Decimal) else value
            cell.font = Font(bold=True)
            cell.border = border
            cell.fill = PatternFill("solid", fgColor=_PENDING_FILL)
            if isinstance(value, Decimal):
                cell.number_format = "#,##0.00"
                cell.alignment = Alignment(horizontal="right", vertical="center")
    for column, header in enumerate(SALES_WORKBOOK_HEADERS, start=1):
        longest = len(header)
        for row_index in range(2, sheet.max_row + 1):
            longest = max(
                longest,
                len(_display(sheet.cell(row=row_index, column=column).value)),
            )
        sheet.column_dimensions[get_column_letter(column)].width = max(
            12, min(longest + 2, 60)
        )
    workbook.properties.creator = "DSH-Core"
    workbook.properties.title = "TABLEAU_VENTES_CLIENTS"
    _atomic_save_workbook(workbook, path)


def _row_date(value: Any) -> date | None:
    return _parse_date_value(value)


def customer_sales_rows(store: Any, tenant_id: str) -> list[dict[str, Any]]:
    return list(store.list_customer_invoices(tenant_id))


def _relative_piece_link(workbook_path: Path, row: Mapping[str, Any]) -> str | None:
    """Resolve the split piece of one invoice as a relative OneDrive link."""
    stored = str(row.get("piece_pdf") or "").strip()
    if stored:
        candidate = Path(stored)
        if not candidate.is_absolute():
            candidate = workbook_path.parent / candidate
        if candidate.is_file():
            return relative_link(candidate, workbook_path)
    number = str(row.get("invoice_id") or row.get("line_id") or "")
    found = find_piece_for_document(
        workbook_path.parent, number, side=SALES_SUBDIR
    )
    if found is None:
        return None
    return relative_link(found, workbook_path)


def split_customer_pdf_for_export(
    pdf_source: str | Path | bytes | bytearray,
    export_dir: str | Path,
    *,
    filename: str | None = None,
) -> Any:
    """Physically split a grouped customer PDF next to the export workbooks."""
    if isinstance(pdf_source, (bytes, bytearray)):
        target = Path(export_dir) / (filename or MULTI_INVOICE_PDF_FILENAME)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bytes(pdf_source))
        pdf_source = target
    return split_customer_invoices_pdf(pdf_source, export_dir)


def export_customer_sales_workbook(
    tenant_id: str,
    *,
    store: Any | None = None,
    root: str | Path | None = None,
    export_dir: str | Path | None = None,
    output_path: str | Path | None = None,
) -> CustomerSalesExportReport:
    """Write ``TABLEAU_VENTES_CLIENTS.xlsx`` with payment lettering status."""
    tenant = validate_tenant_id(tenant_id)
    selected_store = store if store is not None else get_shared_sqlite_store()
    if output_path is not None:
        path = Path(output_path)
    else:
        tree = ensure_tree(tenant, root)
        output_dir = (
            Path(export_dir)
            if export_dir is not None
            else tree.state_dir(FolderState.EXPORT_COMPTA) / tenant
        )
        path = output_dir / SALES_WORKBOOK_FILENAME
    rows = sorted(
        customer_sales_rows(selected_store, tenant),
        key=lambda row: (
            str(row.get("operation_date") or ""),
            str(row.get("invoice_id") or row.get("line_id") or ""),
        ),
    )
    table_rows: list[tuple[Any, ...]] = []
    total_ht = total_tva = total_ttc = Decimal("0.00")
    reconciled = pending = 0
    for row in rows:
        ht = _parse_decimal(row.get("amount_ht"))
        tva = _parse_decimal(row.get("amount_tva"))
        ttc = _parse_decimal(row.get("amount_ttc"))
        total_ht += ht
        total_tva += tva
        total_ttc += ttc
        status = str(row.get("reconciliation_status") or PENDING_STATUS).upper()
        if status == PAID_STATUS:
            reconciled += 1
        else:
            status = PENDING_STATUS
            pending += 1
        table_rows.append(
            (
                row.get("invoice_id") or row.get("line_id") or "",
                row.get("source_status") or "",
                _row_date(row.get("operation_date")),
                row.get("counterparty") or "",
                row.get("company_name") or "",
                ht,
                tva,
                ttc,
                _parse_decimal(row.get("balance_due") or ttc),
                _row_date(row.get("due_date")),
                row.get("client_siret") or "",
                row.get("client_vat_number") or "",
                row.get("payment_method") or "",
                row.get("payment_terms") or "",
                status,
                _row_date(row.get("reconciliation_date")),
                row.get("reconciliation_reference") or "",
                _parse_decimal(row.get("reconciliation_amount"))
                if row.get("reconciliation_amount")
                else (ttc if status == PAID_STATUS else None),
                _relative_piece_link(path, row),
            )
        )
    total_row = (
        "TOTAL",
        "",
        None,
        "",
        "",
        total_ht.quantize(_CENT),
        total_tva.quantize(_CENT),
        total_ttc.quantize(_CENT),
        sum(
            (
                _parse_decimal(row.get("balance_due") or row.get("amount_ttc"))
                for row in rows
            ),
            Decimal("0.00"),
        ).quantize(_CENT),
        None,
        "",
        "",
        "",
        "",
        "",
        None,
        "",
        None,
        None,
    )
    _write_sales_workbook(path, table_rows, total_row=total_row)
    return CustomerSalesExportReport(
        path=path,
        row_count=len(table_rows),
        reconciled=reconciled,
        pending=pending,
        total_ht=total_ht.quantize(_CENT),
        total_tva=total_tva.quantize(_CENT),
        total_ttc=total_ttc.quantize(_CENT),
    )


class CustomerInvoiceBatchImporter:
    """Stateful convenience facade around the P20 batch import functions."""

    def __init__(self, store: Any, tenant_id: str) -> None:
        self.store = store
        self.tenant_id = validate_tenant_id(tenant_id)

    def import_batch(
        self,
        sage_source: str | Path | bytes | bytearray | None = None,
        *,
        pdf_source: str | Path | bytes | bytearray | None = None,
        filename: str | None = None,
        update_payment_rules: bool = True,
        split_export_dir: str | Path | None = None,
    ) -> CustomerInvoiceImportReport:
        return import_customer_invoice_batch(
            self.store,
            self.tenant_id,
            sage_source,
            pdf_source=pdf_source,
            filename=filename,
            update_payment_rules=update_payment_rules,
            split_export_dir=split_export_dir,
        )

    def import_invoices(
        self,
        invoices: Sequence[CustomerInvoice],
        *,
        update_payment_rules: bool = True,
    ) -> CustomerInvoiceImportReport:
        return import_customer_invoices(
            self.store,
            self.tenant_id,
            invoices,
            update_payment_rules=update_payment_rules,
        )

    def export(
        self,
        *,
        root: str | Path | None = None,
        export_dir: str | Path | None = None,
        output_path: str | Path | None = None,
    ) -> CustomerSalesExportReport:
        return export_customer_sales_workbook(
            self.tenant_id,
            store=self.store,
            root=root,
            export_dir=export_dir,
            output_path=output_path,
        )

    def reconcile(self, *args: Any, **kwargs: Any) -> Any:
        from services.customer_invoice_reconciliation import (
            reconcile_customer_invoices,
        )

        return reconcile_customer_invoices(
            self.store, self.tenant_id, *args, **kwargs
        )


def reconcile_customer_payments(*args: Any, **kwargs: Any) -> Any:
    """Lazy re-export of the P20 reconciliation entry point."""
    from services.customer_invoice_reconciliation import (
        reconcile_customer_invoices,
    )

    return reconcile_customer_invoices(*args, **kwargs)


def find_exact_invoice_combination(*args: Any, **kwargs: Any) -> Any:
    """Lazy re-export of the exact cent-level subset-sum helper."""
    from services.customer_invoice_reconciliation import (
        find_exact_invoice_combination as _find,
    )

    return _find(*args, **kwargs)


parse_sage_csv = parse_sage_customer_invoices
parse_sage_invoices = parse_sage_customer_invoices
parse_customer_invoice_pdf = parse_multi_invoice_pdf
TABLEAU_VENTES_CLIENTS_FILENAME = SALES_WORKBOOK_FILENAME


__all__ = [
    "CUSTOMER_INVOICE_DOCUMENT_TYPE",
    "CustomerBatchImportError",
    "CustomerInvoice",
    "CustomerInvoiceBatchImporter",
    "CustomerInvoiceImportReport",
    "CustomerSalesExportReport",
    "MULTI_INVOICE_PDF_FILENAME",
    "PAID_STATUS",
    "PENDING_STATUS",
    "PdfInvoiceBatch",
    "SAGE_INVOICE_FILENAME",
    "SALES_WORKBOOK_FILENAME",
    "SALES_WORKBOOK_HEADERS",
    "TABLEAU_VENTES_CLIENTS_FILENAME",
    "customer_sales_rows",
    "export_customer_sales_workbook",
    "find_exact_invoice_combination",
    "import_customer_invoice_batch",
    "import_customer_invoices",
    "merge_customer_invoices",
    "parse_customer_invoice_pdf",
    "parse_multi_invoice_pdf",
    "parse_sage_csv",
    "parse_sage_customer_invoices",
    "parse_sage_invoices",
    "reconcile_customer_payments",
]
