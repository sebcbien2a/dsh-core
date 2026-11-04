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

CATEGORY_SUMMARY_COLUMNS = (
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
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.stem}_", suffix=".tmp", dir=output_path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        workbook.save(temporary)
        os.replace(temporary, output_path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


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
    return relative_link(found, workbook_path)


def _write_piece_link(cell: Any, value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    if not value.replace("\\", "/").startswith("PIECES_JOINTES/"):
        return False
    cell.value = value
    cell.hyperlink = value
    cell.font = Font(color="0563C1", underline="single")
    return True


def _write_category_workbook(
    category: DocumentCategory, records: list[dict[str, Any]], output_path: Path
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

    for column, name in enumerate(CATEGORY_SUMMARY_COLUMNS, start=1):
        cell = sheet.cell(row=1, column=column, value=name)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = border
        cell.alignment = header_alignment
    sheet.row_dimensions[1].height = 32

    for offset, record in enumerate(records, start=2):
        values = (
            _date_cell(record["date"]),
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
        if column in (5, 6, 7):
            letter = get_column_letter(column)
            cell.value = (
                f"=SUM({letter}2:{letter}{last_data_row})" if records else 0
            )
            cell.number_format = "#,##0.00"
            cell.alignment = Alignment(horizontal="right", vertical="center")
        elif column == 1:
            cell.alignment = Alignment(horizontal="left", vertical="center")

    for column in range(1, len(CATEGORY_SUMMARY_COLUMNS) + 1):
        longest = len(CATEGORY_SUMMARY_COLUMNS[column - 1])
        for row in range(2, last_data_row + 1):
            longest = max(longest, _display_length(sheet.cell(row=row, column=column).value))
        width = max(_MIN_WIDTHS[column - 1], min(longest + 2, 60))
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
    ) -> None:
        self._store = store
        self._export_dir = _resolve_export_dir(export_dir)

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
        _write_category_workbook(selected, records, target)
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


SUPPLIER_LEDGER_COLUMNS = (
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
SUPPLIER_LEDGER_MAX_COMBINATION_ITEMS = 30
SUPPLIER_LEDGER_EQUILIBRIUM_FILL = "C6EFCE"
SUPPLIER_LEDGER_DIAGNOSTIC_FILL = "FCE4D6"
SUPPLIER_LEDGER_MIN_WIDTHS = (12, 12, 24, 40, 14, 14, 16, 24, 32)


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
) -> list[SupplierLedgerEntry]:
    matched_invoice_ids = {
        str(row.get("decision_invoice_id"))
        for row in rows
        if row.get("decision_invoice_id")
    }
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
        if _is_bank_ledger_row(row):
            if action == "PERSONAL_EXPENSE" or raw_status == "BOOKED_PERSONAL":
                continue
            debit = _decimal(row.get("debit_amount")) or Decimal("0.00")
            credit = _decimal(row.get("credit_amount")) or Decimal("0.00")
            if debit == 0 and credit == 0:
                continue
            matched = bool(row.get("decision_invoice_id")) or raw_status != "PENDING"
            entries.append(
                SupplierLedgerEntry(
                    entry_type=SUPPLIER_LEDGER_TYPE_PAIEMENT,
                    operation_date=row.get("operation_date"),
                    reference=_piece_reference(
                        row, str(row.get("line_id") or "")
                    ),
                    label=str(row.get("label") or ""),
                    debit=debit,
                    credit=credit,
                    status=_ledger_status(True, raw_status, matched),
                    raw_status=raw_status,
                    line_id=str(row.get("line_id") or ""),
                    invoice_id=(
                        str(
                            row.get("decision_invoice_id")
                            or row.get("invoice_id")
                            or ""
                        )
                        or None
                    ),
                    supplier=_ledger_supplier(row),
                    matched=matched,
                    matchable=False,
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
                supplier=_ledger_supplier(row),
                matched=matched,
                matchable=(
                    not personal
                    and not matched
                    and (debit != 0 or credit != 0)
                ),
            )
        )
    return entries


def _group_supplier_entries(
    entries: list[SupplierLedgerEntry],
) -> dict[str, list[SupplierLedgerEntry]]:
    groups: dict[str, list[SupplierLedgerEntry]] = {}
    display: dict[str, str] = {}
    for entry in entries:
        name = entry.supplier.strip() or "NON_AFFECTE"
        key = " ".join(name.casefold().split())
        if key not in groups:
            groups[key] = []
            display[key] = name
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


def _write_supplier_ledger_sheet(
    sheet: Any,
    entries: list[SupplierLedgerEntry],
    diagnostics: list[SupplierLedgerDiagnostic],
    workbook_path: Path | None = None,
) -> None:
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
    for column, name in enumerate(SUPPLIER_LEDGER_COLUMNS, start=1):
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
        piece_link = (
            _piece_link_for_row(
                workbook_path,
                {
                    "invoice_id": entry.invoice_id or entry.reference,
                    "category": DocumentCategory.ACHATS_MARCHANDISES.value,
                },
            )
            if workbook_path is not None
            else None
        )
        values = (
            _date_cell(entry.operation_date),
            entry.entry_type,
            _text(entry.reference),
            _text(entry.label),
            entry.debit,
            entry.credit,
            None,
            _text(entry.status),
            piece_link,
        )
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=index, column=column)
            if column == len(SUPPLIER_LEDGER_COLUMNS) and _write_piece_link(
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
    sheet.auto_filter.ref = f"A1:H{last_data_row}"
    sheet.freeze_panes = "A2"

    total_row = last_data_row + 1
    total_values = {
        1: SUPPLIER_LEDGER_TOTAL_LABEL,
        5: f"=SUM(E2:E{last_data_row})" if entries else 0,
        6: f"=SUM(F2:F{last_data_row})" if entries else 0,
        7: f"=G{last_data_row}" if entries else 0,
        8: "SOLDE FINAL",
    }
    for column in range(1, len(SUPPLIER_LEDGER_COLUMNS) + 1):
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
    for column in range(2, len(SUPPLIER_LEDGER_COLUMNS) + 1):
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

    for column in range(1, len(SUPPLIER_LEDGER_COLUMNS) + 1):
        longest = len(SUPPLIER_LEDGER_COLUMNS[column - 1])
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
            SUPPLIER_LEDGER_MIN_WIDTHS[column - 1],
            min(longest + 2, 60),
        )
        sheet.column_dimensions[get_column_letter(column)].width = width


def _write_supplier_ledger_workbook(
    groups: dict[str, list[SupplierLedgerEntry]],
    diagnostics: dict[str, list[SupplierLedgerDiagnostic]],
    output_path: Path,
    title: str,
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
    ) -> None:
        self._store = store
        self._export_dir = _resolve_export_dir(export_dir)

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
        entries = _build_supplier_ledger_entries(rows)
        groups = _group_supplier_entries(entries)
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
            groups, diagnostics, target, f"Grand livre {tenant}"
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


# ---------------------------------------------------------------------------
# P17 - Expense sub-categories, master tables, VAT de-duplication & fiscal year
# ---------------------------------------------------------------------------

EXPENSE_SUBCATEGORY_HEADERS: dict[ExpenseSubCategory, tuple[str, ...]] = {
    ExpenseSubCategory.RESTAURATION: (
        "Date Ticket",
        "Lieu",
        "Société / Personne Invitée",
        "HT",
        "TVA",
        "TTC",
    ),
    ExpenseSubCategory.CARBURANT: (
        "Date",
        "Fournisseur / Station",
        "Véhicule",
        "HT",
        "TVA",
        "TTC",
    ),
    ExpenseSubCategory.TRANSPORT_PARKING: (
        "Date",
        "Nature (Péage/Train/Avion/Parking)",
        "Trajet / Lieu",
        "HT",
        "TVA",
        "TTC",
    ),
    ExpenseSubCategory.HOTELLERIE: (
        "Date",
        "Hôtel / Lieu",
        "HT Hébergement",
        "HT Petit-Déj / Repas",
        "TVA Déductible",
        "TVA Non Déductible",
        "TTC",
    ),
    ExpenseSubCategory.INFORMATIQUE_WEB: (
        "Date",
        "Fournisseur",
        "Description",
        "HT",
        "TVA",
        "TTC",
    ),
    ExpenseSubCategory.HONORAIRES_JURIDIQUES: (
        "Date",
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
MASTER_ACHATS_FILENAME = "TABLEAU_MERE_ACHATS_CHARGES.xlsx"
MASTER_BANQUE_TVA_FILENAME = "TABLEAU_MERE_BANQUE_TVA.xlsx"
MASTER_ACHATS_HEADERS: tuple[str, ...] = (
    "Date Pièce",
    "Fournisseur",
    "N° Pièce",
    "Catégorie",
    "Sous-catégorie",
    "Repère Classement",
    "Statut Anti-Doublon TVA",
    "Mode Exercice Fiscal",
    "Exercice Cible",
    "Mois Décalé",
    "Date Comptable",
    "HT",
    "TVA",
    "TTC",
    "Lien Justificatif",
    "Source de Paiement",
    "Compte de Contrepartie",
    PIECE_LINK_HEADER,
)
MASTER_BANQUE_TVA_HEADERS: tuple[str, ...] = (
    "Date Pièce",
    "Fournisseur",
    "N° Pièce",
    "TVA",
    "TVA Comptée",
    "Repère Classement",
    "Statut Anti-Doublon TVA",
    "Mode Exercice Fiscal",
    "Exercice Cible",
    "Mois Décalé",
    "Date Comptable",
    "HT",
    "TTC",
    "Lien Justificatif",
    "Source de Paiement",
    "Compte de Contrepartie",
    PIECE_LINK_HEADER,
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
    ht = record["ht"] or Decimal("0.00")
    tva = record["tva"] or Decimal("0.00")
    ttc = record["ttc"] or Decimal("0.00")
    if subcategory is ExpenseSubCategory.RESTAURATION:
        return (
            date_value,
            _text_field(row, "lieu_restaurant") or record["counterparty"],
            _text_field(row, "invite_restaurant"),
            ht,
            tva,
            ttc,
        )
    if subcategory is ExpenseSubCategory.CARBURANT:
        return (
            date_value,
            _text_field(row, "fuel_station") or record["counterparty"],
            _text_field(row, "vehicle"),
            ht,
            tva,
            ttc,
        )
    if subcategory is ExpenseSubCategory.TRANSPORT_PARKING:
        return (
            date_value,
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
    ):
        return (
            date_value,
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
                        value[2] + value[3]
                        for value in values
                        if isinstance(value[2], Decimal)
                        and isinstance(value[3], Decimal)
                    ),
                    Decimal("0.00"),
                )
                tva = sum(
                    (
                        value[4] + value[5]
                        for value in values
                        if isinstance(value[4], Decimal)
                        and isinstance(value[5], Decimal)
                    ),
                    Decimal("0.00"),
                )
                ttc_index = 6
            else:
                ht = sum(
                    (
                        value[3]
                        for value in values
                        if isinstance(value[3], Decimal)
                    ),
                    Decimal("0.00"),
                )
                tva = sum(
                    (
                        value[4]
                        for value in values
                        if isinstance(value[4], Decimal)
                    ),
                    Decimal("0.00"),
                )
                ttc_index = 5
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


def _master_rows(store: Any, tenant_id: str) -> list[dict[str, Any]]:
    list_master = getattr(store, "list_master_lines", None)
    if callable(list_master):
        rows = list_master(tenant_id)
        return [
            row
            for row in rows
            if str(row.get("category") or "") != DocumentCategory.VENTES_CLIENTS.value
            and str(row.get("document_type") or "").strip().upper()
            not in NON_ACCOUNTABLE_DOCUMENT_TYPES
        ]
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for category in DocumentCategory:
        if category is DocumentCategory.VENTES_CLIENTS:
            continue
        for row in store.list_category_lines(tenant_id, category):
            if (
                str(row.get("document_type") or "").strip().upper()
                in NON_ACCOUNTABLE_DOCUMENT_TYPES
            ):
                continue
            identifier = _line_identifier(row, len(rows))
            if identifier in seen:
                continue
            seen.add(identifier)
            rows.append(row)
    return rows


class MasterTableExporter:
    """Build the two P17 master workbooks under ``04_EXPORT_COMPTA``."""

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
        achats_path = output_dir / MASTER_ACHATS_FILENAME
        banque_path = output_dir / MASTER_BANQUE_TVA_FILENAME
        rows = _master_rows(self._store, tenant)
        statuses, duplicates = compute_tva_dedup(rows)
        fiscal_totals = compute_fiscal_totals(rows, statuses)

        achats_rows: list[tuple[Any, ...]] = []
        banque_rows: list[tuple[Any, ...]] = []
        for row, status in zip(rows, statuses):
            record = _summary_record(row)
            subcategory = _infer_expense_subcategory(row)
            marker = classify_marker(subcategory)
            decision = resolve_fiscal_year(row)
            piece = _piece_number_from_row(row) or _piece_reference(
                row, row.get("invoice_id") or row.get("line_id") or ""
            )
            date_value = _date_cell(record["date"])
            effective = decision.effective_date or date_value
            ht = record["ht"] or Decimal("0.00")
            tva = record["tva"] or Decimal("0.00")
            ttc = record["ttc"] or Decimal("0.00")
            common_tail = (
                marker,
                _status_value(status),
                decision.mode.value,
                decision.target_year if decision.target_year is not None else "",
                decision.deferred_month or "",
                effective,
            )
            achats_rows.append(
                (
                    date_value,
                    record["counterparty"],
                    piece,
                    str(row.get("category") or ""),
                    subcategory.value if subcategory is not None else NON_CLASSIFIED_MARKER,
                    *common_tail,
                    ht,
                    tva,
                    ttc,
                    record["reference"],
                    payment_source_of(row),
                    counterparty_account_of(row),
                    _piece_link_for_row(achats_path, row),
                )
            )
            if tva != 0 or str(row.get("category") or "") in {
                DocumentCategory.BANQUE_FINANCE.value,
                DocumentCategory.FISCAL_SOCIAL.value,
            }:
                banque_rows.append(
                    (
                        date_value,
                        record["counterparty"],
                        piece,
                        tva,
                        Decimal("0.00")
                        if _is_duplicate_status(status)
                        else tva,
                        *common_tail,
                        ht,
                        ttc,
                        record["reference"],
                        payment_source_of(row),
                        counterparty_account_of(row),
                        _piece_link_for_row(banque_path, row),
                    )
                )

        _write_p17_workbook(
            achats_path,
            title="TABLEAU_MERE_ACHATS_CHARGES",
            headers=MASTER_ACHATS_HEADERS,
            rows=achats_rows,
        )
        _write_p17_workbook(
            banque_path,
            title="TABLEAU_MERE_BANQUE_TVA",
            headers=MASTER_BANQUE_TVA_HEADERS,
            rows=banque_rows,
        )
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
            achats_path=achats_path,
            banque_tva_path=banque_path,
            row_count=len(rows),
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
