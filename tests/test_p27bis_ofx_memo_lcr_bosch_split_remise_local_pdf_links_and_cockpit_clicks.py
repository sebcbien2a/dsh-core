"""P27-bis - OFX MEMO, Bosch LCR, split remises, local PDF links and cockpit."""

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
    MasterTableExporter,
)
from services.apd_control_panel import dashboard_html
from services.ofx_bank_statement_parser import parse_ofx_statement
from services.onedrive_accounting_bridge_service import (
    OneDriveAccountingBridge,
    onedrive_bridge_enabled,
)
from services.sqlite_accounting_store import SqliteAccountingStore
from services.tier_reference_service import TierReferenceService

TENANT = "APD"


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


def add_invoice(
    store: SqliteAccountingStore,
    invoice_id: str,
    *,
    day: str,
    ttc: str,
    counterparty: str,
    category: DocumentCategory = DocumentCategory.VENTES_CLIENTS,
) -> None:
    line = {
        "operation_date": day,
        "label": f"Facture {invoice_id} {counterparty}",
        "counterparty": counterparty,
        "invoice_id": invoice_id,
        "category": category.value,
        "document_type": "CUSTOMER_INVOICE"
        if category is DocumentCategory.VENTES_CLIENTS
        else "FACTURE",
        "amount_ht": str((Decimal(ttc) / Decimal("1.2")).quantize(Decimal("0.01"))),
        "amount_tva": str(
            (
                Decimal(ttc)
                - (Decimal(ttc) / Decimal("1.2")).quantize(Decimal("0.01"))
            ).quantize(Decimal("0.01"))
        ),
        "amount_ttc": ttc,
        "status": "VALIDE",
    }
    store.upsert_document_line(TENANT, f"SALE-{invoice_id}", line)


def add_bank(
    store: SqliteAccountingStore,
    line_id: str,
    *,
    day: str,
    label: str,
    debit: str | None = None,
    credit: str | None = None,
    counterparty: str | None = None,
) -> None:
    store.add_line(
        TENANT,
        line_id,
        {
            "operation_date": day,
            "label": label,
            "counterparty": counterparty or label,
            "debit_amount": debit,
            "credit_amount": credit,
            "category": DocumentCategory.BANQUE_FINANCE.value,
            "document_type": "OFX",
            "status": "PENDING",
        },
    )


# -- 1. OFX NAME + MEMO -----------------------------------------------------


def test_ofx_concatenates_name_and_memo_and_extracts_lcr_supplier():
    content = """OFXHEADER:100
<OFX>
<BANKMSGSRSV1>
<STMTTRNRS>
<STMTRS>
<CURDEF>EUR
<BANKACCTFROM><ACCTID>12345678901</BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20260310
<TRNAMT>-410,67
<FITID>FIT-LCR-BOSCH
<NAME>LCR 806255 RLV 00000385
<MEMO>BOSCH FRANCE SA
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20260311
<TRNAMT>-100,00
<FITID>FIT-LCR-EMAUX
<NAME>LCR 806256 RLV 00000386
<MEMO>SAS LES EMAUX
</STMTTRN>
</BANKTRANLIST>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>
"""
    statement = parse_ofx_statement(content)
    first, second = statement.transactions
    assert first.label == "LCR 806255 RLV 00000385 | BOSCH FRANCE SA"
    assert first.memo == "BOSCH FRANCE SA"
    assert first.counterparty == "BOSCH FRANCE SA"
    assert second.label == "LCR 806256 RLV 00000386 | SAS LES EMAUX"
    assert second.counterparty == "SAS LES EMAUX"


def test_ofx_keeps_single_label_when_name_equals_memo():
    content = """OFXHEADER:100
<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>EUR
<BANKACCTFROM><ACCTID>12345678901</BANKACCTFROM>
<BANKTRANLIST><STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20260310
<TRNAMT>-44,14
<FITID>FIT-VIR
<NAME>VIR SEPA NORMANDIE FILTRE FACTOR
<MEMO>VIR SEPA NORMANDIE FILTRE FACTOR
</STMTTRN></BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
"""
    transaction = parse_ofx_statement(content).transactions[0]
    assert transaction.label == "VIR SEPA NORMANDIE FILTRE FACTOR"
    assert transaction.counterparty == "NORMANDIE FILTRE FACTOR"


# -- 2. Bosch / new suppliers ----------------------------------------------


def test_bosch_sia_unified_and_new_suppliers(tmp_path):
    store = make_store(tmp_path)
    tiers = install_temp_tiers(tmp_path, store)
    for alias in (
        "BOSCH",
        "BOSCH SA",
        "BOSCH FRANCE SA",
        "BOSCH SA FRANCE",
        "ROBERT BOSCH",
        "ROBERT BOSCH FRANCE",
        "SIA",
        "SIA ABRASIVES",
        "SIA ABRASIVES FRANCE",
    ):
        assert tiers.canonical_name(alias) == "BOSCH_SIA"
    assert tiers.role_for("BOSCH FRANCE SA") == "FOURNISSEUR"
    assert tiers.resolve("BOSCH FRANCE SA").category == "ACHATS_MARCHANDISES"
    assert tiers.canonical_name("SAS LES EMAUX") == "SAS_LES_EMAUX"
    assert tiers.canonical_name("LES EMAUX") == "SAS_LES_EMAUX"
    assert tiers.canonical_name("ANG PEINTURE") == "ANG_PEINTURE"
    assert tiers.canonical_name("ANG PEINTURES") == "ANG_PEINTURE"
    assert tiers.canonical_name("SOCIETE DISTRIB") == "SOCIETE_DISTRIB"
    assert tiers.canonical_name("NORMANDIE FILTRE FACTOR") == "NORMANDIE_FILTRE"
    assert tiers.canonical_name("NORMANDIE FILTRE") == "NORMANDIE_FILTRE"


# -- 3. split multi-client LCR remise --------------------------------------


def test_multi_client_remise_is_split_per_invoice(tmp_path):
    store = make_store(tmp_path)
    install_temp_tiers(tmp_path, store)
    add_invoice(store, "FA16421", day="2026-01-30", ttc="4293.23", counterparty="CHOLLET")
    add_invoice(store, "FA16437", day="2026-02-28", ttc="2004.17", counterparty="CHOLLET")
    add_invoice(store, "FA16463", day="2026-03-31", ttc="129.60", counterparty="CARROSSERIE RIGAL")
    total = Decimal("4293.23") + Decimal("2004.17") + Decimal("129.60")
    add_bank(
        store,
        "OFX-REMISE-91787",
        day="2026-03-10",
        label="REMISE ENCAISSEMENT N 91787",
        credit=str(total),
        counterparty="CLIENTS LCR",
    )
    master = MasterTableExporter(
        store, root=tmp_path, export_dir=tmp_path / "exports"
    ).export(TENANT)
    sheet = load_workbook(master.banque_tva_path).active
    rows = list(sheet.iter_rows(min_row=2, values_only=True))
    split = [row for row in rows if row[2] == "LCR_ENCAISSEMENT_LOT"]
    assert len(split) == 3
    clients = {str(row[1]) for row in split}
    assert "CHOLLET" in clients
    assert any("RIGAL" in client for client in clients)
    assert sum(Decimal(str(row[5])) for row in split) == total
    labels = " ".join(str(row[3]) for row in split)
    assert "REMISE ENCAISSEMENT N 91787" in labels
    assert "FA16421" in labels and "FA16437" in labels and "FA16463" in labels


# -- 4. local PDF HYPERLINK formula ----------------------------------------


def test_pdf_links_use_local_hyperlink_formula(tmp_path):
    store = make_store(tmp_path)
    install_temp_tiers(tmp_path, store)
    add_invoice(
        store,
        "FA16423",
        day="2026-01-31",
        ttc="950.76",
        counterparty="Carrosserie Yucell",
        category=DocumentCategory.VENTES_CLIENTS,
    )
    store.force_upsert_document_line(
        TENANT,
        "SALE-FA16423",
        {
            **store.get_line(TENANT, "SALE-FA16423"),
            "reconciliation_status": "PAYEE_RAPPROCHEE",
            "reconciliation_date": "2026-07-03",
            "reconciliation_reference": "VIREMENT 20260703141545180335145058189420260703",
            "reconciliation_amount": "950.76",
            "balance_due": "0.00",
        },
    )
    add_bank(
        store,
        "OFX-20260703141545180335145058189420260703",
        day="2026-07-03",
        label="VIR INST SARL CARROSSERIE YUCE",
        credit="950.76",
        counterparty="SARL CARROSSERIE YUCE",
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
    formula = str(payment[9].value)
    assert formula.startswith("=IFERROR(HYPERLINK(")
    assert "CELL(\"filename\",A1)" in formula
    assert "PIECES_JOINTES\\VENTES\\FA16423_2026-01-31_CARROSSERIE_YUCELL.pdf" in formula
    assert payment[9].hyperlink is None


# -- 5. OneDrive bridge disabled -------------------------------------------


def test_onedrive_bridge_can_be_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("APD_ONEDRIVE_BRIDGE", "0")
    assert onedrive_bridge_enabled() is False
    bridge = OneDriveAccountingBridge(
        TENANT, root=tmp_path / "document_flow", onedrive_dir=tmp_path
    )
    assert bridge.shared_dir is None
    report = bridge.sync()
    assert report.skipped == ("ONEDRIVE_BRIDGE_DISABLED",)


# -- 6. cockpit buttons use data attributes and delegation -----------------


def test_cockpit_uses_data_doc_id_and_event_delegation():
    html = dashboard_html(pending_questions=[])
    assert "data-doc-id=" in html
    assert 'data-action="validate"' in html
    assert "document.addEventListener('click'" in html
    assert "onclick=\"validerPiece" not in html
    assert "P27" in html
    for label in (
        "Étape 1",
        "Étape 2",
        "Étape 3",
        "Étape 4",
        "Statut général",
        "Questions en attente",
        "Tout exécuter",
    ):
        assert label in html
