"""P27 - LCR / RLV / cheque payment-instrument reconciliation.

A bank label such as ``LCR 806255 RLV 00000385`` or ``CHEQUE N 0000412`` is a
payment instrument, never a company name. This service:

1. finds the supplier invoices (bank debit) or customer invoices (bank credit)
   whose TTC amount matches exactly, or whose subset-sum matches the instrument;
2. letters the bank line and the invoice(s) automatically when the match is
   unique, assigning the real invoice counterparty;
3. returns the unmatched instruments so the cockpit can ask the director with
   the real bank date, instrument number and TTC amount.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable

from schemas.folder_state_schema import DocumentCategory
from services.sqlite_accounting_store import SqliteAccountingStore
from services.tier_reference_service import is_payment_instrument_label

_CENT = Decimal("0.01")
_DATE_WINDOW_DAYS = 180
_MAX_CANDIDATES = 20
_PURCHASE_CATEGORIES = frozenset(
    {
        DocumentCategory.ACHATS_MARCHANDISES.value,
        DocumentCategory.TRANSPORTS_LOGISTIQUE.value,
        DocumentCategory.FRAIS_GENERAUX.value,
        DocumentCategory.FISCAL_SOCIAL.value,
        DocumentCategory.IMMOBILISATIONS.value,
    }
)


@dataclass(frozen=True)
class PaymentInstrumentMatch:
    line_id: str
    operation_date: str
    label: str
    amount: Decimal
    direction: str
    invoice_ids: tuple[str, ...]
    counterparty: str


@dataclass(frozen=True)
class UnmatchedPaymentInstrument:
    line_id: str
    operation_date: str
    label: str
    amount: Decimal
    direction: str


@dataclass(frozen=True)
class PaymentInstrumentReport:
    matched: tuple[PaymentInstrumentMatch, ...]
    unmatched: tuple[UnmatchedPaymentInstrument, ...]


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(_CENT)
    except Exception:  # noqa: BLE001
        return None


def _parse_date(value: Any) -> date_cls | None:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _is_bank_row(row: dict[str, Any]) -> bool:
    document_type = str(row.get("document_type") or "").upper()
    line_id = str(row.get("line_id") or "").upper()
    return document_type in {"OFX", "QFX"} or line_id.startswith("OFX-")


def _is_invoice_row(row: dict[str, Any]) -> bool:
    if _is_bank_row(row):
        return False
    document_type = str(row.get("document_type") or "").upper()
    if document_type in {"OFX", "QFX", "LCR_PAYMENT", "LCR_REMITTANCE"}:
        return False
    return bool(row.get("invoice_id"))


def _subset_sum(
    candidates: list[tuple[dict[str, Any], Decimal]],
    target: Decimal,
    *,
    max_items: int = 3,
) -> list[dict[str, Any]] | None:
    items = sorted(candidates, key=lambda item: item[1], reverse=True)[
        :_MAX_CANDIDATES
    ]

    def search(
        index: int,
        remaining: Decimal,
        chosen: list[dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        if abs(remaining) < _CENT:
            return chosen
        if index >= len(items) or remaining < 0 or len(chosen) >= max_items:
            return None
        row, amount = items[index]
        if amount <= remaining:
            result = search(index + 1, remaining - amount, [*chosen, row])
            if result is not None:
                return result
        return search(index + 1, remaining, chosen)

    return search(0, target, [])


def _candidates(
    rows: Iterable[dict[str, Any]],
    *,
    direction: str,
    amount: Decimal,
    bank_date: date_cls | None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if not _is_invoice_row(row):
            continue
        category = str(row.get("category") or "")
        if direction == "DEBIT" and category not in _PURCHASE_CATEGORIES:
            continue
        if direction == "CREDIT" and category != DocumentCategory.VENTES_CLIENTS.value:
            continue
        ttc = _decimal(row.get("amount_ttc"))
        if ttc is None or ttc <= 0:
            continue
        if bank_date is not None:
            invoice_date = _parse_date(row.get("operation_date"))
            if invoice_date is not None:
                delta = abs((invoice_date - bank_date).days)
                if delta > _DATE_WINDOW_DAYS:
                    continue
        candidates.append(row)
    return candidates


def _match_invoices(
    rows: list[dict[str, Any]],
    *,
    direction: str,
    amount: Decimal,
    bank_date: date_cls | None,
) -> list[dict[str, Any]]:
    candidates = _candidates(
        rows, direction=direction, amount=amount, bank_date=bank_date
    )
    exact = [
        row
        for row in candidates
        if (_decimal(row.get("amount_ttc")) or Decimal("0.00")) == amount
    ]
    if len(exact) == 1:
        return exact
    if len(exact) > 1:
        return []
    # Subset-sum is only trusted inside one counterparty and for at most three
    # invoices, otherwise a large LCR would match an arbitrary mix of suppliers.
    by_counterparty: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        name = str(row.get("counterparty") or "").strip().casefold()
        if not name:
            continue
        by_counterparty.setdefault(name, []).append(row)
    matches: list[list[dict[str, Any]]] = []
    for group in by_counterparty.values():
        subset = _subset_sum(
            [
                (row, _decimal(row.get("amount_ttc")) or Decimal("0.00"))
                for row in group
            ],
            amount,
        )
        if subset:
            matches.append(subset)
    if len(matches) == 1:
        return matches[0]
    return []


def reconcile_payment_instruments(
    store: SqliteAccountingStore, tenant_id: str
) -> PaymentInstrumentReport:
    """Letter every ``LCR`` / ``CHEQUE`` bank line against real invoices."""
    rows = store.list_master_lines(tenant_id)
    matched: list[PaymentInstrumentMatch] = []
    unmatched: list[UnmatchedPaymentInstrument] = []
    for row in rows:
        if not _is_bank_row(row):
            continue
        label = str(row.get("label") or "").strip()
        if not is_payment_instrument_label(label):
            continue
        debit = _decimal(row.get("debit_amount")) or Decimal("0.00")
        credit = _decimal(row.get("credit_amount")) or Decimal("0.00")
        if debit > 0:
            direction, amount = "DEBIT", debit
        elif credit > 0:
            direction, amount = "CREDIT", credit
        else:
            continue
        bank_date = _parse_date(row.get("operation_date"))
        selected = _match_invoices(
            rows, direction=direction, amount=amount, bank_date=bank_date
        )
        line_id = str(row.get("line_id") or "")
        if not selected:
            unmatched.append(
                UnmatchedPaymentInstrument(
                    line_id=line_id,
                    operation_date=str(row.get("operation_date") or ""),
                    label=label,
                    amount=amount,
                    direction=direction,
                )
            )
            continue
        counterparty = str(selected[0].get("counterparty") or "").strip()
        invoice_ids = tuple(
            str(invoice.get("invoice_id") or "") for invoice in selected
        )
        bank_line = dict(row)
        bank_line.pop("line_id", None)
        bank_line.update(
            {
                "counterparty": counterparty or bank_line.get("counterparty"),
                "invoice_id": " + ".join(invoice_ids),
                "reconciliation_status": "PAYEE_RAPPROCHEE",
                "reconciliation_date": str(row.get("operation_date") or ""),
                "reconciliation_reference": label,
                "reconciliation_amount": str(amount),
                "balance_due": "0.00",
            }
        )
        store.force_upsert_document_line(tenant_id, line_id, bank_line)
        for invoice in selected:
            invoice_line = dict(invoice)
            invoice_line.pop("line_id", None)
            invoice_line.update(
                {
                    "reconciliation_status": "PAYEE_RAPPROCHEE",
                    "reconciliation_date": str(row.get("operation_date") or ""),
                    "reconciliation_reference": label,
                    "reconciliation_amount": str(
                        _decimal(invoice.get("amount_ttc"))
                        or Decimal("0.00")
                    ),
                    "balance_due": "0.00",
                }
            )
            store.force_upsert_document_line(
                tenant_id, str(invoice.get("line_id") or ""), invoice_line
            )
        matched.append(
            PaymentInstrumentMatch(
                line_id=line_id,
                operation_date=str(row.get("operation_date") or ""),
                label=label,
                amount=amount,
                direction=direction,
                invoice_ids=invoice_ids,
                counterparty=counterparty,
            )
        )
    return PaymentInstrumentReport(
        matched=tuple(matched), unmatched=tuple(unmatched)
    )


__all__ = [
    "PaymentInstrumentMatch",
    "PaymentInstrumentReport",
    "UnmatchedPaymentInstrument",
    "reconcile_payment_instruments",
]
