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
import tempfile
from datetime import date as date_cls
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import StringIO
from pathlib import Path
from typing import Any, Protocol

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from schemas.folder_state_schema import DocumentCategory
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
_CENT = Decimal("0.01")
_HEADER_FILL = "1F4E78"
_BAND_FILL = "F2F7FB"
_TOTAL_FILL = "DDEBF7"
_BORDER_COLOR = "B7C4D4"
_MIN_WIDTHS = (12, 24, 18, 32, 14, 14, 14, 32)


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
        "invoice_id": row.get("invoice_id") or row.get("line_id") or "",
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
        )
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=offset, column=column)
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
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(CATEGORY_SUMMARY_COLUMNS))}{last_data_row}"
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
        records = [_summary_record(row) for row in rows]
        target = (
            Path(output_path)
            if output_path is not None
            else self._export_dir
            / tenant
            / f"SYNTHESE_{tenant}_{selected.value}.xlsx"
        )
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
