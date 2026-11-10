"""P27-nonies - cockpit / readable mirror, new analytic buckets, card repairs.

The four screenshots of the director are covered here:

1. ``_A_COMPLETER.xlsx`` mirrors the web cockpit with Date, Source, TTC and
   Sub-Category for every pending line (including unknown bank tiers);
2. ``BPCE IARD`` is an insurance premium, never fuel and never a pending tier;
3. the internal-transfer categories and the promotion/insurance sub-tables
   exist with their dedicated workbooks;
4. ``TABLEAU_INFORMATIQUE_WEB.xlsx`` is clean: Mise Sur Orbite owns invoice
   19544, the ACTION store invoice 18417 is gone, ``DetailRemise_1_`` is gone,
   OpenRouter has one unified line per purchase, and no raw OFX PDF is linked.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

from schemas.accounting_choices import (
    CATEGORY_CHOICES,
    SUBCATEGORY_CHOICES,
)
from schemas.folder_state_schema import (
    DocumentCategory,
    ExpenseSubCategory,
)
from services.acompleter_readable_service import READABLE_HEADERS
from services.accounting_spreadsheet_export_service import (
    CategorySummaryExporter,
    ExpenseSubCategoryExporter,
)
from services.apd_four_stage_pipeline import ApdFourStagePipeline
from services.apd_p27nonies_repair_service import repair_p27nonies
from services.hermes_accounting_bridge import HermesAccountingBridge
from services.sqlite_accounting_store import SqliteAccountingStore
from services.tier_reference_service import TierReferenceService

REPO_ROOT = Path(__file__).resolve().parents[1]
TENANT = "APD"
TENANT_DIR = REPO_ROOT / "workspace" / "document_flow" / TENANT
READABLE = TENANT_DIR / "02_A_COMPLETER" / "_A_COMPLETER.xlsx"
TIER_REFERENCE = TENANT_DIR / "REFERENTIEL_TIERS_APD.xlsx"
EXPORT_DIR = TENANT_DIR / "04_EXPORT_COMPTA" / TENANT
FRAIS_DIR = EXPORT_DIR / "FRAIS_GENERAUX"
INFO_TABLE = FRAIS_DIR / "INFORMATIQUE_WEB" / "TABLEAU_INFORMATIQUE_WEB.xlsx"
INSURANCE_TABLE = FRAIS_DIR / "ASSURANCES_SOCIETE" / "TABLEAU_ASSURANCES_SOCIETE.xlsx"
PROMOTION_TABLE = FRAIS_DIR / "PROMOTION_CADEAUX" / "TABLEAU_PROMOTION_CADEAUX.xlsx"
INTERNAL_SUMMARY = EXPORT_DIR / "SYNTHESE_APD_PAIEMENTS_INTERNES_SOCIETE.xlsx"
PERSO_SUMMARY = EXPORT_DIR / "SYNTHESE_APD_COMPTE_COURANT_PERSO.xlsx"
DB_PATH = REPO_ROOT / "data" / "accounting" / "accounting.sqlite3"


def _data_rows(path: Path) -> list[dict[str, object]]:
    workbook = load_workbook(path, data_only=False)
    try:
        sheet = workbook.active
        headers = [str(cell.value or "") for cell in sheet[1]]
        rows: list[dict[str, object]] = []
        for row_index in range(2, sheet.max_row + 1):
            row: dict[str, object] = {
                "_row": row_index,
                "_sheet": sheet,
            }
            for column, header in enumerate(headers, start=1):
                if header:
                    row[header] = sheet.cell(
                        row=row_index, column=column
                    ).value
            rows.append(row)
        return rows
    finally:
        workbook.close()


def _cents(value: object) -> Decimal:
    return Decimal(str(value or "0")).quantize(Decimal("0.01"))


@pytest.fixture(scope="module")
def store() -> SqliteAccountingStore:
    if not DB_PATH.is_file():
        pytest.skip("accounting SQLite store is not available")
    return SqliteAccountingStore(DB_PATH)


@pytest.fixture(scope="module", autouse=True)
def refreshed_cockpit(store: SqliteAccountingStore):
    """Rebuild the mirror and the Excel tables from the repaired ledger."""
    tier_reference = TierReferenceService(
        TIER_REFERENCE, store=store, tenant_id=TENANT
    )
    tier_reference.bootstrap()
    repair_p27nonies(
        store,
        TENANT,
        tenant_dir=TENANT_DIR,
        export_dir=EXPORT_DIR,
        tier_reference=tier_reference,
    )
    pipeline = ApdFourStagePipeline(
        TENANT,
        store=store,
        export_dir=EXPORT_DIR,
        year=2026,
    )
    pipeline._write_readable_views()
    ExpenseSubCategoryExporter(
        store, root=None, export_dir=EXPORT_DIR
    ).export(TENANT)
    for category in (
        DocumentCategory.PAIEMENT_INTERNE_SOCIETE,
        DocumentCategory.PAIEMENT_INTERNE_PERSO_REMBOURSEMENT,
    ):
        CategorySummaryExporter(
            store, export_dir=EXPORT_DIR.parent, include_payment_date=True
        ).export_category_summary(TENANT, category)
    return pipeline


# 1. readable workbook / cockpit mirror --------------------------------------


def test_readable_workbook_mirrors_cockpit_with_date_source_ttc_and_subcategory():
    if not READABLE.is_file():
        pytest.skip("_A_COMPLETER.xlsx has not been refreshed yet")
    workbook = load_workbook(READABLE, data_only=True)
    try:
        sheet = workbook.active
        headers = [cell.value for cell in sheet[1]]
        assert tuple(headers[: len(READABLE_HEADERS)]) == READABLE_HEADERS
        assert "Sous-Catégorie" in headers
        assert sheet.max_row >= 2
        # The workbook is genuinely formatted: styled header and dropdowns.
        assert sheet.cell(row=1, column=1).font.bold is True
        assert sheet.cell(row=1, column=1).fill.fgColor.rgb.endswith("1F4E78")
        formulas = {
            str(validation.formula1)
            for validation in sheet.data_validations.dataValidation
        }
        assert any("PAIEMENT_INTERNE_SOCIETE" in formula for formula in formulas)
        assert any(
            "PROMOTION_CADEAUX_PUBLICITE" in formula for formula in formulas
        )
        for row_index in range(2, sheet.max_row + 1):
            date_value = sheet.cell(row=row_index, column=1).value
            source = str(sheet.cell(row=row_index, column=2).value or "")
            amount = sheet.cell(row=row_index, column=6).value
            assert date_value not in (None, ""), row_index
            assert source in {"BANQUE_OFX", "PIECE_PDF"}, row_index
            assert amount not in (None, ""), row_index
            assert Decimal(str(amount)) >= Decimal("0.00"), row_index
    finally:
        workbook.close()

    bridge = HermesAccountingBridge(TENANT)
    questions = bridge.list_pending_questions()
    workbook = load_workbook(READABLE, data_only=True)
    try:
        sheet = workbook.active
        assert len(questions) == sheet.max_row - 1
    finally:
        workbook.close()
    for question in questions:
        assert question["date_piece"] not in (None, "")
        assert question["source"] in {"BANQUE_OFX", "PIECE_PDF"}
        assert question["montant_ttc"] is not None
        assert "sous_categorie" in question


def test_bank_tier_rows_expose_real_date_label_and_summed_ttc():
    if not READABLE.is_file():
        pytest.skip("_A_COMPLETER.xlsx has not been refreshed yet")
    rows = _data_rows(READABLE)
    by_name = {
        str(row["Fichier / Réf"]): row
        for row in rows
        if row.get("Fichier / Réf")
    }
    atlandes = by_name.get("TIER__ATLANDES.tier")
    assert atlandes is not None
    assert "->" in str(atlandes["Date"]) and "op." in str(atlandes["Date"])
    assert atlandes["Source"] == "BANQUE_OFX"
    assert _cents(atlandes["Montant TTC (€)"]) == Decimal("17.60")
    assert "CB ATLANDES" in str(atlandes["Libellé / N° Pièce"])

    plenergy = by_name.get("TIER__PLENERGY US 020.tier")
    assert plenergy is not None
    assert plenergy["Source"] == "BANQUE_OFX"
    assert _cents(plenergy["Montant TTC (€)"]) == Decimal("74.11")
    assert "CB PLENERGY" in str(plenergy["Libellé / N° Pièce"])


# 2. BPCE IARD insurance -----------------------------------------------------


def test_bpce_iard_is_insurance_and_not_a_pending_question(refreshed_cockpit):
    reference = TierReferenceService(TIER_REFERENCE)
    reference.bootstrap(write_missing=False)
    record = reference.resolve("PRLV BPCE IARD")
    assert record is not None
    assert record.canonical == "CAISSE_D_EPARGNE_ASSURANCE"
    assert record.category == DocumentCategory.FRAIS_GENERAUX.value
    assert record.subcategory == ExpenseSubCategory.ASSURANCES_SOCIETE.value

    store = SqliteAccountingStore(DB_PATH)
    rows = [
        row
        for row in store.list_master_lines(TENANT)
        if "BPCE IARD" in f"{row.get('counterparty')} {row.get('label')}".upper()
    ]
    assert rows
    for row in rows:
        assert row["category"] == DocumentCategory.FRAIS_GENERAUX.value
        assert row["expense_subcategory"] == "ASSURANCES_SOCIETE"
        assert _cents(row["amount_tva"]) == Decimal("0.00")
        assert _cents(row["amount_ttc"]) > Decimal("0.00")
        assert not str(row.get("piece_pdf") or "").upper().endswith(
            "SOURCE_OFX"
        )

    assert INSURANCE_TABLE.is_file()
    insurance_rows = _data_rows(INSURANCE_TABLE)
    assert insurance_rows
    assert all(
        row["Compagnie / Contrat"] == "CAISSE_D_EPARGNE_ASSURANCE"
        for row in insurance_rows
    )
    assert all(_cents(row["TVA (EXONERE_ASSURANCE_0%)"]) == Decimal("0.00") for row in insurance_rows)

    bridge = HermesAccountingBridge(TENANT)
    pending = bridge.list_pending_questions()
    assert not any(
        "BPCE" in f"{item.get('fournisseur')} {item.get('libelle')}".upper()
        for item in pending
    )


# 3. new categories / sub-categories / summaries -----------------------------


def test_internal_and_promo_categories_exist_with_dedicated_workbooks():
    assert "PAIEMENT_INTERNE_SOCIETE" in CATEGORY_CHOICES
    assert "PAIEMENT_INTERNE_PERSO_REMBOURSEMENT" in CATEGORY_CHOICES
    assert "PROMOTION_CADEAUX_PUBLICITE" in SUBCATEGORY_CHOICES
    assert "ASSURANCES_SOCIETE" in SUBCATEGORY_CHOICES
    assert DocumentCategory("PAIEMENT_INTERNE_SOCIETE").value == (
        "PAIEMENT_INTERNE_SOCIETE"
    )
    assert DocumentCategory("PAIEMENT_INTERNE_PERSO_REMBOURSEMENT").value == (
        "PAIEMENT_INTERNE_PERSO_REMBOURSEMENT"
    )

    assert INTERNAL_SUMMARY.is_file()
    assert PERSO_SUMMARY.is_file()
    assert PROMOTION_TABLE.is_file()
    assert INSURANCE_TABLE.is_file()
    assert PROMOTION_TABLE.name == "TABLEAU_PROMOTION_CADEAUX.xlsx"
    assert INSURANCE_TABLE.name == "TABLEAU_ASSURANCES_SOCIETE.xlsx"

    tiers = load_workbook(TIER_REFERENCE, data_only=True)
    try:
        sheet = tiers.active
        headers = [str(cell.value or "") for cell in sheet[1]]
        subcategory_column = headers.index("Sous-Catégorie") + 1
        formulas = {
            str(validation.formula1)
            for validation in sheet.data_validations.dataValidation
        }
        assert any("PROMOTION_CADEAUX_PUBLICITE" in item for item in formulas)
        assert any("ASSURANCES_SOCIETE" in item for item in formulas)
        records = {}
        for row_index in range(2, sheet.max_row + 1):
            canonical = str(sheet.cell(row=row_index, column=1).value or "")
            if canonical:
                records[canonical] = row_index
        assert "MISE_SUR_ORBITE" in records
        mise_row = records["MISE_SUR_ORBITE"]
        alias_text = str(sheet.cell(row=mise_row, column=2).value or "")
        assert "MISE SUR ORBITE" in alias_text
        assert "SARL MISE SUR ORBITE" in alias_text
        assert str(
            sheet.cell(row=mise_row, column=subcategory_column).value or ""
        ) == "INFORMATIQUE_WEB"
    finally:
        tiers.close()


# 4. TABLEAU_INFORMATIQUE_WEB cleanup ---------------------------------------


def test_informatique_web_table_contains_one_clean_line_per_purchase(refreshed_cockpit):
    assert INFO_TABLE.is_file()
    rows = _data_rows(INFO_TABLE)

    invoice_19544 = [
        row
        for row in rows
        if str(row.get("Description") or "").startswith("19544")
    ]
    assert len(invoice_19544) == 1
    row = invoice_19544[0]
    assert row["Fournisseur"] == "MISE_SUR_ORBITE"
    assert row["Fournisseur"] != "ACTION"
    assert _cents(row["TTC"]) == Decimal("193.20")
    assert _cents(row["HT"]) == Decimal("161.00")
    assert _cents(row["TVA"]) == Decimal("32.20")

    assert not any("18417" in str(row.get("Description") or "") for row in rows)
    assert not any("ACTION" == str(row.get("Fournisseur") or "") for row in rows)
    assert not any(
        "DETAILREMISE" in str(row.get("Description") or "").upper().replace(
            "_", ""
        )
        for row in rows
    )

    openrouter = [
        row
        for row in rows
        if str(row.get("Fournisseur") or "") == "OPENROUTER"
    ]
    descriptions = [str(row.get("Description") or "") for row in openrouter]
    assert not any("Receipt" in text for text in descriptions)
    invoice_0001 = [
        row
        for row in openrouter
        if "Invoice-GENMCYCQ-0001" in str(row.get("Description") or "")
    ]
    invoice_0002 = [
        row
        for row in openrouter
        if "Invoice-GENMCYCQ-0002" in str(row.get("Description") or "")
    ]
    assert len(invoice_0001) == 1
    assert len(invoice_0002) == 1
    assert _cents(invoice_0001[0]["TTC"]) == Decimal("10.74")
    assert _cents(invoice_0002[0]["TTC"]) == Decimal("10.69")
    assert _cents(invoice_0001[0]["TVA"]) == Decimal("0.00")
    assert _cents(invoice_0002[0]["TVA"]) == Decimal("0.00")
    assert "04/11/2026" in str(invoice_0001[0]["Description"])
    assert "07/11/2026" in str(invoice_0002[0]["Description"])

    linked = 0
    for row in rows:
        sheet = row["_sheet"]
        cell = sheet.cell(row=int(row["_row"]), column=8)
        value = str(cell.value or "")
        target = str(cell.hyperlink.target or "") if cell.hyperlink else ""
        blob = f"{value} {target}".upper()
        assert "SOURCE_OFX" not in blob
        if target or "HYPERLINK(" in value.upper():
            linked += 1
            assert "OPENROUTER_INC_FACTURE" in blob or (
                "MISE_SUR_ORBITE_FACTURE" in blob
            )
    assert linked >= 3
