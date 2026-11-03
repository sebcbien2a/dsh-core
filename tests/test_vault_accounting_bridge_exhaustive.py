"""P15.1 - Vault <-> accounting validation event bridge (offline, exhaustive).

Covers the supplier entity enrichment (relations, wikilinks, last piece date),
the strict human review layer (``locked`` / ``source: human`` / ``content_hash``
divergence), dated events, and the non-blocking contract: a vault crash never
alters the SQLite transaction nor the file move to ``03_VALIDE_PRET_FEC``.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from openpyxl import load_workbook

from schemas.folder_state_schema import (
    DocumentCategory,
    DocumentStatus,
    FolderState,
)
from schemas.obsidian_vault_schema import EVENTS_DIR, NoteSource
from services.acompleter_workbook_service import SHEET_NAME, derive_document_id
from services.folder_sync_service import FolderSyncService
from services.folder_tree_service import ensure_tree
from services.inbox_ingestion_service import ExtractedInvoice, InboxIngestionService
from services.obsidian_vault_service import (
    OUTCOME_PRESERVED,
    OUTCOME_UNCHANGED,
    VaultWriter,
    parse_note,
)
from services.sqlite_accounting_store import SqliteAccountingStore
from services.vault_accounting_bridge import (
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_WRITTEN,
    VaultAccountingBridge,
    VaultBridgeReport,
    VaultValidationRecord,
    entity_slug,
)

TENANT = "CLIENT_01"
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
DATA = b"%PDF-1.4 bridge"
SHA = hashlib.sha256(DATA).hexdigest()


def record(**overrides) -> VaultValidationRecord:
    values = {
        "tenant_id": TENANT,
        "sha256": SHA,
        "nom_fichier": "facture-acme.pdf",
        "supplier_name": "ACME SAS",
        "invoice_number": "FA-001",
        "issue_date": "2026-01-15",
        "amount_ht": "100.00",
        "amount_ttc": "120.00",
        "category": "FRAIS_GENERAUX",
        "document_id": derive_document_id(SHA),
        "relative_path": "03_VALIDE_PRET_FEC/FRAIS_GENERAUX/facture-acme.pdf",
        "currency": "EUR",
    }
    values.update(overrides)
    return VaultValidationRecord(**values)


def bridge(tmp_path: Path, clock=None) -> VaultAccountingBridge:
    writer = VaultWriter(tmp_path / "vault", now=clock or (lambda: NOW))
    return VaultAccountingBridge(writer=writer)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def note(path: Path) -> tuple[dict, str]:
    parsed = parse_note(read(path))
    assert parsed is not None
    return parsed


def put(directory: Path, name: str, data: bytes = DATA) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(data)
    return hashlib.sha256(data).hexdigest()


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


def build_flow(tmp_path: Path, vault_bridge=None):
    root = tmp_path / "flow"
    tree = ensure_tree(TENANT, root)
    store = SqliteAccountingStore(tmp_path / "accounting.sqlite3")
    return tree, store, root


def inbox_service(tmp_path: Path, extractor, vault_bridge=None):
    tree, store, root = build_flow(tmp_path)
    service = InboxIngestionService(
        TENANT,
        store,
        root,
        extractor=extractor,
        vault_bridge=vault_bridge,
    )
    return tree, store, service, tree.state_dir(FolderState.INBOX)


def edit_workbook_row(service: FolderSyncService, sha: str, **cells) -> None:
    columns = {
        "champs_manquants": 4,
        "categorie_suggeree": 5,
        "statut": 6,
    }
    path = service.workbook.workbook_path
    wb = load_workbook(path)
    ws = wb[SHEET_NAME]
    for index in range(2, ws.max_row + 1):
        if str(ws.cell(row=index, column=2).value or "").strip().lower() == sha:
            for name, value in cells.items():
                ws.cell(row=index, column=columns[name]).value = value
            wb.save(path)
            return
    raise AssertionError(f"row for {sha} not found")


# -- slugs & record contract --------------------------------------------------


def test_entity_slug_folds_accents_spaces_and_case():
    assert entity_slug("ACME SAS") == "acme-sas"
    assert entity_slug("Établissements Müller & Fils") == "etablissements-muller-fils"
    assert entity_slug("  ---  ") == "fournisseur-inconnu"
    assert entity_slug(None) == "fournisseur-inconnu"


def test_validation_record_normalises_and_derives_ids():
    item = record(issue_date=NOW, amount_ttc="120,5", currency="eur")
    assert item.issue_date == "2026-11-03"
    assert item.amount_ttc == "120.50"
    assert item.currency == "EUR"
    assert item.event_id == f"validation-{derive_document_id(SHA)}"
    assert item.piece_slug == "piece-fa-001"
    assert item.supplier_slug == "acme-sas"
    assert "FA-001" in item.summary()
    assert "120.50 EUR TTC" in item.summary()


def test_validation_record_requires_identity_fields():
    with pytest.raises(ValueError):
        VaultValidationRecord(tenant_id="", sha256=SHA, nom_fichier="a.pdf")
    with pytest.raises(ValueError):
        VaultValidationRecord(tenant_id=TENANT, sha256="", nom_fichier="a.pdf")
    with pytest.raises(ValueError):
        VaultValidationRecord(tenant_id=TENANT, sha256=SHA, nom_fichier="")


def test_bridge_report_defaults_are_skipped():
    report = VaultBridgeReport()
    assert report.status == "skipped"
    assert report.entity is None and report.event is None
    assert report.errors == ()
    assert report.written is False


# -- entity & event export ----------------------------------------------------


def test_notify_creates_supplier_entity_with_relation_and_wikilinks(tmp_path):
    vault = bridge(tmp_path)
    report = vault.notify_validated(record())

    assert report.status == STATUS_WRITTEN
    assert report.entity is not None and report.entity.status == "created"
    assert report.event is not None and report.event.status == "created"

    entity_path = tmp_path / "vault" / "04-graph" / "entities" / "acme-sas.md"
    frontmatter, body = note(entity_path)
    assert frontmatter["type"] == "entity"
    assert frontmatter["entity"] == "acme-sas"
    assert frontmatter["relations"] == [
        {"predicate": "achat", "target": "piece-fa-001"}
    ]
    assert "[[acme-sas]]" in body
    assert "[[piece-fa-001]]" in body
    assert "Dernière pièce" in frontmatter["summary"]
    assert "2026-01-15" in frontmatter["summary"]
    assert frontmatter["source"] == NoteSource.AGENT.value


def test_notify_writes_dated_event_in_memory_events(tmp_path):
    vault = bridge(tmp_path)
    report = vault.notify_validated(record())
    event_path = (
        tmp_path
        / "vault"
        / EVENTS_DIR
        / f"validation-{derive_document_id(SHA)}.md"
    )
    assert event_path.is_file()
    frontmatter, body = note(event_path)
    assert frontmatter["type"] == "event"
    assert frontmatter["entity"] == "acme-sas"
    assert frontmatter["occurred_at"] == "2026-01-15"
    assert "[[acme-sas]]" in body
    assert "[[piece-fa-001]]" in body
    assert report.event.path == event_path


def test_second_validation_merges_relation_without_duplication(tmp_path):
    vault = bridge(tmp_path)
    first = record(invoice_number="FA-001")
    second = record(
        invoice_number="FA-002",
        document_id=derive_document_id("b" * 64),
        sha256="b" * 64,
    )
    assert vault.notify_validated(first).status == STATUS_WRITTEN
    result = vault.notify_validated(second)
    assert result.status == STATUS_WRITTEN

    entity_path = tmp_path / "vault" / "04-graph" / "entities" / "acme-sas.md"
    frontmatter, _body = note(entity_path)
    targets = [relation["target"] for relation in frontmatter["relations"]]
    assert targets == ["piece-fa-001", "piece-fa-002"]

    replay = vault.notify_validated(second)
    assert replay.entity is not None
    assert replay.entity.status == OUTCOME_UNCHANGED
    assert replay.event is not None and replay.event.status == OUTCOME_UNCHANGED
    frontmatter, _body = note(entity_path)
    assert [relation["target"] for relation in frontmatter["relations"]] == targets


def test_missing_supplier_still_records_a_dated_event(tmp_path):
    vault = bridge(tmp_path)
    report = vault.notify_validated(
        record(supplier_name=None, invoice_number=None)
    )
    assert report.status == STATUS_WRITTEN
    assert report.entity is None
    assert report.supplier is None
    event_path = (
        tmp_path
        / "vault"
        / EVENTS_DIR
        / f"validation-{derive_document_id(SHA)}.md"
    )
    frontmatter, body = note(event_path)
    assert frontmatter["entity"] == "comptabilite"
    assert "[[comptabilite]]" in body


# -- human review layer -------------------------------------------------------


@pytest.mark.parametrize(
    "header,reason",
    [
        ("source: human\nlocked: false", "human_source"),
        ("source: agent\nlocked: true", "locked"),
        ("source: agent\nlocked: false\nstatus: preserved", "preserved_status"),
    ],
)
def test_human_or_locked_supplier_note_is_never_overwritten(
    tmp_path, header, reason
):
    vault = bridge(tmp_path)
    vault.notify_validated(record())
    entity_path = tmp_path / "vault" / "04-graph" / "entities" / "acme-sas.md"
    human_note = (
        f"---\n{header}\n---\n\n# Note humaine ACME\n\n"
        "Contexte protégé — [[acme-sas]]\n"
    )
    entity_path.write_text(human_note, encoding="utf-8", newline="\n")
    before = entity_path.read_bytes()

    report = vault.notify_validated(record(invoice_number="FA-099"))

    assert report.entity is not None
    assert report.entity.status == OUTCOME_PRESERVED
    assert report.entity.detail == reason
    assert entity_path.read_bytes() == before
    assert report.event is not None
    assert report.status == STATUS_WRITTEN


def test_content_hash_divergence_protects_supplier_note(tmp_path):
    vault = bridge(tmp_path)
    vault.notify_validated(record())
    entity_path = tmp_path / "vault" / "04-graph" / "entities" / "acme-sas.md"
    original = read(entity_path)
    entity_path.write_text(
        original + "\nNote ajoutée à la main.\n", encoding="utf-8", newline="\n"
    )
    before = entity_path.read_bytes()

    report = vault.notify_validated(record(invoice_number="FA-002"))

    assert report.entity is not None
    assert report.entity.status == OUTCOME_PRESERVED
    assert report.entity.detail == "content_diverged"
    assert entity_path.read_bytes() == before


# -- non-blocking failures ----------------------------------------------------


def test_writer_crash_is_captured_and_never_raised(tmp_path, monkeypatch):
    vault = bridge(tmp_path)

    def boom(*_args, **_kwargs):
        raise OSError("vault locked by another application")

    monkeypatch.setattr(vault.writer, "update_entity", boom)
    monkeypatch.setattr(vault.writer, "write_event", boom)

    report = vault.notify_validated(record())

    assert report.status == STATUS_FAILED
    assert len(report.errors) == 2
    assert any(error.startswith("ENTITY:OSERROR") for error in report.errors)
    assert any(error.startswith("EVENT:OSERROR") for error in report.errors)


def test_partial_failure_keeps_successful_write_and_reports_partial(
    tmp_path, monkeypatch
):
    vault = bridge(tmp_path)

    def boom(*_args, **_kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(vault.writer, "write_event", boom)

    report = vault.notify_validated(record())

    assert report.status == STATUS_PARTIAL
    assert report.entity is not None and report.entity.status == "created"
    assert report.event is None
    assert report.errors and report.errors[0].startswith("EVENT:PERMISSIONERROR")


# -- InboxIngestionService wiring --------------------------------------------


def test_inbox_validation_enriches_vault_and_keeps_store_line(tmp_path):
    vault = bridge(tmp_path)
    tree, store, service, inbox = inbox_service(
        tmp_path, constant(complete()), vault_bridge=vault
    )
    digest = put(inbox, "facture-acme.pdf")

    report = service.ingest_inbox()

    destination = (
        tree.category_dir(
            FolderState.VALIDE_PRET_FEC, DocumentCategory.FRAIS_GENERAUX
        )
        / "facture-acme.pdf"
    )
    assert destination.is_file()
    assert report.moved_to_03 == 1
    assert report.vault_events == 1
    assert any(trace == f"{digest}:VAULT:written" for trace in report.traces)
    line = store.get_line(TENANT, derive_document_id(digest))
    assert line["raw_status"] == DocumentStatus.VALIDE.value
    assert line["counterparty"] == "ACME SAS"
    entity_path = tmp_path / "vault" / "04-graph" / "entities" / "acme-sas.md"
    frontmatter, _body = note(entity_path)
    assert frontmatter["relations"][0]["target"] == "piece-fa-001"


def test_inbox_pending_document_does_not_notify_vault(tmp_path):
    vault = bridge(tmp_path)
    _tree, _store, service, inbox = inbox_service(
        tmp_path, constant(complete(invoice_number=None)), vault_bridge=vault
    )
    put(inbox, "facture-acme.pdf")

    report = service.ingest_inbox()

    assert report.moved_to_02 == 1
    assert report.vault_events == 0
    assert list((tmp_path / "vault" / "04-graph" / "entities").iterdir()) == []
    assert list((tmp_path / "vault" / EVENTS_DIR).iterdir()) == []


def test_inbox_vault_crash_keeps_sqlite_and_file_move(tmp_path, monkeypatch):
    vault = bridge(tmp_path)

    def boom(*_args, **_kwargs):
        raise OSError("OneDrive disconnected")

    monkeypatch.setattr(vault.writer, "update_entity", boom)
    monkeypatch.setattr(vault.writer, "write_event", boom)
    tree, store, service, inbox = inbox_service(
        tmp_path, constant(complete()), vault_bridge=vault
    )
    digest = put(inbox, "facture-acme.pdf")

    report = service.ingest_inbox()

    destination = (
        tree.category_dir(
            FolderState.VALIDE_PRET_FEC, DocumentCategory.FRAIS_GENERAUX
        )
        / "facture-acme.pdf"
    )
    assert destination.is_file()
    assert report.moved_to_03 == 1
    assert report.vault_events == 0
    assert any(":VAULT:failed" in trace for trace in report.traces)
    line = store.get_line(TENANT, derive_document_id(digest))
    assert line["raw_status"] == DocumentStatus.VALIDE.value
    assert line["invoice_url"].startswith("03_VALIDE_PRET_FEC/FRAIS_GENERAUX/")


def test_inbox_survives_a_bridge_that_raises(tmp_path):
    class ExplodingBridge:
        def notify_validated(self, _record):
            raise RuntimeError("bridge down")

    tree, store, service, inbox = inbox_service(
        tmp_path, constant(complete()), vault_bridge=ExplodingBridge()
    )
    digest = put(inbox, "facture-acme.pdf")

    report = service.ingest_inbox()

    destination = (
        tree.category_dir(
            FolderState.VALIDE_PRET_FEC, DocumentCategory.FRAIS_GENERAUX
        )
        / "facture-acme.pdf"
    )
    assert destination.is_file()
    assert report.vault_events == 0
    assert any("VAULT_ERROR" in trace for trace in report.traces)
    assert store.get_line(TENANT, derive_document_id(digest))["raw_status"] == "VALIDE"


# -- FolderSyncService wiring -------------------------------------------------


def test_folder_sync_valide_notifies_vault_with_supplier_from_store(tmp_path):
    tree, store, root = build_flow(tmp_path)
    ingestor = InboxIngestionService(
        TENANT, store, root, extractor=constant(complete(amount_ht=None))
    )
    inbox = tree.state_dir(FolderState.INBOX)
    digest = put(inbox, "facture-acme.pdf")
    assert ingestor.ingest_inbox().moved_to_02 == 1

    vault = bridge(tmp_path)
    sync = FolderSyncService(TENANT, store, root, vault_bridge=vault)
    edit_workbook_row(sync, digest, statut="VALIDE")

    report = sync.sync_human_to_pipeline()

    assert report.moved_to_03 == 1
    assert report.vault_events == 1
    destination = (
        tree.category_dir(
            FolderState.VALIDE_PRET_FEC, DocumentCategory.FRAIS_GENERAUX
        )
        / "facture-acme.pdf"
    )
    assert destination.is_file()
    line = store.get_line(TENANT, derive_document_id(digest))
    assert line["raw_status"] == "VALIDE"
    assert line["counterparty"] == "ACME SAS"
    entity_path = tmp_path / "vault" / "04-graph" / "entities" / "acme-sas.md"
    frontmatter, body = note(entity_path)
    assert frontmatter["relations"][0]["target"] == "piece-fa-001"
    assert "[[acme-sas]]" in body


def test_folder_sync_manual_move_notifies_vault(tmp_path):
    tree, store, root = build_flow(tmp_path)
    sync = FolderSyncService(TENANT, store, root, vault_bridge=bridge(tmp_path))
    inbox = tree.state_dir(FolderState.A_COMPLETER)
    digest = put(inbox, "facture-acme.pdf")
    sync.workbook.upsert(
        digest, "facture-acme.pdf", [], DocumentCategory.FRAIS_GENERAUX
    )
    store.upsert_document_line(
        TENANT,
        derive_document_id(digest),
        {
            "label": "facture-acme.pdf",
            "counterparty": "ACME SAS",
            "status": DocumentStatus.EN_ATTENTE.value,
        },
    )
    target = (
        tree.category_dir(
            FolderState.VALIDE_PRET_FEC, DocumentCategory.FRAIS_GENERAUX
        )
        / "facture-acme.pdf"
    )
    target.write_bytes(DATA)
    (inbox / "facture-acme.pdf").unlink()

    report = sync.sync_human_to_pipeline()

    assert report.manual_moves == 1
    assert report.vault_events == 1
    entity_path = tmp_path / "vault" / "04-graph" / "entities" / "acme-sas.md"
    assert entity_path.is_file()
    assert list((tmp_path / "vault" / EVENTS_DIR).glob("validation-*.md"))


def test_folder_sync_vault_crash_keeps_sqlite_and_file_move(tmp_path, monkeypatch):
    tree, store, root = build_flow(tmp_path)
    ingestor = InboxIngestionService(
        TENANT, store, root, extractor=constant(complete(amount_ht=None))
    )
    inbox = tree.state_dir(FolderState.INBOX)
    digest = put(inbox, "facture-acme.pdf")
    ingestor.ingest_inbox()

    vault = bridge(tmp_path)

    def boom(*_args, **_kwargs):
        raise OSError("vault read-only")

    monkeypatch.setattr(vault.writer, "update_entity", boom)
    monkeypatch.setattr(vault.writer, "write_event", boom)
    sync = FolderSyncService(TENANT, store, root, vault_bridge=vault)
    edit_workbook_row(sync, digest, statut="VALIDE")

    report = sync.sync_human_to_pipeline()

    destination = (
        tree.category_dir(
            FolderState.VALIDE_PRET_FEC, DocumentCategory.FRAIS_GENERAUX
        )
        / "facture-acme.pdf"
    )
    assert destination.is_file()
    assert report.moved_to_03 == 1
    assert report.vault_events == 0
    assert any(":VAULT:failed" in trace for trace in report.traces)
    line = store.get_line(TENANT, derive_document_id(digest))
    assert line["raw_status"] == "VALIDE"
    assert line["invoice_url"].startswith("03_VALIDE_PRET_FEC/FRAIS_GENERAUX/")


def test_folder_sync_rejection_does_not_notify_vault(tmp_path):
    tree, store, root = build_flow(tmp_path)
    vault = bridge(tmp_path)
    sync = FolderSyncService(TENANT, store, root, vault_bridge=vault)
    inbox = tree.state_dir(FolderState.A_COMPLETER)
    digest = put(inbox, "facture-acme.pdf")
    sync.workbook.upsert(
        digest, "facture-acme.pdf", [], DocumentCategory.FRAIS_GENERAUX
    )
    edit_workbook_row(sync, digest, statut="REJETE")

    report = sync.sync_human_to_pipeline()

    assert report.archived == 1
    assert report.vault_events == 0
    assert list((tmp_path / "vault" / EVENTS_DIR).iterdir()) == []
    assert list((tmp_path / "vault" / "04-graph" / "entities").iterdir()) == []
