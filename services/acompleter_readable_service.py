"""P22 - Human-readable ``_A_COMPLETER.xlsx`` (business language + PDF links).

The historical workbook kept opaque UUID / SHA-256 columns and a comma-joined
``Champs manquants`` cell. This module builds the manager-facing view required
by P22: one clear line per doubt, a real French accountant explanation, an
explicit action, and a clickable relative link to the PDF from day one
(``02_A_COMPLETER``) through every downstream workbook.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from schemas.folder_state_schema import DocumentCategory, DocumentStatus
from services.pdf_splitter_and_linker import relative_link

READABLE_HEADERS: tuple[str, ...] = (
    "Date Pièce",
    "Fournisseur Détecté",
    "Nom Fichier",
    "N° Pièce",
    "HT",
    "TVA",
    "TTC",
    "Catégorie Proposée",
    "Pourquoi c'est en attente & Conseil IA (Argumenté)",
    "Action / Décision",
    "Pièce PDF (Clic)",
)
READABLE_SHEET = "A_COMPLETER"
READABLE_FILENAME = "_A_COMPLETER.xlsx"
READABLE_WORKBOOK_HEADER_FILL = "1F4E78"
READABLE_WORKBOOK_BAND_FILL = "F2F7FB"
READABLE_PENDING_FILL = "FFF2CC"
READABLE_VALID_FILL = "E2EFDA"
READABLE_REJECT_FILL = "FCE4D6"


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


def _text(value: Any) -> str:
    return "" if value is None else str(value)


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
    # The caller supplies a path relative to the tenant root. Resolve it from
    # the workbook parent when possible, otherwise keep the stored path.
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


def write_readable_workbook(
    output_path: str | os.PathLike[str],
    rows: Iterable[ReadableRow],
    *,
    sheet_name: str = READABLE_SHEET,
) -> Path:
    """Write a styled, filtered, hyperlinked manager workbook atomically."""
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
            row.filename,
            row.document_number,
            _amount_cell(row.amount_ht),
            _amount_cell(row.amount_tva),
            _amount_cell(row.amount_ttc),
            row.category_value,
            row.reason,
            row.action,
            None,
        )
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=offset, column=column)
            cell.value = value
            cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if isinstance(value, float):
                cell.number_format = "#,##0.00"
                cell.alignment = Alignment(horizontal="right", vertical="top")
            if offset % 2 == 0 and column not in (9, 10):
                cell.fill = band_fill
        status_fill = status_fills.get(row.status_value)
        if status_fill is not None:
            sheet.cell(row=offset, column=10).fill = status_fill
        link = _link_for(path, row.piece_pdf)
        if link:
            cell = sheet.cell(row=offset, column=len(READABLE_HEADERS))
            cell.value = link
            cell.hyperlink = link
            cell.font = Font(color="0563C1", underline="single")
            cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    last_row = max(sheet.max_row, 1)
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(READABLE_HEADERS))}{last_row}"
    sheet.freeze_panes = "A2"
    widths = (12, 26, 32, 18, 12, 12, 12, 24, 62, 42, 40)
    for column, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    workbook.properties.creator = "DSH-Core P22"
    workbook.properties.title = "APD - Pièces à compléter"

    temporary = path.with_name(path.name + ".tmp-p22")
    try:
        workbook.save(temporary)
        workbook.close()
        try:
            os.replace(temporary, path)
        except OSError:
            shutil.copy2(temporary, path)
    except OSError as exc:
        workbook.close()
        raise ReadableWorkbookError("WRITE_FAILED", str(path)) from exc
    finally:
        temporary.unlink(missing_ok=True)
    return path


__all__ = [
    "READABLE_FILENAME",
    "READABLE_HEADERS",
    "READABLE_SHEET",
    "ReadableRow",
    "ReadableWorkbookError",
    "write_readable_workbook",
]
