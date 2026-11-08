"""P15.3 - Exhaustive offline tests of the background document-flow watcher.

Covers OneDrive/scan stabilisation (size + mtime), the full ingestion then Excel
sync cycle, Excel lock deferral, single-instance locking and clean loop stop.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from openpyxl import load_workbook

from schemas.folder_state_schema import (
    DocumentCategory,
    FolderState,
)
from schemas.obsidian_vault_schema import NoteSource
from services.acompleter_workbook_service import SHEET_NAME, derive_document_id
from services.document_flow_watcher import (
    COCKPIT_NOTE_NAME,
    DocumentFlowWatcher,
    WatcherAlreadyRunningError,
)
from services import inbox_ingestion_service as iis
from services.folder_sync_service import FolderSyncService
from services.folder_tree_service import ensure_tree
from services.inbox_ingestion_service import ExtractedInvoice, InboxIngestionService
from services.obsidian_vault_service import VaultWriter, parse_note
from services.sqlite_accounting_store import SqliteAccountingStore

TENANT = "CLIENT_01"
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


def complete(**overrides) -> ExtractedInvoice:
    values = {
        "issue_date": "2026-01-15",
        "invoice_number": "FA-001",
        "supplier_name": "ACME SAS",
        "amount_ht": "100.00",
        "amount_tva": "20.00",
        "amount_ttc": "120.00",
        "category": DocumentCategory.FRAIS_GENERAUX,
        "confidence": 0.95,
    }
    values.update(overrides)
    return ExtractedInvoice(**values)


def constant(value):
    def extractor(_source: Path):
        return value

    return extractor


def put(directory: Path, name: str, data: bytes = b"%PDF-1.4 watcher") -> str:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def edit_workbook_row(sync: FolderSyncService, sha: str, **cells) -> None:
    columns = {"champs_manquants": 4, "categorie_suggeree": 5, "statut": 6}
    path = sync.workbook.workbook_path
    wb = load_workbook(path)
    ws = wb[SHEET_NAME]
    for index in range(2, ws.max_row + 1):
        if str(ws.cell(row=index, column=2).value or "").strip().lower() == sha:
            for name, value in cells.items():
                ws.cell(row=index, column=columns[name]).value = value
            wb.save(path)
            return
    raise AssertionError(f"row for {sha} not found")


def build(
    tmp_path: Path,
    extractor=None,
    clock=None,
    *,
    auto_export: bool = True,
    vault_writer=None,
    export_dir=None,
    onedrive_inbox=None,
):
    root = tmp_path / "flow"
    tree = ensure_tree(TENANT, root)
    store = SqliteAccountingStore(tmp_path / "accounting.sqlite3")
    ingestion = InboxIngestionService(
        TENANT,
        store,
        root,
        extractor=extractor or constant(complete()),
        onedrive_inbox=onedrive_inbox,
    )
    sync = FolderSyncService(TENANT, store, root)
    watcher = DocumentFlowWatcher(
        TENANT,
        poll_interval=5.0,
        settle_delay=2.0,
        root=root,
        store=store,
        ingestion=ingestion,
        onedrive_inbox=onedrive_inbox,
        sync=sync,
        clock=clock,
        auto_export=auto_export,
        export_dir=export_dir if export_dir is not None else tmp_path / "exports",
        vault_writer=vault_writer,
        vault_root=tmp_path / "vault",
        now_fn=lambda: NOW,
    )
    return tree, store, watcher, ingestion, sync


def stage_validated_piece(tree, sync, name: str = "facture.pdf") -> str:
    """Park a pending file and validate its workbook row (human VALIDE)."""
    pending = tree.state_dir(FolderState.A_COMPLETER)
    digest = put(pending, name, f"validated:{name}".encode())
    sync.workbook.upsert(
        digest, name, [], DocumentCategory.FRAIS_GENERAUX
    )
    edit_workbook_row(sync, digest, statut="VALIDE")
    return digest


# -- contracts ----------------------------------------------------------------


def test_defaults_and_validation(tmp_path):
    root = tmp_path / "flow"
    store = SqliteAccountingStore(tmp_path / "accounting.sqlite3")
    watcher = DocumentFlowWatcher(TENANT, root=root, store=store)
    assert watcher.tenant_id == TENANT
    assert watcher.poll_interval == 5.0
    assert watcher.settle_delay == 2.0
    assert watcher.lock_path == root.resolve() / TENANT / ".watcher.lock"
    assert watcher.lock_held is False

    with pytest.raises(ValueError):
        DocumentFlowWatcher(TENANT, poll_interval=0, root=root, store=store)
    with pytest.raises(ValueError):
        DocumentFlowWatcher(TENANT, settle_delay=-1, root=root, store=store)
    with pytest.raises(ValueError):
        DocumentFlowWatcher(TENANT, poll_interval="oops", root=root, store=store)


def test_empty_cycle_runs_sync_and_returns_reports(tmp_path):
    _tree, _store, watcher, _ingestion, _sync = build(tmp_path)
    report = watcher.run_once(now=10.0)
    assert report.stabilized == ()
    assert report.ingestion is not None
    assert report.ingestion.scanned == 0
    assert report.sync is not None
    assert report.deferred_lock is False
    assert watcher.lock_path.exists() is False


# -- settle_delay / partial reads --------------------------------------------


def test_file_still_growing_is_ignored_until_stabilised(tmp_path):
    clock = [100.0]
    tree, _store, watcher, _ingestion, _sync = build(
        tmp_path, clock=lambda: clock[0]
    )
    inbox = tree.state_dir(FolderState.INBOX)
    put(inbox, "facture.pdf", b"%PDF-1.4 a")

    first = watcher.run_once()
    assert first.stabilized == ()
    assert first.ingestion.scanned == 0

    clock[0] = 101.0
    (inbox / "facture.pdf").write_bytes(b"%PDF-1.4 growing content")
    second = watcher.run_once()
    assert second.stabilized == ()
    assert (inbox / "facture.pdf").is_file()

    clock[0] = 102.9
    assert watcher.run_once().stabilized == ()

    clock[0] = 103.0
    third = watcher.run_once()
    assert third.stabilized == ("facture.pdf",)
    assert third.ingestion is not None
    assert third.ingestion.moved_to_03 == 1
    destination = (
            tree.ged_dir("FACTURES_FOURNISSEURS")
            / "facture.pdf"
    )
    assert destination.is_file()


def test_mtime_change_with_same_size_restarts_stabilisation(tmp_path):
    clock = [0.0]
    tree, _store, watcher, _ingestion, _sync = build(
        tmp_path, clock=lambda: clock[0]
    )
    inbox = tree.state_dir(FolderState.INBOX)
    path = inbox / "scan.pdf"
    path.write_bytes(b"same-size-a")
    watcher.run_once()

    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    clock[0] = 1.0
    assert watcher.run_once().stabilized == ()

    clock[0] = 2.9
    assert watcher.run_once().stabilized == ()

    clock[0] = 3.0
    assert watcher.run_once().stabilized == ("scan.pdf",)


def test_process_inbox_ignores_paths_outside_inbox(tmp_path):
    tree, _store, _watcher, ingestion, _sync = build(tmp_path)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside")
    report = ingestion.process_inbox([outside])
    assert report.scanned == 0
    assert outside.is_file()
    assert list(tree.state_dir(FolderState.INBOX).iterdir()) == []


# -- OneDrive mirror ----------------------------------------------------------


def test_onedrive_mirror_is_stabilised_aspirated_then_ingested(tmp_path):
    clock = [0.0]
    mirror = tmp_path / "OneDrive" / "00_INBOX"
    put(mirror, "mobile.pdf", b"%PDF-1.4 mobile")
    tree, _store, watcher, _ingestion, _sync = build(
        tmp_path, clock=lambda: clock[0], onedrive_inbox=mirror
    )

    assert watcher.onedrive_inbox == mirror.resolve()
    first = watcher.run_once()
    assert first.stabilized == ()
    assert (mirror / "mobile.pdf").is_file()

    clock[0] = 2.0
    second = watcher.run_once()

    assert second.stabilized == ("mobile.pdf",)
    assert second.mirrored == 1
    assert second.deferred_io == 0
    assert second.ingestion is not None
    assert second.ingestion.moved_to_03 == 1
    assert any(trace == "MIRRORED:mobile.pdf" for trace in second.traces)
    assert (mirror / "mobile.pdf").exists() is False
    assert (
            tree.ged_dir("FACTURES_FOURNISSEURS")
            / "mobile.pdf"
    ).is_file()


def test_mirror_permission_error_is_retried_on_the_next_cycle(
    tmp_path, monkeypatch
):
    clock = [0.0]
    mirror = tmp_path / "OneDrive" / "00_INBOX"
    put(mirror, "mobile.pdf", b"%PDF-1.4 mobile")
    tree, _store, watcher, _ingestion, _sync = build(
        tmp_path, clock=lambda: clock[0], onedrive_inbox=mirror
    )
    real_move = iis._move

    def locked_move(source, destination):
        if Path(source).parent.resolve() == mirror.resolve():
            raise PermissionError("fichier ouvert dans un lecteur")
        return real_move(source, destination)

    monkeypatch.setattr(iis, "_move", locked_move)
    watcher.run_once()
    clock[0] = 2.0
    deferred = watcher.run_once()

    assert deferred.mirrored == 0
    assert deferred.deferred_io == 1
    assert "MIRROR_LOCKED:mobile.pdf" in deferred.traces
    assert (mirror / "mobile.pdf").is_file()

    monkeypatch.setattr(iis, "_move", real_move)
    clock[0] = 4.0
    retried = watcher.run_once()

    assert retried.mirrored == 1
    assert retried.deferred_io == 0
    assert retried.ingestion is not None
    assert retried.ingestion.moved_to_03 == 1
    assert (mirror / "mobile.pdf").exists() is False
    assert (
            tree.ged_dir("FACTURES_FOURNISSEURS")
            / "mobile.pdf"
    ).is_file()


# -- full cycle ---------------------------------------------------------------


def test_run_once_chains_inbox_ingestion_then_workbook_sync(tmp_path):
    clock = [0.0]
    tree, _store, watcher, _ingestion, sync = build(
        tmp_path, clock=lambda: clock[0]
    )
    inbox = tree.state_dir(FolderState.INBOX)
    pending = tree.state_dir(FolderState.A_COMPLETER)
    put(inbox, "new.pdf", b"new document")
    watcher.run_once()

    sha = put(pending, "old.pdf", b"old pending")
    sync.workbook.upsert(
        sha, "old.pdf", [], DocumentCategory.FRAIS_GENERAUX
    )
    edit_workbook_row(sync, sha, statut="VALIDE")

    clock[0] = 2.0
    report = watcher.run_once()

    assert report.stabilized == ("new.pdf",)
    assert report.ingestion is not None and report.ingestion.moved_to_03 == 1
    assert report.sync is not None and report.sync.moved_to_03 == 1
    assert (
        tree.ged_dir("FACTURES_FOURNISSEURS")
        / "new.pdf"
    ).is_file()
    assert (
        tree.ged_dir("FACTURES_FOURNISSEURS")
        / "old.pdf"
    ).is_file()


def test_excel_lock_defers_sync_without_blocking_ingestion(tmp_path):
    clock = [0.0]
    tree, _store, watcher, _ingestion, _sync = build(
        tmp_path, clock=lambda: clock[0]
    )
    inbox = tree.state_dir(FolderState.INBOX)
    put(inbox, "facture.pdf", b"stable")
    watcher.run_once()

    lock = tree.root / TENANT / "~$A_COMPLETER.xlsx"
    lock.write_bytes(b"lock")
    clock[0] = 2.0

    report = watcher.run_once()

    assert report.deferred_lock is True
    assert report.sync is None
    assert report.ingestion is not None and report.ingestion.moved_to_03 == 1
    assert any("WORKBOOK_LOCKED" in trace for trace in report.traces)


# -- mono-instance lock -------------------------------------------------------


def test_second_watcher_cannot_acquire_the_same_tenant_lock(tmp_path):
    _tree, _store, first, _ingestion, _sync = build(tmp_path)
    _tree2, _store2, second, _ingestion2, _sync2 = build(tmp_path)

    first.acquire_lock()
    try:
        assert first.lock_held is True
        with pytest.raises(WatcherAlreadyRunningError) as info:
            second.acquire_lock()
        assert info.value.code == "WATCHER_ALREADY_RUNNING"
        assert info.value.pid == os.getpid()
        with pytest.raises(WatcherAlreadyRunningError):
            second.run_once()
    finally:
        first.release_lock()

    assert first.lock_path.exists() is False
    second.acquire_lock()
    second.release_lock()
    assert second.lock_path.exists() is False


def test_context_manager_releases_lock(tmp_path):
    _tree, _store, watcher, _ingestion, _sync = build(tmp_path)
    with watcher as owned:
        assert owned is watcher
        assert watcher.lock_path.is_file()
    assert watcher.lock_path.exists() is False


def test_run_forever_stops_cleanly_and_releases_lock(tmp_path, monkeypatch):
    _tree, _store, watcher, _ingestion, _sync = build(tmp_path)
    calls: list[bool] = []

    def fake_run_once(*, now=None):
        calls.append(True)
        watcher.request_stop()

    monkeypatch.setattr(watcher, "run_once", fake_run_once)

    watcher.run_forever()

    assert calls == [True]
    assert watcher.lock_path.exists() is False


# -- auto export / cockpit ----------------------------------------------------


def test_auto_export_refreshes_summaries_ledger_and_cockpit(tmp_path):
    vault = VaultWriter(tmp_path / "vault", now=lambda: NOW)
    tree, _store, watcher, _ingestion, sync = build(tmp_path, vault_writer=vault)
    stage_validated_piece(tree, sync)
    put(
        tree.state_dir(FolderState.A_COMPLETER),
        "en-attente.pdf",
        b"waiting document",
    )

    report = watcher.run_once(now=0.0)

    assert report.sync is not None and report.sync.moved_to_03 == 1
    assert any(
        trace.startswith("EXPORT:SUMMARY:FRAIS_GENERAUX:")
        for trace in report.exports
    )
    assert any(trace.startswith("EXPORT:LEDGER:") for trace in report.exports)
    assert "EXPORT:COCKPIT:created" in report.exports
    assert report.export_errors == ()

    export_root = tmp_path / "exports" / TENANT
    assert (
        export_root / f"SYNTHESE_{TENANT}_FRAIS_GENERAUX.xlsx"
    ).is_file()
    assert (
        export_root / f"GRAND_LIVRE_{TENANT}_TOUS_FOURNISSEURS.xlsx"
    ).is_file()

    cockpit = (
        tmp_path / "vault" / "01-contexte" / f"{COCKPIT_NOTE_NAME}.md"
    )
    assert cockpit.is_file()
    parsed = parse_note(cockpit.read_text(encoding="utf-8-sig"))
    assert parsed is not None
    frontmatter, body = parsed
    assert frontmatter["source"] == "agent"
    assert "Cockpit comptable" in body
    assert "**1** pièce(s)" in body
    assert "FRAIS_GENERAUX" in body
    assert "facture.pdf" in body
    assert "residual_delta = 0" in body
    assert "[[01-contexte/active|Contexte actif]]" in body


def test_no_auto_export_keeps_documents_moving_without_side_effects(tmp_path):
    tree, _store, watcher, _ingestion, sync = build(
        tmp_path, auto_export=False
    )
    stage_validated_piece(tree, sync)

    report = watcher.run_once(now=0.0)

    assert report.sync is not None and report.sync.moved_to_03 == 1
    assert report.exports == ()
    assert report.export_errors == ()
    assert (tmp_path / "exports").exists() is False
    cockpit = (
        tmp_path / "vault" / "01-contexte" / f"{COCKPIT_NOTE_NAME}.md"
    )
    assert cockpit.exists() is False


def test_auto_export_cockpit_reports_reconciliation_residual(tmp_path):
    vault = VaultWriter(tmp_path / "vault", now=lambda: NOW)
    tree, store, watcher, _ingestion, sync = build(
        tmp_path, vault_writer=vault
    )
    digest = stage_validated_piece(tree, sync, "facture-acme.pdf")
    doc_id = derive_document_id(digest)
    store.upsert_document_line(
        TENANT,
        doc_id,
        {
            "operation_date": "2026-01-15",
            "label": "FA-001",
            "counterparty": "ACME SAS",
            "invoice_id": doc_id,
            "invoice_url": None,
            "category": DocumentCategory.FRAIS_GENERAUX.value,
            "amount_ht": "100.00",
            "amount_tva": "20.00",
            "amount_ttc": "120.00",
            "status": "EN_ATTENTE",
        },
    )
    store.add_line(
        TENANT,
        "PAY-1",
        {
            "operation_date": "2026-01-20",
            "label": "Paiement ACME",
            "counterparty": "ACME SAS",
            "debit_amount": "90.00",
            "credit_amount": "0.00",
            "currency": "EUR",
            "status": "PENDING",
        },
    )

    report = watcher.run_once(now=0.0)

    assert report.export_errors == ()
    cockpit = (
        tmp_path / "vault" / "01-contexte" / f"{COCKPIT_NOTE_NAME}.md"
    )
    body = cockpit.read_text(encoding="utf-8-sig")
    assert "ACME SAS" in body
    assert "écart résiduel" in body
    assert "-30.00" in body


def test_auto_export_errors_do_not_block_document_flow(tmp_path, monkeypatch):
    tree, _store, watcher, _ingestion, sync = build(tmp_path)
    stage_validated_piece(tree, sync)

    def boom(*_args, **_kwargs):
        raise RuntimeError("export cassé")

    monkeypatch.setattr(
        "services.document_flow_watcher.CategorySummaryExporter"
        ".export_category_summary",
        boom,
    )

    report = watcher.run_once(now=0.0)

    assert report.sync is not None and report.sync.moved_to_03 == 1
    assert any(
        error.startswith("SUMMARY:FRAIS_GENERAUX:")
        for error in report.export_errors
    )
    assert any(
        "AUTO_EXPORT_ERROR:SUMMARY" in trace for trace in report.traces
    )
    assert any(trace.startswith("EXPORT:LEDGER:") for trace in report.exports)
    assert "EXPORT:COCKPIT:created" in report.exports
    destination = (
            tree.ged_dir("FACTURES_FOURNISSEURS")
            / "facture.pdf"
    )
    assert destination.is_file()


def test_auto_export_preserves_human_owned_cockpit(tmp_path):
    vault = VaultWriter(tmp_path / "vault", now=lambda: NOW)
    first = vault.write_context_note(
        COCKPIT_NOTE_NAME,
        body=(
            "# Cockpit humain\n\n"
            "- [[01-contexte/active|Contexte actif]]\n"
        ),
        wikilinks=("01-contexte/active",),
        source=NoteSource.HUMAN,
    )
    original = first.path.read_bytes()

    tree, _store, watcher, _ingestion, sync = build(
        tmp_path, vault_writer=vault
    )
    stage_validated_piece(tree, sync)

    report = watcher.run_once(now=0.0)

    assert "EXPORT:COCKPIT:preserved" in report.exports
    assert first.path.read_bytes() == original
