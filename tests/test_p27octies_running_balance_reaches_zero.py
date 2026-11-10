"""P27-octies - progressive running balance must fall back to 0,00 EUR.

The two Grands Livres now compute a true remaining-due balance instead of a
bank-only cumulative balance:

- client (411): ``Solde = Solde_precedent + Montant_TTC - Credit_Banque + Debit_Banque``
- supplier (401): ``Solde = Solde_precedent + Montant_TTC - Debit_Banque + Credit_Banque``

The checks below cover the CALANDRE_18 November 2025 cycle settled by
``REMISE 90949``, the AMAZON 36,61 EUR credit note / bank refund pair and the
OneDrive publication of the regenerated workbooks.
"""

from __future__ import annotations

import hashlib
import os
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

from schemas.folder_state_schema import DocumentCategory
from services.accounting_spreadsheet_export_service import (
    LEDGER_BALANCE_NUMBER_FORMAT,
    LEDGER_BALANCE_ZERO_COLOR,
    SupplierLedgerExporter,
)
from services.sqlite_accounting_store import SqliteAccountingStore

REPO_ROOT = Path(__file__).resolve().parents[1]
TENANT = "APD"
EXPORT_DIR = (
    REPO_ROOT / "workspace" / "document_flow" / "APD" / "04_EXPORT_COMPTA" / "APD"
)
CLIENT_LEDGER = EXPORT_DIR / "GRAND_LIVRE_CLIENTS_DETAILLES.xlsx"
SUPPLIER_LEDGER = EXPORT_DIR / "GRAND_LIVRE_APD_TOUS_FOURNISSEURS.xlsx"
ONEDRIVE_DIR = Path(
    os.environ.get("APD_ONEDRIVE_DIR", "").strip()
    or Path.home() / "OneDrive" / "Compta_APD" / "Tableaux_Comptables"
)
BALANCE_HEADER = "Solde Restant Dû (€)"


def _cents(value: object) -> Decimal:
    return Decimal(str(value or "0")).quantize(Decimal("0.01"))


def _data_rows(sheet) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row_index in range(2, sheet.max_row + 1):
        first = sheet.cell(row=row_index, column=1).value
        if isinstance(first, str) and first.startswith("TOTAL"):
            break
        rows.append(
            {
                "row": row_index,
                "date": str(first or "")[:10],
                "type": str(sheet.cell(row=row_index, column=3).value or ""),
                "reference": str(sheet.cell(row=row_index, column=4).value or ""),
                "ttc": _cents(sheet.cell(row=row_index, column=9).value),
                "debit": _cents(sheet.cell(row=row_index, column=10).value),
                "credit": _cents(sheet.cell(row=row_index, column=11).value),
                "balance_formula": str(
                    sheet.cell(row=row_index, column=12).value or ""
                ),
                "balance_cell": sheet.cell(row=row_index, column=12),
            }
        )
    return rows


def _assert_balance_formula(row: dict[str, object], client: bool) -> None:
    index = int(row["row"])
    if client:
        expected = (
            f"=I{index}-K{index}+J{index}"
            if index == 2
            else f"=L{index - 1}+I{index}-K{index}+J{index}"
        )
    else:
        expected = (
            f"=I{index}-J{index}+K{index}"
            if index == 2
            else f"=L{index - 1}+I{index}-J{index}+K{index}"
        )
    assert row["balance_formula"] == expected


def test_calandre18_november_invoices_lettered_by_remise_90949_reach_zero():
    if not CLIENT_LEDGER.is_file():
        pytest.skip("client ledger has not been refreshed yet")

    workbook = load_workbook(CLIENT_LEDGER, data_only=False)
    try:
        assert "CALANDRE_18" in workbook.sheetnames
        sheet = workbook["CALANDRE_18"]
        assert sheet.cell(row=1, column=12).value == BALANCE_HEADER
        rows = _data_rows(sheet)
    finally:
        workbook.close()

    for row in rows:
        _assert_balance_formula(row, client=True)

    november = [
        row
        for row in rows
        if row["type"] == "FACTURE"
        and row["reference"] in {"FA16409", "FA16410"}
    ]
    remise = [
        row
        for row in rows
        if row["type"] == "PAIEMENT" and "90949" in row["reference"]
    ]
    assert [row["reference"] for row in november] == ["FA16409", "FA16410"]
    assert len(remise) == 2
    assert {row["credit"] for row in remise} == {
        Decimal("1084.94"),
        Decimal("144.00"),
    }

    sequence = sorted((*november, *remise), key=lambda row: int(row["row"]))
    balance = Decimal("0.00")
    balances: list[Decimal] = []
    for row in sequence:
        balance = (
            balance + row["ttc"] - row["credit"] + row["debit"]
        ).quantize(Decimal("0.01"))
        balances.append(balance)

    assert balances == [
        Decimal("1084.94"),
        Decimal("1228.94"),
        Decimal("144.00"),
        Decimal("0.00"),
    ], f"CALANDRE_18 90949 running balance: {balances}"

    settled = sequence[-1]["balance_cell"]
    assert settled.number_format == LEDGER_BALANCE_NUMBER_FORMAT


def test_amazon_credit_note_and_bank_refund_reach_zero(tmp_path):
    store = SqliteAccountingStore(tmp_path / "accounting.sqlite3")
    store.force_upsert_document_line(
        TENANT,
        "AMZ-361",
        {
            "operation_date": "2026-09-01",
            "label": "Facture Amazon 36,61 EUR",
            "counterparty": "AMAZON PAYMENTS",
            "invoice_id": "AMZ-361",
            "category": DocumentCategory.FRAIS_GENERAUX.value,
            "document_type": "FACTURE",
            "amount_ht": "30.51",
            "amount_tva": "6.10",
            "amount_ttc": "36.61",
            "status": "VALIDE",
        },
    )
    store.add_line(
        TENANT,
        "AMZ-PAY-361",
        {
            "operation_date": "2026-09-10",
            "label": "CB AMAZON PAYMENTS",
            "counterparty": "AMAZON PAYMENTS",
            "debit_amount": "36.61",
            "category": DocumentCategory.FRAIS_GENERAUX.value,
            "document_type": "OFX",
            "status": "PENDING",
        },
    )
    store.force_upsert_document_line(
        TENANT,
        "AMZ-AVOIR-361",
        {
            "operation_date": "2026-09-15",
            "label": "Avoir Amazon 36,61 EUR",
            "counterparty": "AMAZON PAYMENTS",
            "invoice_id": "AMZ-AVOIR-361",
            "category": DocumentCategory.FRAIS_GENERAUX.value,
            "document_type": "AVOIR",
            "amount_ht": "30.51",
            "amount_tva": "6.10",
            "amount_ttc": "36.61",
            "status": "VALIDE",
        },
    )
    store.add_line(
        TENANT,
        "AMZ-REFUND-361",
        {
            "operation_date": "2026-09-21",
            "label": "CB AMAZON PAYMENTS",
            "counterparty": "AMAZON PAYMENTS",
            "credit_amount": "36.61",
            "category": DocumentCategory.FRAIS_GENERAUX.value,
            "document_type": "OFX",
            "status": "PENDING",
        },
    )

    path = SupplierLedgerExporter(
        store, export_dir=tmp_path / "exports", strict_tiers=True
    ).export_supplier_ledger(TENANT)
    workbook = load_workbook(path, data_only=False)
    try:
        assert "AMAZON" in workbook.sheetnames
        sheet = workbook["AMAZON"]
        assert sheet.cell(row=1, column=12).value == BALANCE_HEADER
        rows = _data_rows(sheet)
    finally:
        workbook.close()

    assert [row["type"] for row in rows] == [
        "FACTURE",
        "PAIEMENT",
        "AVOIR",
        "AVOIR_REMBOURSEMENT",
    ]
    for row in rows:
        _assert_balance_formula(row, client=False)

    balance = Decimal("0.00")
    balances: list[Decimal] = []
    for row in rows:
        balance = (
            balance + row["ttc"] - row["debit"] + row["credit"]
        ).quantize(Decimal("0.01"))
        balances.append(balance)

    assert balances == [
        Decimal("36.61"),
        Decimal("0.00"),
        Decimal("-36.61"),
        Decimal("0.00"),
    ], f"AMAZON running balance: {balances}"

    refund = rows[-1]["balance_cell"]
    assert refund.number_format == LEDGER_BALANCE_NUMBER_FORMAT
    assert str(refund.font.color.rgb).endswith(LEDGER_BALANCE_ZERO_COLOR)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_onedrive_tableaux_comptables_receives_both_ledgers():
    if not ONEDRIVE_DIR.is_dir():
        pytest.skip("OneDrive Tableaux_Comptables folder is not available")
    for local in (CLIENT_LEDGER, SUPPLIER_LEDGER):
        if not local.is_file():
            pytest.skip(f"{local.name} has not been refreshed yet")
        remote = ONEDRIVE_DIR / local.name
        assert remote.is_file(), f"Missing OneDrive workbook: {remote}"
        assert _sha256(local) == _sha256(remote), (
            f"OneDrive copy is stale for {local.name}"
        )

    workbook = load_workbook(
        ONEDRIVE_DIR / CLIENT_LEDGER.name, read_only=True, data_only=False
    )
    try:
        assert workbook.worksheets[0].cell(row=1, column=12).value == BALANCE_HEADER
    finally:
        workbook.close()
