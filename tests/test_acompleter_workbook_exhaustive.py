"""P14.2 - Exhaustive tests of the ``_A_COMPLETER.xlsx`` manager (offline)."""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from schemas.folder_state_schema import DocumentCategory, MissingFieldEnum
from services.acompleter_workbook_service import (
    HEADERS,
    LOCK_GLOB,
    OUTCOME_CREATED,
    OUTCOME_PRESERVED,
    OUTCOME_UNCHANGED,
    OUTCOME_UPDATED,
    SHEET_NAME,
    TEMP_NAME,
    WORKBOOK_NAME,
    AcompleterWorkbook,
    AcompleterWorkbookError,
    WorkbookLockedError,
    WorkbookRow,
    derive_document_id,
    format_champs_manquants,
    normalize_sha256,
    tenant_workbook,
)
from services.folder_tree_service import FLOW_ROOT_ENV, FolderTreeError

SHA = "ab" * 32
SHA_B = "cd" * 32
SHA_C = "ef" * 32


def err(call, *args, **kwargs) -> AcompleterWorkbookError:
    with pytest.raises(AcompleterWorkbookError) as info:
        call(*args, **kwargs)
    return info.value


def book(tmp_path) -> AcompleterWorkbook:
    return AcompleterWorkbook(tmp_path)


def read_sheet(tmp_path):
    wb = load_workbook(tmp_path / WORKBOOK_NAME)
    return wb[SHEET_NAME]


def touch_lock(tmp_path, name: str = "~$_A_COMPLETER.xlsx") -> Path:
    lock = tmp_path / name
    lock.write_text("lock", encoding="utf-8")
    return lock


def human_edit(tmp_path, row: int = 2, **cells) -> None:
    """Simulate direct human edits through Excel (the only human channel)."""
    columns = {
        "id_document": 1,
        "sha256": 2,
        "nom_fichier": 3,
        "champs_manquants": 4,
        "categorie_suggeree": 5,
        "statut": 6,
    }
    path = tmp_path / WORKBOOK_NAME
    wb = load_workbook(path)
    ws = wb[SHEET_NAME]
    for name, value in cells.items():
        ws.cell(row=row, column=columns[name]).value = value
    wb.save(path)


# -- constants & helpers ------------------------------------------------------


def test_headers_are_the_frozen_six_columns():
    assert HEADERS == (
        "ID document",
        "Hash SHA-256",
        "Nom fichier",
        "Champs manquants",
        "Catégorie suggérée",
        "Statut",
    )


def test_file_and_sheet_names():
    assert WORKBOOK_NAME == "_A_COMPLETER.xlsx"
    assert SHEET_NAME == "A_COMPLETER"
    assert LOCK_GLOB == "~$*.xlsx"
    assert TEMP_NAME == "_A_COMPLETER.tmp.xlsx"


def test_derive_document_id_is_deterministic():
    assert derive_document_id(SHA) == derive_document_id(SHA)
    assert derive_document_id(SHA) != derive_document_id(SHA_B)


def test_derive_document_id_normalizes_case():
    assert derive_document_id(SHA.upper()) == derive_document_id(SHA)


def test_derive_document_id_rejects_bad_hash():
    assert err(derive_document_id, "zz").code == "INVALID_SHA256"


def test_normalize_sha256_lowers_and_validates():
    assert normalize_sha256(" " + SHA.upper() + " ") == SHA
    for bad in ("", "a", "g" * 64, SHA[:-1], SHA + "f", " " * 64, 123, None):
        assert err(normalize_sha256, bad).code == "INVALID_SHA256"


def test_format_champs_manquants_joins_in_order():
    text = format_champs_manquants([MissingFieldEnum.DATE_PIECE, "montant_ht"])
    assert text == "date_piece, montant_ht"


def test_format_champs_manquants_empty_is_blank():
    assert format_champs_manquants([]) == ""


def test_format_champs_manquants_rejects_unknown_tokens():
    assert err(format_champs_manquants, ["date"]).code == "INVALID_CHAMP"
    assert err(format_champs_manquants, [None]).code == "INVALID_CHAMP"
    assert err(format_champs_manquants, [123]).code == "INVALID_CHAMP"


# -- ensure_workbook ----------------------------------------------------------


def test_ensure_creates_workbook_with_headers_and_filter(tmp_path):
    path = book(tmp_path).ensure_workbook()
    assert path == tmp_path / WORKBOOK_NAME
    ws = read_sheet(tmp_path)
    assert [ws.cell(row=1, column=i).value for i in range(1, 7)] == list(HEADERS)
    assert ws.auto_filter.ref == "A1:F1"
    assert ws.freeze_panes == "A2"


def test_ensure_styles_headers_bold(tmp_path):
    book(tmp_path).ensure_workbook()
    ws = read_sheet(tmp_path)
    assert all(ws.cell(row=1, column=i).font.bold for i in range(1, 7))


def test_ensure_is_idempotent_and_preserves_rows(tmp_path):
    service = book(tmp_path)
    service.ensure_workbook()
    service.upsert(SHA, "f.pdf", ["date_piece"], DocumentCategory.FRAIS_GENERAUX)
    service.ensure_workbook()
    assert len(service.read_rows()) == 1


def test_ensure_creates_parent_directories(tmp_path):
    deep = tmp_path / "a" / "b"
    AcompleterWorkbook(deep).ensure_workbook()
    assert (deep / WORKBOOK_NAME).is_file()


def test_ensure_rejects_wrong_headers(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME
    ws.append(["autre", "chose"])
    wb.save(tmp_path / WORKBOOK_NAME)
    assert err(book(tmp_path).ensure_workbook).code == "WORKBOOK_HEADERS_MISMATCH"


def test_ensure_rejects_missing_sheet(tmp_path):
    wb = Workbook()
    wb.active.title = "Feuil1"
    wb.save(tmp_path / WORKBOOK_NAME)
    assert err(book(tmp_path).ensure_workbook).code == "WORKBOOK_INVALID"


def test_ensure_rejects_corrupt_file(tmp_path):
    (tmp_path / WORKBOOK_NAME).write_bytes(b"pas un xlsx")
    assert err(book(tmp_path).ensure_workbook).code == "WORKBOOK_INVALID"


# -- Excel / OneDrive lock ----------------------------------------------------


def test_find_lock_file_reports_presence(tmp_path):
    assert book(tmp_path).find_lock_file() is None
    lock = touch_lock(tmp_path)
    assert book(tmp_path).find_lock_file() == lock


def test_non_xlsx_lock_files_are_ignored(tmp_path):
    (tmp_path / "~$notes.txt").write_text("x", encoding="utf-8")
    assert book(tmp_path).find_lock_file() is None


def test_lock_blocks_ensure_and_creates_nothing(tmp_path):
    lock = touch_lock(tmp_path)
    info = err(book(tmp_path).ensure_workbook)
    assert info.code == "WORKBOOK_LOCKED"
    assert isinstance(info, WorkbookLockedError)
    assert info.lock_path == lock
    assert not (tmp_path / WORKBOOK_NAME).exists()


def test_lock_blocks_upsert_without_writing(tmp_path):
    service = book(tmp_path)
    service.ensure_workbook()
    touch_lock(tmp_path)
    info = err(
        service.upsert, SHA, "a.pdf", [], DocumentCategory.DIVERS_A_CLASSER
    )
    assert info.code == "WORKBOOK_LOCKED"
    ws = read_sheet(tmp_path)
    assert ws.max_row == 1


def test_lock_blocks_read(tmp_path):
    service = book(tmp_path)
    service.ensure_workbook()
    touch_lock(tmp_path)
    assert err(service.read_rows).code == "WORKBOOK_LOCKED"


def test_write_works_again_after_lock_disappears(tmp_path):
    service = book(tmp_path)
    service.ensure_workbook()
    lock = touch_lock(tmp_path)
    err(service.upsert, SHA, "a.pdf", [], DocumentCategory.DIVERS_A_CLASSER)
    lock.unlink()
    assert (
        service.upsert(SHA, "a.pdf", [], DocumentCategory.DIVERS_A_CLASSER)
        == OUTCOME_CREATED
    )


# -- upsert: creation ---------------------------------------------------------


def test_upsert_creates_row_with_derived_id(tmp_path):
    service = book(tmp_path)
    outcome = service.upsert(
        SHA,
        "facture.pdf",
        [MissingFieldEnum.DATE_PIECE, "montant_ttc"],
        DocumentCategory.ACHATS_MARCHANDISES,
    )
    assert outcome == OUTCOME_CREATED
    rows = service.read_rows()
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, WorkbookRow)
    assert row.row_number == 2
    assert row.id_document == derive_document_id(SHA)
    assert row.sha256 == SHA
    assert row.nom_fichier == "facture.pdf"
    assert row.champs_manquants == "date_piece, montant_ttc"
    assert row.categorie_suggeree == "ACHATS_MARCHANDISES"
    assert row.statut == "EN_ATTENTE"


def test_upsert_creates_distinct_rows_for_distinct_hashes(tmp_path):
    service = book(tmp_path)
    service.upsert(SHA, "a.pdf", [], DocumentCategory.DIVERS_A_CLASSER)
    service.upsert(SHA_B, "b.pdf", ["date_piece"], DocumentCategory.BANQUE_FINANCE)
    assert [r.sha256 for r in service.read_rows()] == [SHA, SHA_B]


def test_upsert_extends_autofilter(tmp_path):
    service = book(tmp_path)
    service.upsert(SHA, "a.pdf", [], DocumentCategory.DIVERS_A_CLASSER)
    assert read_sheet(tmp_path).auto_filter.ref == "A1:F2"


@pytest.mark.parametrize("bad", ["", "zz", SHA[:-1], None])
def test_upsert_rejects_bad_sha256(tmp_path, bad):
    service = book(tmp_path)
    assert (
        err(service.upsert, bad, "a.pdf", [], DocumentCategory.DIVERS_A_CLASSER).code
        == "INVALID_SHA256"
    )
    assert not (tmp_path / WORKBOOK_NAME).exists()


@pytest.mark.parametrize("bad", ["", "   ", "a/b.pdf", "a\\b.pdf", None])
def test_upsert_rejects_bad_nom_fichier(tmp_path, bad):
    service = book(tmp_path)
    assert (
        err(service.upsert, SHA, bad, [], DocumentCategory.DIVERS_A_CLASSER).code
        == "INVALID_NOM_FICHIER"
    )
    assert not (tmp_path / WORKBOOK_NAME).exists()


def test_upsert_rejects_unknown_category_and_champs(tmp_path):
    service = book(tmp_path)
    assert (
        err(service.upsert, SHA, "a.pdf", [], "AUTRE").code == "INVALID_CATEGORY"
    )
    assert (
        err(service.upsert, SHA, "a.pdf", ["date"], DocumentCategory.DIVERS_A_CLASSER).code
        == "INVALID_CHAMP"
    )


# -- upsert: idempotence (strict SHA-256 key) ---------------------------------


def test_duplicate_hash_is_idempotent(tmp_path):
    service = book(tmp_path)
    assert (
        service.upsert(SHA, "a.pdf", ["date_piece"], DocumentCategory.FRAIS_GENERAUX)
        == OUTCOME_CREATED
    )
    assert (
        service.upsert(SHA, "a.pdf", ["date_piece"], DocumentCategory.FRAIS_GENERAUX)
        == OUTCOME_UNCHANGED
    )
    assert len(service.read_rows()) == 1


def test_duplicate_hash_ignores_case_and_whitespace(tmp_path):
    service = book(tmp_path)
    service.upsert(SHA, "a.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    assert (
        service.upsert(SHA.upper(), "a.pdf", [], DocumentCategory.FRAIS_GENERAUX)
        == OUTCOME_UNCHANGED
    )
    assert len(service.read_rows()) == 1


def test_en_attente_row_is_refreshed_on_reingestion(tmp_path):
    service = book(tmp_path)
    service.upsert(SHA, "a.pdf", ["date_piece"], DocumentCategory.FRAIS_GENERAUX)
    outcome = service.upsert(
        SHA, "a.pdf", ["date_piece", "montant_ht"], DocumentCategory.BANQUE_FINANCE
    )
    assert outcome == OUTCOME_UPDATED
    row = service.read_rows()[0]
    assert row.champs_manquants == "date_piece, montant_ht"
    assert row.categorie_suggeree == "BANQUE_FINANCE"
    assert row.statut == "EN_ATTENTE"
    assert row.row_number == 2


def test_nom_fichier_is_never_rewritten_on_update(tmp_path):
    service = book(tmp_path)
    service.upsert(SHA, "a.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    assert (
        service.upsert(SHA, "renomme.pdf", [], DocumentCategory.FRAIS_GENERAUX)
        == OUTCOME_UNCHANGED
    )
    assert service.read_rows()[0].nom_fichier == "a.pdf"


# -- preservation of human input ----------------------------------------------


def test_human_valide_row_is_preserved(tmp_path):
    service = book(tmp_path)
    service.upsert(SHA, "a.pdf", ["date_piece"], DocumentCategory.FRAIS_GENERAUX)
    human_edit(
        tmp_path,
        statut="VALIDE",
        categorie_suggeree="BANQUE_FINANCE",
        champs_manquants="complété par Marie",
    )
    outcome = service.upsert(
        SHA, "b.pdf", ["montant_ttc"], DocumentCategory.VENTES_CLIENTS
    )
    assert outcome == OUTCOME_PRESERVED
    row = service.read_rows()[0]
    assert row.statut == "VALIDE"
    assert row.categorie_suggeree == "BANQUE_FINANCE"
    assert row.champs_manquants == "complété par Marie"
    assert row.nom_fichier == "a.pdf"


def test_human_rejete_row_is_preserved(tmp_path):
    service = book(tmp_path)
    service.upsert(SHA, "a.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    human_edit(tmp_path, statut="REJETE")
    assert (
        service.upsert(SHA, "a.pdf", ["date_piece"], DocumentCategory.BANQUE_FINANCE)
        == OUTCOME_PRESERVED
    )
    assert service.read_rows()[0].statut == "REJETE"


def test_human_custom_status_is_preserved(tmp_path):
    service = book(tmp_path)
    service.upsert(SHA, "a.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    human_edit(tmp_path, statut="VALIDE_PAR_LE_PD")
    assert (
        service.upsert(SHA, "a.pdf", [], DocumentCategory.BANQUE_FINANCE)
        == OUTCOME_PRESERVED
    )
    assert service.read_rows()[0].statut == "VALIDE_PAR_LE_PD"


def test_cleared_status_is_preserved(tmp_path):
    service = book(tmp_path)
    service.upsert(SHA, "a.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    human_edit(tmp_path, statut="")
    assert (
        service.upsert(SHA, "a.pdf", [], DocumentCategory.BANQUE_FINANCE)
        == OUTCOME_PRESERVED
    )


def test_preserved_upsert_does_not_rewrite_the_file(tmp_path):
    service = book(tmp_path)
    service.upsert(SHA, "a.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    human_edit(tmp_path, statut="VALIDE")
    before = (tmp_path / WORKBOOK_NAME).read_bytes()
    assert (
        service.upsert(SHA, "z.pdf", ["date_piece"], DocumentCategory.VENTES_CLIENTS)
        == OUTCOME_PRESERVED
    )
    assert (tmp_path / WORKBOOK_NAME).read_bytes() == before


def test_reingestion_never_deletes_rows(tmp_path):
    service = book(tmp_path)
    service.upsert(SHA, "a.pdf", [], DocumentCategory.FRAIS_GENERAUX)
    service.upsert(SHA_B, "b.pdf", [], DocumentCategory.BANQUE_FINANCE)
    service.upsert(SHA_C, "c.pdf", [], DocumentCategory.VENTES_CLIENTS)
    service.upsert(SHA_B, "b.pdf", ["date_piece"], DocumentCategory.BANQUE_FINANCE)
    rows = service.read_rows()
    assert [r.sha256 for r in rows] == [SHA, SHA_B, SHA_C]


# -- atomic writes ------------------------------------------------------------


def test_no_temp_leftovers_after_write(tmp_path):
    service = book(tmp_path)
    service.upsert(SHA, "a.pdf", [], DocumentCategory.DIVERS_A_CLASSER)
    assert sorted(p.name for p in tmp_path.iterdir()) == [WORKBOOK_NAME]


def test_failed_save_keeps_original_and_cleans_temp(tmp_path, monkeypatch):
    service = book(tmp_path)
    service.upsert(SHA, "a.pdf", [], DocumentCategory.DIVERS_A_CLASSER)
    before = (tmp_path / WORKBOOK_NAME).read_bytes()

    def boom(src, dst):
        raise OSError("sync OneDrive interrompue")

    monkeypatch.setattr(
        "services.acompleter_workbook_service.os.replace", boom
    )
    assert (
        err(service.upsert, SHA_B, "b.pdf", [], DocumentCategory.DIVERS_A_CLASSER).code
        == "WRITE_FAILED"
    )
    assert (tmp_path / WORKBOOK_NAME).read_bytes() == before
    assert not (tmp_path / TEMP_NAME).exists()
    assert len(service.read_rows()) == 1


# -- read_rows ----------------------------------------------------------------


def test_read_rows_missing_workbook(tmp_path):
    assert err(book(tmp_path).read_rows).code == "WORKBOOK_NOT_FOUND"


def test_read_rows_empty_workbook(tmp_path):
    service = book(tmp_path)
    service.ensure_workbook()
    assert service.read_rows() == []


def test_read_rows_skips_blank_rows(tmp_path):
    service = book(tmp_path)
    service.upsert(SHA, "a.pdf", [], DocumentCategory.DIVERS_A_CLASSER)
    path = tmp_path / WORKBOOK_NAME
    wb = load_workbook(path)
    wb[SHEET_NAME].append(["", "", "", "", "", ""])
    wb.save(path)
    rows = service.read_rows()
    assert len(rows) == 1
    assert rows[0].row_number == 2


# -- tenant binding -----------------------------------------------------------


def test_tenant_workbook_binds_isolated_path(tmp_path):
    service = tenant_workbook("CLIENT_01", tmp_path)
    assert service.directory == (tmp_path / "CLIENT_01").resolve()
    assert service.workbook_path == (
        (tmp_path / "CLIENT_01").resolve() / WORKBOOK_NAME
    )


def test_tenant_workbook_rejects_traversal(tmp_path):
    with pytest.raises(FolderTreeError):
        tenant_workbook("..", tmp_path)


def test_tenant_workbook_uses_env_root(monkeypatch, tmp_path):
    monkeypatch.setenv(FLOW_ROOT_ENV, str(tmp_path / "env"))
    service = tenant_workbook("T")
    assert service.directory == (tmp_path / "env" / "T").resolve()


def test_tenant_workbook_writes_under_tenant_only(tmp_path):
    service = tenant_workbook("TENANT_A", tmp_path)
    service.upsert(SHA, "a.pdf", [], DocumentCategory.DIVERS_A_CLASSER)
    assert (tmp_path / "TENANT_A" / WORKBOOK_NAME).is_file()
    assert not (tmp_path / WORKBOOK_NAME).exists()
    assert not (tmp_path / "TENANT_B").exists()
