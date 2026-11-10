"""P27-quater - Strict bank reconciliation guard rails.

Three rules are enforced here and reused by the OFX parser, the tier
reference, the four-stage pipeline and the spreadsheet exporters:

1. any document number / label matching ``FA\\s*1\\d{4,}`` is an APD customer
   sale (``Rôle = CLIENT``, ``Catégorie = VENTES_CLIENTS``) and must never be
   evaluated with expense keywords (fuel, tolls, restaurants, ...);
2. generic bank wording (``PAR INTERNET``, ``VIR SEPA``, ...) is never a real
   third party;
3. a ``REMISE ENCAISSEMENT N ...`` operation is a customer collection group,
   displayed as ``CLIENTS_ENCAISSEMENT_GROUPE`` (role ``CLIENT``, category
   ``VENTES_CLIENTS``), never as ``INSTRUMENT DE PAIEMENT``.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from schemas.folder_state_schema import DocumentCategory

CLIENT_INVOICE_PATTERN = re.compile(r"FA\s*1\d{4,}", re.IGNORECASE)
CLIENT_INVOICE_REFERENCE_PATTERN = re.compile(r"^FA\s*1\d{4,}", re.IGNORECASE)
CLIENT_ROLE = "CLIENT"
CLIENT_SALES_CATEGORY = DocumentCategory.VENTES_CLIENTS.value

# P27-quinquies - a fully automatic invoice + credit-note combination may
# never contain more than four pieces. Longer combinations become
# statistically meaningless and wrongly letter large supplier LCRs against an
# arbitrary mix of invoices. Explicit remittance bordereaux that list their
# ``FA1*`` invoice numbers are not a subset-sum search and stay unrestricted.
MAX_SUBSET_SUM_PIECES = 4

# P27-sexies - Boxtal and Chronopost are always settled one bank movement for
# one document. A subset-sum (or any invoice mix) is forbidden for them: a
# debit matches a single invoice and a credit matches a single credit note.
STRICT_ONE_TO_ONE_COUNTERPARTIES: frozenset[str] = frozenset(
    {"BOXTAL", "CHRONOPOST"}
)

# Payment methods for which a document dated after the bank movement can never
# be lettered: an invoice cannot be paid before it exists.
ZERO_DAY_PAYMENT_METHODS: frozenset[str] = frozenset(
    {
        "LCR",
        "LCR_ENCAISSEMENT_LOT",
        "LCR_FOURNISSEUR_DEBIT",
        "REMISE",
        "REMISE_ENCAISSEMENT",
        "REMISE_CHEQUES",
        "VIREMENT",
        "VIR",
    }
)

CLIENTS_ENCAISSEMENT_GROUPE = "CLIENTS_ENCAISSEMENT_GROUPE"
VIREMENT_A_QUALIFIER = "VIREMENT_A_QUALIFIER"
INSTRUMENT_A_QUALIFIER = "INSTRUMENT_A_QUALIFIER"
VIREMENT_A_QUALIFIER_SUGGESTION = (
    "COMPTE_COURANT_ASSOCIE / TRESORERIE_INTERNE / HOLDING_APD"
)

INVALID_TIER_NAMES: frozenset[str] = frozenset(
    {
        "PAR INTERNET",
        "VIREMENT PAR INTERNET",
        "VIR SEPA",
        "PRLV SEPA",
        "INSTRUMENT DE PAIEMENT",
    }
)
_INVALID_TIER_FOLDED = frozenset(name.casefold() for name in INVALID_TIER_NAMES)

_REMISE_ENCAISSEMENT = re.compile(r"REMISE\s+ENCAISSEMENT", re.IGNORECASE)
_REMISE_CHEQUES = re.compile(r"REMISE\s+(?:DE\s+)?CHEQUES?", re.IGNORECASE)
_PAR_INTERNET = re.compile(r"\b(?:VIREMENT|VIR)\s+PAR\s+INTERNET\b", re.IGNORECASE)
_INSTRUMENT_LABEL = re.compile(
    r"^\s*(?:LCR|RLV|CHEQUE|CHQ|REMISE)\b", re.IGNORECASE
)


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().casefold()


def _upper(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().upper()


def contains_client_invoice_reference(*values: Any) -> bool:
    """Return ``True`` when any value carries the APD customer ``FA1*`` series."""
    return any(
        bool(CLIENT_INVOICE_PATTERN.search(str(value or "")))
        for value in values
        if value is not None
    )


def is_client_invoice_reference(value: Any) -> bool:
    """Return ``True`` for a value *starting* with the customer ``FA1*`` series."""
    return bool(CLIENT_INVOICE_REFERENCE_PATTERN.match(str(value or "").strip()))


def is_remise_encaissement(value: Any) -> bool:
    return bool(_REMISE_ENCAISSEMENT.search(str(value or "")))


def is_remise_cheques(value: Any) -> bool:
    return bool(_REMISE_CHEQUES.search(str(value or "")))


def is_invalid_tier_name(value: Any) -> bool:
    """Return ``True`` for generic banking words that can never be a tier."""
    folded = _fold(value)
    if not folded:
        return True
    upper = _upper(value)
    if upper in INVALID_TIER_NAMES:
        return True
    return folded in _INVALID_TIER_FOLDED


def clean_bank_tier_name(value: Any) -> str | None:
    """Return a usable third-party name or ``None`` for generic bank noise."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text or is_invalid_tier_name(text):
        return None
    return text


def remise_counterparty_for(label: Any, current: Any = None) -> str:
    """Return the collection group of a remittance line."""
    if is_remise_encaissement(label) or is_remise_cheques(label):
        return CLIENTS_ENCAISSEMENT_GROUPE
    cleaned = clean_bank_tier_name(current)
    return cleaned or CLIENTS_ENCAISSEMENT_GROUPE


def unknown_transfer_counterparty(label: Any, current: Any = None) -> str | None:
    """Return ``VIREMENT_A_QUALIFIER`` for unidentified Internet transfers."""
    text = f"{label or ''} {current or ''}"
    if _PAR_INTERNET.search(str(label or "")) or is_invalid_tier_name(current):
        return VIREMENT_A_QUALIFIER
    return clean_bank_tier_name(current)


def is_virement_a_qualifier(*values: Any) -> bool:
    return any(
        _fold(value) == _fold(VIREMENT_A_QUALIFIER) for value in values if value
    )


def is_strict_one_to_one_counterparty(*values: Any) -> bool:
    """Return ``True`` for Boxtal / Chronopost (strict 1-to-1 settlement).

    These two suppliers are always settled by one card debit per invoice (and
    one credit per credit note). Combining several invoices to reach a bank
    amount is never allowed, even when the cent totals match.
    """
    folded = " ".join(_fold(value) for value in values if value)
    return any(
        token.casefold() in folded
        for token in STRICT_ONE_TO_ONE_COUNTERPARTIES
    )


def is_lcr_operation(*values: Any) -> bool:
    """Return ``True`` for an ``LCR`` bank instrument label."""
    folded = " ".join(_fold(value) for value in values if value)
    return bool(re.search(r"\blcr\b", folded))


def payment_date_allows(
    invoice_date: Any,
    payment_date: Any,
    *,
    tolerance_days: int = 0,
) -> bool:
    """Return ``False`` when a document would be paid before it exists.

    ``invoice_date`` and ``payment_date`` accept ``date``/``datetime`` or an
    ISO ``YYYY-MM-DD`` string. A missing date is never rejected (the caller
    cannot prove an anachronism).
    """
    if invoice_date is None or payment_date is None:
        return True

    def _as_date(value: Any) -> Any:
        if hasattr(value, "date") and not isinstance(value, str):
            return value.date() if hasattr(value, "hour") else value
        text = str(value or "").strip()[:10]
        if not text:
            return None
        from datetime import date as _date

        try:
            return _date.fromisoformat(text)
        except ValueError:
            return None

    left = _as_date(invoice_date)
    right = _as_date(payment_date)
    if left is None or right is None:
        return True
    return (right - left).days >= -abs(int(tolerance_days))



# P27-septies - a customer LCR settles invoices issued at the end of month
# M-2 (30 days end of month, due the 10th of M+2). A remittance therefore only
# matches invoices whose issue date lies between 25 and 62 days before it.
LCR_MATURITY_MIN_DAYS = 25
LCR_MATURITY_MAX_DAYS = 62
_CLIENT_PAID_STATUSES = frozenset(
    {
        "PAYEE_RAPPROCHEE",
        "PAYE",
        "PAYE_PRELEVE",
        "REMBOURSE_RAPPROCHE",
        "SOLDEE",
        "DEJA_PAYEE_2025",
        "SOLDEE_AVANT_DEPART",
        "SOLDEE_EXERCICE_ANTERIEUR_2025",
    }
)
_REMISE_KEY = re.compile(r"REMISE[^\d]{0,30}(\d{2,})", re.IGNORECASE)


def parse_iso_date(value: Any) -> date | None:
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


def lcr_maturity_window(payment_date: date) -> tuple[date, date]:
    """Return the ``[earliest, latest]`` invoice dates of an LCR remittance."""
    from datetime import timedelta

    return (
        payment_date - timedelta(days=LCR_MATURITY_MAX_DAYS),
        payment_date - timedelta(days=LCR_MATURITY_MIN_DAYS),
    )


def lcr_invoice_matches_maturity(
    invoice_date: Any,
    payment_date: Any,
    *,
    minimum_days: int = LCR_MATURITY_MIN_DAYS,
    maximum_days: int = LCR_MATURITY_MAX_DAYS,
) -> bool:
    """True when an invoice is due on the 30-days-end-of-month LCR cycle.

    A missing date is accepted (the caller cannot prove an anachronism), but
    an invoice issued 8 days or 8 months before the remittance is rejected.
    """
    issued = parse_iso_date(invoice_date)
    paid = parse_iso_date(payment_date)
    if issued is None or paid is None:
        return True
    elapsed = (paid - issued).days
    return minimum_days <= elapsed <= maximum_days


def remise_number(value: Any) -> str:
    match = _REMISE_KEY.search(str(value or ""))
    return match.group(1) if match is not None else ""


def format_euro(value: Any) -> str:
    """French display amount: ``9 678,93`` (no currency symbol)."""
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return str(value or "")
    raw = f"{amount:,.2f}"
    return raw.replace(",", "\u00a0").replace(".", ",")


def remittance_explanation(
    reference: Any,
    total: Any,
    matched_amount: Any,
    invoice_numbers: Iterable[Any] = (),
) -> str:
    """Explain a remittance without ever claiming it has no invoice.

    ``REMISE 93847`` must read as ``X € déjà rapprochés (FA16496...) — Reste
    Y € à justifier`` as soon as at least one invoice is lettered.
    """
    number = remise_number(reference) or str(reference or "").strip()
    matched = Decimal(str(matched_amount or "0"))
    total_value = Decimal(str(total or "0"))
    remaining = (total_value - matched).quantize(Decimal("0.01"))
    names = [
        str(item).strip()
        for item in invoice_numbers
        if str(item).strip()
    ]
    if matched > 0:
        detail = f" ({', '.join(names)})" if names else ""
        return (
            f"Remise {number} (Total {format_euro(total_value)} €) : "
            f"{format_euro(matched)} € déjà rapprochés{detail} — "
            f"Reste {format_euro(remaining)} € à justifier "
            "(bordereau ou factures clients manquantes)"
        )
    return (
        f"Remise {number} (Total {format_euro(total_value)} €) : "
        f"aucune facture rapprochée — Reste {format_euro(total_value)} € "
        "à justifier (bordereau ou factures clients manquantes)"
    )


def _row_is_bank(row: dict[str, Any]) -> bool:
    document_type = str(row.get("document_type") or "").strip().upper()
    line_id = str(row.get("line_id") or "").strip().upper()
    return document_type in {"OFX", "QFX"} or line_id.startswith("OFX-") or (
        row.get("debit_amount") not in (None, "")
        or row.get("credit_amount") not in (None, "")
    )


def _row_is_customer_invoice(row: dict[str, Any]) -> bool:
    return (
        not _row_is_bank(row)
        and str(row.get("category") or "")
        == DocumentCategory.VENTES_CLIENTS.value
    )


def _clear_row(
    store: Any,
    tenant_id: str,
    row: dict[str, Any],
    *,
    bank: bool,
) -> None:
    line_id = str(row.get("line_id") or "")
    if not line_id:
        return
    updated = dict(row)
    updated.pop("line_id", None)
    updated.update(
        {
            "reconciliation_status": None,
            "reconciliation_date": None,
            "reconciliation_reference": None,
            "reconciliation_amount": None,
            "balance_due": None,
        }
    )
    if bank:
        updated["invoice_id"] = None
    try:
        store.force_upsert_document_line(tenant_id, line_id, updated)
    except AttributeError:
        store.upsert_document_line(tenant_id, line_id, updated)


def purge_invalid_lcr_remittances(
    store: Any, tenant_id: str
) -> int:
    """Reset automatic LCR lettering that violates the monthly maturity rule.

    This clears the historical errors (``FA16409`` dated 30/11/2025 lettered
    on ``REMISE 93696`` in July 2026, ``FA16509`` dated 31/07/2026 lettered 8
    days later, the four 144.00 EUR ``CALANDRE_18`` fillers on ``REMISE
    94694``) before stage 2 recomputes the lettering.
    """
    try:
        rows = store.list_master_lines(tenant_id)
    except Exception:  # noqa: BLE001 - purge is best effort
        return 0
    bank_rows = [row for row in rows if _row_is_bank(row)]
    remise_dates: dict[str, date] = {}
    remise_totals: dict[str, Decimal] = {}
    for row in bank_rows:
        label = str(
            row.get("label")
            or row.get("reconciliation_reference")
            or row.get("payment_reference")
            or ""
        )
        number = remise_number(label)
        if not number:
            continue
        operation_date = parse_iso_date(row.get("operation_date"))
        if operation_date is not None:
            remise_dates.setdefault(number, operation_date)
        credit = row.get("credit_amount")
        try:
            amount = Decimal(str(credit)) if credit not in (None, "") else Decimal("0")
        except (InvalidOperation, ValueError):
            amount = Decimal("0")
        if amount > 0:
            remise_totals[number] = amount.quantize(Decimal("0.01"))
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if not _row_is_customer_invoice(row):
            continue
        reference = str(
            row.get("reconciliation_reference")
            or row.get("fitid")
            or ""
        )
        number = remise_number(reference)
        if not number:
            continue
        customer = _fold(row.get("counterparty") or "")
        groups.setdefault((number, customer), []).append(row)
    reset_ids: set[str] = set()
    reset_bank_ids: set[str] = set()
    for (number, _customer), items in groups.items():
        payment_date = remise_dates.get(number)
        if payment_date is None:
            continue
        positives: list[dict[str, Any]] = []
        credit_notes = 0
        invalid = False
        for row in items:
            invoice_date = parse_iso_date(row.get("operation_date"))
            if invoice_date is None:
                continue
            if not lcr_invoice_matches_maturity(invoice_date, payment_date):
                invalid = True
            amount = row.get("amount_ttc")
            try:
                value = Decimal(str(amount or "0"))
            except (InvalidOperation, ValueError):
                value = Decimal("0")
            if value < 0 or str(row.get("document_type") or "").upper() in {
                "AVOIR",
                "CREDIT_NOTE",
            }:
                credit_notes += 1
            else:
                positives.append(row)
        dates = {
            parse_iso_date(row.get("operation_date")) for row in positives
        }
        if len(positives) > 2 or len(dates) > 1 or credit_notes > 1:
            invalid = True
        if not invalid:
            continue
        for row in items:
            reset_ids.add(str(row.get("line_id") or ""))
        for bank in bank_rows:
            label = str(
                bank.get("label")
                or bank.get("reconciliation_reference")
                or ""
            )
            if remise_number(label) == number:
                reset_bank_ids.add(str(bank.get("line_id") or ""))
    for row in rows:
        line_id = str(row.get("line_id") or "")
        if line_id in reset_ids:
            _clear_row(store, tenant_id, row, bank=False)
        elif line_id in reset_bank_ids:
            _clear_row(store, tenant_id, row, bank=True)
    return len(reset_ids) + len(reset_bank_ids)


def client_sales_routing(
    *,
    counterparty: Any = None,
    label: Any = None,
    document_number: Any = None,
    invoice_url: Any = None,
) -> tuple[str, str, str] | None:
    """Return ``(counterparty, role, category)`` for an ambiguous ``FA1*`` row.

    ``None`` means the guard does not apply. When it applies, the caller must
    force the row into the customer sales ledger without evaluating any
    expense keyword.
    """
    if not contains_client_invoice_reference(
        document_number, label, invoice_url, counterparty
    ):
        return None
    name = clean_bank_tier_name(counterparty) or clean_bank_tier_name(label) or ""
    return name, CLIENT_ROLE, CLIENT_SALES_CATEGORY


def bank_tier_defaults(
    *,
    label: Any,
    counterparty: Any = None,
    direction: str = "",
) -> tuple[str, str, str]:
    """Return ``(tier, role, category)`` for a clean bank line display.

    Unqualified Internet transfers become ``VIREMENT_A_QUALIFIER``; remittances
    become ``CLIENTS_ENCAISSEMENT_GROUPE``; generic noise never leaks a fake
    third-party name.
    """
    if is_remise_encaissement(label) or is_remise_cheques(label):
        return CLIENTS_ENCAISSEMENT_GROUPE, CLIENT_ROLE, CLIENT_SALES_CATEGORY
    if _INSTRUMENT_LABEL.search(str(label or "")):
        return INSTRUMENT_A_QUALIFIER, "", ""
    if _PAR_INTERNET.search(str(label or "")) or is_invalid_tier_name(counterparty):
        return VIREMENT_A_QUALIFIER, "", ""
    if _fold(counterparty) in {"sepa", "internet"}:
        return VIREMENT_A_QUALIFIER, "", ""
    cleaned = clean_bank_tier_name(counterparty)
    if cleaned:
        return cleaned, "", ""
    return "", "", ""


__all__ = [
    "CLIENTS_ENCAISSEMENT_GROUPE",
    "CLIENT_INVOICE_PATTERN",
    "LCR_MATURITY_MAX_DAYS",
    "LCR_MATURITY_MIN_DAYS",
    "CLIENT_INVOICE_REFERENCE_PATTERN",
    "CLIENT_ROLE",
    "CLIENT_SALES_CATEGORY",
    "INSTRUMENT_A_QUALIFIER",
    "INVALID_TIER_NAMES",
    "MAX_SUBSET_SUM_PIECES",
    "STRICT_ONE_TO_ONE_COUNTERPARTIES",
    "VIREMENT_A_QUALIFIER",
    "VIREMENT_A_QUALIFIER_SUGGESTION",
    "ZERO_DAY_PAYMENT_METHODS",
    "bank_tier_defaults",
    "clean_bank_tier_name",
    "client_sales_routing",
    "contains_client_invoice_reference",
    "is_client_invoice_reference",
    "is_invalid_tier_name",
    "is_lcr_operation",
    "is_remise_cheques",
    "is_remise_encaissement",
    "is_strict_one_to_one_counterparty",
    "is_virement_a_qualifier",
    "lcr_invoice_matches_maturity",
    "lcr_maturity_window",
    "parse_iso_date",
    "payment_date_allows",
    "purge_invalid_lcr_remittances",
    "remise_counterparty_for",
    "remise_number",
    "remittance_explanation",
    "unknown_transfer_counterparty",
]
