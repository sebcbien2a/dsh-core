"""P25 - Tier directory aliases, ledger direction, payment dates and cleanup."""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

from schemas.folder_state_schema import DocumentCategory, DocumentStatus, FolderState
from services.accounting_cleanup_service import (
    clean_pdf_suffixes,
    dedupe_phantom_lines,
    normalize_missing_dates,
    remove_default_workbooks,
)
from services.accounting_spreadsheet_export_service import (
    CLIENT_LEDGER_COLUMNS_P25,
    MASTER_PIECES_HEADERS,
    SUPPLIER_LEDGER_COLUMNS_P25,
    CategorySummaryExporter,
    ClientLedgerExporter,
    MasterTableExporter,
    SupplierLedgerExporter,
)
from services.acompleter_workbook_service import derive_document_id
from services.apd_four_stage_pipeline import ApdFourStagePipeline
from services.folder_tree_service import ensure_tree
from services.sqlite_accounting_store import SqliteAccountingStore
from services.tier_reference_service import (
    COLUMNS,
    TIER_REFERENCE_FILENAME,
    TierReferenceService,
)

TENANT = "APD"
YEAR = 2026


def make_store(tmp_path: Path) -> SqliteAccountingStore:
    return SqliteAccountingStore(tmp_path / "accounting.sqlite3")


def add_document(
    store: SqliteAccountingStore,
    key: str,
    *,
    category: DocumentCategory,
    counterparty: str,
    day: str = "2026-03-15",
    ttc: str = "120.00",
    status: str = "VALIDE",
    **extra: object,
) -> None:
    line = {
        "operation_date": day,
        "label": f"{key}.pdf",
        "counterparty": counterparty,
        "invoice_id": key,
        "invoice_url": f"03_VALIDE_PRET_FEC/{category.value}/{key}.pdf",
        "category": category.value,
        "amount_ht": "100.00",
        "amount_tva": "20.00",
        "amount_ttc": ttc,
        "status": status,
        "document_type": "FACTURE",
    }
    line.update(extra)
    store.upsert_document_line(TENANT, key, line)


def add_bank(
    store: SqliteAccountingStore,
    key: str,
    *,
    counterparty: str,
    day: str = "2026-03-20",
    credit: str | None = "120.00",
    debit: str | None = None,
    label: str | None = None,
    **extra: object,
) -> None:
    store.add_line(
        TENANT,
        key,
        {
            "operation_date": day,
            "label": label or f"VIREMENT {counterparty}",
            "counterparty": counterparty,
            "credit_amount": credit,
            "debit_amount": debit,
            "category": DocumentCategory.BANQUE_FINANCE.value,
            "document_type": "OFX",
            "status": "PENDING",
            **extra,
        },
    )


# -- locked tier directory ---------------------------------------------------


def test_tier_reference_seed_aliases_and_workbook_priority(tmp_path):
    path = tmp_path / TIER_REFERENCE_FILENAME
    service = TierReferenceService(path)
    records = service.bootstrap()
    assert {record.canonical for record in records} >= {
        "APD_INTERNE",
        "CARROSSERIE_YUCEL",
        "PAINTCAR",
        "QREFINISH",
        "BOSCH_SIA",
        "AMAZON",
        "PEAGES_AUTOROUTES",
        "RESTAURANTS_REPAS",
        "STATIONS_CARBURANT",
        "LE_TEMPS_DES_CERISES",
        "TRADEINN",
    }
    assert service.role_for("ASSISTANCE PEINTURES DIFFUSION") == "INTERNE_EXCLUS"
    assert service.canonical_name("CRISOL COLOR S L") == "PAINTCAR"
    assert service.canonical_name("Q REFINISH") == "QREFINISH"
    assert service.canonical_name("AMZ") == "AMAZON"
    assert service.match_reference("LCR ILA193A1") == "PAINTCAR"
    workbook = load_workbook(path)
    sheet = workbook.active
    assert tuple(cell.value for cell in sheet[1]) == COLUMNS
    # The director edits the workbook: add an alias to PAINTCAR.
    paincar_row = next(
        row
        for row in range(2, sheet.max_row + 1)
        if sheet.cell(row, 1).value == "PAINTCAR"
    )
    sheet.cell(paincar_row, 2).value = (
        str(sheet.cell(paincar_row, 2).value) + "; NOUVEL ALIAS PAINTCAR"
    )
    workbook.save(path)
    workbook.close()
    reloaded = TierReferenceService(path)
    reloaded.bootstrap()
    assert reloaded.canonical_name("NOUVEL ALIAS PAINTCAR") == "PAINTCAR"


def test_tier_reference_syncs_sqlite_and_unknown_tier_workflow(tmp_path):
    store = make_store(tmp_path)
    tree = ensure_tree(TENANT, tmp_path)
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    add_document(
        store,
        "UNKNOWN-1",
        category=DocumentCategory.DIVERS_A_CLASSER,
        counterparty="NOUVEAU MAGASIN",
    )
    pipeline = ApdFourStagePipeline(
        TENANT,
        store=store,
        root=tmp_path,
        export_dir=export_dir,
        year=YEAR,
        enable_tier_workflow=True,
    )
    assert store.list_tier_references(TENANT)
    assert pipeline._write_unknown_tier_rows() >= 1
    workbook = pipeline._workbook.workbook_path
    book = load_workbook(workbook)
    sheet = book.active
    headers = [cell.value for cell in sheet[1]]
    assert "Rattacher comme ALIAS de (ou NOUVEAU_TIERS)" in headers
    tier_row = next(
        row
        for row in range(2, sheet.max_row + 1)
        if str(sheet.cell(row, 3).value or "").startswith("TIER__NOUVEAU")
    )
    sha = sheet.cell(tier_row, 2).value
    book.close()
    pipeline._workbook.update_p17_metadata(
        sha, alias_target="AMAZON"
    )
    pipeline.prefill_arbitration_columns()
    pipeline.apply_human_decisions()
    assert pipeline._tier_reference.canonical_name("NOUVEAU MAGASIN") == "AMAZON"
    line = store.get_line(TENANT, "UNKNOWN-1")
    assert line is not None
    assert line["category"] == DocumentCategory.FRAIS_GENERAUX.value


# -- ledger separation, direction and matched pieces ------------------------


def test_client_supplier_separation_direction_and_yucel_matching(tmp_path):
    store = make_store(tmp_path)
    add_document(
        store,
        "FA16423",
        category=DocumentCategory.VENTES_CLIENTS,
        counterparty="Carrosserie Yucell",
        day="2026-01-31",
        ttc="950.76",
    )
    add_bank(
        store,
        "OFX-YUCEL",
        counterparty="CARROSSERIE YUCELL",
        credit="950.76",
        label="VIREMENT CARROSSERIE YUCELL",
    )
    add_document(
        store,
        "AMZ-INV",
        category=DocumentCategory.FRAIS_GENERAUX,
        counterparty="AMAZON PAYMENTS",
        ttc="100.00",
    )
    add_bank(
        store,
        "OFX-AMZ",
        counterparty="AMAZON PAYMENTS",
        credit="36.61",
        label="CB AMAZON PAYMENTS",
        debit=None,
    )
    add_document(
        store,
        "APD-INTERNE",
        category=DocumentCategory.ACHATS_MARCHANDISES,
        counterparty="ASSISTANCE PEINTURES DIFFUSION",
        ttc="500.00",
    )
    client_path = ClientLedgerExporter(
        store,
        export_dir=tmp_path / "exports",
        strict_tiers=True,
        include_matched_pieces=True,
    ).export(TENANT)
    supplier_path = SupplierLedgerExporter(
        store,
        export_dir=tmp_path / "exports",
        strict_tiers=True,
        include_matched_pieces=True,
    ).export_supplier_ledger(TENANT)
    clients = load_workbook(client_path)
    suppliers = load_workbook(supplier_path)
    assert "CARROSSERIE_YUCEL" in clients.sheetnames
    assert "AMAZON" not in clients.sheetnames
    assert not any("ASSISTANCE" in name for name in clients.sheetnames)
    assert "AMAZON" in suppliers.sheetnames
    assert "CARROSSERIE_YUCEL" not in suppliers.sheetnames
    assert not any("ASSISTANCE" in name for name in suppliers.sheetnames)
    client_sheet = clients["CARROSSERIE_YUCEL"]
    assert tuple(cell.value for cell in client_sheet[1]) == CLIENT_LEDGER_COLUMNS_P25
    rows = [row for row in client_sheet.iter_rows(min_row=2, values_only=True)]
    invoice = next(row for row in rows if row[1] == "FACTURE")
    payment = next(row for row in rows if row[1] == "PAIEMENT")
    assert invoice[4] == pytest.approx(950.76) and invoice[5] == 0
    assert payment[4] == 0 and payment[5] == pytest.approx(950.76)
    assert "FA16423" in str(payment[8])
    supplier_sheet = suppliers["AMAZON"]
    assert tuple(cell.value for cell in supplier_sheet[1]) == SUPPLIER_LEDGER_COLUMNS_P25
    supplier_rows = [
        row for row in supplier_sheet.iter_rows(min_row=2, values_only=True)
    ]
    invoice_row = next(row for row in supplier_rows if row[1] == "FACTURE")
    refund_row = next(
        row for row in supplier_rows if row[1] == "AVOIR_REMBOURSEMENT"
    )
    assert invoice_row[4] == 0 and invoice_row[5] == pytest.approx(100.00)
    assert refund_row[4] == pytest.approx(36.61) and refund_row[5] == 0
    clients.close()
    suppliers.close()


def test_payment_dates_in_master_and_category_workbooks(tmp_path):
    store = make_store(tmp_path)
    add_document(
        store,
        "PAID-1",
        category=DocumentCategory.FRAIS_GENERAUX,
        counterparty="AMAZON PAYMENTS",
        day="2026-01-10",
        ttc="120.00",
        reconciliation_status="PAYEE_RAPPROCHEE",
        reconciliation_date="2026-02-15",
    )
    add_document(
        store,
        "UNPAID-1",
        category=DocumentCategory.FRAIS_GENERAUX,
        counterparty="ACTION",
        day="2026-03-10",
        ttc="60.00",
    )
    master = MasterTableExporter(
        store, root=tmp_path, export_dir=tmp_path / "exports"
    ).export(TENANT)
    sheet = load_workbook(master.pieces_path).active
    assert tuple(cell.value for cell in sheet[1]) == MASTER_PIECES_HEADERS
    rows = [row for row in sheet.iter_rows(min_row=2, values_only=True)]
    paid = next(row for row in rows if row[4] == "PAID-1")
    unpaid = next(row for row in rows if row[4] == "UNPAID-1")
    assert paid[1].date() == date(2026, 2, 15)
    assert unpaid[1] == "EN ATTENTE"
    category_path = CategorySummaryExporter(
        store,
        export_dir=tmp_path / "exports",
        include_payment_date=True,
    ).export_category_summary(TENANT, DocumentCategory.FRAIS_GENERAUX)
    category_sheet = load_workbook(category_path).active
    assert category_sheet.cell(1, 2).value == "Date de Paiement"


# -- cleanup -----------------------------------------------------------------


def test_cleanup_phantoms_suffixes_defaults_and_missing_dates(tmp_path):
    store = make_store(tmp_path)
    tree = ensure_tree(TENANT, tmp_path)
    tenant_dir = tree.root / TENANT
    add_document(
        store,
        "BOXTAL 2600023334",
        category=DocumentCategory.TRANSPORTS_LOGISTIQUE,
        counterparty="BOXTAL",
        ttc="45.00",
    )
    store.upsert_document_line(
        TENANT,
        "PHANTOM",
        {
            "category": DocumentCategory.TRANSPORTS_LOGISTIQUE.value,
            "counterparty": "BOXTAL",
            "invoice_id": "BOXTAL 2600023334",
            "status": "PENDING",
        },
    )
    assert dedupe_phantom_lines(store, TENANT) == 1
    assert store.get_line(TENANT, "PHANTOM") is None
    add_document(
        store,
        "FA16222",
        category=DocumentCategory.VENTES_CLIENTS,
        counterparty="Carrosserie Yucell",
    )
    line = store.get_line(TENANT, "FA16222")
    updated = dict(line or {})
    updated.pop("line_id", None)
    updated["operation_date"] = None
    store.upsert_document_line(TENANT, "FA16222", updated)
    assert normalize_missing_dates(store, TENANT) >= 1
    assert store.get_line(TENANT, "FA16222")["operation_date"] == "2025-10-31"
    piece = tenant_dir / "03_VALIDE_PRET_FEC" / "TRANSPORTS_LOGISTIQUE" / "piece__1__1__1.pdf"
    piece.parent.mkdir(parents=True, exist_ok=True)
    piece.write_bytes(b"%PDF-1.4 test")
    renamed, quarantined, errors = clean_pdf_suffixes(tenant_dir, store, TENANT)
    assert renamed == 1 and not errors
    assert (piece.parent / "piece.pdf").is_file()
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    stale = export_dir / "SYNTHESE_default_OLD.xlsx"
    stale.write_bytes(b"x")
    assert remove_default_workbooks(export_dir) == 1
    assert not stale.exists()
