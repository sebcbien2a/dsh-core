"""P12.2 / P16 - Tabular exports for accounting review and category summaries.

Pure CSV functions take plain dicts (already scoped to one tenant by the
caller). Accepted keys: operation_date, label, debit_amount, credit_amount,
status, counterparty, invoice_id, debit_account, credit_account, vat_amount,
invoice_url, reason (or ``arbitration_metadata.reason``).

P16 adds :class:`CategorySummaryExporter`, which reads the non-rejected lines
of one business category from ``SqliteAccountingStore`` and writes a formatted
Excel workbook atomically.
"""

from __future__ import annotations

import csv
import os
import re
import shutil
import tempfile
import unicodedata
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from io import StringIO
from pathlib import Path
from typing import Any, Protocol

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from schemas.folder_state_schema import (
    DEFERRED_MONTHS,
    DIRIGEANT_COUNTERPART_ACCOUNT,
    NON_CLASSIFIED_MARKER,
    DocumentCategory,
    ExpenseSubCategory,
    FiscalYearMode,
    FolderState,
    PaymentSource,
    TvaDedupStatus,
    classify_marker,
    counterparty_account_for_payment_source,
    normalize_payment_source,
)
from services.folder_tree_service import ensure_tree
from services.apd_evidence_rules import NON_ACCOUNTABLE_DOCUMENT_TYPES
from services.accounting_memory_learning_service import (
    AccountingMemoryLearningService,
    normalize_counterparty_name,
)
from services.tier_reference_service import (
    TierReferenceService,
    is_client_invoice_reference,
)
from services.pdf_splitter_and_linker import (
    PIECE_LINK_HEADER,
    PURCHASES_SUBDIR,
    SALES_SUBDIR,
    find_piece_for_document,
    relative_link,
)
from services.hotel_expense_parser import (
    CGI_ARTICLE_206,
    parse_hotel_evening_stage,
)
from services.sqlite_accounting_store import (
    AccountingStoreError,
    SqliteAccountingStore,
    get_shared_sqlite_store,
    validate_tenant_id,
)

UTF8_BOM = "\ufeff"
_LEARNED_MEMORY: AccountingMemoryLearningService | None = None
_TIER_REFERENCE: TierReferenceService | None = None


def _learned_memory() -> AccountingMemoryLearningService:
    """Lazy process-wide portable accounting memory (P24)."""
    global _LEARNED_MEMORY
    if _LEARNED_MEMORY is None:
        _LEARNED_MEMORY = AccountingMemoryLearningService()
    return _LEARNED_MEMORY


def _tier_reference() -> TierReferenceService:
    """Lazy locked tier reference (P25)."""
    global _TIER_REFERENCE
    if _TIER_REFERENCE is None:
        _TIER_REFERENCE = TierReferenceService(memory=_learned_memory())
        _TIER_REFERENCE.bootstrap(write_missing=False)
    return _TIER_REFERENCE

RECONCILIATION_COLUMNS = (
    "Date",
    "Libellé",
    "Débit",
    "Crédit",
    "Statut",
    "Tiers",
    "N° Facture",
    "Compte Débit",
    "Compte Crédit",
    "TVA",
    "Lien Justificatif",
    "Motif Arbitrage",
)

CATEGORY_SUMMARY_COLUMNS_LEGACY = (
    "Date pièce",
    "Tiers / Fournisseur",
    "N° Pièce",
    "Réf externe",
    "Montant HT",
    "Montant TVA",
    "Montant TTC",
    "Statut lettrage / rapprochement",
    PIECE_LINK_HEADER,
)
CATEGORY_SUMMARY_COLUMNS_P25 = (
    "Date pièce",
    "Date de Paiement",
    "Tiers / Fournisseur",
    "N° Pièce",
    "Réf externe",
    "Montant HT",
    "Montant TVA",
    "Montant TTC",
    "Statut lettrage / rapprochement",
    PIECE_LINK_HEADER,
)
CATEGORY_SUMMARY_COLUMNS = CATEGORY_SUMMARY_COLUMNS_LEGACY
CATEGORY_SUMMARY_TOTAL_LABEL = "TOTAL"
EXPORT_DIR_ENV = "DSH_ACCOUNTING_EXPORT_DIR"
DEFAULT_EXPORT_DIR = Path("workspace") / "exports" / "accounting"

PERSONAL_ACCOUNTS = frozenset({"108000", "455000", "108", "455"})
ARBITRATED_STATUSES = frozenset(
    {
        "ARBITRATED_PERSONAL",
        "ARBITRATED_NO_INVOICE",
        "BOOKED_PERSONAL",
        "RELEASED_WITHOUT_INVOICE",
    }
)
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_UUID_TEXT = re.compile(
    r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}"
    r"-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}"
)
_PIECE_DATE = re.compile(r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}")
_PIECE_HINT = re.compile(
    r"(?<![A-Za-z0-9])(?:facture|fact\.?|pi[eè]ce|piece|paiement|avoir|"
    r"n[°o]|num(?:[eé]ro)?|r[eé]f(?:[eé]rence)?)"
    r"\s*(?:n[°o]\s*)?[:#-]?\s*([A-Za-z0-9][A-Za-z0-9/._-]{0,31})",
    re.IGNORECASE,
)
_PIECE_TOKEN = re.compile(
    r"(?<![A-Za-z0-9/._-])"
    r"([A-Za-z0-9][A-Za-z0-9/._-]*\d[A-Za-z0-9/._-]*)"
    r"(?![A-Za-z0-9/._-])"
)
_PIECE_METADATA_KEYS = (
    "piece_number",
    "numero_piece",
    "num_piece",
    "piece_ref",
    "supplier_piece_number",
    "document_number",
    "invoice_number",
    "numero_facture",
)
_PIECE_METADATA_CONTAINERS = (
    "metadata",
    "arbitration_metadata",
    "extracted_data",
    "document_metadata",
)
_PIECE_UUID_FALLBACK_LENGTH = 8
_PIECE_NUMBER_MAX_LENGTH = 40
_CENT = Decimal("0.01")
_HEADER_FILL = "1F4E78"
_BAND_FILL = "F2F7FB"
_TOTAL_FILL = "DDEBF7"
_BORDER_COLOR = "B7C4D4"
_MIN_WIDTHS = (12, 24, 18, 32, 14, 14, 14, 32, 32)
_MIN_WIDTHS_P25 = (12, 16, 24, 18, 32, 14, 14, 14, 32, 32)


class CategorySummaryExportError(ValueError):
    """Invalid summarisation request; ``code`` is stable for tests and logs."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


class CategoryLineStore(Protocol):
    def list_category_lines(
        self, tenant_id: str, category: DocumentCategory
    ) -> list[dict[str, Any]]: ...



def _text(value: Any) -> str:
    """Render a text cell, neutralising spreadsheet formula injection."""
    if value is None:
        return ""
    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _amount(value: Any, decimal_separator: str) -> str:
    if value is None or str(value).strip() == "":
        return ""
    try:
        number = Decimal(str(value).strip().replace(",", "."))
    except InvalidOperation:
        return _text(value)
    if not number.is_finite():
        return _text(value)
    return format(number, "f").replace(".", decimal_separator)


def _reason(entry: dict[str, Any]) -> Any:
    reason = entry.get("reason")
    if reason:
        return reason
    metadata = entry.get("arbitration_metadata")
    if isinstance(metadata, dict):
        return metadata.get("reason")
    return getattr(metadata, "reason", None)


def _row(entry: dict[str, Any], decimal_separator: str) -> list[str]:
    return [
        _text(entry.get("operation_date")),
        _text(entry.get("label")),
        _amount(entry.get("debit_amount"), decimal_separator),
        _amount(entry.get("credit_amount"), decimal_separator),
        _text(entry.get("status")),
        _text(entry.get("counterparty")),
        _text(entry.get("invoice_id")),
        _text(entry.get("debit_account")),
        _text(entry.get("credit_account")),
        _amount(entry.get("vat_amount"), decimal_separator),
        _text(entry.get("invoice_url")),
        _text(_reason(entry)),
    ]


def _render(
    entries: list[dict[str, Any]], delimiter: str, decimal_separator: str, bom: bool
) -> str:
    if len(delimiter) != 1 or delimiter in '"\r\n':
        raise ValueError("delimiter must be a single non-quote character")
    if decimal_separator not in (",", "."):
        raise ValueError("decimal_separator must be ',' or '.'")
    buffer = StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\r\n")
    writer.writerow(RECONCILIATION_COLUMNS)
    for entry in entries:
        writer.writerow(_row(entry, decimal_separator))
    return (UTF8_BOM if bom else "") + buffer.getvalue()


def generate_reconciliation_csv(
    entries: list[dict[str, Any]],
    delimiter: str = ";",
    *,
    decimal_separator: str = ",",
    bom: bool = False,
) -> str:
    """Full reconciliation review sheet (header + one row per entry)."""
    return _render(entries, delimiter, decimal_separator, bom)


def is_arbitration(entry: dict[str, Any]) -> bool:
    """Personal expenses (108/455) and validations without supporting document."""
    status = str(entry.get("status") or "")
    if status in ARBITRATED_STATUSES:
        return True
    accounts = {
        str(entry.get("debit_account") or ""),
        str(entry.get("credit_account") or ""),
    }
    return bool(accounts & PERSONAL_ACCOUNTS)


def generate_arbitrations_summary_csv(
    entries: list[dict[str, Any]],
    delimiter: str = ";",
    *,
    decimal_separator: str = ",",
    bom: bool = False,
) -> str:
    """Same layout as the reconciliation sheet, limited to arbitrated lines."""
    selected = [e for e in entries if is_arbitration(e)]
    return _render(selected, delimiter, decimal_separator, bom)


def _as_category(value: DocumentCategory | str) -> DocumentCategory:
    try:
        return DocumentCategory(value)
    except (TypeError, ValueError):
        raise CategorySummaryExportError("INVALID_CATEGORY", repr(value)) from None


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).replace("\u00a0", "").replace(" ", "").strip()
    if not text:
        return None
    try:
        number = Decimal(text.replace(",", "."))
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return number.quantize(_CENT)


def _clean_piece_number(value: Any) -> str:
    text = str(value or "").strip().strip(" \t\r\n,;:()[]{}\"'")
    if not text:
        return ""
    text = re.sub(
        r"\.(?:pdf|jpe?g|png|tiff?|xml|xlsx?|csv)$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    text = text.strip(" \t\r\n,;:()[]{}\"'")
    if not text or len(text) > _PIECE_NUMBER_MAX_LENGTH:
        return ""
    if not any(character.isdigit() for character in text):
        return ""
    if _UUID_TEXT.fullmatch(text) or _PIECE_DATE.fullmatch(text):
        return ""
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._/-]*", text) is None:
        return ""
    return text


def _piece_number_from_text(value: Any) -> str:
    """Extract a supplier piece number from a label or from metadata."""
    text = str(value or "").strip()
    if not text:
        return ""
    candidate = _clean_piece_number(text)
    if candidate and not any(character.isspace() for character in candidate):
        return candidate
    stem = re.sub(
        r"\.(?:pdf|jpe?g|png|tiff?|xml|xlsx?|csv)$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    for source in (text, stem):
        for match in _PIECE_HINT.finditer(source):
            candidate = _clean_piece_number(match.group(1))
            if candidate:
                return candidate
        for match in _PIECE_TOKEN.finditer(source):
            candidate = _clean_piece_number(match.group(1))
            if candidate:
                return candidate
    return ""


def _piece_number_from_row(row: dict[str, Any]) -> str:
    containers = [row]
    containers.extend(row.get(key) for key in _PIECE_METADATA_CONTAINERS)
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in _PIECE_METADATA_KEYS:
            number = _piece_number_from_text(container.get(key))
            if number:
                return number
    return _piece_number_from_text(row.get("label"))


def _piece_reference(row: dict[str, Any], reference: Any) -> str:
    """Prefer the real piece number, falling back to a short UUID only."""
    text = "" if reference is None else str(reference).strip()
    if _UUID_TEXT.fullmatch(text) is None:
        return text
    number = _piece_number_from_row(row)
    if number:
        return number
    return text.replace("-", "")[:_PIECE_UUID_FALLBACK_LENGTH]


def _summary_record(row: dict[str, Any]) -> dict[str, Any]:
    """Normalise one store row into the eight summary columns.

    Explicit ``amount_*`` fields win. Legacy/bank lines fall back to the
    arbitration VAT and to the bank movement (``debit - credit``) as TTC, then
    the missing HT/TVA/TTC value is deduced when the other two are present.
    """
    debit = _decimal(row.get("debit_amount"))
    credit = _decimal(row.get("credit_amount"))
    ht = _decimal(row.get("amount_ht"))
    tva = _decimal(row.get("amount_tva"))
    ttc = _decimal(row.get("amount_ttc"))
    if tva is None:
        tva = _decimal(row.get("vat_amount"))
    if ttc is None and (debit is not None or credit is not None):
        movement = abs((debit or Decimal("0")) - (credit or Decimal("0")))
        if movement:
            ttc = movement.quantize(_CENT)
    if ht is None and ttc is not None and tva is not None:
        ht = (ttc - tva).quantize(_CENT)
    if tva is None and ht is not None and ttc is not None:
        tva = (ttc - ht).quantize(_CENT)
    if ttc is None and ht is not None and tva is not None:
        ttc = (ht + tva).quantize(_CENT)
    return {
        "date": row.get("operation_date"),
        "counterparty": row.get("counterparty") or row.get("label") or "",
        "invoice_id": _piece_reference(
            row, row.get("invoice_id") or row.get("line_id") or ""
        ),
        "reference": row.get("invoice_url") or "",
        "ht": ht,
        "tva": tva,
        "ttc": ttc,
        "status": row.get("raw_status") or row.get("status") or "",
    }


def _date_cell(value: Any) -> Any:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) >= 10:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    return _text(text)


def _resolve_export_dir(
    export_dir: str | os.PathLike[str] | None,
) -> Path:
    if export_dir is not None:
        raw = os.fspath(export_dir)
    else:
        raw = os.environ.get(EXPORT_DIR_ENV, "").strip() or os.fspath(
            DEFAULT_EXPORT_DIR
        )
    if not raw or "\x00" in raw:
        raise CategorySummaryExportError("INVALID_EXPORT_DIR", repr(raw))
    return Path(raw)


def _validate_output_path(path: Path) -> None:
    raw = os.fspath(path)
    if not raw or "\x00" in raw:
        raise CategorySummaryExportError("INVALID_OUTPUT_PATH", repr(raw))
    if path.exists() and path.is_dir():
        raise CategorySummaryExportError("OUTPUT_IS_DIRECTORY", str(path))


def _atomic_save_workbook(workbook: Workbook, output_path: Path) -> None:
    """Save a workbook without letting Windows rename locks kill the export.

    The workbook is first written to a sibling temporary file. ``os.replace``
    stays the fast path; when OneDrive/Excel refuses the rename the bytes are
    copied over the target with :func:`shutil.copy2` before the temporary file
    is always removed. This keeps the ``04_EXPORT_COMPTA`` folder free of stale
    ``.tmp`` files and prevents empty master tables after a failed rename.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.stem}_", suffix=".tmp", dir=output_path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        workbook.save(temporary)
        try:
            os.replace(temporary, output_path)
        except OSError:
            shutil.copy2(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)


def _display_length(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, date_cls):
        return 10
    if isinstance(value, (int, float)):
        return len(f"{value:,.2f}")
    return len(str(value))


def _piece_side_for_row(row: dict[str, Any]) -> str:
    category = str(row.get("category") or "").upper()
    return SALES_SUBDIR if category == DocumentCategory.VENTES_CLIENTS.value else PURCHASES_SUBDIR


def _piece_link_for_row(
    workbook_path: Path, row: dict[str, Any]
) -> str | None:
    stored = str(row.get("piece_pdf") or "").strip()
    if stored:
        normalized = stored.replace("\\", "/")
        if normalized.startswith("PIECES_JOINTES/"):
            candidate = workbook_path.parent / normalized
            if candidate.is_file():
                return normalized
        candidate = Path(stored)
        if not candidate.is_absolute():
            candidate = workbook_path.parent / candidate
        if candidate.is_file():
            return relative_link(candidate, workbook_path)
    number = str(
        row.get("invoice_id")
        or row.get("piece_number")
        or row.get("reference")
        or row.get("line_id")
        or ""
    )
    found = find_piece_for_document(
        workbook_path.parent, number, side=_piece_side_for_row(row)
    )
    if found is None:
        return None
    return relative_link(
        Path(found).resolve(), Path(workbook_path).resolve()
    )


def _write_piece_link(
    cell: Any, value: Any, display_text: Any = None
) -> bool:
    """Write a local ``HYPERLINK`` formula so Excel opens the PDF in Foxit.

    A plain ``cell.hyperlink`` is rewritten by OneDrive/Excel into a
    ``https://onedrive.live.com/...`` URL that opens in the browser. The
    ``CELL("filename")`` formula keeps the link relative to the workbook
    folder, so the default local PDF reader opens the file.
    """
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = value.replace("\\", "/")
    if not normalized.startswith("PIECES_JOINTES/"):
        return False
    windows_path = normalized.replace("/", "\\")
    display = str(display_text or Path(normalized).name).replace('"', "'")
    formula = (
        '=IFERROR(HYPERLINK(LEFT(CELL("filename",A1),'
        'FIND("[",CELL("filename",A1))-1)'
        f'&"{windows_path}","{display}"),"{windows_path}")'
    )
    cell.value = formula
    cell.font = Font(color="0563C1", underline="single")
    return True


def _payment_date_cell(row: dict[str, Any]) -> Any:
    status = str(row.get("reconciliation_status") or "").upper()
    paid = status in {"PAYEE_RAPPROCHEE", "PAYE", "SOLDEE"} or str(
        row.get("already_paid_on_invoice") or ""
    ).strip() in {"1", "true", "oui"}
    if not paid:
        return "EN ATTENTE"
    raw = (
        row.get("reconciliation_date")
        or row.get("payment_date")
        or row.get("operation_date")
    )
    return _date_cell(raw) if str(raw or "").strip() else "EN ATTENTE"


def _write_category_workbook(
    category: DocumentCategory,
    records: list[dict[str, Any]],
    output_path: Path,
    *,
    include_payment_date: bool = False,
) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = f"SYNTHESE_{category.value}"[:31]
    sheet.sheet_view.showGridLines = False

    thin = Side(style="thin", color=_BORDER_COLOR)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill("solid", fgColor=_HEADER_FILL)
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    band_fill = PatternFill("solid", fgColor=_BAND_FILL)

    columns = (
        CATEGORY_SUMMARY_COLUMNS_P25
        if include_payment_date
        else CATEGORY_SUMMARY_COLUMNS
    )
    for column, name in enumerate(columns, start=1):
        cell = sheet.cell(row=1, column=column, value=name)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = border
        cell.alignment = header_alignment
    sheet.row_dimensions[1].height = 32

    for offset, record in enumerate(records, start=2):
        values = (
            _date_cell(record["date"]),
            *(
                (_payment_date_cell(record),)
                if include_payment_date
                else ()
            ),
            _text(record["counterparty"]),
            _text(record["invoice_id"]),
            _text(record["reference"]),
            record["ht"],
            record["tva"],
            record["ttc"],
            _text(record["status"]),
            record.get("piece_link"),
        )
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=offset, column=column)
            if _write_piece_link(cell, value):
                cell.border = border
                continue
            if isinstance(value, Decimal):
                cell.value = float(value)
            else:
                cell.value = value
            cell.border = border
            if offset % 2 == 0:
                cell.fill = band_fill
            if column in (5, 6, 7):
                cell.number_format = "#,##0.00"
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif column == 1:
                cell.alignment = Alignment(horizontal="center", vertical="center")
                if isinstance(value, date_cls):
                    cell.number_format = "DD/MM/YYYY"
            else:
                cell.alignment = Alignment(vertical="center", wrap_text=column in (2, 4, 8))

    last_data_row = len(records) + 1
    sheet.auto_filter.ref = f"A1:H{last_data_row}"
    sheet.freeze_panes = "A2"

    total_row = last_data_row + 1
    total_font = Font(bold=True)
    total_fill = PatternFill("solid", fgColor=_TOTAL_FILL)
    double_top = Side(style="double", color=_HEADER_FILL)
    total_border = Border(left=thin, right=thin, top=double_top, bottom=thin)
    sheet.cell(row=total_row, column=1, value=CATEGORY_SUMMARY_TOTAL_LABEL)
    for column in range(1, len(CATEGORY_SUMMARY_COLUMNS) + 1):
        cell = sheet.cell(row=total_row, column=column)
        cell.font = total_font
        cell.fill = total_fill
        cell.border = total_border
        if column in ((6, 7, 8) if include_payment_date else (5, 6, 7)):
            letter = get_column_letter(column)
            cell.value = (
                f"=SUM({letter}2:{letter}{last_data_row})" if records else 0
            )
            cell.number_format = "#,##0.00"
            cell.alignment = Alignment(horizontal="right", vertical="center")
        elif column == 1:
            cell.alignment = Alignment(horizontal="left", vertical="center")

    widths_scheme = (
        _MIN_WIDTHS if not include_payment_date else _MIN_WIDTHS_P25
    )
    for column in range(1, len(columns) + 1):
        longest = len(columns[column - 1])
        for row in range(2, last_data_row + 1):
            longest = max(longest, _display_length(sheet.cell(row=row, column=column).value))
        width = max(widths_scheme[column - 1], min(longest + 2, 60))
        sheet.column_dimensions[get_column_letter(column)].width = width

    workbook.properties.creator = "DSH-Core"
    workbook.properties.title = f"Synthèse {category.value}"
    _atomic_save_workbook(workbook, output_path)


class CategorySummaryExporter:
    """Build the per-category analytic workbook from a durable line store."""

    def __init__(
        self,
        store: CategoryLineStore,
        *,
        export_dir: str | os.PathLike[str] | None = None,
        include_payment_date: bool = False,
    ) -> None:
        self._store = store
        self._export_dir = _resolve_export_dir(export_dir)
        self._include_payment_date = bool(include_payment_date)

    @property
    def export_dir(self) -> Path:
        return self._export_dir

    def export_category_summary(
        self,
        tenant_id: str,
        category: DocumentCategory | str,
        output_path: str | os.PathLike[str] | None = None,
    ) -> Path:
        """Write the non-rejected lines of ``category`` to an ``.xlsx`` file.

        ``REJETE`` documents never appear. Without ``output_path`` the file is
        named ``SYNTHESE_<TENANT>_<CATEGORY>.xlsx`` under
        ``DSH_ACCOUNTING_EXPORT_DIR`` (default ``workspace/exports/accounting``).
        """
        try:
            tenant = validate_tenant_id(tenant_id)
        except AccountingStoreError:
            raise CategorySummaryExportError(
                "INVALID_TENANT_ID", repr(tenant_id)
            ) from None
        selected = _as_category(category)
        rows = self._store.list_category_lines(tenant, selected)
        target = (
            Path(output_path)
            if output_path is not None
            else self._export_dir
            / tenant
            / f"SYNTHESE_{tenant}_{selected.value}.xlsx"
        )
        records = [_summary_record(row) for row in rows]
        for record, row in zip(records, rows):
            record["piece_link"] = _piece_link_for_row(target, row)
        _validate_output_path(target)
        _write_category_workbook(
            selected,
            records,
            target,
            include_payment_date=self._include_payment_date,
        )
        return target


def export_category_summary(
    tenant_id: str,
    category: DocumentCategory | str,
    output_path: str | os.PathLike[str] | None = None,
    *,
    store: SqliteAccountingStore | None = None,
    export_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Convenience wrapper around :class:`CategorySummaryExporter`."""
    exporter = CategorySummaryExporter(
        store if store is not None else get_shared_sqlite_store(),
        export_dir=export_dir,
    )
    return exporter.export_category_summary(tenant_id, category, output_path)


CATEGORY_OVERVIEW_FILENAME = "SYNTHESE_CATEGORIES.xlsx"
CATEGORY_OVERVIEW_HEADERS: tuple[str, ...] = (
    "Date pièce",
    "Catégorie",
    "Tiers / Fournisseur",
    "N° Pièce",
    "Réf externe",
    "Montant HT",
    "Montant TVA",
    "Montant TTC",
    PIECE_LINK_HEADER,
)


class CategoryOverviewExporter:
    """Build the global ``SYNTHESE_CATEGORIES.xlsx`` workbook."""

    def __init__(
        self,
        store: Any,
        *,
        root: str | os.PathLike[str] | None = None,
        export_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self._store = store
        self._root = root
        self._export_dir = Path(export_dir) if export_dir is not None else None

    def export(self, tenant_id: str) -> Path:
        tenant = validate_tenant_id(tenant_id)
        tree = ensure_tree(tenant, self._root)
        output_dir = (
            self._export_dir / tenant
            if self._export_dir is not None
            else tree.state_dir(FolderState.EXPORT_COMPTA) / tenant
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / CATEGORY_OVERVIEW_FILENAME
        rows = sorted(
            (_master_rows(self._store, tenant)),
            key=lambda row: (
                str(row.get("operation_date") or ""),
                str(row.get("line_id") or ""),
            ),
        )
        values: list[tuple[Any, ...]] = []
        for row in rows:
            record = _summary_record(row)
            values.append(
                (
                    _date_cell(record["date"]),
                    str(row.get("category") or ""),
                    record["counterparty"],
                    _piece_number_from_row(row)
                    or row.get("invoice_id")
                    or row.get("line_id")
                    or "",
                    record["reference"],
                    record["ht"] or Decimal("0.00"),
                    record["tva"] or Decimal("0.00"),
                    record["ttc"] or Decimal("0.00"),
                    _piece_link_for_row(target, row),
                )
            )
        _write_p17_workbook(
            target,
            title="SYNTHESE_CATEGORIES",
            headers=CATEGORY_OVERVIEW_HEADERS,
            rows=values,
        )
        return target


def export_category_overview(
    tenant_id: str,
    *,
    root: str | os.PathLike[str] | None = None,
    export_dir: str | os.PathLike[str] | None = None,
    store: SqliteAccountingStore | None = None,
) -> Path:
    """Convenience wrapper around :class:`CategoryOverviewExporter`."""
    return CategoryOverviewExporter(
        store if store is not None else get_shared_sqlite_store(),
        root=root,
        export_dir=export_dir,
    ).export(tenant_id)


SUPPLIER_LEDGER_COLUMNS_LEGACY = (
    "Date",
    "Type",
    "N° Pièce / Réf",
    "Libellé",
    "Débit",
    "Crédit",
    "Solde Cumulé",
    "Code Lettrage / Statut",
    PIECE_LINK_HEADER,
)
SUPPLIER_LEDGER_COLUMNS_P25 = (
    "Date",
    "Type",
    "N° Pièce / Réf",
    "Libellé",
    "Débit",
    "Crédit",
    "Solde Cumulé",
    "Code Lettrage / Statut",
    "Pièce(s) / Règlement(s) Rapproché(s) en face",
    PIECE_LINK_HEADER,
)
SUPPLIER_LEDGER_COLUMNS = SUPPLIER_LEDGER_COLUMNS_LEGACY
SUPPLIER_LEDGER_DIAGNOSTIC_COLUMNS = (
    "Paiement",
    "Date",
    "Libellé",
    "Montant",
    "Statut",
    "Combinaison",
    "Écart résiduel",
    "Message",
)
SUPPLIER_LEDGER_TOTAL_LABEL = "TOTAL"
SUPPLIER_LEDGER_DIAGNOSTIC_TITLE = "DIAGNOSTIC RAPPROCHEMENT"
SUPPLIER_LEDGER_EMPTY_SHEET = "AUCUN_MOUVEMENT"
SUPPLIER_LEDGER_TYPE_FACTURE = "FACTURE"
SUPPLIER_LEDGER_TYPE_AVOIR = "AVOIR"
SUPPLIER_LEDGER_TYPE_PAIEMENT = "PAIEMENT"
SUPPLIER_LEDGER_TYPE_AVOIR_REMBOURSEMENT = "AVOIR_REMBOURSEMENT"
SUPPLIER_LEDGER_MAX_COMBINATION_ITEMS = 30
SUPPLIER_LEDGER_EQUILIBRIUM_FILL = "C6EFCE"
SUPPLIER_LEDGER_DIAGNOSTIC_FILL = "FCE4D6"
SUPPLIER_LEDGER_MIN_WIDTHS = (12, 12, 24, 40, 14, 14, 16, 24, 32)
SUPPLIER_LEDGER_MIN_WIDTHS_P25 = (12, 12, 24, 40, 14, 14, 16, 24, 30, 32)


class SupplierLedgerExportError(ValueError):
    """Invalid supplier ledger request; ``code`` is stable for tests and logs."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


class SupplierLedgerStore(Protocol):
    def list_supplier_ledger(
        self, tenant_id: str, supplier: str | None = None
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class SupplierLedgerEntry:
    """One unified supplier ledger line (invoice, credit note or payment)."""

    entry_type: str
    operation_date: str | None
    reference: str
    label: str
    debit: Decimal
    credit: Decimal
    status: str
    raw_status: str
    line_id: str
    invoice_id: str | None
    supplier: str
    matched: bool
    matchable: bool
    matched_pieces: str = ""
    payment_date: str = ""
    payment_method: str = ""
    piece_pdf: str = ""

    @property
    def signed_amount(self) -> Decimal:
        if self.entry_type == SUPPLIER_LEDGER_TYPE_FACTURE:
            return self.credit
        if self.entry_type == SUPPLIER_LEDGER_TYPE_AVOIR:
            return -self.debit
        return Decimal("0.00")

    @property
    def balance_delta(self) -> Decimal:
        return self.credit - self.debit


@dataclass(frozen=True)
class SupplierLedgerDiagnostic:
    """One reconciliation diagnostic attached to an unresolved payment."""

    payment_id: str
    operation_date: str | None
    label: str
    amount: Decimal
    status: str
    combination: tuple[str, ...]
    residual_delta: Decimal
    message: str


@dataclass(frozen=True)
class _Combination:
    exact: bool
    items: tuple[SupplierLedgerEntry, ...]
    residual: Decimal
    truncated: bool


def _to_cents(value: Decimal) -> int:
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _from_cents(value: int) -> Decimal:
    return (Decimal(value) / 100).quantize(_CENT)


def _ledger_amount_ttc(row: dict[str, Any]) -> Decimal:
    ttc = _decimal(row.get("amount_ttc"))
    if ttc is not None:
        return ttc
    ht = _decimal(row.get("amount_ht"))
    tva = _decimal(row.get("amount_tva"))
    if ht is not None and tva is not None:
        return (ht + tva).quantize(_CENT)
    if ht is not None:
        return ht
    if tva is not None:
        return tva
    return Decimal("0.00")


def _is_bank_ledger_row(row: dict[str, Any]) -> bool:
    if str(row.get("decision_action") or "").strip():
        return True
    debit = _decimal(row.get("debit_amount"))
    credit = _decimal(row.get("credit_amount"))
    if (debit is not None and debit != 0) or (credit is not None and credit != 0):
        return True
    document_type = str(row.get("document_type") or "").strip().upper()
    if document_type in {
        "INVOICE",
        "FACTURE",
        "CREDIT_NOTE",
        "AVOIR",
        "RECEIPT",
        "FUEL_RECEIPT",
        "MEAL_RECEIPT",
    }:
        return False
    reference = str(row.get("invoice_url") or "").replace("\\", "/")
    if reference.startswith(("03_VALIDE_PRET_FEC/", "04_EXPORT_COMPTA/")):
        return False
    invoice_id = str(
        row.get("document_invoice_id") or row.get("invoice_id") or ""
    )
    line_id = str(row.get("line_id") or "")
    if invoice_id and invoice_id == line_id:
        return False
    if _decimal(row.get("amount_ttc")) is not None:
        return False
    if _decimal(row.get("amount_ht")) is not None:
        return False
    return True


def _document_ledger_type(row: dict[str, Any]) -> str:
    raw = str(row.get("document_type") or "").strip().upper()
    ttc = _ledger_amount_ttc(row)
    if raw in {"CREDIT_NOTE", "AVOIR", "CREDIT"}:
        return SUPPLIER_LEDGER_TYPE_AVOIR
    if raw in {
        "INVOICE",
        "FACTURE",
        "RECEIPT",
        "FUEL_RECEIPT",
        "MEAL_RECEIPT",
    }:
        return (
            SUPPLIER_LEDGER_TYPE_AVOIR
            if ttc < 0
            else SUPPLIER_LEDGER_TYPE_FACTURE
        )
    return SUPPLIER_LEDGER_TYPE_AVOIR if ttc < 0 else SUPPLIER_LEDGER_TYPE_FACTURE


def _ledger_status(is_bank: bool, raw_status: str, matched: bool) -> str:
    if not is_bank:
        return "LETTRE" if matched else "NON_LETTRE"
    if raw_status == "PENDING":
        return "A_RAPPROCHER"
    if raw_status == "INVOICE_ATTACHED":
        return "RAPPROCHE"
    if raw_status == "BOOKED_PERSONAL":
        return "HORS_PERIMETRE"
    if raw_status == "RELEASED_WITHOUT_INVOICE":
        return "RAPPROCHE_SANS_FACTURE"
    return raw_status or "A_RAPPROCHER"


APD_SELF_SIREN = "810908392"
APD_SELF_NAME = "AUTRE S.A.S."
_APD_SELF_TEXT_FIELDS = (
    "counterparty",
    "supplier",
    "label",
    "counterparty_name",
    "company_name",
    "siren",
    "supplier_siren",
    "counterparty_siret",
    "client_siret",
)


def _digits_only(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def is_apd_self_row(row: dict[str, Any]) -> bool:
    """Return True when the row is APD itself (SIREN 810908392 / Autre S.A.S.).

    ``client_vat_number`` is deliberately ignored: the sales workbook stores
    APD's own VAT number in that column for every customer invoice.
    """
    text = " ".join(str(row.get(key) or "") for key in _APD_SELF_TEXT_FIELDS).casefold()
    if (
        "autre s.a.s" in text
        or "autre sas" in text
        or "assistance peintures diffusion" in text
        or "assistance peintures diffusions" in text
    ):
        return True
    digits = " ".join(_digits_only(row.get(key)) for key in _APD_SELF_TEXT_FIELDS)
    return APD_SELF_SIREN in digits


def _ledger_supplier(row: dict[str, Any]) -> str:
    value = str(row.get("supplier") or row.get("counterparty") or "").strip()
    return value or "NON_AFFECTE"


def _build_supplier_ledger_entries(
    rows: list[dict[str, Any]],
    *,
    strict_tiers: bool = False,
    refund_as_credit: bool = False,
) -> list[SupplierLedgerEntry]:
    matched_invoice_ids = {
        str(row.get("decision_invoice_id"))
        for row in rows
        if row.get("decision_invoice_id")
    }
    invoice_rows: dict[str, dict[str, Any]] = {}
    for row in rows:
        if _is_bank_ledger_row(row):
            continue
        for key in ("invoice_id", "line_id", "document_invoice_id"):
            value = str(row.get(key) or "").strip()
            if value:
                invoice_rows.setdefault(value, row)
    entries: list[SupplierLedgerEntry] = []
    for row in rows:
        if is_apd_self_row(row):
            continue
        if (
            str(row.get("document_type") or "").strip().upper()
            in NON_ACCOUNTABLE_DOCUMENT_TYPES
        ):
            continue
        raw_status = str(row.get("raw_status") or row.get("status") or "PENDING")
        action = str(row.get("decision_action") or "")
        supplier_name = _ledger_supplier(row)
        tiers = _tier_reference()
        if tiers.is_internal(supplier_name):
            continue
        role = tiers.role_for(supplier_name)
        if strict_tiers and role not in {"FOURNISSEUR", "PRESTATAIRE"}:
            continue
        if role == "CLIENT":
            continue
        if _is_bank_ledger_row(row):
            if action == "PERSONAL_EXPENSE" or raw_status == "BOOKED_PERSONAL":
                continue
            debit = _decimal(row.get("debit_amount")) or Decimal("0.00")
            credit = _decimal(row.get("credit_amount")) or Decimal("0.00")
            if debit == 0 and credit == 0:
                continue
            matched = bool(row.get("decision_invoice_id")) or raw_status != "PENDING"
            is_refund = refund_as_credit and credit > 0 and debit == 0
            entry_type = (
                SUPPLIER_LEDGER_TYPE_AVOIR_REMBOURSEMENT
                if is_refund
                else SUPPLIER_LEDGER_TYPE_PAIEMENT
            )
            matched_id = str(
                row.get("decision_invoice_id")
                or row.get("invoice_id")
                or ""
            ).strip()
            matched_row = invoice_rows.get(matched_id)
            piece_number = ""
            piece_pdf = ""
            if matched_row is not None:
                piece_number = str(
                    _piece_number_from_row(matched_row)
                    or matched_row.get("invoice_id")
                    or matched_row.get("line_id")
                    or ""
                ).strip()
                piece_pdf = str(matched_row.get("piece_pdf") or "").strip()
            entries.append(
                SupplierLedgerEntry(
                    entry_type=entry_type,
                    operation_date=row.get("operation_date"),
                    reference=_piece_reference(
                        row, str(row.get("line_id") or "")
                    ),
                    label=str(row.get("label") or ""),
                    debit=credit if is_refund else debit,
                    credit=Decimal("0.00") if is_refund else credit,
                    status=_ledger_status(True, raw_status, matched),
                    raw_status=raw_status,
                    line_id=str(row.get("line_id") or ""),
                    invoice_id=piece_number or matched_id or None,
                    supplier=supplier_name,
                    matched=matched,
                    matchable=False,
                    matched_pieces=piece_number or matched_id,
                    payment_date=str(row.get("reconciliation_date") or ""),
                    payment_method=_payment_method_label(
                        row.get("reconciliation_reference")
                        or row.get("payment_method")
                        or row.get("label")
                    ),
                    piece_pdf=piece_pdf,
                )
            )
            continue
        if raw_status != "VALIDE":
            continue
        ttc = _ledger_amount_ttc(row)
        entry_type = _document_ledger_type(row)
        personal = is_personal_expense(row)
        invoice_id = str(
            row.get("document_invoice_id")
            or row.get("invoice_id")
            or row.get("line_id")
            or ""
        )
        matched = invoice_id in matched_invoice_ids
        if entry_type == SUPPLIER_LEDGER_TYPE_AVOIR:
            debit = abs(ttc).quantize(_CENT)
            credit = Decimal("0.00")
        else:
            debit = Decimal("0.00")
            credit = abs(ttc).quantize(_CENT)
        entries.append(
            SupplierLedgerEntry(
                entry_type=entry_type,
                operation_date=row.get("operation_date"),
                reference=_piece_reference(
                    row, invoice_id or str(row.get("line_id") or "")
                ),
                label=str(row.get("label") or ""),
                debit=debit,
                credit=credit,
                status=(
                    "HORS_PERIMETRE"
                    if personal
                    else _ledger_status(False, raw_status, matched)
                ),
                raw_status=raw_status,
                line_id=str(row.get("line_id") or ""),
                invoice_id=invoice_id or None,
                supplier=supplier_name,
                matched=matched,
                matchable=(
                    not personal
                    and not matched
                    and (debit != 0 or credit != 0)
                ),
                matched_pieces=str(row.get("reconciliation_reference") or ""),
                payment_date=str(row.get("reconciliation_date") or ""),
                payment_method=_payment_method_label(
                    row.get("reconciliation_reference")
                    or row.get("payment_method")
                    or ""
                ),
            )
        )
    return entries


def _group_supplier_entries(
    entries: list[SupplierLedgerEntry],
    *,
    strict_tiers: bool = False,
) -> dict[str, list[SupplierLedgerEntry]]:
    groups: dict[str, list[SupplierLedgerEntry]] = {}
    display: dict[str, str] = {}
    memory = _learned_memory()
    tiers = _tier_reference()
    for entry in entries:
        name = entry.supplier.strip() or "NON_AFFECTE"
        if tiers.is_internal(name):
            continue
        canonical = (
            tiers.canonical_name(name)
            if strict_tiers and tiers.resolve(name) is not None
            else name
        ) or name
        key = " ".join(canonical.casefold().split())
        if key not in groups:
            groups[key] = []
            display[key] = canonical
        groups[key].append(entry)
    ordered: dict[str, list[SupplierLedgerEntry]] = {}
    for key, items in groups.items():
        items.sort(key=lambda item: (item.operation_date or "", item.line_id))
        ordered[display[key]] = items
    return ordered


def _enumerate_sums(amounts: list[int]) -> dict[int, int]:
    sums: dict[int, int] = {0: 0}
    for index, amount in enumerate(amounts):
        bit = 1 << index
        additions: dict[int, int] = {}
        for total, mask in sums.items():
            new_total = total + amount
            if new_total not in sums and new_total not in additions:
                additions[new_total] = mask | bit
        sums.update(additions)
    return sums


def _mask_entries(
    mask: int, entries: list[SupplierLedgerEntry]
) -> list[SupplierLedgerEntry]:
    return [
        entry
        for index, entry in enumerate(entries)
        if mask & (1 << index)
    ]


def _find_combination(
    entries: list[SupplierLedgerEntry], target: Decimal
) -> _Combination:
    if not entries:
        return _Combination(False, (), target, False)
    ordered = sorted(
        entries, key=lambda item: (item.operation_date or "", item.line_id)
    )
    truncated = len(ordered) > SUPPLIER_LEDGER_MAX_COMBINATION_ITEMS
    selected = ordered[:SUPPLIER_LEDGER_MAX_COMBINATION_ITEMS]
    amounts = [_to_cents(entry.signed_amount) for entry in selected]
    half = len(selected) // 2
    left_entries = selected[:half]
    right_entries = selected[half:]
    left = _enumerate_sums(amounts[:half])
    right = _enumerate_sums(amounts[half:])
    target_cents = _to_cents(target)

    for left_sum, left_mask in left.items():
        right_mask = right.get(target_cents - left_sum)
        if right_mask is None:
            continue
        combination = _mask_entries(left_mask, left_entries) + _mask_entries(
            right_mask, right_entries
        )
        if combination:
            return _Combination(True, tuple(combination), Decimal("0.00"), truncated)

    right_sums = sorted(right)
    best_key: tuple[int, int, int] | None = None
    best_total = 0
    best_combination: tuple[SupplierLedgerEntry, ...] = ()
    for left_sum, left_mask in left.items():
        wanted = target_cents - left_sum
        position = bisect_left(right_sums, wanted)
        for candidate_index in (position - 1, position):
            if candidate_index < 0 or candidate_index >= len(right_sums):
                continue
            right_sum = right_sums[candidate_index]
            combination = _mask_entries(left_mask, left_entries) + _mask_entries(
                right[right_sum], right_entries
            )
            if not combination:
                continue
            total = left_sum + right_sum
            key = (abs(target_cents - total), len(combination), total)
            if best_key is None or key < best_key:
                best_key = key
                best_total = total
                best_combination = tuple(combination)
    if best_key is None:
        return _Combination(False, (), target, truncated)
    return _Combination(
        False,
        best_combination,
        _from_cents(target_cents - best_total),
        truncated,
    )


def _build_supplier_diagnostics(
    entries: list[SupplierLedgerEntry],
) -> list[SupplierLedgerDiagnostic]:
    open_items = [
        entry
        for entry in entries
        if entry.matchable
        and entry.entry_type
        in (SUPPLIER_LEDGER_TYPE_FACTURE, SUPPLIER_LEDGER_TYPE_AVOIR)
    ]
    payments = [
        entry
        for entry in entries
        if entry.entry_type == SUPPLIER_LEDGER_TYPE_PAIEMENT
        and entry.debit > 0
        and not entry.matched
    ]
    diagnostics: list[SupplierLedgerDiagnostic] = []
    for payment in payments:
        result = _find_combination(open_items, payment.debit)
        if result.exact:
            status = "EXACT"
            message = "Combinaison exacte"
        elif not result.items:
            status = "AUCUNE_PIECE"
            message = (
                "Aucune pièce ouverte : facture manquante "
                f"{payment.debit:.2f}"
            )
        else:
            status = "ECART"
            if result.residual < 0:
                message = (
                    "Avoir manquant / facture modifiée : "
                    f"{abs(result.residual):.2f}"
                )
            elif result.residual > 0:
                message = (
                    "Facture manquante / avoir modifié : "
                    f"{result.residual:.2f}"
                )
            else:
                message = "Écart nul"
        if result.truncated:
            message += " (analyse limitée à 30 pièces)"
        diagnostics.append(
            SupplierLedgerDiagnostic(
                payment_id=payment.line_id,
                operation_date=payment.operation_date,
                label=payment.label,
                amount=payment.debit,
                status=status,
                combination=tuple(
                    item.reference for item in result.items
                ),
                residual_delta=result.residual,
                message=message,
            )
        )
    return diagnostics


def _safe_sheet_title(value: str, used: set[str]) -> str:
    text = re.sub(r"[\[\]:*?/\\]", "_", value).strip().strip("'")
    text = (text or "FOURNISSEUR")[:31]
    candidate = text
    index = 2
    while candidate.casefold() in used:
        suffix = f"_{index}"
        candidate = f"{text[: 31 - len(suffix)]}{suffix}"
        index += 1
    used.add(candidate.casefold())
    return candidate


def _supplier_slug(value: str) -> str:
    text = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode()
    )
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._-")
    return text[:80] or "FOURNISSEUR"


def _supplier_matched_display(entry: SupplierLedgerEntry) -> str:
    if entry.entry_type in {
        SUPPLIER_LEDGER_TYPE_PAIEMENT,
        SUPPLIER_LEDGER_TYPE_AVOIR_REMBOURSEMENT,
    }:
        pieces = entry.matched_pieces or "NON RAPPROCHEE"
        return f"Règle Facture : {pieces}"
    if entry.entry_type == SUPPLIER_LEDGER_TYPE_FACTURE and entry.payment_date:
        method = entry.payment_method or "VIR"
        return (
            f"Payée le {_format_display_date(entry.payment_date)} par {method}"
        )
    return entry.matched_pieces


def _write_supplier_ledger_sheet(
    sheet: Any,
    entries: list[SupplierLedgerEntry],
    diagnostics: list[SupplierLedgerDiagnostic],
    workbook_path: Path | None = None,
    *,
    include_matched_pieces: bool = False,
) -> None:
    columns = (
        SUPPLIER_LEDGER_COLUMNS_P25
        if include_matched_pieces
        else SUPPLIER_LEDGER_COLUMNS
    )
    thin = Side(style="thin", color=_BORDER_COLOR)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill("solid", fgColor=_HEADER_FILL)
    header_alignment = Alignment(
        horizontal="center", vertical="center", wrap_text=True
    )
    band_fill = PatternFill("solid", fgColor=_BAND_FILL)
    total_fill = PatternFill("solid", fgColor=_TOTAL_FILL)
    equilibrium_fill = PatternFill(
        "solid", fgColor=SUPPLIER_LEDGER_EQUILIBRIUM_FILL
    )
    diagnostic_fill = PatternFill(
        "solid", fgColor=SUPPLIER_LEDGER_DIAGNOSTIC_FILL
    )
    double_top = Side(style="double", color=_HEADER_FILL)
    total_border = Border(left=thin, right=thin, top=double_top, bottom=thin)

    sheet.sheet_view.showGridLines = False
    for column, name in enumerate(columns, start=1):
        cell = sheet.cell(row=1, column=column, value=name)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = border
        cell.alignment = header_alignment
    sheet.row_dimensions[1].height = 32

    running = Decimal("0.00")
    equilibrium_rows: list[int] = []
    for index, entry in enumerate(entries, start=2):
        running = (running + entry.credit - entry.debit).quantize(_CENT)
        link_invoice = (
            entry.invoice_id
            or entry.matched_pieces
            or entry.reference
        )
        if entry.entry_type == SUPPLIER_LEDGER_TYPE_PAIEMENT and entry.matched_pieces:
            link_invoice = entry.matched_pieces.split(" + ")[0].strip()
        piece_link = (
            _piece_link_for_row(
                workbook_path,
                {
                    "invoice_id": link_invoice,
                    "category": DocumentCategory.ACHATS_MARCHANDISES.value,
                    "piece_pdf": entry.piece_pdf,
                },
            )
            if workbook_path is not None
            else None
        )
        matched_display = _supplier_matched_display(entry)
        values = (
            _date_cell(entry.operation_date),
            entry.entry_type,
            _text(entry.reference),
            _text(entry.label),
            entry.debit,
            entry.credit,
            None,
            _text(entry.status),
            *((_text(matched_display),) if include_matched_pieces else ()),
            piece_link,
        )
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=index, column=column)
            if column == len(columns) and _write_piece_link(
                cell, value
            ):
                cell.border = border
                continue
            if column == 7:
                cell.value = (
                    f"=F{index}-E{index}"
                    if index == 2
                    else f"=G{index - 1}+F{index}-E{index}"
                )
            elif isinstance(value, Decimal):
                cell.value = float(value)
            else:
                cell.value = value
            cell.border = border
            if index % 2 == 0:
                cell.fill = band_fill
            if column in (5, 6):
                cell.number_format = "#,##0.00"
                cell.alignment = Alignment(
                    horizontal="right", vertical="center"
                )
            elif column == 7:
                cell.number_format = "#,##0.00"
                cell.alignment = Alignment(
                    horizontal="right", vertical="center"
                )
                if running == 0:
                    cell.fill = equilibrium_fill
                    cell.comment = Comment("Point d'équilibre", "DSH-Core")
                    equilibrium_rows.append(index)
            elif column == 1:
                cell.alignment = Alignment(
                    horizontal="center", vertical="center"
                )
                if isinstance(value, date_cls):
                    cell.number_format = "DD/MM/YYYY"
            elif column == 2:
                cell.alignment = Alignment(
                    horizontal="center", vertical="center"
                )
            else:
                cell.alignment = Alignment(
                    vertical="center", wrap_text=column in (3, 4, 8)
                )

    last_data_row = len(entries) + 1
    sheet.auto_filter.ref = (
        f"A1:{get_column_letter(len(columns) - 1)}{last_data_row}"
    )
    sheet.freeze_panes = "A2"

    total_row = last_data_row + 1
    total_values = {
        1: SUPPLIER_LEDGER_TOTAL_LABEL,
        5: f"=SUM(E2:E{last_data_row})" if entries else 0,
        6: f"=SUM(F2:F{last_data_row})" if entries else 0,
        7: f"=G{last_data_row}" if entries else 0,
        8: "SOLDE FINAL",
    }
    for column in range(1, len(columns) + 1):
        cell = sheet.cell(row=total_row, column=column)
        cell.font = Font(bold=True)
        cell.fill = total_fill
        cell.border = total_border
        cell.value = total_values.get(column)
        if column in (5, 6, 7):
            cell.number_format = "#,##0.00"
            cell.alignment = Alignment(
                horizontal="right", vertical="center"
            )
        else:
            cell.alignment = Alignment(
                horizontal="left", vertical="center"
            )
    if entries and running == 0:
        balance_cell = sheet.cell(row=total_row, column=7)
        balance_cell.fill = equilibrium_fill
        balance_cell.comment = Comment("Point d'équilibre", "DSH-Core")

    diagnostic_start = total_row + 2
    title_cell = sheet.cell(
        row=diagnostic_start,
        column=1,
        value=SUPPLIER_LEDGER_DIAGNOSTIC_TITLE,
    )
    title_cell.font = header_font
    title_cell.fill = header_fill
    title_cell.border = border
    for column in range(2, len(columns) + 1):
        cell = sheet.cell(row=diagnostic_start, column=column)
        cell.fill = header_fill
        cell.border = border
    sheet.row_dimensions[diagnostic_start].height = 22

    header_row = diagnostic_start + 1
    for column, name in enumerate(
        SUPPLIER_LEDGER_DIAGNOSTIC_COLUMNS, start=1
    ):
        cell = sheet.cell(row=header_row, column=column, value=name)
        cell.font = Font(bold=True)
        cell.fill = total_fill
        cell.border = border
        cell.alignment = header_alignment

    row = header_row + 1
    if diagnostics:
        for diagnostic in diagnostics:
            values = (
                _text(diagnostic.payment_id),
                _date_cell(diagnostic.operation_date),
                _text(diagnostic.label),
                diagnostic.amount,
                _text(diagnostic.status),
                _text(
                    ", ".join(diagnostic.combination)
                    if diagnostic.combination
                    else "AUCUNE"
                ),
                diagnostic.residual_delta,
                _text(diagnostic.message),
            )
            for column, value in enumerate(values, start=1):
                cell = sheet.cell(row=row, column=column)
                cell.value = float(value) if isinstance(value, Decimal) else value
                cell.border = border
                cell.fill = diagnostic_fill
                if column in (4, 7):
                    cell.number_format = "#,##0.00"
                    cell.alignment = Alignment(
                        horizontal="right", vertical="center"
                    )
                elif column == 2:
                    cell.alignment = Alignment(
                        horizontal="center", vertical="center"
                    )
                    if isinstance(value, date_cls):
                        cell.number_format = "DD/MM/YYYY"
                else:
                    cell.alignment = Alignment(
                        vertical="center",
                        wrap_text=column in (3, 6, 8),
                    )
            row += 1
    else:
        cell = sheet.cell(
            row=row, column=1, value="Aucun paiement non rapproché"
        )
        cell.border = border
        cell.alignment = Alignment(vertical="center")
        row += 1

    equilibrium_text = (
        "Points d'équilibre : "
        + ", ".join(str(item) for item in equilibrium_rows)
        if equilibrium_rows
        else "Aucun point d'équilibre"
    )
    cell = sheet.cell(row=row, column=1, value=equilibrium_text)
    cell.font = Font(bold=True)
    cell.alignment = Alignment(vertical="center")

    for column in range(1, len(columns) + 1):
        longest = len(columns[column - 1])
        if column == 1:
            longest = max(longest, len(SUPPLIER_LEDGER_DIAGNOSTIC_TITLE))
        for row_index in range(2, last_data_row + 1):
            longest = max(
                longest,
                _display_length(
                    sheet.cell(row=row_index, column=column).value
                ),
            )
        for row_index in range(header_row + 1, row + 1):
            longest = max(
                longest,
                _display_length(
                    sheet.cell(row=row_index, column=column).value
                ),
            )
        width = max(
            SUPPLIER_LEDGER_MIN_WIDTHS_P25[column - 1] if include_matched_pieces else SUPPLIER_LEDGER_MIN_WIDTHS[column - 1],
            min(longest + 2, 60),
        )
        sheet.column_dimensions[get_column_letter(column)].width = width


def _write_supplier_ledger_workbook(
    groups: dict[str, list[SupplierLedgerEntry]],
    diagnostics: dict[str, list[SupplierLedgerDiagnostic]],
    output_path: Path,
    title: str,
    *,
    include_matched_pieces: bool = False,
) -> None:
    workbook = Workbook()
    used_titles: set[str] = set()
    first = True
    if not groups:
        groups = {SUPPLIER_LEDGER_EMPTY_SHEET: []}
        diagnostics = {SUPPLIER_LEDGER_EMPTY_SHEET: []}
    for supplier, entries in groups.items():
        sheet = workbook.active if first else workbook.create_sheet()
        first = False
        sheet.title = _safe_sheet_title(supplier, used_titles)
        _write_supplier_ledger_sheet(
            sheet,
            entries,
            diagnostics.get(supplier, []),
            workbook_path=output_path,
            include_matched_pieces=include_matched_pieces,
        )
    workbook.properties.creator = "DSH-Core"
    workbook.properties.title = title
    _atomic_save_workbook(workbook, output_path)


def collect_supplier_diagnostics(
    store: SupplierLedgerStore, tenant_id: str
) -> tuple["SupplierDiagnosticEntry", ...]:
    """Return every reconciliation diagnostic of one tenant (all suppliers).

    Used by the background watcher to surface ``residual_delta != 0`` alerts in
    the Obsidian accounting cockpit. The computation is the exact same one as
    the supplier ledger export, so the cockpit and the workbook never diverge.
    """
    tenant = validate_tenant_id(tenant_id)
    rows = store.list_supplier_ledger(tenant)
    entries = _build_supplier_ledger_entries(rows)
    groups = _group_supplier_entries(entries)
    return tuple(
        SupplierDiagnosticEntry(supplier=supplier, diagnostic=diagnostic)
        for supplier, items in groups.items()
        for diagnostic in _build_supplier_diagnostics(items)
    )


@dataclass(frozen=True)
class SupplierDiagnosticEntry:
    """One reconciliation diagnostic paired with its third party."""

    supplier: str
    diagnostic: SupplierLedgerDiagnostic


class SupplierLedgerExporter:
    """Build the multi-supplier auxiliary ledger workbook."""

    def __init__(
        self,
        store: SupplierLedgerStore,
        *,
        export_dir: str | os.PathLike[str] | None = None,
        strict_tiers: bool = False,
        include_matched_pieces: bool = False,
    ) -> None:
        self._store = store
        self._export_dir = _resolve_export_dir(export_dir)
        self._strict_tiers = bool(strict_tiers)
        self._include_matched_pieces = bool(include_matched_pieces)

    @property
    def export_dir(self) -> Path:
        return self._export_dir

    def export_supplier_ledger(
        self,
        tenant_id: str,
        supplier_query: str | None = None,
        output_path: str | os.PathLike[str] | None = None,
    ) -> Path:
        """Write the supplier ledger workbook.

        A missing ``supplier_query`` produces one sheet per third party. A
        supplied query performs a case-insensitive partial match on the
        supplier name and still produces one sheet per matching third party.
        The file is named ``GRAND_LIVRE_<TENANT>_<SUPPLIER>.xlsx`` under
        ``DSH_ACCOUNTING_EXPORT_DIR`` (default
        ``workspace/exports/accounting``).
        """
        try:
            tenant = validate_tenant_id(tenant_id)
        except AccountingStoreError:
            raise SupplierLedgerExportError(
                "INVALID_TENANT_ID", repr(tenant_id)
            ) from None
        if supplier_query is not None and not isinstance(supplier_query, str):
            raise SupplierLedgerExportError(
                "INVALID_SUPPLIER_QUERY", repr(supplier_query)
            )
        rows = self._store.list_supplier_ledger(tenant, supplier_query)
        entries = _build_supplier_ledger_entries(
            rows,
            strict_tiers=self._strict_tiers,
            refund_as_credit=self._include_matched_pieces,
        )
        groups = _group_supplier_entries(
            entries, strict_tiers=self._strict_tiers
        )
        if (
            supplier_query is not None
            and supplier_query.strip()
            and not groups
        ):
            raise SupplierLedgerExportError(
                "SUPPLIER_NOT_FOUND", supplier_query
            )
        diagnostics = {
            supplier: _build_supplier_diagnostics(items)
            for supplier, items in groups.items()
        }
        query_label = (
            (supplier_query or "").strip() or "TOUS_FOURNISSEURS"
        )
        target = (
            Path(output_path)
            if output_path is not None
            else self._export_dir
            / tenant
            / (
                f"GRAND_LIVRE_{tenant}_"
                f"{_supplier_slug(query_label)}.xlsx"
            )
        )
        _validate_output_path(target)
        _write_supplier_ledger_workbook(
            groups,
            diagnostics,
            target,
            f"Grand livre {tenant}",
            include_matched_pieces=self._include_matched_pieces,
        )
        return target


def export_supplier_ledger(
    tenant_id: str,
    supplier_query: str | None = None,
    output_path: str | os.PathLike[str] | None = None,
    *,
    store: SqliteAccountingStore | None = None,
    export_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Convenience wrapper around :class:`SupplierLedgerExporter`."""
    exporter = SupplierLedgerExporter(
        store if store is not None else get_shared_sqlite_store(),
        export_dir=export_dir,
    )
    return exporter.export_supplier_ledger(
        tenant_id, supplier_query, output_path
    )


CLIENT_LEDGER_FILENAME = "GRAND_LIVRE_CLIENTS_DETAILLES.xlsx"
CLIENT_LEDGER_TYPE_FACTURE = "FACTURE"
CLIENT_LEDGER_TYPE_PAIEMENT = "PAIEMENT"
CLIENT_LEDGER_TYPE_AVOIR = "AVOIR"
CLIENT_LEDGER_COLUMNS_LEGACY: tuple[str, ...] = (
    "Date",
    "Type",
    "N° Pièce / Réf",
    "Libellé",
    "Débit",
    "Crédit",
    "Solde Cumulé",
    "Code Lettrage / Statut",
    PIECE_LINK_HEADER,
)
CLIENT_LEDGER_COLUMNS_P25: tuple[str, ...] = (
    "Date",
    "Type",
    "N° Pièce / Réf",
    "Libellé",
    "Débit",
    "Crédit",
    "Solde Cumulé",
    "Code Lettrage / Statut",
    "Pièce(s) / Règlement(s) Rapproché(s) en face",
    PIECE_LINK_HEADER,
)
CLIENT_LEDGER_COLUMNS = CLIENT_LEDGER_COLUMNS_LEGACY
CLIENT_LEDGER_MIN_OPERATIONS = 2


@dataclass(frozen=True)
class ClientLedgerEntry:
    """One unified customer ledger line (invoice, credit note or payment)."""

    entry_type: str
    operation_date: str | None
    reference: str
    label: str
    debit: Decimal
    credit: Decimal
    status: str
    line_id: str
    invoice_id: str | None
    client: str
    piece_pdf: str | None = None
    matched_pieces: str = ""
    payment_date: str = ""
    payment_method: str = ""


def _client_invoice_date(row: dict[str, Any], record: dict[str, Any]) -> str:
    for key in ("operation_date", "origin_document_date", "reconciliation_date"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    invoice = str(row.get("invoice_id") or record.get("invoice_id") or "")
    match = re.search(r"FA(\d+)", invoice, re.IGNORECASE)
    if match and int(match.group(1)) < 16406:
        return "2025-10-31"
    return ""


def _client_invoice_entries(
    rows: list[dict[str, Any]], *, strict_tiers: bool = False
) -> list[ClientLedgerEntry]:
    entries: list[ClientLedgerEntry] = []
    tiers = _tier_reference()
    memory = _learned_memory()
    for row in rows:
        if str(row.get("category") or "") != DocumentCategory.VENTES_CLIENTS.value:
            continue
        document_type = str(row.get("document_type") or "").strip().upper()
        line_id = str(row.get("line_id") or "")
        if document_type in {"OFX", "QFX", "LCR_PAYMENT"} or line_id.upper().startswith(
            "OFX-"
        ):
            continue
        if document_type in {"LCR_REMITTANCE", "LCR_SUB_PAYMENT"}:
            continue
        if (
            str(row.get("reconciliation_status") or "").strip().upper()
            == "SOLDEE_EXERCICE_ANTERIEUR_2025"
        ):
            continue
        record = _summary_record(row)
        ttc = record["ttc"] or Decimal("0.00")
        if ttc == 0:
            continue
        raw_client = str(record["counterparty"] or "").strip()
        role = tiers.role_for(raw_client)
        if role == "INTERNE_EXCLUS" or role in {"FOURNISSEUR", "PRESTATAIRE"}:
            continue
        if strict_tiers and role != "CLIENT":
            continue
        client = (
            tiers.canonical_name(raw_client)
            if tiers.resolve(raw_client) is not None
            else memory.canonical_name(raw_client)
        ) or raw_client
        if not client:
            continue
        is_credit_note = document_type == "AVOIR" or ttc < 0
        paid = (
            str(row.get("reconciliation_status") or "").upper()
            in _PAID_RECONCILIATION_STATUSES
        )
        reconciliation_reference = _text_field(
            row, "reconciliation_reference", "fitid"
        )
        entries.append(
            ClientLedgerEntry(
                entry_type=(
                    CLIENT_LEDGER_TYPE_AVOIR
                    if is_credit_note
                    else CLIENT_LEDGER_TYPE_FACTURE
                ),
                operation_date=_client_invoice_date(row, record),
                reference=str(record["invoice_id"] or line_id),
                label=str(row.get("label") or record["invoice_id"] or ""),
                debit=Decimal("0.00") if is_credit_note else abs(ttc).quantize(_CENT),
                credit=abs(ttc).quantize(_CENT) if is_credit_note else Decimal("0.00"),
                status=_reconciliation_label(row),
                line_id=line_id,
                invoice_id=str(record["invoice_id"] or line_id),
                client=client,
                piece_pdf=str(row.get("piece_pdf") or "") or None,
                matched_pieces=reconciliation_reference if paid else "",
                payment_date=(
                    str(row.get("reconciliation_date") or "") if paid else ""
                ),
                payment_method=(
                    _payment_method_label(reconciliation_reference) if paid else ""
                ),
            )
        )
    return entries


def _client_subset(
    entries: list[ClientLedgerEntry], target: Decimal
) -> list[ClientLedgerEntry] | None:
    if target <= 0 or not entries:
        return None
    items = sorted(
        ((entry, abs(entry.debit - entry.credit)) for entry in entries),
        key=lambda item: item[1],
        reverse=True,
    )[:20]

    def search(index: int, remaining: Decimal, chosen: list[ClientLedgerEntry]):
        if abs(remaining) < _CENT:
            return chosen
        if index >= len(items) or remaining < 0:
            return None
        entry, amount = items[index]
        if amount <= remaining:
            result = search(index + 1, remaining - amount, [*chosen, entry])
            if result is not None:
                return result
        return search(index + 1, remaining, chosen)

    return search(0, target.quantize(_CENT), [])


def _client_payment_entries(
    rows: list[dict[str, Any]],
    invoices: list[ClientLedgerEntry],
    *,
    strict_tiers: bool = False,
) -> list[ClientLedgerEntry]:
    entries: list[ClientLedgerEntry] = []
    memory = _learned_memory()
    tiers = _tier_reference()
    invoice_numbers: dict[str, str] = {}
    for invoice in invoices:
        for value in (invoice.reference, invoice.invoice_id or ""):
            if value:
                invoice_numbers[_fold(value)] = invoice.client
    matched_invoices: set[str] = set()
    sorted_rows = sorted(
        rows,
        key=lambda row: str(row.get("operation_date") or ""),
    )
    for row in sorted_rows:
        debit = _decimal(row.get("debit_amount")) or Decimal("0.00")
        credit = _decimal(row.get("credit_amount")) or Decimal("0.00")
        if credit <= 0 or debit > 0:
            continue
        label = str(row.get("label") or "")
        reference = str(
            row.get("payment_reference")
            or row.get("remittance_reference")
            or row.get("fitid")
            or row.get("line_id")
            or ""
        )
        folded_label = _fold(label)
        raw_client = _text_field(row, "counterparty", "supplier")
        role = tiers.role_for(raw_client) or tiers.role_for(label)
        if role == "INTERNE_EXCLUS" or role in {"FOURNISSEUR", "PRESTATAIRE"}:
            continue
        if strict_tiers and role != "CLIENT":
            continue
        client = (
            tiers.canonical_name(raw_client)
            if tiers.resolve(raw_client) is not None
            else memory.canonical_name(raw_client)
        )
        if not client:
            client = memory.match_reference(label) or ""
        if not client:
            for number, invoice_client in invoice_numbers.items():
                if number and number in folded_label:
                    client = invoice_client
                    break
        if not client:
            continue
        if "yucel" in _fold(f"{label} {client}"):
            client = "CARROSSERIE_YUCEL"
        candidates = [
            invoice
            for invoice in invoices
            if invoice.client == client
            and invoice.reference not in matched_invoices
            and (invoice.debit - invoice.credit) != 0
        ]
        exact = [
            invoice
            for invoice in candidates
            if abs(invoice.debit - invoice.credit) == credit
        ]
        matched_pieces: list[str] = []
        if exact:
            payment_date = str(row.get("operation_date") or "")
            exact.sort(
                key=lambda invoice: abs(
                    (
                        _parse_iso_date(invoice.operation_date)
                        or date_cls.min
                    )
                    - (
                        _parse_iso_date(payment_date)
                        or date_cls.min
                    )
                )
            )
            selected = [exact[0]]
        else:
            subset = _client_subset(candidates, credit)
            selected = subset or []
        if selected:
            matched_pieces = [value.reference for value in selected]
            matched_invoices.update(value.reference for value in selected)
        entries.append(
            ClientLedgerEntry(
                entry_type=CLIENT_LEDGER_TYPE_PAIEMENT,
                operation_date=str(row.get("operation_date") or ""),
                reference=reference,
                label=label,
                debit=Decimal("0.00"),
                credit=credit.quantize(_CENT),
                status="LETTRE" if selected else "NON_LETTRE",
                line_id=str(row.get("line_id") or ""),
                invoice_id=None,
                client=client,
                matched_pieces=" + ".join(matched_pieces),
            )
        )
    return entries


_PAYMENT_REFERENCE_PREFIXES = ("virement ", "vir ", "lcr ", "cb ")


def _normalize_payment_reference(value: Any) -> str:
    """Fold a payment reference and strip the bank-operation prefixes.

    ``"VIREMENT 202610061041193444201450"`` and
    ``"202610061041193444201450"`` must compare equal so a synthetic
    ``Règlement lettré (registre)`` line is never created twice.
    """
    text = _fold(value).strip()
    changed = True
    while changed:
        changed = False
        for prefix in _PAYMENT_REFERENCE_PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix) :].strip()
                changed = True
    return text


def _payment_method_label(reference: Any) -> str:
    """Return ``VIR`` / ``REMISE`` / ``LCR`` / ``CB`` for a payment reference."""
    text = _fold(reference)
    if "remise" in text:
        return "REMISE"
    if "lcr" in text:
        return "LCR"
    if text.startswith("cb ") or "carte" in text:
        return "CB"
    if "vir" in text:
        return "VIR"
    return ""


def _client_synthetic_payments(
    invoices: list[dict[str, Any]], existing: list[ClientLedgerEntry]
) -> list[ClientLedgerEntry]:
    """Materialise lettering already stored on invoices without a bank row.

    A synthetic payment is only kept for a global remittance that has no
    individual bank/virement line already booked. Any reference, date/amount
    pair or amount already lettered on a ``FA1*`` invoice suppresses the
    duplicate ``Règlement lettré (registre)`` line.
    """
    existing_references = {
        _normalize_payment_reference(entry.reference)
        for entry in existing
        if entry.reference
    }
    existing_pairs = {
        (str(entry.operation_date or ""), entry.credit) for entry in existing
    }
    existing_amounts = {entry.credit for entry in existing}
    lettered_client_amounts: set[Decimal] = set()
    lettered_client_references: set[str] = set()
    for entry in existing:
        pieces = [
            piece.strip()
            for piece in re.split(r"\s*\+\s*", entry.matched_pieces or "")
            if piece.strip()
        ]
        for piece in pieces:
            if is_client_invoice_reference(piece):
                lettered_client_amounts.add(entry.credit)
                lettered_client_references.add(piece.upper())
    synthetic: list[ClientLedgerEntry] = []
    for row in invoices:
        status = str(row.get("reconciliation_status") or "").upper()
        if status not in _PAID_RECONCILIATION_STATUSES:
            continue
        amount = _decimal_field(row, "reconciliation_amount")
        record = _summary_record(row)
        if amount is None:
            amount = record["ttc"] or Decimal("0.00")
        amount = amount.quantize(_CENT)
        if amount <= 0:
            continue
        client = _learned_memory().canonical_name(record["counterparty"])
        if not client:
            continue
        reference = _text_field(row, "reconciliation_reference", "fitid") or str(
            row.get("line_id") or ""
        )
        date_value = str(
            row.get("reconciliation_date") or row.get("operation_date") or ""
        )
        normalized_reference = _normalize_payment_reference(reference)
        invoice_reference = str(record["invoice_id"] or "").upper()
        if normalized_reference and normalized_reference in existing_references:
            continue
        if (date_value, amount) in existing_pairs:
            continue
        if amount in existing_amounts and is_client_invoice_reference(
            invoice_reference
        ):
            continue
        if amount in lettered_client_amounts:
            continue
        if invoice_reference and invoice_reference in lettered_client_references:
            continue
        existing_references.add(normalized_reference)
        existing_pairs.add((date_value, amount))
        existing_amounts.add(amount)
        synthetic.append(
            ClientLedgerEntry(
                entry_type=CLIENT_LEDGER_TYPE_PAIEMENT,
                operation_date=date_value,
                reference=reference,
                label="Règlement lettré (registre)",
                debit=Decimal("0.00"),
                credit=amount,
                status="LETTRE",
                line_id=str(row.get("line_id") or ""),
                invoice_id=None,
                client=client,
            )
        )
    return synthetic


def _format_display_date(value: Any) -> str:
    parsed = _parse_iso_date(value)
    if parsed is not None:
        return parsed.strftime("%d/%m/%Y")
    text = str(value or "").strip()
    return text[:10] if text else ""


def _client_matched_display(entry: ClientLedgerEntry) -> str:
    if entry.entry_type == CLIENT_LEDGER_TYPE_PAIEMENT:
        pieces = entry.matched_pieces or "NON RAPPROCHEE"
        return f"Règle Facture : {pieces}"
    if entry.entry_type == CLIENT_LEDGER_TYPE_FACTURE and entry.payment_date:
        method = entry.payment_method or "VIR"
        return (
            f"Payée le {_format_display_date(entry.payment_date)} par {method}"
        )
    return entry.matched_pieces


def _write_client_ledger_sheet(
    sheet: Any,
    entries: list[ClientLedgerEntry],
    workbook_path: Path,
    *,
    include_matched_pieces: bool = False,
) -> None:
    columns = (
        CLIENT_LEDGER_COLUMNS_P25
        if include_matched_pieces
        else CLIENT_LEDGER_COLUMNS
    )
    thin = Side(style="thin", color=_BORDER_COLOR)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill("solid", fgColor=_HEADER_FILL)
    header_alignment = Alignment(
        horizontal="center", vertical="center", wrap_text=True
    )
    band_fill = PatternFill("solid", fgColor=_BAND_FILL)
    total_fill = PatternFill("solid", fgColor=_TOTAL_FILL)
    equilibrium_fill = PatternFill(
        "solid", fgColor=SUPPLIER_LEDGER_EQUILIBRIUM_FILL
    )
    double_top = Side(style="double", color=_HEADER_FILL)
    total_border = Border(left=thin, right=thin, top=double_top, bottom=thin)
    sheet.sheet_view.showGridLines = False
    for column, name in enumerate(columns, start=1):
        cell = sheet.cell(row=1, column=column, value=name)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = border
        cell.alignment = header_alignment
    sheet.row_dimensions[1].height = 32
    running = Decimal("0.00")
    for index, entry in enumerate(entries, start=2):
        running = (running + entry.debit - entry.credit).quantize(_CENT)
        link_invoice = entry.invoice_id or entry.reference
        if entry.entry_type == CLIENT_LEDGER_TYPE_PAIEMENT and entry.matched_pieces:
            link_invoice = entry.matched_pieces.split(" + ")[0].strip()
        link = _piece_link_for_row(
            workbook_path,
            {
                "invoice_id": link_invoice,
                "category": DocumentCategory.VENTES_CLIENTS.value,
                "piece_pdf": entry.piece_pdf,
            },
        )
        matched_display = _client_matched_display(entry)
        values = (
            _date_cell(entry.operation_date),
            entry.entry_type,
            _text(entry.reference),
            _text(entry.label),
            entry.debit,
            entry.credit,
            None,
            _text(entry.status),
            *((_text(matched_display),) if include_matched_pieces else ()),
            link,
        )
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=index, column=column)
            if column == len(columns) and _write_piece_link(cell, value):
                cell.border = border
                continue
            if column == 7:
                cell.value = (
                    f"=E{index}-F{index}"
                    if index == 2
                    else f"=G{index - 1}+E{index}-F{index}"
                )
            elif isinstance(value, Decimal):
                cell.value = float(value)
            else:
                cell.value = value
            cell.border = border
            if index % 2 == 0:
                cell.fill = band_fill
            if column in (5, 6):
                cell.number_format = "#,##0.00"
                cell.alignment = Alignment(
                    horizontal="right", vertical="center"
                )
            elif column == 7:
                cell.number_format = "#,##0.00"
                cell.alignment = Alignment(
                    horizontal="right", vertical="center"
                )
                if running == 0:
                    cell.fill = equilibrium_fill
                    cell.comment = Comment("Solde client soldé", "DSH-Core")
            elif column == 1:
                cell.alignment = Alignment(
                    horizontal="center", vertical="center"
                )
                if isinstance(value, date_cls):
                    cell.number_format = "DD/MM/YYYY"
            elif column == 2:
                cell.alignment = Alignment(
                    horizontal="center", vertical="center"
                )
    total_row = len(entries) + 2
    total_debit = sum((entry.debit for entry in entries), Decimal("0.00"))
    total_credit = sum((entry.credit for entry in entries), Decimal("0.00"))
    totals = {
        1: "TOTAL",
        5: total_debit.quantize(_CENT),
        6: total_credit.quantize(_CENT),
        7: (total_debit - total_credit).quantize(_CENT),
    }
    for column in range(1, len(columns) + 1):
        cell = sheet.cell(row=total_row, column=column)
        cell.font = Font(bold=True)
        cell.fill = total_fill
        cell.border = total_border
        cell.value = totals.get(column)
        if column in (5, 6, 7):
            cell.number_format = "#,##0.00"
            cell.alignment = Alignment(horizontal="right", vertical="center")
        else:
            cell.alignment = Alignment(horizontal="left", vertical="center")
    sheet.auto_filter.ref = (
        f"A1:{get_column_letter(len(columns) - 1)}{len(entries) + 1}"
    )
    sheet.freeze_panes = "A2"
    widths = (
        (12, 12, 24, 40, 14, 14, 16, 24, 30, 32)
        if include_matched_pieces
        else (12, 12, 24, 40, 14, 14, 16, 24, 32)
    )
    for column, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width


class ClientLedgerExporter:
    """Build ``GRAND_LIVRE_CLIENTS_DETAILLES.xlsx`` (one sheet per client)."""

    def __init__(
        self,
        store: Any,
        *,
        export_dir: str | os.PathLike[str] | None = None,
        strict_tiers: bool = False,
        include_matched_pieces: bool = False,
    ) -> None:
        self._store = store
        self._export_dir = _resolve_export_dir(export_dir)
        self._strict_tiers = bool(strict_tiers)
        self._include_matched_pieces = bool(include_matched_pieces)

    def export(self, tenant_id: str) -> Path:
        try:
            tenant = validate_tenant_id(tenant_id)
        except AccountingStoreError:
            raise SupplierLedgerExportError(
                "INVALID_TENANT_ID", repr(tenant_id)
            ) from None
        rows = self._store.list_master_lines(tenant)
        invoice_entries = _client_invoice_entries(
            rows, strict_tiers=self._strict_tiers
        )
        payment_entries = _client_payment_entries(
            rows, invoice_entries, strict_tiers=self._strict_tiers
        )
        payment_entries.extend(
            _client_synthetic_payments(rows, payment_entries)
        )
        grouped: dict[str, list[ClientLedgerEntry]] = {}
        for entry in (*invoice_entries, *payment_entries):
            grouped.setdefault(entry.client, []).append(entry)
        regular = {
            client: sorted(
                entries,
                key=lambda item: (
                    item.operation_date or "",
                    0
                    if item.entry_type == CLIENT_LEDGER_TYPE_FACTURE
                    else 1,
                    item.line_id,
                ),
            )
            for client, entries in grouped.items()
            if len(entries) >= CLIENT_LEDGER_MIN_OPERATIONS
            and client.upper()
            not in {"CLIENTS_CHEQUES", "CLIENTS_LCR", "CLIENT_DIVERS"}
        }
        target = (
            self._export_dir
            / tenant
            / CLIENT_LEDGER_FILENAME
        )
        _validate_output_path(target)
        workbook = Workbook()
        used_titles: set[str] = set()
        first = True
        if not regular:
            sheet = workbook.active
            sheet.title = "AUCUN_CLIENT"
            first = False
        for client, entries in sorted(
            regular.items(), key=lambda item: item[0].casefold()
        ):
            sheet = workbook.active if first else workbook.create_sheet()
            first = False
            sheet.title = _safe_sheet_title(
                normalize_counterparty_name(client), used_titles
            )
            _write_client_ledger_sheet(
                sheet,
                entries,
                target,
                include_matched_pieces=self._include_matched_pieces,
            )
        workbook.properties.creator = "DSH-Core P24"
        workbook.properties.title = f"Grand livre clients {tenant}"
        _atomic_save_workbook(workbook, target)
        return target


def export_client_ledger(
    tenant_id: str,
    *,
    store: SqliteAccountingStore | None = None,
    export_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Convenience wrapper around :class:`ClientLedgerExporter`."""
    return ClientLedgerExporter(
        store if store is not None else get_shared_sqlite_store(),
        export_dir=export_dir,
    ).export(tenant_id)


# ---------------------------------------------------------------------------
# P17 - Expense sub-categories, master tables, VAT de-duplication & fiscal year
# ---------------------------------------------------------------------------

EXPENSE_SUBCATEGORY_HEADERS: dict[ExpenseSubCategory, tuple[str, ...]] = {
    ExpenseSubCategory.RESTAURATION: (
        "Date Ticket",
        "Date de Paiement",
        "Lieu",
        "Société / Personne Invitée",
        "HT",
        "TVA",
        "TTC",
    ),
    ExpenseSubCategory.CARBURANT: (
        "Date",
        "Date de Paiement",
        "Fournisseur / Station",
        "Véhicule",
        "HT",
        "TVA",
        "TTC",
    ),
    ExpenseSubCategory.TRANSPORT_PARKING: (
        "Date",
        "Date de Paiement",
        "Nature (Péage/Train/Avion/Parking)",
        "Trajet / Lieu",
        "HT",
        "TVA",
        "TTC",
    ),
    ExpenseSubCategory.HOTELLERIE: (
        "Date",
        "Date de Paiement",
        "Hôtel / Lieu",
        "HT Hébergement",
        "HT Petit-Déj / Repas",
        "TVA Déductible",
        "TVA Non Déductible",
        "TTC",
    ),
    ExpenseSubCategory.INFORMATIQUE_WEB: (
        "Date",
        "Date de Paiement",
        "Fournisseur",
        "Description",
        "HT",
        "TVA",
        "TTC",
    ),
    ExpenseSubCategory.HONORAIRES_JURIDIQUES: (
        "Date",
        "Date de Paiement",
        "Fournisseur",
        "Description",
        "HT",
        "TVA",
        "TTC",
    ),
    ExpenseSubCategory.FRAIS_REPRESENTATION_PRESIDENT: (
        "Date",
        "Date de Paiement",
        "Fournisseur",
        "Description",
        "HT",
        "TVA",
        "TTC",
    ),
}
EXPENSE_SYNTHESIS_HEADERS: tuple[str, ...] = (
    "Sous-catégorie",
    "Nb pièces",
    "HT",
    "TVA",
    "TTC",
)
EXPENSE_SYNTHESIS_FILENAME = "SYNTHESE_FRAIS_GENERAUX.xlsx"
EXPENSE_SUBTABLE_FILENAME = "TABLEAU_{subcategory}.xlsx"
MASTER_PIECES_FILENAME = "TABLEAU_MERE_PIECES_COMPTABLES.xlsx"
MASTER_ACHATS_FILENAME = MASTER_PIECES_FILENAME
LEGACY_MASTER_ACHATS_FILENAME = "TABLEAU_MERE_ACHATS_CHARGES.xlsx"
MASTER_BANQUE_TVA_FILENAME = "TABLEAU_MERE_BANQUE_TVA.xlsx"
MASTER_PIECES_HEADERS: tuple[str, ...] = (
    "Date Pièce",
    "Date de Paiement",
    "Flux (VENTE_CLIENT / ACHAT_FOURNISSEUR)",
    "Tiers (Client ou Fournisseur)",
    "N° Pièce",
    "Montant HT",
    "Montant TVA",
    "Montant TTC",
    "Statut",
    "Lien PDF Pièce (Cliquable)",
    "Rapprochement Banque / LCR",
    "Écart / Solde Dû",
)
MASTER_ACHATS_HEADERS = MASTER_PIECES_HEADERS
MASTER_BANQUE_TVA_HEADERS: tuple[str, ...] = (
    "Date Opération",
    "Tiers / Contrepartie identifiée (Client / Fournisseur / Dirigeant)",
    "Type",
    "Libellé Opération",
    "Débit",
    "Crédit",
    "Pièces Rapprochées (N° & Liens PDF cliquables)",
    "TVA Déduite/Collectée",
    "Solde Lettrage",
)
MASTER_TVA_RECAP_HEADERS: tuple[str, ...] = (
    "Exercice",
    "Nb pièces",
    "HT",
    "TVA",
    "TTC",
)
_SUBCATEGORY_DOCUMENT_TYPES = {
    "FUEL_RECEIPT": ExpenseSubCategory.CARBURANT,
    "MEAL_RECEIPT": ExpenseSubCategory.RESTAURATION,
    "RESTAURANT_RECEIPT": ExpenseSubCategory.RESTAURATION,
    "HOTEL_RECEIPT": ExpenseSubCategory.HOTELLERIE,
    "HOTEL": ExpenseSubCategory.HOTELLERIE,
    "TOLL_RECEIPT": ExpenseSubCategory.TRANSPORT_PARKING,
    "TRAIN_TICKET": ExpenseSubCategory.TRANSPORT_PARKING,
    "FLIGHT_TICKET": ExpenseSubCategory.TRANSPORT_PARKING,
    "PARKING_RECEIPT": ExpenseSubCategory.TRANSPORT_PARKING,
}
_SUBCATEGORY_KEYWORDS: tuple[tuple[ExpenseSubCategory, tuple[str, ...]], ...] = (
    (
        ExpenseSubCategory.FRAIS_REPRESENTATION_PRESIDENT,
        (
            "temps des cerises",
            "indigo gallery",
            "tradeinn",
            "representation",
            "représentation",
        ),
    ),
    (
        ExpenseSubCategory.HOTELLERIE,
        (
            "hotel",
            "nuit",
            "nuitee",
            "hebergement",
            "booking",
            "ibis",
            "premiere classe",
            "soiree etape",
            "petit dejeuner",
        ),
    ),
    (
        ExpenseSubCategory.CARBURANT,
        ("carburant", "essence", "gasoil", "diesel", "station", "total", "shell", "esso", "bp"),
    ),
    (
        ExpenseSubCategory.TRANSPORT_PARKING,
        ("peage", "autoroute", "parking", "train", "sncf", "avion", "vol ", "air france", "taxi"),
    ),
    (
        ExpenseSubCategory.INFORMATIQUE_WEB,
        (
            "hebergement site",
            "site internet",
            "nom de domaine",
            "noms de domaine",
            "informatique",
            "logiciel",
            "saas",
            "openrouter",
        ),
    ),
    (
        ExpenseSubCategory.HONORAIRES_JURIDIQUES,
        (
            "honoraires",
            "juridique",
            "avocat",
            "creation holding",
            "frais de greffe",
            "annonce legale",
        ),
    ),
    (
        ExpenseSubCategory.RESTAURATION,
        ("restaurant", "resto", "repas", "dejeuner", "diner", "brasserie", "cafe", "menu"),
    ),
)
_TRANSPORT_NATURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Péage", ("peage", "péage", "autoroute", "toll")),
    ("Train", ("train", "sncf", "rail")),
    ("Avion", ("avion", "vol", "flight", "air ")),
    ("Parking", ("parking", "stationnement")),
)


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text.casefold()


def _parse_iso_date(value: Any) -> date_cls | None:
    text = str(value or "").strip()
    if len(text) >= 10:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _text_field(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _decimal_field(row: dict[str, Any], *keys: str) -> Decimal | None:
    for key in keys:
        value = _decimal(row.get(key))
        if value is not None:
            return value
    return None


def is_personal_expense(row: dict[str, Any]) -> bool:
    """Return ``True`` for expenses settled by the manager's own account."""
    source = normalize_payment_source(row.get("payment_source"))
    if source is PaymentSource.COMPTE_PERSO_DIRIGEANT:
        return True
    account = str(row.get("counterparty_account") or "").strip().upper()
    return account == DIRIGEANT_COUNTERPART_ACCOUNT


def counterparty_account_of(row: dict[str, Any]) -> str:
    """Return the P17 counterpart account (455000_DIRIGEANT when personal)."""
    if is_personal_expense(row):
        return DIRIGEANT_COUNTERPART_ACCOUNT
    explicit = _text_field(row, "counterparty_account")
    if explicit:
        return explicit
    return counterparty_account_for_payment_source(
        row.get("payment_source")
    ) or ""


def payment_source_of(row: dict[str, Any]) -> str:
    source = normalize_payment_source(row.get("payment_source"))
    if source is not None:
        return source.value
    if is_personal_expense(row):
        return PaymentSource.COMPTE_PERSO_DIRIGEANT.value
    return ""


def _hotel_vat_from_row(
    row: dict[str, Any], record: dict[str, Any]
) -> tuple[Decimal, Decimal, Decimal | None, Decimal | None]:
    """Return ``(deductible, non_deductible, ht_accommodation, ht_meals)``.

    Explicit stored fields win. Otherwise a ``Soirée Étape`` mention or a
    multi-rate hotel bill is re-parsed from the row text. When nothing can be
    isolated, the historical conservative rule applies: accommodation VAT is
    fully non-deductible.
    """
    tva = record["tva"] or Decimal("0.00")
    accommodation = _decimal_field(row, "ht_accommodation")
    meals = _decimal_field(row, "ht_breakfast_meals")
    deductible = _decimal_field(row, "tva_deductible")
    non_deductible = _decimal_field(row, "tva_non_deductible")
    if deductible is not None or non_deductible is not None:
        if deductible is None:
            deductible = max(
                Decimal("0.00"), tva - (non_deductible or Decimal("0.00"))
            )
        if non_deductible is None:
            non_deductible = max(
                Decimal("0.00"), tva - deductible
            )
        return deductible, non_deductible, accommodation, meals

    breakdown = parse_hotel_evening_stage(row)
    if breakdown is not None:
        return (
            breakdown.tva_deductible,
            breakdown.tva_non_deductible,
            accommodation if accommodation is not None else breakdown.ht_accommodation,
            meals if meals is not None else breakdown.ht_meals,
        )
    return Decimal("0.00"), tva, accommodation, meals


def _infer_expense_subcategory(row: dict[str, Any]) -> ExpenseSubCategory | None:
    explicit = str(row.get("expense_subcategory") or "").strip()
    if explicit:
        try:
            return ExpenseSubCategory(explicit)
        except ValueError:
            pass
    document_type = str(row.get("document_type") or "").strip().upper()
    if document_type in _SUBCATEGORY_DOCUMENT_TYPES:
        return _SUBCATEGORY_DOCUMENT_TYPES[document_type]
    if parse_hotel_evening_stage(row) is not None:
        return ExpenseSubCategory.HOTELLERIE
    haystack = _fold(
        " ".join(
            str(row.get(key) or "")
            for key in (
                "label",
                "counterparty",
                "invoice_url",
                "fuel_station",
                "hotel_place",
            )
        )
    )
    for subcategory, keywords in _SUBCATEGORY_KEYWORDS:
        if any(keyword in haystack for keyword in keywords):
            return subcategory
    return None


def _transport_nature(row: dict[str, Any]) -> str:
    explicit = _text_field(row, "transport_nature")
    if explicit:
        return explicit
    haystack = _fold(
        " ".join(
            str(row.get(key) or "")
            for key in ("label", "document_type", "counterparty")
        )
    )
    for label, keywords in _TRANSPORT_NATURES:
        if any(keyword in haystack for keyword in keywords):
            return label
    return "TRANSPORT"


def _expense_row_values(
    subcategory: ExpenseSubCategory,
    row: dict[str, Any],
    record: dict[str, Any],
) -> tuple[Any, ...]:
    date_value = _date_cell(record["date"])
    payment_value = _payment_date_cell(row)
    ht = record["ht"] or Decimal("0.00")
    tva = record["tva"] or Decimal("0.00")
    ttc = record["ttc"] or Decimal("0.00")
    if subcategory is ExpenseSubCategory.RESTAURATION:
        return (
            date_value,
            payment_value,
            _text_field(row, "lieu_restaurant") or record["counterparty"],
            _text_field(row, "invite_restaurant"),
            ht,
            tva,
            ttc,
        )
    if subcategory is ExpenseSubCategory.CARBURANT:
        return (
            date_value,
            payment_value,
            _text_field(row, "fuel_station") or record["counterparty"],
            _text_field(row, "vehicle"),
            ht,
            tva,
            ttc,
        )
    if subcategory is ExpenseSubCategory.TRANSPORT_PARKING:
        return (
            date_value,
            payment_value,
            _transport_nature(row),
            _text_field(row, "transport_route")
            or record["counterparty"]
            or _text_field(row, "label"),
            ht,
            tva,
            ttc,
        )
    if subcategory in (
        ExpenseSubCategory.INFORMATIQUE_WEB,
        ExpenseSubCategory.HONORAIRES_JURIDIQUES,
        ExpenseSubCategory.FRAIS_REPRESENTATION_PRESIDENT,
    ):
        return (
            date_value,
            payment_value,
            record["counterparty"],
            _text_field(row, "label")
            or record["invoice_id"]
            or _text_field(row, "invoice_url"),
            ht,
            tva,
            ttc,
        )
    accommodation = _decimal_field(row, "ht_accommodation") or ht
    breakfast = _decimal_field(row, "ht_breakfast_meals") or Decimal("0.00")
    deductible, non_deductible, parsed_accommodation, parsed_meals = (
        _hotel_vat_from_row(row, record)
    )
    if parsed_accommodation is not None:
        accommodation = parsed_accommodation
    if parsed_meals is not None:
        breakfast = parsed_meals
    return (
        date_value,
        payment_value,
        _text_field(row, "hotel_place") or record["counterparty"],
        accommodation,
        breakfast,
        deductible,
        non_deductible,
        ttc,
    )


@dataclass(frozen=True)
class ExpenseSubCategoryExportReport:
    """Paths produced by the four expense sub-tables and the global summary."""

    global_path: Path
    subtable_paths: dict[ExpenseSubCategory, Path]
    subfolders: dict[ExpenseSubCategory, tuple[Path, Path]]
    subcategory_counts: dict[ExpenseSubCategory, int]


def _write_p17_workbook(
    output_path: Path,
    *,
    title: str,
    headers: tuple[str, ...],
    rows: list[tuple[Any, ...]],
    total_row: tuple[Any, ...] | None = None,
    hyperlinks: dict[tuple[int, int], str] | None = None,
) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title[:31]
    sheet.sheet_view.showGridLines = False
    thin = Side(style="thin", color=_BORDER_COLOR)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill("solid", fgColor=_HEADER_FILL)
    header_alignment = Alignment(
        horizontal="center", vertical="center", wrap_text=True
    )
    band_fill = PatternFill("solid", fgColor=_BAND_FILL)
    total_fill = PatternFill("solid", fgColor=_TOTAL_FILL)
    double_top = Side(style="double", color=_HEADER_FILL)
    total_border = Border(left=thin, right=thin, top=double_top, bottom=thin)

    for column, name in enumerate(headers, start=1):
        cell = sheet.cell(row=1, column=column, value=name)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = border
        cell.alignment = header_alignment
    sheet.row_dimensions[1].height = 32

    for offset, values in enumerate(rows, start=2):
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=offset, column=column)
            if _write_piece_link(cell, value):
                cell.border = border
                continue
            hyperlink = (hyperlinks or {}).get((offset, column))
            if hyperlink:
                if _write_piece_link(cell, hyperlink, display_text=value):
                    cell.border = border
                    cell.alignment = Alignment(
                        vertical="center", wrap_text=True
                    )
                    continue
                cell.value = value
                cell.hyperlink = hyperlink
                cell.font = Font(color="0563C1", underline="single")
                cell.border = border
                cell.alignment = Alignment(vertical="center", wrap_text=True)
                continue
            cell.value = (
                float(value) if isinstance(value, Decimal) else value
            )
            cell.border = border
            if offset % 2 == 0:
                cell.fill = band_fill
            if isinstance(value, Decimal):
                cell.number_format = "#,##0.00"
                cell.alignment = Alignment(
                    horizontal="right", vertical="center"
                )
            elif isinstance(value, date_cls):
                cell.number_format = "DD/MM/YYYY"
                cell.alignment = Alignment(
                    horizontal="center", vertical="center"
                )
            else:
                cell.alignment = Alignment(vertical="center", wrap_text=True)

    last_data_row = len(rows) + 1
    sheet.auto_filter.ref = (
        f"A1:{get_column_letter(len(headers))}{last_data_row}"
    )
    sheet.freeze_panes = "A2"

    if total_row is not None:
        total_index = last_data_row + 1
        for column, value in enumerate(total_row, start=1):
            cell = sheet.cell(row=total_index, column=column)
            cell.value = (
                float(value) if isinstance(value, Decimal) else value
            )
            cell.font = Font(bold=True)
            cell.fill = total_fill
            cell.border = total_border
            if isinstance(value, Decimal):
                cell.number_format = "#,##0.00"
                cell.alignment = Alignment(
                    horizontal="right", vertical="center"
                )

    for column in range(1, len(headers) + 1):
        longest = len(str(headers[column - 1]))
        for row_index in range(2, sheet.max_row + 1):
            longest = max(
                longest,
                _display_length(sheet.cell(row=row_index, column=column).value),
            )
        sheet.column_dimensions[get_column_letter(column)].width = max(
            12, min(longest + 2, 60)
        )

    workbook.properties.creator = "DSH-Core"
    workbook.properties.title = title
    _atomic_save_workbook(workbook, output_path)


class ExpenseSubCategoryExporter:
    """Build the ``FRAIS_GENERAUX`` sub-tables and their global summary."""

    def __init__(
        self,
        store: CategoryLineStore,
        *,
        root: str | os.PathLike[str] | None = None,
        export_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self._store = store
        self._root = root
        self._export_dir = Path(export_dir) if export_dir is not None else None

    def export(
        self, tenant_id: str
    ) -> ExpenseSubCategoryExportReport:
        try:
            tenant = validate_tenant_id(tenant_id)
        except AccountingStoreError:
            raise CategorySummaryExportError(
                "INVALID_TENANT_ID", repr(tenant_id)
            ) from None
        tree = ensure_tree(tenant, self._root)
        base = (
            self._export_dir
            if self._export_dir is not None
            else tree.state_dir(FolderState.EXPORT_COMPTA)
        )
        frais_dir = base / DocumentCategory.FRAIS_GENERAUX.value
        frais_dir.mkdir(parents=True, exist_ok=True)

        rows = self._store.list_category_lines(
            tenant, DocumentCategory.FRAIS_GENERAUX
        )
        grouped: dict[ExpenseSubCategory, list[tuple[Any, ...]]] = {
            subcategory: [] for subcategory in ExpenseSubCategory
        }
        unclassified: list[dict[str, Any]] = []
        for row in rows:
            record = _summary_record(row)
            subcategory = _infer_expense_subcategory(row)
            if subcategory is None:
                unclassified.append(row)
                continue
            grouped[subcategory].append(
                _expense_row_values(subcategory, row, record)
            )

        subtable_paths: dict[ExpenseSubCategory, Path] = {}
        subfolders: dict[ExpenseSubCategory, tuple[Path, Path]] = {}
        counts: dict[ExpenseSubCategory, int] = {}
        for subcategory, values in grouped.items():
            subfolder_03 = tree.expense_subcategory_dir(
                FolderState.VALIDE_PRET_FEC, subcategory
            )
            subfolder_04 = tree.expense_subcategory_dir(
                FolderState.EXPORT_COMPTA, subcategory
            )
            subfolder_03.mkdir(parents=True, exist_ok=True)
            subfolder_04.mkdir(parents=True, exist_ok=True)
            target_dir = frais_dir / subcategory.value
            target_dir.mkdir(parents=True, exist_ok=True)
            path = target_dir / EXPENSE_SUBTABLE_FILENAME.format(
                subcategory=subcategory.value
            )
            _write_p17_workbook(
                path,
                title=f"TABLEAU_{subcategory.value}",
                headers=EXPENSE_SUBCATEGORY_HEADERS[subcategory],
                rows=values,
            )
            subtable_paths[subcategory] = path
            subfolders[subcategory] = (subfolder_03, subfolder_04)
            counts[subcategory] = len(values)

        summary_rows: list[tuple[Any, ...]] = []
        for subcategory in ExpenseSubCategory:
            values = grouped[subcategory]
            if subcategory is ExpenseSubCategory.HOTELLERIE:
                ht = sum(
                    (
                        value[3] + value[4]
                        for value in values
                        if isinstance(value[3], Decimal)
                        and isinstance(value[4], Decimal)
                    ),
                    Decimal("0.00"),
                )
                tva = sum(
                    (
                        value[5] + value[6]
                        for value in values
                        if isinstance(value[5], Decimal)
                        and isinstance(value[6], Decimal)
                    ),
                    Decimal("0.00"),
                )
                ttc_index = 7
            else:
                ht = sum(
                    (
                        value[4]
                        for value in values
                        if isinstance(value[4], Decimal)
                    ),
                    Decimal("0.00"),
                )
                tva = sum(
                    (
                        value[5]
                        for value in values
                        if isinstance(value[5], Decimal)
                    ),
                    Decimal("0.00"),
                )
                ttc_index = 6
            ttc = sum(
                (
                    value[ttc_index]
                    for value in values
                    if isinstance(value[ttc_index], Decimal)
                ),
                Decimal("0.00"),
            )
            summary_rows.append(
                (
                    subcategory.value,
                    len(values),
                    ht.quantize(_CENT),
                    tva.quantize(_CENT),
                    ttc.quantize(_CENT),
                )
            )
        if unclassified:
            summary_rows.append(
                (NON_CLASSIFIED_MARKER, len(unclassified), Decimal("0.00"), Decimal("0.00"), Decimal("0.00"))
            )
        total_ht = sum(
            (row[2] for row in summary_rows if isinstance(row[2], Decimal)),
            Decimal("0.00"),
        )
        total_tva = sum(
            (row[3] for row in summary_rows if isinstance(row[3], Decimal)),
            Decimal("0.00"),
        )
        total_ttc = sum(
            (row[4] for row in summary_rows if isinstance(row[4], Decimal)),
            Decimal("0.00"),
        )
        total_row = (
            CATEGORY_SUMMARY_TOTAL_LABEL,
            sum(row[1] for row in summary_rows),
            total_ht.quantize(_CENT),
            total_tva.quantize(_CENT),
            total_ttc.quantize(_CENT),
        )
        global_path = frais_dir / EXPENSE_SYNTHESIS_FILENAME
        _write_p17_workbook(
            global_path,
            title="SYNTHESE_FRAIS_GENERAUX",
            headers=EXPENSE_SYNTHESIS_HEADERS,
            rows=summary_rows,
            total_row=total_row,
        )
        return ExpenseSubCategoryExportReport(
            global_path=global_path,
            subtable_paths=subtable_paths,
            subfolders=subfolders,
            subcategory_counts=counts,
        )


def export_expense_subcategories(
    tenant_id: str,
    *,
    root: str | os.PathLike[str] | None = None,
    export_dir: str | os.PathLike[str] | None = None,
    store: SqliteAccountingStore | None = None,
) -> ExpenseSubCategoryExportReport:
    """Convenience wrapper around :class:`ExpenseSubCategoryExporter`."""
    exporter = ExpenseSubCategoryExporter(
        store if store is not None else get_shared_sqlite_store(),
        root=root,
        export_dir=export_dir,
    )
    return exporter.export(tenant_id)

@dataclass(frozen=True)
class TvaDuplicate:
    """Explicit duplicate detected on piece number + supplier + amount."""

    piece_number: str
    supplier: str
    amount: Decimal
    line_ids: tuple[str, ...]


@dataclass(frozen=True)
class FiscalYearDecision:
    """Resolved fiscal year / deferred month for one accounting line."""

    mode: FiscalYearMode
    target_year: int | None
    deferred_month: str | None
    effective_date: date_cls | None


@dataclass(frozen=True)
class FiscalTotals:
    """Deduplicated HT/TVA/TTC totals of one fiscal year."""

    year: int
    pieces: int
    ht: Decimal
    tva: Decimal
    ttc: Decimal


@dataclass(frozen=True)
class MasterTableExportReport:
    """Observable outcome of the two master-table exports."""

    achats_path: Path
    banque_tva_path: Path
    row_count: int
    duplicates: tuple[TvaDuplicate, ...]
    fiscal_totals: dict[int, FiscalTotals]

    @property
    def pieces_path(self) -> Path:
        """P23 unified pieces workbook (legacy ``achats_path`` alias)."""
        return self.achats_path


def _line_identifier(row: dict[str, Any], index: int) -> str:
    for key in ("line_id", "invoice_id", "id"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return f"ROW-{index}"


def _dedup_key(row: dict[str, Any], record: dict[str, Any]) -> tuple[str, str, Decimal] | None:
    piece = _piece_number_from_row(row) or _piece_reference(
        row, row.get("invoice_id") or row.get("line_id") or ""
    )
    supplier = _fold(row.get("counterparty") or row.get("supplier") or "")
    amount = record["ttc"]
    if not piece or not supplier or amount is None:
        return None
    return (piece.casefold(), supplier, amount)


def compute_tva_dedup(
    rows: list[dict[str, Any]],
) -> tuple[list[TvaDedupStatus], tuple[TvaDuplicate, ...]]:
    """Return one anti-duplicate status per row plus explicit duplicates.

    The anti-duplicate key is exactly ``(numéro de pièce, fournisseur,
    montant TTC)``. The first occurrence is ``TVA_UNIQUE`` and is the only one
    counted in the TVA recap; every following occurrence is
    ``DOUBLON_TVA_EXCLU``. Rows that cannot be keyed are conservatively marked
    ``TVA_NON_VERIFIABLE`` but remain count-once (no proof of duplication).
    """
    statuses: list[TvaDedupStatus] = []
    first_index: dict[tuple[str, str, Decimal], int] = {}
    occurrences: dict[tuple[str, str, Decimal], list[str]] = {}
    for index, row in enumerate(rows):
        record = _summary_record(row)
        key = _dedup_key(row, record)
        line_id = _line_identifier(row, index)
        stored = str(row.get("tva_dedup_status") or "").strip().upper()
        if stored == TvaDedupStatus.DUPLICATE_EXCLUDED.value:
            statuses.append(TvaDedupStatus.DUPLICATE_EXCLUDED)
            if key is not None:
                occurrences.setdefault(key, []).append(line_id)
            continue
        if key is None:
            statuses.append(TvaDedupStatus.NON_VERIFIABLE)
            continue
        if key in first_index:
            statuses.append(TvaDedupStatus.DUPLICATE_EXCLUDED)
            occurrences[key].append(line_id)
            continue
        first_index[key] = index
        occurrences.setdefault(key, []).append(line_id)
        statuses.append(TvaDedupStatus.UNIQUE)
    duplicates = tuple(
        TvaDuplicate(
            piece_number=key[0],
            supplier=key[1],
            amount=key[2],
            line_ids=tuple(ids),
        )
        for key, ids in occurrences.items()
        if len(ids) > 1
    )
    return statuses, duplicates


def _coerce_month(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        text = text.zfill(2)
    return text if text in DEFERRED_MONTHS else None


def _safe_date(year: int, month: int, day: int) -> date_cls | None:
    for candidate_day in (day, 28, 1):
        try:
            return date_cls(year, month, candidate_day)
        except ValueError:
            continue
    return None


def resolve_fiscal_year(row: dict[str, Any]) -> FiscalYearDecision:
    """Apply ``fiscal_year_mode`` / target / deferred month to one line."""
    try:
        mode = FiscalYearMode(str(row.get("fiscal_year_mode") or "ACTUEL").strip())
    except ValueError:
        mode = FiscalYearMode.ACTUEL
    piece_date = _parse_iso_date(row.get("operation_date"))
    target_text = str(row.get("fiscal_year_target") or "").strip()
    target_year: int | None = None
    if re.fullmatch(r"\d{4}", target_text):
        target_year = int(target_text)
    elif piece_date is not None:
        offset = {
            FiscalYearMode.PRECEDENT: -1,
            FiscalYearMode.ACTUEL: 0,
            FiscalYearMode.SUIVANT: 1,
        }[mode]
        target_year = piece_date.year + offset
    deferred = _coerce_month(row.get("deferred_month_override"))
    effective: date_cls | None = piece_date
    if target_year is not None:
        month = int(deferred) if deferred else (piece_date.month if piece_date else 1)
        day = piece_date.day if piece_date else 1
        effective = _safe_date(target_year, month, day)
    return FiscalYearDecision(
        mode=mode,
        target_year=target_year,
        deferred_month=deferred,
        effective_date=effective,
    )


def _status_value(status: TvaDedupStatus | str) -> str:
    return getattr(status, "value", str(status))


def _is_duplicate_status(status: TvaDedupStatus | str) -> bool:
    return _status_value(status) == TvaDedupStatus.DUPLICATE_EXCLUDED.value


def compute_fiscal_totals(
    rows: list[dict[str, Any]],
    statuses: list[TvaDedupStatus] | None = None,
) -> dict[int, FiscalTotals]:
    """Deduplicated TVA/charges totals isolated per fiscal year."""
    resolved_statuses = (
        statuses
        if statuses is not None
        else compute_tva_dedup(rows)[0]
    )
    buckets: dict[int, dict[str, Any]] = {}
    for row, status in zip(rows, resolved_statuses):
        if _is_duplicate_status(status):
            continue
        decision = resolve_fiscal_year(row)
        if decision.target_year is None:
            continue
        record = _summary_record(row)
        bucket = buckets.setdefault(
            decision.target_year,
            {
                "pieces": 0,
                "ht": Decimal("0.00"),
                "tva": Decimal("0.00"),
                "ttc": Decimal("0.00"),
            },
        )
        bucket["pieces"] += 1
        bucket["ht"] += record["ht"] or Decimal("0.00")
        bucket["tva"] += record["tva"] or Decimal("0.00")
        bucket["ttc"] += record["ttc"] or Decimal("0.00")
    return {
        year: FiscalTotals(
            year=year,
            pieces=data["pieces"],
            ht=data["ht"].quantize(_CENT),
            tva=data["tva"].quantize(_CENT),
            ttc=data["ttc"].quantize(_CENT),
        )
        for year, data in sorted(buckets.items())
    }


_PAID_RECONCILIATION_STATUSES = frozenset(
    {"PAYEE_RAPPROCHEE", "PAYE", "SOLDEE"}
)


def _all_master_rows(store: Any, tenant_id: str) -> list[dict[str, Any]]:
    """Every non-rejected line available to the P23 master workbooks."""
    list_master = getattr(store, "list_master_lines", None)
    if callable(list_master):
        return list(list_master(tenant_id))
    list_lines = getattr(store, "list_lines", None)
    if callable(list_lines):
        try:
            rows, _ = list_lines(tenant_id, status="ALL", limit=-1)
        except TypeError:
            rows = None
        if rows is not None:
            return list(rows)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for category in DocumentCategory:
        for row in store.list_category_lines(tenant_id, category):
            identifier = _line_identifier(row, len(rows))
            if identifier in seen:
                continue
            seen.add(identifier)
            rows.append(row)
    return rows


def _is_bank_operation(row: dict[str, Any]) -> bool:
    document_type = str(row.get("document_type") or "").strip().upper()
    line_id = str(row.get("line_id") or "").strip().upper()
    debit = _decimal(row.get("debit_amount"))
    credit = _decimal(row.get("credit_amount"))
    return (
        document_type in {"OFX", "QFX"}
        or line_id.startswith("OFX-")
        or bool(debit)
        or bool(credit)
    )


def _is_sales_row(row: dict[str, Any]) -> bool:
    return str(row.get("category") or "") == DocumentCategory.VENTES_CLIENTS.value


def _master_rows(store: Any, tenant_id: str) -> list[dict[str, Any]]:
    """Purchase/fee rows used for the VAT anti-duplicate and fiscal recap."""
    return [
        row
        for row in _all_master_rows(store, tenant_id)
        if not _is_sales_row(row)
        and not _is_bank_operation(row)
        and str(row.get("document_type") or "").strip().upper()
        not in NON_ACCOUNTABLE_DOCUMENT_TYPES
    ]


def _piece_sort_key(row: dict[str, Any]) -> tuple[Any, int, str, str]:
    piece_date = _parse_iso_date(row.get("operation_date")) or date_cls.max
    flux_priority = 0 if _is_sales_row(row) else 1
    number = _piece_number_from_row(row) or _summary_record(row)["invoice_id"]
    return (
        piece_date,
        flux_priority,
        str(number or "").casefold(),
        _fold(_summary_record(row)["counterparty"]),
    )


def _piece_rows(store: Any, tenant_id: str) -> list[dict[str, Any]]:
    """All document pieces (customer sales + supplier purchases/fees)."""
    pieces: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in _all_master_rows(store, tenant_id):
        if _is_bank_operation(row):
            continue
        if (
            str(row.get("document_type") or "").strip().upper()
            in NON_ACCOUNTABLE_DOCUMENT_TYPES
        ):
            continue
        if not any(
            row.get(key) not in (None, "")
            for key in (
                "amount_ht",
                "amount_tva",
                "amount_ttc",
                "invoice_id",
                "piece_pdf",
                "invoice_url",
            )
        ):
            continue
        identifier = _line_identifier(row, len(pieces))
        if identifier in seen:
            continue
        seen.add(identifier)
        pieces.append(row)
    return sorted(pieces, key=_piece_sort_key)


def _bank_operations(store: Any, tenant_id: str) -> list[dict[str, Any]]:
    return sorted(
        [
            row
            for row in _all_master_rows(store, tenant_id)
            if _is_bank_operation(row)
        ],
        key=lambda row: (
            _parse_iso_date(row.get("operation_date")) or date_cls.max,
            str(row.get("line_id") or ""),
        ),
    )


def _movement_amount(row: dict[str, Any]) -> Decimal:
    debit = _decimal(row.get("debit_amount")) or Decimal("0.00")
    credit = _decimal(row.get("credit_amount")) or Decimal("0.00")
    return (credit if credit else debit).quantize(_CENT)


def _reference_keys(*values: Any) -> set[str]:
    keys: set[str] = set()
    for value in values:
        text = re.sub(r"\s+", " ", _fold(value)).strip()
        if not text:
            continue
        keys.add(text)
        for pattern in (
            r"remise(?:\s+encaissement)?\s*(?:n[°o]?\s*)?([a-z0-9]+)",
            r"virement\s*(?:n[°o]?\s*)?([a-z0-9]+)",
            r"\bvir\b\s*(?:n[°o]?\s*)?([a-z0-9]+)",
        ):
            for match in re.finditer(pattern, text):
                token = match.group(1)
                if token in {"de", "des", "du", "encaissement"}:
                    continue
                keys.add(token)
                keys.add(f"remise {token}")
                keys.add(f"virement {token}")
    return keys


_GENERIC_REFERENCE_KEYS = frozenset(
    {"virement", "remise", "vir", "reglement_valide", "reglement"}
)


def _is_specific_reference(key: str) -> bool:
    return len(key) >= 4 and key not in _GENERIC_REFERENCE_KEYS


def _subset_for_amount(
    documents: list[dict[str, Any]], target: Decimal
) -> list[dict[str, Any]] | None:
    """Find an exact TTC subset matching one bank movement (bounded DFS)."""
    if target <= Decimal("0.00") or not documents:
        return None
    items = sorted(
        (
            (document, (_summary_record(document)["ttc"] or Decimal("0.00")))
            for document in documents
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    visited: set[tuple[int, str]] = set()

    def search(
        index: int, remaining: Decimal
    ) -> list[dict[str, Any]] | None:
        if abs(remaining) < _CENT:
            return []
        if index >= len(items) or remaining < Decimal("0.00"):
            return None
        if len(visited) > 50_000:
            return None
        marker = (index, str(remaining))
        if marker in visited:
            return None
        visited.add(marker)
        document, amount = items[index]
        if amount <= remaining:
            tail = search(index + 1, (remaining - amount).quantize(_CENT))
            if tail is not None:
                return [document, *tail]
        return search(index + 1, remaining)

    return search(0, target.quantize(_CENT))


def _match_bank_documents(
    bank_row: dict[str, Any], documents: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return document pieces lettered against one Caisse d'Épargne line."""
    if str(bank_row.get("payment_method") or "").strip().upper() in {
        "FRAIS_BANCAIRES",
    }:
        return []
    bank_invoice = _fold(_text_field(bank_row, "invoice_id"))
    bank_keys = _reference_keys(
        _text_field(bank_row, "fitid"),
        _text_field(bank_row, "payment_reference"),
        _text_field(bank_row, "remittance_reference"),
        _text_field(bank_row, "label"),
    )
    bank_label = re.sub(
        r"\s+", " ", _fold(_text_field(bank_row, "label"))
    ).strip()
    memory = _learned_memory()
    bank_group = memory.match_reference(
        " ".join(
            (
                _text_field(bank_row, "fitid"),
                _text_field(bank_row, "payment_reference"),
                _text_field(bank_row, "remittance_reference"),
                _text_field(bank_row, "label"),
            )
        )
    )
    matched: list[dict[str, Any]] = []
    seen: set[str] = set()
    for document in documents:
        identifier = _line_identifier(document, len(seen))
        document_invoice = _fold(
            _text_field(document, "invoice_id", "origin_document_number")
        )
        document_keys = _reference_keys(
            _text_field(
                document,
                "reconciliation_reference",
                "fitid",
                "payment_reference",
                "remittance_reference",
            )
        )
        hit = bool(bank_invoice and document_invoice == bank_invoice)
        hit = hit or bool(bank_keys & document_keys)
        if not hit and document_keys:
            hit = any(
                _is_specific_reference(key) and key in bank_label
                for key in document_keys
            )
        if not hit:
            number = _fold(_piece_number_from_row(document))
            hit = bool(number and len(number) >= 4 and number in bank_label)
        if not hit and bank_group:
            document_group = memory.canonical_name(
                _text_field(document, "counterparty", "supplier")
            )
            hit = _fold(document_group) == _fold(bank_group)
        if hit and identifier not in seen:
            seen.add(identifier)
            matched.append(document)
    return matched


def _reconciliation_label(row: dict[str, Any]) -> str:
    status = str(row.get("reconciliation_status") or "").strip().upper()
    reference = _text_field(row, "reconciliation_reference")
    method = _text_field(row, "payment_method")
    parts: list[str] = []
    if status:
        parts.append(status)
    if reference and reference.upper() != status:
        parts.append(reference)
    if not parts and method:
        parts.append(method)
    return " — ".join(dict.fromkeys(parts)) or "NON RAPPROCHE"


def _solde_du(row: dict[str, Any], record: dict[str, Any]) -> Decimal:
    ttc = record["ttc"] or Decimal("0.00")
    explicit = _decimal_field(row, "balance_due")
    if explicit is not None:
        return explicit
    status = str(row.get("reconciliation_status") or "").strip().upper()
    if status in _PAID_RECONCILIATION_STATUSES:
        return Decimal("0.00")
    reconciled = _decimal_field(row, "reconciliation_amount")
    if reconciled is not None:
        return (ttc - reconciled).quantize(_CENT)
    return ttc.quantize(_CENT)


def _pieces_table_rows(
    store: Any, tenant_id: str, workbook_path: Path
) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for row in _piece_rows(store, tenant_id):
        record = _summary_record(row)
        rows.append(
            (
                _date_cell(record["date"]),
                _payment_date_cell(row),
                (
                    "VENTE_CLIENT"
                    if _is_sales_row(row)
                    else "ACHAT_FOURNISSEUR"
                ),
                record["counterparty"],
                record["invoice_id"],
                record["ht"] or Decimal("0.00"),
                record["tva"] or Decimal("0.00"),
                record["ttc"] or Decimal("0.00"),
                str(row.get("raw_status") or row.get("status") or ""),
                _piece_link_for_row(workbook_path, row),
                _reconciliation_label(row),
                _solde_du(row, record),
            )
        )
    return rows


def _bank_counterparty_display(
    row: dict[str, Any], matched: list[dict[str, Any]]
) -> str:
    """Identify the third party of one bank line (P24 accountant column)."""
    memory = _learned_memory()
    raw = _text_field(row, "counterparty", "supplier")
    nature = ""
    name = ""
    if matched:
        names: list[str] = []
        natures: list[str] = []
        for document in matched:
            record = _summary_record(document)
            label = str(record["counterparty"] or "").strip()
            if label:
                names.append(label)
            natures.append("CLIENT" if _is_sales_row(document) else "FOURNISSEUR")
        name = memory.canonical_name(names[0]) if names else raw
        nature = "CLIENT" if "CLIENT" in natures else "FOURNISSEUR"
    label_text = " ".join(
        (
            _text_field(row, "label"),
            _text_field(row, "payment_reference"),
            _text_field(row, "remittance_reference"),
        )
    )
    if not name:
        name = (
            memory.match_reference(label_text)
            or memory.match_reference(raw)
            or memory.canonical_name(raw)
        )
    folded = _fold(f"{raw} {label_text}")
    if not name or _fold(name) == _fold(label_text):
        if "yucel" in folded:
            name, nature = "CARROSSERIE_YUCEL", nature or "CLIENT"
        elif "dirigeant" in folded or "455" in folded:
            name, nature = "DIRIGEANT", "DIRIGEANT"
        elif any(
            token in folded
            for token in ("caisse d'epargne", "commission", "abo ce", "frais bancaire")
        ):
            name, nature = "CAISSE D'EPARGNE", "BANQUE"
        else:
            name = raw or name
            nature = nature or ("FOURNISSEUR" if name else "")
    canonical = memory.canonical_name(name) or name
    if not canonical:
        return "NON IDENTIFIE"
    return f"{canonical} [{nature}]" if nature else canonical


def _is_remise_encaissement(label: Any) -> bool:
    return "REMISE ENCAISSEMENT" in _fold(label).upper()


def _split_remise_rows(
    row: dict[str, Any],
    matched: list[dict[str, Any]],
    movement: Decimal,
    workbook_path: Path,
    start_index: int,
) -> tuple[list[tuple[Any, ...]], dict[tuple[int, int], str]]:
    """Explode one multi-client LCR remise into one row per customer invoice."""
    label = _text_field(row, "label")
    rows: list[tuple[Any, ...]] = []
    hyperlinks: dict[tuple[int, int], str] = {}
    total = Decimal("0.00")
    for offset, document in enumerate(matched):
        record = _summary_record(document)
        ttc = (record["ttc"] or Decimal("0.00")).quantize(_CENT)
        total += ttc
        number = _piece_number_from_row(document) or record["invoice_id"]
        client = str(record["counterparty"] or "").strip() or "CLIENT"
        rows.append(
            (
                _date_cell(row.get("operation_date")),
                client,
                "LCR_ENCAISSEMENT_LOT",
                f"{label} (Lot {movement} €) — Facture {number}",
                Decimal("0.00"),
                ttc,
                str(number),
                (record["tva"] or Decimal("0.00")).quantize(_CENT),
                Decimal("0.00"),
            )
        )
        link = _piece_link_for_row(workbook_path, document)
        if link:
            hyperlinks[(start_index + offset, 7)] = link
    residual = (movement - total).quantize(_CENT)
    if residual != 0:
        rows.append(
            (
                _date_cell(row.get("operation_date")),
                "ECART REMISE",
                "LCR_ENCAISSEMENT_LOT",
                f"{label} (Lot {movement} €) — Écart de lettrage",
                Decimal("0.00"),
                residual,
                "ECART",
                Decimal("0.00"),
                Decimal("0.00"),
            )
        )
    return rows, hyperlinks


def _bank_mirror_rows(
    store: Any, tenant_id: str, workbook_path: Path
) -> tuple[list[tuple[Any, ...]], dict[tuple[int, int], str]]:
    documents = _piece_rows(store, tenant_id)
    rows: list[tuple[Any, ...]] = []
    hyperlinks: dict[tuple[int, int], str] = {}
    row_index = 2
    for row in _bank_operations(store, tenant_id):
        movement = _movement_amount(row)
        matched = _match_bank_documents(row, documents)
        if matched:
            candidate_total = sum(
                (
                    _summary_record(document)["ttc"] or Decimal("0.00")
                    for document in matched
                ),
                Decimal("0.00"),
            )
            if candidate_total != movement:
                subset = _subset_for_amount(matched, movement)
                if subset is not None:
                    matched = subset
        label = _text_field(row, "label")
        if not matched and _is_remise_encaissement(label):
            candidates = [
                document
                for document in documents
                if _is_sales_row(document)
                and str(document.get("reconciliation_status") or "").upper()
                not in _PAID_RECONCILIATION_STATUSES
            ]
            matched = _subset_for_amount(candidates, movement) or []
        if _is_remise_encaissement(label) and len(matched) > 1:
            split_rows, split_links = _split_remise_rows(
                row, matched, movement, workbook_path, row_index
            )
            rows.extend(split_rows)
            hyperlinks.update(split_links)
            row_index += len(split_rows)
            continue
        numbers: list[str] = []
        links: list[str] = []
        matched_total = Decimal("0.00")
        matched_tva = Decimal("0.00")
        for document in matched:
            record = _summary_record(document)
            number = _piece_number_from_row(document) or record["invoice_id"]
            if number:
                numbers.append(str(number))
            matched_total += record["ttc"] or Decimal("0.00")
            matched_tva += record["tva"] or Decimal("0.00")
            link = _piece_link_for_row(workbook_path, document)
            if link:
                links.append(link)
        display = " + ".join(dict.fromkeys(numbers)) or "NON RAPPROCHEE"
        debit = _decimal(row.get("debit_amount")) or Decimal("0.00")
        credit = _decimal(row.get("credit_amount")) or Decimal("0.00")
        tva = (
            matched_tva
            if matched
            else _decimal_field(row, "amount_tva", "vat_amount")
            or Decimal("0.00")
        )
        rows.append(
            (
                _date_cell(row.get("operation_date")),
                _bank_counterparty_display(row, matched),
                _text_field(row, "payment_method", "document_type", "category")
                or ("DEBIT" if debit else "CREDIT"),
                label,
                debit,
                credit,
                display,
                tva.quantize(_CENT),
                (movement - matched_total).quantize(_CENT),
            )
        )
        if links:
            hyperlinks[(row_index, 7)] = links[0]
        row_index += 1
    return rows, hyperlinks


class MasterTableExporter:
    """Build the unified P23 pieces table and the bank/TVA mirror."""

    def __init__(
        self,
        store: Any,
        *,
        root: str | os.PathLike[str] | None = None,
        export_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self._store = store
        self._root = root
        self._export_dir = Path(export_dir) if export_dir is not None else None

    def export(self, tenant_id: str) -> MasterTableExportReport:
        try:
            tenant = validate_tenant_id(tenant_id)
        except AccountingStoreError:
            raise CategorySummaryExportError(
                "INVALID_TENANT_ID", repr(tenant_id)
            ) from None
        tree = ensure_tree(tenant, self._root)
        output_dir = (
            self._export_dir
            if self._export_dir is not None
            else tree.state_dir(FolderState.EXPORT_COMPTA) / tenant
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        pieces_path = output_dir / MASTER_PIECES_FILENAME
        banque_path = output_dir / MASTER_BANQUE_TVA_FILENAME
        rows = _master_rows(self._store, tenant)
        statuses, duplicates = compute_tva_dedup(rows)
        fiscal_totals = compute_fiscal_totals(rows, statuses)

        pieces_rows = _pieces_table_rows(
            self._store, tenant, pieces_path
        )
        banque_rows, bank_hyperlinks = _bank_mirror_rows(
            self._store, tenant, banque_path
        )

        _write_p17_workbook(
            pieces_path,
            title="TABLEAU_MERE_PIECES_COMPTABLES",
            headers=MASTER_PIECES_HEADERS,
            rows=pieces_rows,
        )
        _write_p17_workbook(
            banque_path,
            title="TABLEAU_MERE_BANQUE_TVA",
            headers=MASTER_BANQUE_TVA_HEADERS,
            rows=banque_rows,
            hyperlinks=bank_hyperlinks,
        )
        legacy_path = output_dir / LEGACY_MASTER_ACHATS_FILENAME
        if legacy_path.exists():
            try:
                legacy_path.unlink()
            except OSError:
                pass
        workbook = load_workbook(banque_path)
        recap = workbook.create_sheet("RECAP_TVA")
        recap.sheet_view.showGridLines = False
        for column, name in enumerate(MASTER_TVA_RECAP_HEADERS, start=1):
            recap.cell(row=1, column=column, value=name).font = Font(
                bold=True, color="FFFFFF"
            )
            recap.cell(row=1, column=column).fill = PatternFill(
                "solid", fgColor=_HEADER_FILL
            )
        for offset, totals in enumerate(fiscal_totals.values(), start=2):
            values = (
                totals.year,
                totals.pieces,
                totals.ht,
                totals.tva,
                totals.ttc,
            )
            for column, value in enumerate(values, start=1):
                cell = recap.cell(row=offset, column=column)
                cell.value = (
                    float(value) if isinstance(value, Decimal) else value
                )
                if isinstance(value, Decimal):
                    cell.number_format = "#,##0.00"
        for column in range(1, len(MASTER_TVA_RECAP_HEADERS) + 1):
            recap.column_dimensions[
                get_column_letter(column)
            ].width = 18
        _atomic_save_workbook(workbook, banque_path)

        return MasterTableExportReport(
            achats_path=pieces_path,
            banque_tva_path=banque_path,
            row_count=len(pieces_rows),
            duplicates=duplicates,
            fiscal_totals=fiscal_totals,
        )


def export_master_tables(
    tenant_id: str,
    *,
    root: str | os.PathLike[str] | None = None,
    export_dir: str | os.PathLike[str] | None = None,
    store: SqliteAccountingStore | None = None,
) -> MasterTableExportReport:
    """Convenience wrapper around :class:`MasterTableExporter`."""
    exporter = MasterTableExporter(
        store if store is not None else get_shared_sqlite_store(),
        root=root,
        export_dir=export_dir,
    )
    return exporter.export(tenant_id)


# ---------------------------------------------------------------------------
# P17.1 - Manager personal-expense claims (Compte Courant 455000_DIRIGEANT)
# ---------------------------------------------------------------------------

PERSONAL_EXPENSE_NOTE_FILENAME = "NOTE_DE_FRAIS_DIRIGEANT_{month}.xlsx"
PERSONAL_EXPENSE_NOTE_HEADERS: tuple[str, ...] = (
    "Date Pièce",
    "Fournisseur",
    "N° Pièce",
    "Catégorie",
    "Sous-catégorie",
    "HT",
    "TVA Déductible",
    "TTC",
    "Source de Paiement",
    "Compte de Contrepartie",
    "Lien Justificatif",
    PIECE_LINK_HEADER,
)


@dataclass(frozen=True)
class PersonalExpenseNoteReport:
    """Monthly claims paid by the manager and booked through 455000."""

    paths: dict[str, Path]
    piece_counts: dict[str, int]
    total_ht: dict[str, Decimal]
    total_tva: dict[str, Decimal]
    total_ttc: dict[str, Decimal]

    @property
    def output_path(self) -> Path:
        """Latest generated workbook (single month for explicit exports)."""
        return list(self.paths.values())[-1]

    @property
    def month(self) -> str:
        return list(self.paths.keys())[-1] if self.paths else ""

    @property
    def piece_count(self) -> int:
        return sum(self.piece_counts.values())

    @property
    def total_net(self) -> Decimal:
        """Total amount to reimburse to the manager (TTC)."""
        return sum(self.total_ttc.values(), Decimal("0.00")).quantize(_CENT)

    @property
    def total_to_reimburse(self) -> Decimal:
        return self.total_net


def _normalise_month(value: Any) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date_cls):
        return f"{value.year:04d}-{value.month:02d}"
    text = str(value or "").strip()
    if not text:
        raise CategorySummaryExportError("INVALID_MONTH", repr(value))
    patterns = (
        (r"^(\d{4})[-_/.](\d{1,2})$", "ym"),
        (r"^(\d{4})(\d{2})$", "ym"),
        (r"^(\d{1,2})[-_/.](\d{4})$", "my"),
        (r"^(\d{4})[-_/.](\d{1,2})[-_/.]\d{1,2}$", "ymd"),
        (r"^(\d{1,2})[-_/.](\d{1,2})[-_/.](\d{4})$", "dmy"),
    )
    for pattern, order in patterns:
        match = re.match(pattern, text)
        if match is None:
            continue
        groups = match.groups()
        if order == "ymd":
            year, month = int(groups[0]), int(groups[1])
        elif order == "dmy":
            year, month = int(groups[2]), int(groups[1])
        elif order == "my":
            year, month = int(groups[1]), int(groups[0])
        else:
            year, month = int(groups[0]), int(groups[1])
        if not 1 <= month <= 12:
            break
        return f"{year:04d}-{month:02d}"
    raise CategorySummaryExportError("INVALID_MONTH", repr(value))


def _row_month(row: dict[str, Any]) -> str | None:
    value = row.get("operation_date")
    if isinstance(value, datetime):
        return f"{value.year:04d}-{value.month:02d}"
    if isinstance(value, date_cls):
        return f"{value.year:04d}-{value.month:02d}"
    parsed = _parse_iso_date(value)
    if parsed is not None:
        return f"{parsed.year:04d}-{parsed.month:02d}"
    text = str(value or "").strip()
    if len(text) >= 7 and re.match(r"^\d{4}[-_/.]\d{1,2}", text):
        try:
            return _normalise_month(text[:7])
        except CategorySummaryExportError:
            return None
    return None


def _personal_note_row(row: dict[str, Any]) -> tuple[Any, ...]:
    record = _summary_record(row)
    subcategory = _infer_expense_subcategory(row)
    if subcategory is ExpenseSubCategory.HOTELLERIE:
        deductible, _non_deductible, _acc, _meals = _hotel_vat_from_row(
            row, record
        )
    else:
        deductible = record["tva"] or Decimal("0.00")
    return (
        _date_cell(record["date"]),
        record["counterparty"],
        _piece_number_from_row(row)
        or _piece_reference(
            row, row.get("invoice_id") or row.get("line_id") or ""
        ),
        str(row.get("category") or ""),
        subcategory.value if subcategory is not None else NON_CLASSIFIED_MARKER,
        record["ht"] or Decimal("0.00"),
        deductible,
        record["ttc"] or Decimal("0.00"),
        payment_source_of(row),
        counterparty_account_of(row) or DIRIGEANT_COUNTERPART_ACCOUNT,
        record["reference"],
    )


class PersonalExpenseNoteExporter:
    """Generate the monthly ``NOTE_DE_FRAIS_DIRIGEANT_<mois>.xlsx`` workbook."""

    def __init__(
        self,
        store: Any,
        *,
        export_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self._store = store
        self._export_dir = (
            Path(export_dir) if export_dir is not None else DEFAULT_EXPORT_DIR
        )

    def export(
        self, tenant_id: str, month: str | date_cls | None = None
    ) -> PersonalExpenseNoteReport:
        tenant = validate_tenant_id(tenant_id)
        selected_month = _normalise_month(month) if month is not None else None
        rows = [
            row
            for row in _master_rows(self._store, tenant)
            if is_personal_expense(row)
        ]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            row_month = _row_month(row)
            if row_month is None:
                if selected_month is not None:
                    continue
                row_month = "INCONNU"
            if selected_month is not None and row_month != selected_month:
                continue
            grouped.setdefault(row_month, []).append(row)
        if selected_month is not None:
            grouped.setdefault(selected_month, [])

        output_dir = self._export_dir / tenant
        output_dir.mkdir(parents=True, exist_ok=True)
        paths: dict[str, Path] = {}
        counts: dict[str, int] = {}
        total_ht: dict[str, Decimal] = {}
        total_tva: dict[str, Decimal] = {}
        total_ttc: dict[str, Decimal] = {}
        for month_key in sorted(grouped):
            month_rows = sorted(
                grouped[month_key],
                key=lambda item: (
                    str(item.get("operation_date") or ""),
                    str(item.get("line_id") or ""),
                ),
            )
            path = output_dir / PERSONAL_EXPENSE_NOTE_FILENAME.format(
                month=month_key
            )
            values = [
                _personal_note_row(row)
                + (_piece_link_for_row(path, row),)
                for row in month_rows
            ]
            ht = sum(
                (value[5] for value in values if isinstance(value[5], Decimal)),
                Decimal("0.00"),
            ).quantize(_CENT)
            tva = sum(
                (value[6] for value in values if isinstance(value[6], Decimal)),
                Decimal("0.00"),
            ).quantize(_CENT)
            ttc = sum(
                (value[7] for value in values if isinstance(value[7], Decimal)),
                Decimal("0.00"),
            ).quantize(_CENT)
            total_row = (
                "TOTAL NET A REMBOURSER",
                "",
                "",
                "",
                "",
                ht,
                tva,
                ttc,
                PaymentSource.COMPTE_PERSO_DIRIGEANT.value,
                DIRIGEANT_COUNTERPART_ACCOUNT,
                "",
                "",
            )
            _write_p17_workbook(
                path,
                title=f"NOTE_DE_FRAIS_{month_key}",
                headers=PERSONAL_EXPENSE_NOTE_HEADERS,
                rows=values,
                total_row=total_row,
            )
            paths[month_key] = path
            counts[month_key] = len(values)
            total_ht[month_key] = ht
            total_tva[month_key] = tva
            total_ttc[month_key] = ttc
        return PersonalExpenseNoteReport(
            paths=paths,
            piece_counts=counts,
            total_ht=total_ht,
            total_tva=total_tva,
            total_ttc=total_ttc,
        )


def export_personal_expense_note(
    tenant_id: str,
    month: str | date_cls | None = None,
    *,
    export_dir: str | os.PathLike[str] | None = None,
    store: SqliteAccountingStore | None = None,
) -> PersonalExpenseNoteReport:
    """Convenience wrapper around :class:`PersonalExpenseNoteExporter`."""
    exporter = PersonalExpenseNoteExporter(
        store if store is not None else get_shared_sqlite_store(),
        export_dir=export_dir,
    )
    return exporter.export(tenant_id, month)


def export_personal_expense_notes(
    tenant_id: str,
    *,
    export_dir: str | os.PathLike[str] | None = None,
    store: SqliteAccountingStore | None = None,
) -> PersonalExpenseNoteReport:
    """Export every month that contains manager-funded expenses."""
    return export_personal_expense_note(
        tenant_id, None, export_dir=export_dir, store=store
    )


NOTE_DE_FRAIS_DIRIGEANT_HEADERS = PERSONAL_EXPENSE_NOTE_HEADERS
NoteDeFraisDirigeantExporter = PersonalExpenseNoteExporter
NoteDeFraisDirigeantReport = PersonalExpenseNoteReport
export_note_de_frais_dirigeant = export_personal_expense_note


__all__ = [
    "CGI_ARTICLE_206",
    "NOTE_DE_FRAIS_DIRIGEANT_HEADERS",
    "NoteDeFraisDirigeantExporter",
    "NoteDeFraisDirigeantReport",
    "PERSONAL_EXPENSE_NOTE_FILENAME",
    "PERSONAL_EXPENSE_NOTE_HEADERS",
    "PersonalExpenseNoteExporter",
    "PersonalExpenseNoteReport",
    "counterparty_account_of",
    "export_note_de_frais_dirigeant",
    "export_personal_expense_note",
    "export_personal_expense_notes",
    "is_personal_expense",
    "payment_source_of",
]
