"""P27-quater - Resilient Gmail IMAP + local ``00_INBOX`` runner.

Usage::

    .venv-v3\\Scripts\\python.exe scripts/fetch_gmail_inbox.py --tenant APD

The runner never blocks when ``GMAIL_APP_PASSWORD`` is missing: it prints an
explicit warning, skips the remote IMAP pass and still ingests the local
``workspace/document_flow/APD/00_INBOX`` through the normal GED pipeline.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.folder_tree_service import ensure_tree  # noqa: E402
from services.gmail_imap_sync_service import (  # noqa: E402
    DEFAULT_TENANT,
    GMAIL_PORT,
    GMAIL_HOST,
    MISSING_CREDENTIALS_WARNING,
    GmailImapSyncService,
    gmail_credentials,
)
from services.inbox_ingestion_service import InboxIngestionService  # noqa: E402
from services.sqlite_accounting_store import (  # noqa: E402
    DEFAULT_DB_PATH,
    SqliteAccountingStore,
)


def _password_present() -> bool:
    if os.environ.get("GMAIL_APP_PASSWORD", "").strip():
        return True
    return bool(os.environ.get("DSH_MAIL_PASSWORD", "").strip())


def _print_sync_report(report) -> None:
    print("=" * 78, flush=True)
    print("RELEVE GMAIL IMAP - APD", flush=True)
    print("=" * 78, flush=True)
    if not report.connected:
        print(
            "Connexion IMAP ignoree : "
            + ", ".join(report.skipped or ("INCONNUE",)),
            flush=True,
        )
        return
    print(f"Dossiers scannes : {', '.join(report.folders)}", flush=True)
    print(
        f"Messages : {report.messages_scanned} | Pieces deposees : "
        f"{report.attachments_staged} | Doublons SHA-256 : "
        f"{report.duplicates_skipped} | Ignorees : "
        f"{report.attachments_ignored}",
        flush=True,
    )
    print(
        f"Labels appliques : {report.labels_synced} | Messages archives "
        f"hors INBOX : {report.messages_archived}",
        flush=True,
    )
    if report.pdf_attached:
        print(
            "PDF rattaches : " + ", ".join(report.pdf_attached), flush=True
        )
    if report.pdf_missing:
        print(
            "PDF manquants : " + ", ".join(report.pdf_missing), flush=True
        )
    for error in report.errors:
        print(f"ERREUR IMAP : {error}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument("--root", default=None)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument(
        "--no-ingest",
        action="store_true",
        help="skip the local 00_INBOX ingestion pass",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / ".env")
    except Exception:  # noqa: BLE001 - dotenv is optional
        pass

    store = SqliteAccountingStore(args.db)
    tree = ensure_tree(args.tenant, args.root)
    print(f"Dossier documentaire : {tree.root / args.tenant}", flush=True)

    if gmail_credentials() is None:
        print(MISSING_CREDENTIALS_WARNING, flush=True)
    else:
        print(
            f"Connexion IMAP {GMAIL_HOST}:{GMAIL_PORT} ...", flush=True
        )
        service = GmailImapSyncService(
            args.tenant,
            store=store,
            root=args.root,
            classify_messages=True,
            ingest_for_classification=True,
        )
        _print_sync_report(service.sync())

    if not args.no_ingest:
        report = InboxIngestionService(
            args.tenant, store, args.root
        ).process_inbox()
        print(
            f"INGESTION 00_INBOX : {report.scanned} scanne(s), "
            f"{report.moved_to_03} valide(s), "
            f"{report.moved_to_02} en attente, "
            f"{report.duplicates} doublon(s), {report.archived} archive(s)",
            flush=True,
        )
        for trace in report.traces:
            if any(
                marker in trace
                for marker in ("FAILED", "ERROR", "UNREADABLE", "UNIFIED")
            ):
                print(f"  - {trace}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
