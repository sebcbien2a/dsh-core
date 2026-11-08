"""P27-ter - Apply the director's three validated _A_COMPLETER decisions.

The director validated three lines in ``_A_COMPLETER.xlsx``:

* ``F-2026-037`` (EI - ATIA SONIA / TALENT&CO, 1 800,00 €) -> ``SONIA_ATIA``
  / ``FRAIS_GENERAUX`` / ``HONORAIRES_JURIDIQUES`` / 2026 / VALIDE;
* ``Invoice-GENMCYCQ-0001`` and ``Receipt-2136-4590`` (OpenRouter, Inc) ->
  ``OPENROUTER`` / ``FRAIS_GENERAUX`` / ``INFORMATIQUE_WEB`` / 2026 / VALIDE.

The script writes the decisions to SQLite and removes the lines from the
readable workbook on the next refresh.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from schemas.folder_state_schema import DocumentCategory  # noqa: E402
from services.acompleter_workbook_service import derive_document_id  # noqa: E402
from services.sqlite_accounting_store import (  # noqa: E402
    DEFAULT_DB_PATH,
    SqliteAccountingStore,
)

TENANT = "APD"
DECISIONS: tuple[dict[str, str], ...] = (
    {
        "sha256": "6363a2056fe2b80381b4eabdc10fff0c32e1db9f155d61ce26102d085fa1efc1",
        "counterparty": "SONIA_ATIA",
        "category": DocumentCategory.FRAIS_GENERAUX.value,
        "expense_subcategory": "HONORAIRES_JURIDIQUES",
        "fiscal_year_target": "2026",
    },
    {
        "sha256": "e76e965caef7b97d80d7d3e567e4d02792a43785dd3d0bb03067230074624401",
        "counterparty": "OPENROUTER",
        "category": DocumentCategory.FRAIS_GENERAUX.value,
        "expense_subcategory": "INFORMATIQUE_WEB",
        "fiscal_year_target": "2026",
    },
    {
        "sha256": "08f3dc5f0d637219d69e646ba9bed893f8556b01e257098ec243190d8c848936",
        "counterparty": "OPENROUTER",
        "category": DocumentCategory.FRAIS_GENERAUX.value,
        "expense_subcategory": "INFORMATIQUE_WEB",
        "fiscal_year_target": "2026",
    },
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default=TENANT)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = SqliteAccountingStore(args.db)
    applied = 0
    for decision in DECISIONS:
        line_id = derive_document_id(decision["sha256"])
        line = store.get_line(args.tenant, line_id)
        if line is None:
            print(f"Ligne absente : {line_id}")
            continue
        updated = dict(line)
        updated.pop("line_id", None)
        updated.update(
            {
                "counterparty": decision["counterparty"],
                "category": decision["category"],
                "expense_subcategory": decision["expense_subcategory"],
                "fiscal_year_target": decision["fiscal_year_target"],
                "status": "VALIDE",
                "reconciliation_status": "VALIDE",
            }
        )
        store.force_upsert_document_line(args.tenant, line_id, updated)
        applied += 1
        print(
            f"{decision['counterparty']} : {line.get('invoice_id') or line_id} "
            "-> VALIDE"
        )
    print(f"Décisions appliquées : {applied}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
