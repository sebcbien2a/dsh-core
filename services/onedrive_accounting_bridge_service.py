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

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

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
LOCAL_PDF_BAT_NAME = "Activer_Ouverture_PDF_Locale_PC_Pere.bat"
PULL_STATE_FILENAME = ".onedrive_pull_state.json"
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


def _file_signature(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
        return {"mtime": stat.st_mtime, "size": stat.st_size}
    except OSError:
        return {}


def _state_path(tenant_id: str, root: str | os.PathLike[str] | None) -> Path:
    base = (
        Path(root).expanduser() / tenant_id
        if root is not None
        else Path("workspace") / "document_flow" / tenant_id
    )
    return base / PULL_STATE_FILENAME


def _read_pull_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_pull_state(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        os.replace(temporary, path)
    except OSError:
        return


def _director_targets(
    tenant_id: str, root: str | os.PathLike[str] | None
) -> dict[str, tuple[Path, ...]]:
    base = (
        Path(root).expanduser() / tenant_id
        if root is not None
        else Path("workspace") / "document_flow" / tenant_id
    )
    return {
        READABLE_FILENAME: (base / "02_A_COMPLETER" / READABLE_FILENAME,),
        TIER_REFERENCE_FILENAME: (base / TIER_REFERENCE_FILENAME,),
    }


def _merge_xlsx(remote: Path, local: Path, target: Path) -> None:
    """Merge two Excel workbooks, giving absolute priority to ``remote``.

    Rows are matched by their business key (canonical tier, SHA-256, filename
    or piece number). Remote values always win; local-only rows are kept. The
    header list is the union of both schemas so a locally upgraded layout
    (``Exercice Année``, ``Date Départ Solde 0``) is never reverted to the old
    OneDrive layout.
    """
    from openpyxl import Workbook, load_workbook

    key_names = (
        "Nom Officiel Canonique",
        "SHA-256",
        "Nom Fichier",
        "Nom fichier",
        "N° Pièce",
        "Date Pièce",
    )

    def read_sheet(path: Path) -> tuple[list[str], dict[str, dict[str, Any]]]:
        workbook = load_workbook(path, data_only=False)
        try:
            sheet = workbook.active
            headers = [
                str(cell.value or "").strip() for cell in sheet[1]
            ]
            key_index = next(
                (
                    index
                    for index, name in enumerate(headers)
                    if name in key_names
                ),
                0,
            )
            rows: dict[str, dict[str, Any]] = {}
            for row_index in range(2, sheet.max_row + 1):
                key = str(
                    sheet.cell(row=row_index, column=key_index + 1).value
                    or ""
                ).strip()
                if not key:
                    continue
                values: dict[str, Any] = {}
                for column, header in enumerate(headers, start=1):
                    if not header:
                        continue
                    cell = sheet.cell(row=row_index, column=column)
                    values[header] = cell.value
                    if cell.hyperlink is not None and cell.hyperlink.target:
                        values[f"__link__{header}"] = cell.hyperlink.target
                rows[key] = values
            return headers, rows
        finally:
            workbook.close()

    remote_headers, remote_rows = read_sheet(remote)
    if not local.is_file():
        _copy_atomic(remote, target)
        return
    local_headers, local_rows = read_sheet(local)
    headers: list[str] = list(remote_headers)
    for header in local_headers:
        if header and header not in headers:
            headers.append(header)
    merged: dict[str, dict[str, Any]] = dict(local_rows)
    merged.update(remote_rows)
    workbook = Workbook()
    sheet = workbook.active
    for column, header in enumerate(headers, start=1):
        sheet.cell(row=1, column=column, value=header or None)
    for row_index, values in enumerate(merged.values(), start=2):
        for column, header in enumerate(headers, start=1):
            if not header:
                continue
            cell = sheet.cell(row=row_index, column=column)
            cell.value = values.get(header)
            link = values.get(f"__link__{header}")
            if link:
                cell.hyperlink = link
    temporary = target.with_name(target.name + ".tmp-p27-merge")
    try:
        workbook.save(temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _pull_remote_file(remote: Path, target: Path, force: bool) -> bool:
    """Copy or merge one OneDrive file into its local target."""
    if not remote.is_file():
        return False
    if target.is_file() and not force:
        try:
            remote_stat = remote.stat()
            target_stat = target.stat()
            if (
                target_stat.st_size == remote_stat.st_size
                and target_stat.st_mtime >= remote_stat.st_mtime
            ):
                return False
        except OSError:
            pass
    if target.suffix.casefold() in {".xlsx", ".xlsm"}:
        try:
            _merge_xlsx(remote, target, target)
            return True
        except Exception:  # noqa: BLE001 - non-Excel payloads are copied
            pass
    _copy_atomic(remote, target)
    return True


def remove_duplicate_local_readable(
    tenant_id: str = "APD",
    root: str | os.PathLike[str] | None = None,
) -> Path | None:
    """Delete the historical tenant-root ``_A_COMPLETER.xlsx`` duplicate."""
    base = (
        Path(root).expanduser() / tenant_id
        if root is not None
        else Path("workspace") / "document_flow" / tenant_id
    )
    duplicate = base / READABLE_FILENAME
    if not duplicate.is_file():
        return None
    try:
        duplicate.unlink()
        return duplicate
    except OSError:
        return None


def pull_from_onedrive(
    tenant_id: str = "APD",
    *,
    root: str | os.PathLike[str] | None = None,
    onedrive_dir: str | os.PathLike[str] | None = None,
) -> tuple[Path, ...]:
    """Pull the fresher director workbooks before any local write.

    The module-level helper exists so ``refresh_cockpit.py`` can call it as its
    very first statement, before ``ensure_tree`` or any exporter touches
    ``A:\\``. OneDrive always wins: a remote file whose signature changed since
    the last pull is merged (remote rows kept, local-only rows appended) even
    when the local copy claims a newer timestamp.
    """
    if not onedrive_bridge_enabled():
        return ()
    destination = resolve_onedrive_root(onedrive_dir)
    if destination is None:
        return ()
    direct = (
        destination.name.casefold() == TABLEAUX_COMPTABLES_DIR_NAME.casefold()
    )
    pilotage = (
        destination
        if direct
        else destination / SHARED_DIR_NAME / PILOTAGE_DIR_NAME
    )
    remove_duplicate_local_readable(tenant_id, root)
    state_path = _state_path(tenant_id, root)
    state = _read_pull_state(state_path)
    pulled: list[Path] = []
    for name, targets in _director_targets(tenant_id, root).items():
        remote = pilotage / name
        if not remote.is_file():
            continue
        signature = _file_signature(remote)
        known = state.get(name) if isinstance(state.get(name), dict) else None
        changed = known != signature
        force = changed or known is None
        for target in targets:
            if _pull_remote_file(remote, target, force):
                pulled.append(target)
        state[name] = signature
    _write_pull_state(state_path, state)
    return tuple(dict.fromkeys(pulled))


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
        # P27-septies - the readable workbook only lives in ``02_A_COMPLETER``.
        # The historical tenant-root ``_A_COMPLETER.xlsx`` duplicate is never
        # pulled, pushed or recreated.
        readable = self._tree.state_dir("02_A_COMPLETER") / READABLE_FILENAME
        tier_root = self._tenant_dir / TIER_REFERENCE_FILENAME
        # One single master tier workbook: the historical
        # ``04_EXPORT_COMPTA/APD/REFERENTIEL_TIERS_APD.xlsx`` duplicate is
        # never pulled or recreated.
        return {
            READABLE_FILENAME: (readable,),
            TIER_REFERENCE_FILENAME: (tier_root,),
        }

    @property
    def _pull_state_path(self) -> Path:
        return self._tenant_dir / PULL_STATE_FILENAME

    def pull_from_onedrive(self) -> tuple[Path, ...]:
        """Import and merge every OneDrive director edit before a stage."""
        if self._pilotage_dir is None:
            return ()
        remove_duplicate_local_readable(
            self._tenant_id, self._tenant_dir.parent
        )
        state = _read_pull_state(self._pull_state_path)
        pulled: list[Path] = []
        for name, local_paths in self._local_director_files().items():
            remote = self._pilotage_dir / name
            if not remote.is_file():
                continue
            signature = _file_signature(remote)
            known = (
                state.get(name) if isinstance(state.get(name), dict) else None
            )
            force = known != signature or known is None
            for local in local_paths:
                if _pull_remote_file(remote, local, force):
                    pulled.append(local)
            state[name] = signature
        _write_pull_state(self._pull_state_path, state)
        return tuple(dict.fromkeys(pulled))

    def pull_director_files(self) -> tuple[Path, ...]:
        """Backward-compatible alias of :meth:`pull_from_onedrive`."""
        return self.pull_from_onedrive()

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
        pushed.extend(self.sync_pieces_tree())
        if self._direct_mode:
            self.activate_local_pdf_opening()
        return tuple(pushed)

    def sync_pieces_tree(self) -> tuple[Path, ...]:
        """Copy the whole ``PIECES_JOINTES`` tree next to the workbooks.

        The father and the accountant open the workbooks directly from
        ``OneDrive\\Compta_APD\\Tableaux_Comptables``: the PDF links are child
        paths (``PIECES_JOINTES/<sub>/<file>.pdf``) and must resolve locally,
        never through the Office co-authoring web URL.
        """
        if self._expert_dir is None:
            return ()
        source = self._export_dir / PIECES_DIR_NAME
        if not source.is_dir():
            return ()
        target_pieces = self._expert_dir / PIECES_DIR_NAME
        target_pieces.mkdir(parents=True, exist_ok=True)
        pushed: list[Path] = []
        for piece in source.rglob("*"):
            if not piece.is_file():
                continue
            relative = piece.relative_to(source)
            target = target_pieces / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if _copy_if_stale(piece, target):
                pushed.append(target)
        return tuple(pushed)

    def activate_local_pdf_opening(self) -> Path | None:
        """Force Windows/Office to open every PDF link with the local shell.

        Generates and immediately executes
        ``Activer_Ouverture_PDF_Locale_PC_Pere.bat``: Office Web links are
        disabled, ``ForceShellExecute`` routes hyperlinks to the Windows shell
        (Foxit / Adobe) and OneDrive is asked to keep the PDF tree physically
        on disk (``attrib +P -U``).
        """
        if self._shared_dir is None:
            return None
        self._shared_dir.mkdir(parents=True, exist_ok=True)
        bat_path = self._shared_dir / LOCAL_PDF_BAT_NAME
        pieces = self._shared_dir / PIECES_DIR_NAME
        script = "\n".join(
            (
                "@echo off",
                "setlocal",
                "rem P27-septies - ouverture 100% locale des liens PDF",
                'set "PIECES=%USERPROFILE%\\OneDrive\\Compta_APD'
                '\\Tableaux_Comptables\\PIECES_JOINTES"',
                'reg add "HKCU\\Software\\Microsoft\\Office\\16.0'
                '\\Common\\Internet" /v UseOnlineContent /t REG_DWORD'
                " /d 0 /f >nul 2>&1",
                'reg add "HKCU\\Software\\Microsoft\\Office\\16.0'
                '\\Common\\Internet" /v ForceShellExecute /t REG_DWORD'
                " /d 1 /f >nul 2>&1",
                'reg add "HKCU\\Software\\Microsoft\\Office\\9.0'
                '\\Common\\Internet" /v ForceShellExecute /t REG_DWORD'
                " /d 1 /f >nul 2>&1",
                'reg add "HKLM\\Software\\Microsoft\\Office\\9.0'
                '\\Common\\Internet" /v ForceShellExecute /t REG_DWORD'
                " /d 1 /f >nul 2>&1",
                'reg add "HKLM\\Software\\Microsoft\\Office\\16.0'
                '\\Common\\Internet" /v ForceShellExecute /t REG_DWORD'
                " /d 1 /f >nul 2>&1",
                'if exist "%PIECES%" attrib +P -U "%PIECES%\\*" /S /D',
                "endlocal",
                "",
            )
        )
        try:
            bat_path.write_text(script, encoding="utf-8")
        except OSError:
            return None
        if os.name == "nt":
            try:
                subprocess.run(
                    ["cmd", "/c", str(bat_path)],
                    check=False,
                    capture_output=True,
                    timeout=120,
                )
            except (OSError, subprocess.SubprocessError):
                pass
            try:
                subprocess.run(
                    ["attrib", "+P", "-U", str(pieces / "*"), "/S", "/D"],
                    check=False,
                    capture_output=True,
                    timeout=120,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        return bat_path

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
    "LOCAL_PDF_BAT_NAME",
    "ONEDRIVE_ENV",
    "OneDriveAccountingBridge",
    "OneDriveSyncReport",
    "PILOTAGE_DIR_NAME",
    "PIECES_DIR_NAME",
    "PULL_STATE_FILENAME",
    "SHARED_DIR_NAME",
    "TABLEAUX_COMPTABLES_DIR_NAME",
    "pull_from_onedrive",
    "remove_duplicate_local_readable",
    "resolve_onedrive_root",
]
