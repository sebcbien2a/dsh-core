"""P14.4 - Exhaustive offline tests of the ``00_INBOX`` ingestion service."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from schemas.folder_state_schema import (
    DocumentCategory,
    DocumentStatus,
    FolderState,
    MissingFieldEnum,
)
from schemas.invoice_schema import InvoiceSchema
from services import inbox_ingestion_service as iis
from services.acompleter_workbook_service import (
    WORKBOOK_NAME,
    derive_document_id,
)
from services.inbox_ingestion_service import (
    OUTCOME_A_COMPLETER,
    OUTCOME_DUPLICATE,
    OUTCOME_ERREUR,
    OUTCOME_VALIDE,
    ExtractedInvoice,
    InboxIngestionReport,
    InboxIngestionService,
)
from services.folder_tree_service import ensure_tree
from services.invoice_producer_protocol import InvoiceProducerError
from services.sqlite_accounting_store import SqliteAccountingStore

TENANT = "CLIENT_01"


def build(tmp_path: Path, extractor=None, threshold: float = 0.75):
    root = tmp_path / "flow"
    tree = ensure_tree(TENANT, root)
    store = SqliteAccountingStore(tmp_path / "accounting.sqlite3")
    service = InboxIngestionService(
        TENANT,
        store,
        root,
        extractor=extractor,
        confidence_threshold=threshold,
    )
    inbox = tree.state_dir(FolderState.INBOX)
    return tree, store, service, inbox, root / TENANT


def put(directory: Path, name: str, data: bytes = b"%PDF-1.4 fake") -> str:
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
        if isinstance(value, Exception):
            raise value
        return value

    return extractor


def read_document(service: InboxIngestionService, sha: str) -> dict[str, str]:
    for row in service.workbook.read_rows():
        if row.sha256.strip().lower() == sha:
            return {
                "id_document": row.id_document,
                "nom_fichier": row.nom_fichier,
                "champs_manquants": row.champs_manquants,
                "categorie_suggeree": row.categorie_suggeree,
                "statut": row.statut,
            }
    raise AssertionError(f"no workbook row for {sha}")


# -- report & contracts -------------------------------------------------------


def test_report_defaults():
    report = InboxIngestionReport()
    assert report.scanned == 0
    assert report.duplicates == 0
    assert report.moved_to_03 == report.moved_to_02 == report.archived == 0
    assert report.rows_written == report.rows_updated == report.deferred_lock == 0
    assert report.documents == ()
    assert report.traces == ()


def test_empty_inbox_reports_nothing_and_never_calls_extractor(tmp_path):
    def extractor(_source):
        raise AssertionError("extractor must not run on an empty inbox")

    tree, _store, service, _inbox, _tenant = build(tmp_path, extractor)
    report = service.ingest_inbox()
    assert report == InboxIngestionReport()
    assert service.tenant_id == TENANT
    assert service.tree.root == tree.root
    assert service.tree.state_dir(FolderState.INBOX) == tree.state_dir(
        FolderState.INBOX
    )


def test_resolve_onedrive_inbox_uses_env_then_canonical_default(
    tmp_path, monkeypatch
):
    monkeypatch.delenv(iis.ONEDRIVE_INBOX_ENV, raising=False)
    assert iis.resolve_onedrive_inbox(TENANT, tmp_path / "flow") == (
        tmp_path / "flow" / TENANT / FolderState.INBOX.value
    ).resolve()

    mirror = tmp_path / "OneDrive" / "00_INBOX"
    monkeypatch.setenv(iis.ONEDRIVE_INBOX_ENV, str(mirror))
    assert iis.resolve_onedrive_inbox(TENANT, tmp_path / "flow") == mirror.resolve()


def test_onedrive_mirror_defaults_to_the_canonical_inbox(tmp_path, monkeypatch):
    monkeypatch.delenv(iis.ONEDRIVE_INBOX_ENV, raising=False)
    _tree, _store, service, inbox, _tenant = build(tmp_path)
    assert service.onedrive_inbox == inbox.resolve()


def test_ingest_inbox_aspirates_the_onedrive_mirror(tmp_path, monkeypatch):
    mirror = tmp_path / "OneDrive" / "00_INBOX"
    put(mirror, "mobile.pdf", b"%PDF-1.4 mobile")
    monkeypatch.setenv(iis.ONEDRIVE_INBOX_ENV, str(mirror))

    tree, _store, service, inbox, _tenant = build(
        tmp_path, constant(complete())
    )
    report = service.ingest_inbox()

    assert report.mirrored == 1
    assert report.deferred_io == 0
    assert report.moved_to_03 == 1
    assert list(mirror.iterdir()) == []
    assert list(inbox.iterdir()) == []
    assert any(trace == "MIRRORED:mobile.pdf" for trace in report.traces)
    assert (
        tree.ged_dir("FACTURES_FOURNISSEURS")
        / "mobile.pdf"
    ).is_file()


def test_mirror_permission_error_is_deferred_then_retried(
    tmp_path, monkeypatch
):
    mirror = tmp_path / "OneDrive" / "00_INBOX"
    put(mirror, "mobile.pdf", b"%PDF-1.4 mobile")
    monkeypatch.setenv(iis.ONEDRIVE_INBOX_ENV, str(mirror))

    tree, _store, service, _inbox, _tenant = build(
        tmp_path, constant(complete())
    )
    real_move = iis._move

    def locked_move(source, destination):
        if Path(source).parent.resolve() == mirror.resolve():
            raise PermissionError("fichier ouvert dans un lecteur")
        return real_move(source, destination)

    monkeypatch.setattr(iis, "_move", locked_move)
    first = service.ingest_inbox()

    assert first.mirrored == 0
    assert first.deferred_io == 1
    assert first.moved_to_03 == 0
    assert (mirror / "mobile.pdf").is_file()
    assert "MIRROR_LOCKED:mobile.pdf" in first.traces

    monkeypatch.setattr(iis, "_move", real_move)
    second = service.ingest_inbox()

    assert second.mirrored == 1
    assert second.deferred_io == 0
    assert second.moved_to_03 == 1
    assert (mirror / "mobile.pdf").exists() is False
    assert (
        tree.ged_dir("FACTURES_FOURNISSEURS")
        / "mobile.pdf"
    ).is_file()


def test_process_inbox_explicit_paths_do_not_sweep_the_mirror(
    tmp_path, monkeypatch
):
    mirror = tmp_path / "OneDrive" / "00_INBOX"
    put(mirror, "mobile.pdf", b"%PDF-1.4 mobile")
    monkeypatch.setenv(iis.ONEDRIVE_INBOX_ENV, str(mirror))

    _tree, _store, service, inbox, _tenant = build(
        tmp_path, constant(complete())
    )
    put(inbox, "local.pdf", b"%PDF-1.4 local")
    report = service.process_inbox([inbox / "local.pdf"])

    assert report.mirrored == 0
    assert report.moved_to_03 == 1
    assert (mirror / "mobile.pdf").is_file()


def test_invalid_confidence_threshold_is_rejected(tmp_path):
    store = SqliteAccountingStore(tmp_path / "accounting.sqlite3")
    with pytest.raises(ValueError):
        InboxIngestionService(TENANT, store, tmp_path / "flow", confidence_threshold=1.5)
    with pytest.raises(ValueError):
        InboxIngestionService(TENANT, store, tmp_path / "flow", confidence_threshold=-0.1)


def test_extracted_invoice_missing_fields_are_ordered():
    extracted = ExtractedInvoice(category=DocumentCategory.DIVERS_A_CLASSER)
    assert extracted.missing_fields == (
        MissingFieldEnum.DATE_PIECE,
        MissingFieldEnum.NUMERO_PIECE,
        MissingFieldEnum.NOM_FOURNISSEUR,
        MissingFieldEnum.MONTANT_HT,
        MissingFieldEnum.MONTANT_TVA,
        MissingFieldEnum.MONTANT_TTC,
        MissingFieldEnum.CATEGORIE,
    )
    assert extracted.is_complete is False


def test_extracted_invoice_unknown_supplier_is_missing():
    assert ExtractedInvoice(supplier_name="FOURNISSEUR_INCONNU").supplier_name is None
    assert ExtractedInvoice(supplier_name="   ").supplier_name is None
    assert ExtractedInvoice(supplier_name=" ACME ").supplier_name == "ACME"


def test_extracted_invoice_normalises_values():
    extracted = ExtractedInvoice(
        issue_date="15/01/2026",
        invoice_number="  FA-001 ",
        supplier_name="ACME",
        amount_ht="1 234,50",
        amount_tva="246,90",
        amount_ttc="1 481,40",
        category="FRAIS_GENERAUX",
        confidence=1.7,
        currency="eur",
        document_type="credit_note",
    )
    assert extracted.issue_date == date(2026, 1, 15)
    assert extracted.invoice_number == "FA-001"
    assert extracted.amount_ht == Decimal("1234.50")
    assert extracted.amount_tva == Decimal("246.90")
    assert extracted.amount_ttc == Decimal("1481.40")
    assert extracted.category == DocumentCategory.FRAIS_GENERAUX
    assert extracted.confidence == 1.0
    assert extracted.currency == "EUR"
    assert extracted.document_type == "CREDIT_NOTE"
    assert extracted.missing_fields == ()


def test_extracted_invoice_ignores_invalid_values():
    extracted = ExtractedInvoice(
        issue_date="not-a-date",
        amount_ht="abc",
        amount_tva="NaN",
        amount_ttc=object(),
        category="UNKNOWN",
        confidence="bad",
        currency="EURO",
        document_type="",
    )
    assert extracted.issue_date is None
    assert extracted.amount_ht is None
    assert extracted.amount_tva is None
    assert extracted.amount_ttc is None
    assert extracted.category == DocumentCategory.DIVERS_A_CLASSER
    assert extracted.confidence == 0.0
    assert extracted.currency == "EUR"
    assert extracted.document_type == "INVOICE"


def test_extracted_invoice_accepts_datetime_and_iso_dates():
    assert ExtractedInvoice(issue_date=datetime(2026, 2, 3, 10, 0)).issue_date == date(
        2026, 2, 3
    )
    assert ExtractedInvoice(issue_date="2026-02-03T10:00:00").issue_date == date(
        2026, 2, 3
    )
    assert ExtractedInvoice(issue_date="03-02-26").issue_date == date(2026, 2, 3)


def test_category_inference_ignores_generic_vat_keyword():
    category = iis._category_from_payload(
        {"raw_text": "Facture EDF - TVA 20% - Total TTC"},
        source_name="facture edf.pdf",
    )
    assert category == DocumentCategory.FRAIS_GENERAUX


def test_category_inference_detects_transport_supplier():
    category = iis._category_from_payload(
        {}, source_name="facture.pdf", supplier_name="DHL Express"
    )
    assert category == DocumentCategory.TRANSPORTS_LOGISTIQUE


def test_category_inference_falls_back_to_divers():
    category = iis._category_from_payload(
        {"raw_text": "Document sans mot-cle metier"}, source_name="scan.pdf"
    )
    assert category == DocumentCategory.DIVERS_A_CLASSER


def test_extracted_invoice_uses_canonical_invoice_category():
    invoice = InvoiceSchema.model_validate(
        {
            "document_type": "INVOICE",
            "direction": "PURCHASE",
            "supplier": {"name": "ACME SAS"},
            "reference": {"invoice_number": "FA-009", "issue_date": "2026-01-15"},
            "amount": {
                "currency": "EUR",
                "net_amount": "50.00",
                "tax_amount": "10.00",
                "gross_amount": "60.00",
            },
            "category": "ACHATS_MARCHANDISES",
        }
    )
    extracted = iis.extracted_from_invoice(invoice, source_name="facture.pdf")
    assert extracted.category == DocumentCategory.ACHATS_MARCHANDISES


# -- success -> 03_ -----------------------------------------------------------


def test_complete_high_confidence_routes_to_03_and_persists_store(tmp_path):
    tree, store, service, inbox, _tenant = build(tmp_path, constant(complete()))
    digest = put(inbox, "facture-acme.pdf")

    report = service.ingest_inbox()

    destination = (
        tree.ged_dir("FACTURES_FOURNISSEURS")
        / "facture-acme.pdf"
    )
    assert destination.is_file()
    assert not (inbox / "facture-acme.pdf").exists()
    assert report.scanned == 1
    assert report.moved_to_03 == 1
    assert report.moved_to_02 == report.duplicates == report.archived == 0
    line = store.get_line(TENANT, derive_document_id(digest))
    assert line["raw_status"] == DocumentStatus.VALIDE.value
    assert line["operation_date"] == "2026-01-15"
    assert line["label"] == "FA-001"
    assert line["counterparty"] == "ACME SAS"
    assert line["category"] == "FRAIS_GENERAUX"
    assert line["amount_ht"] == "100.00"
    assert line["amount_tva"] == "20.00"
    assert line["amount_ttc"] == "120.00"
    assert line["currency"] == "EUR"
    assert (
        line["invoice_url"]
        == "03_VALIDE_PRET_FEC/FACTURES_FOURNISSEURS/facture-acme.pdf"
    )
    document = report.documents[0]
    assert document.outcome == OUTCOME_VALIDE
    assert document.destination == line["invoice_url"]
    assert document.missing_fields == ()
    assert any(
        trace == f"{digest}:01_EN_COURS->{line['invoice_url']}"
        for trace in report.traces
    )


def test_extractor_may_return_invoice_schema(tmp_path):
    invoice = InvoiceSchema.model_validate(
        {
            "document_type": "INVOICE",
            "direction": "PURCHASE",
            "supplier": {"name": "ACME SAS"},
            "reference": {"invoice_number": "FA-009", "issue_date": "2026-01-15"},
            "amount": {
                "currency": "EUR",
                "net_amount": "50.00",
                "tax_amount": "10.00",
                "gross_amount": "60.00",
            },
            "category": "FRAIS_GENERAUX",
            "confidence_score": 0.92,
        }
    )
    tree, store, service, inbox, _tenant = build(tmp_path, constant(invoice))
    digest = put(inbox, "facture.pdf")

    report = service.ingest_inbox()

    assert report.moved_to_03 == 1
    assert (
        tree.ged_dir("FACTURES_FOURNISSEURS")
        / "facture.pdf"
    ).is_file()
    line = store.get_line(TENANT, derive_document_id(digest))
    assert line["label"] == "FA-009"
    assert line["amount_ttc"] == "60.00"
    assert line["raw_status"] == "VALIDE"


def test_extractor_may_return_canonical_mapping(tmp_path):
    payload = {
        "document_type": "INVOICE",
        "direction": "PURCHASE",
        "supplier": {"name": "ACME SAS"},
        "reference": {"invoice_number": "FA-010", "issue_date": "2026-01-15"},
        "amount": {
            "currency": "EUR",
            "net_amount": "50.00",
            "tax_amount": "10.00",
            "gross_amount": "60.00",
        },
        "category": "FRAIS_GENERAUX",
        "confidence_score": 0.92,
    }
    tree, store, service, inbox, _tenant = build(tmp_path, constant(payload))
    digest = put(inbox, "facture.pdf")

    report = service.ingest_inbox()

    assert report.moved_to_03 == 1
    line = store.get_line(TENANT, derive_document_id(digest))
    assert line["label"] == "FA-010"
    assert line["raw_status"] == "VALIDE"
    assert (
        tree.ged_dir("FACTURES_FOURNISSEURS")
        / "facture.pdf"
    ).is_file()


def test_name_collision_in_03_gets_unique_name_and_store_path(tmp_path):
    tree, store, service, inbox, _tenant = build(tmp_path, constant(complete()))
    category_dir = tree.ged_dir("FACTURES_FOURNISSEURS")
    (category_dir / "facture.pdf").write_bytes(b"other document")
    digest = put(inbox, "facture.pdf")

    report = service.ingest_inbox()

    destination = category_dir / "facture__1.pdf"
    assert destination.is_file()
    assert report.moved_to_03 == 1
    line = store.get_line(TENANT, derive_document_id(digest))
    assert line["invoice_url"].endswith("/facture__1.pdf")


# -- pending -> 02_ + workbook + store ---------------------------------------


def test_partial_extraction_routes_to_02_adds_workbook_row_and_pending_store(tmp_path):
    extracted = complete(
        invoice_number=None,
        category=DocumentCategory.DIVERS_A_CLASSER,
        confidence=0.4,
    )
    tree, store, service, inbox, tenant_dir = build(tmp_path, constant(extracted))
    digest = put(inbox, "facture.pdf")

    report = service.ingest_inbox()

    destination = tree.state_dir(FolderState.A_COMPLETER) / "facture.pdf"
    assert destination.is_file()
    assert report.moved_to_02 == 1
    assert report.moved_to_03 == report.archived == 0
    assert report.rows_written == 1
    row = read_document(service, digest)
    assert row["id_document"] == derive_document_id(digest)
    assert row["nom_fichier"] == "facture.pdf"
    assert row["champs_manquants"] == "numero_piece, categorie"
    assert row["categorie_suggeree"] == "DIVERS_A_CLASSER"
    assert row["statut"] == "EN_ATTENTE"
    line = store.get_line(TENANT, derive_document_id(digest))
    assert line["raw_status"] == DocumentStatus.EN_ATTENTE.value
    assert line["invoice_url"] == "02_A_COMPLETER/facture.pdf"
    assert line["amount_ht"] == "100.00"
    assert line["category"] == "DIVERS_A_CLASSER"
    document = report.documents[0]
    assert document.outcome == OUTCOME_A_COMPLETER
    assert document.missing_fields == (
        MissingFieldEnum.NUMERO_PIECE,
        MissingFieldEnum.CATEGORIE,
    )
    assert (
        tree.state_dir(FolderState.A_COMPLETER) / "_TECHNIQUE" / WORKBOOK_NAME
    ).is_file()


def test_low_confidence_complete_file_routes_to_02_without_missing_fields(tmp_path):
    tree, _store, service, inbox, _tenant = build(
        tmp_path, constant(complete(confidence=0.5))
    )
    digest = put(inbox, "facture.pdf")

    report = service.ingest_inbox()

    assert report.moved_to_02 == 1
    assert report.moved_to_03 == 0
    row = read_document(service, digest)
    assert row["champs_manquants"] == ""
    assert row["statut"] == "EN_ATTENTE"
    assert (
        tree.state_dir(FolderState.A_COMPLETER) / "facture.pdf"
    ).is_file()


def test_missing_amounts_are_reported_in_workbook(tmp_path):
    extracted = complete(amount_ht=None, amount_tva=None, amount_ttc=None)
    _tree, _store, service, inbox, _tenant = build(tmp_path, constant(extracted))
    digest = put(inbox, "facture.pdf")

    report = service.ingest_inbox()

    assert report.moved_to_02 == 1
    row = read_document(service, digest)
    assert (
        row["champs_manquants"]
        == "montant_ht, montant_tva, montant_ttc"
    )


def test_name_collision_in_02_gets_unique_name(tmp_path):
    tree, store, service, inbox, _tenant = build(
        tmp_path, constant(complete(invoice_number=None))
    )
    a_completer = tree.state_dir(FolderState.A_COMPLETER)
    (a_completer / "facture.pdf").write_bytes(b"older pending")
    digest = put(inbox, "facture.pdf")

    report = service.ingest_inbox()

    assert (a_completer / "facture__1.pdf").is_file()
    assert report.moved_to_02 == 1
    row = read_document(service, digest)
    assert row["nom_fichier"] == "facture__1.pdf"
    line = store.get_line(TENANT, derive_document_id(digest))
    assert line["invoice_url"].endswith("/facture__1.pdf")


def test_workbook_lock_defers_row_but_still_moves_to_02(tmp_path):
    tree, store, service, inbox, tenant_dir = build(
        tmp_path, constant(complete(invoice_number=None))
    )
    (tenant_dir / "~$A_COMPLETER.xlsx").write_bytes(b"lock")
    digest = put(inbox, "facture.pdf")

    report = service.ingest_inbox()

    assert report.moved_to_02 == 1
    assert report.rows_written == 0
    assert report.deferred_lock == 1
    assert (tree.state_dir(FolderState.A_COMPLETER) / "facture.pdf").is_file()
    assert not (tenant_dir / WORKBOOK_NAME).exists()
    line = store.get_line(TENANT, derive_document_id(digest))
    assert line["raw_status"] == "EN_ATTENTE"
    document = report.documents[0]
    assert document.detail == "WORKBOOK_LOCKED"


# -- errors -> 05_ ------------------------------------------------------------


def test_extraction_failure_archives_file(tmp_path):
    error = InvoiceProducerError(
        "EXTRACTION_FAILED", "corrupt pdf", source=Path("facture.pdf")
    )
    tree, store, service, inbox, _tenant = build(tmp_path, constant(error))
    digest = put(inbox, "facture.pdf")

    report = service.ingest_inbox()

    destination = tree.state_dir(FolderState.ARCHIVE_ERREUR) / "facture.pdf"
    assert destination.is_file()
    assert not (inbox / "facture.pdf").exists()
    assert report.archived == 1
    assert report.moved_to_02 == report.moved_to_03 == 0
    assert store.get_line(TENANT, derive_document_id(digest)) is None
    document = report.documents[0]
    assert document.outcome == OUTCOME_ERREUR
    assert document.detail is not None
    assert "EXTRACTION_FAILED" in document.detail
    assert any("corrupt pdf" in trace for trace in report.traces)


def test_invalid_extractor_result_archives_file(tmp_path):
    tree, _store, service, inbox, _tenant = build(
        tmp_path, constant(object())
    )
    put(inbox, "facture.pdf")

    report = service.ingest_inbox()

    assert report.archived == 1
    assert (tree.state_dir(FolderState.ARCHIVE_ERREUR) / "facture.pdf").is_file()
    assert "TYPEERROR" in (report.documents[0].detail or "")


def test_unreadable_file_is_archived(tmp_path, monkeypatch):
    tree, _store, service, inbox, _tenant = build(tmp_path, constant(complete()))
    put(inbox, "facture.pdf")

    def fail(_path):
        raise OSError("unreadable")

    monkeypatch.setattr(iis, "_file_sha256", fail)

    report = service.ingest_inbox()

    assert report.archived == 1
    assert (tree.state_dir(FolderState.ARCHIVE_ERREUR) / "facture.pdf").is_file()
    assert "UNREADABLE" in (report.documents[0].detail or "")


def test_archive_name_collision_gets_unique_name(tmp_path):
    tree, _store, service, inbox, _tenant = build(
        tmp_path, constant(InvoiceProducerError("BROKEN", "bad"))
    )
    archive = tree.state_dir(FolderState.ARCHIVE_ERREUR)
    (archive / "facture.pdf").write_bytes(b"previous error")
    put(inbox, "facture.pdf")

    report = service.ingest_inbox()

    assert (archive / "facture__1.pdf").is_file()
    assert report.archived == 1


# -- duplicates ---------------------------------------------------------------


def test_direct_duplicate_in_store_is_archived_without_extraction(tmp_path):
    inbox_path = tmp_path / "flow" / TENANT / "00_INBOX"
    digest = put(inbox_path, "facture.pdf")
    tree, store, service, inbox, _tenant = build(
        tmp_path,
        constant(AssertionError("extractor must not run for a duplicate")),
    )
    assert (inbox / "facture.pdf").is_file()
    store.upsert_document_line(
        TENANT,
        derive_document_id(digest),
        {"label": "already known", "status": "VALIDE"},
    )

    report = service.ingest_inbox()

    destination = tree.state_dir(FolderState.ARCHIVE_ERREUR) / "facture.pdf"
    assert destination.is_file()
    assert report.duplicates == 1
    assert report.archived == 1
    assert report.moved_to_03 == report.moved_to_02 == 0
    assert report.documents[0].outcome == OUTCOME_DUPLICATE
    assert any("DUPLICATE_SHA256" in trace for trace in report.traces)
    line = store.get_line(TENANT, derive_document_id(digest))
    assert line["label"] == "already known"
    assert line["raw_status"] == "VALIDE"


def test_direct_duplicate_already_in_archive_is_archived(tmp_path):
    tree, _store, service, inbox, _tenant = build(tmp_path, constant(complete()))
    data = b"%PDF-1.4 duplicate"
    archive = tree.state_dir(FolderState.ARCHIVE_ERREUR)
    (archive / "older.pdf").write_bytes(data)
    put(inbox, "facture.pdf", data)

    report = service.ingest_inbox()

    assert report.duplicates == 1
    assert (archive / "facture.pdf").is_file()
    assert (archive / "older.pdf").is_file()
    assert not (inbox / "facture.pdf").exists()


def test_duplicate_within_same_inbox_pass(tmp_path):
    tree, _store, service, inbox, _tenant = build(tmp_path, constant(complete()))
    data = b"%PDF-1.4 same content"
    put(inbox, "a.pdf", data)
    put(inbox, "b.pdf", data)

    report = service.ingest_inbox()

    assert report.scanned == 2
    assert report.moved_to_03 == 1
    assert report.duplicates == 1
    category_dir = tree.ged_dir("FACTURES_FOURNISSEURS")
    assert (category_dir / "a.pdf").is_file()
    assert (
        tree.state_dir(FolderState.ARCHIVE_ERREUR) / "b.pdf"
    ).is_file()


def test_duplicate_of_pending_document_is_archived(tmp_path):
    tree, store, service, inbox, _tenant = build(
        tmp_path, constant(complete(invoice_number=None))
    )
    data = b"%PDF-1.4 pending"
    digest = put(inbox, "a.pdf", data)
    put(inbox, "b.pdf", data)

    report = service.ingest_inbox()

    assert report.moved_to_02 == 1
    assert report.duplicates == 1
    assert (tree.state_dir(FolderState.A_COMPLETER) / "a.pdf").is_file()
    assert (tree.state_dir(FolderState.ARCHIVE_ERREUR) / "b.pdf").is_file()
    line = store.get_line(TENANT, derive_document_id(digest))
    assert line["raw_status"] == "EN_ATTENTE"


# -- multiple files -----------------------------------------------------------


def test_two_documents_are_routed_independently(tmp_path):
    def extractor(source: Path):
        if source.name == "complete.pdf":
            return complete()
        return complete(invoice_number=None)

    tree, _store, service, inbox, _tenant = build(tmp_path, extractor)
    put(inbox, "complete.pdf", b"complete")
    put(inbox, "pending.pdf", b"pending")

    report = service.ingest_inbox()

    assert report.scanned == 2
    assert report.moved_to_03 == 1
    assert report.moved_to_02 == 1
    assert report.rows_written == 1
    assert (
        tree.ged_dir("FACTURES_FOURNISSEURS")
        / "complete.pdf"
    ).is_file()
    assert (tree.state_dir(FolderState.A_COMPLETER) / "pending.pdf").is_file()


def test_tenant_isolation(tmp_path):
    root = tmp_path / "flow"
    tree_a = ensure_tree("TENANT_A", root)
    ensure_tree("TENANT_B", root)
    store = SqliteAccountingStore(tmp_path / "accounting.sqlite3")
    service = InboxIngestionService(
        "TENANT_A", store, root, extractor=constant(complete())
    )
    put(tree_a.state_dir(FolderState.INBOX), "facture.pdf")

    report = service.ingest_inbox()

    assert report.moved_to_03 == 1
    assert not (root / "TENANT_B" / "01_EN_COURS" / "facture.pdf").exists()
    assert list(
        (root / "TENANT_B" / "03_VALIDE_PRET_FEC").rglob("facture.pdf")
    ) == []
