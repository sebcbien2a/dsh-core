"""P27-bis - Remove duplicate folders and stale ``_A_COMPLETER__*.xlsx``.

* merges the empty ``SCI APD`` folder into ``SCI_APD`` (moving any PDF to
  ``SCI_APD/00_INBOX``);
* deletes the legacy ``workspace/document_flow/default`` folder (already
  migrated to ``APD``);
* deletes ``_A_COMPLETER__1..4.xlsx`` duplicates;
* deletes the redundant ``C:\\Users\\<user>\\OneDrive\\APD_COMPTA_PARTAGE``
  folder because ``A:\\dsh-core`` is already natively synced.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.onedrive_accounting_bridge_service import (  # noqa: E402
    SHARED_DIR_NAME,
    resolve_onedrive_root,
)

TENANT = "APD"


def _merge_sci_apd(flow_root: Path) -> list[str]:
    actions: list[str] = []
    spaced = flow_root / "SCI APD"
    underscored = flow_root / "SCI_APD"
    if not spaced.is_dir():
        return actions
    underscored.mkdir(parents=True, exist_ok=True)
    inbox = underscored / "00_INBOX"
    inbox.mkdir(parents=True, exist_ok=True)
    for path in spaced.rglob("*"):
        if not path.is_file():
            continue
        target = inbox / path.name
        index = 1
        while target.exists():
            target = inbox / f"{path.stem}__{index}{path.suffix}"
            index += 1
        shutil.move(str(path), str(target))
        actions.append(f"SCI APD -> SCI_APD/00_INBOX : {path.name}")
    shutil.rmtree(spaced, ignore_errors=True)
    actions.append("Dossier 'SCI APD' supprimé")
    return actions


def _delete_default(flow_root: Path) -> list[str]:
    legacy = flow_root / "default"
    if not legacy.is_dir():
        return []
    shutil.rmtree(legacy, ignore_errors=True)
    return ["Dossier 'default' supprimé"]


def _delete_duplicate_workbooks(tenant_dir: Path) -> list[str]:
    actions: list[str] = []
    for path in sorted(tenant_dir.glob("_A_COMPLETER__*.xlsx")):
        path.unlink(missing_ok=True)
        actions.append(f"Doublon supprimé : {path.name}")
    return actions


def _delete_onedrive_shared(onedrive_dir: str | None) -> list[str]:
    root = resolve_onedrive_root(onedrive_dir)
    if root is None:
        return []
    shared = root / SHARED_DIR_NAME
    if not shared.is_dir():
        return []
    shutil.rmtree(shared, ignore_errors=True)
    return [f"Dossier OneDrive redondant supprimé : {shared}"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(REPO_ROOT))
    parser.add_argument("--tenant", default=TENANT)
    parser.add_argument("--onedrive-dir", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    flow_root = Path(args.root) / "workspace" / "document_flow"
    tenant_dir = flow_root / args.tenant
    actions: list[str] = []
    actions.extend(_merge_sci_apd(flow_root))
    actions.extend(_delete_default(flow_root))
    actions.extend(_delete_duplicate_workbooks(tenant_dir))
    actions.extend(_delete_onedrive_shared(args.onedrive_dir))
    for action in actions:
        print(action)
    print(f"Actions : {len(actions)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
