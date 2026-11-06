"""``_A_COMPLETER.xlsx`` workbook manager: non-destructive, lock-aware (P14.2).

The workbook is the single structured human input channel for documents parked
in ``02_A_COMPLETER`` (spec P14, section 1.4). Frozen column order: ``ID
document``, ``Hash SHA-256``, ``Nom fichier``, ``Champs manquants``, ``Catégorie
suggérée``, ``Statut``. The strict primary key is ``Hash SHA-256``: re-ingestion
is idempotent and never destroys human input. A row whose ``Statut`` is anything
other than ``EN_ATTENTE`` is left strictly untouched (human-owned), rows are
never deleted and only ``Champs manquants`` / ``Catégorie suggérée`` are ever
refreshed on a pipeline-owned row. The human may move a ``VALIDE`` row to
``REJETE`` ("annule et remplace"); ``align_statut`` accepts every status
transition for the P14.3 synchroniser. Writes are atomic (temp file then
``os.replace``) for OneDrive compatibility and are refused while an Excel lock
file (``~$*.xlsx``) is present, raising the controlled ``WorkbookLockedError``.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from schemas.folder_state_schema import (
    DEFERRED_MONTHS,
    DIRIGEANT_COUNTERPART_ACCOUNT,
    DocumentCategory,
    DocumentStatus,
    ExpenseSubCategory,
    FiscalYearMode,
    MissingFieldEnum,
    PaymentSource,
    counterparty_account_for_payment_source,
    normalize_payment_source,
)
from services.folder_tree_service import (
    ensure_contained,
    resolve_flow_root,
    validate_tenant_id,
)
from services.pdf_splitter_and_linker import PIECE_LINK_HEADER
from services.acompleter_readable_service import READABLE_HEADERS, ReadableRow
from schemas.accounting_choices import (
    CATEGORY_CHOICES,
    COUNTERPARTY_ACCOUNT_CHOICES,
    DEFERRED_MONTH_CHOICES,
    EXERCISE_CHOICES,
    PAYMENT_SOURCE_CHOICES,
    REPORT_CHOICES,
    STATUS_CHOICES,
    SUBCATEGORY_CHOICES,
    excel_list,
    normalize_payment_source as normalize_official_payment_source,
)

WORKBOOK_NAME = "_A_COMPLETER.xlsx"
SHEET_NAME = "A_COMPLETER"
HEADERS: tuple[str, ...] = (
    "ID document",
    "Hash SHA-256",
    "Nom fichier",
    "Champs manquants",
    "Catégorie suggérée",
    "Statut",
)
# P17 extension columns are appended after the frozen six legacy columns so
# every existing workbook and every existing reader keeps working unchanged.
P17_EXTENSION_HEADERS: tuple[str, ...] = (
    "Sous-catégorie Frais",
    "Invité Restaurant",
    "Lieu Restaurant",
    "Repère Classement",
    "Statut Anti-Doublon TVA",
    "Mode Exercice Fiscal",
    "Exercice Cible",
    "Mois Décalé",
    "Source de Paiement",
    "Compte de Contrepartie",
    PIECE_LINK_HEADER,
    "Report",
    "Rattacher comme ALIAS de (ou NOUVEAU_TIERS)",
)
EXTENDED_HEADERS: tuple[str, ...] = HEADERS + P17_EXTENSION_HEADERS
# P22 appends the manager-facing readable columns. They are only materialised
# by ``write_readable_rows`` so the frozen P14/P17 creation contract is intact.
P22_EXTENSION_HEADERS: tuple[str, ...] = READABLE_HEADERS
P22_FIRST_COLUMN = len(EXTENDED_HEADERS) + 1
P22_LAST_COLUMN = P22_FIRST_COLUMN + len(P22_EXTENSION_HEADERS) - 1
LOCK_GLOB = "~$*.xlsx"
TEMP_NAME = "_A_COMPLETER.tmp.xlsx"
CHAMPS_SEPARATOR = ", "
PIPELINE_STATUS = DocumentStatus.EN_ATTENTE.value

OUTCOME_CREATED = "created"
OUTCOME_UPDATED = "updated"
OUTCOME_UNCHANGED = "unchanged"
OUTCOME_PRESERVED = "preserved"

_SHA256 = re.compile(r"[0-9a-f]{64}")

_VALIDATION_LISTS: dict[str, tuple[str, ...]] = {
    "catégorie suggérée": CATEGORY_CHOICES,
    "catégorie proposée": CATEGORY_CHOICES,
    "statut": STATUS_CHOICES,
    "sous-catégorie frais": SUBCATEGORY_CHOICES,
    "mode exercice fiscal": ("ACTUEL", "SUIVANT", "PRECEDENT"),
    "exercice cible": EXERCISE_CHOICES,
    "mois décalé": DEFERRED_MONTH_CHOICES,
    "source de paiement": PAYMENT_SOURCE_CHOICES,
    "compte de contrepartie": COUNTERPARTY_ACCOUNT_CHOICES,
    "report": REPORT_CHOICES,
    "rattacher comme alias de (ou nouveau_tiers)": (
        "CREER_NOUVEAU_FOURNISSEUR",
        "CREER_NOUVEAU_CLIENT",
        "RESTAURANTS_REPAS",
        "PEAGES_AUTOROUTES",
        "STATIONS_CARBURANT",
    ),
}


def _tier_alias_choices() -> tuple[str, ...]:
    try:
        from services.tier_reference_service import TierReferenceService

        return TierReferenceService().bootstrap(
            write_missing=False
        ).alias_targets()
    except Exception:  # noqa: BLE001
        return _VALIDATION_LISTS["rattacher comme alias de (ou nouveau_tiers)"]


def _normalized_header(value: object) -> str:
    return " ".join(_cell_text(value).strip().casefold().split())


def install_data_validations(sheet: Any, last_row: int) -> None:
    """Add the official dropdowns to every matching P17/P24 column."""
    from openpyxl.worksheet.datavalidation import DataValidation

    try:
        sheet.data_validations.dataValidation.clear()
    except AttributeError:
        pass
    for cell in sheet[1]:
        header = _normalized_header(cell.value)
        choices = _VALIDATION_LISTS.get(header)
        if header == "rattacher comme alias de (ou nouveau_tiers)":
            choices = _tier_alias_choices()
        if choices is None:
            continue
        validation = DataValidation(
            type="list",
            formula1=excel_list(choices),
            allow_blank=True,
            showDropDown=False,
        )
        validation.error = "Choisissez une valeur proposée dans la liste."
        validation.errorTitle = "Choix invalide"
        sheet.add_data_validation(validation)
        validation.add(
            f"{cell.column_letter}2:{cell.column_letter}{max(last_row, 2)}"
        )


class AcompleterWorkbookError(ValueError):
    """Invalid workbook operation; ``code`` is stable for tests and logs."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


class WorkbookLockedError(AcompleterWorkbookError):
    """An Excel / OneDrive lock file (``~$*.xlsx``) blocks reads and writes."""

    def __init__(self, lock_path: Path) -> None:
        super().__init__("WORKBOOK_LOCKED", str(lock_path))
        self.lock_path = lock_path


@dataclass(frozen=True)
class WorkbookRow:
    """One data row of ``_A_COMPLETER.xlsx`` (human-tolerant raw values)."""

    row_number: int
    id_document: str
    sha256: str
    nom_fichier: str
    champs_manquants: str
    categorie_suggeree: str
    statut: str


def normalize_sha256(value: object) -> str:
    """Return the canonical lowercase hex digest or raise ``INVALID_SHA256``."""
    if not isinstance(value, str):
        raise AcompleterWorkbookError("INVALID_SHA256", repr(value))
    text = value.strip().lower()
    if _SHA256.fullmatch(text) is None:
        raise AcompleterWorkbookError("INVALID_SHA256", repr(value))
    return text


def derive_document_id(sha256: str) -> str:
    """Deterministic ``ID document`` derived from the content hash.

    Uses ``uuid5(NAMESPACE_URL, ...)`` exactly like
    ``PersistenceManager.build_invoice_id`` (contract v1.0.2), so every DSH
    identifier stays hash-derived and stable.
    """
    return str(uuid5(NAMESPACE_URL, f"doc:{normalize_sha256(sha256)}"))


def format_champs_manquants(values: Iterable[object]) -> str:
    """Join missing-field tokens in order; each token must be a known field."""
    tokens: list[str] = []
    for value in values:
        try:
            tokens.append(MissingFieldEnum(value).value)
        except (ValueError, TypeError) as exc:
            raise AcompleterWorkbookError("INVALID_CHAMP", repr(value)) from exc
    return CHAMPS_SEPARATOR.join(tokens)


def _clean_nom_fichier(value: object) -> str:
    if not isinstance(value, str):
        raise AcompleterWorkbookError("INVALID_NOM_FICHIER", repr(value))
    text = value.strip()
    if not text or "/" in text or "\\" in text or "\x00" in text:
        raise AcompleterWorkbookError("INVALID_NOM_FICHIER", repr(value))
    return text


def _clean_category(value: object) -> str:
    try:
        return DocumentCategory(value).value
    except (ValueError, TypeError) as exc:
        raise AcompleterWorkbookError("INVALID_CATEGORY", repr(value)) from exc


def _cell_text(value: object) -> str:
    return "" if value is None else str(value)


def _close(wb: Any) -> None:
    close = getattr(wb, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class AcompleterWorkbook:
    """Create, read and upsert ``_A_COMPLETER.xlsx`` inside one directory."""

    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self._directory = Path(directory)

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def workbook_path(self) -> Path:
        return self._directory / WORKBOOK_NAME

    def find_lock_file(self) -> Path | None:
        """Return the active Excel / OneDrive lock file, if any."""
        if not self._directory.is_dir():
            return None
        locks = sorted(self._directory.glob(LOCK_GLOB))
        return locks[0] if locks else None

    def _require_no_lock(self) -> None:
        lock = self.find_lock_file()
        if lock is not None:
            raise WorkbookLockedError(lock)

    def ensure_workbook(self) -> Path:
        """Create the workbook (headers + filter) or validate the existing one."""
        self._require_no_lock()
        if self.workbook_path.exists():
            wb = self._load_workbook()
            _close(wb)
            return self.workbook_path
        self._directory.mkdir(parents=True, exist_ok=True)
        wb = self._new_workbook()
        self._atomic_save(wb)
        return self.workbook_path

    def read_rows(self) -> list[WorkbookRow]:
        """Return every non-empty data row; human values stay raw text."""
        self._require_no_lock()
        if not self.workbook_path.exists():
            raise AcompleterWorkbookError("WORKBOOK_NOT_FOUND", str(self.workbook_path))
        wb = self._load_workbook()
        ws = wb[SHEET_NAME]
        rows: list[WorkbookRow] = []
        try:
            for index in range(2, ws.max_row + 1):
                cells = [
                    _cell_text(ws.cell(row=index, column=col).value)
                    for col in range(1, len(HEADERS) + 1)
                ]
                if not any(cells):
                    continue
                rows.append(
                    WorkbookRow(
                        row_number=index,
                        id_document=cells[0],
                        sha256=cells[1],
                        nom_fichier=cells[2],
                        champs_manquants=cells[3],
                        categorie_suggeree=cells[4],
                        statut=cells[5],
                    )
                )
        finally:
            _close(wb)
        return rows

    def upsert(
        self,
        sha256: str,
        nom_fichier: str,
        champs_manquants: Iterable[object],
        categorie_suggeree: DocumentCategory | str,
        *,
        piece_pdf: str | None = None,
    ) -> str:
        """Idempotent, non-destructive upsert keyed on the content hash.

        Returns ``created``, ``updated``, ``unchanged`` or ``preserved``. The
        ``Statut`` column is written once as ``EN_ATTENTE`` at creation and is
        never modified by ``upsert`` (P14.3 mirrors manual moves through
        ``align_statut`` only): it belongs to the human.
        """
        key = normalize_sha256(sha256)
        name = _clean_nom_fichier(nom_fichier)
        champs_text = format_champs_manquants(champs_manquants)
        category = _clean_category(categorie_suggeree)

        self.ensure_workbook()
        wb = self._load_workbook()
        ws = wb[SHEET_NAME]
        try:
            link_column = (
                self._ensure_piece_link_column(ws) if piece_pdf else None
            )
            target_row = None
            for index in range(2, ws.max_row + 1):
                if _cell_text(ws.cell(row=index, column=2).value).strip().lower() == key:
                    target_row = index
                    break

            if target_row is None:
                ws.append(
                    [
                        derive_document_id(key),
                        key,
                        name,
                        champs_text,
                        category,
                        PIPELINE_STATUS,
                    ]
                )
                target_row = ws.max_row
                if link_column is not None and piece_pdf:
                    self._write_piece_link(ws, target_row, link_column, piece_pdf)
                ws.auto_filter.ref = f"A1:F{ws.max_row}"
                self._atomic_save(wb)
                return OUTCOME_CREATED

            current_status = _cell_text(ws.cell(row=target_row, column=6).value).strip()
            if current_status != PIPELINE_STATUS:
                return OUTCOME_PRESERVED

            changed = False
            for column, new_value in ((4, champs_text), (5, category)):
                cell = ws.cell(row=target_row, column=column)
                if _cell_text(cell.value).strip() != new_value:
                    cell.value = new_value
                    changed = True
            if link_column is not None and piece_pdf:
                cell = ws.cell(row=target_row, column=link_column)
                if _cell_text(cell.value).strip() != piece_pdf:
                    self._write_piece_link(ws, target_row, link_column, piece_pdf)
                    changed = True
            if not changed:
                return OUTCOME_UNCHANGED
            self._atomic_save(wb)
            return OUTCOME_UPDATED
        finally:
            _close(wb)

    def write_readable_rows(
        self,
        rows: Iterable[ReadableRow],
        *,
        update_status: bool = False,
    ) -> int:
        """Materialise the P22 readable columns on the legacy workbook.

        Columns ``A``/``B`` (UUID + SHA-256) and the old ``Champs manquants`` /
        ``Catégorie suggérée`` duplicates are hidden; the manager sees the
        P22 headers appended at column ``R``. Human ``REJETE`` rows are never
        overwritten. Returns the number of rows written.
        """
        from openpyxl.styles import Alignment, Font

        self.ensure_workbook()
        wb = self._load_workbook()
        ws = wb[SHEET_NAME]
        written = 0
        try:
            self._ensure_extension_headers(ws)
            for column, header in enumerate(
                P22_EXTENSION_HEADERS, start=P22_FIRST_COLUMN
            ):
                cell = ws.cell(row=1, column=column, value=header)
                cell.font = Font(bold=True, color="FFFFFF")
                from openpyxl.styles import PatternFill

                cell.fill = PatternFill("solid", fgColor="1F4E78")
                cell.alignment = Alignment(
                    horizontal="center", vertical="center", wrap_text=True
                )
            for letter in ("A", "B", "D", "E", "Q"):
                ws.column_dimensions[letter].hidden = True
            widths = {
                "R": 13,
                "S": 24,
                "T": 13,
                "U": 26,
                "V": 18,
                "W": 12,
                "X": 24,
                "Y": 62,
                "Z": 42,
                "AA": 22,
                "AB": 22,
                "AC": 14,
                "AD": 14,
                "AE": 10,
                "AF": 24,
                "AG": 32,
            }
            for letter, width in widths.items():
                ws.column_dimensions[letter].width = width
            install_data_validations(ws, ws.max_row)

            for row in rows:
                key = normalize_sha256(row.sha256)
                target = self._find_row(ws, key)
                if target is None:
                    ws.append(
                        [
                            derive_document_id(key),
                            key,
                            row.filename,
                            "",
                            row.category_value,
                            row.status_value,
                        ]
                    )
                    target = ws.max_row
                current_status = _cell_text(
                    ws.cell(row=target, column=6).value
                ).strip()
                if (
                    update_status
                    and current_status
                    in (DocumentStatus.EN_ATTENTE.value, DocumentStatus.VALIDE.value)
                    and row.status_value
                    in (DocumentStatus.EN_ATTENTE.value, DocumentStatus.VALIDE.value)
                ):
                    ws.cell(row=target, column=6, value=row.status_value)
                values = (
                    row.document_date,
                    row.supplier,
                    row.document_number,
                    float(row.amount_ttc)
                    if row.amount_ttc is not None
                    else None,
                    row.category_value,
                    row.reason,
                    row.action,
                    row.extra.get("payment_source", ""),
                    row.extra.get("counterparty_account", ""),
                    row.extra.get("fiscal_year_target", ""),
                    row.extra.get("deferred_month", ""),
                    row.extra.get("report", ""),
                    row.extra.get("tier_alias_target", ""),
                )
                for offset, value in enumerate(values):
                    cell = ws.cell(
                        row=target, column=P22_FIRST_COLUMN + offset, value=value
                    )
                    cell.alignment = Alignment(vertical="top", wrap_text=True)
                    if isinstance(value, float):
                        cell.number_format = "#,##0.00"
                link = str(row.piece_pdf or "").replace("\\", "/").strip()
                link_cell = ws.cell(
                    row=target, column=P22_LAST_COLUMN
                )
                if link and not link.startswith("/") and not re.match(
                    r"^[A-Za-z]:", link
                ):
                    link_cell.value = link
                    link_cell.hyperlink = link
                    link_cell.font = Font(color="0563C1", underline="single")
                written += 1
            self._atomic_save(wb)
            return written
        finally:
            _close(wb)

    def align_statut(self, sha256: str, statut: DocumentStatus | str) -> bool:
        """Mirror a manual move into the ``Statut`` column (P14.3 sync only).

        The single pipeline-side write to the human-owned ``Statut`` cell; used
        to align rows whose file the human moved by hand and to record the
        ``VALIDE`` -> ``REJETE`` transition ("annule et remplace"). Returns
        ``True`` when the cell changed. Raises ``WORKBOOK_LOCKED`` /
        ``WORKBOOK_NOT_FOUND`` / ``ROW_NOT_FOUND`` / ``INVALID_STATUS`` as
        controlled errors.
        """
        key = normalize_sha256(sha256)
        try:
            value = DocumentStatus(statut).value
        except (ValueError, TypeError) as exc:
            raise AcompleterWorkbookError("INVALID_STATUS", repr(statut)) from exc
        self._require_no_lock()
        if not self.workbook_path.exists():
            raise AcompleterWorkbookError(
                "WORKBOOK_NOT_FOUND", str(self.workbook_path)
            )
        wb = self._load_workbook()
        ws = wb[SHEET_NAME]
        try:
            for index in range(2, ws.max_row + 1):
                if _cell_text(ws.cell(row=index, column=2).value).strip().lower() == key:
                    cell = ws.cell(row=index, column=6)
                    if _cell_text(cell.value).strip() == value:
                        return False
                    cell.value = value
                    self._atomic_save(wb)
                    return True
        finally:
            _close(wb)
        raise AcompleterWorkbookError("ROW_NOT_FOUND", key)

    def update_p17_metadata(
        self,
        sha256: str,
        *,
        expense_subcategory: ExpenseSubCategory | str | None = None,
        invite_restaurant: object = None,
        lieu_restaurant: object = None,
        is_classified_marker: object = None,
        tva_dedup_status: object = None,
        fiscal_year_mode: FiscalYearMode | str | None = None,
        fiscal_year_target: object = None,
        deferred_month_override: object = None,
        payment_source: PaymentSource | str | None = None,
        counterparty_account: object = None,
        report: object = None,
        alias_target: object = None,
    ) -> bool:
        """Write the P17 columns of one existing row (pipeline-owned cells).

        The frozen six-column contract is untouched: new values always land in
        the appended extension columns. Returns ``True`` when at least one cell
        changed. Raises ``WORKBOOK_LOCKED`` / ``WORKBOOK_NOT_FOUND`` /
        ``ROW_NOT_FOUND`` / ``INVALID_P17_VALUE`` as controlled errors.
        """
        key = normalize_sha256(sha256)
        raw_values = {
            "expense_subcategory": expense_subcategory,
            "invite_restaurant": invite_restaurant,
            "lieu_restaurant": lieu_restaurant,
            "is_classified_marker": is_classified_marker,
            "tva_dedup_status": tva_dedup_status,
            "fiscal_year_mode": fiscal_year_mode,
            "fiscal_year_target": fiscal_year_target,
            "deferred_month_override": deferred_month_override,
            "payment_source": payment_source,
            "counterparty_account": counterparty_account,
            "report": report,
            "alias_target": alias_target,
        }
        values = self._normalize_p17_values(**raw_values)
        raw_for_header = {
            "Sous-catégorie Frais": expense_subcategory,
            "Invité Restaurant": invite_restaurant,
            "Lieu Restaurant": lieu_restaurant,
            "Repère Classement": is_classified_marker,
            "Statut Anti-Doublon TVA": tva_dedup_status,
            "Mode Exercice Fiscal": fiscal_year_mode,
            "Exercice Cible": fiscal_year_target,
            "Mois Décalé": deferred_month_override,
            "Source de Paiement": payment_source,
            "Compte de Contrepartie": counterparty_account,
            "Report": report,
            "Rattacher comme ALIAS de (ou NOUVEAU_TIERS)": alias_target,
        }
        selected = {
            header: value
            for header, value in values.items()
            if raw_for_header.get(header) is not None
            or (
                header == "Compte de Contrepartie"
                and payment_source is not None
            )
        }
        self._require_no_lock()
        if not self.workbook_path.exists():
            raise AcompleterWorkbookError(
                "WORKBOOK_NOT_FOUND", str(self.workbook_path)
            )
        wb = self._load_workbook()
        ws = wb[SHEET_NAME]
        try:
            extension_added = self._ensure_extension_headers(ws)
            target_row = self._find_row(ws, key)
            if target_row is None:
                raise AcompleterWorkbookError("ROW_NOT_FOUND", key)
            changed = extension_added
            for header, value in selected.items():
                column = EXTENDED_HEADERS.index(header) + 1
                cell = ws.cell(row=target_row, column=column)
                if _cell_text(cell.value).strip() != value:
                    cell.value = value
                    changed = True
            if changed:
                self._atomic_save(wb)
            return changed
        finally:
            _close(wb)

    def read_p17_metadata(self, sha256: str) -> dict[str, str] | None:
        """Return the P17 extension cells of one row (``None`` if absent)."""
        key = normalize_sha256(sha256)
        self._require_no_lock()
        if not self.workbook_path.exists():
            raise AcompleterWorkbookError(
                "WORKBOOK_NOT_FOUND", str(self.workbook_path)
            )
        wb = self._load_workbook()
        ws = wb[SHEET_NAME]
        try:
            target_row = self._find_row(ws, key)
            if target_row is None:
                return None
            if ws.max_column < len(EXTENDED_HEADERS):
                return {
                    header: ""
                    for header in P17_EXTENSION_HEADERS
                    if header != PIECE_LINK_HEADER
                }
            return {
                header: _cell_text(
                    ws.cell(
                        row=target_row,
                        column=EXTENDED_HEADERS.index(header) + 1,
                    ).value
                )
                for header in P17_EXTENSION_HEADERS
                if header != PIECE_LINK_HEADER
            }
        finally:
            _close(wb)

    @staticmethod
    def _normalize_p17_values(**raw_values: object) -> dict[str, str]:
        def text(value: object) -> str:
            return "" if value is None else str(value).strip()

        def subcategory(value: object) -> str:
            if value is None or text(value) == "":
                return ""
            try:
                return ExpenseSubCategory(value).value
            except (TypeError, ValueError) as exc:
                raise AcompleterWorkbookError(
                    "INVALID_P17_VALUE", f"expense_subcategory={value!r}"
                ) from exc

        def fiscal_mode(value: object) -> str:
            if value is None or text(value) == "":
                return ""
            try:
                return FiscalYearMode(value).value
            except (TypeError, ValueError) as exc:
                raise AcompleterWorkbookError(
                    "INVALID_P17_VALUE", f"fiscal_year_mode={value!r}"
                ) from exc

        def month(value: object) -> str:
            if value is None or text(value) == "":
                return "NON"
            candidate = text(value).upper()
            if candidate == "NON":
                return "NON"
            if candidate.isdigit():
                candidate = candidate.zfill(2)
            if candidate not in DEFERRED_MONTH_CHOICES:
                raise AcompleterWorkbookError(
                    "INVALID_P17_VALUE", f"deferred_month_override={value!r}"
                )
            return candidate

        def payment(value: object, account: object) -> tuple[str, str]:
            if value is None or text(value) == "":
                if text(account).upper() == DIRIGEANT_COUNTERPART_ACCOUNT:
                    return (
                        PaymentSource.COMPTE_PERSO_DIRIGEANT.value,
                        DIRIGEANT_COUNTERPART_ACCOUNT,
                    )
                return "", ""
            official = normalize_official_payment_source(value)
            if official == "COMPTE_PERSO_DIRIGEANT":
                source = PaymentSource.COMPTE_PERSO_DIRIGEANT
            elif official == "BANQUE_PRO":
                source = PaymentSource.BANQUE_PRO
            else:
                source = normalize_payment_source(value)
            if source is None:
                raise AcompleterWorkbookError(
                    "INVALID_P17_VALUE", f"payment_source={value!r}"
                )
            mandatory = counterparty_account_for_payment_source(source)
            if mandatory is not None:
                return source.value, mandatory
            return source.value, text(account)

        payment_source_value, counterparty_account_value = payment(
            raw_values.get("payment_source"),
            raw_values.get("counterparty_account"),
        )

        return {
            "Sous-catégorie Frais": subcategory(
                raw_values.get("expense_subcategory")
            ),
            "Invité Restaurant": text(raw_values.get("invite_restaurant")),
            "Lieu Restaurant": text(raw_values.get("lieu_restaurant")),
            "Repère Classement": text(raw_values.get("is_classified_marker")),
            "Statut Anti-Doublon TVA": text(
                raw_values.get("tva_dedup_status")
            ),
            "Mode Exercice Fiscal": fiscal_mode(
                raw_values.get("fiscal_year_mode")
            ),
            "Exercice Cible": text(raw_values.get("fiscal_year_target")),
            "Mois Décalé": month(raw_values.get("deferred_month_override")),
            "Source de Paiement": payment_source_value,
            "Compte de Contrepartie": counterparty_account_value,
            "Report": text(raw_values.get("report")).upper() or "NON",
            "Rattacher comme ALIAS de (ou NOUVEAU_TIERS)": text(
                raw_values.get("alias_target")
            ),
        }

    @staticmethod
    def _ensure_piece_link_column(ws: Any) -> int:
        from openpyxl.styles import Font

        column = EXTENDED_HEADERS.index(PIECE_LINK_HEADER) + 1
        cell = ws.cell(row=1, column=column)
        if _cell_text(cell.value).strip() != PIECE_LINK_HEADER:
            cell.value = PIECE_LINK_HEADER
            cell.font = Font(bold=True)
        return column

    @staticmethod
    def _write_piece_link(
        ws: Any, row: int, column: int, piece_pdf: str
    ) -> None:
        from openpyxl.styles import Font

        link = str(piece_pdf or "").replace("\\", "/").strip()
        if not link or link.startswith("/") or re.match(r"^[A-Za-z]:", link):
            return
        cell = ws.cell(row=row, column=column)
        cell.value = link
        cell.hyperlink = link
        cell.font = Font(color="0563C1", underline="single")

    @staticmethod
    def _find_row(ws: Any, key: str) -> int | None:
        for index in range(2, ws.max_row + 1):
            if _cell_text(ws.cell(row=index, column=2).value).strip().lower() == key:
                return index
        return None

    @staticmethod
    def _ensure_extension_headers(ws: Any) -> bool:
        changed = False
        for offset, header in enumerate(P17_EXTENSION_HEADERS):
            column = len(HEADERS) + offset + 1
            cell = ws.cell(row=1, column=column)
            if _cell_text(cell.value).strip() != header:
                cell.value = header
                from openpyxl.styles import Font

                cell.font = Font(bold=True)
                changed = True
        return changed

    def _new_workbook(self) -> Any:
        from openpyxl import Workbook
        from openpyxl.styles import Font

        wb = Workbook()
        ws = wb.active
        ws.title = SHEET_NAME
        ws.append(list(EXTENDED_HEADERS))
        bold = Font(bold=True)
        for cell in ws[1]:
            cell.font = bold
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = f"A1:F{ws.max_row}"
        return wb

    def _load_workbook(self) -> Any:
        from openpyxl import load_workbook

        try:
            wb = load_workbook(self.workbook_path)
        except Exception as exc:
            raise AcompleterWorkbookError(
                "WORKBOOK_INVALID", str(self.workbook_path)
            ) from exc
        if SHEET_NAME not in wb.sheetnames:
            _close(wb)
            raise AcompleterWorkbookError(
                "WORKBOOK_INVALID", f"sheet {SHEET_NAME} missing"
            )
        ws = wb[SHEET_NAME]
        headers = tuple(
            _cell_text(ws.cell(row=1, column=col).value)
            for col in range(1, len(HEADERS) + 1)
        )
        if headers != HEADERS:
            _close(wb)
            raise AcompleterWorkbookError("WORKBOOK_HEADERS_MISMATCH", repr(headers))
        return wb

    def _atomic_save(self, wb: Any) -> None:
        temp = self._directory / TEMP_NAME
        try:
            wb.save(temp)
            _close(wb)
            try:
                os.replace(temp, self.workbook_path)
            except OSError:
                try:
                    shutil.copy2(temp, self.workbook_path)
                except OSError:
                    fallback = self._directory / (
                        f"{self.workbook_path.stem}_MIS_A_JOUR"
                        f"{self.workbook_path.suffix}"
                    )
                    index = 1
                    while fallback.exists():
                        fallback = self._directory / (
                            f"{self.workbook_path.stem}_MIS_A_JOUR_{index}"
                            f"{self.workbook_path.suffix}"
                        )
                        index += 1
                    shutil.copy2(temp, fallback)
        except OSError as exc:
            raise AcompleterWorkbookError(
                "WRITE_FAILED", str(self.workbook_path)
            ) from exc
        finally:
            _close(wb)
            if temp.exists():
                temp.unlink(missing_ok=True)


def tenant_workbook(
    tenant_id: str, root: str | os.PathLike[str] | None = None
) -> AcompleterWorkbook:
    """Bind the workbook service to ``<root>/<tenant_id>/`` (validated, no I/O)."""
    tenant = validate_tenant_id(tenant_id)
    base = resolve_flow_root(root).resolve()
    directory = ensure_contained(base, base / tenant)
    return AcompleterWorkbook(directory)
