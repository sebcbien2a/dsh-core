"""P26 - Yucel opening statement, synthetic duplicates, PDF links and aliases."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

from schemas.folder_state_schema import DocumentCategory
from services.accounting_memory_learning_service import (
    AccountingMemoryLearningService,
)
from services.accounting_spreadsheet_export_service import (
    CLIENT_LEDGER_COLUMNS_P25,
    ClientLedgerExporter,
    _normalize_payment_reference,
)
from services.apd_control_panel import dashboard_html, load_pending_questions
from services.opening_statement_ingestion_service import (
    OPENING_STATEMENT_STATUS_DUE,
    OPENING_STATEMENT_STATUS_PAID,
    OPENING_STATEMENT_STATUS_PRIOR,
    OpeningStatementIngestionService,
    OpeningStatementRow,
    read_opening_statement,
    write_opening_statement,
)
from services.sqlite_accounting_store import SqliteAccountingStore
from services.tier_reference_service import (
    COLUMNS,
    TierReferenceService,
)

TENANT = "APD"
YUCEL = "CARROSSERIE_YUCEL"

YUCEL_ROWS: tuple[OpeningStatementRow, ...] = (
    OpeningStatementRow("FA16222", "2024-10-31", Decimal("1614.80"), Decimal("0.00"), True, status=OPENING_STATEMENT_STATUS_PRIOR),
    OpeningStatementRow("FA16247", "2024-11-30", Decimal("757.86"), Decimal("0.00"), True, status=OPENING_STATEMENT_STATUS_PRIOR),
    OpeningStatementRow("FA16262", "2024-12-31", Decimal("1347.20"), Decimal("0.00"), True, status=OPENING_STATEMENT_STATUS_PRIOR),
    OpeningStatementRow("FA16277", "2025-01-31", Decimal("1680.58"), Decimal("0.00"), True, status=OPENING_STATEMENT_STATUS_PRIOR),
    OpeningStatementRow("FA16292", "2025-02-28", Decimal("301.76"), Decimal("0.00"), True, status=OPENING_STATEMENT_STATUS_PRIOR),
    OpeningStatementRow("FA16306", "2025-03-31", Decimal("747.25"), Decimal("0.00"), True, status=OPENING_STATEMENT_STATUS_PRIOR),
    OpeningStatementRow("FA16325", "2025-04-30", Decimal("1071.32"), Decimal("0.00"), True, "2026-01-23", "2026-01-23", OPENING_STATEMENT_STATUS_PAID),
    OpeningStatementRow("FA16339", "2025-05-31", Decimal("928.22"), Decimal("0.00"), True, "2026-01-23", "2026-01-23", OPENING_STATEMENT_STATUS_PAID),
    OpeningStatementRow("FA16347", "2025-06-30", Decimal("1112.95"), Decimal("0.00"), True, "2026-01-23", "2026-01-23", OPENING_STATEMENT_STATUS_PAID),
    OpeningStatementRow("FA16354", "2025-07-31", Decimal("718.43"), Decimal("0.00"), True, "2026-03-05", "2026-03-05", OPENING_STATEMENT_STATUS_PAID),
    OpeningStatementRow("FA16367", "2025-08-31", Decimal("1410.02"), Decimal("0.00"), True, "2026-03-05", "2026-03-05", OPENING_STATEMENT_STATUS_PAID),
    OpeningStatementRow("FA16378", "2025-09-30", Decimal("1753.45"), Decimal("0.00"), True, "2026-07-03", "2026-07-03", OPENING_STATEMENT_STATUS_PAID),
    OpeningStatementRow("FA16393", "2025-10-31", Decimal("1662.65"), Decimal("0.00"), True, "2026-10-06", "2026-10-06", OPENING_STATEMENT_STATUS_PAID),
    OpeningStatementRow("FA16423", "2026-01-31", Decimal("950.76"), Decimal("0.00"), True, "2026-07-03", "2026-07-03", OPENING_STATEMENT_STATUS_PAID),
    OpeningStatementRow("FA16444", "2026-02-28", Decimal("654.92"), Decimal("0.00"), True, "2026-10-06", "2026-10-06", OPENING_STATEMENT_STATUS_PAID),
    OpeningStatementRow("FA16459", "2026-03-31", Decimal("750.14"), Decimal("0.00"), True, "2026-10-06", "2026-10-06", OPENING_STATEMENT_STATUS_PAID),
    OpeningStatementRow("FA16406", "2025-11-30", Decimal("2013.32"), Decimal("2013.32"), False, status=OPENING_STATEMENT_STATUS_DUE),
    OpeningStatementRow("FA16413", "2025-12-31", Decimal("2054.85"), Decimal("2054.85"), False, status=OPENING_STATEMENT_STATUS_DUE),
    OpeningStatementRow("FA16471", "2026-04-30", Decimal("680.27"), Decimal("680.27"), False, status=OPENING_STATEMENT_STATUS_DUE),
    OpeningStatementRow("FA16484", "2026-05-30", Decimal("883.56"), Decimal("883.56"), False, status=OPENING_STATEMENT_STATUS_DUE),
    OpeningStatementRow("FA16495", "2026-06-30", Decimal("917.33"), Decimal("917.33"), False, status=OPENING_STATEMENT_STATUS_DUE),
    OpeningStatementRow("FA16508", "2026-07-31", Decimal("672.25"), Decimal("672.25"), False, status=OPENING_STATEMENT_STATUS_DUE),
    OpeningStatementRow("FA16528", "2026-09-30", Decimal("1005.08"), Decimal("1005.08"), False, status=OPENING_STATEMENT_STATUS_DUE),
    OpeningStatementRow("FA16529", "2026-09-30", Decimal("7.62"), Decimal("7.62"), False, status=OPENING_STATEMENT_STATUS_DUE),
)

VIREMENTS: tuple[tuple[str, str, str], ...] = (
    ("20260123111107065686145052992420260123", "2026-01-23", "1071.32"),
    ("20260123111149563798145057703820260123", "2026-01-23", "928.22"),
    ("20260123111246863889145054852920260123", "2026-01-23", "1112.95"),
    ("20260305115659259247145057029720260305", "2026-03-05", "718.43"),
    ("20260305115818582640145052287120260305", "2026-03-05", "1410.02"),
    ("20260703141453886915145054271320260703", "2026-07-03", "1753.45"),
    ("20260703141545180335145058189420260703", "2026-07-03", "950.76"),
    ("20261006103748161408145054596020261006", "2026-10-06", "1662.65"),
    ("20261006103857581315145051131620261006", "2026-10-06", "654.92"),
    ("20261006104119344420145055338420261006", "2026-10-06", "750.14"),
)


def make_store(tmp_path: Path) -> SqliteAccountingStore:
    return SqliteAccountingStore(tmp_path / "accounting.sqlite3")


def add_invoice(
    store: SqliteAccountingStore,
    invoice_id: str,
    *,
    day: str,
    ttc: str,
    counterparty: str = "Carrosserie Yucell",
    **extra: object,
) -> None:
    line = {
        "operation_date": day,
        "label": f"Facture {invoice_id} {counterparty}",
        "counterparty": counterparty,
        "invoice_id": invoice_id,
        "category": DocumentCategory.VENTES_CLIENTS.value,
        "document_type": "CUSTOMER_INVOICE",
        "amount_ht": str((Decimal(ttc) / Decimal("1.2")).quantize(Decimal("0.01"))),
        "amount_tva": str((Decimal(ttc) - (Decimal(ttc) / Decimal("1.2")).quantize(Decimal("0.01"))).quantize(Decimal("0.01"))),
        "amount_ttc": ttc,
        "status": "VALIDE",
    }
    line.update(extra)
    store.upsert_document_line(TENANT, f"SALE-{invoice_id}", line)


def add_virement(
    store: SqliteAccountingStore,
    fitid: str,
    day: str,
    amount: str,
) -> None:
    store.add_line(
        TENANT,
        f"OFX-{fitid}",
        {
            "operation_date": day,
            "label": "VIR INST SARL CARROSSERIE YUCE",
            "counterparty": "SARL CARROSSERIE YUCE",
            "credit_amount": amount,
            "category": DocumentCategory.VENTES_CLIENTS.value,
            "document_type": "OFX",
            "fitid": fitid,
            "status": "PENDING",
        },
    )


def install_temp_tiers(tmp_path: Path, store: SqliteAccountingStore) -> None:
    import services.accounting_spreadsheet_export_service as export_service

    memory = AccountingMemoryLearningService(tmp_path / "rules.json")
    tiers = TierReferenceService(
        tmp_path / "REFERENTIEL_TIERS_APD.xlsx",
        store=store,
        memory=memory,
        tenant_id=TENANT,
    )
    tiers.bootstrap()
    export_service._LEARNED_MEMORY = memory
    export_service._TIER_REFERENCE = tiers


# -- 1. synthetic duplicates ------------------------------------------------


def test_payment_reference_normalisation_strips_bank_prefixes():
    assert (
        _normalize_payment_reference(
            "VIREMENT 202610061041193444201450"
        )
        == "202610061041193444201450"
    )
    assert _normalize_payment_reference("VIR 12345") == "12345"
    assert _normalize_payment_reference("LCR ILA193A1") == "ila193a1"
    assert _normalize_payment_reference("CB AMAZON") == "amazon"


def test_synthetic_payment_is_not_duplicated_when_bank_line_exists(tmp_path):
    store = make_store(tmp_path)
    install_temp_tiers(tmp_path, store)
    add_invoice(
        store,
        "FA16423",
        day="2026-01-31",
        ttc="950.76",
        reconciliation_status="PAYEE_RAPPROCHEE",
        reconciliation_date="2026-07-03",
        reconciliation_reference="VIREMENT 20260703141545180335145058189420260703",
        reconciliation_amount="950.76",
        balance_due="0.00",
    )
    add_virement(
        store,
        "20260703141545180335145058189420260703",
        "2026-07-03",
        "950.76",
    )
    path = ClientLedgerExporter(
        store,
        export_dir=tmp_path / "exports",
        strict_tiers=True,
        include_matched_pieces=True,
    ).export(TENANT)
    sheet = load_workbook(path)["CARROSSERIE_YUCEL"]
    rows = list(sheet.iter_rows(min_row=2, values_only=True))
    payments = [row for row in rows if row[1] == "PAIEMENT"]
    assert len(payments) == 1
    assert payments[0][5] == pytest.approx(950.76)
    assert not any(
        row[3] == "Règlement lettré (registre)" for row in rows
    )


# -- 2. opening statement ---------------------------------------------------


def test_yucel_opening_statement_balance_is_8234_28(tmp_path):
    store = make_store(tmp_path)
    install_temp_tiers(tmp_path, store)
    for fitid, day, amount in VIREMENTS:
        add_virement(store, fitid, day, amount)
    statement = tmp_path / "RELEVE_OUVERTURE_CARROSSERIE_YUCEL_20261006.xlsx"
    write_opening_statement(statement, YUCEL_ROWS)
    parsed = read_opening_statement(statement)
    assert len(parsed) == 24
    service = OpeningStatementIngestionService(store, TENANT, client=YUCEL)
    report = service.ingest(
        statement, purge_remittances=("90949", "92174", "93696", "93847")
    )
    assert report.balance_due == Decimal("8234.28")
    lines = {
        row["invoice_id"]: row
        for row in store.list_master_lines(TENANT)
        if (row.get("invoice_id") or "").startswith("FA16")
    }
    for invoice in ("FA16222", "FA16247", "FA16262", "FA16277", "FA16292", "FA16306"):
        assert lines[invoice]["reconciliation_status"] == OPENING_STATEMENT_STATUS_PRIOR
    for invoice in ("FA16325", "FA16339", "FA16347", "FA16354", "FA16367", "FA16378", "FA16393", "FA16423", "FA16444", "FA16459"):
        assert lines[invoice]["reconciliation_status"] == OPENING_STATEMENT_STATUS_PAID
        assert lines[invoice]["reconciliation_reference"] in {
            fitid for fitid, _day, _amount in VIREMENTS
        }
    for invoice in ("FA16406", "FA16413", "FA16471", "FA16484", "FA16495", "FA16508", "FA16528", "FA16529"):
        assert lines[invoice]["reconciliation_status"] == OPENING_STATEMENT_STATUS_DUE
    path = ClientLedgerExporter(
        store,
        export_dir=tmp_path / "exports",
        strict_tiers=True,
        include_matched_pieces=True,
    ).export(TENANT)
    sheet = load_workbook(path)["CARROSSERIE_YUCEL"]
    total = sheet.cell(sheet.max_row, 7).value
    assert total == pytest.approx(8234.28)


def test_purge_removes_false_remittance_lettering(tmp_path):
    store = make_store(tmp_path)
    install_temp_tiers(tmp_path, store)
    add_invoice(
        store,
        "FA16406",
        day="2025-11-30",
        ttc="2013.32",
        reconciliation_status="PAYEE_RAPPROCHEE",
        reconciliation_date="2026-01-07",
        reconciliation_reference="REMISE 90949",
        reconciliation_amount="2013.32",
        balance_due="0.00",
    )
    service = OpeningStatementIngestionService(store, TENANT, client=YUCEL)
    assert service.purge_remittance_lettering(("90949",)) == 1
    line = store.get_line(TENANT, "SALE-FA16406")
    assert line is not None
    assert line["reconciliation_status"] == OPENING_STATEMENT_STATUS_DUE
    assert line["reconciliation_reference"] is None
    assert Decimal(line["balance_due"]) == Decimal("2013.32")


# -- 3. PDF links and readable reconciliation -------------------------------


def test_payment_lines_have_pdf_links_and_readable_lettering(tmp_path):
    store = make_store(tmp_path)
    install_temp_tiers(tmp_path, store)
    add_invoice(
        store,
        "FA16423",
        day="2026-01-31",
        ttc="950.76",
        reconciliation_status="PAYEE_RAPPROCHEE",
        reconciliation_date="2026-07-03",
        reconciliation_reference="VIREMENT 20260703141545180335145058189420260703",
        reconciliation_amount="950.76",
        balance_due="0.00",
    )
    add_virement(
        store,
        "20260703141545180335145058189420260703",
        "2026-07-03",
        "950.76",
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
    assert tuple(cell.value for cell in sheet[1]) == CLIENT_LEDGER_COLUMNS_P25
    rows = list(sheet.iter_rows(min_row=2))
    invoice_row = next(row for row in rows if row[1].value == "FACTURE")
    payment_row = next(row for row in rows if row[1].value == "PAIEMENT")
    assert invoice_row[8].value == "Payée le 03/07/2026 par VIR"
    assert payment_row[8].value == "Règle Facture : FA16423"
    assert payment_row[9].hyperlink is not None
    assert (
        "PIECES_JOINTES/VENTES/FA16423_2026-01-31_CARROSSERIE_YUCELL.pdf"
        in str(payment_row[9].hyperlink.target)
    )


# -- 4. alias table merge ---------------------------------------------------


def test_alias_table_merges_sqlite_memory_and_duplicate_sheet(tmp_path):
    store = make_store(tmp_path)
    memory = AccountingMemoryLearningService(tmp_path / "rules.json")
    path = tmp_path / "REFERENTIEL_TIERS_APD.xlsx"
    service = TierReferenceService(
        path, store=store, memory=memory, tenant_id=TENANT
    )
    service.bootstrap()
    workbook = load_workbook(path)
    sheet = workbook.active
    assert tuple(cell.value for cell in sheet[1]) == COLUMNS
    # Add a duplicate tier and ask the director to rename it into Yucel.
    row = sheet.max_row + 1
    sheet.cell(row, 1, "CARROSSERIE_YUCELL_OLD")
    sheet.cell(row, 2, "ANCIENNE CARROSSERIE")
    sheet.cell(row, 3, "CARROSSERIE_YUCEL")
    sheet.cell(row, 6, "CLIENT")
    sheet.cell(row, 7, "VENTES_CLIENTS")
    sheet.cell(row, 10, "AUTORISE")
    workbook.save(path)
    workbook.close()
    add_invoice(
        store,
        "FA90001",
        day="2026-01-31",
        ttc="100.00",
        counterparty="CARROSSERIE_YUCELL_OLD",
    )
    reloaded = TierReferenceService(
        path, store=store, memory=memory, tenant_id=TENANT
    )
    reloaded.bootstrap()
    assert reloaded.canonical_name("CARROSSERIE_YUCELL_OLD") == "CARROSSERIE_YUCEL"
    assert reloaded.canonical_name("ANCIENNE CARROSSERIE") == "CARROSSERIE_YUCEL"
    line = store.get_line(TENANT, "SALE-FA90001")
    assert line is not None
    assert line["counterparty"] == "CARROSSERIE_YUCEL"
    rules = json.loads((tmp_path / "rules.json").read_text(encoding="utf-8"))
    assert rules["aliases"]["CARROSSERIE_YUCELL_OLD"] == "CARROSSERIE_YUCEL"


# -- 5. cockpit loads pending questions immediately -------------------------


def test_cockpit_embeds_pending_questions_without_click(tmp_path):
    store = make_store(tmp_path)
    tree = tmp_path / "APD"
    pending_dir = tree / "02_A_COMPLETER"
    pending_dir.mkdir(parents=True, exist_ok=True)
    from services.acompleter_workbook_service import AcompleterWorkbook

    workbook = AcompleterWorkbook(tree)
    workbook.ensure_workbook()
    workbook.upsert(
        "a" * 64,
        "FACTURE_A_VERIFIER.pdf",
        [],
        DocumentCategory.FRAIS_GENERAUX,
    )
    store.upsert_document_line(
        TENANT,
        "a" * 64,
        {
            "operation_date": "2026-04-02",
            "label": "FACTURE_A_VERIFIER.pdf",
            "counterparty": "FOURNISSEUR INCONNU",
            "invoice_id": "INV-1",
            "category": DocumentCategory.FRAIS_GENERAUX.value,
            "amount_ttc": "120.00",
            "status": "EN_ATTENTE",
            "document_type": "FACTURE",
        },
    )
    questions = load_pending_questions(tenant_id=TENANT, root=tmp_path)
    assert any(
        question.get("filename") == "FACTURE_A_VERIFIER.pdf"
        for question in questions
    )
    html = dashboard_html(pending_questions=questions)
    assert "window.INITIAL_PENDING" in html
    assert "FACTURE_A_VERIFIER.pdf" in html


# -- 6. read-only split audit ----------------------------------------------


def test_audit_script_generates_three_bucket_report(tmp_path):
    from scripts.audit_dsh_vs_compta_map import main

    root = tmp_path / "repo"
    (root / "services").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "docs").mkdir()
    (root / "services" / "dsh_harness.py").write_text("x", encoding="utf-8")
    (root / "services" / "accounting_spreadsheet_export_service.py").write_text(
        "x", encoding="utf-8"
    )
    (root / "tests" / "test_demo.py").write_text("x", encoding="utf-8")
    (root / "debug.log").write_text("x", encoding="utf-8")
    output = tmp_path / "report.md"
    assert main(["--root", str(root), "--output", str(output)]) == 0
    report = output.read_text(encoding="utf-8")
    assert "Socle tri-système `dsh-core`" in report
    assert "Application métier `apd-compta`" in report
    assert "artefacts temporaires" in report
    assert "dsh_harness.py" in report
    assert "accounting_spreadsheet_export_service.py" in report
    assert "test_demo.py" in report
