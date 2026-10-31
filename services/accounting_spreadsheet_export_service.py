"""P12.2 - Tabular (CSV) exports for accounting review and arbitration report.

Pure functions: entries are plain dicts (already scoped to one tenant by the
caller). Accepted keys: operation_date, label, debit_amount, credit_amount,
status, counterparty, invoice_id, debit_account, credit_account, vat_amount,
invoice_url, reason (or ``arbitration_metadata.reason``).
"""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Any

UTF8_BOM = "\ufeff"

RECONCILIATION_COLUMNS = (
    "Date",
    "Libellé",
    "Débit",
    "Crédit",
    "Statut",
    "Tiers",
    "N° Facture",
    "Compte Débit",
    "Compte Crédit",
    "TVA",
    "Lien Justificatif",
    "Motif Arbitrage",
)

PERSONAL_ACCOUNTS = frozenset({"108000", "455000", "108", "455"})
ARBITRATED_STATUSES = frozenset(
    {
        "ARBITRATED_PERSONAL",
        "ARBITRATED_NO_INVOICE",
        "BOOKED_PERSONAL",
        "RELEASED_WITHOUT_INVOICE",
    }
)
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _text(value: Any) -> str:
    """Render a text cell, neutralising spreadsheet formula injection."""
    if value is None:
        return ""
    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _amount(value: Any, decimal_separator: str) -> str:
    if value is None or str(value).strip() == "":
        return ""
    try:
        number = Decimal(str(value).strip().replace(",", "."))
    except InvalidOperation:
        return _text(value)
    if not number.is_finite():
        return _text(value)
    return format(number, "f").replace(".", decimal_separator)


def _reason(entry: dict[str, Any]) -> Any:
    reason = entry.get("reason")
    if reason:
        return reason
    metadata = entry.get("arbitration_metadata")
    if isinstance(metadata, dict):
        return metadata.get("reason")
    return getattr(metadata, "reason", None)


def _row(entry: dict[str, Any], decimal_separator: str) -> list[str]:
    return [
        _text(entry.get("operation_date")),
        _text(entry.get("label")),
        _amount(entry.get("debit_amount"), decimal_separator),
        _amount(entry.get("credit_amount"), decimal_separator),
        _text(entry.get("status")),
        _text(entry.get("counterparty")),
        _text(entry.get("invoice_id")),
        _text(entry.get("debit_account")),
        _text(entry.get("credit_account")),
        _amount(entry.get("vat_amount"), decimal_separator),
        _text(entry.get("invoice_url")),
        _text(_reason(entry)),
    ]


def _render(
    entries: list[dict[str, Any]], delimiter: str, decimal_separator: str, bom: bool
) -> str:
    if len(delimiter) != 1 or delimiter in '"\r\n':
        raise ValueError("delimiter must be a single non-quote character")
    if decimal_separator not in (",", "."):
        raise ValueError("decimal_separator must be ',' or '.'")
    buffer = StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\r\n")
    writer.writerow(RECONCILIATION_COLUMNS)
    for entry in entries:
        writer.writerow(_row(entry, decimal_separator))
    return (UTF8_BOM if bom else "") + buffer.getvalue()


def generate_reconciliation_csv(
    entries: list[dict[str, Any]],
    delimiter: str = ";",
    *,
    decimal_separator: str = ",",
    bom: bool = False,
) -> str:
    """Full reconciliation review sheet (header + one row per entry)."""
    return _render(entries, delimiter, decimal_separator, bom)


def is_arbitration(entry: dict[str, Any]) -> bool:
    """Personal expenses (108/455) and validations without supporting document."""
    status = str(entry.get("status") or "")
    if status in ARBITRATED_STATUSES:
        return True
    accounts = {
        str(entry.get("debit_account") or ""),
        str(entry.get("credit_account") or ""),
    }
    return bool(accounts & PERSONAL_ACCOUNTS)


def generate_arbitrations_summary_csv(
    entries: list[dict[str, Any]],
    delimiter: str = ";",
    *,
    decimal_separator: str = ",",
    bom: bool = False,
) -> str:
    """Same layout as the reconciliation sheet, limited to arbitrated lines."""
    selected = [e for e in entries if is_arbitration(e)]
    return _render(selected, delimiter, decimal_separator, bom)
