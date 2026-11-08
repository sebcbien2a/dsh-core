"""P16.2 - Exhaustive tests of the supplier auxiliary ledger export (offline)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from openpyxl import load_workbook

from schemas.folder_state_schema import DocumentCategory
from services.accounting_spreadsheet_export_service import (
    CATEGORY_SUMMARY_COLUMNS,
    SUPPLIER_LEDGER_COLUMNS,
    SUPPLIER_LEDGER_DIAGNOSTIC_TITLE,
    CategorySummaryExporter,
    SupplierLedgerExportError,
    SupplierLedgerExporter,
    export_supplier_ledger,
)
from services.sqlite_accounting_store import SqliteAccountingStore

TENANT = "CLIENT_01"
OTHER_TENANT = "CLIENT_02"
SIA = "sia Abrasives France"
CRISOL = "CRISOL COLOR, S.L."
BOSCH = "Robert Bosch France"
CATEGORY = DocumentCategory.ACHATS_MARCHANDISES


def make_store(tmp_path: Path) -> SqliteAccountingStore:
    return SqliteAccountingStore(tmp_path / "accounting.sqlite3")


def add_document(
    store: SqliteAccountingStore,
    key: str,
    supplier: str = SIA,
    *,
    day: str = "2026-01-05",
    ttc: str = "100.00",
    document_type: str | None = None,
    status: str = "VALIDE",
    tenant: str = TENANT,
    label: str | None = None,
    category: DocumentCategory = CATEGORY,
) -> str:
    line: dict[str, object] = {
        "operation_date": day,
        "label": label if label is not None else f"Piece {key}",
        "counterparty": supplier,
        "invoice_id": key,
        "invoice_url": f"03_VALIDE_PRET_FEC/{category.value}/{key}.pdf",
        "category": category.value,
        "amount_ht": ttc,
        "amount_tva": "0.00",
        "amount_ttc": ttc,
        "status": status,
    }
    if document_type is not None:
        line["document_type"] = document_type
    store.upsert_document_line(tenant, key, line)
    return key


def add_payment(
    store: SqliteAccountingStore,
    key: str,
    supplier: str = SIA,
    *,
    day: str = "2026-01-10",
    amount: str = "100.00",
    tenant: str = TENANT,
    status: str = "PENDING",
    credit: bool = False,
    attach: str | None = None,
) -> str:
    line = {
        "operation_date": day,
        "label": f"Paiement {key}",
        "counterparty": supplier,
        "debit_amount": "0.00" if credit else amount,
        "credit_amount": amount if credit else "0.00",
        "currency": "EUR",
        "status": status,
    }
    store.add_line(tenant, key, line)
    if attach is not None:
        store.arbitrate(
            tenant,
            key,
            {
                "action": "ATTACH_INVOICE",
                "status": "INVOICE_ATTACHED",
                "invoice_id": attach,
            },
        )
    return key


def export(
    store: SqliteAccountingStore,
    tmp_path: Path,
    supplier_query: str | None = None,
    name: str = "grand_livre.xlsx",
) -> Path:
    return SupplierLedgerExporter(store).export_supplier_ledger(
        TENANT, supplier_query, tmp_path / "out" / name
    )


def sheet_of(path: Path, title: str | None = None):
    workbook = load_workbook(path)
    return workbook[title] if title is not None else workbook.active


def ledger_rows(sheet) -> list[tuple]:
    rows: list[tuple] = []
    for row in range(2, sheet.max_row + 1):
        value = sheet.cell(row=row, column=1).value
        if isinstance(value, str) and value.startswith(
            ("TOTAL PIÈCES", "TOTAL PI")
        ):
            break
        rows.append(
            tuple(
                sheet.cell(row=row, column=column).value
                for column in range(1, 15)
            )
        )
    return rows


def total_row_of(sheet) -> int:
    for row in range(2, sheet.max_row + 1):
        value = sheet.cell(row=row, column=1).value
        if isinstance(value, str) and value.startswith(
            ("TOTAL PIÈCES", "TOTAL PI")
        ):
            return row
    raise AssertionError("TOTAL row not found")


def diagnostic_rows(sheet) -> list[tuple]:
    start = None
    for row in range(1, sheet.max_row + 1):
        if sheet.cell(row=row, column=1).value == SUPPLIER_LEDGER_DIAGNOSTIC_TITLE:
            start = row + 2
            break
    assert start is not None
    rows: list[tuple] = []
    for row in range(start, sheet.max_row + 1):
        value = sheet.cell(row=row, column=1).value
        if value is None or str(value).startswith(
            ("Points d'équilibre", "Aucun point", "Aucun paiement")
        ):
            break
        rows.append(tuple(sheet.cell(row=row, column=column).value for column in range(1, 9)))
    return rows


def equilibrium_line(sheet) -> str:
    for row in range(1, sheet.max_row + 1):
        value = sheet.cell(row=row, column=1).value
        if isinstance(value, str) and (
            value.startswith("Points d'équilibre") or value.startswith("Aucun point")
        ):
            return value
    raise AssertionError("equilibrium line not found")


# -- structure ----------------------------------------------------------------


def test_workbook_columns_title_and_layout(tmp_path):
    store = make_store(tmp_path)
    add_document(store, "F1")
    add_payment(store, "PAY-1", amount="100.00")
    path = export(store, tmp_path)
    workbook = load_workbook(path)
    sheet = workbook.active
    assert sheet.title == SIA
    assert tuple(cell.value for cell in sheet[1]) == SUPPLIER_LEDGER_COLUMNS
    assert sheet.freeze_panes == "A2"
    assert list(sheet.merged_cells.ranges) == []
    assert sheet.auto_filter.ref == "A1:N3"
    rows = ledger_rows(sheet)
    assert len(rows) == 2
    assert rows[0][1] == "FACTURE"
    assert rows[0][2] == "F1"
    assert rows[0][3] == "Piece F1"
    assert rows[0][4] == 100.0
    assert rows[0][5] == 0.0
    assert rows[0][7] == 100.0
    assert rows[0][8] == 0
    assert rows[0][9] == 0
    assert rows[0][11] == "NON_LETTRE"
    assert rows[1][1] == "PAIEMENT"
    assert rows[1][8] == 100.0
    assert rows[1][9] == 0.0
    assert rows[1][11] == "A_RAPPROCHER"


def test_balance_formulas_totals_and_equilibrium_marker(tmp_path):
    store = make_store(tmp_path)
    add_document(store, "F1", day="2026-01-01", ttc="100.00")
    add_document(store, "F2", day="2026-01-02", ttc="50.00")
    add_document(store, "A1", day="2026-01-03", ttc="30.00", document_type="CREDIT_NOTE")
    add_payment(store, "PAY-1", day="2026-01-04", amount="120.00")
    sheet = sheet_of(export(store, tmp_path))
    assert [row[1] for row in ledger_rows(sheet)] == [
        "FACTURE",
        "FACTURE",
        "AVOIR",
        "PAIEMENT",
    ]
    assert [row[7] for row in ledger_rows(sheet)] == [100.0, 50.0, -30.0, 0.0]
    assert [row[8] for row in ledger_rows(sheet)] == [0.0, 0.0, 0.0, 120.0]
    assert [row[9] for row in ledger_rows(sheet)] == [0.0, 0.0, 0.0, 0.0]
    assert sheet["K2"].value == "=J2-I2"
    assert sheet["K3"].value == "=K2+J3-I3"
    assert sheet["K4"].value == "=K3+J4-I4"
    assert sheet["K5"].value == "=K4+J5-I5"
    total = total_row_of(sheet)
    assert sheet.cell(row=total, column=1).value.startswith("TOTAL PI")
    assert sheet.cell(row=total, column=5).value == "=SUM(E2:E5)"
    assert sheet.cell(row=total, column=6).value == "=SUM(F2:F5)"
    assert sheet.cell(row=total, column=8).value == "=SUM(H2:H5)"
    assert sheet.cell(row=total + 1, column=9).value == "=SUM(I2:I5)"
    assert sheet.cell(row=total + 1, column=10).value == "=SUM(J2:J5)"
    assert sheet.cell(row=total + 1, column=11).value == "=K5"
    assert sheet.cell(row=total + 1, column=11).comment is None
    assert equilibrium_line(sheet).startswith("Aucun point")


def test_store_query_returns_documents_and_bank_candidates(tmp_path):
    store = make_store(tmp_path)
    add_document(store, "F1", ttc="100.00")
    add_payment(store, "PAY-1", amount="40.00")
    add_payment(store, "PAY-2", day="2026-01-11", amount="20.00", credit=True)
    rows = store.list_supplier_ledger(TENANT, "sia")
    assert [row["line_id"] for row in rows] == ["F1", "PAY-1", "PAY-2"]
    assert rows[0]["document_invoice_id"] == "F1"
    assert rows[1]["raw_status"] == "PENDING"
    assert rows[1]["debit_amount"] == "40.00"
    assert rows[2]["credit_amount"] == "20.00"


def test_atomic_write_leaves_no_temporary_file(tmp_path):
    store = make_store(tmp_path)
    add_document(store, "F1")
    output = tmp_path / "out" / "ledger.xlsx"
    SupplierLedgerExporter(store).export_supplier_ledger(TENANT, None, output)
    assert output.is_file()
    assert [
        path.name for path in output.parent.iterdir() if path.suffix == ".tmp"
    ] == []


def test_credit_note_type_and_negative_amount_both_go_to_debit(tmp_path):
    store = make_store(tmp_path)
    add_document(
        store, "CN-1", day="2026-01-01", ttc="25.00", document_type="CREDIT_NOTE"
    )
    add_document(store, "NEG-1", day="2026-01-02", ttc="-10.00")
    add_document(store, "INV-1", day="2026-01-03", ttc="50.00")
    rows = ledger_rows(sheet_of(export(store, tmp_path)))
    assert [row[1] for row in rows] == ["AVOIR", "AVOIR", "FACTURE"]
    assert [row[4] for row in rows] == [-25.0, -10.0, 50.0]
    assert [row[7] for row in rows] == [-25.0, -10.0, 50.0]
    assert [row[8] for row in rows] == [0.0, 0.0, 0.0]
    assert rows[0][2] == "CN-1"
    assert rows[1][2] == "NEG-1"
    assert rows[2][2] == "INV-1"


def test_document_type_column_is_migrated_and_exposed(tmp_path):
    path = tmp_path / "ledger.sqlite3"
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
            " (tenant_id, line_id, operation_date, label, counterparty, status,"
            " invoice_id, invoice_url, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                TENANT,
                "LEGACY-1",
                "2026-01-01",
                "legacy.pdf",
                SIA,
                "VALIDE",
                "LEGACY-1",
                "03_VALIDE_PRET_FEC/ACHATS_MARCHANDISES/legacy.pdf",
                "2026-01-01T00:00:00+00:00",
            ),
        )
    store = SqliteAccountingStore(path)
    with sqlite3.connect(path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(reconciliation_lines)")
        }
    assert "document_type" in columns
    add_document(store, "F1", ttc="100.00")
    rows = store.list_supplier_ledger(TENANT, "sia")
    assert [row["line_id"] for row in rows] == ["LEGACY-1", "F1"]
    assert rows[0]["document_type"] == ""
    assert rows[1]["document_type"] == ""


# -- filtering and isolation --------------------------------------------------


def test_rejected_documents_and_payments_are_excluded(tmp_path):
    store = make_store(tmp_path)
    add_document(store, "VALID", ttc="100.00")
    add_document(store, "REJET", status="REJETE")
    add_document(store, "REJET-AVOIR", document_type="CREDIT_NOTE", status="REJETE")
    add_payment(store, "PAY-REJ", amount="20.00", status="REJETE")
    store.add_line(
        TENANT,
        "PAY-ARB-REJ",
        {
            "label": "Paiement rejeté",
            "counterparty": SIA,
            "debit_amount": "30.00",
            "credit_amount": "0.00",
        },
    )
    store.arbitrate(
        TENANT,
        "PAY-ARB-REJ",
        {
            "action": "ATTACH_INVOICE",
            "status": "REJETE",
            "invoice_id": "VALID",
        },
    )
    rows = ledger_rows(sheet_of(export(store, tmp_path)))
    assert len(rows) == 1
    assert rows[0][2] == "VALID"


def test_pending_documents_are_not_part_of_the_ledger(tmp_path):
    store = make_store(tmp_path)
    add_document(store, "PENDING", status="PENDING")
    path = export(store, tmp_path)
    sheet = sheet_of(path)
    assert ledger_rows(sheet) == []
    assert sheet.title == "AUCUN_MOUVEMENT"


def test_tenant_isolation(tmp_path):
    store = make_store(tmp_path)
    add_document(store, "OWN", tenant=TENANT)
    add_document(store, "FOREIGN", tenant=OTHER_TENANT)
    rows = ledger_rows(sheet_of(export(store, tmp_path)))
    assert [row[2] for row in rows] == ["OWN"]


def test_all_suppliers_get_one_sheet_each(tmp_path):
    store = make_store(tmp_path)
    add_document(store, "SIA-1", supplier=SIA)
    add_document(store, "BOSCH-1", supplier=BOSCH)
    add_payment(store, "SIA-PAY", supplier=SIA, attach="SIA-1")
    workbook = load_workbook(export(store, tmp_path))
    assert workbook.sheetnames == sorted([SIA, BOSCH], key=str.casefold)
    assert [row[2] for row in ledger_rows(workbook[SIA])] == ["SIA-1", "SIA-PAY"]
    assert [row[2] for row in ledger_rows(workbook[BOSCH])] == ["BOSCH-1"]


def test_supplier_query_is_case_insensitive_partial_match(tmp_path):
    store = make_store(tmp_path)
    add_document(store, "SIA-1", supplier=SIA)
    add_document(store, "BOSCH-1", supplier=BOSCH)
    path = export(store, tmp_path, "SIA")
    workbook = load_workbook(path)
    assert workbook.sheetnames == [SIA]
    assert [row[2] for row in ledger_rows(workbook[SIA])] == ["SIA-1"]


def test_supplier_query_not_found_raises(tmp_path):
    store = make_store(tmp_path)
    add_document(store, "SIA-1", supplier=SIA)
    exporter = SupplierLedgerExporter(store)
    with pytest.raises(SupplierLedgerExportError) as exc:
        exporter.export_supplier_ledger(TENANT, "UNKNOWN")
    assert exc.value.code == "SUPPLIER_NOT_FOUND"


def test_empty_ledger_creates_a_placeholder_sheet(tmp_path):
    store = make_store(tmp_path)
    path = export(store, tmp_path)
    workbook = load_workbook(path)
    assert workbook.sheetnames == ["AUCUN_MOUVEMENT"]
    sheet = workbook.active
    assert tuple(cell.value for cell in sheet[1]) == SUPPLIER_LEDGER_COLUMNS
    assert ledger_rows(sheet) == []
    assert sheet["A2"].value.startswith("TOTAL PI")


def test_invalid_tenant_is_rejected(tmp_path):
    store = make_store(tmp_path)
    exporter = SupplierLedgerExporter(store)
    with pytest.raises(SupplierLedgerExportError) as exc:
        exporter.export_supplier_ledger("..")
    assert exc.value.code == "INVALID_TENANT_ID"


def test_long_supplier_names_produce_unique_sheet_titles(tmp_path):
    store = make_store(tmp_path)
    first = "A" * 40 + "1"
    second = "A" * 40 + "2"
    add_document(store, "ONE", supplier=first)
    add_document(store, "TWO", supplier=second)
    workbook = load_workbook(export(store, tmp_path))
    assert len(workbook.sheetnames) == 2
    assert workbook.sheetnames[0] != workbook.sheetnames[1]
    assert all(len(name) <= 31 for name in workbook.sheetnames)


# -- combinatorial reconciliation diagnostics ---------------------------------


def test_exact_combination_of_invoices_and_credit_notes(tmp_path):
    store = make_store(tmp_path)
    add_document(store, "F1", day="2026-01-01", ttc="100.00")
    add_document(store, "F2", day="2026-01-02", ttc="50.00")
    add_document(
        store, "A1", day="2026-01-03", ttc="30.00", document_type="CREDIT_NOTE"
    )
    add_payment(store, "PAY-1", day="2026-01-04", amount="120.00")
    path = export(store, tmp_path)
    sheet = sheet_of(path)
    rows = diagnostic_rows(sheet)
    assert len(rows) == 1
    assert rows[0][0] == "PAY-1"
    assert rows[0][4] == "EXACT"
    assert rows[0][5] == "F1, F2, A1"
    assert rows[0][6] == 0.0
    assert rows[0][7] == "Combinaison exacte"


def test_matched_documents_are_excluded_from_the_open_pool(tmp_path):
    store = make_store(tmp_path)
    add_document(store, "F1", ttc="100.00")
    add_payment(store, "PAY-1", amount="100.00", attach="F1")
    add_payment(store, "PAY-2", day="2026-01-11", amount="100.00")
    rows = diagnostic_rows(sheet_of(export(store, tmp_path)))
    assert len(rows) == 1
    assert rows[0][0] == "PAY-2"
    assert rows[0][4] == "AUCUNE_PIECE"
    assert rows[0][6] == 100.0
    ledger = ledger_rows(sheet_of(export(store, tmp_path, name="other.xlsx")))
    by_reference = {row[2]: row for row in ledger}
    assert by_reference["F1"][11] == "LETTRE"
    assert by_reference["PAY-1"][11] == "RAPPROCHE"
    assert by_reference["PAY-2"][11] == "A_RAPPROCHER"


def test_residual_delta_identifies_a_missing_credit_note(tmp_path):
    store = make_store(tmp_path)
    add_document(store, "F1", day="2026-01-01", ttc="100.00")
    add_payment(store, "PAY-1", day="2026-01-02", amount="80.00")
    rows = diagnostic_rows(sheet_of(export(store, tmp_path)))
    assert len(rows) == 1
    assert rows[0][4] == "ECART"
    assert rows[0][5] == "F1"
    assert rows[0][6] == -20.0
    assert rows[0][7] == "Avoir manquant / facture modifiée : 20.00"


def test_residual_delta_identifies_a_missing_invoice(tmp_path):
    store = make_store(tmp_path)
    add_document(store, "F1", day="2026-01-01", ttc="100.00")
    add_document(
        store, "A1", day="2026-01-02", ttc="30.00", document_type="CREDIT_NOTE"
    )
    add_payment(store, "PAY-1", day="2026-01-03", amount="80.00")
    rows = diagnostic_rows(sheet_of(export(store, tmp_path)))
    assert len(rows) == 1
    assert rows[0][4] == "ECART"
    assert rows[0][5] == "F1, A1"
    assert rows[0][6] == 10.0
    assert rows[0][7] == "Facture manquante / avoir modifié : 10.00"


def test_residual_delta_reports_no_open_piece(tmp_path):
    store = make_store(tmp_path)
    add_payment(store, "PAY-1", amount="80.00")
    rows = diagnostic_rows(sheet_of(export(store, tmp_path)))
    assert len(rows) == 1
    assert rows[0][4] == "AUCUNE_PIECE"
    assert rows[0][5] == "AUCUNE"
    assert rows[0][6] == 80.0
    assert rows[0][7] == "Aucune pièce ouverte : facture manquante 80.00"


def test_personal_expense_lines_are_not_supplier_ledger_entries(tmp_path):
    store = make_store(tmp_path)
    add_document(store, "F1", ttc="100.00")
    add_payment(store, "PAY-1", amount="50.00")
    store.arbitrate(
        TENANT,
        "PAY-1",
        {
            "action": "PERSONAL_EXPENSE",
            "status": "BOOKED_PERSONAL",
            "debit_account": "108000",
            "credit_account": "455000",
        },
    )
    rows = ledger_rows(sheet_of(export(store, tmp_path)))
    assert [row[2] for row in rows] == ["F1"]


def test_supplier_refund_is_a_credit_line_without_diagnostic(tmp_path):
    store = make_store(tmp_path)
    add_document(store, "F1", ttc="100.00")
    add_payment(store, "REF-1", day="2026-01-11", amount="50.00", credit=True)
    sheet = sheet_of(export(store, tmp_path))
    rows = ledger_rows(sheet)
    assert [row[1] for row in rows] == ["FACTURE", "AVOIR_REMBOURSEMENT"]
    assert rows[1][8] == 0.0
    assert rows[1][9] == 50.0
    assert rows[1][11] == "A_RAPPROCHER"
    assert diagnostic_rows(sheet) == []


# -- coexistence and public wrappers -----------------------------------------


def test_coexistence_with_category_summary(tmp_path):
    store = make_store(tmp_path)
    add_document(store, "F1", ttc="100.00")
    add_document(
        store, "A1", day="2026-01-06", ttc="30.00", document_type="CREDIT_NOTE"
    )
    add_document(store, "REJET", status="REJETE")
    category_path = CategorySummaryExporter(store).export_category_summary(
        TENANT, CATEGORY, tmp_path / "category.xlsx"
    )
    ledger_path = SupplierLedgerExporter(store).export_supplier_ledger(
        TENANT, None, tmp_path / "ledger.xlsx"
    )
    assert category_path.is_file()
    assert ledger_path.is_file()
    category_sheet = load_workbook(category_path).active
    assert tuple(cell.value for cell in category_sheet[1]) == CATEGORY_SUMMARY_COLUMNS
    category_rows = [
        category_sheet.cell(row=row, column=3).value
        for row in range(2, category_sheet.max_row)
        if category_sheet.cell(row=row, column=1).value != "TOTAL"
    ]
    assert category_rows == ["F1", "A1"]
    ledger = ledger_rows(load_workbook(ledger_path).active)
    assert [row[2] for row in ledger] == ["F1", "A1"]


def test_public_wrapper_uses_the_tenant_export_directory(tmp_path):
    store = make_store(tmp_path)
    add_document(store, "F1", supplier=SIA)
    path = export_supplier_ledger(
        TENANT,
        store=store,
        export_dir=tmp_path / "exports",
    )
    assert path == (
        tmp_path
        / "exports"
        / TENANT
        / f"GRAND_LIVRE_{TENANT}_TOUS_FOURNISSEURS.xlsx"
    )
    assert path.is_file()
    query_path = export_supplier_ledger(
        TENANT,
        "Sia",
        store=store,
        export_dir=tmp_path / "exports",
    )
    assert query_path.name == f"GRAND_LIVRE_{TENANT}_Sia.xlsx"
    assert query_path.is_file()


def test_formula_injection_is_neutralised(tmp_path):
    store = make_store(tmp_path)
    add_document(store, "=SUM(A1)", label="=HYPERLINK(\"http://x\")")
    rows = ledger_rows(sheet_of(export(store, tmp_path)))
    assert rows[0][2].startswith("'=")
    assert rows[0][3].startswith("'=")
