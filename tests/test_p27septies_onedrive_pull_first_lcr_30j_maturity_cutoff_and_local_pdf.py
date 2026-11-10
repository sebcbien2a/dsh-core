"""P27-septies - OneDrive priority, LCR 30-day EOM, exercise year, local PDF.

The director's five new screenshots require:

1. ``pull_from_onedrive()`` to run before any local write and to preserve every
   row of the 13 KB OneDrive ``REFERENTIEL_TIERS_APD.xlsx``;
2. the LCR 30-days-end-of-month maturity rule (no FA16509 on REMISE 93847, no
   FA16409 on REMISE 93696, no four 144.00 EUR CALANDRE_18 fillers on 94694);
3. the explicit ``Exercice Année`` column (2025 for Nov/Dec 2025 invoices);
4. no ``..`` in any PDF link stored in the OneDrive workbooks.
"""

from __future__ import annotations

import inspect
import os
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from services.apd_four_stage_pipeline import ApdFourStagePipeline
from services.bank_reconciliation_service import (
    lcr_invoice_matches_maturity,
    purge_invalid_lcr_remittances,
)
from services.customer_invoice_reconciliation import (
    ReconciliableInvoice,
    find_remittance_combination,
)
from services.onedrive_accounting_bridge_service import (
    TABLEAUX_COMPTABLES_DIR_NAME,
    OneDriveAccountingBridge,
)
from services.sqlite_accounting_store import SqliteAccountingStore
from services.tier_reference_service import TIER_REFERENCE_FILENAME

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "data" / "accounting" / "accounting.sqlite3"
EXPORT_DIR = (
    REPO_ROOT
    / "workspace"
    / "document_flow"
    / "APD"
    / "04_EXPORT_COMPTA"
    / "APD"
)
CLIENT_LEDGER = EXPORT_DIR / "GRAND_LIVRE_CLIENTS_DETAILLES.xlsx"
MASTER_PIECES = EXPORT_DIR / "TABLEAU_MERE_PIECES_COMPTABLES.xlsx"
TENANT = "APD"
READABLE = "_A_COMPLETER.xlsx"


def _tier_workbook(path: Path, names: list[str]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "REFERENTIEL_TIERS"
    sheet.append(
        [
            "Nom Officiel Canonique",
            "ALIAS à regrouper (séparés par ;)",
            "Renommer en (Optionnel)",
            "SIREN / SIRET",
            "IBAN / RIB (séparés par ;)",
            "Rôle",
            "Catégorie par Défaut",
            "Sous-Catégorie",
            "Mode Règlement",
            "Statut",
        ]
    )
    for name in names:
        sheet.append(
            [name, "", "", "", "", "CLIENT", "VENTES_CLIENTS", "", "LCR", "AUTORISE"]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


def _canonicals(path: Path) -> set[str]:
    workbook = load_workbook(path, data_only=True)
    try:
        sheet = workbook.active
        return {
            str(sheet.cell(row=row, column=1).value or "").strip()
            for row in range(2, sheet.max_row + 1)
            if str(sheet.cell(row=row, column=1).value or "").strip()
        }
    finally:
        workbook.close()


def test_pull_from_onedrive_runs_first_and_preserves_remote_rows(tmp_path):
    flow = tmp_path / "workspace" / "document_flow"
    tenant_dir = flow / TENANT
    readable_dir = tenant_dir / "02_A_COMPLETER"
    readable_dir.mkdir(parents=True)
    export_dir = tenant_dir / "04_EXPORT_COMPTA" / TENANT
    export_dir.mkdir(parents=True)
    local_tier = tenant_dir / TIER_REFERENCE_FILENAME
    local_readable = readable_dir / READABLE
    duplicate = tenant_dir / READABLE
    onedrive = tmp_path / "OneDrive" / "Compta_APD" / TABLEAUX_COMPTABLES_DIR_NAME
    onedrive.mkdir(parents=True)

    remote_names = [f"CLIENT_ONEDRIVE_{index:02d}" for index in range(13)]
    local_names = ["CLIENT_LOCAL_SEULEMENT"]
    _tier_workbook(onedrive / TIER_REFERENCE_FILENAME, remote_names)
    _tier_workbook(local_tier, local_names)
    _tier_workbook(local_readable, ["CLIENT_LISIBLE"])
    _tier_workbook(duplicate, ["DOUBLON"])
    # The pipeline already rewrote the local file: local mtime is newer than
    # OneDrive, yet OneDrive must still win.
    os.utime(onedrive / TIER_REFERENCE_FILENAME, (1, 1))
    os.utime(local_tier, (2_000_000_000, 2_000_000_000))

    bridge = OneDriveAccountingBridge(
        TENANT,
        root=flow,
        onedrive_dir=onedrive,
        export_dir=export_dir,
        enabled=True,
    )
    pulled = bridge.pull_from_onedrive()

    merged = _canonicals(local_tier)
    assert set(remote_names) <= merged
    assert "CLIENT_LOCAL_SEULEMENT" in merged
    assert local_tier in pulled
    assert not duplicate.exists()

    run_stage_source = inspect.getsource(ApdFourStagePipeline.run_stage)
    run_stage_lines = [line.strip() for line in run_stage_source.splitlines()]
    assert run_stage_lines.index("self.pull_from_onedrive()") < run_stage_lines.index(
        "if stage not in STAGE_NAMES:"
    )
    run_all_source = inspect.getsource(ApdFourStagePipeline.run_all)
    run_all_lines = [line.strip() for line in run_all_source.splitlines()]
    assert run_all_lines.index("self.pull_from_onedrive()") < run_all_lines.index(
        "reports: list[StageReport] = []"
    )


def _invoice(
    number: str,
    issued: date,
    amount: str,
    customer: str = "CALANDRE_18",
    *,
    credit_note: bool = False,
) -> ReconciliableInvoice:
    return ReconciliableInvoice(
        line_id=f"line-{number}",
        number=number,
        customer=customer,
        amount=Decimal(amount),
        due_date=None,
        payment_method="LCR",
        status="EN_ATTENTE_REGLEMENT",
        invoice_date=issued,
        is_credit_note=credit_note,
    )


def test_lcr_30_days_end_of_month_maturity():
    assert not lcr_invoice_matches_maturity("2025-11-30", "2026-07-29")
    assert not lcr_invoice_matches_maturity("2026-07-31", "2026-08-08")
    assert lcr_invoice_matches_maturity("2026-06-30", "2026-08-08")

    invoices = [
        _invoice("FA16496", date(2026, 6, 30), "1000.00"),
        _invoice("FA16497", date(2026, 6, 30), "144.00"),
        _invoice("FA16509", date(2026, 7, 31), "1017.59"),
        _invoice("FA16409", date(2025, 11, 30), "1084.94"),
        _invoice("FA16410", date(2025, 11, 30), "144.00"),
        _invoice("FA16418", date(2025, 12, 31), "144.00"),
        _invoice("FA16425", date(2026, 1, 31), "144.00"),
        _invoice("FA16441", date(2026, 2, 28), "144.00"),
    ]
    pair = find_remittance_combination(
        invoices,
        Decimal("1144.00"),
        payment_date=date(2026, 8, 8),
        maturity_date=date(2026, 8, 8),
    )
    assert pair is not None
    assert {item.number for item in pair} == {"FA16496", "FA16497"}

    assert (
        find_remittance_combination(
            invoices,
            Decimal("1017.59"),
            payment_date=date(2026, 8, 8),
            maturity_date=date(2026, 8, 8),
        )
        is None
    )
    assert (
        find_remittance_combination(
            invoices,
            Decimal("1084.94"),
            payment_date=date(2026, 7, 29),
            maturity_date=date(2026, 7, 29),
        )
        is None
    )
    assert (
        find_remittance_combination(
            invoices,
            Decimal("576.00"),
            payment_date=date(2026, 10, 21),
            maturity_date=date(2026, 10, 21),
        )
        is None
    )


def test_purge_clears_anachronistic_lcr_lettering(tmp_path):
    store = SqliteAccountingStore(tmp_path / "accounting.sqlite3")
    bank = {
        "line_id": "OFX-93847",
        "label": "REMISE ENCAISSEMENT N 93847",
        "operation_date": "2026-08-08",
        "document_type": "OFX",
        "credit_amount": "9678.93",
        "payment_method": "LCR_ENCAISSEMENT_LOT",
        "reconciliation_status": "PAYEE_RAPPROCHEE",
        "reconciliation_date": "2026-08-08",
        "reconciliation_reference": "REMISE 93847",
        "invoice_id": "FA16509",
    }
    invoice = {
        "line_id": "FA16509-line",
        "invoice_id": "FA16509",
        "label": "Facture FA16509",
        "operation_date": "2026-07-31",
        "document_type": "CUSTOMER_INVOICE",
        "category": "VENTES_CLIENTS",
        "counterparty": "CALANDRE_18",
        "amount_ttc": "1017.59",
        "reconciliation_status": "PAYEE_RAPPROCHEE",
        "reconciliation_date": "2026-08-08",
        "reconciliation_reference": "REMISE 93847",
    }
    store.force_upsert_document_line(TENANT, "OFX-93847", bank)
    store.force_upsert_document_line(TENANT, "FA16509-line", invoice)

    purged = purge_invalid_lcr_remittances(store, TENANT)
    rows = {row["line_id"]: row for row in store.list_master_lines(TENANT)}

    assert purged >= 1
    assert rows["FA16509-line"].get("reconciliation_reference") is None
    assert rows["OFX-93847"].get("invoice_id") is None


def _cell_texts(path: Path) -> list[str]:
    workbook = load_workbook(path, data_only=False)
    try:
        values: list[str] = []
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows():
                for cell in row:
                    if cell.hyperlink is not None:
                        target = cell.hyperlink.target
                        if target:
                            values.append(str(target))
                    if isinstance(cell.value, str) and "PIECES_JOINTES" in cell.value:
                        values.append(cell.value)
        return values
    finally:
        workbook.close()


def test_exercice_annee_column_keeps_2025_for_late_2025_invoices():
    if not CLIENT_LEDGER.is_file():
        pytest.skip("client ledger has not been refreshed yet")
    workbook = load_workbook(CLIENT_LEDGER, data_only=True)
    try:
        headers = [str(cell.value or "") for cell in workbook.worksheets[0][1]]
        assert "Exercice Année" in headers
        year_index = headers.index("Exercice Année") + 1
        reference_index = headers.index("N° Pièce / Réf") + 1
        found = None
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows(min_row=2):
                if str(row[reference_index - 1].value or "").strip() == "FA16409":
                    found = str(row[year_index - 1].value or "").strip()
                    break
            if found is not None:
                break
        assert found == "2025", f"FA16409 Exercice Année = {found!r}"
    finally:
        workbook.close()

    if MASTER_PIECES.is_file():
        workbook = load_workbook(MASTER_PIECES, data_only=True)
        try:
            headers = [
                str(cell.value or "") for cell in workbook.worksheets[0][1]
            ]
            assert "Exercice Année" in headers
        finally:
            workbook.close()


def test_no_parent_segment_in_onedrive_pdf_links():
    configured = os.environ.get("APD_ONEDRIVE_DIR", "").strip()
    onedrive = (
        Path(configured)
        if configured
        else Path.home() / "OneDrive" / "Compta_APD" / TABLEAUX_COMPTABLES_DIR_NAME
    )
    if not onedrive.is_dir():
        pytest.skip("OneDrive Tableaux_Comptables folder is not available")
    offenders: list[tuple[str, str]] = []
    for path in onedrive.glob("*.xlsx"):
        if path.name.startswith("~$"):
            continue
        try:
            texts = _cell_texts(path)
        except Exception:  # noqa: BLE001 - a locked workbook is skipped
            continue
        for text in texts:
            if ".." in text.replace("\\", "/"):
                offenders.append((path.name, text))
    assert offenders == [], f"Parent segments in OneDrive PDF links: {offenders}"


def test_real_ledger_has_no_anachronistic_calandre_remises():
    if not DB_PATH.is_file():
        pytest.skip("APD SQLite ledger is not available")
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        rows = [
            dict(row)
            for row in connection.execute(
                "SELECT invoice_id, operation_date, amount_ttc, "
                "reconciliation_reference, counterparty "
                "FROM reconciliation_lines WHERE tenant_id = ?",
                (TENANT,),
            )
        ]
    finally:
        connection.close()
    offenders = []
    for row in rows:
        reference = str(row.get("reconciliation_reference") or "")
        invoice = str(row.get("invoice_id") or "")
        if reference.endswith("93847") and invoice == "FA16509":
            offenders.append(row)
        if reference.endswith("93696") and invoice == "FA16409":
            offenders.append(row)
    fillers = {
        row["invoice_id"]
        for row in rows
        if str(row.get("reconciliation_reference") or "").endswith("94694")
        and str(row.get("counterparty") or "") == "CALANDRE_18"
        and str(row.get("amount_ttc") or "").startswith("144")
    }
    assert offenders == [], f"Anachronistic remittance lettering: {offenders}"
    assert len(fillers) <= 1, f"Old 144.00 EUR fillers on 94694: {fillers}"
