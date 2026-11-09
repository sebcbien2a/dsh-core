"""P27 - Bidirectional OneDrive bridge for the director, father and accountant.

The bridge materialises a shared OneDrive folder::

    APD_COMPTA_PARTAGE/
        01_PILOTAGE_DIRIGEANT_ET_PERE/
            _A_COMPLETER.xlsx
            REFERENTIEL_TIERS_APD.xlsx
        02_CONSULTATION_EXPERT_COMPTABLE/
            GRAND_LIVRE_CLIENTS_DETAILLES.xlsx
            GRAND_LIVRE_APD_TOUS_FOURNISSEURS.xlsx
            TABLEAU_MERE_PIECES_COMPTABLES.xlsx
            SIMULATION_CA12_2026.xlsx
            SYNTHESE_APD_*.xlsx
            REFERENTIEL_TIERS_APD.xlsx
            PIECES_JOINTES/...

Before every calculation the bridge pulls the director's Excel edits from
OneDrive when they are newer than the local copy. After every stage it pushes
the readable workbooks and the accountant consultation folder, including the
``PIECES_JOINTES`` tree so every PDF hyperlink keeps working.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from services.folder_tree_service import ensure_tree

ONEDRIVE_ENV = "APD_ONEDRIVE_DIR"
ONEDRIVE_BRIDGE_ENV = "APD_ONEDRIVE_BRIDGE"
SHARED_DIR_NAME = "APD_COMPTA_PARTAGE"
PILOTAGE_DIR_NAME = "01_PILOTAGE_DIRIGEANT_ET_PERE"
EXPERT_DIR_NAME = "02_CONSULTATION_EXPERT_COMPTABLE"
TABLEAUX_COMPTABLES_DIR_NAME = "Tableaux_Comptables"
READABLE_FILENAME = "_A_COMPLETER.xlsx"
TIER_REFERENCE_FILENAME = "REFERENTIEL_TIERS_APD.xlsx"
PIECES_DIR_NAME = "PIECES_JOINTES"
EXPERT_WORKBOOK_PATTERNS: tuple[str, ...] = (
    "GRAND_LIVRE_CLIENTS_DETAILLES.xlsx",
    "GRAND_LIVRE_APD_TOUS_FOURNISSEURS.xlsx",
    "TABLEAU_MERE_PIECES_COMPTABLES.xlsx",
    "SIMULATION_CA12_*.xlsx",
    "SYNTHESE_APD_*.xlsx",
    "REFERENTIEL_TIERS_APD.xlsx",
)


@dataclass(frozen=True)
class OneDriveSyncReport:
    onedrive_root: Path | None
    shared_dir: Path | None
    pulled: tuple[Path, ...]
    pushed_director: tuple[Path, ...]
    pushed_expert: tuple[Path, ...]
    skipped: tuple[str, ...]


def onedrive_bridge_enabled() -> bool:
    """Return ``False`` when the workspace is already natively synced."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:  # noqa: BLE001 - dotenv is optional
        pass
    raw = os.environ.get(ONEDRIVE_BRIDGE_ENV, "1").strip().lower()
    return raw not in {"0", "false", "off", "no", "disabled"}


def resolve_onedrive_root(
    onedrive_dir: str | os.PathLike[str] | None = None,
) -> Path | None:
    """Detect the local OneDrive folder (explicit, env, then ``~/OneDrive``)."""
    if onedrive_dir is not None:
        candidate = Path(onedrive_dir).expanduser()
        return candidate if candidate.is_dir() else None
    for variable in (
        ONEDRIVE_ENV,
        "OneDrive",
        "OneDriveConsumer",
        "OneDriveCommercial",
    ):
        raw = os.environ.get(variable, "").strip()
        if raw:
            candidate = Path(raw).expanduser()
            if candidate.is_dir():
                return candidate
    fallback = Path.home() / "OneDrive"
    return fallback if fallback.is_dir() else None


def _is_newer(source: Path, target: Path) -> bool:
    if not source.is_file():
        return False
    if not target.is_file():
        return True
    try:
        return source.stat().st_mtime > target.stat().st_mtime
    except OSError:
        return False


def _copy_atomic(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp-p27")
    try:
        shutil.copy2(source, temporary)
        try:
            os.replace(temporary, target)
        except OSError:
            shutil.copy2(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_if_stale(source: Path, target: Path) -> bool:
    """Copy only when the target is missing or older than the source."""
    if target.is_file():
        try:
            source_stat = source.stat()
            target_stat = target.stat()
            if (
                target_stat.st_size == source_stat.st_size
                and target_stat.st_mtime >= source_stat.st_mtime
            ):
                return False
        except OSError:
            pass
    _copy_atomic(source, target)
    return True


class OneDriveAccountingBridge:
    """Pull director edits and push accountant workbooks through OneDrive."""

    def __init__(
        self,
        tenant_id: str = "APD",
        *,
        root: str | os.PathLike[str] | None = None,
        onedrive_dir: str | os.PathLike[str] | None = None,
        export_dir: str | os.PathLike[str] | None = None,
        enabled: bool | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._tree = ensure_tree(tenant_id, root)
        self._tenant_dir = self._tree.root / tenant_id
        self._export_dir = (
            Path(export_dir)
            if export_dir is not None
            else self._tree.state_dir("04_EXPORT_COMPTA") / tenant_id
        )
        self._enabled = (
            onedrive_bridge_enabled() if enabled is None else bool(enabled)
        )
        # Tests and side workspaces pass an explicit ``root``. They must never
        # overwrite the director's real OneDrive folder: only the production
        # workspace (``root=None``) or an explicit ``onedrive_dir`` may sync.
        if self._enabled and onedrive_dir is None and root is not None:
            self._enabled = False
        self._onedrive_root = (
            resolve_onedrive_root(onedrive_dir) if self._enabled else None
        )
        # P27-sexies - when the director points APD_ONEDRIVE_DIR at the real
        # ``Compta_APD\Tableaux_Comptables`` folder, publish directly at its
        # root (no intermediate 01_PILOTAGE / 02_CONSULTATION sub-folder).
        self._direct_mode = bool(
            self._onedrive_root is not None
            and self._onedrive_root.name.casefold()
            == TABLEAUX_COMPTABLES_DIR_NAME.casefold()
        )
        self._shared_dir = (
            self._onedrive_root
            if self._direct_mode
            else (
                self._onedrive_root / SHARED_DIR_NAME
                if self._onedrive_root is not None
                else None
            )
        )
        self._pilotage_dir = (
            self._shared_dir
            if self._direct_mode
            else (
                self._shared_dir / PILOTAGE_DIR_NAME
                if self._shared_dir is not None
                else None
            )
        )
        self._expert_dir = (
            self._shared_dir
            if self._direct_mode
            else (
                self._shared_dir / EXPERT_DIR_NAME
                if self._shared_dir is not None
                else None
            )
        )

    @property
    def direct_mode(self) -> bool:
        return self._direct_mode

    @property
    def onedrive_root(self) -> Path | None:
        return self._onedrive_root

    @property
    def shared_dir(self) -> Path | None:
        return self._shared_dir

    @property
    def pilotage_dir(self) -> Path | None:
        return self._pilotage_dir

    @property
    def expert_dir(self) -> Path | None:
        return self._expert_dir

    def ensure_structure(self) -> None:
        if self._direct_mode:
            if self._expert_dir is not None:
                self._expert_dir.mkdir(parents=True, exist_ok=True)
            return
        if self._pilotage_dir is not None:
            self._pilotage_dir.mkdir(parents=True, exist_ok=True)
        if self._expert_dir is not None:
            self._expert_dir.mkdir(parents=True, exist_ok=True)

    def _local_director_files(self) -> dict[str, tuple[Path, ...]]:
        readable = self._tree.state_dir("02_A_COMPLETER") / READABLE_FILENAME
        technical = self._tenant_dir / READABLE_FILENAME
        tier_root = self._tenant_dir / TIER_REFERENCE_FILENAME
        # P27-septies - one single master tier workbook: the historical
        # ``04_EXPORT_COMPTA/APD/REFERENTIEL_TIERS_APD.xlsx`` duplicate is
        # never pulled or recreated.
        return {
            READABLE_FILENAME: (readable, technical),
            TIER_REFERENCE_FILENAME: (tier_root,),
        }

    def pull_director_files(self) -> tuple[Path, ...]:
        """Import OneDrive director edits when they are newer than local."""
        if self._pilotage_dir is None:
            return ()
        pulled: list[Path] = []
        for name, local_paths in self._local_director_files().items():
            remote = self._pilotage_dir / name
            if not remote.is_file():
                continue
            for local in local_paths:
                if _is_newer(remote, local):
                    _copy_atomic(remote, local)
                    pulled.append(local)
        return tuple(pulled)

    def push_director_files(self) -> tuple[Path, ...]:
        """Publish the readable workbook and tier reference to OneDrive.

        P27-septies - "last writer wins": a local file older than the OneDrive
        copy is never pushed, so the father's fresher edits can never be
        overwritten by a stale pipeline run.
        """
        if self._pilotage_dir is None:
            return ()
        self._pilotage_dir.mkdir(parents=True, exist_ok=True)
        pushed: list[Path] = []
        for name, local_paths in self._local_director_files().items():
            source = next((path for path in local_paths if path.is_file()), None)
            if source is None:
                continue
            target = self._pilotage_dir / name
            if target.is_file():
                try:
                    if target.stat().st_mtime > source.stat().st_mtime:
                        continue
                except OSError:
                    pass
            _copy_atomic(source, target)
            pushed.append(target)
        return tuple(pushed)

    def _expert_workbooks(self) -> list[Path]:
        if not self._export_dir.is_dir():
            return []
        if self._direct_mode:
            return sorted(
                (
                    path
                    for path in self._export_dir.glob("*.xlsx")
                    if "__" not in path.stem
                    and not path.name.startswith("~$")
                ),
                key=lambda path: path.name.casefold(),
            )
        selected: list[Path] = []
        for pattern in EXPERT_WORKBOOK_PATTERNS:
            for path in sorted(self._export_dir.glob(pattern)):
                if "__" in path.stem:
                    continue
                selected.append(path)
        return sorted(dict.fromkeys(selected), key=lambda path: path.name.casefold())

    def push_expert_workbooks(self) -> tuple[Path, ...]:
        """Copy the accountant workbooks and the PDF tree to OneDrive."""
        if self._expert_dir is None:
            return ()
        self._expert_dir.mkdir(parents=True, exist_ok=True)
        pushed: list[Path] = []
        for source in self._expert_workbooks():
            target = self._expert_dir / source.name
            _copy_atomic(source, target)
            pushed.append(target)
        pieces = self._export_dir / PIECES_DIR_NAME
        if pieces.is_dir():
            target_pieces = self._expert_dir / PIECES_DIR_NAME
            target_pieces.mkdir(parents=True, exist_ok=True)
            for source in pieces.rglob("*"):
                if not source.is_file():
                    continue
                relative = source.relative_to(pieces)
                target = target_pieces / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if _copy_if_stale(source, target):
                    pushed.append(target)
        return tuple(pushed)

    def sync(self) -> OneDriveSyncReport:
        """Pull director edits, then publish both shared folders."""
        if self._shared_dir is None:
            reason = (
                "ONEDRIVE_BRIDGE_DISABLED"
                if not self._enabled
                else "ONEDRIVE_NOT_FOUND"
            )
            return OneDriveSyncReport(
                onedrive_root=None,
                shared_dir=None,
                pulled=(),
                pushed_director=(),
                pushed_expert=(),
                skipped=(reason,),
            )
        self.ensure_structure()
        pulled = self.pull_director_files()
        pushed_director = self.push_director_files()
        pushed_expert = self.push_expert_workbooks()
        return OneDriveSyncReport(
            onedrive_root=self._onedrive_root,
            shared_dir=self._shared_dir,
            pulled=pulled,
            pushed_director=pushed_director,
            pushed_expert=pushed_expert,
            skipped=(),
        )


__all__ = [
    "EXPERT_DIR_NAME",
    "EXPERT_WORKBOOK_PATTERNS",
    "ONEDRIVE_ENV",
    "OneDriveAccountingBridge",
    "OneDriveSyncReport",
    "PILOTAGE_DIR_NAME",
    "PIECES_DIR_NAME",
    "SHARED_DIR_NAME",
    "TABLEAUX_COMPTABLES_DIR_NAME",
    "resolve_onedrive_root",
]
