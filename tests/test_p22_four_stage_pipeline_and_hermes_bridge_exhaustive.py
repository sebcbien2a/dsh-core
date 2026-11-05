"""P22 - Exhaustive offline tests: evidence rules, 4-stage pipeline, readable
workbook, OFX supplier reconciliation, balance simulation and Hermès bridge.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook

from schemas.folder_state_schema import DocumentCategory, DocumentStatus, FolderState
from services.acompleter_readable_service import READABLE_HEADERS
from services.acompleter_workbook_service import AcompleterWorkbook, derive_document_id
from services.apd_evidence_rules import (
    BON_DE_COMMANDE_NON_COMPTABLE,
    evaluate_evidence,
)
from services.apd_four_stage_pipeline import (
    ApdFourStagePipeline,
    DEFAULT_YEAR,
    STAGE_NAMES,
)
from services.balance_simulation_service import (
    BALANCE_FILENAME,
    BalanceSimulationService,
)
from services.folder_tree_service import ensure_tree
from services.hermes_accounting_bridge import HermesAccountingBridge
from services.ofx_bank_statement_parser import parse_ofx_statement
from services.sqlite_accounting_store import SqliteAccountingStore

TENANT = "APD"
YEAR = 2026


def make_text_pdf(path: Path, text: str) -> Path:
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(fitz.Rect(40, 40, 560, 800), text, fontsize=9)
    document.save(path)
    document.close()
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def store_for(tmp_path: Path) -> SqliteAccountingStore:
    return SqliteAccountingStore(tmp_path / "accounting.sqlite3")


def ofx_diffam() -> str:
    return """OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:UTF-8
CHARSET:UTF-8
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<BANKMSGSRSV1>
<STMTTRNRS>
<TRNUID>1
<STATUS><CODE>0<SEVERITY>INFO</STATUS>
<STMTRS>
<CURDEF>EUR
<BANKACCTFROM><BANKID>14505<ACCTID>08001409859</BANKACCTFROM>
<BANKTRANLIST>
<DTSTART>20260701
<DTEND>20260731
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20260727
<TRNAMT>-651,98
<FITID>DIFFAM-270726
<NAME>CB DIFFAM FACT 240726
<MEMO>CB DIFFAM FACT 240726
</STMTTRN>
</BANKTRANLIST>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>
"""


# -- evidence rules ----------------------------------------------------------


def free_text() -> str:
    return "\n".join(
        [
            "FREE TELECOM",
            "Facture Freebox n° 1447097480 du 02 Février 2026",
            "Total HT 33,33 EUR",
            "Total TVA 6,66 EUR",
            "Total TTC 39,99 EUR",
            "Prélèvement SEPA",
        ]
    )


def ila_text() -> str:
    return "\n".join(
        [
            "CRISOL COLOR, S.L.",
            "www.paint-car.com",
            "FACTURE",
            "DATE FACTURE 14/09/2026",
            "PI/51",
            "FACTURE HT",
            "3.712,12",
            "Operación intracomunitaria exenta",
            "TOTAL T.V.A.",
            "3.712,12",
            "NET A PAYER",
        ]
    )


def chronopost_text() -> str:
    return "\n".join(
        [
            "CHRONOPOST SAS",
            "N° Facture : W260707W1957907O306043910",
            "Date : 07/07/2026",
            "Total HT 11,58 EUR",
            "TVA 20,0",
            "Net à payer 13,9 €",
        ]
    )


def cholet_text() -> str:
    return "\n".join(
        [
            "Autre S.A.S. Assistance Peintures Diffusion",
            "CHOLLET",
            "Avoir N° AV15101",
            "Date 16/01/2026",
            "Total HT 2 203,91",
            "Total TVA 440,78",
            "Total TTC 2 644,69",
            "NET A PAYER 2 644,69",
        ]
    )


def ema_text() -> str:
    return "\n".join(
        [
            "EM'A Les Emaux de l'Atlantique",
            "SAS ASSISTANCE PEINTURES DIFFUSION",
            "Facture N°262105",
            "Date d'émission : 30/06/2026",
            "Motant H.T",
            "Montant TVA",
            "TVA",
            " 52,81",
            "10,56 EUR",
            " 20,00%",
            "Total brut H.T.",
            " 52,81 €",
            " 63,37 €",
            "NET A PAYER",
            "Mode de règlement : LCR Directe",
        ]
    )


def diffam_text() -> str:
    return "\n".join(
        [
            "HABITAT AUTOMATISME",
            "FACTURE",
            "Notre Référence :FAC-J2718",
            "Date :27/07/2026",
            "Règlement 24/07/2026 Carte Bancaire",
            "Montant HT :",
            "543.31€",
            "Montant TVA :",
            "108.67€",
            "651.98€",
            "Montant TTC:",
        ]
    )


@pytest.mark.parametrize(
    ("filename", "text", "supplier", "number", "category", "status"),
    [
        (
            "Facture_Free_202602_30827543_1447097480.pdf",
            free_text(),
            "FREE TELECOM / FREE MOBILE",
            "1447097480",
            DocumentCategory.FRAIS_GENERAUX,
            DocumentStatus.VALIDE,
        ),
        (
            "ILA193A1.pdf",
            ila_text(),
            "PAINTCAR",
            "ILA193A1",
            DocumentCategory.ACHATS_MARCHANDISES,
            DocumentStatus.VALIDE,
        ),
        (
            "W260707W1957907O306043910.pdf",
            chronopost_text(),
            "CHRONOPOST",
            "W260707W1957907O306043910",
            DocumentCategory.TRANSPORTS_LOGISTIQUE,
            DocumentStatus.VALIDE,
        ),
        (
            "avoir cholet 2026.pdf",
            cholet_text(),
            "CHOLLET",
            "AVOIR_CHOLET_2026",
            DocumentCategory.VENTES_CLIENTS,
            DocumentStatus.VALIDE,
        ),
        (
            "Facture client - 262105.pdf",
            ema_text(),
            "EM'A Les Emaux de l'Atlantique",
            "262105",
            DocumentCategory.ACHATS_MARCHANDISES,
            DocumentStatus.VALIDE,
        ),
        (
            "FAC-J2718_SAS-DIFFAM.pdf",
            diffam_text(),
            "HABITAT AUTOMATISME / SAS DIFFAM",
            "FAC-J2718",
            DocumentCategory.FRAIS_GENERAUX,
            DocumentStatus.VALIDE,
        ),
    ],
)
def test_evidence_rules_validate_obvious_pieces(
    tmp_path, filename, text, supplier, number, category, status
):
    pdf = make_text_pdf(tmp_path / filename, text)
    decision = evaluate_evidence(pdf)
    assert decision is not None
    assert decision.supplier == supplier
    assert decision.document_number == number
    assert decision.category is category
    assert decision.status is status
    assert decision.reason
    assert decision.action


def test_free_rule_extracts_amounts_and_sepa(tmp_path):
    pdf = make_text_pdf(
        tmp_path / "Facture_Free_202602_30827543_1447097480.pdf", free_text()
    )
    decision = evaluate_evidence(pdf)
    assert decision is not None
    assert decision.amount_ht == Decimal("33.33")
    assert decision.amount_tva == Decimal("6.66")
    assert decision.amount_ttc == Decimal("39.99")
    assert decision.expense_subcategory == "TELECOM_INTERNET"
    assert decision.payment_method == "PRELEVEMENT_SEPA"


def test_paintcar_ila_is_intracom_without_vat(tmp_path):
    pdf = make_text_pdf(tmp_path / "ILA193A1.pdf", ila_text())
    decision = evaluate_evidence(pdf)
    assert decision is not None
    assert decision.amount_ht == Decimal("3712.12")
    assert decision.amount_tva == Decimal("0.00")
    assert decision.amount_ttc == Decimal("3712.12")


def test_ema_is_purchase_not_sale_and_uses_supplier_lcr(tmp_path):
    pdf = make_text_pdf(tmp_path / "Facture client - 262105.pdf", ema_text())
    decision = evaluate_evidence(pdf)
    assert decision is not None
    assert decision.category is DocumentCategory.ACHATS_MARCHANDISES
    assert decision.payment_method == "LCR_FOURNISSEUR_DEBIT"
    assert decision.amount_ttc == Decimal("63.37")


def test_bosch_order_conf_is_non_accountable(tmp_path):
    pdf = make_text_pdf(
        tmp_path / "Y16V_SD_ORDER_CONF 20260916 152129 4086518725.pdf",
        "ROBERT BOSCH (France) S.A.S\nConfirmation de commande\n"
        "N° Commande 43978631\nDate 16.09.2026\nTotal 774,60 EUR",
    )
    decision = evaluate_evidence(pdf)
    assert decision is not None
    assert decision.document_type == BON_DE_COMMANDE_NON_COMPTABLE
    assert decision.is_accountable is False
    assert decision.status is DocumentStatus.VALIDE
    assert "non comptable" in decision.action.casefold()


def mise_sur_orbite_text(number: str, day: str, ht: str, tva: str, ttc: str) -> str:
    return "\n".join(
        [
            "MISE SUR ORBITE",
            "19 rue des petits margois",
            "36400 LA CHATRE - France",
            "SIRET 82013071400034",
            "TVA FR55820130714",
            "SAS ASSISTANCE PENTURE DIFFUSION",
            f"Facture #{number} du {day}",
            "Hébergement site Internet et emails",
            f"Total HT {ht} €",
            "TVA 20.00%",
            f"{tva} €",
            f"Total TTC {ttc} €",
            "Mode de paiement : par carte bancaire ou par virement",
        ]
    )


@pytest.mark.parametrize(
    ("number", "day", "ht", "tva", "ttc"),
    [
        ("19544", "02/10/2026", "161,00", "32,20", "193,20"),
        ("18417", "22/05/2026", "51,00", "10,20", "61,20"),
    ],
)
def test_mise_sur_orbite_supplier_rule_validates_web_hosting_invoices(
    tmp_path, number, day, ht, tva, ttc
):
    pdf = make_text_pdf(
        tmp_path / f"{number}.pdf", mise_sur_orbite_text(number, day, ht, tva, ttc)
    )
    decision = evaluate_evidence(pdf)
    assert decision is not None
    assert decision.status is DocumentStatus.VALIDE
    assert decision.supplier == "MISE SUR ORBITE"
    assert decision.category is DocumentCategory.FRAIS_GENERAUX
    assert decision.expense_subcategory == "INFORMATIQUE_WEB"
    assert decision.document_number == number
    assert decision.amount_ht == Decimal(ht.replace(",", "."))
    assert decision.amount_tva == Decimal(tva.replace(",", "."))
    assert decision.amount_ttc == Decimal(ttc.replace(",", "."))
    assert decision.rule == "MISE_SUR_ORBITE_WEB_HOSTING"


def test_director_arbitration_validates_holding_invoice(tmp_path):
    holding = make_text_pdf(tmp_path / "facture creation holding.pdf", "")
    decision = evaluate_evidence(holding)
    assert decision is not None
    assert decision.status is DocumentStatus.VALIDE
    assert decision.supplier == "YASMIN CONSULTING"
    assert decision.category is DocumentCategory.FRAIS_GENERAUX
    assert decision.expense_subcategory == "HONORAIRES_JURIDIQUES"
    assert decision.amount_ht == Decimal("850.00")
    assert decision.amount_tva == Decimal("0.00")
    assert decision.amount_ttc == Decimal("850.00")
    assert decision.rule == "YASMIN_HOLDING_CREATION"
    assert "création de holding" in decision.reason.casefold()


def test_director_arbitration_validates_catroux_transport_invoice(tmp_path):
    catroux = make_text_pdf(
        tmp_path / "Facture 423 du 31_05_2025_01.pdf",
        "TRANSPORTS CATROUX\nFACTURE 423\nDate 31/05/2025\n"
        "Montant H.T. 145,00\nT.V.A. 29,00\nMontant T.T.C. 174,00\n",
    )
    decision = evaluate_evidence(catroux)
    assert decision is not None
    assert decision.status is DocumentStatus.VALIDE
    assert decision.supplier == "TRANSPORTS CATROUX"
    assert decision.category is DocumentCategory.TRANSPORTS_LOGISTIQUE
    assert decision.document_number == "423"
    assert decision.amount_ht == Decimal("145.00")
    assert decision.amount_tva == Decimal("29.00")
    assert decision.amount_ttc == Decimal("174.00")
    assert decision.already_paid is True
    assert decision.rule == "CATROUX_TRANSPORT_423"


def test_openrouter_remains_pending_with_business_explanation(tmp_path):
    saas = make_text_pdf(
        tmp_path / "Invoice-GENMCYCQ-0001.pdf",
        "Invoice GENMCYCQ-0001\nOpenRouter, Inc\nTotal $10.85 USD\n",
    )
    saas_decision = evaluate_evidence(saas)
    assert saas_decision is not None
    assert saas_decision.status is DocumentStatus.EN_ATTENTE
    assert "anglais" in saas_decision.reason
    assert "SaaS" in saas_decision.reason


# -- stage 1 / readable workbook -------------------------------------------


def prepare_flow(tmp_path: Path) -> tuple[Any, Path, SqliteAccountingStore]:
    tree = ensure_tree(TENANT, tmp_path)
    store = store_for(tmp_path)
    return tree, tmp_path / TENANT, store


def write_pending(tree, name: str, text: str) -> Path:
    return make_text_pdf(tree.state_dir(FolderState.A_COMPLETER) / name, text)


def test_stage1_validates_evidence_moves_files_and_updates_readable_workbook(tmp_path):
    tree, tenant_dir, store = prepare_flow(tmp_path)
    free_pdf = write_pending(
        tree,
        "Facture_Free_202602_30827543_1447097480.pdf",
        free_text(),
    )
    holding_pdf = write_pending(tree, "facture creation holding.pdf", "")
    pending_pdf = write_pending(
        tree,
        "Invoice-GENMCYCQ-0001.pdf",
        "Invoice GENMCYCQ-0001\nOpenRouter, Inc\nTotal $10.85 USD\n",
    )
    assert holding_pdf.exists()
    free_sha = sha(free_pdf)
    pipeline = ApdFourStagePipeline(
        TENANT,
        store=store,
        root=tmp_path,
        export_dir=tmp_path / "exports",
        year=YEAR,
    )
    report = pipeline.run_stage(1)
    assert report.validated >= 2
    assert report.pending >= 1
    # The source has been moved to the validated category.
    assert not (tree.state_dir(FolderState.A_COMPLETER) / free_pdf.name).exists()
    assert not holding_pdf.exists()
    validated = list(
        (tree.state_dir(FolderState.VALIDE_PRET_FEC) / "FRAIS_GENERAUX").glob(
            "Facture_Free_*.pdf"
        )
    )
    assert validated
    assert list(
        (tree.state_dir(FolderState.VALIDE_PRET_FEC) / "FRAIS_GENERAUX").glob(
            "facture creation holding.pdf"
        )
    )
    line = store.get_line(TENANT, derive_document_id(free_sha))
    assert line is not None
    assert line["status"] == "VALIDE"
    assert line["category"] == "FRAIS_GENERAUX"
    assert line["amount_ttc"] == "39.99"
    assert line["piece_pdf"].startswith("PIECES_JOINTES/ACHATS/")
    # The dedicated readable workbook exists in 02_A_COMPLETER with the exact
    # P22 headers and clickable relative links; validated pieces are excluded.
    readable = tree.state_dir(FolderState.A_COMPLETER) / "_A_COMPLETER.xlsx"
    assert readable.is_file()
    workbook = load_workbook(readable)
    sheet = workbook.active
    assert tuple(cell.value for cell in sheet[1]) == READABLE_HEADERS
    rows = [row for row in sheet.iter_rows(min_row=2, values_only=True)]
    assert any(row[2] == "Invoice-GENMCYCQ-0001.pdf" for row in rows)
    assert not any(row[2] == "facture creation holding.pdf" for row in rows)
    link_cells = [row[-1] for row in rows if row[-1]]
    assert link_cells
    for link in link_cells:
        assert ":" not in link
        assert not link.startswith("/")
    workbook.close()
    # The legacy workbook also materialises P22 columns and hides the UUID/hash.
    legacy = load_workbook(tenant_dir / "_A_COMPLETER.xlsx")
    legacy_sheet = legacy.active
    assert legacy_sheet.column_dimensions["A"].hidden is True
    assert legacy_sheet.column_dimensions["B"].hidden is True
    assert legacy_sheet.cell(row=1, column=19).value == "Date Pièce"
    legacy.close()
    assert pending_pdf.exists()


def test_stage1_writes_real_business_reason_for_openrouter_pending(tmp_path):
    tree, _tenant_dir, store = prepare_flow(tmp_path)
    write_pending(
        tree,
        "Invoice-GENMCYCQ-0001.pdf",
        "Invoice GENMCYCQ-0001\nOpenRouter, Inc\nTotal $10.85 USD\n",
    )
    pipeline = ApdFourStagePipeline(
        TENANT, store=store, root=tmp_path, export_dir=tmp_path / "exports"
    )
    pipeline.run_stage(1)
    readable = tree.state_dir(FolderState.A_COMPLETER) / "_A_COMPLETER.xlsx"
    sheet = load_workbook(readable).active
    reasons = [row[8] for row in sheet.iter_rows(min_row=2, values_only=True)]
    assert any("anglais" in str(reason) for reason in reasons)
    assert any("SaaS" in str(reason) for reason in reasons)
    assert all(reason != "numero_piece, categorie" for reason in reasons)


# -- stage 2 / OFX reconciliation -------------------------------------------


def test_stage2_matches_diffam_card_debit_and_marks_invoice_paid(tmp_path):
    tree, _tenant_dir, store = prepare_flow(tmp_path)
    pdf = write_pending(tree, "FAC-J2718_SAS-DIFFAM.pdf", diffam_text())
    digest = sha(pdf)
    pipeline = ApdFourStagePipeline(
        TENANT, store=store, root=tmp_path, export_dir=tmp_path / "exports"
    )
    pipeline.run_stage(1)
    sources = tree.state_dir(FolderState.EXPORT_COMPTA) / "SOURCES_REEL"
    sources.mkdir(parents=True, exist_ok=True)
    (sources / "operations.ofx").write_text(ofx_diffam(), encoding="utf-8")
    report = pipeline.run_stage(2)
    assert report.imported_ofx >= 1
    assert report.reconciled >= 1
    line = store.get_line(TENANT, derive_document_id(digest))
    assert line is not None
    assert line["reconciliation_status"] == "PAYEE_RAPPROCHEE"
    assert line["fitid"] == "DIFFAM-270726"
    assert line["balance_due"] == "0.00"
    statement = parse_ofx_statement(ofx_diffam())
    assert statement.transactions[0].amount == Decimal("-651.98")


# -- stage 4 / balance simulation -------------------------------------------


def test_balance_simulation_computes_result_and_balance_sheet(tmp_path):
    store = store_for(tmp_path)
    common = {"currency": "EUR", "status": "VALIDE"}
    store.upsert_document_line(
        TENANT,
        "SALE-1",
        {
            **common,
            "operation_date": "2026-03-01",
            "label": "FA100",
            "counterparty": "CLIENT A",
            "category": "VENTES_CLIENTS",
            "document_type": "CUSTOMER_INVOICE",
            "amount_ht": "1000.00",
            "amount_tva": "200.00",
            "amount_ttc": "1200.00",
            "balance_due": "1200.00",
            "reconciliation_status": "EN_ATTENTE_REGLEMENT",
        },
    )
    store.upsert_document_line(
        TENANT,
        "BUY-1",
        {
            **common,
            "operation_date": "2026-03-02",
            "label": "F1",
            "counterparty": "FOURNISSEUR A",
            "category": "ACHATS_MARCHANDISES",
            "document_type": "FACTURE",
            "amount_ht": "400.00",
            "amount_tva": "80.00",
            "amount_ttc": "480.00",
            "balance_due": "480.00",
        },
    )
    store.upsert_document_line(
        TENANT,
        "TR-1",
        {
            **common,
            "operation_date": "2026-03-03",
            "label": "CHRONOPOST",
            "counterparty": "CHRONOPOST",
            "category": "TRANSPORTS_LOGISTIQUE",
            "document_type": "FACTURE",
            "amount_ht": "50.00",
            "amount_tva": "10.00",
            "amount_ttc": "60.00",
        },
    )
    store.upsert_document_line(
        TENANT,
        "FG-1",
        {
            **common,
            "operation_date": "2026-03-04",
            "label": "FREE",
            "counterparty": "FREE",
            "category": "FRAIS_GENERAUX",
            "document_type": "FACTURE",
            "amount_ht": "100.00",
            "amount_tva": "20.00",
            "amount_ttc": "120.00",
        },
    )
    store.upsert_document_line(
        TENANT,
        "BANK-1",
        {
            "operation_date": "2026-03-05",
            "label": "VIR CLIENT",
            "counterparty": "CLIENT A",
            "category": "BANQUE_FINANCE",
            "credit_amount": "500.00",
            "status": "PENDING",
        },
    )
    store.upsert_document_line(
        TENANT,
        "BANK-2",
        {
            "operation_date": "2026-03-06",
            "label": "PRLV FOURNISSEUR",
            "counterparty": "FOURNISSEUR A",
            "category": "BANQUE_FINANCE",
            "debit_amount": "200.00",
            "status": "PENDING",
        },
    )
    store.upsert_document_line(
        TENANT,
        "PERSO-1",
        {
            **common,
            "operation_date": "2026-03-07",
            "label": "AVANCE DIRIGEANT",
            "counterparty": "DIRIGEANT",
            "category": "FRAIS_GENERAUX",
            "document_type": "FACTURE",
            "amount_ht": "50.00",
            "amount_tva": "0.00",
            "amount_ttc": "50.00",
            "payment_source": "COMPTE_PERSO_DIRIGEANT",
            "counterparty_account": "455000_DIRIGEANT",
        },
    )
    service = BalanceSimulationService(store, export_dir=tmp_path / "exports")
    report = service.simulate(TENANT, YEAR)
    assert report.revenue_ht == Decimal("1000.00")
    assert report.purchases_ht == Decimal("400.00")
    assert report.gross_margin == Decimal("600.00")
    assert report.transport_ht == Decimal("50.00")
    assert report.general_expenses_ht == Decimal("150.00")
    assert report.operating_result == Decimal("400.00")
    assert report.receivables == Decimal("1200.00")
    assert report.payables == Decimal("660.00")
    assert report.cash == Decimal("300.00")
    assert report.shareholder_current_account == Decimal("50.00")
    path = service.write_report(report)
    assert path.name == BALANCE_FILENAME.format(year=YEAR)
    workbook = load_workbook(path)
    assert "COMPTE_RESULTAT" in workbook.sheetnames
    assert "BILAN" in workbook.sheetnames
    assert workbook["COMPTE_RESULTAT"].cell(row=1, column=1).value == "Poste"
    workbook.close()


# -- Hermès bridge -----------------------------------------------------------


def test_hermes_status_pending_and_decide_document(tmp_path):
    tree, _tenant_dir, store = prepare_flow(tmp_path)
    write_pending(
        tree,
        "Invoice-GENMCYCQ-0001.pdf",
        "Invoice GENMCYCQ-0001\nOpenRouter, Inc\nTotal $10.85 USD\n",
    )
    pipeline = ApdFourStagePipeline(
        TENANT, store=store, root=tmp_path, export_dir=tmp_path / "exports"
    )
    pipeline.run_stage(1)
    bridge = HermesAccountingBridge(
        TENANT,
        store,
        root=tmp_path,
        export_dir=tmp_path / "exports",
        pipeline=pipeline,
    )
    status = bridge.status_summary()
    assert status["pending"] >= 1
    assert status["tenant"] == TENANT
    questions = bridge.list_pending_questions()
    assert questions
    question = next(
        item
        for item in questions
        if item["filename"] == "Invoice-GENMCYCQ-0001.pdf"
    )
    assert "anglais" in question["why"]
    assert "SaaS" in question["why"]
    assert question["link"]
    result = bridge.decide_document(question["doc_id"], "VALIDER")
    assert result["status"] == "VALIDE"
    assert result["destination"]
    assert Path(result["destination"]).is_file()
    assert bridge.status_summary()["pending"] == 0


def test_hermes_run_stage_returns_structured_payload(tmp_path):
    _tree, _tenant_dir, store = prepare_flow(tmp_path)
    bridge = HermesAccountingBridge(
        TENANT, store, root=tmp_path, export_dir=tmp_path / "exports"
    )
    payload = bridge.run_stage(1)
    assert payload["stage"] == 1
    assert payload["name"] == STAGE_NAMES[1]
    assert payload["status"] == "OK"


# -- full four-stage smoke test ---------------------------------------------


def test_full_four_stage_pipeline_writes_ca12_and_balance(tmp_path):
    tree, _tenant_dir, store = prepare_flow(tmp_path)
    write_pending(
        tree,
        "Facture_Free_202602_30827543_1447097480.pdf",
        free_text(),
    )
    sources = tree.state_dir(FolderState.EXPORT_COMPTA) / "SOURCES_REEL"
    sources.mkdir(parents=True, exist_ok=True)
    (sources / "operations.ofx").write_text(ofx_diffam(), encoding="utf-8")
    pipeline = ApdFourStagePipeline(
        TENANT, store=store, root=tmp_path, export_dir=tmp_path / "exports"
    )
    reports = pipeline.run_all()
    assert [report.stage for report in reports] == [1, 2, 3, 4]
    assert all(report.status == "OK" for report in reports)
    names = {path.name for path in reports[-1].workbooks}
    assert f"SIMULATION_CA12_{YEAR}.xlsx" in names
    assert BALANCE_FILENAME.format(year=YEAR) in names
