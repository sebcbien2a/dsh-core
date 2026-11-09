"""P20 - Exact customer-payment lettering for LCR remittances and transfers.

The bank statement only exposes global credits: ``REMISE ENCAISSEMENT N 91374``
or ``VIR SEPA SCTI SARL``. This module links each credit to the customer
invoices imported by :mod:`services.customer_invoice_batch_importer`:

* an LCR remittance with a parsed bordereau PDF is split into its exact 1-to-N
  allocations (P19), then each allocation is matched by customer and amount;
* an LCR remittance without a bordereau is matched by an exact subset-sum of
  LCR invoices due around the credit date;
* direct transfers are matched against the invoices of the named counterparty.

Every match writes ``PAYEE_RAPPROCHEE``, the bank date and the OFX reference in
the dedicated store columns, so the manager's workbook immediately shows that
invoices flagged "A relancer" in Sage are in fact already paid.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from services.customer_invoice_batch_importer import (
    PAID_STATUS,
    PENDING_STATUS,
)
from services.lcr_remittance_parser import (
    LcrRemittance,
    allocate_lcr_remittance,
    parse_lcr_remittance,
)
from services.ofx_bank_statement_parser import (
    OfxStatement,
    OfxTransaction,
    parse_ofx_statement,
)
from schemas.payment_method_schema import normalize_counterparty_name
from services.bank_reconciliation_service import MAX_SUBSET_SUM_PIECES
from services.sqlite_accounting_store import validate_tenant_id

_CENT = Decimal("0.01")
_DEFAULT_TOLERANCE = Decimal("0.00")
_REFERENCE = re.compile(r"\bN\s*(\d{2,})\b", re.IGNORECASE)
_SPACES = re.compile(r"\s+")
_LEGAL_SUFFIXES = frozenset(
    {"SARL", "SAS", "SASU", "SA", "EURL", "SCI", "SNC", "SCS", "GIE", "SELARL"}
)

REMISE = "REMISE"
VIREMENT = "VIREMENT"
REMISE_CHEQUES = "REMISE_CHEQUES"


class CustomerReconciliationError(ValueError):
    """Invalid P20 lettering request; ``code`` is stable."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class ReconciliableInvoice:
    """Minimal view of one pending customer invoice."""

    line_id: str
    number: str
    customer: str
    amount: Decimal
    due_date: date | None
    payment_method: str
    status: str = PENDING_STATUS
    invoice_date: date | None = None
    is_credit_note: bool = False

    @property
    def pending(self) -> bool:
        return self.status != PAID_STATUS


@dataclass(frozen=True)
class InvoiceLettering:
    """One invoice paid by one bank credit."""

    line_id: str
    number: str
    customer: str
    amount: Decimal
    due_date: date | None
    reference: str
    reconciliation_date: date | None


@dataclass(frozen=True)
class PaymentLettering:
    """Outcome of lettering one bank credit."""

    bank_line_id: str
    bank_reference: str
    bank_date: date | None
    bank_amount: Decimal
    payment_method: str
    kind: str
    invoices: tuple[InvoiceLettering, ...] = ()
    matched: bool = False
    bordereau_reference: str = ""
    reason: str = ""

    @property
    def allocated_total(self) -> Decimal:
        return sum((item.amount for item in self.invoices), Decimal("0.00")).quantize(
            _CENT
        )

    @property
    def invoice_numbers(self) -> tuple[str, ...]:
        return tuple(item.number for item in self.invoices)


@dataclass(frozen=True)
class CustomerReconciliationReport:
    """Observable outcome of one P20 reconciliation run."""

    payments: tuple[PaymentLettering, ...]
    reconciled_invoices: int
    pending_invoices: int
    errors: tuple[str, ...] = ()

    def by_reference(self, reference: str) -> PaymentLettering | None:
        needle = str(reference)
        for payment in self.payments:
            if payment.bank_reference == needle or needle in payment.bank_reference:
                return payment
        return None

    @property
    def matched_payments(self) -> int:
        return sum(1 for payment in self.payments if payment.matched)


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return _SPACES.sub(" ", text).strip().casefold()


def _name_tokens(value: Any) -> tuple[str, ...]:
    folded = _fold(value)
    words = [word for word in re.split(r"[^a-z0-9]+", folded) if word]
    kept = [word for word in words if word.upper() not in _LEGAL_SUFFIXES]
    return tuple(kept or words)


def counterparty_names_match(left: Any, right: Any) -> bool:
    """True when two customer names designate the same counterparty.

    Legal suffixes are ignored and a long enough token prefix counts as the
    same name, which absorbs Sage spellings such as ``SARL CARROSSERIE YUCE``
    versus ``CARROSSERIE YUCELL``.
    """
    left_tokens = _name_tokens(left)
    right_tokens = _name_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    if left_tokens == right_tokens:
        return True
    left_set, right_set = set(left_tokens), set(right_tokens)
    if left_set <= right_set or right_set <= left_set:
        return True
    for first in left_tokens:
        for second in right_tokens:
            if len(first) >= 4 and len(second) >= 4 and (
                first.startswith(second) or second.startswith(first)
            ):
                return True
    return False


def _parse_iso_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _amount(value: Any) -> Decimal:
    try:
        return Decimal(str(value)).quantize(_CENT)
    except Exception as exc:  # noqa: BLE001 - normalised error
        raise CustomerReconciliationError("INVALID_AMOUNT", repr(value)) from exc


def _invoice_from_row(row: Mapping[str, Any]) -> ReconciliableInvoice | None:
    line_id = str(row.get("line_id") or "").strip()
    if not line_id:
        return None
    document_type = str(row.get("document_type") or "").strip().upper()
    if document_type == "OFX" or line_id.upper().startswith("OFX-"):
        return None
    amount_value = row.get("amount_ttc")
    if amount_value in (None, ""):
        amount_value = row.get("credit_amount")
    try:
        amount = _amount(amount_value)
    except CustomerReconciliationError:
        return None
    return ReconciliableInvoice(
        line_id=line_id,
        number=str(row.get("invoice_id") or line_id),
        customer=str(row.get("counterparty") or ""),
        amount=amount,
        due_date=_parse_iso_date(row.get("due_date") or row.get("operation_date")),
        payment_method=str(row.get("payment_method") or ""),
        status=str(row.get("reconciliation_status") or PENDING_STATUS).upper(),
        invoice_date=_parse_iso_date(row.get("operation_date")),
        is_credit_note=(
            str(row.get("document_type") or "").strip().upper()
            in {"AVOIR", "CREDIT_NOTE"}
            or amount < 0
        ),
    )


def load_pending_invoices(store: Any, tenant_id: str) -> list[ReconciliableInvoice]:
    """Read every customer invoice and keep the P20 view of each row."""
    invoices: list[ReconciliableInvoice] = []
    for row in store.list_customer_invoices(tenant_id):
        invoice = _invoice_from_row(row)
        if invoice is not None:
            invoices.append(invoice)
    return invoices


def _better_combo(
    candidate: tuple[int, ...],
    existing: tuple[int, ...] | None,
    items: Sequence[ReconciliableInvoice],
) -> bool:
    if existing is None:
        return True
    if len(candidate) != len(existing):
        return len(candidate) < len(existing)
    return tuple(sorted(candidate)) < tuple(sorted(existing))


def _subset_sums(
    items: Sequence[ReconciliableInvoice],
) -> dict[int, tuple[int, ...]]:
    sums: dict[int, tuple[int, ...]] = {0: ()}
    for index, item in enumerate(items):
        cents = int((item.amount * 100).to_integral_value())
        additions: dict[int, tuple[int, ...]] = {}
        for total, combo in list(sums.items()):
            candidate_total = total + cents
            candidate = combo + (index,)
            if _better_combo(candidate, sums.get(candidate_total) or additions.get(candidate_total), items):
                additions[candidate_total] = candidate
        for total, combo in additions.items():
            if _better_combo(combo, sums.get(total), items):
                sums[total] = combo
    return sums


def _coerce_reconciliable(value: Any) -> ReconciliableInvoice:
    if isinstance(value, ReconciliableInvoice):
        return value
    if isinstance(value, Mapping):
        invoice = _invoice_from_row(value)
        if invoice is not None:
            return invoice
    amount = getattr(value, "total_ttc", None)
    if amount is None:
        amount = getattr(value, "amount", None)
    status = (
        getattr(value, "reconciliation_status", None)
        or getattr(value, "status", None)
        or (PAID_STATUS if getattr(value, "pending", True) is False else PENDING_STATUS)
    )
    return ReconciliableInvoice(
        line_id=str(getattr(value, "line_id", "")) or str(getattr(value, "number", "")),
        number=str(getattr(value, "number", "")),
        customer=str(getattr(value, "customer", "")),
        amount=_amount(amount),
        due_date=getattr(value, "due_date", None),
        payment_method=str(getattr(value, "payment_method", "")),
        status=str(status).upper(),
        invoice_date=getattr(value, "invoice_date", None),
        is_credit_note=bool(
            getattr(value, "is_credit_note", False) or _amount(amount) < 0
        ),
    )


def find_exact_invoice_combination(
    invoices: Sequence[ReconciliableInvoice | Any],
    target: Decimal,
    *,
    tolerance: Decimal = _DEFAULT_TOLERANCE,
    preferred_date: date | None = None,
    payment_date: date | None = None,
) -> tuple[ReconciliableInvoice, ...] | None:
    """Find the exact cent-level subset of ``invoices`` summing to ``target``.

    The search is deterministic: minimal number of invoices first, then the
    combination whose due dates are closest to ``preferred_date``. It is a
    meet-in-the-middle subset-sum, exact to the cent and bounded to 30
    candidates (the P20 batch size).

    P27-sexies: when ``payment_date`` is supplied, a document dated after the
    bank movement is never lettered (``invoice_date <= payment_date``).
    """
    target_cents = int((_amount(target) * 100).to_integral_value())
    tolerance_cents = int((_amount(tolerance) * 100).to_integral_value())
    if target_cents <= 0:
        return None
    pending: list[ReconciliableInvoice] = []
    for invoice in invoices:
        coerced = _coerce_reconciliable(invoice)
        if not coerced.pending:
            continue
        if (
            payment_date is not None
            and coerced.invoice_date is not None
            and coerced.invoice_date > payment_date
        ):
            continue
        pending.append(coerced)
    if not pending:
        return None
    if len(pending) > 40:
        pending = sorted(
            pending,
            key=lambda item: (
                abs((item.due_date - preferred_date).days)
                if item.due_date and preferred_date
                else 10_000,
                item.number,
            ),
        )[:40]
    if preferred_date is not None:
        pending.sort(
            key=lambda item: (
                abs((item.due_date - preferred_date).days)
                if item.due_date
                else 10_000,
                item.number,
            )
        )
    midpoint = len(pending) // 2
    left_items, right_items = pending[:midpoint], pending[midpoint:]
    left_sums = _subset_sums(left_items)
    right_sums = _subset_sums(right_items)
    best: tuple[ReconciliableInvoice, ...] | None = None
    best_key: tuple[int, int] | None = None
    for left_total, left_combo in left_sums.items():
        for delta in range(-tolerance_cents, tolerance_cents + 1):
            needed = target_cents - left_total + delta
            right_combo = right_sums.get(needed)
            if right_combo is None:
                continue
            combo = left_combo + tuple(index + midpoint for index in right_combo)
            if not combo or len(combo) > MAX_SUBSET_SUM_PIECES:
                continue
            selected = tuple(pending[index] for index in sorted(combo))
            total = sum((item.amount for item in selected), Decimal("0.00"))
            difference = int(
                (abs(total - _amount(target)) * 100).to_integral_value()
            )
            key = (len(selected), difference)
            if best_key is None or key < best_key:
                best, best_key = selected, key
    if best is None:
        return None
    return tuple(sorted(best, key=lambda item: (item.due_date or date.min, item.number)))


def find_single_invoice_or_avoir(
    invoices: Sequence[ReconciliableInvoice | Any],
    target: Decimal,
    *,
    payment_date: date | None = None,
    tolerance: Decimal = _DEFAULT_TOLERANCE,
) -> tuple[ReconciliableInvoice, ...] | None:
    """P27-sexies - match one invoice, or one invoice minus one credit note.

    Two positive invoices are never combined. Repeated identical amounts are
    consumed FIFO (``invoice_date ASC, invoice_id ASC``).
    """
    target = _amount(target)
    pending = [_coerce_reconciliable(invoice) for invoice in invoices]
    pending = [
        invoice
        for invoice in pending
        if invoice.pending
        and (
            payment_date is None
            or invoice.invoice_date is None
            or invoice.invoice_date <= payment_date
        )
    ]
    positives = sorted(
        (
            invoice
            for invoice in pending
            if not invoice.is_credit_note and invoice.amount > 0
        ),
        key=lambda item: (item.invoice_date or date.min, item.number),
    )
    credit_notes = sorted(
        (invoice for invoice in pending if invoice.is_credit_note),
        key=lambda item: (item.invoice_date or date.min, item.number),
    )
    for invoice in positives:
        if abs(invoice.amount - target) <= tolerance:
            return (invoice,)
        for credit_note in credit_notes:
            net = (invoice.amount - abs(credit_note.amount)).quantize(_CENT)
            if abs(net - target) <= tolerance:
                return (invoice, credit_note)
    return None


def find_remittance_combination(
    invoices: Sequence[ReconciliableInvoice | Any],
    target: Decimal,
    *,
    payment_date: date | None = None,
    tolerance: Decimal = _DEFAULT_TOLERANCE,
    max_clients: int = 12,
    forbidden_clients: Iterable[str] | None = None,
) -> tuple[ReconciliableInvoice, ...] | None:
    """P27-sexies Rule C - one invoice (or invoice - avoir) per client.

    A multi-client LCR remittance can never use two positive invoices from the
    same customer to fabricate the bank amount. The small ``CALANDRE_18``
    144.00 EUR invoices can therefore no longer act as filler.
    """
    target_cents = int((_amount(target) * 100).to_integral_value())
    tolerance_cents = int((_amount(tolerance) * 100).to_integral_value())
    if target_cents <= 0:
        return None
    pending: list[ReconciliableInvoice] = []
    for invoice in invoices:
        coerced = _coerce_reconciliable(invoice)
        if not coerced.pending:
            continue
        if (
            payment_date is not None
            and coerced.invoice_date is not None
            and coerced.invoice_date > payment_date
        ):
            continue
        pending.append(coerced)
    if not pending:
        return None
    grouped: dict[str, list[ReconciliableInvoice]] = {}
    forbidden = {
        normalize_counterparty_name(value) or str(value)
        for value in (forbidden_clients or ())
        if str(value).strip()
    }
    for invoice in pending:
        key = normalize_counterparty_name(invoice.customer) or invoice.customer
        if key in forbidden:
            continue
        grouped.setdefault(key, []).append(invoice)
    keys = sorted(grouped)
    if len(keys) > max_clients:
        keys = keys[:max_clients]
    bundles: dict[str, list[tuple[int, tuple[ReconciliableInvoice, ...]]]] = {}
    for key in keys:
        items = grouped[key]
        positives = sorted(
            (
                item
                for item in items
                if not item.is_credit_note and item.amount > 0
            ),
            key=lambda item: (item.invoice_date or date.min, item.number),
        )
        credit_notes = sorted(
            (item for item in items if item.is_credit_note),
            key=lambda item: (item.invoice_date or date.min, item.number),
        )
        client_bundles: list[tuple[int, tuple[ReconciliableInvoice, ...]]] = []
        for positive in positives:
            cents = int((positive.amount * 100).to_integral_value())
            client_bundles.append((cents, (positive,)))
            for credit_note in credit_notes:
                net = (positive.amount - abs(credit_note.amount)).quantize(_CENT)
                if net <= 0:
                    continue
                net_cents = int((net * 100).to_integral_value())
                client_bundles.append((net_cents, (positive, credit_note)))
        if client_bundles:
            bundles[key] = client_bundles
    keys = [key for key in keys if key in bundles]
    from functools import lru_cache

    @lru_cache(maxsize=None)
    def solve(index: int, remaining: int):
        if abs(remaining) <= tolerance_cents:
            return ()
        if index >= len(keys):
            return None
        key = keys[index]
        ordered = sorted(
            bundles[key],
            key=lambda bundle: (
                -bundle[0],
                bundle[1][0].invoice_date or date.min,
                bundle[1][0].number,
            ),
        )
        for cents, combo in ordered:
            if cents > remaining + tolerance_cents:
                continue
            tail = solve(index + 1, remaining - cents)
            if tail is not None:
                return combo + tail
        return solve(index + 1, remaining)

    selected = solve(0, target_cents)
    if selected is None:
        return None
    return tuple(
        sorted(
            selected,
            key=lambda item: (
                item.invoice_date or date.min,
                item.number,
            ),
        )
    )


def _method_family(value: Any) -> str:
    text = _fold(value)
    if "lcr" in text:
        return "LCR"
    if "vir" in text:
        return "VIREMENT"
    if "cheq" in text or "chq" in text:
        return "CHEQUE"
    return ""


def _rule_methods(rule: Mapping[str, Any] | None) -> tuple[str, ...]:
    if rule is None:
        return ()
    methods = [str(rule.get("expected_payment_method") or "")]
    methods.extend(str(item) for item in (rule.get("fallback_methods") or []))
    return tuple(item for item in methods if item)


def _rule_allows(rule: Mapping[str, Any] | None, method: str) -> bool:
    family = _method_family(method)
    if not family or rule is None:
        return False
    allowed = {_method_family(item) for item in _rule_methods(rule)}
    if family in allowed:
        return True
    # P20 business rule: an LCR client always accepts LCR first, with CHEQUE
    # and VIREMENT as fallbacks, even when only one mention was learned.
    if _method_family(rule.get("expected_payment_method")) == "LCR" and family in {
        "LCR",
        "CHEQUE",
        "VIREMENT",
    }:
        return True
    return False


def _candidate_allows(
    invoice: ReconciliableInvoice,
    method: str,
    rules: Mapping[str, Mapping[str, Any]],
) -> bool:
    family = _method_family(method)
    if not family or not invoice.payment_method:
        return True
    if _method_family(invoice.payment_method) == family:
        return True
    rule = rules.get(normalize_counterparty_name(invoice.customer))
    return _rule_allows(rule, method)


def _filter_candidates(
    invoices: Sequence[ReconciliableInvoice],
    *,
    method: str,
    reference_date: date | None,
    window_days: int,
    rules: Mapping[str, Mapping[str, Any]] | None = None,
    window_before_days: int | None = None,
    window_after_days: int | None = None,
    require_window: bool = True,
) -> list[ReconciliableInvoice]:
    pending = [invoice for invoice in invoices if invoice.pending]
    rule_map = rules or {}
    typed = [
        invoice
        for invoice in pending
        if _candidate_allows(invoice, method, rule_map)
    ]
    if not typed:
        typed = pending
    if not require_window or reference_date is None:
        return typed
    dated = [invoice for invoice in typed if invoice.due_date is not None]
    if not dated:
        return typed
    before = window_days if window_before_days is None else window_before_days
    after = window_days if window_after_days is None else window_after_days
    return [
        invoice
        for invoice in dated
        if -before <= (invoice.due_date - reference_date).days <= after
    ]


def _bank_values(line: Any) -> dict[str, Any]:
    if isinstance(line, OfxTransaction):
        return {
            "line_id": line.fitid,
            "fitid": line.fitid,
            "label": line.label,
            "memo": line.memo,
            "amount": line.amount,
            "operation_date": line.operation_date,
            "payment_method": line.payment_method,
            "payment_reference": line.payment_reference,
            "counterparty": line.counterparty,
        }
    if not isinstance(line, Mapping):
        raise CustomerReconciliationError("INVALID_BANK_LINE", type(line).__name__)
    credit = line.get("credit_amount")
    amount_value = credit if credit not in (None, "") else line.get("amount")
    return {
        "line_id": str(line.get("line_id") or line.get("fitid") or ""),
        "fitid": str(line.get("fitid") or ""),
        "label": str(line.get("label") or ""),
        "memo": str(line.get("memo") or ""),
        "amount": (
            _amount(amount_value)
            if amount_value not in (None, "")
            else Decimal("0.00")
        ),
        "operation_date": _parse_iso_date(line.get("operation_date")),
        "payment_method": str(line.get("payment_method") or ""),
        "payment_reference": line.get("payment_reference"),
        "counterparty": str(line.get("counterparty") or ""),
    }


def _reference_of(values: Mapping[str, Any]) -> str:
    reference = str(values.get("payment_reference") or "").strip()
    if reference:
        return reference
    match = _REFERENCE.search(str(values.get("label") or ""))
    return match.group(1) if match is not None else ""


def _bank_reference(kind: str, values: Mapping[str, Any]) -> str:
    reference = _reference_of(values)
    if reference:
        return f"{kind} {reference}"
    fitid = str(values.get("fitid") or values.get("line_id") or "").strip()
    return f"{kind} {fitid}" if fitid else kind


_REMISE_NUMBER = re.compile(r"REMISE[^\d]{0,30}(\d{2,})", re.IGNORECASE)


def _remise_key(reference: Any) -> str:
    match = _REMISE_NUMBER.search(str(reference or ""))
    return f"REMISE {match.group(1)}" if match is not None else str(reference or "")


def _coerce_bordereaux(
    bordereaux: Iterable[LcrRemittance | str | bytes | Path],
) -> tuple[list[LcrRemittance], list[str]]:
    parsed: list[LcrRemittance] = []
    errors: list[str] = []
    for index, item in enumerate(bordereaux, start=1):
        if isinstance(item, LcrRemittance):
            parsed.append(item)
            continue
        try:
            parsed.append(parse_lcr_remittance(item))
        except Exception as exc:  # noqa: BLE001 - a bad slip never blocks others
            code = getattr(exc, "code", type(exc).__name__.upper())
            errors.append(f"BORDEREAU_{index}:{code}")
    return parsed, errors


def _find_bordereau(
    bordereaux: Sequence[LcrRemittance], amount: Decimal
) -> LcrRemittance | None:
    for remittance in bordereaux:
        total = remittance.total_amount or remittance.computed_total
        if abs(total - amount) <= _DEFAULT_TOLERANCE:
            return remittance
    return None


def _mark_invoice(
    store: Any,
    tenant_id: str,
    invoice: ReconciliableInvoice,
    *,
    reference: str,
    reconciliation_date: date | None,
) -> InvoiceLettering | None:
    if reconciliation_date is None:
        return None
    if (
        invoice.invoice_date is not None
        and reconciliation_date < invoice.invoice_date
    ):
        # P27-sexies invariant: a document is never paid before it exists.
        return None
    try:
        store.reconcile_customer_invoice(
            tenant_id,
            invoice.line_id,
            reconciliation_date=reconciliation_date,
            reference=reference,
            amount=invoice.amount,
        )
    except Exception:  # noqa: BLE001 - reported by the caller as unmatched
        return None
    return InvoiceLettering(
        line_id=invoice.line_id,
        number=invoice.number,
        customer=invoice.customer,
        amount=invoice.amount,
        due_date=invoice.due_date,
        reference=reference,
        reconciliation_date=reconciliation_date,
    )


def _reconcile_bordereau(
    store: Any,
    tenant_id: str,
    values: Mapping[str, Any],
    remittance: LcrRemittance,
    pending: dict[str, ReconciliableInvoice],
    reference: str,
    operations: date | None,
) -> tuple[PaymentLettering, list[str]]:
    errors: list[str] = []
    allocations = allocate_lcr_remittance(
        remittance,
        fitid=str(values.get("fitid") or values.get("line_id") or ""),
        operation_date=operations,
    )
    letterings: list[InvoiceLettering] = []
    for allocation in allocations:
        candidates = [
            invoice
            for invoice in pending.values()
            if counterparty_names_match(invoice.customer, allocation.drawer)
        ]
        combination = find_single_invoice_or_avoir(
            candidates,
            allocation.amount,
            payment_date=operations,
        )
        if combination is None:
            errors.append(f"UNMATCHED_ALLOCATION:{allocation.drawer}:{allocation.amount}")
            continue
        for invoice in combination:
            lettering = _mark_invoice(
                store,
                tenant_id,
                invoice,
                reference=reference,
                reconciliation_date=operations,
            )
            if lettering is None:
                errors.append(f"LETTERING_FAILED:{invoice.number}")
                continue
            letterings.append(lettering)
            pending.pop(invoice.line_id, None)
    return (
        PaymentLettering(
            bank_line_id=str(values.get("line_id") or ""),
            bank_reference=reference,
            bank_date=operations,
            bank_amount=values["amount"],
            payment_method=str(values.get("payment_method") or ""),
            kind=REMISE,
            invoices=tuple(letterings),
            matched=len(letterings) > 0 and len(errors) == 0,
            bordereau_reference=remittance.transmission_reference,
            reason="" if not errors else "UNMATCHED_ALLOCATIONS",
        ),
        errors,
    )


def _reconcile_remittance_combination(
    store: Any,
    tenant_id: str,
    values: Mapping[str, Any],
    invoices: Sequence[ReconciliableInvoice],
    pending: dict[str, ReconciliableInvoice],
    reference: str,
    operations: date | None,
    window_days: int,
    *,
    method: str = "LCR",
    kind: str = REMISE,
    rules: Mapping[str, Mapping[str, Any]] | None = None,
    window_before_days: int | None = None,
    window_after_days: int | None = None,
    forbidden_clients: Iterable[str] | None = None,
) -> PaymentLettering:
    candidates = _filter_candidates(
        list(pending.values()),
        method=method,
        reference_date=operations,
        window_days=window_days,
        rules=rules,
        window_before_days=window_before_days,
        window_after_days=window_after_days,
        require_window=False,
    )
    if _method_family(method) == "LCR":
        combination = find_remittance_combination(
            candidates,
            values["amount"],
            payment_date=operations,
            forbidden_clients=forbidden_clients,
        )
    else:
        combination = find_exact_invoice_combination(
            candidates,
            values["amount"],
            preferred_date=operations,
            payment_date=operations,
        )
    if combination is None:
        return PaymentLettering(
            bank_line_id=str(values.get("line_id") or ""),
            bank_reference=reference,
            bank_date=operations,
            bank_amount=values["amount"],
            payment_method=str(values.get("payment_method") or ""),
            kind=kind,
            matched=False,
            reason="NO_EXACT_COMBINATION",
        )
    letterings: list[InvoiceLettering] = []
    for invoice in combination:
        lettering = _mark_invoice(
            store,
            tenant_id,
            invoice,
            reference=reference,
            reconciliation_date=operations,
        )
        if lettering is not None:
            letterings.append(lettering)
            pending.pop(invoice.line_id, None)
    return PaymentLettering(
        bank_line_id=str(values.get("line_id") or ""),
        bank_reference=reference,
        bank_date=operations,
        bank_amount=values["amount"],
        payment_method=str(values.get("payment_method") or ""),
        kind=kind,
        invoices=tuple(letterings),
        matched=len(letterings) == len(combination),
        reason="" if len(letterings) == len(combination) else "LETTERING_FAILED",
    )


def _reconcile_transfer(
    store: Any,
    tenant_id: str,
    values: Mapping[str, Any],
    invoices: Sequence[ReconciliableInvoice],
    pending: dict[str, ReconciliableInvoice],
    reference: str,
    operations: date | None,
    *,
    rules: Mapping[str, Mapping[str, Any]] | None = None,
) -> PaymentLettering:
    counterparty = str(values.get("counterparty") or "")
    named = [
        invoice
        for invoice in pending.values()
        if counterparty_names_match(invoice.customer, counterparty)
    ]
    if not named:
        return PaymentLettering(
            bank_line_id=str(values.get("line_id") or ""),
            bank_reference=reference,
            bank_date=operations,
            bank_amount=values["amount"],
            payment_method=str(values.get("payment_method") or ""),
            kind=VIREMENT,
            matched=False,
            reason="NO_COUNTERPARTY_MATCH",
        )
    rule_map = rules or {}
    typed = [
        invoice
        for invoice in named
        if _candidate_allows(invoice, "VIREMENT", rule_map)
    ]
    candidates = typed or named
    combination = find_exact_invoice_combination(
        candidates,
        values["amount"],
        preferred_date=operations,
        payment_date=operations,
    )
    if combination is None:
        return PaymentLettering(
            bank_line_id=str(values.get("line_id") or ""),
            bank_reference=reference,
            bank_date=operations,
            bank_amount=values["amount"],
            payment_method=str(values.get("payment_method") or ""),
            kind=VIREMENT,
            matched=False,
            reason="NO_EXACT_COMBINATION",
        )
    letterings: list[InvoiceLettering] = []
    for invoice in combination:
        lettering = _mark_invoice(
            store,
            tenant_id,
            invoice,
            reference=reference,
            reconciliation_date=operations,
        )
        if lettering is not None:
            letterings.append(lettering)
            pending.pop(invoice.line_id, None)
    return PaymentLettering(
        bank_line_id=str(values.get("line_id") or ""),
        bank_reference=reference,
        bank_date=operations,
        bank_amount=values["amount"],
        payment_method=str(values.get("payment_method") or ""),
        kind=VIREMENT,
        invoices=tuple(letterings),
        matched=len(letterings) == len(combination),
        reason="" if len(letterings) == len(combination) else "LETTERING_FAILED",
    )


def _apply_bordereau_rules_parsed(
    store: Any, tenant_id: str, parsed: Sequence[LcrRemittance]
) -> list[dict[str, Any]]:
    updated: list[dict[str, Any]] = []
    for remittance in parsed:
        for drawer in remittance.unique_drawers:
            existing = store.get_counterparty_payment_rule(tenant_id, drawer)
            fallbacks = [
                str(item).upper()
                for item in ((existing or {}).get("fallback_methods") or [])
                if str(item).strip()
            ]
            current = str((existing or {}).get("expected_payment_method") or "").upper()
            if current and current != "LCR" and current not in fallbacks:
                fallbacks.append(current)
            for method in ("CHEQUE", "VIREMENT"):
                if method not in fallbacks:
                    fallbacks.append(method)
            updated.append(
                store.set_counterparty_payment_rule(
                    tenant_id,
                    drawer,
                    "LCR",
                    confidence=Decimal("1.0"),
                    source="IMPORT",
                    fallback_methods=fallbacks,
                )
            )
    return updated


def apply_bordereau_payment_rules(
    store: Any,
    tenant_id: str,
    bordereaux: Iterable[LcrRemittance | str | bytes | Path],
) -> list[dict[str, Any]]:
    """Record LCR as the priority habit of every drawer in a bordereau (P20)."""
    tenant = validate_tenant_id(tenant_id)
    parsed, errors = _coerce_bordereaux(bordereaux)
    if errors:
        raise CustomerReconciliationError("INVALID_BORDEREAU", "; ".join(errors))
    return _apply_bordereau_rules_parsed(store, tenant, parsed)


def reconcile_customer_invoices(
    store: Any,
    tenant_id: str,
    bank_lines: Sequence[Any] | OfxStatement | None = None,
    *,
    bordereaux: Iterable[LcrRemittance | str | bytes | Path] = (),
    bordereau_paths: Iterable[LcrRemittance | str | bytes | Path] | None = None,
    ofx_statement: OfxStatement | None = None,
    statement: OfxStatement | None = None,
    window_days: int = 60,
    window_before_days: int | None = None,
    window_after_days: int | None = None,
    only_methods: Sequence[str] | None = None,
) -> CustomerReconciliationReport:
    """Letter every LCR remittance / customer transfer against open invoices.

    Bank credits are replayed in strict ``DTPOSTED`` ascending order, so an
    early remittance consumes the invoices due around its own date before a
    later slip (for example a tardy bordereau) can preempt them.
    """
    tenant = validate_tenant_id(tenant_id)
    if not isinstance(window_days, int) or isinstance(window_days, bool) or window_days < 0:
        raise CustomerReconciliationError("INVALID_WINDOW_DAYS", repr(window_days))
    if bordereau_paths is not None:
        bordereaux = bordereau_paths
    if isinstance(bordereaux, (str, bytes, bytearray, Path, LcrRemittance)):
        bordereaux = (bordereaux,)
    if bank_lines is None:
        bank_lines = ofx_statement or statement
    if isinstance(bank_lines, (str, bytes, bytearray)):
        bank_lines = parse_ofx_statement(bank_lines)
    if bank_lines is None:
        raw_lines = [
            row
            for row in store.list_master_lines(tenant)
            if str(row.get("document_type") or "").upper() == "OFX"
            or str(row.get("line_id") or "").startswith("OFX-")
        ]
    elif isinstance(bank_lines, OfxStatement):
        raw_lines = list(bank_lines.transactions)
    else:
        raw_lines = list(bank_lines)
    parsed_bordereaux, errors = _coerce_bordereaux(bordereaux)
    if parsed_bordereaux:
        _apply_bordereau_rules_parsed(store, tenant, parsed_bordereaux)
    rules = {
        normalize_counterparty_name(rule["counterparty"]): rule
        for rule in store.list_counterparty_payment_rules(tenant)
    }
    invoices = load_pending_invoices(store, tenant)
    pending = {
        invoice.line_id: invoice
        for invoice in invoices
        if invoice.pending
    }
    allowed = (
        {str(method).upper() for method in only_methods}
        if only_methods is not None and not isinstance(only_methods, str)
        else (
            {only_methods.upper()}
            if isinstance(only_methods, str)
            else {"LCR_ENCAISSEMENT_LOT", "VIREMENT", "REMISE_CHEQUES"}
        )
    )
    prepared: list[tuple[date, int, dict[str, Any]]] = []
    for index, raw in enumerate(raw_lines):
        values = _bank_values(raw)
        if values["amount"] <= 0:
            continue
        method = str(values.get("payment_method") or "").upper()
        if method not in allowed:
            continue
        prepared.append(
            (values.get("operation_date") or date.max, index, values)
        )
    prepared.sort(key=lambda item: (item[0], item[1]))
    payments: list[PaymentLettering] = []
    remise_used_clients: dict[str, set[str]] = {}
    for _, _, values in prepared:
        method = str(values.get("payment_method") or "").upper()
        operations = values.get("operation_date")
        reference = _bank_reference(
            REMISE
            if method in {"LCR_ENCAISSEMENT_LOT", "REMISE_CHEQUES"}
            else VIREMENT,
            values,
        )
        if method == "LCR_ENCAISSEMENT_LOT":
            remise_key = _remise_key(reference)
            used_clients = remise_used_clients.setdefault(remise_key, set())
            remittance = _find_bordereau(parsed_bordereaux, values["amount"])
            if remittance is not None:
                payment, allocation_errors = _reconcile_bordereau(
                    store,
                    tenant,
                    values,
                    remittance,
                    pending,
                    reference,
                    operations,
                )
                errors.extend(allocation_errors)
            else:
                payment = _reconcile_remittance_combination(
                    store,
                    tenant,
                    values,
                    invoices,
                    pending,
                    reference,
                    operations,
                    window_days,
                    method="LCR",
                    kind=REMISE,
                    rules=rules,
                    window_before_days=window_before_days,
                    window_after_days=window_after_days,
                    forbidden_clients=used_clients,
                )
            for lettering in payment.invoices:
                used_clients.add(lettering.customer)
        elif method == "REMISE_CHEQUES":
            payment = _reconcile_remittance_combination(
                store,
                tenant,
                values,
                invoices,
                pending,
                reference,
                operations,
                window_days,
                method="CHEQUE",
                kind=REMISE_CHEQUES,
                rules=rules,
                window_before_days=window_before_days,
                window_after_days=window_after_days,
            )
        else:
            payment = _reconcile_transfer(
                store,
                tenant,
                values,
                invoices,
                pending,
                reference,
                operations,
                rules=rules,
            )
        payments.append(payment)
        if not payment.matched and payment.reason:
            errors.append(f"{payment.bank_reference}:{payment.reason}")
    reconciled = sum(len(payment.invoices) for payment in payments)
    return CustomerReconciliationReport(
        payments=tuple(payments),
        reconciled_invoices=reconciled,
        pending_invoices=len(pending),
        errors=tuple(errors),
    )


letter_customer_payments = reconcile_customer_invoices
reconcile_customer_payments = reconcile_customer_invoices


__all__ = [
    "CustomerReconciliationError",
    "CustomerReconciliationReport",
    "InvoiceLettering",
    "PaymentLettering",
    "REMISE",
    "REMISE_CHEQUES",
    "ReconciliableInvoice",
    "apply_bordereau_payment_rules",
    "VIREMENT",
    "counterparty_names_match",
    "find_exact_invoice_combination",
    "find_remittance_combination",
    "find_single_invoice_or_avoir",
    "letter_customer_payments",
    "load_pending_invoices",
    "reconcile_customer_invoices",
    "reconcile_customer_payments",
]
