"""P16 - Exhaustive tests of the per-category analytic Excel export (offline)."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest
from openpyxl import load_workbook

from schemas.folder_state_schema import DocumentCategory
from services import accounting_spreadsheet_export_service as export_mod
from services.accounting_spreadsheet_export_service import (
    CATEGORY_SUMMARY_COLUMNS,
    CATEGORY_SUMMARY_TOTAL_LABEL,
    CategorySummaryExportError,
    CategorySummaryExporter,
    export_category_summary,
)
from services.sqlite_accounting_store import (
    AccountingStoreError,
    SqliteAccountingStore,
)

TENANT = "CLIENT_01"
OTHER_TENANT = "CLIENT_02"
TRANSPORT = DocumentCategory.TRANSPORTS_LOGISTIQUE
GENERAL = DocumentCategory.FRAIS_GENERAUX
TRANSPORTS_LOGISTIQUE_VALUE = TRANSPORT.value


def make_store(tmp_path) -> SqliteAccountingStore:
    return SqliteAccountingStore(tmp_path / "accounting.sqlite3")


def make_line(
    category: DocumentCategory = TRANSPORT,
    *,
    line_id: str = "DOC-1",
    day: str | None = "2026-01-05",
    counterparty: str = "TRANSPORTEUR SA",
    ht: str | None = "100.00",
    tva: str | None = "20.00",
    ttc: str | None = "120.00",
    status: str = "VALIDE",
    reference: str | None = None,
    invoice_id: str | None = None,
    debit: str | None = None,
    credit: str | None = None,
) -> dict[str, object]:
    return {
        "operation_date": day,
        "label": f"{line_id}.pdf",
        "debit_amount": debit,
        "credit_amount": credit,
        "counterparty": counterparty,
        "invoice_id": invoice_id if invoice_id is not None else line_id,
        "invoice_url": reference
        if reference is not None
        else f"03_VALIDE_PRET_FEC/{category.value}/{line_id}.pdf",
        "category": category.value,
        "amount_ht": ht,
        "amount_tva": tva,
        "amount_ttc": ttc,
        "status": status,
    }


def add_line(
    store: SqliteAccountingStore,
    category: DocumentCategory = TRANSPORT,
    tenant: str = TENANT,
    key: str | None = None,
    **overrides,
) -> dict[str, object]:
    line = make_line(category, **overrides)
    store.upsert_document_line(tenant, key or str(line["invoice_id"]), line)
    return line


def export(
    store: SqliteAccountingStore,
    tmp_path: Path,
    category: DocumentCategory | str = TRANSPORT,
    tenant: str = TENANT,
    name: str = "summary.xlsx",
) -> Path:
    exporter = CategorySummaryExporter(store)
    return exporter.export_category_summary(
        tenant, category, tmp_path / "out" / name
    )


def sheet_of(path: Path):
    return load_workbook(path).active


def data_values(sheet) -> list[tuple]:
    rows = []
    for row in sheet.iter_rows(min_row=2, max_row=sheet.max_row - 1, values_only=False):
        if row[0].value == CATEGORY_SUMMARY_TOTAL_LABEL:
            continue
        rows.append(tuple(cell.value for cell in row))
    return rows


# -- structure ----------------------------------------------------------------


def test_export_creates_workbook_at_requested_path(tmp_path):
    store = make_store(tmp_path)
    add_line(store)
    path = export(store, tmp_path)
    assert path == tmp_path / "out" / "summary.xlsx"
    assert path.is_file() and path.suffix == ".xlsx"


def test_sheet_title_and_normative_header(tmp_path):
    store = make_store(tmp_path)
    add_line(store)
    sheet = sheet_of(export(store, tmp_path))
    assert sheet.title == "SYNTHESE_TRANSPORTS_LOGISTIQUE"
    assert tuple(cell.value for cell in sheet[1]) == CATEGORY_SUMMARY_COLUMNS


def test_header_has_filter_freeze_and_style(tmp_path):
    store = make_store(tmp_path)
    add_line(store, line_id="DOC-1")
    add_line(store, line_id="DOC-2")
    sheet = sheet_of(export(store, tmp_path))
    assert sheet.auto_filter.ref == "A1:H3"
    assert sheet.freeze_panes == "A2"
    header = sheet["A1"]
    assert header.font.bold is True
    assert str(header.fill.fgColor.rgb).endswith("1F4E78")
    assert header.border.left.style == "thin"


def test_no_merged_cells_in_workbook(tmp_path):
    store = make_store(tmp_path)
    add_line(store)
    sheet = sheet_of(export(store, tmp_path))
    assert list(sheet.merged_cells.ranges) == []


def test_data_cells_are_bordered_and_banded(tmp_path):
    store = make_store(tmp_path)
    add_line(store, line_id="DOC-1")
    add_line(store, line_id="DOC-2")
    sheet = sheet_of(export(store, tmp_path))
    first = sheet["B2"]
    second = sheet["B3"]
    assert all(
        getattr(first.border, side).style == "thin"
        for side in ("left", "right", "top", "bottom")
    )
    assert str(first.fill.fgColor.rgb).endswith("F2F7FB")


def test_column_widths_are_auto_adjusted(tmp_path):
    store = make_store(tmp_path)
    add_line(store, counterparty="TRANSPORTEUR INTERNATIONAL AVEC UN NOM TRES LONG")
    sheet = sheet_of(export(store, tmp_path))
    widths = {
        letter: sheet.column_dimensions[letter].width for letter in "ABCDEFGH"
    }
    assert all(width and width > 0 for width in widths.values())
    assert widths["B"] >= 50
    assert widths["A"] >= 12


# -- filtering ----------------------------------------------------------------


def test_only_requested_category_is_exported(tmp_path):
    store = make_store(tmp_path)
    add_line(store, TRANSPORT, line_id="TRANSPORT-1")
    add_line(store, GENERAL, line_id="GENERAL-1")
    sheet = sheet_of(export(store, tmp_path))
    rows = data_values(sheet)
    assert len(rows) == 1
    assert rows[0][2] == "TRANSPORT-1"


def test_other_category_export_uses_its_own_rows(tmp_path):
    store = make_store(tmp_path)
    add_line(store, TRANSPORT, line_id="TRANSPORT-1")
    add_line(store, GENERAL, line_id="GENERAL-1")
    sheet = sheet_of(export(store, tmp_path, GENERAL))
    rows = data_values(sheet)
    assert len(rows) == 1
    assert rows[0][2] == "GENERAL-1"


def test_rejected_documents_are_strictly_excluded(tmp_path):
    store = make_store(tmp_path)
    add_line(store, line_id="DOC-VALIDE", status="VALIDE")
    add_line(store, line_id="DOC-REJETE", status="REJETE")
    sheet = sheet_of(export(store, tmp_path))
    rows = data_values(sheet)
    assert [row[2] for row in rows] == ["DOC-VALIDE"]
    assert store.list_category_lines(TENANT, TRANSPORT)[0]["line_id"] == "DOC-VALIDE"


def test_rejected_document_with_inferred_category_is_excluded(tmp_path):
    store = make_store(tmp_path)
    store.add_line(
        TENANT,
        "BANK-REJ",
        {
            "invoice_url": "03_VALIDE_PRET_FEC/TRANSPORTS_LOGISTIQUE/rejete.pdf",
            "status": "REJETE",
        },
    )
    assert data_values(sheet_of(export(store, tmp_path))) == []


def test_rejected_status_wins_over_arbitration_decision(tmp_path):
    store = make_store(tmp_path)
    add_line(store, line_id="DOC-1", status="PENDING")
    store.arbitrate(
        TENANT,
        "DOC-1",
        {"action": "ATTACH_INVOICE", "status": "INVOICE_ATTACHED"},
    )
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE reconciliation_lines SET status = 'REJETE'"
            " WHERE tenant_id = ? AND line_id = ?",
            (TENANT, "DOC-1"),
        )
    sheet = sheet_of(export(store, tmp_path))
    assert data_values(sheet) == []
    assert store.list_category_lines(TENANT, TRANSPORT) == []


def test_tenant_isolation(tmp_path):
    store = make_store(tmp_path)
    add_line(store, line_id="OWN", tenant=TENANT)
    add_line(store, line_id="FOREIGN", tenant=OTHER_TENANT)
    rows = data_values(sheet_of(export(store, tmp_path)))
    assert [row[2] for row in rows] == ["OWN"]


def test_category_is_inferred_from_invoice_url(tmp_path):
    store = make_store(tmp_path)
    store.add_line(
        TENANT,
        "BANK-1",
        {
            "invoice_url": "03_VALIDE_PRET_FEC/TRANSPORTS_LOGISTIQUE/x.pdf",
            "status": "VALIDE",
        },
    )
    rows = data_values(sheet_of(export(store, tmp_path)))
    assert [row[2] for row in rows] == ["BANK-1"]


def test_category_inference_requires_an_exact_path_segment(tmp_path):
    store = make_store(tmp_path)
    store.add_line(
        TENANT,
        "BANK-1",
        {
            "invoice_url": "03_VALIDE_PRET_FEC/MON_TRANSPORTS_LOGISTIQUE/x.pdf",
            "status": "VALIDE",
        },
    )
    assert data_values(sheet_of(export(store, tmp_path))) == []


def test_category_inference_handles_underscores(tmp_path):
    store = make_store(tmp_path)
    store.add_line(
        TENANT,
        "BANK-1",
        {
            "invoice_url": "03_VALIDE_PRET_FEC/DIVERS_A_CLASSER/x.pdf",
            "status": "VALIDE",
        },
    )
    assert data_values(sheet_of(export(store, tmp_path, DocumentCategory.DIVERS_A_CLASSER)))
    assert data_values(sheet_of(export(store, tmp_path, TRANSPORT, name="other.xlsx"))) == []


def test_explicit_category_must_match_even_with_category_like_url(tmp_path):
    store = make_store(tmp_path)
    add_line(
        store,
        GENERAL,
        line_id="DOC-1",
        reference="03_VALIDE_PRET_FEC/TRANSPORTS_LOGISTIQUE/DOC-1.pdf",
    )
    assert data_values(sheet_of(export(store, tmp_path))) == []


def test_bank_lines_without_category_are_ignored(tmp_path):
    store = make_store(tmp_path)
    store.add_line(TENANT, "BANK-1", {"label": "Virement", "status": "PENDING"})
    assert data_values(sheet_of(export(store, tmp_path))) == []


def test_rows_are_sorted_by_operation_date(tmp_path):
    store = make_store(tmp_path)
    add_line(store, line_id="LATE", day="2026-03-01")
    add_line(store, line_id="EARLY", day="2026-01-01")
    rows = data_values(sheet_of(export(store, tmp_path)))
    assert [row[0].date() for row in rows] == [date(2026, 1, 1), date(2026, 3, 1)]
    assert [row[2] for row in rows] == ["EARLY", "LATE"]


# -- cells and amounts --------------------------------------------------------


def test_date_cell_is_a_real_date_with_format(tmp_path):
    store = make_store(tmp_path)
    add_line(store)
    sheet = sheet_of(export(store, tmp_path))
    assert sheet["A2"].value.date() == date(2026, 1, 5)
    assert sheet["A2"].number_format == "DD/MM/YYYY"


def test_invalid_date_is_kept_as_text(tmp_path):
    store = make_store(tmp_path)
    add_line(store, day="not-a-date")
    sheet = sheet_of(export(store, tmp_path))
    assert sheet["A2"].value == "not-a-date"


def test_amounts_are_numeric_with_financial_format(tmp_path):
    store = make_store(tmp_path)
    add_line(store, ht="100.10", tva="20.02", ttc="120.12")
    sheet = sheet_of(export(store, tmp_path))
    assert (sheet["E2"].value, sheet["F2"].value, sheet["G2"].value) == (
        100.10,
        20.02,
        120.12,
    )
    for cell in (sheet["E2"], sheet["F2"], sheet["G2"]):
        assert cell.number_format == "#,##0.00"


def test_missing_amounts_stay_blank(tmp_path):
    store = make_store(tmp_path)
    add_line(store, ht=None, tva=None, ttc=None)
    sheet = sheet_of(export(store, tmp_path))
    assert (sheet["E2"].value, sheet["F2"].value, sheet["G2"].value) == (
        None,
        None,
        None,
    )


def test_amounts_fall_back_to_bank_movement_and_vat(tmp_path):
    store = make_store(tmp_path)
    add_line(
        store,
        line_id="DOC-1",
        ht=None,
        tva=None,
        ttc=None,
        debit="120.00",
        status="PENDING",
    )
    store.arbitrate(
        TENANT,
        "DOC-1",
        {
            "action": "ATTACH_INVOICE",
            "status": "INVOICE_ATTACHED",
            "vat_amount": "20.00",
        },
    )
    sheet = sheet_of(export(store, tmp_path))
    assert (sheet["E2"].value, sheet["F2"].value, sheet["G2"].value) == (
        100.0,
        20.0,
        120.0,
    )


def test_amounts_deduce_missing_ttc_from_ht_and_vat(tmp_path):
    store = make_store(tmp_path)
    add_line(store, ht="50.00", tva="10.00", ttc=None)
    sheet = sheet_of(export(store, tmp_path))
    assert sheet["G2"].value == 60.0


def test_amounts_deduce_missing_tva_from_ht_and_ttc(tmp_path):
    store = make_store(tmp_path)
    add_line(store, ht="50.00", tva=None, ttc="60.00")
    sheet = sheet_of(export(store, tmp_path))
    assert sheet["F2"].value == 10.0


def test_tiers_falls_back_to_label(tmp_path):
    store = make_store(tmp_path)
    add_line(store, counterparty="")
    sheet = sheet_of(export(store, tmp_path))
    assert sheet["B2"].value == "DOC-1.pdf"


def test_piece_number_falls_back_to_line_id(tmp_path):
    store = make_store(tmp_path)
    add_line(store, invoice_id="", key="DOC-1")
    sheet = sheet_of(export(store, tmp_path))
    assert sheet["C2"].value == "DOC-1"


def test_reference_column_contains_invoice_url(tmp_path):
    store = make_store(tmp_path)
    add_line(store, line_id="DOC-1")
    sheet = sheet_of(export(store, tmp_path))
    assert (
        sheet["D2"].value
        == "03_VALIDE_PRET_FEC/TRANSPORTS_LOGISTIQUE/DOC-1.pdf"
    )


def test_status_column_exposes_raw_arbitration_status(tmp_path):
    store = make_store(tmp_path)
    add_line(store, line_id="DOC-1", status="PENDING")
    store.arbitrate(
        TENANT,
        "DOC-1",
        {"action": "ATTACH_INVOICE", "status": "INVOICE_ATTACHED"},
    )
    sheet = sheet_of(export(store, tmp_path))
    assert sheet["H2"].value == "INVOICE_ATTACHED"


def test_formula_like_text_is_neutralised(tmp_path):
    store = make_store(tmp_path)
    add_line(store, counterparty="=HYPERLINK(\"http://evil\")", invoice_id="+CMD")
    sheet = sheet_of(export(store, tmp_path))
    assert sheet["B2"].value.startswith("'=")
    assert sheet["B2"].data_type == "s"
    assert sheet["C2"].value.startswith("'+")


# -- totals -------------------------------------------------------------------


def test_total_row_label_and_sum_formulas(tmp_path):
    store = make_store(tmp_path)
    add_line(store, line_id="DOC-1", ht="100.00", tva="20.00", ttc="120.00")
    add_line(store, line_id="DOC-2", ht="250.50", tva="50.10", ttc="300.60")
    sheet = sheet_of(export(store, tmp_path))
    assert sheet["A4"].value == CATEGORY_SUMMARY_TOTAL_LABEL
    assert sheet["E4"].value == "=SUM(E2:E3)"
    assert sheet["F4"].value == "=SUM(F2:F3)"
    assert sheet["G4"].value == "=SUM(G2:G3)"
    for cell in (sheet["E4"], sheet["F4"], sheet["G4"]):
        assert cell.number_format == "#,##0.00"
        assert cell.font.bold is True


def test_total_row_values_match_data_column_sums(tmp_path):
    store = make_store(tmp_path)
    add_line(store, line_id="DOC-1", ht="100.00", tva="20.00", ttc="120.00")
    add_line(store, line_id="DOC-2", ht="250.50", tva="50.10", ttc="300.60")
    sheet = sheet_of(export(store, tmp_path))
    sums = [
        round(sum(sheet.cell(row=row, column=column).value for row in (2, 3)), 2)
        for column in (5, 6, 7)
    ]
    assert sums == [350.5, 70.1, 420.6]


def test_total_row_has_double_top_border(tmp_path):
    store = make_store(tmp_path)
    add_line(store)
    sheet = sheet_of(export(store, tmp_path))
    assert sheet["A3"].border.top.style == "double"
    assert str(sheet["A3"].fill.fgColor.rgb).endswith("DDEBF7")


def test_empty_category_keeps_header_and_zero_totals(tmp_path):
    store = make_store(tmp_path)
    sheet = sheet_of(export(store, tmp_path))
    assert sheet.max_row == 2
    assert tuple(cell.value for cell in sheet[1]) == CATEGORY_SUMMARY_COLUMNS
    assert sheet["A2"].value == CATEGORY_SUMMARY_TOTAL_LABEL
    assert (sheet["E2"].value, sheet["F2"].value, sheet["G2"].value) == (0, 0, 0)
    assert sheet.auto_filter.ref == "A1:H1"


# -- output path, atomicity and validation ------------------------------------


def test_default_output_path_uses_configured_export_dir(tmp_path):
    store = make_store(tmp_path)
    add_line(store)
    exporter = CategorySummaryExporter(store, export_dir=tmp_path / "exports")
    path = exporter.export_category_summary(TENANT, TRANSPORT)
    assert path == (
        tmp_path
        / "exports"
        / TENANT
        / f"SYNTHESE_{TENANT}_TRANSPORTS_LOGISTIQUE.xlsx"
    )
    assert path.is_file()


def test_default_output_path_reads_environment(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    add_line(store)
    target = tmp_path / "env_exports"
    monkeypatch.setenv("DSH_ACCOUNTING_EXPORT_DIR", str(target))
    path = CategorySummaryExporter(store).export_category_summary(TENANT, TRANSPORT)
    assert path.parent == target / TENANT


def test_function_wrapper_uses_injected_store(tmp_path):
    store = make_store(tmp_path)
    add_line(store)
    path = export_category_summary(
        TENANT,
        TRANSPORT,
        tmp_path / "wrapped.xlsx",
        store=store,
    )
    assert path.is_file()


def test_function_wrapper_resolves_shared_store(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    add_line(store)
    monkeypatch.setattr(export_mod, "get_shared_sqlite_store", lambda: store)
    path = export_category_summary(
        TENANT, TRANSPORT, tmp_path / "shared.xlsx"
    )
    assert path.is_file()


def test_export_overwrites_existing_workbook_atomically(tmp_path):
    store = make_store(tmp_path)
    add_line(store, line_id="DOC-1")
    path = export(store, tmp_path)
    assert len(data_values(sheet_of(path))) == 1
    add_line(store, line_id="DOC-2")
    assert export(store, tmp_path) == path
    assert len(data_values(sheet_of(path))) == 2


def test_atomic_write_leaves_no_temporary_file(tmp_path):
    store = make_store(tmp_path)
    add_line(store)
    path = export(store, tmp_path)
    leftovers = [
        item for item in path.parent.iterdir() if item.suffix == ".tmp" or item.name.startswith(".")
    ]
    assert leftovers == []


def test_invalid_category_string_is_rejected(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(CategorySummaryExportError) as info:
        CategorySummaryExporter(store).export_category_summary(
            TENANT, "NOT_A_CATEGORY", tmp_path / "x.xlsx"
        )
    assert info.value.code == "INVALID_CATEGORY"


def test_invalid_category_type_is_rejected(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(CategorySummaryExportError) as info:
        CategorySummaryExporter(store).export_category_summary(
            TENANT, None, tmp_path / "x.xlsx"
        )
    assert info.value.code == "INVALID_CATEGORY"


def test_invalid_tenant_is_rejected(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(CategorySummaryExportError) as info:
        CategorySummaryExporter(store).export_category_summary(
            "../escape", TRANSPORT, tmp_path / "x.xlsx"
        )
    assert info.value.code == "INVALID_TENANT_ID"


def test_output_path_pointing_to_directory_is_rejected(tmp_path):
    store = make_store(tmp_path)
    directory = tmp_path / "a_directory"
    directory.mkdir()
    with pytest.raises(CategorySummaryExportError) as info:
        CategorySummaryExporter(store).export_category_summary(
            TENANT, TRANSPORT, directory
        )
    assert info.value.code == "OUTPUT_IS_DIRECTORY"


# -- store contract -----------------------------------------------------------


def test_store_query_rejects_unknown_category(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(AccountingStoreError) as info:
        store.list_category_lines(TENANT, "UNKNOWN")
    assert info.value.code == "INVALID_CATEGORY"


def test_store_query_returns_amounts_and_raw_status(tmp_path):
    store = make_store(tmp_path)
    add_line(store, line_id="DOC-1")
    (row,) = store.list_category_lines(TENANT, TRANSPORT)
    assert row["category"] == TRANSPORTS_LOGISTIQUE_VALUE
    assert row["amount_ht"] == "100.00"
    assert row["amount_tva"] == "20.00"
    assert row["amount_ttc"] == "120.00"
    assert row["raw_status"] == "VALIDE"


def test_store_query_is_tenant_scoped(tmp_path):
    store = make_store(tmp_path)
    add_line(store, line_id="OWN", tenant=TENANT)
    add_line(store, line_id="FOREIGN", tenant=OTHER_TENANT)
    assert [row["line_id"] for row in store.list_category_lines(TENANT, TRANSPORT)] == [
        "OWN"
    ]


def test_legacy_database_is_migrated_and_query_works(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE reconciliation_lines (
                tenant_id TEXT NOT NULL,
                line_id TEXT NOT NULL,
                operation_date TEXT,
                label TEXT,
                debit_amount TEXT,
                credit_amount TEXT,
                currency TEXT,
                counterparty TEXT,
                status TEXT NOT NULL DEFAULT 'PENDING',
                invoice_id TEXT,
                invoice_url TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, line_id)
            )
            """
        )
        connection.execute(
            "INSERT INTO reconciliation_lines"
            " (tenant_id, line_id, operation_date, label, status, invoice_id,"
            " invoice_url, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                TENANT,
                "LEGACY-1",
                "2026-02-01",
                "legacy.pdf",
                "VALIDE",
                "LEGACY-1",
                "03_VALIDE_PRET_FEC/TRANSPORTS_LOGISTIQUE/legacy.pdf",
                "2026-02-01T00:00:00+00:00",
            ),
        )
    store = SqliteAccountingStore(path)
    with sqlite3.connect(path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(reconciliation_lines)")
        }
    assert {"category", "amount_ht", "amount_tva", "amount_ttc"} <= columns
    (row,) = store.list_category_lines(TENANT, TRANSPORT)
    assert row["line_id"] == "LEGACY-1"
    assert row["category"] == ""
