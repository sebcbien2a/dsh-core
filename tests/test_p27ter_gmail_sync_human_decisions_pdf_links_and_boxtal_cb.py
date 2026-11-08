"""P27-ter - Gmail IMAP, human decisions, native PDF links and Boxtal CB."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

from schemas.folder_state_schema import DocumentCategory
from services.accounting_memory_learning_service import (
    AccountingMemoryLearningService,
)
from services.accounting_spreadsheet_export_service import (
    ClientLedgerExporter,
    _piece_number_from_text,
)
from services.acompleter_readable_service import (
    _is_human_decided,
    read_readable_decisions,
)
from services.email_inbox_bridge import EmailAttachment, EmailMessage
from services.gmail_imap_sync_service import (
    GmailImapSyncService,
    MISSING_PDF_INVOICES,
)
from services.sqlite_accounting_store import SqliteAccountingStore
from services.tier_reference_service import (
    ROOT_TIER_REFERENCE_PATH,
    TierReferenceService,
)

TENANT = "APD"


class FakeMailClient:
    def __init__(self, messages: dict[str, list[EmailMessage]]) -> None:
        self._messages = messages
        self.moves: list[tuple[str, str, str]] = []

    def ensure_folder(self, folder: str) -> None:
        return None

    def list_messages(self, folder: str) -> list[EmailMessage]:
        return list(self._messages.get(folder, []))

    def move_message(
        self, message_id: str, source_folder: str, target_folder: str
    ) -> None:
        self.moves.append((message_id, source_folder, target_folder))


def make_store(tmp_path: Path) -> SqliteAccountingStore:
    return SqliteAccountingStore(tmp_path / "accounting.sqlite3")


def install_temp_tiers(tmp_path: Path, store: SqliteAccountingStore) -> TierReferenceService:
    memory = AccountingMemoryLearningService(tmp_path / "rules.json")
    tiers = TierReferenceService(
        tmp_path / "REFERENTIEL_TIERS_APD.xlsx",
        store=store,
        memory=memory,
        tenant_id=TENANT,
    )
    tiers.bootstrap()
    return tiers


# -- 1. Gmail IMAP sync -----------------------------------------------------


def test_gmail_sync_stages_attachments_and_attaches_missing_pdfs(tmp_path):
    store = make_store(tmp_path)
    install_temp_tiers(tmp_path, store)
    root = tmp_path / "document_flow"
    tenant_dir = root / TENANT
    (tenant_dir / "00_INBOX").mkdir(parents=True)
    (tenant_dir / "03_VALIDE_PRET_FEC" / "VENTES_CLIENTS").mkdir(parents=True)
    (tenant_dir / "04_EXPORT_COMPTA" / TENANT).mkdir(parents=True)
    # A Yucel opening statement used as fallback for the 6 missing invoices.
    fallback = tenant_dir / "RELEVE_OUVERTURE_CARROSSERIE_YUCEL_20261006.pdf"
    fallback.write_bytes(b"%PDF-1.4 fallback")
    # A real Solta invoice PDF in the inbox.
    solta_pdf = tenant_dir / "00_INBOX" / "2601616_SOLTA.pdf"
    solta_pdf.write_bytes(b"%PDF-1.4 solta")
    for invoice, line_id, _side in MISSING_PDF_INVOICES:
        store.force_upsert_document_line(
            TENANT,
            line_id,
            {
                "operation_date": "2025-05-31",
                "label": f"Facture {invoice}",
                "counterparty": "CARROSSERIE_YUCEL"
                if invoice.startswith("FA")
                else "SOLTA_LR_COLOR",
                "invoice_id": invoice,
                "category": DocumentCategory.VENTES_CLIENTS.value
                if invoice.startswith("FA")
                else DocumentCategory.ACHATS_MARCHANDISES.value,
                "document_type": "CUSTOMER_INVOICE"
                if invoice.startswith("FA")
                else "FACTURE",
                "amount_ttc": "100.00",
                "status": "VALIDE",
            },
        )
    message = EmailMessage(
        message_id="42",
        folder="INBOX",
        subject="Facture Solta",
        sender="peinturediffusion@hotmail.fr",
        attachments=(
            EmailAttachment("2601616_SOLTA.pdf", b"%PDF-1.4 solta"),
            EmailAttachment("releve.ofx", b"OFXHEADER:100"),
        ),
    )
    client = FakeMailClient(
        {
            "INBOX": [message],
            "[Gmail]/Spam": [],
            "APD/00_INBOX": [],
        }
    )
    service = GmailImapSyncService(
        TENANT,
        store=store,
        root=root,
        client=client,
        enabled=True,
    )
    report = service.sync()
    assert report.connected is True
    assert report.messages_scanned == 1
    assert report.attachments_staged == 2
    assert "2601616" in report.pdf_attached
    assert len(report.pdf_attached) == 7
    assert not report.pdf_missing
    assert report.labels_synced == 1
    assert client.moves == [("42", "INBOX", "APD/LISTES_TRAITEES")]
    solta_line = store.get_line(TENANT, "PURCHASE-2601616")
    assert solta_line is not None
    assert solta_line["piece_pdf"].startswith("PIECES_JOINTES/ACHATS/")
    assert (tenant_dir / "04_EXPORT_COMPTA" / TENANT / solta_line["piece_pdf"]).is_file()


def test_gmail_sync_is_skipped_without_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("APD_INVOICE_EMAIL", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    monkeypatch.delenv("DSH_MAIL_USER", raising=False)
    monkeypatch.delenv("DSH_MAIL_PASSWORD", raising=False)
    service = GmailImapSyncService(
        TENANT, root=tmp_path / "document_flow", enabled=False
    )
    report = service.sync()
    assert report.connected is False
    assert report.skipped == ("GMAIL_CREDENTIALS_MISSING",)


# -- 2. human decisions -----------------------------------------------------


def test_human_decision_detected_from_alias_column():
    values = {
        "Catégorie Proposée": "DIVERS_A_CLASSER",
        "Catégorie IA": "DIVERS_A_CLASSER",
        "Action / Décision": "Choisir un rattachement ALIAS",
        "Action IA": "Choisir un rattachement ALIAS",
        "Rattacher comme ALIAS de (ou NOUVEAU_TIERS)": "sonia ATIA",
        "Fournisseur / Tiers": "EI - ATIA SONIA / TALENT&CO",
    }
    assert _is_human_decided(values) is True


def test_human_decision_detected_from_action_keyword():
    values = {
        "Catégorie Proposée": "DIVERS_A_CLASSER",
        "Catégorie IA": "DIVERS_A_CLASSER",
        "Action / Décision": "Frais Généraux / Informatique exercice 2026",
        "Action IA": "Choisir Frais Généraux / Informatique-SaaS",
        "Rattacher comme ALIAS de (ou NOUVEAU_TIERS)": "",
        "Fournisseur / Tiers": "OpenRouter, Inc",
    }
    assert _is_human_decided(values) is True


def test_human_decision_detected_from_authorized_tier(tmp_path):
    store = make_store(tmp_path)
    tiers = install_temp_tiers(tmp_path, store)
    values = {
        "Catégorie Proposée": "DIVERS_A_CLASSER",
        "Catégorie IA": "DIVERS_A_CLASSER",
        "Action / Décision": "Choisir un rattachement ALIAS",
        "Action IA": "Choisir un rattachement ALIAS",
        "Rattacher comme ALIAS de (ou NOUVEAU_TIERS)": "",
        "Fournisseur / Tiers": "SONIA ATIA",
    }
    assert _is_human_decided(values, tiers) is True


def test_read_readable_decisions_merges_mis_a_jour_and_deletes_it(tmp_path):
    from openpyxl import Workbook

    directory = tmp_path / "02_A_COMPLETER"
    directory.mkdir()
    primary = directory / "_A_COMPLETER.xlsx"
    fallback = directory / "_A_COMPLETER_MIS_A_JOUR__5.xlsx"
    headers = [
        "Date Pièce",
        "Fournisseur / Tiers",
        "N° Pièce",
        "TTC",
        "Catégorie Proposée",
        "Pourquoi c'est en attente & Conseil IA",
        "Action / Décision",
        "Source de paiement",
        "Compte de contrepartie",
        "Exercice cible",
        "Mois décalé",
        "Report",
        "Rattacher comme ALIAS de (ou NOUVEAU_TIERS)",
        "Pièce PDF (Clic)",
        "Catégorie IA",
        "Action IA",
        "SHA-256",
        "Nom Fichier",
    ]
    for path, alias in ((primary, ""), (fallback, "sonia ATIA")):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(headers)
        sheet.append(
            [
                "2026-10-19",
                "EI - ATIA SONIA / TALENT&CO",
                "F-2026-037",
                1800,
                "DIVERS_A_CLASSER",
                "Honoraires",
                "Choisir un rattachement ALIAS",
                "BANQUE_PRO",
                "622600",
                "2026",
                "NON",
                "NON",
                alias,
                None,
                "DIVERS_A_CLASSER",
                "Choisir un rattachement ALIAS",
                "abc123",
                "F-2026-037.pdf",
            ]
        )
        workbook.save(path)
        workbook.close()
    decisions = read_readable_decisions(primary)
    assert len(decisions) == 1
    assert decisions[0].status == "VALIDE"
    assert decisions[0].tier_alias_target == "sonia ATIA"
    assert not fallback.exists()


# -- 3. native PDF hyperlinks ----------------------------------------------


def test_ledger_uses_native_relative_hyperlink(tmp_path):
    store = make_store(tmp_path)
    install_temp_tiers(tmp_path, store)
    store.force_upsert_document_line(
        TENANT,
        "SALE-FA16423",
        {
            "operation_date": "2026-01-31",
            "label": "Facture FA16423 Carrosserie Yucell",
            "counterparty": "Carrosserie Yucell",
            "invoice_id": "FA16423",
            "category": DocumentCategory.VENTES_CLIENTS.value,
            "document_type": "CUSTOMER_INVOICE",
            "amount_ht": "792.30",
            "amount_tva": "158.46",
            "amount_ttc": "950.76",
            "status": "VALIDE",
            "reconciliation_status": "PAYEE_RAPPROCHEE",
            "reconciliation_date": "2026-07-03",
            "reconciliation_reference": "VIREMENT 20260703141545180335145058189420260703",
            "reconciliation_amount": "950.76",
            "balance_due": "0.00",
        },
    )
    store.add_line(
        TENANT,
        "OFX-20260703141545180335145058189420260703",
        {
            "operation_date": "2026-07-03",
            "label": "VIR INST SARL CARROSSERIE YUCE",
            "counterparty": "SARL CARROSSERIE YUCE",
            "credit_amount": "950.76",
            "category": DocumentCategory.VENTES_CLIENTS.value,
            "document_type": "OFX",
            "status": "PENDING",
        },
    )
    export_dir = tmp_path / "exports"
    piece = (
        export_dir
        / TENANT
        / "PIECES_JOINTES"
        / "VENTES"
        / "FA16423_2026-01-31_CARROSSERIE_YUCELL.pdf"
    )
    piece.parent.mkdir(parents=True, exist_ok=True)
    piece.write_bytes(b"%PDF-1.4 test")
    path = ClientLedgerExporter(
        store,
        export_dir=export_dir,
        strict_tiers=True,
        include_matched_pieces=True,
    ).export(TENANT)
    sheet = load_workbook(path)["CARROSSERIE_YUCEL"]
    payment = next(
        row for row in sheet.iter_rows(min_row=2) if row[1].value == "PAIEMENT"
    )
    assert payment[13].hyperlink is not None
    assert (
        "PIECES_JOINTES/VENTES/FA16423_2026-01-31_CARROSSERIE_YUCELL.pdf"
        in str(payment[13].hyperlink.target)
    )
    assert not str(payment[9].value).startswith("=IFERROR(HYPERLINK(")


# -- 4. Boxtal CB date token ------------------------------------------------


def test_cb_fact_date_is_not_an_invoice_number():
    assert _piece_number_from_text("CB Boxtal FACT 060326") == ""
    assert _piece_number_from_text("CB Boxtal FACT 060326") != "060326"
    assert _piece_number_from_text("Facture 2600023334 Boxtal") == "2600023334"


# -- 5. bidirectional tier reference ---------------------------------------


def test_tier_reference_merges_root_and_export_workbooks(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    export_path = tmp_path / "export" / "REFERENTIEL_TIERS_APD.xlsx"
    root_path = tmp_path / "root" / "REFERENTIEL_TIERS_APD.xlsx"
    export_path.parent.mkdir(parents=True)
    root_path.parent.mkdir(parents=True)
    service = TierReferenceService(
        export_path, store=store, tenant_id=TENANT
    )
    service.bootstrap()
    # The director adds a tier only in the root workbook.
    from openpyxl import load_workbook

    workbook = load_workbook(export_path)
    sheet = workbook.active
    sheet.append(
        [
            "MANUAL_TIER",
            "MANUAL ALIAS",
            None,
            None,
            None,
            "FOURNISSEUR",
            "ACHATS_MARCHANDISES",
            None,
            "VIREMENT",
            "AUTORISE",
        ]
    )
    workbook.save(root_path)
    workbook.close()
    monkeypatch.setattr(
        "services.tier_reference_service.ROOT_TIER_REFERENCE_PATH", root_path
    )
    reloaded = TierReferenceService(export_path, store=store, tenant_id=TENANT)
    reloaded.bootstrap()
    assert reloaded.canonical_name("MANUAL ALIAS") == "MANUAL_TIER"


# -- 6. duplicate cleanup and BOM-free launcher ----------------------------


def test_cleanup_duplicates_removes_stale_workbooks(tmp_path):
    from scripts.cleanup_p27ter_duplicates import main

    tenant_dir = tmp_path / "workspace" / "document_flow" / TENANT
    tenant_dir.mkdir(parents=True)
    for name in (
        "GRAND_LIVRE_default_OLD.xlsx",
        "TABLEAU_MERE_PIECES_COMPTABLES__1.xlsx",
        "TABLEAU_MERE_PIECES_COMPTABLES__5.xlsx",
        "_A_COMPLETER_MIS_A_JOUR__5.xlsx",
        "KEEP_ME.xlsx",
    ):
        (tenant_dir / name).write_bytes(b"x")
    assert main(["--root", str(tmp_path), "--tenant", TENANT]) == 0
    assert not (tenant_dir / "GRAND_LIVRE_default_OLD.xlsx").exists()
    assert not (tenant_dir / "TABLEAU_MERE_PIECES_COMPTABLES__1.xlsx").exists()
    assert not (tenant_dir / "TABLEAU_MERE_PIECES_COMPTABLES__5.xlsx").exists()
    assert not (tenant_dir / "_A_COMPLETER_MIS_A_JOUR__5.xlsx").exists()
    assert (tenant_dir / "KEEP_ME.xlsx").exists()


def test_launcher_has_no_bom():
    path = Path(__file__).resolve().parents[1] / "Lancer_Cockpit_APD.bat"
    data = path.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")
    assert data.startswith(b"@echo off")
