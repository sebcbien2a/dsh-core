"""P27-ter - Remove duplicate workbooks and stale ``_MIS_A_JOUR`` files.

Deletes, under ``workspace/document_flow/APD/``:

* ``*_default_*.xlsx`` legacy workbooks;
* ``*__1.xlsx`` ... ``*__5.xlsx`` duplicate exports;
* ``_A_COMPLETER_MIS_A_JOUR*.xlsx`` fallback copies.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TENANT = "APD"
_DUPLICATE = re.compile(r"__[1-5]\.xlsx$", re.IGNORECASE)


def _is_duplicate(path: Path) -> bool:
    name = path.name
    if "_default_" in name:
        return True
    if name.startswith("_A_COMPLETER_MIS_A_JOUR"):
        return True
    return bool(_DUPLICATE.search(name))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(REPO_ROOT))
    parser.add_argument("--tenant", default=TENANT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    tenant_dir = Path(args.root) / "workspace" / "document_flow" / args.tenant
    if not tenant_dir.is_dir():
        print(f"Dossier introuvable : {tenant_dir}")
        return 1
    removed = 0
    for path in sorted(tenant_dir.rglob("*.xlsx")):
        if not _is_duplicate(path):
            continue
        path.unlink(missing_ok=True)
        removed += 1
        print(f"Supprimé : {path.relative_to(tenant_dir)}")
    print(f"Doublons supprimés : {removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
