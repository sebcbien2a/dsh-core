"""P14.3 - Exhaustive tests of the bidirectional folder synchroniser (offline)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from openpyxl import load_workbook

from schemas.folder_state_schema import (
    DocumentCategory,
    DocumentSource,
    DocumentStateRecord,
    DocumentStatus,
    FolderState,
    MissingFieldEnum,
)
from services import folder_sync_service as fss
from services.acompleter_workbook_service import (
    AcompleterWorkbookError,
    SHEET_NAME,
    WORKBOOK_NAME,
    derive_document_id,
)
from services.folder_sync_service import FolderSyncService, SyncReport
from services.folder_tree_service import ensure_tree
from services.sqlite_accounting_store import SqliteAccountingStore

TENANT = "CLIENT_01"
NOW = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


def build(tmp_path):
    root = tmp_path / "flow"
    tree = ensure_tree(TENANT, root)
    store = SqliteAccountingStore(tmp_path / "accounting.sqlite3")
    service = FolderSyncService(TENANT, store, root)
    inbox = tree.state_dir(FolderState.A_COMPLETER)
    return tree, store, service, inbox


def put(directory: Path, name: str, data: bytes = b"%PDF-1.4 fake") -> str:
    (directory / name).write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def edit_row(service, sha: str, **cells) -> None:
    columns = {
        "id_document": 1,
        "sha256": 2,
        "nom_fichier": 3,
        "champs_manquants": 4,
        "categorie_suggeree": 5,
        "statut": 6,
    }
    path = service.workbook.workbook_path
    wb = load_workbook(path)
    ws = wb[SHEET_NAME]
    key = sha.strip().lower()
    for index in range(2, ws.max_row + 1):
        if str(ws.cell(row=index, column=2).value or "").strip().lower() == key:
            for name, value in cells.items():
                ws.cell(row=index, column=columns[name]).value = value
            wb.save(path)
            return
    raise AssertionError(f"row for {sha} not found")


def read_row(service, sha: str) -> dict:
    for row in service.workbook.read_rows():
        if row.sha256.strip().lower() == sha.strip().lower():
            return {
                "id_document": row.id_document,
                "nom_fichier": row.nom_fichier,
                "champs_manquants": row.champs_manquants,
                "categorie_suggeree": row.categorie_suggeree,
                "statut": row.statut,
            }
    raise AssertionError(f"row for {sha} not found")


def make_record(sha: str, name: str, champs=(), cat=DocumentCategory.DIVERS_A_CLASSER):
    return DocumentStateRecord(
        sha256=sha,
        nom_fichier=name,
        state=FolderState.A_COMPLETER,
        category=cat,
        champs_manquants=list(champs),
        categorie_suggeree=cat,
        source=DocumentSource.MANUAL,
        horodatage=NOW,
    )


# -- SyncReport ---------------------------------------------------------------


def test_sync_report_defaults():
    report = SyncReport()
    assert report.skipped is False
    assert report.deferred_lock == 0
    assert report.rows_written == report.rows_updated == 0
    assert report.moved_to_03 == report.archived == 0
    assert report.manual_moves == report.skipped_invalid == 0
    assert report.traces == ()


# -- cycle 02_ -> VALIDE -> 03_/<CAT>/ + SQLite --------------------------------


def test_valide_moves_file_persists_and_traces(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(
        sha, "facture.pdf", ["date_piece"], DocumentCategory.ACHATS_MARCHANDISES
    )
    edit_row(service, sha, statut="VALIDE")

    report = service.sync_human_to_pipeline()

    assert report.moved_to_03 == 1
    assert report.archived == 0
    dest = (
        tree.ged_dir("FACTURES_FOURNISSEURS")
        / "facture.pdf"
    )
    assert dest.is_file()
    assert not (inbox / "facture.pdf").exists()
    line = store.get_line(TENANT, derive_document_id(sha))
    assert line["label"] == "facture.pdf"
    assert line["invoice_id"] == derive_document_id(sha)
    assert (
        line["invoice_url"]
        == "03_VALIDE_PRET_FEC/FACTURES_FOURNISSEURS/facture.pdf"
    )
    assert line["raw_status"] == "VALIDE"
    assert any(t.startswith(f"{sha}:02_A_COMPLETER->03_") for t in report.traces)


def test_valide_moves_despite_other_missing_fields(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(
        sha, "facture.pdf", ["date_piece", "montant_ht"], DocumentCategory.FRAIS_GENERAUX
    )
    edit_row(service, sha, statut="VALIDE")
    report = service.sync_human_to_pipeline()
    assert report.moved_to_03 == 1
    assert (
        tree.state_dir(FolderState.VALIDE_PRET_FEC)
        / "FACTURES_FOURNISSEURS"
        / "facture.pdf"
    ).is_file()


def test_valide_with_categorie_still_missing_is_skipped(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(
        sha, "facture.pdf", ["categorie"], DocumentCategory.DIVERS_A_CLASSER
    )
    edit_row(service, sha, statut="VALIDE")
    report = service.sync_human_to_pipeline()
    assert report.skipped_invalid == 1
    assert report.moved_to_03 == 0
    assert (inbox / "facture.pdf").is_file()
    assert store.get_line(TENANT, derive_document_id(sha)) is None
    assert any("SKIPPED_INVALID_CATEGORY" in t for t in report.traces)


def test_valide_with_invalid_category_is_skipped(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(sha, "facture.pdf", [], DocumentCategory.DIVERS_A_CLASSER)
    edit_row(service, sha, statut="VALIDE", categorie_suggeree="AUTRE")
    report = service.sync_human_to_pipeline()
    assert report.skipped_invalid == 1
    assert (inbox / "facture.pdf").is_file()
    assert store.get_line(TENANT, derive_document_id(sha)) is None


def test_valide_destination_name_conflict_archives_source(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf", b"original")
    cat_dir = tree.ged_dir("FACTURES_FOURNISSEURS")
    (cat_dir / "facture.pdf").write_bytes(b"autre contenu")
    service.workbook.upsert(sha, "facture.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    edit_row(service, sha, statut="VALIDE")
    report = service.sync_human_to_pipeline()
    assert report.archived == 1
    assert report.moved_to_03 == 0
    assert (tree.state_dir(FolderState.ARCHIVE_ERREUR) / "facture.pdf").is_file()
    assert (cat_dir / "facture.pdf").read_bytes() == b"autre contenu"


def test_en_attente_row_is_left_alone(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(sha, "facture.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    report = service.sync_human_to_pipeline()
    assert report.moved_to_03 == report.archived == report.skipped_invalid == 0
    assert (inbox / "facture.pdf").is_file()


def test_second_sync_is_skipped_after_valide(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(sha, "facture.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    edit_row(service, sha, statut="VALIDE")
    service.sync_human_to_pipeline()
    report = service.sync_human_to_pipeline()
    assert report.skipped is True
    assert report.moved_to_03 == 0


def test_valide_row_without_file_does_nothing(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = "ab" * 32
    service.workbook.upsert(sha, "fantome.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    edit_row(service, sha, statut="VALIDE")
    report = service.sync_human_to_pipeline()
    assert report.moved_to_03 == 0
    assert store.get_line(TENANT, derive_document_id(sha)) is None


def test_tenants_are_isolated(tmp_path):
    root = tmp_path / "flow"
    tree_a = ensure_tree("TENANT_A", root)
    store = SqliteAccountingStore(tmp_path / "accounting.sqlite3")
    service_a = FolderSyncService("TENANT_A", store, root)
    inbox_a = tree_a.state_dir(FolderState.A_COMPLETER)
    sha = put(inbox_a, "facture.pdf")
    service_a.workbook.upsert(sha, "facture.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    edit_row(service_a, sha, statut="VALIDE")
    service_a.sync_human_to_pipeline()
    assert not (root / "TENANT_B").exists()
    assert store.get_line("TENANT_B", derive_document_id(sha)) is None


# -- cycle 02_ -> REJETE -> 05_/REJET_MANUEL/ ---------------------------------


def test_rejete_moves_to_rejet_manuel_and_traces_state(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(sha, "facture.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    edit_row(service, sha, statut="REJETE")

    report = service.sync_human_to_pipeline()

    assert report.archived == 1
    dest = tree.rejet_manuel_dir() / "facture.pdf"
    assert dest.is_file()
    assert not (inbox / "facture.pdf").exists()
    line = store.get_line(TENANT, derive_document_id(sha))
    assert line["raw_status"] == "REJETE"
    assert line["invoice_url"] == "05_ARCHIVE_ERREUR/REJET_MANUEL/facture.pdf"
    assert any("REJET_MANUEL" in t for t in report.traces)


def test_rejete_name_collision_gets_unique_name(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    (tree.rejet_manuel_dir() / "facture.pdf").write_bytes(b"deja la")
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(sha, "facture.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    edit_row(service, sha, statut="REJETE")
    report = service.sync_human_to_pipeline()
    assert report.archived == 1
    assert (tree.rejet_manuel_dir() / "facture__1.pdf").is_file()
    assert (tree.rejet_manuel_dir() / "facture.pdf").read_bytes() == b"deja la"


def test_custom_status_is_left_alone(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(sha, "facture.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    edit_row(service, sha, statut="A_VALIDER_PAR_LE_COMPTABLE")
    report = service.sync_human_to_pipeline()
    assert report.moved_to_03 == report.archived == 0
    assert (inbox / "facture.pdf").is_file()


# -- priorité au déplacement manuel -------------------------------------------


def test_manual_move_syncs_store_and_aligns_statut(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(sha, "facture.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    dest_dir = tree.category_dir(FolderState.VALIDE_PRET_FEC, DocumentCategory.BANQUE_FINANCE)
    (inbox / "facture.pdf").rename(dest_dir / "facture.pdf")

    report = service.sync_human_to_pipeline()

    assert report.manual_moves == 1
    assert report.moved_to_03 == 0
    line = store.get_line(TENANT, derive_document_id(sha))
    assert line["raw_status"] == "VALIDE"
    assert line["invoice_url"] == "03_VALIDE_PRET_FEC/BANQUE_FINANCE/facture.pdf"
    assert read_row(service, sha)["statut"] == "VALIDE"
    assert (dest_dir / "facture.pdf").is_file()


def test_manual_move_folder_category_wins(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(sha, "facture.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    dest_dir = tree.category_dir(FolderState.VALIDE_PRET_FEC, DocumentCategory.VENTES_CLIENTS)
    (inbox / "facture.pdf").rename(dest_dir / "facture.pdf")
    service.sync_human_to_pipeline()
    line = store.get_line(TENANT, derive_document_id(sha))
    assert line["invoice_url"] == "03_VALIDE_PRET_FEC/VENTES_CLIENTS/facture.pdf"


def test_manual_move_wins_over_rejete_statut(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(sha, "facture.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    edit_row(service, sha, statut="REJETE")
    dest_dir = tree.category_dir(FolderState.VALIDE_PRET_FEC, DocumentCategory.FRAIS_GENERAUX)
    (inbox / "facture.pdf").rename(dest_dir / "facture.pdf")
    report = service.sync_human_to_pipeline()
    assert report.manual_moves == 1
    assert report.archived == 0
    assert read_row(service, sha)["statut"] == "VALIDE"
    assert (dest_dir / "facture.pdf").is_file()


def test_manual_move_same_name_different_content_is_ignored(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf", b"contenu A")
    service.workbook.upsert(sha, "facture.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    dest_dir = tree.category_dir(FolderState.VALIDE_PRET_FEC, DocumentCategory.FRAIS_GENERAUX)
    (dest_dir / "facture.pdf").write_bytes(b"contenu B")
    (inbox / "facture.pdf").unlink()
    report = service.sync_human_to_pipeline()
    assert report.manual_moves == 0
    assert store.get_line(TENANT, derive_document_id(sha)) is None


def test_service_never_moves_validated_files_back(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(sha, "facture.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    dest_dir = tree.category_dir(FolderState.VALIDE_PRET_FEC, DocumentCategory.FRAIS_GENERAUX)
    (inbox / "facture.pdf").rename(dest_dir / "facture.pdf")
    service.sync_human_to_pipeline()
    service.sync_human_to_pipeline()
    assert (dest_dir / "facture.pdf").is_file()
    assert not (inbox / "facture.pdf").exists()


# -- détection mtime + SHA-256 ------------------------------------------------


def test_workbook_signature_tracks_mtime_and_sha(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    mtime, sha = service.workbook_signature()
    assert sha == hashlib.sha256(service.workbook.workbook_path.read_bytes()).hexdigest()
    assert mtime == service.workbook.workbook_path.stat().st_mtime_ns


def test_skip_when_signature_unchanged(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    put(inbox, "facture.pdf")
    service.sync_pipeline_to_human()
    first = service.sync_human_to_pipeline()
    assert first.skipped is False
    second = service.sync_human_to_pipeline()
    assert second.skipped is True
    assert second.traces == ()


def test_resync_when_workbook_changes(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.sync_pipeline_to_human()
    service.sync_human_to_pipeline()
    edit_row(service, sha, statut="VALIDE", categorie_suggeree="FRAIS_GENERAUX")
    report = service.sync_human_to_pipeline()
    assert report.skipped is False
    assert report.moved_to_03 == 1


def test_touch_only_mtime_triggers_resync(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    service.sync_human_to_pipeline()
    assert service.sync_human_to_pipeline().skipped is True
    path = service.workbook.workbook_path
    stat = path.stat()
    import os

    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert service.sync_human_to_pipeline().skipped is False


# -- lock Excel ---------------------------------------------------------------


def test_lock_defers_cleanly_and_touches_nothing(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(sha, "facture.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    edit_row(service, sha, statut="VALIDE")
    lock = tree.root / TENANT / "~$_A_COMPLETER.xlsx"
    lock.write_text("lock", encoding="utf-8")

    report = service.sync_human_to_pipeline()

    assert report.deferred_lock == 1
    assert report.skipped is False
    assert report.moved_to_03 == 0
    assert (inbox / "facture.pdf").is_file()


def test_lock_defers_pipeline_to_human_too(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    (tree.root / TENANT / "~$_A_COMPLETER.xlsx").write_text("lock", encoding="utf-8")
    report = service.sync_pipeline_to_human()
    assert report.deferred_lock == 1
    assert report.rows_written == 0


def test_sync_works_after_lock_disappears(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(sha, "facture.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    edit_row(service, sha, statut="VALIDE")
    lock = tree.root / TENANT / "~$_A_COMPLETER.xlsx"
    lock.write_text("lock", encoding="utf-8")
    assert service.sync_human_to_pipeline().deferred_lock == 1
    lock.unlink()
    assert service.sync_human_to_pipeline().moved_to_03 == 1


# -- doublons SHA-256 / erreurs ----------------------------------------------


def test_duplicate_hash_in_inbox_archives_extra(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    put(inbox, "aaa.pdf", b"meme contenu")
    put(inbox, "bbb.pdf", b"meme contenu")
    report = service.sync_human_to_pipeline()
    assert report.archived == 1
    assert (inbox / "aaa.pdf").is_file()
    assert (tree.state_dir(FolderState.ARCHIVE_ERREUR) / "bbb.pdf").is_file()


def test_duplicate_against_validated_archives_inbox_copy(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    cat_dir = tree.category_dir(FolderState.VALIDE_PRET_FEC, DocumentCategory.FRAIS_GENERAUX)
    (cat_dir / "deja_valide.pdf").write_bytes(b"meme contenu")
    put(inbox, "copie.pdf", b"meme contenu")
    report = service.sync_human_to_pipeline()
    assert report.archived == 1
    assert not (inbox / "copie.pdf").exists()
    assert (tree.state_dir(FolderState.ARCHIVE_ERREUR) / "copie.pdf").is_file()


def test_unreadable_file_is_archived(tmp_path, monkeypatch):
    tree, store, service, inbox = build(tmp_path)
    put(inbox, "corrompu.pdf")
    real = fss._file_sha256

    def flaky(path):
        if Path(path).name == "corrompu.pdf":
            raise OSError("illisible")
        return real(path)

    monkeypatch.setattr(fss, "_file_sha256", flaky)
    report = service.sync_human_to_pipeline()
    assert report.archived == 1
    assert (tree.state_dir(FolderState.ARCHIVE_ERREUR) / "corrompu.pdf").is_file()


def test_archives_are_never_deleted(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    put(inbox, "aaa.pdf", b"meme contenu")
    put(inbox, "bbb.pdf", b"meme contenu")
    service.sync_human_to_pipeline()
    assert sorted(
        p.name for p in tree.state_dir(FolderState.ARCHIVE_ERREUR).iterdir()
    ) == ["REJET_MANUEL", "bbb.pdf"]


# -- pipeline -> humain -------------------------------------------------------


def test_pipeline_to_human_provisions_missing_rows(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    report = service.sync_pipeline_to_human()
    assert report.rows_written == 1
    row = read_row(service, sha)
    assert row["statut"] == "EN_ATTENTE"
    assert row["categorie_suggeree"] == "DIVERS_A_CLASSER"
    assert row["nom_fichier"] == "facture.pdf"


def test_pipeline_to_human_upserts_explicit_records(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    record = make_record(
        sha, "facture.pdf", champs=[MissingFieldEnum.DATE_PIECE],
        cat=DocumentCategory.ACHATS_MARCHANDISES,
    )
    report = service.sync_pipeline_to_human([record])
    assert report.rows_written == 1
    row = read_row(service, sha)
    assert row["champs_manquants"] == "date_piece"
    assert row["categorie_suggeree"] == "ACHATS_MARCHANDISES"


def test_pipeline_to_human_updates_en_attente_only(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(sha, "facture.pdf", ["date_piece"], DocumentCategory.FRAIS_GENERAUX)
    record = make_record(
        sha, "facture.pdf", champs=[MissingFieldEnum.MONTANT_TTC],
        cat=DocumentCategory.BANQUE_FINANCE,
    )
    report = service.sync_pipeline_to_human([record])
    assert report.rows_updated == 1
    assert read_row(service, sha)["champs_manquants"] == "montant_ttc"


def test_pipeline_to_human_preserves_human_rows(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(sha, "facture.pdf", ["date_piece"], DocumentCategory.FRAIS_GENERAUX)
    edit_row(service, sha, statut="VALIDE")
    record = make_record(sha, "facture.pdf", champs=[MissingFieldEnum.MONTANT_TTC])
    report = service.sync_pipeline_to_human([record])
    assert report.rows_written == report.rows_updated == 0
    row = read_row(service, sha)
    assert row["statut"] == "VALIDE"
    assert row["champs_manquants"] == "date_piece"


def test_pipeline_to_human_is_idempotent(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    put(inbox, "facture.pdf")
    assert service.sync_pipeline_to_human().rows_written == 1
    assert service.sync_pipeline_to_human().rows_written == 0


# -- SqliteAccountingStore.upsert_document_line --------------------------------


def test_store_upsert_creates_then_updates(tmp_path):
    store = SqliteAccountingStore(tmp_path / "acc.sqlite3")
    line = {
        "label": "facture.pdf",
        "invoice_id": "DOC-1",
        "invoice_url": "03_VALIDE_PRET_FEC/FRAIS_GENERAUX/facture.pdf",
        "status": "VALIDE",
    }
    assert store.upsert_document_line(TENANT, "DOC-1", line) == "created"
    assert store.get_line(TENANT, "DOC-1")["invoice_url"].endswith("FRAIS_GENERAUX/facture.pdf")
    line["invoice_url"] = "03_VALIDE_PRET_FEC/BANQUE_FINANCE/facture.pdf"
    assert store.upsert_document_line(TENANT, "DOC-1", line) == "updated"
    assert store.get_line(TENANT, "DOC-1")["invoice_url"].endswith("BANQUE_FINANCE/facture.pdf")


def test_store_upsert_preserves_arbitrated_lines(tmp_path):
    store = SqliteAccountingStore(tmp_path / "acc.sqlite3")
    store.add_line(TENANT, "DOC-1", {"label": "facture.pdf", "invoice_id": "DOC-1"})
    store.arbitrate(
        TENANT, "DOC-1", {"action": "ATTACH_INVOICE", "status": "VALIDATED", "invoice_id": "DOC-1"}
    )
    outcome = store.upsert_document_line(
        TENANT,
        "DOC-1",
        {"label": "autre.pdf", "invoice_id": "AUTRE", "invoice_url": "05/x", "status": "REJETE"},
    )
    assert outcome == "preserved"
    line = store.get_line(TENANT, "DOC-1")
    assert line["label"] == "facture.pdf"
    assert line["raw_status"] == "VALIDATED"


# -- align_statut --------------------------------------------------------------


def test_align_statut_updates_and_is_idempotent(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(sha, "facture.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    assert service.workbook.align_statut(sha, DocumentStatus.VALIDE) is True
    assert read_row(service, sha)["statut"] == "VALIDE"
    assert service.workbook.align_statut(sha, "VALIDE") is False


def test_align_statut_unknown_row_raises(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    service.workbook.ensure_workbook()
    with pytest.raises(AcompleterWorkbookError) as info:
        service.workbook.align_statut("cd" * 32, DocumentStatus.VALIDE)
    assert info.value.code == "ROW_NOT_FOUND"


def test_align_statut_rejects_unknown_status(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(sha, "facture.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    with pytest.raises(AcompleterWorkbookError) as info:
        service.workbook.align_statut(sha, "PEU_IMPORTE")
    assert info.value.code == "INVALID_STATUS"


def test_align_statut_blocked_by_lock(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(sha, "facture.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    (tree.root / TENANT / "~$_A_COMPLETER.xlsx").write_text("lock", encoding="utf-8")
    with pytest.raises(AcompleterWorkbookError) as info:
        service.workbook.align_statut(sha, DocumentStatus.VALIDE)
    assert info.value.code == "WORKBOOK_LOCKED"


# -- annule et remplace (03_ -> REJETE -> 05_) --------------------------------


def test_annule_et_remplace_full_cycle(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(
        sha, "facture.pdf", [], DocumentCategory.ACHATS_MARCHANDISES
    )
    edit_row(service, sha, statut="VALIDE")
    first = service.sync_human_to_pipeline()
    assert first.moved_to_03 == 1
    doc_id = derive_document_id(sha)
    assert store.get_line(TENANT, doc_id)["raw_status"] == "VALIDE"
    assert store.list_lines(TENANT)[1] == 1

    edit_row(service, sha, statut="REJETE")
    second = service.sync_human_to_pipeline()

    assert second.skipped is False
    assert second.archived == 1
    assert second.manual_moves == 0
    source_dir = tree.category_dir(
        FolderState.VALIDE_PRET_FEC, DocumentCategory.ACHATS_MARCHANDISES
    )
    assert not (source_dir / "facture.pdf").exists()
    rejet = tree.rejet_manuel_dir() / "facture.pdf"
    assert rejet.is_file()
    assert store.get_line(TENANT, doc_id)["raw_status"] == "REJETE"
    assert store.list_lines(TENANT)[1] == 0
    rejected_rows, total = store.list_lines(TENANT, status="REJETE")
    assert total == 1 and rejected_rows[0]["line_id"] == doc_id
    assert store.list_validated_entries(TENANT, None, None) == []
    assert any(
        t.endswith("03_VALIDE_PRET_FEC->05_ARCHIVE_ERREUR/REJET_MANUEL/facture.pdf")
        for t in second.traces
    )


def test_annule_et_remplace_collision_gets_unique_name(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(sha, "facture.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    edit_row(service, sha, statut="VALIDE")
    service.sync_human_to_pipeline()
    (tree.rejet_manuel_dir() / "facture.pdf").write_bytes(b"deja rejete")

    edit_row(service, sha, statut="REJETE")
    report = service.sync_human_to_pipeline()

    assert report.archived == 1
    assert (tree.rejet_manuel_dir() / "facture__1.pdf").is_file()
    assert (tree.rejet_manuel_dir() / "facture.pdf").read_bytes() == b"deja rejete"


def test_reject_from_02_deactivates_active_store_line(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(sha, "facture.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    service.sync_pipeline_to_human()
    doc_id = derive_document_id(sha)
    store.upsert_document_line(
        TENANT,
        doc_id,
        {"label": "facture.pdf", "invoice_id": doc_id, "status": "VALIDE"},
    )
    edit_row(service, sha, statut="REJETE")

    report = service.sync_human_to_pipeline()

    assert report.archived == 1
    assert store.get_line(TENANT, doc_id)["raw_status"] == "REJETE"
    assert (tree.rejet_manuel_dir() / "facture.pdf").is_file()


def test_align_statut_valide_to_rejete(tmp_path):
    tree, store, service, inbox = build(tmp_path)
    sha = put(inbox, "facture.pdf")
    service.workbook.upsert(sha, "facture.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    assert service.workbook.align_statut(sha, DocumentStatus.VALIDE) is True
    assert service.workbook.align_statut(sha, DocumentStatus.REJETE) is True
    assert read_row(service, sha)["statut"] == "REJETE"


def test_rejected_documents_are_excluded_from_pool(tmp_path):
    store = SqliteAccountingStore(tmp_path / "acc.sqlite3")
    sha = "ab" * 32
    doc_id = derive_document_id(sha)
    store.upsert_document_line(
        TENANT,
        doc_id,
        {"label": "facture.pdf", "invoice_id": doc_id, "status": "VALIDE"},
    )
    assert store.list_lines(TENANT)[1] == 1
    assert store.deactivate_rejected_document(TENANT, sha) is True
    assert store.deactivate_rejected_document(TENANT, sha) is False
    assert store.list_lines(TENANT)[1] == 0
    assert store.list_lines(TENANT, status="REJETE")[1] == 1
    assert store.get_line(TENANT, doc_id)["raw_status"] == "REJETE"


def test_deactivate_overrides_arbitrated_preservation(tmp_path):
    store = SqliteAccountingStore(tmp_path / "acc.sqlite3")
    sha = "ab" * 32
    doc_id = derive_document_id(sha)
    store.add_line(TENANT, doc_id, {"label": "facture.pdf", "invoice_id": doc_id})
    store.arbitrate(
        TENANT,
        doc_id,
        {"action": "ATTACH_INVOICE", "status": "INVOICE_ATTACHED", "invoice_id": doc_id},
    )
    kept = store.upsert_document_line(
        TENANT,
        doc_id,
        {"label": "x", "invoice_id": doc_id, "status": "REJETE"},
    )
    assert kept == "preserved"
    assert store.deactivate_rejected_document(TENANT, sha) is True
    assert store.get_line(TENANT, doc_id)["raw_status"] == "INVOICE_ATTACHED"
    assert store.list_lines(TENANT)[1] == 0


def test_rejected_documents_excluded_from_csv_endpoint(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.middleware.tenant_middleware import TenantContextMiddleware
    from backend.app.routers.accounting_export import router as export_router
    from backend.app.routers.reconciliation_arbitration import get_arbitration_store

    store = SqliteAccountingStore(tmp_path / "acc.sqlite3")
    sha = "ab" * 32
    doc_id = derive_document_id(sha)
    store.upsert_document_line(
        TENANT,
        doc_id,
        {"label": "facture.pdf", "invoice_id": doc_id, "status": "VALIDE"},
    )
    app = FastAPI()
    app.add_middleware(TenantContextMiddleware)
    app.include_router(export_router)
    app.dependency_overrides[get_arbitration_store] = lambda: store
    client = TestClient(app)

    before = client.get(
        "/accounting/export/spreadsheet", headers={"X-Tenant-ID": TENANT}
    )
    assert before.status_code == 200
    assert "facture.pdf" in before.text

    assert store.deactivate_rejected_document(TENANT, sha) is True
    after = client.get(
        "/accounting/export/spreadsheet", headers={"X-Tenant-ID": TENANT}
    )
    assert after.status_code == 200
    assert "facture.pdf" not in after.text
