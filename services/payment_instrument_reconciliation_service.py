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
from services.bank_reconciliation_service import (
    MAX_SUBSET_SUM_PIECES,
    is_lcr_operation,
    is_strict_one_to_one_counterparty,
    payment_date_allows,
)
from services.sqlite_accounting_store import SqliteAccountingStore
from services.tier_reference_service import is_payment_instrument_label

_CENT = Decimal("0.01")
_TOLERANCE = Decimal("0.02")
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


def _is_credit_note(row: dict[str, Any]) -> bool:
    document_type = str(row.get("document_type") or "").strip().upper()
    if document_type in {"AVOIR", "CREDIT_NOTE"}:
        return True
    amount = _decimal(row.get("amount_ttc"))
    return amount is not None and amount < 0


def _invoice_date(row: dict[str, Any]) -> date_cls | None:
    return _parse_date(row.get("operation_date"))


def _fifo_key(row: dict[str, Any]) -> tuple[Any, str]:
    invoice_date = _invoice_date(row) or date_cls.min
    return invoice_date, str(row.get("invoice_id") or row.get("line_id") or "")


def _payment_date_ok(row: dict[str, Any], bank_date: date_cls | None) -> bool:
    return payment_date_allows(_invoice_date(row), bank_date)



def _subset_sum(
    candidates: list[tuple[dict[str, Any], Decimal]],
    target: Decimal,
    *,
    max_items: int = MAX_SUBSET_SUM_PIECES,
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
    reference: str = "",
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
        if ttc is None or ttc == 0:
            continue
        if not _is_credit_note(row) and ttc < 0:
            continue
        # P27-sexies - a document already lettered elsewhere must never be
        # consumed a second time by another bank movement.
        status = str(row.get("reconciliation_status") or "").strip().upper()
        if status in {"PAYEE_RAPPROCHEE", "REMBOURSE_RAPPROCHE"}:
            existing = str(row.get("reconciliation_reference") or "").strip()
            if reference and existing.casefold() == reference.casefold():
                pass
            else:
                continue
        if bank_date is not None:
            invoice_date = _invoice_date(row)
            if invoice_date is not None:
                delta = abs((invoice_date - bank_date).days)
                if delta > _DATE_WINDOW_DAYS:
                    continue
        candidates.append(row)
    return candidates


def _single_invoice_or_avoir(
    candidates: list[dict[str, Any]],
    amount: Decimal,
    bank_date: date_cls | None,
) -> list[dict[str, Any]]:
    """P27-sexies Rule B - one invoice, or one invoice minus one credit note.

    Combining two or more positive invoices is forbidden: a supplier LCR is
    never built from an arbitrary mix of bills.
    """
    positives = sorted(
        (
            row
            for row in candidates
            if not _is_credit_note(row)
            and (_decimal(row.get("amount_ttc")) or Decimal("0.00")) > 0
            and _payment_date_ok(row, bank_date)
        ),
        key=_fifo_key,
    )
    credit_notes = sorted(
        (
            row
            for row in candidates
            if _is_credit_note(row) and _payment_date_ok(row, bank_date)
        ),
        key=_fifo_key,
    )
    for invoice in positives:
        invoice_ttc = _decimal(invoice.get("amount_ttc")) or Decimal("0.00")
        if abs(invoice_ttc - amount) <= _TOLERANCE:
            return [invoice]
        for credit_note in credit_notes:
            credit_ttc = abs(
                _decimal(credit_note.get("amount_ttc")) or Decimal("0.00")
            )
            net = (invoice_ttc - credit_ttc).quantize(_CENT)
            if abs(net - amount) <= _TOLERANCE:
                return [invoice, credit_note]
    return []


def _exact_invoice(
    candidates: list[dict[str, Any]], amount: Decimal, bank_date: date_cls | None
) -> list[dict[str, Any]]:
    exact = [
        row
        for row in candidates
        if _payment_date_ok(row, bank_date)
        and abs(
            (_decimal(row.get("amount_ttc")) or Decimal("0.00")) - amount
        )
        <= _TOLERANCE
    ]
    if not exact:
        return []
    return [sorted(exact, key=_fifo_key)[0]]


def _match_invoices(
    rows: list[dict[str, Any]],
    *,
    direction: str,
    amount: Decimal,
    bank_date: date_cls | None,
    label: str = "",
    reference: str = "",
) -> list[dict[str, Any]]:
    candidates = _candidates(
        rows,
        direction=direction,
        amount=amount,
        bank_date=bank_date,
        reference=reference,
    )
    strict_one_to_one = is_strict_one_to_one_counterparty(label)
    exact = _exact_invoice(candidates, amount, bank_date)
    if strict_one_to_one:
        # Rule A - a Boxtal / Chronopost debit settles exactly one invoice and
        # a credit reimburses exactly one credit note. Never a combination.
        return exact
    if direction == "DEBIT" and is_lcr_operation(label):
        # Rule B - a supplier LCR is one invoice, or one invoice minus one
        # credit note. Two positive invoices are never combined.
        return _single_invoice_or_avoir(candidates, amount, bank_date)
    if exact:
        return exact
    if direction == "CREDIT":
        return []
    # Non-LCR instruments (cheques) keep the historical exact subset-sum,
    # bounded and restricted to one counterparty.
    by_counterparty: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        name = str(row.get("counterparty") or "").strip().casefold()
        if not name:
            continue
        by_counterparty.setdefault(name, []).append(row)
    matches: list[list[dict[str, Any]]] = []
    for group in by_counterparty.values():
        payable_group = [
            row for row in group if _payment_date_ok(row, bank_date)
        ]
        subset = _subset_sum(
            [
                (row, _decimal(row.get("amount_ttc")) or Decimal("0.00"))
                for row in payable_group
            ],
            amount,
        )
        if subset:
            total = sum(
                (
                    _decimal(row.get("amount_ttc")) or Decimal("0.00")
                    for row in subset
                ),
                Decimal("0.00"),
            ).quantize(_CENT)
            if abs((amount - total).quantize(_CENT)) <= Decimal("0.05"):
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
            rows,
            direction=direction,
            amount=amount,
            bank_date=bank_date,
            label=label,
            reference=label,
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
