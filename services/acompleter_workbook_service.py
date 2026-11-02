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
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from schemas.folder_state_schema import (
    DocumentCategory,
    DocumentStatus,
    MissingFieldEnum,
)
from services.folder_tree_service import (
    ensure_contained,
    resolve_flow_root,
    validate_tenant_id,
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
LOCK_GLOB = "~$*.xlsx"
TEMP_NAME = "_A_COMPLETER.tmp.xlsx"
CHAMPS_SEPARATOR = ", "
PIPELINE_STATUS = DocumentStatus.EN_ATTENTE.value

OUTCOME_CREATED = "created"
OUTCOME_UPDATED = "updated"
OUTCOME_UNCHANGED = "unchanged"
OUTCOME_PRESERVED = "preserved"

_SHA256 = re.compile(r"[0-9a-f]{64}")


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
            if not changed:
                return OUTCOME_UNCHANGED
            self._atomic_save(wb)
            return OUTCOME_UPDATED
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

    def _new_workbook(self) -> Any:
        from openpyxl import Workbook
        from openpyxl.styles import Font

        wb = Workbook()
        ws = wb.active
        ws.title = SHEET_NAME
        ws.append(list(HEADERS))
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
            os.replace(temp, self.workbook_path)
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
