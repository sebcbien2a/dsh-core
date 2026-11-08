"""P17 - APD business specialisation, e-mail mirror, expense sub-tables.

Offline-only exhaustive coverage of:

* the APD company profile and the manual mailbox connector;
* the six mailbox mirror folders and the PDF state follow-up;
* the ``07-compta/Cockpit_Compta.md`` cockpit (with ``01-contexte`` alias);
* the six ``FRAIS_GENERAUX`` sub-categories and their dedicated workbooks;
* the two master tables under ``04_EXPORT_COMPTA``, classification marker,
  VAT anti-duplication and the January/February fiscal-year switch;
* P17.1: manager-funded expenses (``COMPTE_PERSO_DIRIGEANT`` /
  ``455000_DIRIGEANT``), monthly reimbursement notes and the hotel
  ``Soirée Étape`` VAT split (CGI Art. 206).
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

from schemas.folder_state_schema import (
    APD_PROFILE,
    DEFERRED_MONTHS,
    DIRIGEANT_COUNTERPART_ACCOUNT,
    DocumentCategory,
    DocumentSource,
    DocumentStateRecord,
    ExpenseSubCategory,
    FiscalYearMode,
    FolderState,
    MissingFieldEnum,
    PaymentSource,
    TvaDedupStatus,
    classify_marker,
    counterparty_account_for_payment_source,
    normalize_payment_source,
)
from services.accounting_spreadsheet_export_service import (
    EXPENSE_SYNTHESIS_FILENAME,
    EXPENSE_SUBCATEGORY_HEADERS,
    MASTER_ACHATS_FILENAME,
    MASTER_ACHATS_HEADERS,
    MASTER_BANQUE_TVA_FILENAME,
    MASTER_BANQUE_TVA_HEADERS,
    MASTER_TVA_RECAP_HEADERS,
    PERSONAL_EXPENSE_NOTE_FILENAME,
    CategorySummaryExportError,
    ExpenseSubCategoryExporter,
    MasterTableExporter,
    PersonalExpenseNoteExporter,
    collect_supplier_diagnostics,
    compute_fiscal_totals,
    compute_tva_dedup,
    export_expense_subcategories,
    export_master_tables,
    export_personal_expense_note,
    export_supplier_ledger,
    is_personal_expense,
    resolve_fiscal_year,
)
from services.acompleter_workbook_service import (
    EXTENDED_HEADERS,
    HEADERS,
    P17_EXTENSION_HEADERS,
    AcompleterWorkbook,
    derive_document_id,
)
from services.document_flow_watcher import (
    COCKPIT_COMPTA_NOTE_NAME,
    COCKPIT_NOTE_NAME,
    DocumentFlowWatcher,
)
from services.email_inbox_bridge import (
    MAILBOX_STATES,
    EmailAttachment,
    EmailInboxBridge,
    EmailInboxBridgeError,
    EmailMessage,
    state_for_ingestion_outcome,
)
from services.folder_tree_service import ensure_tree
from services.hotel_expense_parser import (
    CGI_ARTICLE_206,
    detect_hotel_evening_stage,
    parse_hotel_evening_stage,
    split_hotel_vat,
)
from services.inbox_ingestion_service import (
    ExtractedInvoice,
    InboxIngestionService,
)
from services.invoice_producer_protocol import build_invoice_payload
from services.obsidian_vault_service import VaultWriter, parse_note
from services.sqlite_accounting_store import SqliteAccountingStore

TENANT = "APD"
GENERAL = DocumentCategory.FRAIS_GENERAUX


# -- shared helpers -----------------------------------------------------------


def make_store(tmp_path: Path) -> SqliteAccountingStore:
    return SqliteAccountingStore(tmp_path / "accounting.sqlite3")


def add_expense(
    store: SqliteAccountingStore,
    key: str,
    *,
    subcategory: ExpenseSubCategory | str | None,
    day: str = "2026-03-15",
    supplier: str = "FOURNISSEUR",
    label: str | None = None,
    ht: str = "100.00",
    tva: str = "20.00",
    ttc: str = "120.00",
    **extra: object,
) -> dict[str, object]:
    line: dict[str, object] = {
        "operation_date": day,
        "label": label or f"{key}.pdf",
        "counterparty": supplier,
        "invoice_id": key,
        "invoice_url": (
            f"03_VALIDE_PRET_FEC/FRAIS_GENERAUX/{key}.pdf"
        ),
        "category": GENERAL.value,
        "amount_ht": ht,
        "amount_tva": tva,
        "amount_ttc": ttc,
        "status": "VALIDE",
        "expense_subcategory": (
            ExpenseSubCategory(subcategory).value
            if subcategory is not None
            else None
        ),
    }
    line.update(extra)
    store.upsert_document_line(TENANT, key, line)
    return line


def sheet(path: Path, name: str | None = None):
    workbook = load_workbook(path)
    return workbook[name] if name is not None else workbook.active


def header_of(path: Path, name: str | None = None) -> tuple[str, ...]:
    ws = sheet(path, name)
    return tuple(cell.value for cell in ws[1])


def rows_of(path: Path, name: str | None = None) -> list[tuple]:
    ws = sheet(path, name)
    return [
        tuple(cell.value for cell in row)
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row)
        if any(cell.value not in (None, "") for cell in row)
    ]


# -- APD profile --------------------------------------------------------------


def test_apd_profile_has_expected_identity():
    assert APD_PROFILE.tenant_id == "APD"
    assert APD_PROFILE.name == "Assistance Peintures Diffusion"
    assert APD_PROFILE.siren == "810908392"
    assert APD_PROFILE.vat_number == "FR11810908392"
    assert APD_PROFILE.invoice_email == "facturesapd@gmail.com"


def test_apd_profile_rejects_invalid_siren_and_email():
    with pytest.raises(ValueError):
        type(APD_PROFILE)(
            siren="123",
            vat_number="FR11810908392",
            invoice_email="facturesapd@gmail.com",
        )
    with pytest.raises(ValueError):
        type(APD_PROFILE)(
            siren="810908392",
            vat_number="FR11810908392",
            invoice_email="not-an-email",
        )


# -- e-mail bridge ------------------------------------------------------------


class FakeMailClient:
    def __init__(self, messages: list[EmailMessage]) -> None:
        self.messages = list(messages)
        self.folders: list[str] = []
        self.moves: list[tuple[str, str, str]] = []

    def ensure_folder(self, folder: str) -> None:
        self.folders.append(folder)

    def list_messages(self, folder: str) -> list[EmailMessage]:
        return [message for message in self.messages if message.folder == folder]

    def move_message(
        self, message_id: str, source_folder: str, target_folder: str
    ) -> None:
        self.moves.append((message_id, source_folder, target_folder))
        self.messages = [
            EmailMessage(
                message_id=message.message_id,
                folder=target_folder if message.message_id == message_id else message.folder,
                subject=message.subject,
                sender=message.sender,
                attachments=message.attachments,
            )
            for message in self.messages
        ]


def complete_extractor(path: Path) -> ExtractedInvoice:
    return ExtractedInvoice(
        issue_date="2026-02-01",
        invoice_number="FA-2026-001",
        supplier_name="ACME SAS",
        amount_ht=Decimal("100.00"),
        amount_tva=Decimal("20.00"),
        amount_ttc=Decimal("120.00"),
        category=GENERAL,
        confidence=0.92,
    )


def incomplete_extractor(path: Path) -> ExtractedInvoice:
    return ExtractedInvoice(
        issue_date=None,
        invoice_number=None,
        supplier_name="ACME SAS",
        amount_ht=Decimal("100.00"),
        amount_tva=Decimal("20.00"),
        amount_ttc=Decimal("120.00"),
        category=GENERAL,
        confidence=0.50,
    )


def test_manual_fetch_creates_the_six_mirror_folders(tmp_path):
    client = FakeMailClient(
        [
            EmailMessage(
                "m1",
                attachments=(EmailAttachment("facture.pdf", b"%PDF-A"),),
            )
        ]
    )
    bridge = EmailInboxBridge(
        tenant_id=TENANT,
        root=tmp_path,
        client=client,
        process_attachments=False,
    )
    report = bridge.fetch_and_stage_emails()
    assert report.mirrored_folders == MAILBOX_STATES
    assert set(client.folders) == set(MAILBOX_STATES)
    staged = tmp_path / TENANT / "00_INBOX" / "facture.pdf"
    assert staged.is_file()
    assert report.records[0].sha256 == hashlib.sha256(b"%PDF-A").hexdigest()


def test_manual_fetch_ignores_unsupported_and_duplicate_attachments(tmp_path):
    content = b"%PDF-DUP"
    client = FakeMailClient(
        [
            EmailMessage(
                "m1",
                attachments=(
                    EmailAttachment("facture.pdf", content),
                    EmailAttachment("facture-copie.pdf", content),
                    EmailAttachment("notes.xlsx", b"xlsx"),
                ),
            )
        ]
    )
    bridge = EmailInboxBridge(
        tenant_id=TENANT,
        root=tmp_path,
        client=client,
        process_attachments=False,
    )
    report = bridge.fetch_and_stage_emails()
    assert report.attachments_seen == 3
    assert report.staged == 1
    assert report.duplicates == 1
    assert report.ignored == 1


def test_manual_fetch_triggers_pipeline_and_valide_mail_mirror(tmp_path):
    store = make_store(tmp_path)
    client = FakeMailClient(
        [
            EmailMessage(
                "m1",
                attachments=(EmailAttachment("facture.pdf", b"%PDF-VALIDE"),),
            )
        ]
    )
    bridge = EmailInboxBridge(
        tenant_id=TENANT,
        store=store,
        root=tmp_path,
        client=client,
        extractor=complete_extractor,
    )
    report = bridge.fetch_and_stage_emails()
    digest = hashlib.sha256(b"%PDF-VALIDE").hexdigest()
    assert report.staged == 1
    assert report.mail_moves == 1
    assert client.moves == [("m1", "00_INBOX", "03_VALIDE_PRET_FEC")]
    assert (
        tmp_path
        / TENANT
        / "03_VALIDE_PRET_FEC"
        / "FRAIS_GENERAUX"
        / "facture.pdf"
    ).is_file()
    line = store.get_line(TENANT, derive_document_id(digest))
    assert line is not None and line["raw_status"] == "VALIDE"
    assert report.ingestion[0].moved_to_03 == 1


def test_manual_fetch_routes_incomplete_pdf_to_a_completer_and_mirrors(tmp_path):
    store = make_store(tmp_path)
    client = FakeMailClient(
        [
            EmailMessage(
                "m1",
                attachments=(EmailAttachment("scan.pdf", b"%PDF-INCOMPLET"),),
            )
        ]
    )
    bridge = EmailInboxBridge(
        tenant_id=TENANT,
        store=store,
        root=tmp_path,
        client=client,
        extractor=incomplete_extractor,
    )
    report = bridge.fetch_and_stage_emails()
    assert report.ingestion[0].moved_to_02 == 1
    assert client.moves == [("m1", "00_INBOX", "02_A_COMPLETER")]
    assert bridge.records[0].mailbox_folder == "02_A_COMPLETER"
    assert (
        tmp_path / TENANT / "02_A_COMPLETER" / "scan.pdf"
    ).is_file()
    rows = AcompleterWorkbook(tmp_path / TENANT).read_rows()
    assert rows and rows[0].nom_fichier == "scan.pdf"


def test_sync_email_folder_state_follows_pdf_and_rejects_unknown_hash(tmp_path):
    client = FakeMailClient(
        [
            EmailMessage(
                "m1",
                attachments=(EmailAttachment("facture.pdf", b"%PDF-SYNC"),),
            )
        ]
    )
    bridge = EmailInboxBridge(
        tenant_id=TENANT,
        root=tmp_path,
        client=client,
        process_attachments=False,
    )
    bridge.fetch_and_stage_emails()
    digest = hashlib.sha256(b"%PDF-SYNC").hexdigest()
    assert bridge.sync_email_folder_state(digest, FolderState.EN_COURS) is True
    assert bridge.records[0].mailbox_folder == "01_EN_COURS"
    assert bridge.sync_email_folder_state(digest, FolderState.EN_COURS) is False
    with pytest.raises(EmailInboxBridgeError) as exc:
        bridge.sync_email_folder_state("f" * 64, FolderState.EN_COURS)
    assert exc.value.code == "EMAIL_TRACKING_NOT_FOUND"
    with pytest.raises(EmailInboxBridgeError) as exc:
        bridge.sync_email_folder_state(digest, "99_UNKNOWN")
    assert exc.value.code == "INVALID_EMAIL_STATE"


def test_state_for_ingestion_outcome_mapping():
    assert state_for_ingestion_outcome("VALIDE") is FolderState.VALIDE_PRET_FEC
    assert state_for_ingestion_outcome("A_COMPLETER") is FolderState.A_COMPLETER
    assert state_for_ingestion_outcome("DUPLICATE_SHA256") is FolderState.ARCHIVE_ERREUR
    assert state_for_ingestion_outcome("ERREUR") is FolderState.ARCHIVE_ERREUR
    assert state_for_ingestion_outcome("?") is FolderState.EN_COURS


# -- expense sub-categories ---------------------------------------------------


def test_expense_subcategory_contract_and_missing_fields():
    assert [item.value for item in ExpenseSubCategory] == [
        "RESTAURATION",
        "CARBURANT",
        "TRANSPORT_PARKING",
        "HOTELLERIE",
        "INFORMATIQUE_WEB",
        "HONORAIRES_JURIDIQUES",
        "FRAIS_REPRESENTATION_PRESIDENT",
    ]
    assert MissingFieldEnum.INVITE_RESTAURANT.value == "invite_restaurant"
    assert MissingFieldEnum.LIEU_RESTAURANT.value == "lieu_restaurant"
    assert [item.value for item in MissingFieldEnum] == [
        "date_piece",
        "numero_piece",
        "nom_fournisseur",
        "montant_ht",
        "montant_tva",
        "montant_ttc",
        "categorie",
        "taux_tva",
    ]


def test_expense_subcategory_physical_folders_under_03_and_04(tmp_path):
    tree = ensure_tree(TENANT, tmp_path)
    for state in (FolderState.VALIDE_PRET_FEC, FolderState.EXPORT_COMPTA):
        for subcategory in ExpenseSubCategory:
            directory = tree.expense_subcategory_dir(state, subcategory)
            assert directory.is_dir()
            assert directory.parent.name == GENERAL.value
            assert directory.name == subcategory.value


def test_expense_export_creates_global_and_four_subtables(tmp_path):
    store = make_store(tmp_path)
    for index, subcategory in enumerate(ExpenseSubCategory, start=1):
        add_expense(store, f"DOC-{index}", subcategory=subcategory)
    report = ExpenseSubCategoryExporter(store, root=tmp_path).export(TENANT)
    assert report.global_path.is_file()
    assert report.global_path.name == EXPENSE_SYNTHESIS_FILENAME
    assert set(report.subtable_paths) == set(ExpenseSubCategory)
    for subcategory, path in report.subtable_paths.items():
        assert path.is_file()
        assert path.parent.name == subcategory.value
        assert path.parent.parent.name == GENERAL.value
        assert header_of(path) == EXPENSE_SUBCATEGORY_HEADERS[subcategory]
        assert report.subcategory_counts[subcategory] == 1
        folder_03, folder_04 = report.subfolders[subcategory]
        assert folder_03.name == subcategory.value
        assert folder_04.name == subcategory.value


def test_restaurant_subtable_has_invite_and_place_columns(tmp_path):
    store = make_store(tmp_path)
    add_expense(
        store,
        "REST-001",
        subcategory=ExpenseSubCategory.RESTAURATION,
        supplier="BISTRO LYON",
        day="2026-04-02",
        invite_restaurant="M. Dupont",
        lieu_restaurant="Lyon 2e",
    )
    report = export_expense_subcategories(
        TENANT, root=tmp_path, store=store
    )
    path = report.subtable_paths[ExpenseSubCategory.RESTAURATION]
    headers = header_of(path)
    assert headers[:7] == (
        "Date Ticket",
        "Date de Paiement",
        "Lieu",
        "Société / Personne Invitée",
        "HT",
        "TVA",
        "TTC",
    )
    row = rows_of(path)[0]
    assert row[2] == "Lyon 2e"
    assert row[3] == "M. Dupont"
    assert row[4] == 100.0 and row[5] == 20.0 and row[6] == 120.0


def test_fuel_subtable_has_station_and_vehicle_columns(tmp_path):
    store = make_store(tmp_path)
    add_expense(
        store,
        "FUEL-001",
        subcategory=ExpenseSubCategory.CARBURANT,
        supplier="STATION TOTAL",
        fuel_station="TOTAL A6",
        vehicle="Renault Trafic AB-123-CD",
    )
    report = export_expense_subcategories(
        TENANT, root=tmp_path, store=store
    )
    path = report.subtable_paths[ExpenseSubCategory.CARBURANT]
    assert header_of(path) == (
        "Date",
        "Date de Paiement",
        "Fournisseur / Station",
        "Véhicule",
        "HT",
        "TVA",
        "TTC",
    )
    row = rows_of(path)[0]
    assert row[2] == "TOTAL A6"
    assert row[3] == "Renault Trafic AB-123-CD"


def test_transport_subtable_has_nature_and_route_columns(tmp_path):
    store = make_store(tmp_path)
    add_expense(
        store,
        "TOLL-001",
        subcategory=ExpenseSubCategory.TRANSPORT_PARKING,
        supplier="VINCI AUTOROUTES",
        transport_nature="Péage",
        transport_route="Lyon -> Marseille",
    )
    report = export_expense_subcategories(
        TENANT, root=tmp_path, store=store
    )
    path = report.subtable_paths[ExpenseSubCategory.TRANSPORT_PARKING]
    assert header_of(path) == (
        "Date",
        "Date de Paiement",
        "Nature (Péage/Train/Avion/Parking)",
        "Trajet / Lieu",
        "HT",
        "TVA",
        "TTC",
    )
    row = rows_of(path)[0]
    assert row[2] == "Péage"
    assert row[3] == "Lyon -> Marseille"


def test_hotel_subtable_separates_deductible_and_non_deductible_vat(tmp_path):
    store = make_store(tmp_path)
    add_expense(
        store,
        "HOTEL-001",
        subcategory=ExpenseSubCategory.HOTELLERIE,
        supplier="IBIS",
        ht="200.00",
        tva="20.00",
        ttc="220.00",
        hotel_place="Hôtel Ibis Lyon",
        ht_accommodation="180.00",
        ht_breakfast_meals="20.00",
        tva_deductible="5.00",
        tva_non_deductible="15.00",
    )
    report = export_expense_subcategories(
        TENANT, root=tmp_path, store=store
    )
    path = report.subtable_paths[ExpenseSubCategory.HOTELLERIE]
    assert header_of(path) == (
        "Date",
        "Date de Paiement",
        "Hôtel / Lieu",
        "HT Hébergement",
        "HT Petit-Déj / Repas",
        "TVA Déductible",
        "TVA Non Déductible",
        "TTC",
    )
    row = rows_of(path)[0]
    assert row[2] == "Hôtel Ibis Lyon"
    assert row[3] == 180.0
    assert row[4] == 20.0
    assert row[5] == 5.0
    assert row[6] == 15.0
    assert row[7] == 220.0
    summary = rows_of(report.global_path)
    restaurant_summary = next(
        item for item in summary if item[0] == "HOTELLERIE"
    )
    assert restaurant_summary[2] == 200.0
    assert restaurant_summary[3] == 20.0


def test_expense_export_rejects_invalid_tenant(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(CategorySummaryExportError) as exc:
        ExpenseSubCategoryExporter(store, root=tmp_path).export("../bad")
    assert exc.value.code == "INVALID_TENANT_ID"


# -- master tables, classification, anti-duplicate & fiscal year -------------


def export_master(store: SqliteAccountingStore, tmp_path: Path):
    return MasterTableExporter(store, root=tmp_path).export(TENANT)


def test_master_tables_are_written_under_04_export_compta_tenant(tmp_path):
    store = make_store(tmp_path)
    add_expense(store, "DOC-1", subcategory=ExpenseSubCategory.RESTAURATION)
    expected_root = (
        tmp_path / TENANT / "04_EXPORT_COMPTA" / TENANT
    )
    expected_root.mkdir(parents=True, exist_ok=True)
    legacy = expected_root / "TABLEAU_MERE_ACHATS_CHARGES.xlsx"
    legacy.write_bytes(b"legacy")
    report = export_master(store, tmp_path)
    assert report.pieces_path == expected_root / MASTER_ACHATS_FILENAME
    assert report.achats_path == report.pieces_path
    assert report.banque_tva_path == expected_root / MASTER_BANQUE_TVA_FILENAME
    assert report.pieces_path.is_file()
    assert report.banque_tva_path.is_file()
    assert legacy.exists() is False


def test_master_tables_expose_p23_piece_and_bank_columns(tmp_path):
    store = make_store(tmp_path)
    add_expense(store, "DOC-1", subcategory=ExpenseSubCategory.RESTAURATION)
    report = export_master(store, tmp_path)
    assert header_of(report.pieces_path) == MASTER_ACHATS_HEADERS
    assert header_of(report.banque_tva_path) == MASTER_BANQUE_TVA_HEADERS
    assert MASTER_ACHATS_HEADERS == (
        "Date Pièce",
        "Date de Paiement",
        "Flux (VENTE_CLIENT / ACHAT_FOURNISSEUR)",
        "Tiers (Client ou Fournisseur)",
        "N° Pièce",
        "Montant HT",
        "Montant TVA",
        "Montant TTC",
        "Statut",
        "Lien PDF Pièce (Cliquable)",
        "Rapprochement Banque / LCR",
        "Écart / Solde Dû",
    )
    assert MASTER_BANQUE_TVA_HEADERS == (
        "Date Opération",
        "Tiers / Contrepartie identifiée (Client / Fournisseur / Dirigeant)",
        "Type",
        "Libellé Opération",
        "Débit",
        "Crédit",
        "Pièces Rapprochées (N° & Liens PDF cliquables)",
        "TVA Déduite/Collectée",
        "Solde Lettrage",
    )
    assert header_of(report.banque_tva_path, "RECAP_TVA") == MASTER_TVA_RECAP_HEADERS


def test_classification_marker_classified_vs_unclassified(tmp_path):
    store = make_store(tmp_path)
    add_expense(
        store,
        "REST-001",
        subcategory=ExpenseSubCategory.RESTAURATION,
        label="REST-001.pdf",
    )
    add_expense(
        store,
        "DIVERS-001",
        subcategory=None,
        label="DIVERS-001.pdf",
        supplier="INCONNU",
    )
    report = export_master(store, tmp_path)
    rows = {row[4]: row for row in rows_of(report.pieces_path)}
    assert rows["REST-001"][2] == "ACHAT_FOURNISSEUR"
    assert rows["DIVERS-001"][2] == "ACHAT_FOURNISSEUR"
    assert classify_marker(ExpenseSubCategory.CARBURANT) == (
        "IDENTIFIEE_CLASSEE:CARBURANT"
    )
    assert classify_marker(None) == "NON_CLASSEE"


def test_vat_anti_duplicate_counts_piece_once(tmp_path):
    store = make_store(tmp_path)
    add_expense(
        store,
        "DOC-A",
        subcategory=ExpenseSubCategory.RESTAURATION,
        label="REST-001.pdf",
        supplier="BISTRO LYON",
        ht="100.00",
        tva="20.00",
        ttc="120.00",
    )
    add_expense(
        store,
        "DOC-B",
        subcategory=ExpenseSubCategory.RESTAURATION,
        label="REST-001.pdf",
        supplier="BISTRO LYON",
        ht="100.00",
        tva="20.00",
        ttc="120.00",
    )
    report = export_master(store, tmp_path)
    assert len(report.duplicates) == 1
    duplicate = report.duplicates[0]
    assert duplicate.piece_number.casefold() == "rest-001"
    assert duplicate.supplier == "bistro lyon"
    assert duplicate.amount == Decimal("120.00")
    assert report.fiscal_totals[2026].tva == Decimal("20.00")
    recap = rows_of(report.banque_tva_path, "RECAP_TVA")
    assert len(recap) == 1
    assert recap[0][3] == 20.0


def test_compute_tva_dedup_marks_non_verifiable_without_piece(tmp_path):
    store = make_store(tmp_path)
    store.upsert_document_line(
        TENANT,
        "FREE-1",
        {
            "label": None,
            "counterparty": None,
            "category": GENERAL.value,
            "amount_ttc": "12.00",
            "status": "VALIDE",
        },
    )
    statuses, duplicates = compute_tva_dedup(
        store.list_master_lines(TENANT)
    )
    assert statuses == [TvaDedupStatus.NON_VERIFIABLE]
    assert duplicates == ()


def test_fiscal_year_switch_to_next_january_isolates_tva(tmp_path):
    store = make_store(tmp_path)
    add_expense(
        store,
        "DEC-2026",
        subcategory=ExpenseSubCategory.RESTAURATION,
        day="2026-12-31",
        tva="20.00",
        ttc="120.00",
        fiscal_year_mode="SUIVANT",
        fiscal_year_target="2027",
        deferred_month_override="01",
    )
    add_expense(
        store,
        "JUN-2026",
        subcategory=ExpenseSubCategory.CARBURANT,
        day="2026-06-15",
        tva="10.00",
        ttc="60.00",
        fiscal_year_mode="ACTUEL",
    )
    report = export_master(store, tmp_path)
    assert report.fiscal_totals[2026].tva == Decimal("10.00")
    assert report.fiscal_totals[2027].tva == Decimal("20.00")
    rows = {
        row[4]: row for row in rows_of(report.pieces_path)
    }
    assert set(rows) == {"DEC-2026", "JUN-2026"}
    recap = rows_of(report.banque_tva_path, "RECAP_TVA")
    assert [(row[0], row[3]) for row in recap] == [(2026, 10.0), (2027, 20.0)]


def test_fiscal_year_modes_precedent_actuel_suivant():
    def decision(mode, target=None, month=None, day="2026-06-30"):
        return resolve_fiscal_year(
            {
                "operation_date": day,
                "fiscal_year_mode": mode,
                "fiscal_year_target": target,
                "deferred_month_override": month,
            }
        )

    previous = decision("PRECEDENT")
    assert previous.target_year == 2025
    assert previous.effective_date == date(2025, 6, 30)
    current = decision("ACTUEL")
    assert current.target_year == 2026
    following = decision("SUIVANT", "2027", "02")
    assert following.target_year == 2027
    assert following.deferred_month == "02"
    assert following.effective_date == date(2027, 2, 28)
    assert DEFERRED_MONTHS == frozenset({"01", "02"})


def test_fiscal_year_target_without_mode_defaults_to_actuel():
    decision = resolve_fiscal_year(
        {
            "operation_date": "2026-02-01",
            "fiscal_year_target": "2027",
            "deferred_month_override": None,
        }
    )
    assert decision.mode is FiscalYearMode.ACTUEL
    assert decision.target_year == 2027
    assert decision.deferred_month is None


def test_fiscal_totals_exclude_duplicate_piece():
    rows = [
        {
            "operation_date": "2026-01-10",
            "label": "FAC-1.pdf",
            "counterparty": "ACME",
            "amount_ht": "100",
            "amount_tva": "20",
            "amount_ttc": "120",
            "fiscal_year_mode": "ACTUEL",
        },
        {
            "operation_date": "2026-01-10",
            "label": "FAC-1.pdf",
            "counterparty": "ACME",
            "amount_ht": "100",
            "amount_tva": "20",
            "amount_ttc": "120",
            "fiscal_year_mode": "ACTUEL",
        },
    ]
    statuses, _ = compute_tva_dedup(rows)
    totals = compute_fiscal_totals(rows, statuses)
    assert totals[2026].pieces == 1
    assert totals[2026].tva == Decimal("20.00")


def test_master_export_rejects_invalid_tenant(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(CategorySummaryExportError) as exc:
        MasterTableExporter(store, root=tmp_path).export("../bad")
    assert exc.value.code == "INVALID_TENANT_ID"


def test_master_export_convenience_wrapper(tmp_path):
    store = make_store(tmp_path)
    add_expense(store, "DOC-1", subcategory=ExpenseSubCategory.CARBURANT)
    report = export_master_tables(TENANT, root=tmp_path, store=store)
    assert report.row_count == 1
    assert report.pieces_path.is_file()


# -- P23 unified pieces table & Caisse d'Épargne mirror ----------------------


def test_p23_unified_pieces_table_contains_sales_and_purchases(tmp_path):
    store = make_store(tmp_path)
    add_expense(
        store,
        "ACHAT-BOSCH",
        subcategory=ExpenseSubCategory.CARBURANT,
        supplier="ROBERT BOSCH",
        ht="100.00",
        tva="20.00",
        ttc="120.00",
    )
    store.upsert_document_line(
        TENANT,
        "SALE-FA2001",
        {
            "operation_date": "2026-05-20",
            "label": "FA2001",
            "counterparty": "CLIENT SARL",
            "invoice_id": "FA2001",
            "invoice_url": "03_VALIDE_PRET_FEC/VENTES_CLIENTS/FA2001.pdf",
            "category": DocumentCategory.VENTES_CLIENTS.value,
            "amount_ht": "1000.00",
            "amount_tva": "200.00",
            "amount_ttc": "1200.00",
            "document_type": "CUSTOMER_INVOICE",
            "status": "VALIDE",
        },
    )

    report = export_master(store, tmp_path)
    rows = rows_of(report.pieces_path)

    assert len(rows) == 2
    by_flux = {row[2]: row for row in rows}
    assert set(by_flux) == {"VENTE_CLIENT", "ACHAT_FOURNISSEUR"}
    sale = by_flux["VENTE_CLIENT"]
    assert sale[3] == "CLIENT SARL"
    assert sale[4] == "FA2001"
    assert sale[5] == 1000.0
    assert sale[6] == 200.0
    assert sale[7] == 1200.0
    purchase = by_flux["ACHAT_FOURNISSEUR"]
    assert purchase[3] == "ROBERT BOSCH"
    assert purchase[4] == "ACHAT-BOSCH"
    for row in rows:
        for column in (5, 6, 7, 11):
            assert isinstance(row[column], (int, float))
    assert report.row_count == 2


def test_p23_bank_mirror_letters_sales_piece_and_links_pdf(tmp_path):
    store = make_store(tmp_path)
    output_root = tmp_path / TENANT / "04_EXPORT_COMPTA" / TENANT
    piece = (
        output_root
        / "PIECES_JOINTES"
        / "VENTES"
        / "FA2001.pdf"
    )
    piece.parent.mkdir(parents=True, exist_ok=True)
    piece.write_bytes(b"%PDF-1.4 facture client")
    store.upsert_document_line(
        TENANT,
        "SALE-FA2001",
        {
            "operation_date": "2026-05-20",
            "label": "FA2001",
            "counterparty": "CLIENT SARL",
            "invoice_id": "FA2001",
            "category": DocumentCategory.VENTES_CLIENTS.value,
            "amount_ht": "1000.00",
            "amount_tva": "200.00",
            "amount_ttc": "1200.00",
            "document_type": "CUSTOMER_INVOICE",
            "piece_pdf": "PIECES_JOINTES/VENTES/FA2001.pdf",
            "reconciliation_status": "PAYEE_RAPPROCHEE",
            "reconciliation_reference": "REMISE 94694",
            "reconciliation_amount": "1200.00",
            "status": "VALIDE",
        },
    )
    store.add_line(
        TENANT,
        "OFX-94694",
        {
            "operation_date": "2026-06-30",
            "label": "REMISE ENCAISSEMENT N 94694",
            "credit_amount": "1200.00",
            "category": DocumentCategory.BANQUE_FINANCE.value,
            "payment_method": "LCR_ENCAISSEMENT_LOT",
            "fitid": "FIT-94694",
            "document_type": "OFX",
            "status": "PENDING",
        },
    )

    report = export_master(store, tmp_path)
    rows = rows_of(report.banque_tva_path)

    assert len(rows) == 1
    assert "CLIENT" in rows[0][1]
    assert rows[0][2] == "LCR_ENCAISSEMENT_LOT"
    assert rows[0][3] == "REMISE ENCAISSEMENT N 94694"
    assert rows[0][4] == 0.0
    assert rows[0][5] == 1200.0
    assert "FA2001" in rows[0][6]
    assert rows[0][7] == 200.0
    assert rows[0][8] == 0.0
    link_cell = sheet(report.banque_tva_path).cell(row=2, column=7)
    assert link_cell.hyperlink is not None
    assert "FA2001.pdf" in str(link_cell.hyperlink.target)


def test_p23_atomic_save_falls_back_to_copy_on_rename_lock(
    tmp_path, monkeypatch
):
    from openpyxl import Workbook

    from services import accounting_spreadsheet_export_service as accounting

    target = tmp_path / "locked.xlsx"

    def locked_replace(source, destination):
        raise PermissionError("fichier ouvert dans Excel")

    monkeypatch.setattr(accounting.os, "replace", locked_replace)
    workbook = Workbook()
    workbook.active["A1"] = "contenu"
    accounting._atomic_save_workbook(workbook, target)

    assert target.is_file()
    assert [path.name for path in tmp_path.iterdir()] == [target.name]
    reloaded = load_workbook(target)
    assert reloaded.active["A1"].value == "contenu"


# -- store migration & _A_COMPLETER columns ----------------------------------


def test_sqlite_store_migrates_all_p17_columns(tmp_path):
    store = make_store(tmp_path)
    import sqlite3

    with sqlite3.connect(store.db_path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(reconciliation_lines)"
            )
        }
    assert {
        "expense_subcategory",
        "invite_restaurant",
        "lieu_restaurant",
        "is_classified_marker",
        "tva_dedup_status",
        "fiscal_year_mode",
        "fiscal_year_target",
        "deferred_month_override",
        "ht_accommodation",
        "tva_deductible",
        "tva_non_deductible",
    } <= columns


def test_sqlite_store_roundtrips_p17_fields(tmp_path):
    store = make_store(tmp_path)
    add_expense(
        store,
        "DOC-1",
        subcategory=ExpenseSubCategory.RESTAURATION,
        invite_restaurant="M. Martin",
        lieu_restaurant="Lyon",
        is_classified_marker="IDENTIFIEE_CLASSEE:RESTAURATION",
        tva_dedup_status="TVA_UNIQUE",
        fiscal_year_mode="SUIVANT",
        fiscal_year_target="2027",
        deferred_month_override="02",
    )
    line = store.get_line(TENANT, "DOC-1")
    assert line["expense_subcategory"] == "RESTAURATION"
    assert line["invite_restaurant"] == "M. Martin"
    assert line["lieu_restaurant"] == "Lyon"
    assert line["is_classified_marker"] == "IDENTIFIEE_CLASSEE:RESTAURATION"
    assert line["tva_dedup_status"] == "TVA_UNIQUE"
    assert line["fiscal_year_mode"] == "SUIVANT"
    assert line["fiscal_year_target"] == "2027"
    assert line["deferred_month_override"] == "02"


def test_acompleter_workbook_keeps_legacy_headers_and_adds_p17_columns(tmp_path):
    workbook = AcompleterWorkbook(tmp_path)
    workbook.ensure_workbook()
    assert HEADERS == (
        "ID document",
        "Hash SHA-256",
        "Nom fichier",
        "Champs manquants",
        "Catégorie suggérée",
        "Statut",
    )
    assert EXTENDED_HEADERS == HEADERS + P17_EXTENSION_HEADERS
    ws = sheet(workbook.workbook_path)
    assert tuple(cell.value for cell in ws[1]) == EXTENDED_HEADERS
    assert ws.auto_filter.ref == "A1:F1"


def test_acompleter_workbook_accepts_p17_metadata(tmp_path):
    workbook = AcompleterWorkbook(tmp_path)
    sha = "a" * 64
    workbook.upsert(sha, "facture.pdf", [], GENERAL)
    assert workbook.update_p17_metadata(
        sha,
        expense_subcategory=ExpenseSubCategory.HOTELLERIE,
        invite_restaurant="Mme Durand",
        lieu_restaurant="Annecy",
        is_classified_marker="IDENTIFIEE_CLASSEE:HOTELLERIE",
        tva_dedup_status="TVA_UNIQUE",
        fiscal_year_mode=FiscalYearMode.SUIVANT,
        fiscal_year_target=2027,
        deferred_month_override="01",
    ) is True
    metadata = workbook.read_p17_metadata(sha)
    assert metadata == {
        "Sous-catégorie Frais": "HOTELLERIE",
        "Invité Restaurant": "Mme Durand",
        "Lieu Restaurant": "Annecy",
        "Repère Classement": "IDENTIFIEE_CLASSEE:HOTELLERIE",
        "Statut Anti-Doublon TVA": "TVA_UNIQUE",
        "Mode Exercice Fiscal": "SUIVANT",
        "Exercice Cible": "2027",
        "Mois Décalé": "01",
        "Source de Paiement": "",
        "Compte de Contrepartie": "",
        "Report": "",
        "Rattacher comme ALIAS de (ou NOUVEAU_TIERS)": "",
    }


def test_acompleter_workbook_rejects_bad_p17_values(tmp_path):
    workbook = AcompleterWorkbook(tmp_path)
    sha = "b" * 64
    workbook.upsert(sha, "facture.pdf", [], GENERAL)
    with pytest.raises(Exception) as exc:
        workbook.update_p17_metadata(sha, expense_subcategory="AUTRE")
    assert getattr(exc.value, "code", None) == "INVALID_P17_VALUE"
    with pytest.raises(Exception) as exc:
        workbook.update_p17_metadata(sha, deferred_month_override="13")
    assert getattr(exc.value, "code", None) == "INVALID_P17_VALUE"


def test_upsert_champs_manquants_accepts_restaurant_fields(tmp_path):
    workbook = AcompleterWorkbook(tmp_path)
    sha = "c" * 64
    workbook.upsert(
        sha,
        "ticket.pdf",
        ["invite_restaurant", "lieu_restaurant"],
        GENERAL,
    )
    row = workbook.read_rows()[0]
    assert row.champs_manquants == "invite_restaurant, lieu_restaurant"


# -- 07-compta cockpit --------------------------------------------------------


def test_watcher_writes_cockpit_to_07_compta_with_alias_and_pending_links(
    tmp_path,
):
    store = make_store(tmp_path)
    tree = ensure_tree(TENANT, tmp_path)
    workbook = AcompleterWorkbook(tmp_path / TENANT)
    payload = b"%PDF-COCKPIT"
    digest = hashlib.sha256(payload).hexdigest()
    workbook.upsert(digest, "facture.pdf", [], GENERAL)
    workbook.align_statut(digest, "VALIDE")
    (tree.state_dir(FolderState.A_COMPLETER) / "facture.pdf").write_bytes(payload)
    (tree.state_dir(FolderState.A_COMPLETER) / "en-attente.pdf").write_bytes(
        b"pending"
    )
    vault = VaultWriter(tmp_path / "vault")
    watcher = DocumentFlowWatcher(
        tenant_id=TENANT,
        root=tmp_path,
        store=store,
        auto_export=True,
        export_dir=tmp_path / "exports",
        vault_writer=vault,
    )
    report = watcher.run_once(now=0.0)
    assert report.export_errors == ()
    primary = (
        tmp_path / "vault" / "07-compta" / f"{COCKPIT_COMPTA_NOTE_NAME}.md"
    )
    alias = (
        tmp_path / "vault" / "01-contexte" / f"{COCKPIT_NOTE_NAME}.md"
    )
    assert primary.is_file()
    assert alias.is_file()
    parsed = parse_note(primary.read_text(encoding="utf-8-sig"))
    assert parsed is not None
    _frontmatter, body = parsed
    assert "Cockpit comptable" in body
    assert "[en-attente.pdf](02_A_COMPLETER/en-attente.pdf)" in body
    assert "residual_delta = 0" in body
    assert "[[07-compta/Cockpit_Compta|Cockpit comptable 07-compta]]" in body
    alias_text = alias.read_text(encoding="utf-8-sig")
    assert "[[07-compta/Cockpit_Compta|Cockpit comptable]]" in alias_text
    assert any(
        trace.startswith("EXPORT:COCKPIT_07_COMPTA:")
        for trace in report.exports
    )


# -- P17.1 manager-funded expenses (Compte Courant 455000_DIRIGEANT) ----------


def test_payment_source_contract_and_counterparty_account_helpers():
    assert [source.value for source in PaymentSource] == [
        "BANQUE_PRO",
        "COMPTE_PERSO_DIRIGEANT",
    ]
    assert normalize_payment_source("compte_perso_dirigeant") is (
        PaymentSource.COMPTE_PERSO_DIRIGEANT
    )
    assert normalize_payment_source("BANQUE_PRO") is PaymentSource.BANQUE_PRO
    assert normalize_payment_source("INCONNU") is None
    assert (
        counterparty_account_for_payment_source(
            PaymentSource.COMPTE_PERSO_DIRIGEANT
        )
        == DIRIGEANT_COUNTERPART_ACCOUNT
        == "455000_DIRIGEANT"
    )
    assert counterparty_account_for_payment_source(PaymentSource.BANQUE_PRO) is None


def test_document_state_record_accepts_payment_source():
    from schemas.folder_state_schema import DocumentSource

    record = DocumentStateRecord(
        sha256="a" * 64,
        nom_fichier="ticket.pdf",
        state=FolderState.A_COMPLETER,
        category=GENERAL,
        source=DocumentSource.MANUAL,
        horodatage=datetime.now(timezone.utc),
        payment_source="COMPTE_PERSO_DIRIGEANT",
        counterparty_account=DIRIGEANT_COUNTERPART_ACCOUNT,
    )
    assert record.payment_source is PaymentSource.COMPTE_PERSO_DIRIGEANT
    assert record.counterparty_account == "455000_DIRIGEANT"


def test_acompleter_workbook_persists_payment_source_column(tmp_path):
    workbook = AcompleterWorkbook(tmp_path)
    sha = "d" * 64
    workbook.upsert(sha, "ticket.pdf", [], GENERAL)
    assert workbook.update_p17_metadata(
        sha, payment_source=PaymentSource.COMPTE_PERSO_DIRIGEANT
    ) is True
    metadata = workbook.read_p17_metadata(sha)
    assert metadata["Source de Paiement"] == "COMPTE_PERSO_DIRIGEANT"
    assert metadata["Compte de Contrepartie"] == "455000_DIRIGEANT"
    assert "Source de Paiement" in P17_EXTENSION_HEADERS
    assert "Source de Paiement" in EXTENDED_HEADERS


def test_sqlite_store_migrates_and_roundtrips_payment_source(tmp_path):
    store = make_store(tmp_path)
    add_expense(
        store,
        "PERSO-001",
        subcategory=ExpenseSubCategory.RESTAURATION,
        payment_source="COMPTE_PERSO_DIRIGEANT",
    )
    line = store.get_line(TENANT, "PERSO-001")
    assert line["payment_source"] == "COMPTE_PERSO_DIRIGEANT"
    assert line["counterparty_account"] == "455000_DIRIGEANT"
    assert is_personal_expense(line) is True


def test_personal_expense_is_validated_without_pro_bank_match(tmp_path):
    root = tmp_path / "flow"
    tree = ensure_tree(TENANT, root)
    store = make_store(tmp_path)
    extracted = ExtractedInvoice(
        issue_date="2026-03-15",
        invoice_number="TICKET-CAFE-001",
        supplier_name="CAFE DE LA GARE",
        amount_ht="10.00",
        amount_tva="1.00",
        amount_ttc="11.00",
        category=GENERAL,
        confidence=0.95,
        payment_source="COMPTE_PERSO_DIRIGEANT",
        expense_subcategory="RESTAURATION",
    )
    service = InboxIngestionService(
        TENANT, store, root, extractor=lambda _source: extracted
    )
    payload = b"%PDF-CAFE-PERSO"
    tree.state_dir(FolderState.INBOX).joinpath("ticket-cafe.pdf").write_bytes(
        payload
    )
    report = service.ingest_inbox()
    assert report.moved_to_03 == 1
    digest = hashlib.sha256(payload).hexdigest()
    line = store.get_line(TENANT, derive_document_id(digest))
    assert line is not None
    assert line["raw_status"] == "VALIDE"
    assert line["payment_source"] == "COMPTE_PERSO_DIRIGEANT"
    assert line["counterparty_account"] == "455000_DIRIGEANT"
    assert line["expense_subcategory"] == "RESTAURATION"
    assert collect_supplier_diagnostics(store, TENANT) == ()
    ledger = export_supplier_ledger(
        TENANT, export_dir=tmp_path / "exports", store=store
    )
    ws = load_workbook(ledger).active
    entry = [cell.value for cell in ws[2]]
    assert entry[1] == "FACTURE"
    assert entry[7] == "HORS_PERIMETRE"
    text = " ".join(
        str(cell.value or "")
        for row in ws.iter_rows()
        for cell in row
    )
    assert "A_RAPPROCHER" not in text


def test_personal_expense_note_lists_pieces_and_total_to_reimburse(tmp_path):
    store = make_store(tmp_path)
    add_expense(
        store,
        "PERSO-MARS",
        subcategory=ExpenseSubCategory.RESTAURATION,
        day="2026-03-12",
        supplier="CAFE DE LA GARE",
        ht="10.00",
        tva="1.00",
        ttc="11.00",
        payment_source="COMPTE_PERSO_DIRIGEANT",
    )
    add_expense(
        store,
        "PERSO-MARS-2",
        subcategory=ExpenseSubCategory.TRANSPORT_PARKING,
        day="2026-03-20",
        supplier="SNCF",
        ht="40.00",
        tva="4.00",
        ttc="44.00",
        payment_source="COMPTE_PERSO_DIRIGEANT",
    )
    add_expense(
        store,
        "PRO-MARS",
        subcategory=ExpenseSubCategory.CARBURANT,
        day="2026-03-21",
        supplier="TOTAL",
        ht="50.00",
        tva="10.00",
        ttc="60.00",
        payment_source="BANQUE_PRO",
    )
    report = export_personal_expense_note(
        TENANT,
        "2026-03",
        export_dir=tmp_path / "exports",
        store=store,
    )
    expected = (
        tmp_path
        / "exports"
        / TENANT
        / PERSONAL_EXPENSE_NOTE_FILENAME.format(month="2026-03")
    )
    assert report.output_path == expected
    assert expected.is_file()
    assert report.piece_count == 2
    assert report.total_net == Decimal("55.00")
    rows = rows_of(expected)
    assert len(rows) == 3
    assert rows[0][1] == "CAFE DE LA GARE"
    assert rows[0][8] == "COMPTE_PERSO_DIRIGEANT"
    assert rows[0][9] == "455000_DIRIGEANT"
    assert rows[-1][0] == "TOTAL NET A REMBOURSER"
    assert rows[-1][7] == 55.0


def test_personal_expense_note_exports_every_month(tmp_path):
    store = make_store(tmp_path)
    for key, day in (("M1", "2026-01-10"), ("M2", "2026-02-11")):
        add_expense(
            store,
            key,
            subcategory=ExpenseSubCategory.RESTAURATION,
            day=day,
            ttc="12.00",
            tva="1.20",
            payment_source="COMPTE_PERSO_DIRIGEANT",
        )
    report = PersonalExpenseNoteExporter(
        store, export_dir=tmp_path / "exports"
    ).export(TENANT)
    assert set(report.paths) == {"2026-01", "2026-02"}
    assert report.piece_count == 2
    assert all(path.is_file() for path in report.paths.values())


def test_master_table_marks_personal_counterparty_account(tmp_path):
    store = make_store(tmp_path)
    add_expense(
        store,
        "PERSO-455",
        subcategory=ExpenseSubCategory.RESTAURATION,
        payment_source="COMPTE_PERSO_DIRIGEANT",
    )
    report = export_master_tables(
        TENANT, root=tmp_path, store=store
    )
    assert report.pieces_path.is_file()
    line = store.get_line(TENANT, "PERSO-455")
    assert line is not None and is_personal_expense(line)
    assert line["counterparty_account"] == "455000_DIRIGEANT"


# -- P17.1 hotel "Soirée Étape" & CGI Art. 206 -------------------------------


SOIREE_ETAPE_TEXT = """
HOTEL IBIS LYON
Soirée Étape
Nuitée : 100,00 EUR TVA 10% : 10,00 EUR
Dîner : 30,00 EUR TVA 10% : 3,00 EUR
Petit déjeuner : 12,00 EUR TVA 10% : 1,20 EUR
Total TTC 156,20
"""


def test_hotel_evening_stage_parser_isolates_deductible_vat():
    breakdown = parse_hotel_evening_stage(SOIREE_ETAPE_TEXT)
    assert breakdown is not None
    assert breakdown.evening_stage is True
    assert breakdown.ht_accommodation == Decimal("100.00")
    assert breakdown.ht_meals == Decimal("42.00")
    assert breakdown.tva_non_deductible == Decimal("10.00")
    assert breakdown.tva_deductible == Decimal("4.20")
    assert breakdown.total_tva == Decimal("14.20")
    assert detect_hotel_evening_stage("Soirée Etape") is True
    assert detect_hotel_evening_stage("Facture simple") is False
    assert split_hotel_vat(SOIREE_ETAPE_TEXT) == (
        Decimal("4.20"),
        Decimal("10.00"),
    )
    assert CGI_ARTICLE_206 == "CGI Art. 206"


def test_hotel_evening_stage_parser_accepts_vat_recap_lines():
    text = "Soirée étape\nHébergement 80.00 8.00\nRestauration 20.00 2.00"
    breakdown = parse_hotel_evening_stage(text)
    assert breakdown is not None
    assert breakdown.ht_accommodation == Decimal("80.00")
    assert breakdown.ht_meals == Decimal("20.00")
    assert breakdown.tva_non_deductible == Decimal("8.00")
    assert breakdown.tva_deductible == Decimal("2.00")


def test_hotel_export_uses_soiree_etape_breakdown(tmp_path):
    store = make_store(tmp_path)
    add_expense(
        store,
        "HOTEL-SOIR-001",
        subcategory=ExpenseSubCategory.HOTELLERIE,
        supplier="IBIS LYON",
        ht="142.00",
        tva="14.20",
        ttc="156.20",
        hotel_place=SOIREE_ETAPE_TEXT,
    )
    report = export_expense_subcategories(
        TENANT, root=tmp_path, store=store
    )
    path = report.subtable_paths[ExpenseSubCategory.HOTELLERIE]
    row = rows_of(path)[0]
    assert row[3] == 100.0
    assert row[4] == 42.0
    assert row[5] == 4.2
    assert row[6] == 10.0
    summary = rows_of(report.global_path)
    hotel_summary = next(item for item in summary if item[0] == "HOTELLERIE")
    assert hotel_summary[3] == 14.2


def test_hotel_without_breakdown_keeps_accommodation_vat_non_deductible(tmp_path):
    store = make_store(tmp_path)
    add_expense(
        store,
        "HOTEL-SIMPLE",
        subcategory=ExpenseSubCategory.HOTELLERIE,
        supplier="IBIS",
        ht="100.00",
        tva="10.00",
        ttc="110.00",
    )
    report = export_expense_subcategories(
        TENANT, root=tmp_path, store=store
    )
    row = rows_of(
        report.subtable_paths[ExpenseSubCategory.HOTELLERIE]
    )[0]
    assert row[5] == 0.0
    assert row[6] == 10.0


def test_invoice_parser_exposes_hotel_evening_stage_metadata():
    payload, _notes = build_invoice_payload(
        """
        HOTEL IBIS
        Facture N° FA-2026-010
        Date 15/03/2026
        Soirée Étape
        Nuitée : 100,00 EUR TVA 10% : 10,00 EUR
        Dîner : 30,00 EUR TVA 10% : 3,00 EUR
        Petit déjeuner : 12,00 EUR TVA 10% : 1,20 EUR
        Net HT 142,00
        TVA 14,20
        Net à payer 156,20
        """,
        source_name="hotel.pdf",
    )
    metadata = payload["metadata"]
    assert metadata["expense_subcategory"] == "HOTELLERIE"
    assert metadata["hotel_evening_stage"] is True
    assert metadata["ht_accommodation"] == "100.00"
    assert metadata["ht_breakfast_meals"] == "42.00"
    assert metadata["tva_deductible"] == "4.20"
    assert metadata["tva_non_deductible"] == "10.00"
    assert metadata["cgi_article"] == CGI_ARTICLE_206

