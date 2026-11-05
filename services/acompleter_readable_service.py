"""P24 - Human-readable ``_A_COMPLETER.xlsx`` (business language + PDF links).

The manager-facing workbook is the single human input channel. It exposes the
P17/P24 arbitration columns with official Excel dropdowns and keeps hidden
technical columns (AI suggestion, SHA-256 and filename) so human edits can be
matched back to the SQLite ledger without ambiguity.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from schemas.accounting_choices import (
    CATEGORY_CHOICES,
    DEFERRED_MONTH_CHOICES,
    EXERCISE_CHOICES,
    PAYMENT_SOURCE_CHOICES,
    REPORT_CHOICES,
    excel_list,
    extract_year,
    normalize_category,
    normalize_exercise,
    normalize_month,
    normalize_payment_source,
    normalize_report,
    normalize_status,
)
from schemas.folder_state_schema import DocumentCategory, DocumentStatus
from services.pdf_splitter_and_linker import relative_link

READABLE_HEADERS: tuple[str, ...] = (
    "Date Pièce",
    "Fournisseur / Tiers",
    "N° Pièce",
    "TTC",
    "Catégorie Proposée",
    "Pourquoi c'est en attente & Conseil IA",
    "Action / Décision",
    "Source de paiement",
    "Compte de contrepartie",
    "Exercice cible",
    "Mois décalé",
    "Report",
    "Pièce PDF (Clic)",
)
HIDDEN_HEADERS: tuple[str, ...] = (
    "Catégorie IA",
    "Action IA",
    "SHA-256",
    "Nom Fichier",
)
READABLE_SHEET = "A_COMPLETER"
READABLE_FILENAME = "_A_COMPLETER.xlsx"
READABLE_FALLBACK_SUFFIX = "_MIS_A_JOUR"
READABLE_WORKBOOK_HEADER_FILL = "1F4E78"
READABLE_WORKBOOK_BAND_FILL = "F2F7FB"
READABLE_PENDING_FILL = "FFF2CC"
READABLE_VALID_FILL = "E2EFDA"
READABLE_REJECT_FILL = "FCE4D6"

CATEGORY_COLUMN = "E"
PAYMENT_SOURCE_COLUMN = "H"
EXERCISE_COLUMN = "J"
MONTH_COLUMN = "K"
REPORT_COLUMN = "L"
ACTION_COLUMN = "G"


class ReadableWorkbookError(ValueError):
    """Invalid readable-workbook request; ``code`` is stable for callers."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class ReadableRow:
    """One line of the manager-facing ``_A_COMPLETER.xlsx`` view."""

    sha256: str
    filename: str
    supplier: str
    document_number: str
    document_date: date | None
    amount_ht: Decimal | None
    amount_tva: Decimal | None
    amount_ttc: Decimal | None
    category: DocumentCategory | str
    reason: str
    action: str
    status: DocumentStatus | str = DocumentStatus.EN_ATTENTE
    piece_pdf: str | None = None
    rule: str = ""
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def status_value(self) -> str:
        try:
            return DocumentStatus(self.status).value
        except (TypeError, ValueError):
            return str(self.status)

    @property
    def category_value(self) -> str:
        try:
            return DocumentCategory(self.category).value
        except (TypeError, ValueError):
            return str(self.category)


@dataclass(frozen=True)
class ReadableDecision:
    """Human decision read back from ``02_A_COMPLETER/_A_COMPLETER.xlsx``."""

    sha256: str
    filename: str
    status: str
    category: str | None = None
    payment_source: str | None = None
    counterparty_account: str = ""
    fiscal_year_target: str | None = None
    deferred_month: str | None = None
    report: str | None = None
    action: str = ""

    @property
    def is_validated(self) -> bool:
        return self.status == DocumentStatus.VALIDE.value

    @property
    def is_rejected(self) -> bool:
        return self.status == DocumentStatus.REJETE.value


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _date_cell(value: date | None) -> Any:
    return value if value is not None else ""


def _amount_cell(value: Decimal | None) -> Any:
    return float(value) if isinstance(value, Decimal) else (value if value is not None else "")


def _link_for(workbook_path: Path, target: str | None) -> str | None:
    if not target:
        return None
    raw = str(target).replace("\\", "/").strip()
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_absolute():
        try:
            return relative_link(candidate, workbook_path)
        except ValueError:
            return None
    resolved = (workbook_path.parent / candidate).resolve()
    if resolved.is_file():
        try:
            return relative_link(resolved, workbook_path)
        except ValueError:
            return None
    parent_resolved = (workbook_path.parent.parent / candidate).resolve()
    if parent_resolved.is_file():
        try:
            return relative_link(parent_resolved, workbook_path)
        except ValueError:
            return None
    return raw if not raw.startswith("/") and ":" not in raw[:2] else None


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    index = 1
    while True:
        candidate = path.with_name(f"{path.stem}__{index}{path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _fallback_path(path: Path) -> Path:
    candidate = path.with_name(
        f"{path.stem}{READABLE_FALLBACK_SUFFIX}{path.suffix}"
    )
    return _unique_path(candidate)


def _add_list_validation(
    sheet: Any, column: str, choices: Iterable[str], last_row: int
) -> None:
    from openpyxl.worksheet.datavalidation import DataValidation

    validation = DataValidation(
        type="list",
        formula1=excel_list(choices),
        allow_blank=True,
        showDropDown=False,
    )
    validation.error = "Choisissez une valeur proposée dans la liste."
    validation.errorTitle = "Choix invalide"
    validation.prompt = "Sélectionnez une valeur officielle."
    validation.promptTitle = "Liste officielle"
    sheet.add_data_validation(validation)
    validation.add(f"{column}2:{column}{max(last_row, 2)}")


def _install_validations(sheet: Any, last_row: int) -> None:
    try:
        sheet.data_validations.dataValidation.clear()
    except AttributeError:
        pass
    _add_list_validation(sheet, CATEGORY_COLUMN, CATEGORY_CHOICES, last_row)
    _add_list_validation(
        sheet, PAYMENT_SOURCE_COLUMN, PAYMENT_SOURCE_CHOICES, last_row
    )
    _add_list_validation(sheet, EXERCISE_COLUMN, EXERCISE_CHOICES, last_row)
    _add_list_validation(
        sheet, MONTH_COLUMN, DEFERRED_MONTH_CHOICES, last_row
    )
    _add_list_validation(sheet, REPORT_COLUMN, REPORT_CHOICES, last_row)


def write_readable_workbook(
    output_path: str | os.PathLike[str],
    rows: Iterable[ReadableRow],
    *,
    sheet_name: str = READABLE_SHEET,
) -> Path:
    """Write the styled, filtered, dropdown-enabled manager workbook.

    If the target workbook is held open by Excel (``PermissionError`` /
    ``[Errno 13]``), the bytes are written to ``_A_COMPLETER_MIS_A_JOUR.xlsx``
    and that path is returned instead of crashing the cockpit.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name[:31]
    sheet.sheet_view.showGridLines = False

    thin = Side(style="thin", color="B7C4D4")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill("solid", fgColor=READABLE_WORKBOOK_HEADER_FILL)
    header_alignment = Alignment(
        horizontal="center", vertical="center", wrap_text=True
    )
    band_fill = PatternFill("solid", fgColor=READABLE_WORKBOOK_BAND_FILL)
    status_fills = {
        DocumentStatus.EN_ATTENTE.value: PatternFill(
            "solid", fgColor=READABLE_PENDING_FILL
        ),
        DocumentStatus.VALIDE.value: PatternFill(
            "solid", fgColor=READABLE_VALID_FILL
        ),
        DocumentStatus.REJETE.value: PatternFill(
            "solid", fgColor=READABLE_REJECT_FILL
        ),
    }

    for column, header in enumerate(READABLE_HEADERS, start=1):
        cell = sheet.cell(row=1, column=column, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = border
        cell.alignment = header_alignment
    for offset, header in enumerate(
        HIDDEN_HEADERS, start=len(READABLE_HEADERS) + 1
    ):
        cell = sheet.cell(row=1, column=offset, value=header)
        cell.font = Font(bold=True, color="7F7F7F", size=9)
        sheet.column_dimensions[get_column_letter(offset)].hidden = True
    sheet.row_dimensions[1].height = 42

    ordered = sorted(
        list(rows),
        key=lambda item: (
            0 if item.status_value == DocumentStatus.EN_ATTENTE.value else 1,
            item.document_date or date.max,
            _text(item.filename).casefold(),
        ),
    )
    for offset, row in enumerate(ordered, start=2):
        values = (
            _date_cell(row.document_date),
            row.supplier,
            row.document_number,
            _amount_cell(row.amount_ttc),
            row.category_value,
            row.reason,
            row.action,
            row.extra.get("payment_source", ""),
            row.extra.get("counterparty_account", ""),
            row.extra.get("fiscal_year_target", ""),
            row.extra.get("deferred_month", ""),
            row.extra.get("report", ""),
            None,
        )
        hidden_values = (
            row.extra.get("ai_category", row.category_value),
            row.extra.get("ai_action", row.action),
            row.sha256,
            row.filename,
        )
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=offset, column=column)
            cell.value = value
            cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if isinstance(value, float):
                cell.number_format = "#,##0.00"
                cell.alignment = Alignment(horizontal="right", vertical="top")
            if offset % 2 == 0 and column not in (6, 7):
                cell.fill = band_fill
        for offset_hidden, value in enumerate(
            hidden_values, start=len(READABLE_HEADERS) + 1
        ):
            cell = sheet.cell(row=offset, column=offset_hidden, value=value)
            cell.alignment = Alignment(vertical="top")
        status_fill = status_fills.get(row.status_value)
        if status_fill is not None:
            sheet.cell(row=offset, column=7).fill = status_fill
        link = _link_for(path, row.piece_pdf)
        if link:
            cell = sheet.cell(row=offset, column=len(READABLE_HEADERS))
            cell.value = link
            cell.hyperlink = link
            cell.font = Font(color="0563C1", underline="single")
            cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    last_row = max(sheet.max_row, 1)
    _install_validations(sheet, last_row)
    sheet.auto_filter.ref = (
        f"A1:{get_column_letter(len(READABLE_HEADERS))}{last_row}"
    )
    sheet.freeze_panes = "A2"
    widths = (12, 26, 18, 12, 24, 62, 42, 22, 22, 14, 14, 10, 32)
    for column, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    workbook.properties.creator = "DSH-Core P24"
    workbook.properties.title = "APD - Pièces à compléter"

    temporary = path.with_name(path.name + ".tmp-p24")
    written = path
    try:
        workbook.save(temporary)
        try:
            os.replace(temporary, path)
        except OSError:
            try:
                shutil.copy2(temporary, path)
            except OSError:
                written = _fallback_path(path)
                shutil.copy2(temporary, written)
    except OSError as exc:
        workbook.close()
        raise ReadableWorkbookError("WRITE_FAILED", str(path)) from exc
    finally:
        workbook.close()
        temporary.unlink(missing_ok=True)
    return written


def read_readable_decisions(
    output_path: str | os.PathLike[str],
) -> tuple[ReadableDecision, ...]:
    """Read the human edits of the readable workbook (priority over AI)."""
    from openpyxl import load_workbook

    path = Path(output_path)
    if not path.is_file():
        return ()
    try:
        workbook = load_workbook(path, data_only=True)
    except (OSError, PermissionError):
        return ()
    sheet = workbook.active
    try:
        headers = {
            _text(cell.value): cell.column
            for cell in next(sheet.iter_rows(min_row=1, max_row=1))
            if _text(cell.value)
        }
        decisions: list[ReadableDecision] = []
        for row in sheet.iter_rows(min_row=2):
            values = {
                header: _text(row[column - 1].value)
                for header, column in headers.items()
            }
            sha = values.get("SHA-256", "")
            filename = (
                values.get("Nom Fichier", "")
                or values.get("Nom fichier", "")
                or values.get("N° Pièce", "")
            )
            if not sha and not filename:
                continue
            action = values.get("Action / Décision", "")
            action_ia = values.get("Action IA", "")
            observed_category = values.get("Catégorie Proposée", "")
            ai_category = values.get("Catégorie IA", "")
            category = normalize_category(observed_category)
            status = None
            if action_ia:
                if _normalize_label(action) != _normalize_label(action_ia):
                    status = normalize_status(action)
            else:
                status = _explicit_status(action)
            if status is None and category is not None and ai_category:
                if _normalize_label(observed_category) != _normalize_label(ai_category):
                    status = DocumentStatus.VALIDE.value
            if status is None:
                status = DocumentStatus.EN_ATTENTE.value
            decisions.append(
                ReadableDecision(
                    sha256=sha,
                    filename=filename,
                    status=status,
                    category=category,
                    payment_source=normalize_payment_source(
                        values.get("Source de paiement", "")
                    ),
                    counterparty_account=values.get(
                        "Compte de contrepartie", ""
                    ),
                    fiscal_year_target=normalize_exercise(
                        values.get("Exercice cible", "")
                    )
                    or extract_year(observed_category),
                    deferred_month=normalize_month(
                        values.get("Mois décalé", "")
                    ),
                    report=normalize_report(values.get("Report", "")),
                    action=action,
                )
            )
        return tuple(decisions)
    finally:
        workbook.close()


def _explicit_status(value: Any) -> str | None:
    token = re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())
    if token in {"VALIDE", "VALIDER", "OK", "ACCEPTER"}:
        return DocumentStatus.VALIDE.value
    if token in {"REJETE", "REJETER", "KO", "REFUSER"}:
        return DocumentStatus.REJETE.value
    return None


def _normalize_label(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


__all__ = [
    "HIDDEN_HEADERS",
    "READABLE_FILENAME",
    "READABLE_FALLBACK_SUFFIX",
    "READABLE_HEADERS",
    "READABLE_SHEET",
    "ReadableDecision",
    "ReadableRow",
    "ReadableWorkbookError",
    "read_readable_decisions",
    "write_readable_workbook",
]
