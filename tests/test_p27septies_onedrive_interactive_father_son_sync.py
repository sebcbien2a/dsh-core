"""P27-septies - interactive father/son OneDrive sync for the two master files.

``_A_COMPLETER.xlsx`` and ``REFERENTIEL_TIERS_APD.xlsx`` live in
``Compta_APD/Tableaux_Comptables`` and follow the "last writer wins" rule: the
fresher OneDrive copy is pulled before a stage, the fresher local copy is
pushed after it, and the historical export-folder tier workbook is never
recreated.
"""

from __future__ import annotations

import os
from pathlib import Path

from openpyxl import Workbook

from services.onedrive_accounting_bridge_service import (
    TABLEAUX_COMPTABLES_DIR_NAME,
    OneDriveAccountingBridge,
)
from services.tier_reference_service import (
    TIER_REFERENCE_FILENAME,
    purge_duplicate_tier_workbooks,
)

TENANT = "APD"
READABLE = "_A_COMPLETER.xlsx"


def _workspace(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    flow = tmp_path / "workspace" / "document_flow"
    tenant_dir = flow / TENANT
    readable_dir = tenant_dir / "02_A_COMPLETER"
    readable_dir.mkdir(parents=True)
    export_dir = tenant_dir / "04_EXPORT_COMPTA" / TENANT
    export_dir.mkdir(parents=True)
    local_readable = readable_dir / READABLE
    local_tier = tenant_dir / TIER_REFERENCE_FILENAME
    return flow, tenant_dir, export_dir, local_readable, local_tier


def _onedrive(tmp_path: Path) -> Path:
    directory = tmp_path / "OneDrive" / "Compta_APD" / TABLEAUX_COMPTABLES_DIR_NAME
    directory.mkdir(parents=True)
    return directory


def _bridge(
    flow: Path, export_dir: Path, onedrive: Path
) -> OneDriveAccountingBridge:
    return OneDriveAccountingBridge(
        TENANT,
        root=flow,
        onedrive_dir=onedrive,
        export_dir=export_dir,
        enabled=True,
    )


def _touch_newer(path: Path, reference: Path, seconds: int = 120) -> None:
    base = reference.stat().st_mtime if reference.exists() else 0
    os.utime(path, (base + seconds, base + seconds))


def test_direct_mode_uses_tableaux_comptables_root(tmp_path):
    flow, _tenant, export_dir, _readable, _tier = _workspace(tmp_path)
    onedrive = _onedrive(tmp_path)
    bridge = _bridge(flow, export_dir, onedrive)
    assert bridge.direct_mode is True
    assert bridge.shared_dir == onedrive
    assert bridge.pilotage_dir == onedrive
    assert bridge.expert_dir == onedrive


def test_newer_onedrive_files_are_pulled_into_both_local_copies(tmp_path):
    flow, tenant_dir, export_dir, local_readable, local_tier = _workspace(tmp_path)
    local_readable.write_bytes(b"local-old")
    local_tier.write_bytes(b"tier-old")
    onedrive = _onedrive(tmp_path)
    remote_readable = onedrive / READABLE
    remote_tier = onedrive / TIER_REFERENCE_FILENAME
    remote_readable.write_bytes(b"father-readable")
    remote_tier.write_bytes(b"father-tier")
    _touch_newer(remote_readable, local_readable)
    _touch_newer(remote_tier, local_tier)

    bridge = _bridge(flow, export_dir, onedrive)
    pulled = bridge.pull_director_files()

    assert local_readable.read_bytes() == b"father-readable"
    assert (tenant_dir / READABLE).read_bytes() == b"father-readable"
    assert local_tier.read_bytes() == b"father-tier"
    assert {path.name for path in pulled} == {READABLE, TIER_REFERENCE_FILENAME}
    assert not (export_dir / TIER_REFERENCE_FILENAME).exists()


def test_push_uses_last_writer_wins_and_preserves_father_edit(tmp_path):
    flow, _tenant, export_dir, local_readable, local_tier = _workspace(tmp_path)
    local_readable.write_bytes(b"son-readable")
    local_tier.write_bytes(b"son-tier")
    onedrive = _onedrive(tmp_path)
    remote_readable = onedrive / READABLE
    remote_tier = onedrive / TIER_REFERENCE_FILENAME
    remote_readable.write_bytes(b"old-remote")
    remote_tier.write_bytes(b"old-remote-tier")
    _touch_newer(local_readable, remote_readable)
    _touch_newer(local_tier, remote_tier)

    bridge = _bridge(flow, export_dir, onedrive)
    pushed = bridge.push_director_files()

    assert remote_readable.read_bytes() == b"son-readable"
    assert remote_tier.read_bytes() == b"son-tier"
    assert {path.name for path in pushed} == {READABLE, TIER_REFERENCE_FILENAME}

    # The father saves a fresher edit on his own PC: the son's push must keep it.
    remote_readable.write_bytes(b"father-newer")
    remote_tier.write_bytes(b"father-newer-tier")
    _touch_newer(remote_readable, local_readable, seconds=600)
    _touch_newer(remote_tier, local_tier, seconds=600)
    pushed_again = bridge.push_director_files()
    assert pushed_again == ()
    assert remote_readable.read_bytes() == b"father-newer"
    assert remote_tier.read_bytes() == b"father-newer-tier"


def test_purge_removes_the_export_folder_tier_duplicate(tmp_path):
    flow = tmp_path / "workspace" / "document_flow"
    tenant_dir = flow / TENANT
    tenant_dir.mkdir(parents=True)
    canonical = tenant_dir / TIER_REFERENCE_FILENAME
    workbook = Workbook()
    workbook.active["A1"] = "master"
    workbook.save(canonical)
    duplicate = tenant_dir / "04_EXPORT_COMPTA" / TENANT / TIER_REFERENCE_FILENAME
    duplicate.parent.mkdir(parents=True)
    duplicate.write_bytes(b"duplicate")

    removed = purge_duplicate_tier_workbooks(tenant_dir)

    assert duplicate in removed
    assert not duplicate.exists()
    assert canonical.is_file()
