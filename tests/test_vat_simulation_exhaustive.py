"""P18 - Exhaustive offline coverage of the CA12 VAT simulator.

The suite validates the public contract of
:mod:`services.vat_simulation_service`:

* collected / deductible / fixed-asset VAT aggregation;
* the APD P17 rules (manager-funded expenses keep their deductible VAT,
  ``Soiree Etape`` only deducts meals / breakfast VAT);
* the January / February deferral switch between scenario A and B;
* the next-year half-yearly instalments and the taxable-result impact;
* the generated Excel workbook and the command-line runner.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

from schemas.folder_state_schema import DocumentCategory, ExpenseSubCategory
from scripts.simulate_ca12_apd import main as cli_main
from services.accounting_spreadsheet_export_service import is_personal_expense
from services.sqlite_accounting_store import SqliteAccountingStore
from services.vat_simulation_service import (
    CA12_DETAIL_A_SHEET,
    CA12_DETAIL_B_SHEET,
    CA12_DEFERRED_SHEET,
    CA12_INCOMING_SHEET,
    CA12_OUTPUT_SHEET,
    CA12_SIMULATION_FILENAME,
    CA12_SUMMARY_SHEET,
    Ca12SimulationReport,
    VatSimulationError,
    VatSimulationService,
    ca12_export_path,
    export_ca12_simulation,
    report_payload,
    simulate_ca12,
)

TENANT = "APD"
GENERAL = DocumentCategory.FRAIS_GENERAUX
SALES = DocumentCategory.VENTES_CLIENTS
PURCHASES = DocumentCategory.ACHATS_MARCHANDISES
TRANSPORT = DocumentCategory.TRANSPORTS_LOGISTIQUE
ASSET = DocumentCategory.IMMOBILISATIONS

SOIREE_ETAPE_TEXT = """
HOTEL IBIS LYON
Soiree Etape
Nuitee : 100,00 EUR TVA 10% : 10,00 EUR
Diner : 30,00 EUR TVA 10% : 3,00 EUR
Petit dejeuner : 12,00 EUR TVA 10% : 1,20 EUR
Total TTC 156,20
"""


def make_store(tmp_path: Path) -> SqliteAccountingStore:
    return SqliteAccountingStore(tmp_path / "accounting.sqlite3")


def add_line(
    store: SqliteAccountingStore,
    key: str,
    *,
    category: DocumentCategory = GENERAL,
    day: str = "2026-03-15",
    supplier: str = "FOURNISSEUR",
    label: str | None = None,
    ht: str = "100.00",
    tva: str = "20.00",
    ttc: str = "120.00",
    status: str = "VALIDE",
    subcategory: ExpenseSubCategory | str | None = None,
    **extra: object,
) -> dict[str, object]:
    line: dict[str, object] = {
        "operation_date": day,
        "label": label or f"{key}.pdf",
        "counterparty": supplier,
        "invoice_id": key,
        "invoice_url": f"03_VALIDE_PRET_FEC/{category.value}/{key}.pdf",
        "category": category.value,
        "amount_ht": ht,
        "amount_tva": tva,
        "amount_ttc": ttc,
        "status": status,
        "expense_subcategory": (
            ExpenseSubCategory(subcategory).value
            if subcategory is not None
            else None
        ),
    }
    line.update(extra)
    store.upsert_document_line(TENANT, key, line)
    return line


def simulation(
    store: SqliteAccountingStore,
    tmp_path: Path,
    *,
    year: int = 2026,
) -> Ca12SimulationReport:
    return VatSimulationService(
        store, export_dir=tmp_path / "exports"
    ).simulate(TENANT, year)


def sheet_values(path: Path, sheet: str) -> list[tuple]:
    workbook = load_workbook(path)
    ws = workbook[sheet]
    return [
        tuple(cell.value for cell in row)
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row)
        if any(cell.value not in (None, "") for cell in row)
    ]


def comparison_map(path: Path) -> dict[str, tuple]:
    return {
        row[0]: row
        for row in sheet_values(path, CA12_OUTPUT_SHEET)
    }


# -- collected / deductible aggregation --------------------------------------


def test_collected_deductible_and_asset_vat_are_split(tmp_path):
    store = make_store(tmp_path)
    add_line(store, "SALE-1", category=SALES, ht="1000.00", tva="200.00", ttc="1200.00")
    add_line(store, "BUY-1", category=PURCHASES, ht="500.00", tva="100.00", ttc="600.00")
    add_line(store, "TRA-1", category=TRANSPORT, ht="100.00", tva="10.00", ttc="110.00")
    add_line(store, "GEN-1", category=GENERAL, ht="200.00", tva="20.00", ttc="220.00")
    add_line(store, "AST-1", category=ASSET, ht="1000.00", tva="200.00", ttc="1200.00")

    report = simulation(store, tmp_path)
    scenario = report.scenario_a
    assert scenario.pieces == 5
    assert scenario.collected_tva == Decimal("200.00")
    assert scenario.deductible_achats_marchandises == Decimal("100.00")
    assert scenario.deductible_transports == Decimal("10.00")
    assert scenario.deductible_frais_generaux == Decimal("20.00")
    assert scenario.deductible_immobilisations == Decimal("200.00")
    assert scenario.deductible_goods_services == Decimal("130.00")
    assert scenario.deductible_total == Decimal("330.00")
    assert scenario.net_tva == Decimal("-130.00")
    assert scenario.tva_due == Decimal("0.00")
    assert scenario.credit_tva == Decimal("130.00")
    assert scenario.charges == Decimal("1800.00")
    assert report.scenario_b.tva_due == scenario.tva_due


def test_net_vat_due_and_contribution_balance(tmp_path):
    store = make_store(tmp_path)
    add_line(store, "SALE-1", category=SALES, ht="1000.00", tva="200.00", ttc="1200.00")
    add_line(store, "GEN-1", category=GENERAL, ht="100.00", tva="20.00", ttc="120.00")

    scenario = simulation(store, tmp_path).scenario_a
    assert scenario.collected_tva == Decimal("200.00")
    assert scenario.deductible_total == Decimal("20.00")
    assert scenario.net_tva == Decimal("180.00")
    assert scenario.tva_due == Decimal("180.00")
    assert scenario.credit_tva == Decimal("0.00")
    assert scenario.charges == Decimal("100.00")


def test_credit_vat_when_deductible_exceeds_collected(tmp_path):
    store = make_store(tmp_path)
    add_line(store, "SALE-1", category=SALES, ht="100.00", tva="20.00", ttc="120.00")
    add_line(store, "GEN-1", category=GENERAL, ht="200.00", tva="40.00", ttc="240.00")

    scenario = simulation(store, tmp_path).scenario_a
    assert scenario.net_tva == Decimal("-20.00")
    assert scenario.tva_due == Decimal("0.00")
    assert scenario.credit_tva == Decimal("20.00")
    assert scenario.total_acomptes == Decimal("0.00")


def test_non_simulated_and_non_validated_lines_are_ignored(tmp_path):
    store = make_store(tmp_path)
    add_line(store, "SALE-1", category=SALES, ht="100.00", tva="20.00", ttc="120.00")
    add_line(store, "IGNORED-1", category=DocumentCategory.DIVERS_A_CLASSER)
    add_line(store, "IGNORED-2", category=DocumentCategory.BANQUE_FINANCE)
    add_line(store, "PENDING-1", category=GENERAL, status="PENDING")
    add_line(store, "REJECTED-1", category=GENERAL, status="REJETE")

    scenario = simulation(store, tmp_path).scenario_a
    assert scenario.pieces == 1
    assert scenario.collected_tva == Decimal("20.00")
    assert scenario.deductible_total == Decimal("0.00")


def test_only_the_requested_exercise_is_in_scenario_a(tmp_path):
    store = make_store(tmp_path)
    add_line(store, "OLD", category=SALES, day="2025-12-31", tva="10.00", ttc="110.00")
    add_line(store, "CURRENT", category=SALES, day="2026-06-30", tva="20.00", ttc="120.00")
    add_line(store, "NEXT", category=SALES, day="2027-01-01", tva="30.00", ttc="130.00")

    report = simulation(store, tmp_path, year=2026)
    assert report.scenario_a.pieces == 1
    assert report.scenario_a.collected_tva == Decimal("20.00")


# -- P17 APD rules -----------------------------------------------------------


def test_manager_personal_expense_keeps_deductible_vat_and_cca_455(tmp_path):
    store = make_store(tmp_path)
    add_line(
        store,
        "PERSO-1",
        category=GENERAL,
        payment_source="COMPTE_PERSO_DIRIGEANT",
    )
    line = store.get_line(TENANT, "PERSO-1")
    assert line is not None
    assert is_personal_expense(line) is True
    assert line["counterparty_account"] == "455000_DIRIGEANT"

    report = simulation(store, tmp_path)
    scenario = report.scenario_a
    assert scenario.deductible_frais_generaux == Decimal("20.00")
    assert scenario.deductible_total == Decimal("20.00")
    assert scenario.lines[0].payment_source == "COMPTE_PERSO_DIRIGEANT"
    assert scenario.lines[0].counterparty_account == "455000_DIRIGEANT"
    assert report.scenario_b.deductible_total == Decimal("20.00")


def test_hotel_soiree_etape_only_deducts_meal_vat(tmp_path):
    store = make_store(tmp_path)
    add_line(
        store,
        "HOTEL-1",
        category=GENERAL,
        subcategory=ExpenseSubCategory.HOTELLERIE,
        supplier="IBIS LYON",
        ht="142.00",
        tva="14.20",
        ttc="156.20",
        hotel_place=SOIREE_ETAPE_TEXT,
    )

    scenario = simulation(store, tmp_path).scenario_a
    assert scenario.deductible_frais_generaux == Decimal("4.20")
    assert scenario.deductible_total == Decimal("4.20")
    assert scenario.non_deductible_tva == Decimal("10.00")
    assert scenario.charges == Decimal("152.00")
    line = scenario.lines[0]
    assert line.subcategory == "HOTELLERIE"
    assert line.deductible_tva == Decimal("4.20")
    assert line.non_deductible_tva == Decimal("10.00")


def test_hotel_soiree_etape_explicit_split_fields_win(tmp_path):
    store = make_store(tmp_path)
    add_line(
        store,
        "HOTEL-2",
        category=GENERAL,
        subcategory=ExpenseSubCategory.HOTELLERIE,
        ht="200.00",
        tva="20.00",
        ttc="220.00",
        ht_accommodation="180.00",
        ht_breakfast_meals="20.00",
        tva_deductible="5.00",
        tva_non_deductible="15.00",
    )

    scenario = simulation(store, tmp_path).scenario_a
    assert scenario.deductible_frais_generaux == Decimal("5.00")
    assert scenario.non_deductible_tva == Decimal("15.00")
    assert scenario.charges == Decimal("215.00")


def test_hotel_without_breakdown_keeps_vat_non_deductible(tmp_path):
    store = make_store(tmp_path)
    add_line(
        store,
        "HOTEL-3",
        category=GENERAL,
        subcategory=ExpenseSubCategory.HOTELLERIE,
        ht="100.00",
        tva="10.00",
        ttc="110.00",
    )

    scenario = simulation(store, tmp_path).scenario_a
    assert scenario.deductible_total == Decimal("0.00")
    assert scenario.non_deductible_tva == Decimal("10.00")
    assert scenario.charges == Decimal("110.00")


def test_duplicate_vat_piece_is_counted_once(tmp_path):
    store = make_store(tmp_path)
    add_line(
        store,
        "DUP-1",
        category=GENERAL,
        supplier="BISTRO LYON",
        label="REST-001.pdf",
        invoice_id="REST-001",
    )
    add_line(
        store,
        "DUP-2",
        category=GENERAL,
        supplier="BISTRO LYON",
        label="REST-001.pdf",
        invoice_id="REST-001",
    )

    scenario = simulation(store, tmp_path).scenario_a
    assert scenario.pieces == 1
    assert scenario.deductible_frais_generaux == Decimal("20.00")
    assert scenario.charges == Decimal("100.00")


# -- scenario A / B deferral switch ------------------------------------------


def test_scenario_a_keeps_december_piece_in_original_year(tmp_path):
    store = make_store(tmp_path)
    add_line(store, "SALE-1", category=SALES, ht="1000.00", tva="200.00", ttc="1200.00")
    add_line(
        store,
        "DEC-2026",
        category=GENERAL,
        day="2026-12-31",
        fiscal_year_mode="SUIVANT",
        fiscal_year_target="2027",
        deferred_month_override="01",
    )

    report = simulation(store, tmp_path)
    assert report.scenario_a.pieces == 2
    assert report.scenario_a.deductible_total == Decimal("20.00")
    assert report.scenario_a.tva_due == Decimal("180.00")
    assert len(report.deferred_to_next_year) == 1
    assert report.deferred_to_next_year[0].deferred_to_next_year is True


def test_scenario_b_excludes_january_and_february_reports(tmp_path):
    store = make_store(tmp_path)
    add_line(store, "SALE-1", category=SALES, ht="1000.00", tva="200.00", ttc="1200.00")
    add_line(
        store,
        "JAN-2026",
        category=GENERAL,
        day="2026-01-15",
        fiscal_year_target="2027",
        deferred_month_override="01",
    )
    add_line(
        store,
        "FEB-2026",
        category=GENERAL,
        day="2026-02-15",
        fiscal_year_target="2027",
        deferred_month_override="02",
    )
    add_line(
        store,
        "MARCH-2026",
        category=GENERAL,
        day="2026-03-15",
        fiscal_year_target="2027",
        deferred_month_override="03",
    )

    report = simulation(store, tmp_path)
    assert len(report.deferred_to_next_year) == 2
    assert report.scenario_a.pieces == 4
    assert report.scenario_b.pieces == 2
    assert report.scenario_a.deductible_total == Decimal("60.00")
    assert report.scenario_b.deductible_total == Decimal("20.00")
    assert {line.line_id for line in report.deferred_to_next_year} == {
        "JAN-2026",
        "FEB-2026",
    }


def test_scenario_b_includes_incoming_previous_year_reports(tmp_path):
    store = make_store(tmp_path)
    add_line(
        store,
        "DEC-2025",
        category=GENERAL,
        day="2025-12-20",
        fiscal_year_mode="SUIVANT",
        fiscal_year_target="2026",
        deferred_month_override="02",
    )
    add_line(
        store,
        "NOV-2025",
        category=GENERAL,
        day="2025-11-20",
        fiscal_year_target="2027",
        deferred_month_override="01",
    )

    report = simulation(store, tmp_path, year=2026)
    assert report.scenario_a.pieces == 0
    assert report.scenario_b.pieces == 1
    assert len(report.incoming_from_previous_year) == 1
    assert report.incoming_from_previous_year[0].line_id == "DEC-2025"
    assert report.incoming_from_previous_year[0].incoming_from_previous_year is True


def test_scenario_b_uses_suivant_mode_when_target_is_missing(tmp_path):
    store = make_store(tmp_path)
    add_line(
        store,
        "DEC-2026",
        category=GENERAL,
        day="2026-12-31",
        fiscal_year_mode="SUIVANT",
        deferred_month_override="01",
    )

    report = simulation(store, tmp_path)
    assert report.scenario_a.pieces == 1
    assert report.scenario_b.pieces == 0
    assert len(report.deferred_to_next_year) == 1
    assert report.deferred_to_next_year[0].fiscal_year_target == 2027


def test_scenario_b_uses_suivant_mode_for_incoming_report(tmp_path):
    store = make_store(tmp_path)
    add_line(
        store,
        "DEC-2025",
        category=GENERAL,
        day="2025-12-31",
        fiscal_year_mode="SUIVANT",
        deferred_month_override="02",
    )

    report = simulation(store, tmp_path, year=2026)
    assert report.scenario_a.pieces == 0
    assert report.scenario_b.pieces == 1
    assert report.incoming_from_previous_year[0].fiscal_year_target == 2026


def test_scenario_b_target_without_deferred_month_is_not_moved(tmp_path):
    store = make_store(tmp_path)
    add_line(
        store,
        "DEC-2026",
        category=GENERAL,
        day="2026-12-31",
        fiscal_year_mode="SUIVANT",
        fiscal_year_target="2027",
    )

    report = simulation(store, tmp_path)
    assert report.scenario_a.pieces == 1
    assert report.scenario_b.pieces == 1
    assert report.deferred_to_next_year == ()


def test_scenario_b_is_a_strict_comparison_not_a_mutation(tmp_path):
    store = make_store(tmp_path)
    add_line(
        store,
        "DEC-2026",
        category=GENERAL,
        day="2026-12-31",
        fiscal_year_target="2027",
        deferred_month_override="01",
    )

    report = simulation(store, tmp_path)
    assert report.scenario_a.pieces == 1
    assert report.scenario_b.pieces == 0
    assert report.scenario_a.lines[0].deductible_tva == Decimal("20.00")
    assert report.scenario_b.lines == ()


# -- acomptes and taxable-result impact --------------------------------------


def test_next_year_acomptes_are_55_45_of_due_vat(tmp_path):
    store = make_store(tmp_path)
    add_line(store, "SALE-1", category=SALES, ht="1000.00", tva="200.00", ttc="1200.00")

    scenario = simulation(store, tmp_path).scenario_a
    assert scenario.tva_due == Decimal("200.00")
    assert [item.period for item in scenario.acomptes] == ["JUILLET", "DECEMBRE"]
    assert [item.rate for item in scenario.acomptes] == [
        Decimal("0.55"),
        Decimal("0.45"),
    ]
    assert [item.amount for item in scenario.acomptes] == [
        Decimal("110.00"),
        Decimal("90.00"),
    ]
    assert [item.due_date for item in scenario.acomptes] == [
        date(2027, 7, 31),
        date(2027, 12, 31),
    ]
    assert scenario.total_acomptes == Decimal("200.00")


def test_credit_scenario_has_zero_acomptes(tmp_path):
    store = make_store(tmp_path)
    add_line(store, "SALE-1", category=SALES, ht="100.00", tva="20.00", ttc="120.00")
    add_line(store, "GEN-1", category=GENERAL, ht="200.00", tva="40.00", ttc="240.00")

    report = simulation(store, tmp_path)
    assert report.scenario_a.tva_due == Decimal("0.00")
    assert [item.amount for item in report.scenario_a.acomptes] == [
        Decimal("0.00"),
        Decimal("0.00"),
    ]
    assert report.next_year_acomptes == report.scenario_b.acomptes


def test_taxable_result_impact_equals_deferred_charges(tmp_path):
    store = make_store(tmp_path)
    add_line(store, "SALE-1", category=SALES, ht="1000.00", tva="200.00", ttc="1200.00")
    add_line(
        store,
        "DEC-2026",
        category=GENERAL,
        day="2026-12-31",
        ht="100.00",
        tva="20.00",
        ttc="120.00",
        fiscal_year_target="2027",
        deferred_month_override="01",
    )

    report = simulation(store, tmp_path)
    assert report.scenario_a.charges == Decimal("100.00")
    assert report.scenario_b.charges == Decimal("0.00")
    assert report.taxable_result_impact == Decimal("100.00")
    assert report.deferred_charges == Decimal("100.00")
    assert report.vat_due_delta == Decimal("20.00")


def test_incoming_report_reduces_taxable_result(tmp_path):
    store = make_store(tmp_path)
    add_line(store, "SALE-1", category=SALES, ht="1000.00", tva="200.00", ttc="1200.00")
    add_line(
        store,
        "DEC-2025",
        category=GENERAL,
        day="2025-12-31",
        ht="100.00",
        tva="20.00",
        ttc="120.00",
        fiscal_year_target="2026",
        deferred_month_override="02",
    )

    report = simulation(store, tmp_path, year=2026)
    assert report.scenario_a.charges == Decimal("0.00")
    assert report.scenario_b.charges == Decimal("100.00")
    assert report.taxable_result_impact == Decimal("-100.00")
    assert report.incoming_charges == Decimal("100.00")


# -- export ------------------------------------------------------------------


def test_export_creates_expected_workbook_and_sheets(tmp_path):
    store = make_store(tmp_path)
    add_line(store, "SALE-1", category=SALES, ht="1000.00", tva="200.00", ttc="1200.00")
    add_line(store, "GEN-1", category=GENERAL)
    add_line(
        store,
        "DEC-2026",
        category=GENERAL,
        day="2026-12-31",
        fiscal_year_target="2027",
        deferred_month_override="01",
    )

    service = VatSimulationService(store, export_dir=tmp_path / "exports")
    report = service.simulate(TENANT, 2026)
    path = service.write_report(report)
    expected = (
        tmp_path
        / "exports"
        / TENANT
        / CA12_SIMULATION_FILENAME.format(year=2026)
    )
    assert path == expected
    assert expected.is_file()

    workbook = load_workbook(expected)
    assert workbook.sheetnames == [
        CA12_OUTPUT_SHEET,
        CA12_SUMMARY_SHEET,
        CA12_DETAIL_A_SHEET,
        CA12_DETAIL_B_SHEET,
        CA12_DEFERRED_SHEET,
        CA12_INCOMING_SHEET,
    ]
    assert workbook[CA12_OUTPUT_SHEET]["A1"].value == "Rubrique"
    values = comparison_map(expected)
    assert values["TVA collectee"][1] == 200.0
    assert values["TVA collectee"][2] == 200.0
    assert values["TVA due"][1] == pytest.approx(160.0)
    assert values["TVA due"][2] == pytest.approx(180.0)
    assert values[f"Acompte JUILLET 2027"][1] == pytest.approx(88.0)
    assert values[f"Acompte DECEMBRE 2027"][1] == pytest.approx(72.0)
    assert values["Impact resultat imposable (A - B)"][1] == pytest.approx(100.0)


def test_export_detail_rows_match_scenario_totals(tmp_path):
    store = make_store(tmp_path)
    add_line(store, "SALE-1", category=SALES, ht="1000.00", tva="200.00", ttc="1200.00")
    add_line(store, "GEN-1", category=GENERAL)
    add_line(
        store,
        "DEC-2026",
        category=GENERAL,
        day="2026-12-31",
        fiscal_year_target="2027",
        deferred_month_override="01",
    )
    add_line(
        store,
        "DEC-2025",
        category=GENERAL,
        day="2025-12-20",
        fiscal_year_target="2026",
        deferred_month_override="02",
    )

    service = VatSimulationService(store, export_dir=tmp_path / "exports")
    report = service.simulate(TENANT, 2026)
    path = service.write_report(report)

    assert len(sheet_values(path, CA12_DETAIL_A_SHEET)) == report.scenario_a.pieces
    assert len(sheet_values(path, CA12_DETAIL_B_SHEET)) == report.scenario_b.pieces
    assert len(sheet_values(path, CA12_DEFERRED_SHEET)) == len(
        report.deferred_to_next_year
    )
    assert len(sheet_values(path, CA12_INCOMING_SHEET)) == len(
        report.incoming_from_previous_year
    )
    deferred_rows = sheet_values(path, CA12_DEFERRED_SHEET)
    assert deferred_rows[0][-1] == "REPORTE_N_PLUS_1"
    incoming_rows = sheet_values(path, CA12_INCOMING_SHEET)
    assert incoming_rows[0][-1] == "REPORT_ENTRANT_N"


def test_export_summary_matches_report(tmp_path):
    store = make_store(tmp_path)
    add_line(store, "SALE-1", category=SALES, ht="1000.00", tva="200.00", ttc="1200.00")
    add_line(store, "GEN-1", category=GENERAL)

    service = VatSimulationService(store, export_dir=tmp_path / "exports")
    report = service.simulate(TENANT, 2026)
    path = service.write_report(report)
    summary = {
        row[0]: row[1]
        for row in sheet_values(path, CA12_SUMMARY_SHEET)
    }
    assert summary["Tenant"] == TENANT
    assert summary["Exercice"] == 2026
    assert summary["TVA nette A"] == pytest.approx(
        float(report.scenario_a.net_tva)
    )
    assert summary["TVA nette B"] == pytest.approx(
        float(report.scenario_b.net_tva)
    )


def test_export_convenience_wrapper_and_default_filename(tmp_path):
    store = make_store(tmp_path)
    add_line(store, "SALE-1", category=SALES, ht="1000.00", tva="200.00", ttc="1200.00")
    path = export_ca12_simulation(
        TENANT, 2026, store=store, export_dir=tmp_path / "exports"
    )
    assert path.name == "SIMULATION_CA12_2026.xlsx"
    assert path.parent.name == TENANT


def test_no_data_still_produces_a_valid_workbook(tmp_path):
    store = make_store(tmp_path)
    service = VatSimulationService(store, export_dir=tmp_path / "exports")
    report = service.simulate(TENANT, 2026)
    assert report.scenario_a.pieces == 0
    assert report.scenario_a.tva_due == Decimal("0.00")
    assert report.scenario_a.credit_tva == Decimal("0.00")
    path = service.write_report(report)
    assert path.is_file()
    values = comparison_map(path)
    assert values["TVA due"][1] == pytest.approx(0.0)
    assert values["Pieces"][1] == 0


# -- validation and CLI ------------------------------------------------------


def test_invalid_tenant_is_rejected(tmp_path):
    store = make_store(tmp_path)
    service = VatSimulationService(store, export_dir=tmp_path / "exports")
    with pytest.raises(VatSimulationError) as exc:
        service.simulate("../bad", 2026)
    assert exc.value.code == "INVALID_TENANT_ID"
    with pytest.raises(VatSimulationError) as exc:
        ca12_export_path("../bad", 2026, tmp_path)
    assert exc.value.code == "INVALID_TENANT_ID"


@pytest.mark.parametrize("year", [0, -1, 1800, 2300, True, "2026", None])
def test_invalid_year_is_rejected(tmp_path, year):
    store = make_store(tmp_path)
    service = VatSimulationService(store, export_dir=tmp_path / "exports")
    with pytest.raises(VatSimulationError) as exc:
        service.simulate(TENANT, year)
    assert exc.value.code == "INVALID_YEAR"


def test_export_path_helper_uses_tenant_and_year(tmp_path):
    path = ca12_export_path(TENANT, 2026, tmp_path)
    assert path == tmp_path / TENANT / "SIMULATION_CA12_2026.xlsx"


def test_report_payload_is_json_serialisable(tmp_path):
    store = make_store(tmp_path)
    add_line(store, "SALE-1", category=SALES, ht="1000.00", tva="200.00", ttc="1200.00")
    report = simulation(store, tmp_path)
    payload = report_payload(report)
    assert payload["tenant"] == TENANT
    assert payload["year"] == 2026
    assert payload["scenario_a"]["tva_due"] == "200.00"
    assert json.loads(json.dumps(payload))["scenario_a"]["code"] == "A"


def test_cli_runner_prints_table_and_writes_workbook(tmp_path, capsys):
    store = make_store(tmp_path)
    add_line(store, "SALE-1", category=SALES, ht="1000.00", tva="200.00", ttc="1200.00")
    db_path = store.db_path
    export_dir = tmp_path / "exports"
    exit_code = cli_main(
        [
            "--tenant",
            TENANT,
            "--year",
            "2026",
            "--db",
            str(db_path),
            "--export-dir",
            str(export_dir),
        ]
    )
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "SIMULATION CA12" in output
    assert "TVA due" in output
    assert "SIMULATION_CA12_2026.xlsx" in output
    assert (
        export_dir / TENANT / "SIMULATION_CA12_2026.xlsx"
    ).is_file()


def test_cli_runner_json_option(tmp_path, capsys):
    store = make_store(tmp_path)
    add_line(store, "SALE-1", category=SALES, ht="1000.00", tva="200.00", ttc="1200.00")
    exit_code = cli_main(
        [
            "--tenant",
            TENANT,
            "--year",
            "2026",
            "--db",
            str(store.db_path),
            "--export-dir",
            str(tmp_path / "exports"),
            "--json",
        ]
    )
    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"scenario_a"' in output
    assert '"tva_due": "200.00"' in output


def test_cli_runner_invalid_year_returns_error(tmp_path, capsys):
    exit_code = cli_main(
        [
            "--tenant",
            TENANT,
            "--year",
            "0",
            "--db",
            str(tmp_path / "accounting.sqlite3"),
            "--export-dir",
            str(tmp_path / "exports"),
        ]
    )
    assert exit_code == 2
    assert "INVALID_YEAR" in capsys.readouterr().err
