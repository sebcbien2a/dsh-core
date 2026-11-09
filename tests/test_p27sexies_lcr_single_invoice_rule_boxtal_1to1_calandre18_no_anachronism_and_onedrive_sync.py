"""P27-sexies - strict LCR / Boxtal / Chronopost and client-ledger rules.

These checks run against the real APD SQLite ledger and the freshly generated
``GRAND_LIVRE_CLIENTS_DETAILLES.xlsx`` (produced by ``refresh_cockpit.py``).
They enforce the director's five business rules:

1. no document is paid before its invoice date (no anachronism);
2. Boxtal / Chronopost are strict 1-to-1 (never an invoice combination);
3. a supplier LCR is one invoice, or one invoice minus one credit note;
4. a multi-client LCR remittance uses at most one invoice (or one invoice
   minus one credit note) per customer, unless the bank bordereau explicitly
   lists two distinct effects for that customer;
5. every paid invoice emits a ``PAIEMENT`` row and the per-sheet credit total
   equals the TTC total of the ``PAYEE_RAPPROCHEE`` invoices;
6. the OneDrive ``Tableaux_Comptables`` copy is byte-identical to the local
   workbook.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

from services.lcr_remittance_parser import parse_lcr_remittance_file

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
TENANT = "APD"
PURCHASE_CATEGORIES = {
    "ACHATS_MARCHANDISES",
    "TRANSPORTS_LOGISTIQUE",
    "FRAIS_GENERAUX",
    "FISCAL_SOCIAL",
    "IMMOBILISATIONS",
}


def _fold(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.split()).casefold()


def _client_key(value: object) -> str:
    return " ".join(_fold(value).replace("_", " ").split())


def _rows() -> list[dict]:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM reconciliation_lines WHERE tenant_id = ?", (TENANT,)
            )
        ]
    finally:
        connection.close()


def _is_bank(row: dict) -> bool:
    document_type = str(row.get("document_type") or "").upper()
    return document_type in {"OFX", "QFX"} or str(row.get("line_id") or "").upper().startswith(
        "OFX-"
    )


def _invoice_date(row: dict) -> date | None:
    return _as_date(row.get("operation_date"))


def _payment_date(row: dict) -> date | None:
    return _as_date(row.get("reconciliation_date"))


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _amount(value: object) -> Decimal:
    try:
        return Decimal(str(value or "0").replace(",", "."))
    except Exception:  # noqa: BLE001 - invalid amounts are not positive invoices
        return Decimal("0.00")


def _is_credit_note(row: dict) -> bool:
    return str(row.get("document_type") or "").upper() in {
        "AVOIR",
        "CREDIT_NOTE",
    } or _amount(row.get("amount_ttc")) < 0


def _remise_number(value: object) -> str:
    match = re.search(r"REMISE[^\d]{0,30}(\d{2,})", str(value or ""), re.IGNORECASE)
    return match.group(1) if match else ""


def _bordereaux() -> list:
    candidates: list[Path] = []
    for base in (
        REPO_ROOT / "workspace" / "document_flow" / "APD" / "04_EXPORT_COMPTA",
        REPO_ROOT / "workspace" / "document_flow" / "APD" / "03_VALIDE_PRET_FEC",
    ):
        if not base.is_dir():
            continue
        for path in base.rglob("*.pdf"):
            name = path.name.casefold()
            if "bordereau" in name or "remise" in name or "detailremise" in name:
                candidates.append(path)
    parsed = []
    for path in sorted(dict.fromkeys(candidates), key=lambda item: str(item).casefold()):
        try:
            parsed.append(parse_lcr_remittance_file(path))
        except Exception:  # noqa: BLE001 - an unreadable slip is ignored
            continue
    return parsed


def test_no_invoice_is_paid_before_its_invoice_date():
    rows = _rows()
    offenders = []
    for row in rows:
        if _is_bank(row):
            continue
        invoice_date = _invoice_date(row)
        payment_date = _payment_date(row)
        if invoice_date is None or payment_date is None:
            continue
        if payment_date < invoice_date:
            offenders.append(
                (
                    row.get("invoice_id"),
                    row.get("counterparty"),
                    invoice_date,
                    payment_date,
                    row.get("reconciliation_reference"),
                )
            )
    assert offenders == [], f"Anachronistic lettering: {offenders}"

    workbook = load_workbook(CLIENT_LEDGER, data_only=False)
    workbook_offenders = []
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if row[1] != "FACTURE":
                continue
            invoice_date = _as_date(row[0])
            match = re.search(
                r"Pay[ée]e le (\d{2})/(\d{2})/(\d{4})",
                str(row[12] or ""),
                re.IGNORECASE,
            )
            if invoice_date is None or match is None:
                continue
            payment_date = date(
                int(match.group(3)), int(match.group(2)), int(match.group(1))
            )
            if payment_date < invoice_date:
                workbook_offenders.append(
                    (sheet.title, row[2], invoice_date, payment_date)
                )
    workbook.close()
    assert workbook_offenders == [], (
        f"Anachronistic workbook lettering: {workbook_offenders}"
    )


def test_boxtal_and_chronopost_are_strict_one_to_one():
    rows = _rows()
    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        if _is_bank(row):
            continue
        counterparty = _fold(row.get("counterparty"))
        if "boxtal" not in counterparty and "chronopost" not in counterparty:
            continue
        reference = str(row.get("reconciliation_reference") or "").strip()
        if not reference:
            continue
        groups[(counterparty, reference)].add(str(row.get("invoice_id") or ""))
    offenders = {
        key: sorted(values) for key, values in groups.items() if len(values) > 1
    }
    assert offenders == {}, f"Boxtal/Chronopost invoice combinations: {offenders}"

    bank_offenders = []
    for row in rows:
        if not _is_bank(row):
            continue
        label = _fold(f"{row.get('counterparty')} {row.get('label')}")
        if "boxtal" not in label and "chronopost" not in label:
            continue
        invoice_id = str(row.get("invoice_id") or "")
        if "+" in invoice_id:
            bank_offenders.append((row.get("line_id"), invoice_id))
    assert bank_offenders == [], (
        f"Boxtal/Chronopost bank lines combine invoices: {bank_offenders}"
    )


def test_supplier_lcr_never_combines_two_positive_invoices():
    rows = _rows()
    by_invoice_id: dict[str, dict] = {}
    for row in rows:
        if _is_bank(row):
            continue
        invoice_id = str(row.get("invoice_id") or "")
        if invoice_id:
            by_invoice_id.setdefault(invoice_id, row)

    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if _is_bank(row):
            continue
        reference = str(row.get("reconciliation_reference") or "").strip()
        if reference.upper().startswith("LCR"):
            groups[reference].append(row)
    offenders = {}
    for reference, items in groups.items():
        positives = {
            str(item.get("invoice_id") or "")
            for item in items
            if not _is_credit_note(item)
            and _positive_purchase(item)
        }
        if len(positives) > 1:
            offenders[reference] = sorted(positives)
    assert offenders == {}, f"Supplier LCR combines positive invoices: {offenders}"

    bank_offenders = []
    for row in rows:
        if not _is_bank(row):
            continue
        label = str(row.get("label") or "")
        debit = _amount(row.get("debit_amount"))
        if not label.upper().startswith("LCR") or debit <= 0:
            continue
        invoice_ids = [
            value.strip()
            for value in str(row.get("invoice_id") or "").split("+")
            if value.strip()
        ]
        positives = {
            invoice_id
            for invoice_id in invoice_ids
            if invoice_id in by_invoice_id
            and not _is_credit_note(by_invoice_id[invoice_id])
            and _positive_purchase(by_invoice_id[invoice_id])
        }
        if len(positives) > 1:
            bank_offenders.append((row.get("line_id"), sorted(positives)))
    assert bank_offenders == [], (
        f"Supplier LCR bank lines combine positive invoices: {bank_offenders}"
    )


def _positive_purchase(row: dict) -> bool:
    return (
        str(row.get("category") or "") in PURCHASE_CATEGORIES
        and _amount(row.get("amount_ttc")) > 0
    )


def test_multi_client_lcr_remittance_uses_one_invoice_per_customer():
    rows = _rows()
    bank_rows = [row for row in rows if _is_bank(row)]
    remise_amounts: dict[str, set[Decimal]] = defaultdict(set)
    for row in bank_rows:
        if str(row.get("payment_method") or "").upper() != "LCR_ENCAISSEMENT_LOT":
            continue
        number = _remise_number(row.get("label") or row.get("reconciliation_reference"))
        if number:
            remise_amounts[number].add(
                abs(_amount(row.get("credit_amount")))
            )

    bordereau_by_amount: dict[Decimal, dict[str, int]] = {}
    for remittance in _bordereaux():
        total = remittance.total_amount or remittance.computed_total
        counts: dict[str, int] = defaultdict(int)
        for line in remittance.lines:
            counts[_client_key(line.drawer)] += 1
        bordereau_by_amount[total] = dict(counts)

    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        if _is_bank(row):
            continue
        reference = str(row.get("reconciliation_reference") or "")
        number = _remise_number(reference)
        if not number or number not in remise_amounts:
            continue
        groups[(number, _client_key(row.get("counterparty")))].add(
            str(row.get("invoice_id") or "")
        )

    offenders = {}
    for (number, client), invoice_ids in groups.items():
        allowance = 1
        for amount in remise_amounts.get(number, set()):
            counts = bordereau_by_amount.get(amount)
            if counts is not None and client in counts:
                allowance = max(1, counts[client])
        if len(invoice_ids) > allowance:
            offenders[(number, client)] = sorted(invoice_ids)
    assert offenders == {}, f"Remittance customer over-allocation: {offenders}"

    calandre_small = {
        str(row.get("invoice_id"))
        for row in rows
        if not _is_bank(row)
        and _fold(row.get("counterparty")) == _fold("CALANDRE_18")
        and _amount(row.get("amount_ttc")) == Decimal("144.00")
        and _remise_number(row.get("reconciliation_reference")) == "94694"
    }
    assert len(calandre_small) <= 1, (
        f"CALANDRE_18 144 EUR filler on REMISE 94694: {sorted(calandre_small)}"
    )


def test_every_client_sheet_payment_credit_equals_paid_invoice_ttc():
    workbook = load_workbook(CLIENT_LEDGER, data_only=False)
    issues = []
    for sheet in workbook.worksheets:
        payments = Decimal("0.00")
        paid_invoices = Decimal("0.00")
        payment_rows = 0
        paid_invoice_rows = 0
        for row in sheet.iter_rows(min_row=2, values_only=True):
            entry_type = row[1]
            if entry_type == "PAIEMENT":
                payments += _amount(row[9])
                payment_rows += 1
                status = str(row[11] or "").upper()
                matched = str(row[12] or "")
                if "LETTRE" in status and "NON RAPPROCHEE" in matched.upper():
                    issues.append((sheet.title, "LETTRE without invoice", row[2]))
                if "REMISE" in status and not re.search(
                    r"R[èe]gle Facture\s*:\s*FA", matched, re.IGNORECASE
                ):
                    issues.append((sheet.title, "missing FA rule", row[2]))
            elif entry_type == "FACTURE":
                if "PAYEE_RAPPROCHEE" in str(row[11] or "").upper():
                    paid_invoices += _amount(row[7])
                    paid_invoice_rows += 1
        if payments != paid_invoices:
            issues.append((sheet.title, payments, paid_invoices))
        if payment_rows != paid_invoice_rows:
            issues.append(
                (sheet.title, "payment rows", payment_rows, paid_invoice_rows)
            )
    workbook.close()
    assert issues == [], f"Client ledger invariant violations: {issues}"


def test_onedrive_tableaux_comptables_is_identical_to_local_workbook():
    configured = os.environ.get("APD_ONEDRIVE_DIR", "").strip()
    onedrive = (
        Path(configured)
        if configured
        else Path.home() / "OneDrive" / "Compta_APD" / "Tableaux_Comptables"
    )
    if not onedrive.is_dir():
        pytest.skip("OneDrive Tableaux_Comptables folder is not available")
    remote = onedrive / CLIENT_LEDGER.name
    assert remote.is_file(), f"Missing OneDrive workbook: {remote}"
    assert (
        hashlib.sha256(CLIENT_LEDGER.read_bytes()).hexdigest()
        == hashlib.sha256(remote.read_bytes()).hexdigest()
    ), "OneDrive client ledger is not identical to the local export"
    assert (onedrive / "PIECES_JOINTES").is_dir(), (
        "OneDrive PIECES_JOINTES folder is missing"
    )
