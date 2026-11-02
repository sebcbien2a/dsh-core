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

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from schemas.folder_state_schema import DocumentCategory, FolderState

FLOW_ROOT_ENV = "DSH_DOCUMENT_FLOW_ROOT"
DEFAULT_FLOW_ROOT = Path("workspace") / "document_flow"
REJET_MANUEL_DIR = "REJET_MANUEL"
CATEGORISED_STATES = frozenset(
    {FolderState.VALIDE_PRET_FEC, FolderState.EXPORT_COMPTA}
)

_TENANT_ID = re.compile(r"[A-Za-z0-9._:-]{1,64}")


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

    _mkdir(state_dirs[FolderState.ARCHIVE_ERREUR] / REJET_MANUEL_DIR)

    return FolderTree(root=base, tenant_id=tenant, state_dirs=state_dirs)
