"""Per-tenant ``00_``-``05_`` document flow tree builder and validator (P14.1).

Layout: ``<root>/<tenant_id>/<state>/`` where ``<root>`` comes from the
``DSH_DOCUMENT_FLOW_ROOT`` environment variable (default
``workspace/document_flow``, local or inside a OneDrive-synced folder) and
``<state>`` is one of the six folder states. The eight business categories are
materialised under ``03_VALIDE_PRET_FEC`` and ``04_EXPORT_COMPTA``; manual
rejections live under ``05_ARCHIVE_ERREUR/REJET_MANUEL`` (spec P14, decision 3).
Every computed path is checked against traversal outside ``<root>``.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from schemas.folder_state_schema import (
    DocumentCategory,
    ExpenseSubCategory,
    FolderState,
)

FLOW_ROOT_ENV = "DSH_DOCUMENT_FLOW_ROOT"
ACTIVE_TENANT_ENV = "DSH_ACTIVE_TENANT"
DEFAULT_FLOW_ROOT = Path("workspace") / "document_flow"
DEFAULT_ACTIVE_TENANT = "APD"
LEGACY_TENANT = "default"
LEGACY_MARKER = "MIGRE_VERS_APD.md"
LEGACY_DUPLICATES_DIR = "_DOUBLONS_IDENTIQUES"
REJET_MANUEL_DIR = "REJET_MANUEL"
CATEGORISED_STATES = frozenset(
    {FolderState.VALIDE_PRET_FEC, FolderState.EXPORT_COMPTA}
)

_TENANT_ID = re.compile(r"[A-Za-z0-9._:-]{1,64}")
_CHUNK = 1 << 16


@dataclass(frozen=True)
class LegacyMigrationReport:
    """Observable result of the one-way ``default`` -> ``APD`` migration."""

    source: Path
    target: Path
    moved: tuple[Path, ...] = ()
    skipped: tuple[Path, ...] = ()
    failed: tuple[str, ...] = ()
    db_rows: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.moved or self.db_rows)


def resolve_active_tenant() -> str:
    """Return the production tenant (``ONEDRIVE``-safe, never ``default``)."""
    raw = os.environ.get(ACTIVE_TENANT_ENV, "").strip()
    if raw:
        return validate_tenant_id(raw)
    return DEFAULT_ACTIVE_TENANT


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    index = 1
    while True:
        candidate = path.with_name(f"{path.stem}__{index}{path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _migrate_store_tenant(db_path: str | os.PathLike[str] | None) -> int:
    """Move ``default`` SQLite rows to ``APD`` without losing conflicts."""
    if db_path is None:
        return 0
    path = Path(db_path)
    if not path.is_file():
        return 0
    backup = path.with_name(path.name + ".bak_p24")
    if not backup.exists():
        try:
            shutil.copy2(path, backup)
        except OSError:
            pass
    changed = 0
    with sqlite3.connect(path, timeout=30) as connection:
        for table in (
            "reconciliation_lines",
            "arbitration_decisions",
            "counterparty_payment_rules",
            "candidate_rules",
        ):
            try:
                cursor = connection.execute(
                    f"UPDATE OR IGNORE {table} SET tenant_id = ?"
                    " WHERE tenant_id = ?",
                    (DEFAULT_ACTIVE_TENANT, LEGACY_TENANT),
                )
                changed += max(cursor.rowcount, 0)
            except sqlite3.Error:
                continue
        try:
            cursor = connection.execute(
                "UPDATE accounting_entries SET tenant_id = ?"
                " WHERE tenant_id = ?",
                (DEFAULT_ACTIVE_TENANT, LEGACY_TENANT),
            )
            changed += max(cursor.rowcount, 0)
        except sqlite3.Error:
            pass
    return changed


def migrate_default_to_apd(
    root: str | os.PathLike[str] | None = None,
    *,
    store: object | None = None,
    enabled: bool | None = None,
) -> LegacyMigrationReport:
    """Merge every file of ``workspace/document_flow/default`` into ``APD``.

    The migration is idempotent, non-destructive (``_unique_destination`` when a
    target name already exists) and only active for the real production root
    unless explicitly forced. The legacy folder is kept for audit and marked.
    """
    base = resolve_flow_root(root).resolve()
    source = base / LEGACY_TENANT
    if enabled is None:
        enabled = root is None
    if not enabled or not source.is_dir():
        return LegacyMigrationReport(
            source=source, target=base / DEFAULT_ACTIVE_TENANT
        )
    tree = ensure_tree(DEFAULT_ACTIVE_TENANT, root)
    target = tree.root / DEFAULT_ACTIVE_TENANT
    moved: list[Path] = []
    skipped: list[Path] = []
    failed: list[str] = []
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.name == LEGACY_MARKER:
            continue
        if LEGACY_DUPLICATES_DIR in path.relative_to(source).parts:
            continue
        relative = path.relative_to(source)
        parts = list(relative.parts)
        if (
            len(parts) > 1
            and parts[0]
            in {"03_VALIDE_PRET_FEC", "04_EXPORT_COMPTA"}
            and parts[1] == LEGACY_TENANT
        ):
            parts[1] = DEFAULT_ACTIVE_TENANT
            relative = Path(*parts)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            try:
                if _file_sha256(path) == _file_sha256(destination):
                    duplicate = (
                        source
                        / LEGACY_DUPLICATES_DIR
                        / relative
                    )
                    duplicate.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.move(str(path), str(duplicate))
                    except OSError:
                        pass
                    skipped.append(path)
                    continue
            except OSError:
                pass
            destination = _unique_destination(destination)
        try:
            shutil.move(str(path), str(destination))
        except PermissionError:
            try:
                shutil.copy2(path, destination)
            except OSError as exc:
                failed.append(f"{path.name}:{exc}")
                continue
            moved.append(destination)
        except OSError as exc:
            failed.append(f"{path.name}:{exc}")
            continue
        else:
            moved.append(destination)
    db_rows = 0
    if store is not None:
        db_rows = _migrate_store_tenant(getattr(store, "db_path", None))
    try:
        (source / LEGACY_MARKER).write_text(
            "Migration P24 effectuée vers workspace/document_flow/APD.\n"
            f"Fichiers déplacés : {len(moved)} ; ignorés : {len(skipped)} ; "
            f"échecs : {len(failed)} ; lignes SQLite migrées : {db_rows}.\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    return LegacyMigrationReport(
        source=source,
        target=target,
        moved=tuple(moved),
        skipped=tuple(skipped),
        failed=tuple(failed),
        db_rows=db_rows,
    )



class FolderTreeError(ValueError):
    """Invalid folder tree request; ``code`` is stable for tests and logs."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


def validate_tenant_id(tenant_id: object) -> str:
    """Accept the shared tenant grammar only (same rule as P10.1 / P10.2)."""
    if (
        not isinstance(tenant_id, str)
        or _TENANT_ID.fullmatch(tenant_id) is None
        or tenant_id in (".", "..")
        or set(tenant_id) == {"."}
    ):
        raise FolderTreeError("INVALID_TENANT_ID", repr(tenant_id))
    return tenant_id


def resolve_flow_root(root: str | os.PathLike[str] | None = None) -> Path:
    """Explicit ``root`` wins, then ``DSH_DOCUMENT_FLOW_ROOT``, then the default."""
    if root is not None:
        raw = os.fspath(root)
    else:
        raw = os.environ.get(FLOW_ROOT_ENV, "").strip() or os.fspath(DEFAULT_FLOW_ROOT)
    if not raw or "\x00" in raw:
        raise FolderTreeError("INVALID_ROOT", repr(raw))
    return Path(raw)


def ensure_contained(base: Path, candidate: Path) -> Path:
    """Return the resolved ``candidate`` or raise when it escapes ``base``."""
    root = Path(base).resolve()
    resolved = Path(candidate).resolve()
    if root != resolved and root not in resolved.parents:
        raise FolderTreeError("PATH_TRAVERSAL", str(candidate))
    return resolved


@dataclass(frozen=True)
class FolderTree:
    """Resolved layout of one tenant inside the flow root."""

    root: Path
    tenant_id: str
    state_dirs: Mapping[FolderState, Path]

    def state_dir(self, state: FolderState) -> Path:
        return self.state_dirs[FolderState(state)]

    def category_dir(self, state: FolderState, category: DocumentCategory) -> Path:
        state = FolderState(state)
        if state not in CATEGORISED_STATES:
            raise FolderTreeError("INVALID_CATEGORY_STATE", state.value)
        return self.state_dir(state) / DocumentCategory(category).value

    def expense_subcategory_dir(
        self,
        state: FolderState,
        subcategory: ExpenseSubCategory | str,
    ) -> Path:
        """Resolved ``FRAIS_GENERAUX/<SOUS_CAT>`` folder under ``03_``/``04_``."""
        state = FolderState(state)
        if state not in CATEGORISED_STATES:
            raise FolderTreeError("INVALID_CATEGORY_STATE", state.value)
        try:
            selected = ExpenseSubCategory(subcategory)
        except (TypeError, ValueError):
            raise FolderTreeError(
                "INVALID_EXPENSE_SUBCATEGORY", repr(subcategory)
            ) from None
        return (
            self.category_dir(state, DocumentCategory.FRAIS_GENERAUX)
            / selected.value
        )

    def rejet_manuel_dir(self) -> Path:
        return self.state_dir(FolderState.ARCHIVE_ERREUR) / REJET_MANUEL_DIR


def _mkdir(path: Path) -> None:
    if path.exists() and not path.is_dir():
        raise FolderTreeError("NOT_A_DIRECTORY", str(path))
    path.mkdir(parents=True, exist_ok=True)


def ensure_tree(
    tenant_id: str, root: str | os.PathLike[str] | None = None
) -> FolderTree:
    """Create (idempotently) and validate the tenant flow tree.

    Materialises the six state folders, the eight category folders under
    ``03_VALIDE_PRET_FEC`` and ``04_EXPORT_COMPTA`` and
    ``05_ARCHIVE_ERREUR/REJET_MANUEL``. An existing file standing on a mandatory
    path aborts with ``NOT_A_DIRECTORY``; nothing outside ``<root>/<tenant_id>``
    is ever created or touched.
    """
    tenant = validate_tenant_id(tenant_id)
    base = resolve_flow_root(root).resolve()
    tenant_dir = ensure_contained(base, base / tenant)

    state_dirs: dict[FolderState, Path] = {}
    for state in FolderState:
        path = ensure_contained(base, tenant_dir / state.value)
        _mkdir(path)
        state_dirs[state] = path

    for state in CATEGORISED_STATES:
        for category in DocumentCategory:
            _mkdir(state_dirs[state] / category.value)

    for state in CATEGORISED_STATES:
        frais_generaux = state_dirs[state] / DocumentCategory.FRAIS_GENERAUX.value
        for subcategory in ExpenseSubCategory:
            _mkdir(frais_generaux / subcategory.value)

    _mkdir(state_dirs[FolderState.ARCHIVE_ERREUR] / REJET_MANUEL_DIR)

    return FolderTree(root=base, tenant_id=tenant, state_dirs=state_dirs)
