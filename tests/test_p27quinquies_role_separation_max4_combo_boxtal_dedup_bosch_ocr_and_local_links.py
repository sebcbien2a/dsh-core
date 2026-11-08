"""P27-quinquies - Role separation, max-4 subset-sum, Boxtal, Bosch and links."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

import services.accounting_spreadsheet_export_service as export_service
from schemas.folder_state_schema import DocumentCategory
from services.accounting_memory_learning_service import (
    AccountingMemoryLearningService,
)
from services.accounting_spreadsheet_export_service import (
    ClientLedgerEntry,
    ClientLedgerExporter,
    MasterTableExporter,
    SupplierLedgerExporter,
    _client_subset,
    _subset_for_amount,
)
from services.apd_p27quinquies_repair_service import (
    P27QuinquiesReport,
    repair_p27quinquies,
)
from services.bank_reconciliation_service import (
    MAX_SUBSET_SUM_PIECES,
)
from services.payment_instrument_reconciliation_service import _subset_sum
from services.sqlite_accounting_store import SqliteAccountingStore
from services.tier_reference_service import TierReferenceService

TENANT = "APD"


def make_store(tmp_path: Path) -> SqliteAccountingStore:
    return SqliteAccountingStore(tmp_path / "accounting.sqlite3")


def install_temp_tiers(tmp_path: Path, store: SqliteAccountingStore):
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
    return tiers


def add_document(
    store: SqliteAccountingStore,
    line_id: str,
    *,
    counterparty: str,
    invoice_id: str,
    document_type: str = "FACTURE",
    category: DocumentCategory = DocumentCategory.ACHATS_MARCHANDISES,
    day: str | None = "2026-01-05",
    ttc: str = "120.00",
    status: str = "VALIDE",
    **extra: object,
) -> None:
    line: dict[str, object] = {
        "operation_date": day,
        "label": f"{document_type} {invoice_id}",
        "counterparty": counterparty,
        "invoice_id": invoice_id,
        "category": category.value,
        "document_type": document_type,
        "amount_ht": ttc,
        "amount_tva": "0.00",
        "amount_ttc": ttc,
        "status": status,
    }
    line.update(extra)
    store.force_upsert_document_line(TENANT, line_id, line)


def add_bank(
    store: SqliteAccountingStore,
    line_id: str,
    *,
    counterparty: str,
    day: str,
    debit: str = "0.00",
    credit: str = "0.00",
    label: str = "OPERATION",
    category: DocumentCategory = DocumentCategory.TRANSPORTS_LOGISTIQUE,
    status: str = "PENDING",
) -> None:
    store.add_line(
        TENANT,
        line_id,
        {
            "operation_date": day,
            "label": label,
            "counterparty": counterparty,
            "debit_amount": debit,
            "credit_amount": credit,
            "category": category.value,
            "document_type": "OFX",
            "status": status,
        },
    )


def test_client_ledger_never_contains_supplier_roles_and_has_local_links(tmp_path):
    store = make_store(tmp_path)
    install_temp_tiers(tmp_path, store)
    pieces = tmp_path / "exports" / TENANT / "PIECES_JOINTES" / "VENTES"
    pieces.mkdir(parents=True)
    piece = pieces / "FA17001_2026-01-31_CARROSSERIE_YUCEL.pdf"
    piece.write_bytes(b"%PDF-1.4 test")
    add_document(
        store,
        "SALE-FA17001",
        counterparty="Carrosserie Yucell",
        invoice_id="FA17001",
        document_type="CUSTOMER_INVOICE",
        category=DocumentCategory.VENTES_CLIENTS,
        day="2026-01-31",
        ttc="950.76",
        revenue="1",
        reconciliation_status="PAYEE_RAPPROCHEE",
        reconciliation_date="2026-07-03",
        reconciliation_reference="VIREMENT 20260703141545180335145058189420260703",
        reconciliation_amount="950.76",
    )
    add_bank(
        store,
        "OFX-YUCEL-1",
        counterparty="SARL CARROSSERIE YUCE",
        day="2026-07-03",
        credit="950.76",
        label="VIR INST SARL CARROSSERIE YUCE",
        category=DocumentCategory.VENTES_CLIENTS,
    )
    for index, supplier in enumerate(("BOSCH_SIA", "BOXTAL", "OPENROUTER"), start=1):
        add_document(
            store,
            f"SUP-{index}",
            counterparty=supplier,
            invoice_id=f"SUP-{index}",
            day="2026-02-01",
            ttc="100.00",
            reconciliation_status="PAYEE_RAPPROCHEE",
            reconciliation_date="2026-02-02",
            reconciliation_reference=f"LCR 72593{index} RLV 00000370",
            reconciliation_amount="100.00",
        )
    path = ClientLedgerExporter(
        store,
        export_dir=tmp_path / "exports",
        strict_tiers=True,
        include_matched_pieces=True,
    ).export(TENANT)
    workbook = load_workbook(path)
    assert "BOSCH_SIA" not in workbook.sheetnames
    assert "BOXTAL" not in workbook.sheetnames
    assert "OPENROUTER" not in workbook.sheetnames
    assert any(
        cell.value == "Règlement lettré (registre)"
        for sheet in workbook.worksheets
        for row in sheet.iter_rows()
        for cell in row
    ) is False
    sheet = workbook["CARROSSERIE_YUCEL"]
    assert any(
        cell.hyperlink is not None
        and str(cell.hyperlink.target).startswith("PIECES_JOINTES/")
        for row in sheet.iter_rows(min_row=2)
        for cell in row
    )


def test_ledger_totals_use_clean_utf8_labels(tmp_path):
    store = make_store(tmp_path)
    install_temp_tiers(tmp_path, store)
    add_document(
        store,
        "BOXTAL-1",
        counterparty="BOXTAL",
        invoice_id="2600023334",
        category=DocumentCategory.TRANSPORTS_LOGISTIQUE,
        day="2026-01-12",
        ttc="65.47",
    )
    path = SupplierLedgerExporter(
        store, export_dir=tmp_path / "exports", strict_tiers=True
    ).export_supplier_ledger(TENANT)
    values = [
        str(cell.value or "")
        for sheet in load_workbook(path).worksheets
        for row in sheet.iter_rows()
        for cell in row
    ]
    joined = " ".join(values)
    for broken in ("PI?CES", "OP?RATIONS", "?CART", "D?CAISSEMENTS"):
        assert broken not in joined
    assert "TOTAL PIÈCES (FACTURES - AVOIRS)" in joined
    assert "TOTAL BANQUE (OPÉRATIONS)" in joined


def test_subset_sum_is_strictly_capped_to_four_pieces():
    assert MAX_SUBSET_SUM_PIECES == 4
    documents = [{"line_id": f"D{i}", "amount_ttc": "10.00"} for i in range(5)]
    assert _subset_for_amount(documents, Decimal("50.00")) is None
    assert (
        len(_subset_for_amount(documents[:4], Decimal("40.00")) or []) == 4
    )
    candidates = [
        ({"line_id": f"P{i}", "amount_ttc": "10.00"}, Decimal("10.00"))
        for i in range(5)
    ]
    assert _subset_sum(candidates, Decimal("50.00")) is None
    assert len(_subset_sum(candidates[:4], Decimal("40.00")) or []) == 4
    entries = [
        ClientLedgerEntry(
            entry_type="FACTURE",
            operation_date="2026-01-01",
            reference=f"P{i}",
            label="",
            debit=Decimal("10.00"),
            credit=Decimal("0.00"),
            status="",
            line_id=f"P{i}",
            invoice_id=f"P{i}",
            client="CARROSSERIE_YUCEL",
        )
        for i in range(5)
    ]
    assert _client_subset(entries, Decimal("50.00")) is None


def test_boxtal_case_is_unified_and_invoice_payment_is_one_summary_line(
    tmp_path, monkeypatch
):
    store = make_store(tmp_path)
    install_temp_tiers(tmp_path, store)
    export_root = tmp_path / "flow" / TENANT / "04_EXPORT_COMPTA" / TENANT
    piece = (
        export_root
        / "PIECES_JOINTES"
        / "ACHATS"
        / "BOXTAL_FACTURE_2600023334_2026-01-12.pdf"
    )
    piece.parent.mkdir(parents=True)
    piece.write_bytes(b"%PDF-1.4 boxtal")
    add_document(
        store,
        "SETTLED_BOXTAL_2600023334",
        counterparty="BOXTAL",
        invoice_id="2600023334",
        category=DocumentCategory.TRANSPORTS_LOGISTIQUE,
        day="2026-01-12",
        ttc="65.47",
        piece_pdf="PIECES_JOINTES/ACHATS/BOXTAL_FACTURE_2600023334_2026-01-12.pdf",
    )
    add_bank(
        store,
        "OFX-BOXTAL-1",
        counterparty="Boxtal",
        day="2026-01-15",
        debit="65.47",
        label="CB Boxtal FACT 120126",
    )
    tenant_dir = tmp_path / "flow" / TENANT
    monkeypatch.setenv(
        "DSH_TIER_REFERENCE_PATH",
        str(tmp_path / "REFERENTIEL_TIERS_APD.xlsx"),
    )
    report = repair_p27quinquies(store, TENANT, tenant_dir, export_root)
    assert isinstance(report, P27QuinquiesReport)
    bank = store.get_line(TENANT, "OFX-BOXTAL-1")
    assert bank is not None
    assert bank["counterparty"] == "BOXTAL"
    assert bank["reconciliation_status"] == "PAYEE_RAPPROCHEE"
    assert bank["reconciliation_date"] == "2026-01-15"
    path = export_service.CategorySummaryExporter(
        store,
        export_dir=export_root.parent,
        include_payment_date=True,
    ).export_category_summary(TENANT, DocumentCategory.TRANSPORTS_LOGISTIQUE)
    sheet = load_workbook(path).active
    rows = list(sheet.iter_rows(min_row=2, values_only=True))
    boxtal_rows = [row for row in rows if row[2] == "BOXTAL"]
    assert len(boxtal_rows) == 1
    assert boxtal_rows[0][3] == "2600023334"
    assert str(boxtal_rows[0][1])[:10] == "2026-01-15"
    assert not any(
        row[2] == "BOXTAL" and row[8] == "PENDING" for row in boxtal_rows
    )


def test_27_bosch_invoices_get_real_date_and_non_zero_amounts(tmp_path):
    store = make_store(tmp_path)
    install_temp_tiers(tmp_path, store)
    numbers = [str(3766822382 + index) for index in range(27)]
    for index, number in enumerate(numbers, start=1):
        add_document(
            store,
            f"BOSCH-SIA-FACTURE-{number}",
            counterparty="BOSCH_SIA",
            invoice_id=number,
            day="2026-01-07",
            ttc=f"{100 + index}.00",
            piece_pdf=f"PIECES_JOINTES/ACHATS/BOSCH_SIA_FACTURE_{number}.pdf",
        )
        store.force_upsert_document_line(
            TENANT,
            f"00000000-0000-0000-0000-{index:012d}",
            {
                "operation_date": None,
                "label": f"FACTURE {number}",
                "counterparty": "ROBERT BOSCH - SIA ABRASIVES",
                "invoice_id": f"00000000-0000-0000-0000-{index:012d}",
                "invoice_url": (
                    "03_VALIDE_PRET_FEC/FACTURES_FOURNISSEURS/"
                    f"Y16V_SD_INVOICE_20260107_{number}.pdf"
                ),
                "category": DocumentCategory.ACHATS_MARCHANDISES.value,
                "document_type": "FACTURE",
                "status": "VALIDE",
            },
        )
    report = repair_p27quinquies(
        store,
        TENANT,
        tmp_path / "flow" / TENANT,
        tmp_path / "flow" / TENANT / "04_EXPORT_COMPTA" / TENANT,
    )
    assert report.repaired_bosch == 27
    repaired = 0
    for index, number in enumerate(numbers, start=1):
        row = store.get_line(
            TENANT, f"00000000-0000-0000-0000-{index:012d}"
        )
        assert row is not None
        assert row["operation_date"] == "2026-01-07"
        assert Decimal(str(row["amount_ttc"])) > 0
        assert row["invoice_id"] == number
        repaired += 1
    assert repaired == 27


def test_master_table_excludes_ofx_and_raw_multi_page_batches(tmp_path):
    store = make_store(tmp_path)
    install_temp_tiers(tmp_path, store)
    add_document(
        store,
        "SOURCE-OFX",
        counterparty="CAISSE D'EPARGNE",
        invoice_id="SOURCE-OFX",
        document_type="SOURCE_OFX",
        category=DocumentCategory.BANQUE_FINANCE,
        day="2026-01-01",
        ttc="0.00",
        invoice_url="03_VALIDE_PRET_FEC/LISTES_ET_BORDEREAUX/operations_01012026_03112026.ofx",
    )
    for index, (name, document_type) in enumerate(
        (
            ("factures clients 011225 a 311026.pdf", "CUSTOMER_INVOICE"),
            ("Bordereau_remise_deffet_encaissement.pdf", "BORDEREAU_LCR"),
            ("data_[archived_IC].pdf", "FACTURE"),
        ),
        start=1,
    ):
        add_document(
            store,
            f"BATCH-{index}",
            counterparty="CLIENTS",
            invoice_id=f"LOT-{index}",
            document_type=document_type,
            category=DocumentCategory.VENTES_CLIENTS,
            day="2026-01-05",
            ttc="100.00",
            invoice_url=f"03_VALIDE_PRET_FEC/FACTURES_CLIENTS/{name}",
        )
    add_document(
        store,
        "REAL-INVOICE",
        counterparty="BOXTAL",
        invoice_id="2600023334",
        category=DocumentCategory.TRANSPORTS_LOGISTIQUE,
        day="2026-01-12",
        ttc="65.47",
        invoice_url=(
            "03_VALIDE_PRET_FEC/FACTURES_FOURNISSEURS/"
            "BOXTAL_FACTURE_2600023334_2026-01-12.pdf"
        ),
    )
    export_root = tmp_path / "flow" / TENANT / "04_EXPORT_COMPTA" / TENANT
    piece = (
        export_root
        / "PIECES_JOINTES"
        / "ACHATS"
        / "BOXTAL_FACTURE_2600023334_2026-01-12.pdf"
    )
    piece.parent.mkdir(parents=True)
    piece.write_bytes(b"%PDF-1.4 boxtal")
    store.force_upsert_document_line(
        TENANT,
        "REAL-INVOICE",
        {
            **store.get_line(TENANT, "REAL-INVOICE"),
            "piece_pdf": "PIECES_JOINTES/ACHATS/BOXTAL_FACTURE_2600023334_2026-01-12.pdf",
        },
    )
    report = MasterTableExporter(
        store, root=tmp_path / "flow", export_dir=export_root
    ).export(TENANT)
    sheet = load_workbook(report.achats_path).active
    values = " ".join(
        str(cell.value or "")
        for row in sheet.iter_rows()
        for cell in row
    ).casefold()
    assert "operations_01012026_03112026.ofx" not in values
    assert "factures clients 011225" not in values
    assert "bordereau_remise" not in values
    assert "data_[archived_ic]" not in values
    assert "2600023334" in values
    links = [
        cell.hyperlink.target
        for row in sheet.iter_rows(min_row=2)
        for cell in row
        if cell.hyperlink is not None
    ]
    assert links
    assert all(
        ".." not in str(target) and "onedrive.live.com" not in str(target)
        for target in links
    )
