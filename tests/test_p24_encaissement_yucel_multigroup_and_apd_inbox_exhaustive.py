"""P24 - Encaissement Yucel, multi-group memory, APD inbox and control panel."""

from __future__ import annotations

import hashlib
import os
from datetime import date
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path

import pytest
from openpyxl import load_workbook

from schemas.folder_state_schema import (
    DocumentCategory,
    DocumentStatus,
    ExpenseSubCategory,
    FolderState,
)
from services.accounting_memory_learning_service import (
    AccountingMemoryLearningService,
)
from services.accounting_spreadsheet_export_service import (
    CLIENT_LEDGER_COLUMNS,
    ClientLedgerExporter,
    MASTER_BANQUE_TVA_HEADERS,
    MasterTableExporter,
)
from services.acompleter_readable_service import (
    READABLE_HEADERS,
    ReadableRow,
    read_readable_decisions,
    write_readable_workbook,
)
from services.acompleter_workbook_service import AcompleterWorkbook, derive_document_id
from schemas.accounting_choices import (
    CATEGORY_CHOICES,
    DEFERRED_MONTH_CHOICES,
    EXERCISE_CHOICES,
    PAYMENT_SOURCE_CHOICES,
    REPORT_CHOICES,
    excel_list,
)
from services.apd_control_panel import (
    dashboard_html,
    render_pending_table,
    render_status_table,
)
from services.apd_four_stage_pipeline import ApdFourStagePipeline
from services.folder_tree_service import ensure_tree
from services.inbox_ingestion_service import (
    InboxIngestionService,
    sync_workbooks_to_onedrive,
)
from services.sqlite_accounting_store import SqliteAccountingStore
from services.vat_simulation_service import VatSimulationService

TENANT = "default"
YEAR = 2026


def make_store(tmp_path: Path) -> SqliteAccountingStore:
    return SqliteAccountingStore(tmp_path / "accounting.sqlite3")


def make_text_pdf(path: Path, text: str) -> Path:
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(fitz.Rect(40, 40, 560, 800), text, fontsize=9)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    document.close()
    return path


def add_line(
    store: SqliteAccountingStore,
    key: str,
    *,
    category: DocumentCategory,
    day: str = "2026-03-15",
    counterparty: str = "FOURNISSEUR",
    invoice_id: str | None = None,
    ht: str = "100.00",
    tva: str = "20.00",
    ttc: str = "120.00",
    status: str = "VALIDE",
    **extra: object,
) -> None:
    line: dict[str, object] = {
        "operation_date": day,
        "label": f"{key}.pdf",
        "counterparty": counterparty,
        "invoice_id": invoice_id or key,
        "invoice_url": f"03_VALIDE_PRET_FEC/{category.value}/{key}.pdf",
        "category": category.value,
        "amount_ht": ht,
        "amount_tva": tva,
        "amount_ttc": ttc,
        "status": status,
        "document_type": "CUSTOMER_INVOICE"
        if category is DocumentCategory.VENTES_CLIENTS
        else "FACTURE",
    }
    line.update(extra)
    store.upsert_document_line(TENANT, key, line)


# -- portable memory ---------------------------------------------------------


def test_memory_groups_ila_aliases_and_learning(tmp_path):
    service = AccountingMemoryLearningService(tmp_path / "rules.json")
    assert service.canonical_name("CRISOL COLOR, S.L.") == "PAINTCAR"
    assert service.canonical_name("GROUPE TOTCOLOR") == "PAINTCAR"
    assert service.match_reference("REMISE LCR ILA193A1") == "PAINTCAR"
    assert service.canonical_name("QREFINISH FRANCE") == "QREFINISH"
    assert service.canonical_name("SASU INDIGO GALLERY") == "LE TEMPS DES CERISES"
    assert service.canonical_name("HABITAT AUTOMATISME") == "SAS DIFFAM"
    assert service.canonical_name("Carrosserie Yucell") == "CARROSSERIE_YUCEL"
    before = service.decision_count()
    service.remember_decision(
        counterparty="NOUVEAU FOURNISSEUR",
        document_number="X-1",
        category="FRAIS_GENERAUX",
        decision="VALIDE",
    )
    assert service.decision_count() == before + 1
    # The file is portable JSON and reloads the new fact.
    reloaded = AccountingMemoryLearningService(tmp_path / "rules.json")
    assert reloaded.load()["decisions"]


# -- cash VAT, prudent / optimized and Yucel ledgers ------------------------


def test_scenario_a_prudent_b_optimized_uses_2026_cash_date(tmp_path):
    store = make_store(tmp_path)
    add_line(
        store,
        "SALE-PAID",
        category=DocumentCategory.VENTES_CLIENTS,
        day="2026-02-01",
        counterparty="CLIENT PAYE",
        ht="1000.00",
        tva="200.00",
        ttc="1200.00",
        reconciliation_status="PAYEE_RAPPROCHEE",
        reconciliation_date="2026-02-20",
        reconciliation_amount="1200.00",
        balance_due="0.00",
    )
    add_line(
        store,
        "REP-1",
        category=DocumentCategory.FRAIS_GENERAUX,
        day="2025-12-31",
        counterparty="LE TEMPS DES CERISES",
        ht="471.65",
        tva="94.33",
        ttc="565.98",
        expense_subcategory=ExpenseSubCategory.FRAIS_REPRESENTATION_PRESIDENT.value,
        reconciliation_status="PAYEE_RAPPROCHEE",
        reconciliation_date="2026-01-15",
        reconciliation_amount="565.98",
        balance_due="0.00",
    )
    service = VatSimulationService(
        store, export_dir=tmp_path / "exports", cash_basis=True
    )
    report = service.simulate(TENANT, YEAR)
    assert report.cash_basis is True
    assert report.scenario_a.pieces == 2
    assert report.scenario_a.deductible_sensitive_tva == Decimal("0.00")
    assert report.scenario_b.deductible_sensitive_tva == Decimal("94.33")
    assert (
        report.scenario_b.deductible_total - report.scenario_a.deductible_total
    ) == Decimal("94.33")
    assert report.scenario_a.tva_due == Decimal("200.00")
    assert report.scenario_a.tva_due - report.scenario_b.tva_due == Decimal("94.33")
    path = service.write_report(report)
    workbook = load_workbook(path)
    headers = [cell.value for cell in workbook["COMPARATIF"][1]]
    assert headers[:4] == ["Rubrique", "Scenario A", "Scenario B", "Ecart (B - A)"]
    rows = {
        row[0]: row
        for row in workbook["COMPARATIF"].iter_rows(min_row=2, values_only=True)
    }
    assert rows[
        "TVA deductible - Depenses sensibles (representation / hotel / restaurant)"
    ][3] == pytest.approx(94.33)
    workbook.close()


def test_client_ledger_yucel_and_bank_mirror_counterparty(tmp_path):
    store = make_store(tmp_path)
    add_line(
        store,
        "SALE-FA16406",
        category=DocumentCategory.VENTES_CLIENTS,
        day="2025-11-30",
        counterparty="Carrosserie Yucell",
        invoice_id="FA16406",
        ht="1677.77",
        tva="335.55",
        ttc="2013.32",
    )
    store.add_line(
        TENANT,
        "OFX-YUCEL",
        {
            "operation_date": "2026-02-10",
            "label": "VIREMENT CARROSSERIE YUCELL",
            "counterparty": "CARROSSERIE YUCELL",
            "credit_amount": "2013.32",
            "category": DocumentCategory.BANQUE_FINANCE.value,
            "document_type": "OFX",
            "payment_method": "VIREMENT",
            "fitid": "FIT-YUCEL",
            "status": "PENDING",
        },
    )
    path = ClientLedgerExporter(store, export_dir=tmp_path / "exports").export(
        TENANT
    )
    workbook = load_workbook(path)
    assert "CARROSSERIE_YUCEL" in workbook.sheetnames
    sheet = workbook["CARROSSERIE_YUCEL"]
    assert tuple(cell.value for cell in sheet[1]) == CLIENT_LEDGER_COLUMNS
    rows = [row for row in sheet.iter_rows(min_row=2, values_only=True)]
    assert any(row[1] == "FACTURE" and row[2] == "FA16406" for row in rows)
    assert any(row[1] == "PAIEMENT" and row[5] == 2013.32 for row in rows)
    workbook.close()

    master = MasterTableExporter(
        store, root=tmp_path, export_dir=tmp_path / "exports"
    ).export(TENANT)
    bank = load_workbook(master.banque_tva_path)
    bank_sheet = bank.active
    assert tuple(cell.value for cell in bank_sheet[1]) == MASTER_BANQUE_TVA_HEADERS
    values = [row for row in bank_sheet.iter_rows(min_row=2, values_only=True)]
    assert any("CARROSSERIE_YUCEL" in str(row[1]) for row in values)
    bank.close()


# -- readable _A_COMPLETER: prefill, human validation, PermissionError -----


def test_readable_arbitration_columns_are_prefilled_and_applied(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(
        "DSH_ACCOUNTING_MEMORY_PATH", str(tmp_path / "memory.json")
    )
    store = make_store(tmp_path)
    tree = ensure_tree(TENANT, tmp_path)
    tenant_dir = tree.root / TENANT
    pending_dir = tree.state_dir(FolderState.A_COMPLETER)
    pdf = make_text_pdf(pending_dir / "SOURCES_SAAS.pdf", "SAAS invoice")
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    store.upsert_document_line(
        TENANT,
        derive_document_id(digest),
        {
            "operation_date": "2026-04-02",
            "label": "SOURCES_SAAS.pdf",
            "counterparty": "OPENROUTER, INC",
            "invoice_id": "SAAS-1",
            "invoice_url": "02_A_COMPLETER/SOURCES_SAAS.pdf",
            "category": DocumentCategory.FRAIS_GENERAUX.value,
            "amount_ht": "100.00",
            "amount_tva": "20.00",
            "amount_ttc": "120.00",
            "status": DocumentStatus.EN_ATTENTE.value,
            "document_type": "FACTURE",
        },
    )
    workbook = AcompleterWorkbook(tenant_dir)
    workbook.upsert(digest, pdf.name, [], DocumentCategory.FRAIS_GENERAUX)
    pipeline = ApdFourStagePipeline(
        TENANT,
        store=store,
        root=tmp_path,
        export_dir=tmp_path / "exports",
        year=YEAR,
    )
    pipeline.prefill_arbitration_columns()
    metadata = workbook.read_p17_metadata(digest)
    assert metadata is not None
    assert metadata["Source de Paiement"]
    assert metadata["Compte de Contrepartie"]
    assert metadata["Exercice Cible"] == "2026"
    assert metadata["Mois Décalé"] == "NON"
    assert metadata["Report"] == "NON"
    workbook.align_statut(digest, DocumentStatus.VALIDE)
    pipeline.apply_human_decisions()
    line = store.get_line(TENANT, derive_document_id(digest))
    assert line is not None and line["status"] == "VALIDE"
    assert not (pending_dir / pdf.name).exists()
    validated = tree.ged_dir("FACTURES_FOURNISSEURS")
    assert any(path.name == pdf.name for path in validated.glob("*.pdf"))


def test_locked_workbook_does_not_break_prefill(tmp_path):
    store = make_store(tmp_path)
    tree = ensure_tree(TENANT, tmp_path)
    tenant_dir = tree.root / TENANT
    workbook = AcompleterWorkbook(tenant_dir)
    workbook.ensure_workbook()
    (tenant_dir / "~$A_COMPLETER.xlsx").write_bytes(b"lock")
    pipeline = ApdFourStagePipeline(
        TENANT,
        store=store,
        root=tmp_path,
        export_dir=tmp_path / "exports",
    )
    assert pipeline.prefill_arbitration_columns() == 0


# -- unified APD/default inbox and .eml attachments -------------------------


def test_unified_apd_inbox_extracts_eml_pdf_attachment(tmp_path):
    store = make_store(tmp_path)
    tree_apd = ensure_tree("APD", tmp_path)
    apd_inbox = tree_apd.state_dir(FolderState.INBOX)
    pdf = make_text_pdf(
        tmp_path / "free.pdf",
        "\n".join(
            [
                "FREE TELECOM",
                "Facture Freebox n° 1447097480 du 02 Février 2026",
                "Total HT 33,33 EUR",
                "Total TVA 6,66 EUR",
                "Total TTC 39,99 EUR",
                "Prélèvement SEPA",
            ]
        ),
    )
    message = EmailMessage()
    message["Subject"] = "SMTRT - facture"
    message["From"] = "facturation@smtrt.example"
    message.set_content("Veuillez trouver la pièce jointe.")
    message.add_attachment(
        pdf.read_bytes(),
        maintype="application",
        subtype="pdf",
        filename="SMTRT_FACTURE.pdf",
    )
    (apd_inbox / "SMTRT.eml").write_bytes(message.as_bytes())
    service = InboxIngestionService(
        TENANT,
        store,
        tmp_path,
        unified_tenants=("APD",),
        enable_onedrive_mirror=False,
    )
    report = service.process_inbox()
    assert report.scanned >= 1
    assert not (apd_inbox / "SMTRT.eml").exists()
    default_inbox = tree = ensure_tree(TENANT, tmp_path)
    assert not list(default_inbox.state_dir(FolderState.INBOX).glob("*.eml"))
    # The attachment entered the flow (validated or at least persisted).
    assert any(
        row.get("counterparty") and "FREE" in str(row.get("counterparty")).upper()
        for row in store.list_master_lines(TENANT)
    )


def test_onedrive_compta_mirror_creates_and_copies(tmp_path, monkeypatch):
    mirror = tmp_path / "OneDrive" / "Compta_APD"
    monkeypatch.setenv("DSH_ONEDRIVE_COMPTA_DIR", str(mirror))
    workbook = tmp_path / "TABLEAU_MERE_PIECES_COMPTABLES.xlsx"
    workbook.write_bytes(b"xlsx")
    copied = sync_workbooks_to_onedrive([workbook], root=tmp_path)
    assert (mirror / "00_INBOX").is_dir()
    assert (mirror / "Tableaux_Comptables").is_dir()
    assert copied and (mirror / "Tableaux_Comptables" / workbook.name).is_file()


# -- control panel -----------------------------------------------------------


def test_control_panel_renders_french_html_tables_and_stage_buttons():
    html = dashboard_html()
    assert "Cockpit comptable" in html
    assert "Étape 1" in html and "Étape 4" in html
    assert "<table>" in html
    assert "Compte-rendu" in html
    status_html = render_status_table(
        {
            "tenant": "default",
            "year": 2026,
            "pending_questions": 2,
            "validated": 10,
        }
    )
    assert "<table" in status_html and "Questions en attente" in status_html
    assert "Aucune question" in render_pending_table([])


# -- P24 readable dropdowns, priority decisions and fallback saves ----------


def test_readable_workbook_has_official_dropdowns_and_hidden_trace(tmp_path):
    path = write_readable_workbook(
        tmp_path / "02_A_COMPLETER" / "_A_COMPLETER.xlsx",
        [
            ReadableRow(
                sha256="a" * 64,
                filename="SAAS.pdf",
                supplier="FOURNISSEUR INCONNU",
                document_number="SAAS-1",
                document_date=date(2026, 4, 2),
                amount_ht=Decimal("100.00"),
                amount_tva=Decimal("20.00"),
                amount_ttc=Decimal("120.00"),
                category=DocumentCategory.FRAIS_GENERAUX,
                reason="SaaS en attente",
                action="Compl?ter la ventilation",
                extra={
                    "payment_source": "BANQUE_PRO",
                    "counterparty_account": "622600",
                    "fiscal_year_target": "2026",
                    "deferred_month": "NON",
                    "report": "NON",
                },
            )
        ],
    )
    workbook = load_workbook(path)
    sheet = workbook.active
    assert tuple(cell.value for cell in sheet[1])[: len(READABLE_HEADERS)] == READABLE_HEADERS
    assert sheet.column_dimensions["O"].hidden is True
    formulas = {
        str(validation.sqref): validation.formula1
        for validation in sheet.data_validations.dataValidation
    }
    assert excel_list(CATEGORY_CHOICES) in formulas.values()
    assert excel_list(PAYMENT_SOURCE_CHOICES) in formulas.values()
    assert excel_list(EXERCISE_CHOICES) in formulas.values()
    assert excel_list(DEFERRED_MONTH_CHOICES) in formulas.values()
    assert excel_list(REPORT_CHOICES) in formulas.values()
    decisions = read_readable_decisions(path)
    assert decisions and decisions[0].sha256 == "a" * 64
    assert decisions[0].status == DocumentStatus.EN_ATTENTE.value
    workbook.close()


def test_readable_human_edit_is_applied_and_removed_from_pending(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(
        "DSH_ACCOUNTING_MEMORY_PATH", str(tmp_path / "memory.json")
    )
    store = make_store(tmp_path)
    tree = ensure_tree(TENANT, tmp_path)
    tenant_dir = tree.root / TENANT
    pending_dir = tree.state_dir(FolderState.A_COMPLETER)
    pdf = make_text_pdf(pending_dir / "A_VALIDER.pdf", "piece a valider")
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    store.upsert_document_line(
        TENANT,
        derive_document_id(digest),
        {
            "operation_date": "2026-04-02",
            "label": "A_VALIDER.pdf",
            "counterparty": "FOURNISSEUR TEST",
            "invoice_id": "VAL-1",
            "invoice_url": "02_A_COMPLETER/A_VALIDER.pdf",
            "category": DocumentCategory.DIVERS_A_CLASSER.value,
            "amount_ht": "100.00",
            "amount_tva": "20.00",
            "amount_ttc": "120.00",
            "status": DocumentStatus.EN_ATTENTE.value,
            "document_type": "FACTURE",
        },
    )
    readable = pending_dir / "_A_COMPLETER.xlsx"
    write_readable_workbook(
        readable,
        [
            ReadableRow(
                sha256=digest,
                filename=pdf.name,
                supplier="FOURNISSEUR TEST",
                document_number="VAL-1",
                document_date=date(2026, 4, 2),
                amount_ht=Decimal("100.00"),
                amount_tva=Decimal("20.00"),
                amount_ttc=Decimal("120.00"),
                category=DocumentCategory.DIVERS_A_CLASSER,
                reason="Cat?gorie ? confirmer",
                action="Choisir une cat?gorie",
                extra={
                    "payment_source": "BANQUE_PRO",
                    "counterparty_account": "622600",
                    "fiscal_year_target": "2026",
                    "deferred_month": "NON",
                    "report": "NON",
                },
                piece_pdf=pdf.name,
            )
        ],
    )
    workbook = load_workbook(readable)
    sheet = workbook.active
    sheet["E2"] = "TRANSPORTS_LOGISTIQUE"
    sheet["G2"] = "VALIDER"
    sheet["H2"] = "CCA_455_DIRIGEANT"
    workbook.save(readable)
    workbook.close()
    pipeline = ApdFourStagePipeline(
        TENANT,
        store=store,
        root=tmp_path,
        export_dir=tmp_path / "exports",
        year=YEAR,
    )
    assert pipeline.apply_human_decisions() >= 1
    line = store.get_line(TENANT, derive_document_id(digest))
    assert line is not None
    assert line["status"] == "VALIDE"
    assert line["reconciliation_status"] == "VALIDE"
    assert line["category"] == "TRANSPORTS_LOGISTIQUE"
    assert line["payment_source"] == "COMPTE_PERSO_DIRIGEANT"
    assert line["counterparty_account"] == "455000_DIRIGEANT"
    assert not (pending_dir / pdf.name).exists()
    pipeline._write_readable_views()
    remaining = read_readable_decisions(readable)
    assert not any(item.sha256 == digest for item in remaining)
