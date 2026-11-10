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
    expense_subcategory_folder_name,
)
from services.bank_reconciliation_service import (
    CLIENT_SALES_CATEGORY,
    contains_client_invoice_reference,
)

FLOW_ROOT_ENV = "DSH_DOCUMENT_FLOW_ROOT"
ACTIVE_TENANT_ENV = "DSH_ACTIVE_TENANT"
DEFAULT_FLOW_ROOT = Path("workspace") / "document_flow"
DEFAULT_ACTIVE_TENANT = "APD"
LEGACY_TENANT = "default"
LEGACY_MARKER = "MIGRE_VERS_APD.md"
LEGACY_DUPLICATES_DIR = "_DOUBLONS_IDENTIQUES"
REJET_MANUEL_DIR = "REJET_MANUEL"
GED_FACTURES_FOURNISSEURS = "FACTURES_FOURNISSEURS"
GED_FACTURES_CLIENTS = "FACTURES_CLIENTS"
GED_AVOIRS_FOURNISSEURS = "AVOIRS_FOURNISSEURS"
GED_AVOIRS_CLIENTS = "AVOIRS_CLIENTS"
GED_LISTES_ET_BORDEREAUX = "LISTES_ET_BORDEREAUX"
GED_SUBDIRS: tuple[str, ...] = (
    GED_FACTURES_FOURNISSEURS,
    GED_FACTURES_CLIENTS,
    GED_AVOIRS_FOURNISSEURS,
    GED_AVOIRS_CLIENTS,
    GED_LISTES_ET_BORDEREAUX,
)
_GED_BANK_MARKERS: tuple[str, ...] = (
    "OFX",
    "QFX",
    "RELEVE",
    "BORDEREAU",
    "LCR",
    "REMISE",
    "LISTE",
    "LISTING",
    "LOT",
    "BATCH",
    "RECAP",
    "SAGE",
    "EXPORT",
)
_GED_CREDIT_MARKERS: frozenset[str] = frozenset(
    {"AVOIR", "CREDIT_NOTE", "CREDIT", "CREDIT_MEMO"}
)
CATEGORISED_STATES = frozenset(
    {FolderState.VALIDE_PRET_FEC, FolderState.EXPORT_COMPTA}
)


def ged_subdir_for_document(
    *,
    category: DocumentCategory | str | None = None,
    document_type: str | None = None,
    invoice_number: str | None = None,
    label: str | None = None,
    filename: str | None = None,
) -> str:
    """Return the physical GED sub-folder of one validated document.

    The mapping is deterministic and deliberately checks the ``FA1*`` customer
    series before any category or document-type heuristic so a customer sale
    can never fall into a supplier expense folder.
    """
    kind = re.sub(r"\s+", " ", str(document_type or "")).strip().upper()
    try:
        selected_category = (
            DocumentCategory(category).value if category is not None else ""
        )
    except (TypeError, ValueError):
        selected_category = str(category or "").strip().upper()
    is_client_sale = selected_category == CLIENT_SALES_CATEGORY or (
        contains_client_invoice_reference(invoice_number, label, filename)
    )
    if any(marker in kind for marker in _GED_CREDIT_MARKERS):
        return GED_AVOIRS_CLIENTS if is_client_sale else GED_AVOIRS_FOURNISSEURS
    if any(marker in kind for marker in _GED_BANK_MARKERS):
        return GED_LISTES_ET_BORDEREAUX
    if is_client_sale:
        return GED_FACTURES_CLIENTS
    return GED_FACTURES_FOURNISSEURS


def validated_destination(
    tree: "FolderTree",
    filename: str,
    *,
    category: DocumentCategory | str | None = None,
    document_type: str | None = None,
    invoice_number: str | None = None,
    label: str | None = None,
) -> Path:
    """Return the final ``03_VALIDE_PRET_FEC/<GED>/<filename>`` path."""
    subdir = ged_subdir_for_document(
        category=category,
        document_type=document_type,
        invoice_number=invoice_number,
        label=label,
        filename=filename,
    )
    return tree.state_dir(FolderState.VALIDE_PRET_FEC) / subdir / Path(filename).name


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

    def ged_dir(self, subdir: str) -> Path:
        """Return one of the five physical GED sub-folders of ``03_``."""
        if subdir not in GED_SUBDIRS:
            raise FolderTreeError("INVALID_GED_SUBDIR", repr(subdir))
        return self.state_dir(FolderState.VALIDE_PRET_FEC) / subdir

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
            / expense_subcategory_folder_name(selected)
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

    for subdir in GED_SUBDIRS:
        _mkdir(state_dirs[FolderState.VALIDE_PRET_FEC] / subdir)

    for state in CATEGORISED_STATES:
        frais_generaux = state_dirs[state] / DocumentCategory.FRAIS_GENERAUX.value
        for subcategory in ExpenseSubCategory:
            _mkdir(
                frais_generaux
                / expense_subcategory_folder_name(subcategory)
            )

    _mkdir(state_dirs[FolderState.ARCHIVE_ERREUR] / REJET_MANUEL_DIR)

    return FolderTree(root=base, tenant_id=tenant, state_dirs=state_dirs)


def migrate_validated_to_ged(
    tenant_id: str,
    root: str | os.PathLike[str] | None = None,
    *,
    store: object | None = None,
) -> tuple[Path, ...]:
    """Move legacy ``03_VALIDE_PRET_FEC/<CATEGORY>`` files into the 5 GED dirs.

    Idempotent, non-destructive and traced by the caller. Existing GED files
    are never overwritten: a ``__N`` suffix is used. When a SQLite store is
    supplied, the ``invoice_url`` of the matching line is rewritten to the new
    tenant-relative path so every workbook link follows the physical move.
    """
    tree = ensure_tree(tenant_id, root)
    tenant = validate_tenant_id(tenant_id)
    tenant_dir = tree.root / tenant
    rows_by_url: dict[str, dict] = {}
    rows_by_name: dict[str, dict] = {}
    all_rows: list[dict] = []
    if store is not None:
        lister = getattr(store, "list_master_lines", None)
        if callable(lister):
            try:
                rows = list(lister(tenant))
            except Exception:  # noqa: BLE001 - migration never blocks
                rows = []
            all_rows = rows
            for row in rows:
                url = str(row.get("invoice_url") or "").replace("\\", "/")
                if url:
                    rows_by_url.setdefault(url, row)
                name = Path(str(row.get("nom_fichier") or url)).name
                if name:
                    rows_by_name.setdefault(name.casefold(), row)
    moved: list[Path] = []
    for category in DocumentCategory:
        directory = tree.category_dir(FolderState.VALIDE_PRET_FEC, category)
        if not directory.is_dir():
            continue
        for path in sorted(
            (item for item in directory.rglob("*") if item.is_file()),
            key=lambda item: str(item).casefold(),
        ):
            if path.name.startswith("~$"):
                continue
            rel = path.relative_to(tenant_dir).as_posix()
            row = rows_by_url.get(rel) or rows_by_name.get(path.name.casefold())
            selected = category
            if row is not None:
                try:
                    selected = DocumentCategory(str(row.get("category") or category))
                except ValueError:
                    selected = category
            destination = validated_destination(
                tree,
                path.name,
                category=selected,
                document_type=str((row or {}).get("document_type") or ""),
                invoice_number=str(
                    (row or {}).get("invoice_id") or path.stem
                ),
                label=str(
                    (row or {}).get("counterparty")
                    or (row or {}).get("supplier")
                    or ""
                ),
            )
            if destination.exists():
                destination = _unique_destination(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(path), str(destination))
            except OSError:
                continue
            moved.append(destination)
            if store is not None and row is not None:
                line_id = str(row.get("line_id") or "")
                if line_id:
                    line = store.get_line(tenant, line_id)
                    if line is not None:
                        line.pop("line_id", None)
                        line["invoice_url"] = destination.relative_to(
                            tenant_dir
                        ).as_posix()
                        try:
                            store.upsert_document_line(tenant, line_id, line)
                        except Exception:  # noqa: BLE001
                            pass
    if store is not None and all_rows:
        export_root = tree.state_dir(FolderState.EXPORT_COMPTA) / tenant
        for row in all_rows:
            piece_pdf = str(row.get("piece_pdf") or "").strip()
            if not piece_pdf:
                continue
            invoice_url = str(row.get("invoice_url") or "").replace("\\", "/")
            current = tenant_dir / invoice_url if invoice_url else None
            if current is not None and current.is_file() and any(
                invoice_url.startswith(f"{FolderState.VALIDE_PRET_FEC.value}/{subdir}/")
                for subdir in GED_SUBDIRS
            ):
                continue
            source = Path(piece_pdf)
            if not source.is_absolute():
                source = export_root / piece_pdf
            if not source.is_file():
                alternate = tenant_dir / piece_pdf
                if alternate.is_file():
                    source = alternate
                else:
                    continue
            selected = DocumentCategory.DIVERS_A_CLASSER
            try:
                selected = DocumentCategory(str(row.get("category") or ""))
            except ValueError:
                selected = DocumentCategory.DIVERS_A_CLASSER
            destination = validated_destination(
                tree,
                source.name,
                category=selected,
                document_type=str(row.get("document_type") or ""),
                invoice_number=str(row.get("invoice_id") or source.stem),
                label=str(
                    row.get("counterparty") or row.get("supplier") or ""
                ),
            )
            if destination.exists():
                destination = _unique_destination(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source, destination)
            except OSError:
                continue
            moved.append(destination)
            line_id = str(row.get("line_id") or "")
            if line_id:
                line = store.get_line(tenant, line_id)
                if line is not None:
                    line.pop("line_id", None)
                    line["invoice_url"] = destination.relative_to(
                        tenant_dir
                    ).as_posix()
                    try:
                        store.upsert_document_line(tenant, line_id, line)
                    except Exception:  # noqa: BLE001
                        pass
    return tuple(moved)
