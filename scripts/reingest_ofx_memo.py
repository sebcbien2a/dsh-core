"""P27-bis - Re-ingest OFX statements with the full ``NAME | MEMO`` label.

The Caisse d'Epargne OFX file stores the supplier name of an ``LCR`` in the
``MEMO`` tag. This runner re-parses every ``.ofx`` file of the tenant and
force-updates the existing SQLite lines so the supplier is no longer
``NON IDENTIFIE``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.ofx_bank_statement_parser import parse_ofx_statement  # noqa: E402
from services.sqlite_accounting_store import (  # noqa: E402
    DEFAULT_DB_PATH,
    SqliteAccountingStore,
)

TENANT = "APD"


def _ofx_paths(root: Path) -> list[Path]:
    sources = root / "workspace" / "document_flow" / TENANT / "04_EXPORT_COMPTA" / "SOURCES_REEL"
    paths: list[Path] = []
    if sources.is_dir():
        paths.extend(sources.glob("*.ofx"))
        paths.extend(sources.glob("*.qfx"))
    return sorted(dict.fromkeys(paths), key=lambda item: item.name.casefold())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default=TENANT)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--root", default=str(REPO_ROOT))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = SqliteAccountingStore(args.db)
    paths = _ofx_paths(Path(args.root))
    if not paths:
        print("Aucun fichier OFX trouvé.")
        return 1
    total = 0
    for path in paths:
        statement = parse_ofx_statement(path.read_bytes())
        for transaction in statement.transactions:
            line = transaction.as_reconciliation_line()
            store.force_upsert_document_line(
                args.tenant, line["line_id"], line
            )
            total += 1
        print(f"{path.name}: {len(statement.transactions)} ligne(s)")
    print(f"Total ré-ingéré : {total} ligne(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
