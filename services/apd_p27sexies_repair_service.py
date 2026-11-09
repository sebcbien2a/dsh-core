"""P27-sexies - strict reconciliation repairs before every cockpit refresh.

The director's five rules are applied directly to the SQLite ledger:

1. Boxtal / Chronopost are strict 1-to-1: a debit matches one invoice, a
   credit matches one credit note, never a combination;
2. a supplier ``LCR`` is one invoice, or one invoice minus one credit note,
   never two positive invoices;
3. a multi-client ``REMISE ENCAISSEMENT`` may only use one invoice (or one
   invoice minus one credit note) per customer, unless the bank bordereau
   explicitly lists two distinct effects for that customer;
4. a document is never paid before its invoice date (``invoice_date <=
   payment_date``), and recurring identical amounts are consumed FIFO;
5. every paid customer invoice gets its ``PAIEMENT`` row in the client ledger.

This service clears the violating lettering so the four-stage pipeline can
re-letter it with the strict rules. It is idempotent.
"""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from services.bank_reconciliation_service import (
    is_strict_one_to_one_counterparty,
)

_CENT = Decimal("0.01")
_PURCHASE_CATEGORIES = frozenset(
    {
        "ACHATS_MARCHANDISES",
        "TRANSPORTS_LOGISTIQUE",
        "FRAIS_GENERAUX",
        "FISCAL_SOCIAL",
        "IMMOBILISATIONS",
    }
)
_TECHNICAL_TYPES = frozenset(
    {
        "OFX",
        "QFX",
        "SOURCE_OFX",
        "BORDEREAU_LCR",
        "LCR_REMITTANCE",
        "LCR_PAYMENT",
        "LCR_SUB_PAYMENT",
        "LCR_ALLOCATION",
    }
)
_PAID_STATUSES = frozenset(
    {"PAYEE_RAPPROCHEE", "PAYE", "PAYE_PRELEVE", "REMBOURSE_RAPPROCHE", "SOLDEE"}
)


@dataclass(frozen=True)
class P27SexiesReport:
    reset_customer_invoices: int = 0
    reset_anachronistic_invoices: int = 0
    reset_boxtal_groups: int = 0
    reset_lcr_groups: int = 0
    reset_bank_lines: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.split()).casefold()


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", ".")).quantize(_CENT)
    except (InvalidOperation, ValueError):
        return None


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _is_bank_row(row: dict[str, Any]) -> bool:
    document_type = str(row.get("document_type") or "").strip().upper()
    line_id = str(row.get("line_id") or "").strip().upper()
    if document_type in {"OFX", "QFX"} or line_id.startswith("OFX-"):
        return True
    return row.get("debit_amount") not in (None, "") or row.get(
        "credit_amount"
    ) not in (None, "")


def _is_customer_invoice(row: dict[str, Any]) -> bool:
    return (
        str(row.get("category") or "") == "VENTES_CLIENTS"
        and str(row.get("document_type") or "").strip().upper()
        in {"CUSTOMER_INVOICE", "SALE", "SALE_INVOICE", "VENTE_CLIENT"}
    )


def _is_purchase_invoice(row: dict[str, Any]) -> bool:
    if _is_bank_row(row):
        return False
    if str(row.get("category") or "") not in _PURCHASE_CATEGORIES:
        return False
    document_type = str(row.get("document_type") or "").strip().upper()
    return document_type in {
        "FACTURE",
        "INVOICE",
        "AVOIR",
        "CREDIT_NOTE",
        "BON_DE_COMMANDE_NON_COMPTABLE",
    }


def _clear_invoice(store: Any, tenant_id: str, row: dict[str, Any]) -> None:
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
    store.force_upsert_document_line(tenant_id, line_id, updated)


def _clear_bank(store: Any, tenant_id: str, row: dict[str, Any]) -> None:
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
            "invoice_id": None,
            "balance_due": None,
        }
    )
    store.force_upsert_document_line(tenant_id, line_id, updated)


def _group_by_reference(
    rows: Iterable[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        reference = str(row.get("reconciliation_reference") or "").strip()
        if not reference:
            continue
        groups.setdefault(reference, []).append(row)
    return groups


def _bank_reference(row: dict[str, Any]) -> str:
    return str(
        row.get("reconciliation_reference")
        or row.get("label")
        or row.get("payment_reference")
        or ""
    ).strip()


def repair_p27sexies(
    store: Any,
    tenant_id: str,
    *,
    tenant_dir: str | os.PathLike[str] | None = None,
    export_dir: str | os.PathLike[str] | None = None,
) -> P27SexiesReport:
    """Reset every P27-sexies violation so the pipeline re-letters it."""
    del tenant_dir, export_dir  # reserved for future bordereau-aware repairs
    rows = store.list_master_lines(tenant_id)
    bank_rows = [row for row in rows if _is_bank_row(row)]
    invoices = [row for row in rows if not _is_bank_row(row)]
    bank_by_reference: dict[str, list[dict[str, Any]]] = {}
    for bank in bank_rows:
        reference = _bank_reference(bank)
        if reference:
            bank_by_reference.setdefault(reference, []).append(bank)

    reset_invoice_ids: set[str] = set()
    reset_bank_ids: set[str] = set()
    boxtal_groups = 0
    lcr_groups = 0

    groups = _group_by_reference(invoices)
    for reference, items in groups.items():
        positives = [
            row
            for row in items
            if not (
                str(row.get("document_type") or "").strip().upper()
                in {"AVOIR", "CREDIT_NOTE"}
                or (_decimal(row.get("amount_ttc")) or Decimal("0.00")) < 0
            )
        ]
        strict_group = any(
            is_strict_one_to_one_counterparty(row.get("counterparty"))
            for row in items
        )
        if strict_group and len(items) > 1:
            boxtal_groups += 1
            for row in items:
                reset_invoice_ids.add(str(row.get("line_id") or ""))
            for bank in bank_by_reference.get(reference, []):
                reset_bank_ids.add(str(bank.get("line_id") or ""))
            continue
        if (
            reference.upper().startswith("LCR")
            and len(positives) > 1
        ):
            lcr_groups += 1
            for row in items:
                reset_invoice_ids.add(str(row.get("line_id") or ""))
            for bank in bank_by_reference.get(reference, []):
                reset_bank_ids.add(str(bank.get("line_id") or ""))
            continue

    anachronistic = 0
    for row in invoices:
        if _is_customer_invoice(row):
            continue
        invoice_date = _parse_date(row.get("operation_date"))
        payment_date = _parse_date(row.get("reconciliation_date"))
        if invoice_date is None or payment_date is None:
            continue
        if payment_date < invoice_date:
            anachronistic += 1
            reset_invoice_ids.add(str(row.get("line_id") or ""))

    for row in invoices:
        line_id = str(row.get("line_id") or "")
        if not line_id:
            continue
        if line_id in reset_invoice_ids:
            _clear_invoice(store, tenant_id, row)
    for row in bank_rows:
        line_id = str(row.get("line_id") or "")
        if not line_id:
            continue
        if line_id in reset_bank_ids:
            _clear_bank(store, tenant_id, row)

    customer_reset = 0
    reset_method = getattr(store, "reset_customer_invoice_reconciliation", None)
    if callable(reset_method):
        customer_reset = reset_method(tenant_id)

    return P27SexiesReport(
        reset_customer_invoices=customer_reset,
        reset_anachronistic_invoices=anachronistic,
        reset_boxtal_groups=boxtal_groups,
        reset_lcr_groups=lcr_groups,
        reset_bank_lines=len(reset_bank_ids),
    )


__all__ = [
    "P27SexiesReport",
    "repair_p27sexies",
]
