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

from openpyxl import Workbook
from openpyxl.comments import Comment
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


SUPPLIER_LEDGER_COLUMNS = (
    "Date",
    "Type",
    "N° Pièce / Réf",
    "Libellé",
    "Débit",
    "Crédit",
    "Solde Cumulé",
    "Code Lettrage / Statut",
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
SUPPLIER_LEDGER_MIN_WIDTHS = (12, 12, 24, 40, 14, 14, 16, 24)


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
                status=_ledger_status(False, raw_status, matched),
                raw_status=raw_status,
                line_id=str(row.get("line_id") or ""),
                invoice_id=invoice_id or None,
                supplier=_ledger_supplier(row),
                matched=matched,
                matchable=not matched and (debit != 0 or credit != 0),
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
        values = (
            _date_cell(entry.operation_date),
            entry.entry_type,
            _text(entry.reference),
            _text(entry.label),
            entry.debit,
            entry.credit,
            None,
            _text(entry.status),
        )
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=index, column=column)
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
            sheet, entries, diagnostics.get(supplier, [])
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
