"""P27 - Ingest the SOLTA / LR COLOR invoice 2601616 (05/11/2026).

The director's screenshot shows a real supplier invoice:

* supplier: SOLTA / LR COLOR (canonical ``SOLTA_LR_COLOR``)
* invoice: ``2601616`` dated ``2026-11-05``, due ``2026-12-05``
* HT ``140,40 €`` / TVA ``28,08 €`` / TTC ``168,48 €``

The line is written as a validated purchase so it enters the supplier ledger
and the CA12 VAT simulation immediately.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from schemas.folder_state_schema import DocumentCategory  # noqa: E402
from services.sqlite_accounting_store import (  # noqa: E402
    DEFAULT_DB_PATH,
    SqliteAccountingStore,
)

TENANT = "APD"
LINE_ID = "PURCHASE-2601616"
INVOICE = {
    "operation_date": "2026-11-05",
    "label": "Facture 2601616 SOLTA / LR COLOR",
    "counterparty": "SOLTA_LR_COLOR",
    "invoice_id": "2601616",
    "category": DocumentCategory.ACHATS_MARCHANDISES.value,
    "document_type": "FACTURE",
    "amount_ht": "140.40",
    "amount_tva": "28.08",
    "amount_ttc": "168.48",
    "due_date": "2026-12-05",
    "payment_method": "VIREMENT",
    "status": "VALIDE",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default=TENANT)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = SqliteAccountingStore(args.db)
    result = store.force_upsert_document_line(args.tenant, LINE_ID, INVOICE)
    print(f"Facture 2601616 SOLTA / LR COLOR : {result}")
    print("HT 140,40 € / TVA 28,08 € / TTC 168,48 € — échéance 05/12/2026")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
