"""P27-quater - Migrate the APD GED and repair FA1 / bank tier anomalies.

Usage::

    .venv-v3\\Scripts\\python.exe scripts/apply_p27quater_ged_fa1_and_bank_cleanup.py --tenant APD

The script is idempotent:

1. forces every ``FA\\s*1\\d{4,}`` row into ``VENTES_CLIENTS`` (role CLIENT,
   no fuel/expense sub-category) and canonicalises Ydes Auto Accessoires;
2. normalises ``REMISE ENCAISSEMENT N ...`` lines to
   ``CLIENTS_ENCAISSEMENT_GROUPE`` (CLIENT / VENTES_CLIENTS);
3. replaces generic bank tier noise (``PAR INTERNET``, ``VIR SEPA``, ...) with
   ``VIREMENT_A_QUALIFIER``;
4. moves legacy ``03_VALIDE_PRET_FEC/<CATEGORIE>`` files into the five GED
   sub-folders and rewrites their SQLite ``invoice_url``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from schemas.folder_state_schema import DocumentCategory  # noqa: E402
from services.accounting_memory_learning_service import (  # noqa: E402
    AccountingMemoryLearningService,
)
from services.bank_reconciliation_service import (  # noqa: E402
    CLIENTS_ENCAISSEMENT_GROUPE,
    CLIENT_SALES_CATEGORY,
    VIREMENT_A_QUALIFIER,
    bank_tier_defaults,
    contains_client_invoice_reference,
    is_remise_encaissement,
)
from services.folder_tree_service import (  # noqa: E402
    ensure_tree,
    migrate_validated_to_ged,
)
from services.sqlite_accounting_store import (  # noqa: E402
    DEFAULT_DB_PATH,
    SqliteAccountingStore,
)
from services.tier_reference_service import TierReferenceService  # noqa: E402

DEFAULT_TENANT = "APD"


def repair_rows(
    store: SqliteAccountingStore,
    tenant: str,
    tiers: TierReferenceService,
) -> tuple[int, int, int]:
    sales = 0
    remises = 0
    transfers = 0
    for row in store.list_master_lines(tenant):
        line_id = str(row.get("line_id") or "")
        if not line_id:
            continue
        line = store.get_line(tenant, line_id)
        if line is None:
            continue
        updated = dict(line)
        updated.pop("line_id", None)
        name = str(
            row.get("counterparty") or row.get("supplier") or ""
        ).strip()
        label = str(row.get("label") or "").strip()
        is_bank = str(row.get("document_type") or "").upper() in {
            "OFX",
            "QFX",
        } or line_id.upper().startswith("OFX-")
        changed = False
        if contains_client_invoice_reference(
            name,
            label,
            row.get("invoice_id"),
            row.get("invoice_url"),
            row.get("piece_pdf"),
        ):
            record = tiers.resolve(name) or tiers.resolve(label)
            canonical = (
                record.canonical
                if record is not None and record.role == "CLIENT"
                else name
            )
            updated.update(
                {
                    "category": CLIENT_SALES_CATEGORY,
                    "expense_subcategory": None,
                    "counterparty": canonical or name,
                    "is_classified_marker": (
                        f"TIER:{canonical or 'CLIENT'}:CLIENT"
                    ),
                }
            )
            changed = True
            sales += 1
        elif is_bank and is_remise_encaissement(label):
            updated.update(
                {
                    "counterparty": CLIENTS_ENCAISSEMENT_GROUPE,
                    "category": CLIENT_SALES_CATEGORY,
                    "expense_subcategory": None,
                    "is_classified_marker": (
                        f"TIER:{CLIENTS_ENCAISSEMENT_GROUPE}:CLIENT"
                    ),
                }
            )
            changed = True
            remises += 1
        elif is_bank and (
            bank_tier_defaults(label=label, counterparty=name)[0]
            == VIREMENT_A_QUALIFIER
        ):
            updated.update(
                {
                    "counterparty": VIREMENT_A_QUALIFIER,
                    "is_classified_marker": "A_QUALIFIER",
                }
            )
            changed = True
            transfers += 1
        if changed:
            store.force_upsert_document_line(tenant, line_id, updated)
    return sales, remises, transfers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument("--root", default=None)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument(
        "--no-migrate-folders",
        action="store_true",
        help="only repair SQLite rows, do not move the physical GED files",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = SqliteAccountingStore(args.db)
    tree = ensure_tree(args.tenant, args.root)
    memory = AccountingMemoryLearningService()
    tiers = TierReferenceService(
        store=store, memory=memory, tenant_id=args.tenant
    )
    tiers.bootstrap()
    sales, remises, transfers = repair_rows(store, args.tenant, tiers)
    moved: tuple[Path, ...] = ()
    if not args.no_migrate_folders:
        moved = migrate_validated_to_ged(
            args.tenant, args.root, store=store
        )
    print(f"Tenant : {args.tenant}")
    print(f"GED : {tree.root / args.tenant}")
    print(f"Lignes FA1* forcees en VENTES_CLIENTS : {sales}")
    print(f"Remises normalisees : {remises}")
    print(f"Virements a qualifier : {transfers}")
    print(f"Fichiers deplaces vers la GED : {len(moved)}")
    for path in moved:
        print(f"  - {path.relative_to(tree.root / args.tenant).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
