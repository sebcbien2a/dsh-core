"""P27-bis - Shorten PDF piece filenames to 60 characters for OneDrive.

OneDrive rejects paths longer than 255 characters. This runner renames every
PDF under ``PIECES_JOINTES`` whose stem exceeds 60 characters, sanitises the
name (no special characters, no repeated spaces) and updates the SQLite
``piece_pdf`` / ``invoice_url`` references so the hyperlinks keep working.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.sqlite_accounting_store import (  # noqa: E402
    DEFAULT_DB_PATH,
    SqliteAccountingStore,
)

TENANT = "APD"
MAX_STEM = 60
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize(stem: str) -> str:
    text = unicodedata.normalize("NFKD", stem)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = _SAFE.sub("_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:MAX_STEM].rstrip("._-") or "piece"


def _unique(target: Path) -> Path:
    if not target.exists():
        return target
    index = 1
    while True:
        candidate = target.with_name(
            f"{target.stem[: MAX_STEM - 4]}_{index}{target.suffix}"
        )
        if not candidate.exists():
            return candidate
        index += 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default=TENANT)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--root", default=str(REPO_ROOT))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    export_dir = (
        Path(args.root)
        / "workspace"
        / "document_flow"
        / args.tenant
        / "04_EXPORT_COMPTA"
        / args.tenant
    )
    pieces = export_dir / "PIECES_JOINTES"
    if not pieces.is_dir():
        print(f"Dossier introuvable : {pieces}")
        return 1
    store = SqliteAccountingStore(args.db)
    renamed = 0
    for path in sorted(pieces.rglob("*.pdf")):
        if len(path.stem) <= MAX_STEM:
            continue
        target = _unique(path.with_name(f"{_sanitize(path.stem)}{path.suffix}"))
        old_relative = path.relative_to(export_dir).as_posix()
        new_relative = target.relative_to(export_dir).as_posix()
        path.rename(target)
        renamed += 1
        for row in store.list_master_lines(args.tenant):
            piece = str(row.get("piece_pdf") or "").replace("\\", "/")
            invoice_url = str(row.get("invoice_url") or "").replace("\\", "/")
            if piece != old_relative and invoice_url != old_relative:
                continue
            line = dict(row)
            line.pop("line_id", None)
            if piece == old_relative:
                line["piece_pdf"] = new_relative
            if invoice_url == old_relative:
                line["invoice_url"] = new_relative
            store.force_upsert_document_line(
                args.tenant, str(row.get("line_id") or ""), line
            )
    print(f"PDF raccourcis : {renamed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
