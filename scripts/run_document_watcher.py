#!/usr/bin/env python
"""Run the discreet DSH document-flow watcher.

Examples::

    python scripts/run_document_watcher.py --tenant default --interval 5
    python scripts/run_document_watcher.py --tenant default --once

The watcher ingests stabilised ``00_INBOX`` files, then applies the human
``_A_COMPLETER.xlsx`` decisions when the Excel lock is gone. Logs only the
useful events: detected file, routing to ``02_``/``03_``/``05_`` and Excel sync.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.document_flow_watcher import (
    DEFAULT_POLL_INTERVAL,
    DEFAULT_SETTLE_DELAY,
    DEFAULT_TENANT,
    DocumentFlowWatcher,
    WatcherAlreadyRunningError,
)
from services.sqlite_accounting_store import (
    SqliteAccountingStore,
    get_shared_sqlite_store,
)

LOGGER = logging.getLogger("dsh.watcher")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        help="Secondes entre deux cycles de surveillance (défaut: 5).",
    )
    parser.add_argument(
        "--settle-delay",
        type=float,
        default=DEFAULT_SETTLE_DELAY,
        help="Secondes de stabilité taille+mtime avant ingestion (défaut: 2).",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Racine du flux documentaire (défaut: DSH_DOCUMENT_FLOW_ROOT).",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Base SQLite comptable (défaut: DSH_ACCOUNTING_DB ou partagée).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Exécute un seul cycle puis quitte.",
    )
    parser.add_argument(
        "--no-auto-export",
        dest="auto_export",
        action="store_false",
        help=(
            "Désactive le rafraîchissement automatique des classeurs de "
            "synthèse, du grand livre fournisseur et du cockpit Obsidian."
        ),
    )
    parser.set_defaults(auto_export=True)
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    store = (
        SqliteAccountingStore(args.db)
        if args.db
        else get_shared_sqlite_store()
    )
    watcher = DocumentFlowWatcher(
        tenant_id=args.tenant,
        poll_interval=args.interval,
        settle_delay=args.settle_delay,
        root=args.root,
        store=store,
        auto_export=args.auto_export,
    )

    try:
        if args.once:
            report = watcher.run_once()
            LOGGER.info(
                "Cycle terminé: %d fichier(s) stabilisé(s), "
                "ingestion=%s, sync=%s, verrou_excel=%s, "
                "exports=%d, erreurs_export=%d",
                len(report.stabilized),
                "oui" if report.ingestion else "non",
                "différée" if report.deferred_lock else "oui",
                "présent" if report.deferred_lock else "absent",
                len(report.exports),
                len(report.export_errors),
            )
            return 0
        watcher.run_forever()
        return 0
    except WatcherAlreadyRunningError as exc:
        LOGGER.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        LOGGER.info("Arrêt demandé (clavier)")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
