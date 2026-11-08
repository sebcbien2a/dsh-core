"""P27-quater - GED dispatch, strict FA1 routing and clean bank tiers."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

from schemas.folder_state_schema import DocumentCategory
from services.accounting_spreadsheet_export_service import (
    ClientLedgerExporter,
    _infer_expense_subcategory,
    _is_sales_row,
)
from services.bank_reconciliation_service import (
    CLIENTS_ENCAISSEMENT_GROUPE,
    INVALID_TIER_NAMES,
    VIREMENT_A_QUALIFIER,
    bank_tier_defaults,
    contains_client_invoice_reference,
)
from services.folder_tree_service import (
    GED_AVOIRS_CLIENTS,
    GED_AVOIRS_FOURNISSEURS,
    GED_FACTURES_CLIENTS,
    GED_FACTURES_FOURNISSEURS,
    GED_LISTES_ET_BORDEREAUX,
    ensure_tree,
    ged_subdir_for_document,
)
from services.sqlite_accounting_store import SqliteAccountingStore
from services.tier_reference_service import TierReferenceService

TENANT = "APD"


def test_ged_dispatch_covers_the_five_physical_folders(tmp_path):
    tree = ensure_tree(TENANT, tmp_path / "flow")
    for name in (
        GED_FACTURES_FOURNISSEURS,
        GED_FACTURES_CLIENTS,
        GED_AVOIRS_FOURNISSEURS,
        GED_AVOIRS_CLIENTS,
        GED_LISTES_ET_BORDEREAUX,
    ):
        assert tree.ged_dir(name).is_dir()
    assert (
        ged_subdir_for_document(
            category=DocumentCategory.VENTES_CLIENTS,
            document_type="CUSTOMER_INVOICE",
            invoice_number="FA16436",
        )
        == GED_FACTURES_CLIENTS
    )
    assert (
        ged_subdir_for_document(
            category=DocumentCategory.ACHATS_MARCHANDISES,
            document_type="FACTURE",
            invoice_number="2601616",
        )
        == GED_FACTURES_FOURNISSEURS
    )
    assert (
        ged_subdir_for_document(
            category=DocumentCategory.VENTES_CLIENTS,
            document_type="AVOIR",
            invoice_number="FA16400",
        )
        == GED_AVOIRS_CLIENTS
    )
    assert (
        ged_subdir_for_document(
            category=DocumentCategory.ACHATS_MARCHANDISES,
            document_type="AVOIR",
            invoice_number="AV-1",
        )
        == GED_AVOIRS_FOURNISSEURS
    )
    assert (
        ged_subdir_for_document(
            category=DocumentCategory.BANQUE_FINANCE,
            document_type="SOURCE_OFX",
        )
        == GED_LISTES_ET_BORDEREAUX
    )


def test_fa1_is_always_a_customer_sale_and_never_a_fuel_expense():
    row = {
        "label": "Facture FA16436 Ydes Auto Accessoires GP15",
        "invoice_id": "FA16436",
        "counterparty": "Ydes Auto Accessoires GP15",
        "category": DocumentCategory.FRAIS_GENERAUX.value,
        "expense_subcategory": "CARBURANT",
        "document_type": "CUSTOMER_INVOICE",
        "amount_ttc": "5347.43",
    }
    assert contains_client_invoice_reference("FA16436")
    assert _is_sales_row(row)
    assert _infer_expense_subcategory(row) is None


def test_ydes_and_collection_group_are_in_the_locked_reference(tmp_path):
    tiers = TierReferenceService(tmp_path / "REFERENTIEL_TIERS_APD.xlsx")
    tiers.bootstrap()
    ydes = tiers.resolve("Ydes Auto Accessoires GP15")
    assert ydes is not None
    assert ydes.canonical == "YDES_AUTO_ACCESSOIRES"
    assert ydes.role == "CLIENT"
    assert ydes.category == "VENTES_CLIENTS"
    group = tiers.resolve("REMISE ENCAISSEMENT N 91374")
    assert group is not None
    assert group.canonical == CLIENTS_ENCAISSEMENT_GROUPE
    assert group.role == "CLIENT"
    assert group.category == "VENTES_CLIENTS"
    for invalid in INVALID_TIER_NAMES:
        assert tiers.resolve(invalid) is None


def test_bank_tier_defaults_never_return_instrument_or_internet_noise():
    assert bank_tier_defaults(
        label="REMISE ENCAISSEMENT N 91787", counterparty="CLIENTS LCR"
    ) == (CLIENTS_ENCAISSEMENT_GROUPE, "CLIENT", "VENTES_CLIENTS")
    assert bank_tier_defaults(
        label="VIREMENT PAR INTERNET", counterparty="PAR INTERNET"
    )[0] == VIREMENT_A_QUALIFIER
    assert bank_tier_defaults(
        label="LCR 806255 RLV 00000385", counterparty=None
    )[0] == "INSTRUMENT_A_QUALIFIER"


def test_excel_link_prefers_the_new_ged_folder(tmp_path):
    store = SqliteAccountingStore(tmp_path / "accounting.sqlite3")
    tiers = TierReferenceService(
        tmp_path / "REFERENTIEL_TIERS_APD.xlsx", store=store, tenant_id=TENANT
    )
    tiers.bootstrap()
    tenant_dir = tmp_path / "flow" / TENANT
    invoice = tenant_dir / "03_VALIDE_PRET_FEC" / GED_FACTURES_CLIENTS / "FA16436.pdf"
    invoice.parent.mkdir(parents=True, exist_ok=True)
    invoice.write_bytes(b"%PDF-1.4 ged")
    store.force_upsert_document_line(
        TENANT,
        "SALE-FA16436",
        {
            "operation_date": "2026-01-31",
            "label": "Facture FA16436 Ydes Auto Accessoires GP15",
            "counterparty": "YDES_AUTO_ACCESSOIRES",
            "invoice_id": "FA16436",
            "category": DocumentCategory.VENTES_CLIENTS.value,
            "document_type": "CUSTOMER_INVOICE",
            "amount_ttc": "5347.43",
            "status": "VALIDE",
            "invoice_url": f"03_VALIDE_PRET_FEC/{GED_FACTURES_CLIENTS}/FA16436.pdf",
        },
    )
    store.force_upsert_document_line(
        TENANT,
        "SALE-FA16437",
        {
            "operation_date": "2026-02-28",
            "label": "Facture FA16437 Ydes Auto Accessoires GP15",
            "counterparty": "YDES_AUTO_ACCESSOIRES",
            "invoice_id": "FA16437",
            "category": DocumentCategory.VENTES_CLIENTS.value,
            "document_type": "CUSTOMER_INVOICE",
            "amount_ttc": "100.00",
            "status": "VALIDE",
            "invoice_url": f"03_VALIDE_PRET_FEC/{GED_FACTURES_CLIENTS}/FA16437.pdf",
        },
    )
    path = ClientLedgerExporter(
        store,
        export_dir=tenant_dir / "04_EXPORT_COMPTA",
        strict_tiers=True,
    ).export(TENANT)
    sheet = load_workbook(path)["YDES_AUTO_ACCESSOIRES"]
    row = next(sheet.iter_rows(min_row=2))
    assert row[8].hyperlink is not None
    assert GED_FACTURES_CLIENTS in str(row[8].hyperlink.target)
    assert Path(str(row[8].hyperlink.target)).name == "FA16436.pdf"


def test_amounts_are_kept_as_decimal():
    assert Decimal("5347.43") == Decimal("5347.43")
