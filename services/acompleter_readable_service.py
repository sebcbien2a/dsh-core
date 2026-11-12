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
    DECISION_SIMPLE_CHOICES,
    DEFERRED_MONTH_CHOICES,
    EXERCISE_CHOICES,
    PAYMENT_SOURCE_CHOICES,
    REPORT_CHOICES,
    STATUS_CHOICES,
    SUBCATEGORY_CHOICES,
    excel_list,
    extract_year,
    normalize_category,
    normalize_decision,
    normalize_exercise,
    normalize_month,
    normalize_payment_source,
    normalize_report,
    normalize_status,
)
from schemas.folder_state_schema import DocumentCategory, DocumentStatus
from services.cockpit_decision_service import (
    decision_menu_for,
    full_decision_choices,
)

READABLE_HEADERS: tuple[str, ...] = (
    "Date",
    "Source",
    "Fichier / Réf",
    "Fournisseur / Tiers",
    "Libellé / N° Pièce",
    "Montant TTC (€)",
    "Pourquoi en attente",
    "Action attendue",
    "Catégorie",
    "Sous-Catégorie",
    "Exercice Année",
    "Source de paiement",
    "Décision",
)
# Historical P22/P24 layout, still used by the hidden technical workbook.
LEGACY_READABLE_HEADERS: tuple[str, ...] = (
    "Date Pièce",
    "Exercice Année",
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
    "Rattacher comme ALIAS de (ou NOUVEAU_TIERS)",
    "Pièce PDF (Clic)",
)
HIDDEN_HEADERS: tuple[str, ...] = (
    "Catégorie IA",
    "Action IA",
    "SHA-256",
    "Nom Fichier",
    "Sous-Catégorie IA",
    "Exercice cible",
    "Mois décalé",
    "Report",
    "Rattacher comme ALIAS de (ou NOUVEAU_TIERS)",
    "Compte de contrepartie",
    "Pièce PDF (Clic)",
)
READABLE_SHEET = "A_COMPLETER"
READABLE_FILENAME = "_A_COMPLETER.xlsx"
READABLE_FALLBACK_SUFFIX = "_MIS_A_JOUR"
READABLE_WORKBOOK_HEADER_FILL = "1F4E78"
READABLE_WORKBOOK_BAND_FILL = "F2F7FB"
READABLE_PENDING_FILL = "FFF2CC"
READABLE_VALID_FILL = "E2EFDA"
READABLE_REJECT_FILL = "FCE4D6"

CATEGORY_COLUMN = "I"
SUBCATEGORY_COLUMN = "J"
EXERCISE_YEAR_COLUMN = "K"
PAYMENT_SOURCE_COLUMN = "L"
DECISION_COLUMN = "M"
DATE_COLUMN = "A"
SOURCE_COLUMN = "B"
FILE_COLUMN = "C"
TTC_COLUMN = "F"
ACTION_COLUMN = "H"
# Hidden arbitration columns keep their legacy letters for the technical
# workbook compatibility and are never shown to the director.
EXERCISE_COLUMN = "S"
MONTH_COLUMN = "T"
REPORT_COLUMN = "U"
ALIAS_TARGET_COLUMN = "V"


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
    exercise_year: str = ""
    source: str = ""
    label: str = ""
    subcategory: str = ""
    date_label: str = ""
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
    exercise_year: str | None = None
    deferred_month: str | None = None
    report: str | None = None
    tier_alias_target: str = ""
    action: str = ""
    subcategory: str | None = None
    decision: str = ""

    @property
    def is_validated(self) -> bool:
        return self.status == DocumentStatus.VALIDE.value

    @property
    def is_rejected(self) -> bool:
        return self.status == DocumentStatus.REJETE.value


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _first_value(values: dict[str, Any], *headers: str) -> str:
    for header in headers:
        value = _text(values.get(header))
        if value:
            return value
    return ""


def _date_cell(value: date | None) -> Any:
    return value if value is not None else ""


def _amount_cell(value: Decimal | None) -> Any:
    return float(value) if isinstance(value, Decimal) else (value if value is not None else "")


def _is_fake_bank_source_piece(value: Any) -> bool:
    normalized = str(value or "").replace("\\", "/").upper()
    if not normalized:
        return False
    name = normalized.rsplit("/", 1)[-1]
    return "SOURCE_OFX" in name or "CAISSE_D_EPARGNE_SOURCE_OFX" in name


def _piece_child_link(workbook_path: Path, raw: str) -> str | None:
    """Return a direct ``PIECES_JOINTES/<sub>/<file>`` child link.

    P27-septies - Office co-authoring turns the workbook base URL into
    ``https://d.docs.live.net/...`` as soon as a relative link climbs out of
    the folder. The readable workbook therefore never uses ``..`` and always
    points inside its own ``PIECES_JOINTES`` child tree.

    P27-nonies - a raw OFX statement is never a proof of expense and must not
    be linked as a piece PDF.
    """
    if _is_fake_bank_source_piece(raw):
        return None
    marker = "PIECES_JOINTES/"
    normalized = raw.replace("\\", "/").strip()
    if marker in normalized:
        suffix = normalized.split(marker, 1)[1].lstrip("/")
        if suffix and ".." not in suffix:
            return f"PIECES_JOINTES/{suffix}"
    name = Path(normalized).name
    if not name or _is_fake_bank_source_piece(name):
        return None
    current = workbook_path.resolve().parent
    for _ in range(6):
        pieces = current / "PIECES_JOINTES"
        if pieces.is_dir():
            for candidate in pieces.rglob(name):
                if _is_fake_bank_source_piece(candidate.name):
                    continue
                relative = candidate.relative_to(pieces).as_posix()
                return f"PIECES_JOINTES/{relative}"
        if current.parent == current:
            break
        current = current.parent
    return None


def _link_for(workbook_path: Path, target: str | None) -> str | None:
    if not target or _is_fake_bank_source_piece(target):
        return None
    raw = str(target).replace("\\", "/").strip()
    if not raw:
        return None
    sibling = workbook_path.parent / Path(raw).name
    if sibling.is_file() and not _is_fake_bank_source_piece(sibling.name):
        return sibling.name
    child = _piece_child_link(workbook_path, raw)
    if child is not None:
        return child
    candidate = Path(raw)
    if candidate.is_absolute():
        found = _piece_child_link(workbook_path, candidate.name)
        return found
    resolved = (workbook_path.parent / candidate).resolve()
    if resolved.is_file():
        found = _piece_child_link(workbook_path, resolved.name)
        if found is not None:
            return found
    parent_resolved = (workbook_path.parent.parent / candidate).resolve()
    if parent_resolved.is_file():
        found = _piece_child_link(workbook_path, parent_resolved.name)
        if found is not None:
            return found
    return None


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


def _add_formula_validation(
    sheet: Any,
    column: str,
    formula: str,
    last_row: int,
    *,
    first_row: int = 2,
) -> None:
    from openpyxl.worksheet.datavalidation import DataValidation

    validation = DataValidation(
        type="list",
        formula1=formula,
        allow_blank=True,
        showDropDown=False,
    )
    validation.error = "Choisissez une valeur proposée dans la liste."
    validation.errorTitle = "Choix invalide"
    validation.prompt = "Sélectionnez une valeur officielle."
    validation.promptTitle = "Liste officielle"
    sheet.add_data_validation(validation)
    validation.add(
        f"{column}{max(first_row, 2)}:{column}{max(last_row, 2)}"
    )


def _alias_choices() -> tuple[str, ...]:
    try:
        from services.tier_reference_service import TierReferenceService

        service = TierReferenceService()
        service.bootstrap(write_missing=False)
        return service.alias_targets()
    except Exception:  # noqa: BLE001
        return (
            "CREER_NOUVEAU_FOURNISSEUR",
            "CREER_NOUVEAU_CLIENT",
            "RESTAURANTS_REPAS",
            "PEAGES_AUTOROUTES",
            "STATIONS_CARBURANT",
        )


def _decision_value_for_row(row: ReadableRow) -> Any:
    """Pending lines stay blank (default VALIDE); explicit answers are kept."""
    if row.status_value == DocumentStatus.VALIDE.value:
        return row.status_value
    if row.status_value == DocumentStatus.REJETE.value:
        return row.status_value
    if str(row.extra.get("report") or "").upper() == "OUI":
        return "REPORTER_MOIS_SUIVANT"
    return ""


def _install_validations(
    sheet: Any,
    last_row: int,
    *,
    alias_formula: str | None = None,
    decision_blocks: Iterable[tuple[str, int, int]] = (),
) -> None:
    try:
        sheet.data_validations.dataValidation.clear()
    except AttributeError:
        pass
    _add_list_validation(sheet, CATEGORY_COLUMN, CATEGORY_CHOICES, last_row)
    _add_list_validation(
        sheet, SUBCATEGORY_COLUMN, SUBCATEGORY_CHOICES, last_row
    )
    _add_list_validation(
        sheet, EXERCISE_YEAR_COLUMN, EXERCISE_CHOICES, last_row
    )
    _add_list_validation(
        sheet, PAYMENT_SOURCE_COLUMN, PAYMENT_SOURCE_CHOICES, last_row
    )
    # P27-decies - one contextual Décision list per row block. Known tiers and
    # ARDOISE rows only expose the four lifecycle answers; unknown bank tiers
    # get the enriched list (groups, internal flows, known tiers, creation).
    for formula, first_row, block_last_row in decision_blocks:
        _add_formula_validation(
            sheet,
            DECISION_COLUMN,
            formula,
            block_last_row,
            first_row=first_row,
        )
    _add_list_validation(sheet, EXERCISE_COLUMN, EXERCISE_CHOICES, last_row)
    _add_list_validation(
        sheet, MONTH_COLUMN, DEFERRED_MONTH_CHOICES, last_row
    )
    _add_list_validation(sheet, REPORT_COLUMN, REPORT_CHOICES, last_row)
    if alias_formula:
        _add_formula_validation(
            sheet, ALIAS_TARGET_COLUMN, alias_formula, last_row
        )
    else:
        _add_list_validation(
            sheet, ALIAS_TARGET_COLUMN, _alias_choices(), last_row
        )


def _write_decision_lists(workbook: Any, tier_reference: Any = None) -> None:
    """Create the hidden lists used by the contextual Décision validations."""
    from openpyxl.workbook.defined_name import DefinedName

    lists = workbook.create_sheet("LISTES_DECISIONS")
    simple = tuple(DECISION_SIMPLE_CHOICES)
    full = full_decision_choices(tier_reference)
    for index, choice in enumerate(simple, start=1):
        lists.cell(row=index, column=1, value=choice)
    for index, choice in enumerate(full, start=1):
        lists.cell(row=index, column=2, value=choice)
    lists.sheet_state = "hidden"
    workbook.defined_names.add(
        DefinedName(
            "DECISION_SIMPLE",
            attr_text=f"LISTES_DECISIONS!$A$1:$A${max(len(simple), 1)}",
        )
    )
    workbook.defined_names.add(
        DefinedName(
            "DECISION_BANQUE_INCONNUE",
            attr_text=f"LISTES_DECISIONS!$B$1:$B${max(len(full), 1)}",
        )
    )


def _decision_blocks(
    sheet: Any, last_row: int, tier_reference: Any = None
) -> list[tuple[str, int, int]]:
    """Group consecutive rows sharing the same contextual Décision list."""
    blocks: list[tuple[str, int, int]] = []
    current_formula = ""
    start = 2
    for row_index in range(2, last_row + 1):
        filename = _text(sheet.cell(row=row_index, column=3).value)
        supplier = _text(sheet.cell(row=row_index, column=4).value)
        source = _text(sheet.cell(row=row_index, column=2).value)
        menu = decision_menu_for(
            filename,
            supplier,
            source,
            tier_reference,
        )
        formula = (
            "DECISION_SIMPLE"
            if len(menu.choices) <= len(DECISION_SIMPLE_CHOICES)
            and menu.profile
            in {"TIERS_CONNU", "ARDOISE"}
            else "DECISION_BANQUE_INCONNUE"
        )
        if formula != current_formula:
            if current_formula and row_index > start:
                blocks.append((current_formula, start, row_index - 1))
            current_formula = formula
            start = row_index
    if current_formula and last_row >= start:
        blocks.append((current_formula, start, last_row))
    return blocks
    _add_list_validation(sheet, EXERCISE_COLUMN, EXERCISE_CHOICES, last_row)
    _add_list_validation(
        sheet, MONTH_COLUMN, DEFERRED_MONTH_CHOICES, last_row
    )
    _add_list_validation(sheet, REPORT_COLUMN, REPORT_CHOICES, last_row)
    if alias_formula:
        _add_formula_validation(
            sheet, ALIAS_TARGET_COLUMN, alias_formula, last_row
        )
    else:
        _add_list_validation(
            sheet, ALIAS_TARGET_COLUMN, _alias_choices(), last_row
        )


def _existing_readable_rows(path: Path) -> list[dict[str, Any]]:
    """Read the current workbook rows by header name (non-destructive merge)."""
    if not path.is_file():
        return []
    try:
        from openpyxl import load_workbook

        workbook = load_workbook(path, data_only=True)
    except Exception:  # noqa: BLE001 - a locked/corrupt file is rebuilt
        return []
    try:
        sheet = workbook.active
        headers = {
            _text(cell.value): cell.column
            for cell in next(sheet.iter_rows(min_row=1, max_row=1))
            if _text(cell.value)
        }
        rows: list[dict[str, Any]] = []
        for row in sheet.iter_rows(min_row=2):
            values = {
                header: row[column - 1].value
                for header, column in headers.items()
            }
            if not any(_text(value) for value in values.values()):
                continue
            link_cell = (
                row[headers.get("Pièce PDF (Clic)", 1) - 1]
                if "Pièce PDF (Clic)" in headers
                else None
            )
            link = link_cell.hyperlink if link_cell is not None else None
            values["_link"] = link.target if link is not None else None
            rows.append(values)
        return rows
    finally:
        workbook.close()


def _readable_row_key(values: dict[str, Any]) -> str:
    for header in ("SHA-256", "Nom Fichier", "N° Pièce"):
        value = _text(values.get(header))
        if value:
            return f"{header}:{value}"
    return ""


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
        exercise_year = (
            str(row.exercise_year or "").strip()
            or (
                f"{row.document_date.year:04d}"
                if row.document_date is not None
                else ""
            )
        )
        amount_ttc = _amount_cell(row.amount_ttc)
        if amount_ttc == "":
            amount_ttc = 0.0
        subcategory = (
            str(row.subcategory or "").strip()
            or str(row.extra.get("subcategory") or "").strip()
        )
        source = str(row.source or "").strip() or (
            "BANQUE_OFX"
            if _text(row.filename).upper().startswith(
                ("TIER__", "INSTRUMENT__")
            )
            else "PIECE_PDF"
        )
        label = (
            str(row.label or "").strip()
            or str(row.document_number or "").strip()
            or _text(row.filename)
        )
        decision_value = _decision_value_for_row(row)
        values = (
            row.date_label or _date_cell(row.document_date),
            source,
            row.filename,
            row.supplier,
            label,
            amount_ttc,
            row.reason,
            row.action,
            row.category_value,
            subcategory,
            exercise_year,
            row.extra.get("payment_source", ""),
            decision_value,
        )
        hidden_values = (
            row.extra.get("ai_category", row.category_value),
            row.extra.get("ai_action", row.action),
            row.sha256,
            row.filename,
            row.extra.get("ai_subcategory", subcategory),
            row.extra.get("fiscal_year_target", ""),
            row.extra.get("deferred_month", "NON"),
            row.extra.get("report", "NON"),
            row.extra.get("tier_alias_target", ""),
            row.extra.get("counterparty_account", ""),
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
            if offset % 2 == 0 and column not in (7, 8):
                cell.fill = band_fill
        for offset_hidden, value in enumerate(
            hidden_values, start=len(READABLE_HEADERS) + 1
        ):
            cell = sheet.cell(row=offset, column=offset_hidden, value=value)
            cell.alignment = Alignment(vertical="top")
        status_fill = status_fills.get(row.status_value)
        if status_fill is not None:
            sheet.cell(row=offset, column=13).fill = status_fill
        link = _link_for(path, row.piece_pdf)
        if link:
            cell = sheet.cell(row=offset, column=3)
            cell.value = row.filename
            cell.hyperlink = link
            cell.font = Font(color="0563C1", underline="single")
            cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            hidden_link = sheet.cell(
                row=offset, column=len(READABLE_HEADERS) + len(HIDDEN_HEADERS)
            )
            hidden_link.value = link
            hidden_link.hyperlink = link
            hidden_link.font = Font(color="0563C1", underline="single")

    last_row = max(sheet.max_row, 1)
    tier_reference = _tier_reference()
    _write_decision_lists(workbook, tier_reference)
    alias_choices = _alias_choices()
    alias_formula = None
    if alias_choices:
        from openpyxl.workbook.defined_name import DefinedName

        lists = workbook.create_sheet("LISTES_TIERS")
        for index, choice in enumerate(alias_choices, start=1):
            lists.cell(row=index, column=1, value=choice)
        lists.sheet_state = "hidden"
        workbook.defined_names.add(
            DefinedName(
                "TIERS_ALIAS",
                attr_text=f"LISTES_TIERS!$A$1:$A${len(alias_choices)}",
            )
        )
        alias_formula = "TIERS_ALIAS"
    _install_validations(
        sheet,
        last_row,
        alias_formula=alias_formula,
        decision_blocks=_decision_blocks(sheet, last_row, tier_reference),
    )
    sheet.auto_filter.ref = (
        f"A1:{get_column_letter(len(READABLE_HEADERS))}{last_row}"
    )
    sheet.freeze_panes = "A2"
    widths = (12, 14, 46, 28, 32, 14, 60, 40, 26, 26, 12, 20, 14)
    for column, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    for column in range(len(READABLE_HEADERS) + 1,
                        len(READABLE_HEADERS) + len(HIDDEN_HEADERS) + 1):
        sheet.column_dimensions[get_column_letter(column)].hidden = True
    workbook.properties.creator = "DSH-Core P27-nonies"
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


_DECISION_KEYWORDS: tuple[str, ...] = (
    "frais generaux",
    "informatique",
    "saas",
    "honoraires",
    "juridique",
    "exercice 2026",
    "exercice 2025",
    "valide",
    "ok",
    "note de frais",
    "accepter",
    "paiement interne",
    "compte courant",
    "assurance",
    "cadeaux",
)

_SUBCATEGORY_VALUES: frozenset[str] = frozenset(SUBCATEGORY_CHOICES)


def _normalise_subcategory(value: Any) -> str | None:
    text = _normalize_label(value)
    if not text:
        return None
    for choice in SUBCATEGORY_CHOICES:
        if _normalize_label(choice) == text:
            return choice
    return None


def _tier_reference() -> Any:
    try:
        from services.tier_reference_service import TierReferenceService

        service = TierReferenceService()
        service.bootstrap(write_missing=False)
        return service
    except Exception:  # noqa: BLE001 - tier reference is optional
        return None


def _is_human_decided(
    values: dict[str, str], tier_reference: Any = None
) -> bool:
    """Return ``True`` as soon as the director touched one decision column."""
    observed_category = _first_value(values, "Catégorie", "Catégorie Proposée")
    ai_category = values.get("Catégorie IA", "")
    alias = values.get(
        "Rattacher comme ALIAS de (ou NOUVEAU_TIERS)", ""
    )
    action = _first_value(values, "Action attendue", "Action / Décision")
    action_ia = values.get("Action IA", "")
    decision = _first_value(values, "Décision")
    supplier = values.get("Fournisseur / Tiers", "")
    if alias.strip():
        return True
    if _explicit_status(decision) is not None:
        return True
    if ai_category:
        if _normalize_label(observed_category) != _normalize_label(ai_category):
            return True
    elif observed_category and observed_category.strip().upper() != "DIVERS_A_CLASSER":
        return True
    if action and action_ia and _normalize_label(action) != _normalize_label(action_ia):
        folded = _normalize_label(action)
        if any(keyword in folded for keyword in _DECISION_KEYWORDS):
            return True
    if tier_reference is not None and supplier:
        try:
            record = tier_reference.resolve(supplier)
        except Exception:  # noqa: BLE001
            record = None
        if record is not None and getattr(record, "authorized", False):
            return True
    return False


def _read_decisions_from_workbook(
    path: Path, tier_reference: Any = None
) -> list[ReadableDecision]:
    from openpyxl import load_workbook

    if not path.is_file():
        return []
    try:
        workbook = load_workbook(path, data_only=True)
    except (OSError, PermissionError):
        return []
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
            if re.fullmatch(r"[0-9a-fA-F]{64}", sha.strip()) is None:
                sha = ""
            filename = (
                values.get("Nom Fichier", "")
                or values.get("Nom fichier", "")
                or values.get("N° Pièce", "")
            )
            if (
                not filename
                or "DEJA_PAYEE" in filename.upper()
                or "IMPAYEE" in filename.upper()
                or "/" in filename
            ):
                filename = values.get("N° Pièce", "") or filename
            if not sha and not filename:
                continue
            action = _first_value(
                values, "Action attendue", "Action / Décision"
            )
            action_ia = values.get("Action IA", "")
            observed_category = _first_value(
                values, "Catégorie", "Catégorie Proposée"
            )
            ai_category = values.get("Catégorie IA", "")
            subcategory = _normalise_subcategory(
                _first_value(
                    values, "Sous-Catégorie", "Sous-catégorie Frais"
                )
            )
            decision = _first_value(values, "Décision")
            category = normalize_category(observed_category)
            status = _explicit_status(decision)
            if status is None and action_ia:
                if _normalize_label(action) != _normalize_label(action_ia):
                    status = normalize_status(action)
            if status is None and action:
                status = _explicit_status(action)
            if status is None and category is not None and ai_category:
                if _normalize_label(observed_category) != _normalize_label(ai_category):
                    status = DocumentStatus.VALIDE.value
            if status is None and subcategory is not None:
                ai_subcategory = _normalise_subcategory(
                    values.get("Sous-Catégorie IA", "")
                )
                if (
                    ai_subcategory is not None
                    and subcategory != ai_subcategory
                ):
                    status = DocumentStatus.VALIDE.value
            if status is None and _is_human_decided(
                values,
                None
                if filename.upper().startswith("ARDOISE_")
                else tier_reference,
            ):
                status = DocumentStatus.VALIDE.value
            if status is None:
                status = DocumentStatus.EN_ATTENTE.value
            decisions.append(
                ReadableDecision(
                    sha256=sha,
                    filename=filename,
                    status=status,
                    category=category,
                    subcategory=subcategory,
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
                    exercise_year=extract_year(
                        values.get("Exercice Année", "")
                    ),
                    deferred_month=normalize_month(
                        values.get("Mois décalé", "")
                    ),
                    report=normalize_report(values.get("Report", "")),
                    tier_alias_target=values.get(
                        "Rattacher comme ALIAS de (ou NOUVEAU_TIERS)", ""
                    ),
                    action=action,
                    decision=decision,
                )
            )
        return decisions
    finally:
        workbook.close()


def _decision_paths(output_path: str | os.PathLike[str]) -> list[Path]:
    path = Path(output_path)
    if path.is_dir():
        directory = path
    else:
        directory = path.parent
    candidates: list[Path] = []
    if path.is_file():
        candidates.append(path)
    for pattern in (
        "_A_COMPLETER.xlsx",
        "_A_COMPLETER_MIS_A_JOUR*.xlsx",
        "_A_COMPLETER__*.xlsx",
    ):
        candidates.extend(directory.glob(pattern))
    unique = sorted(
        dict.fromkeys(candidates),
        key=lambda item: item.stat().st_mtime if item.is_file() else 0,
        reverse=True,
    )
    return unique


def read_readable_decisions(
    output_path: str | os.PathLike[str],
    *,
    tier_reference: Any = None,
) -> tuple[ReadableDecision, ...]:
    """Read and merge every human edit, newest workbook first.

    The director's edits are never overwritten: a line is considered decided
    as soon as the category, the alias column, the action or the tier
    reference changed. ``_A_COMPLETER_MIS_A_JOUR*.xlsx`` files are merged and
    then deleted.
    """
    reference = tier_reference if tier_reference is not None else _tier_reference()
    merged: dict[str, ReadableDecision] = {}
    for path in _decision_paths(output_path):
        for decision in _read_decisions_from_workbook(path, reference):
            key = decision.sha256 or decision.filename
            if not key:
                continue
            current = merged.get(key)
            if current is None or (
                current.status != DocumentStatus.VALIDE.value
                and decision.status == DocumentStatus.VALIDE.value
            ):
                merged[key] = decision
    for path in _decision_paths(output_path):
        if "_MIS_A_JOUR" in path.name:
            path.unlink(missing_ok=True)
    return tuple(merged.values())


def _explicit_status(value: Any) -> str | None:
    token = re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())
    if token in {
        "VALIDE",
        "VALIDER",
        "OK",
        "ACCEPTER",
        "DEJAPAYEE2025",
        "IMPAYEE",
    }:
        return DocumentStatus.VALIDE.value
    if token in {"REPORTERMOISSUIVANT"}:
        return DocumentStatus.EN_ATTENTE.value
    if token in {"REJETE", "REJETER", "KO", "REFUSER"}:
        return DocumentStatus.REJETE.value
    return None


def _normalize_label(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


__all__ = [
    "HIDDEN_HEADERS",
    "LEGACY_READABLE_HEADERS",
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
