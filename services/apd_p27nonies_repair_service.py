"""P27-nonies - deterministic repairs for the director's four screenshots.

Applied before every cockpit refresh and at the start of stage 1:

1. ``BPCE IARD`` is a VAT-exempt insurance premium
   (``FRAIS_GENERAUX / ASSURANCES_SOCIETE``, account ``616000``);
2. invoice ``19544`` belongs to ``MISE_SUR_ORBITE`` and never to the ``ACTION``
   supermarket just because the body contains the word "action";
3. ``ACTION`` invoice ``18417`` leaves the IT/web sub-table for generic
   ``FRAIS_GENERAUX``;
4. ``DetailRemise_1_`` (an LCR remittance slip) is rejected;
5. the OpenRouter ``Receipt`` twin is rejected and card debits are unified with
   their invoices (actual EUR debit, VAT ``0,00``);
6. every raw ``CAISSE_D_EPARGNE_SOURCE_OFX_*.pdf`` link is removed from the
   ledger.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from schemas.folder_state_schema import DocumentCategory, DocumentStatus
from services.tier_reference_service import TierReferenceService

_CENT = Decimal("0.01")
_BPCE_IARD = "BPCE IARD"
_BPCE_CANONICAL = "CAISSE_D_EPARGNE_ASSURANCE"
_MISE_SUR_ORBITE = "MISE SUR ORBITE"
_ACTION = "ACTION"
_OPENROUTER = "OPENROUTER"
_INSURANCE_ACCOUNT = "616000"
_ASSURANCE_MARKER = "TIER:CAISSE_D_EPARGNE_ASSURANCE:FOURNISSEUR"
_OPENROUTER_MARKER = "TIER:OPENROUTER:FOURNISSEUR"
_MISE_MARKER = "TIER:MISE_SUR_ORBITE:FOURNISSEUR"
_ACTION_MARKER = "TIER:ACTION:FOURNISSEUR"
_TECHNICAL_DOCUMENT_TYPES = frozenset(
    {"BORDEREAU_LCR", "LCR_REMITTANCE", "SOURCE_OFX", "DOUBLON_TECHNIQUE"}
)


@dataclass(frozen=True)
class P27NoniesReport:
    insurance_rows: int = 0
    mise_sur_orbite_rows: int = 0
    action_rows: int = 0
    technical_removed: int = 0
    openrouter_receipts_removed: int = 0
    openrouter_payments_unified: int = 0
    fake_ofx_links_removed: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().casefold()


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
    document_type = str(row.get("document_type") or "").upper()
    line_id = str(row.get("line_id") or "").upper()
    return (
        document_type in {"OFX", "QFX"}
        or line_id.startswith("OFX-")
        or row.get("debit_amount") not in (None, "")
        or row.get("credit_amount") not in (None, "")
    )


def _is_fake_ofx(value: Any) -> bool:
    normalized = str(value or "").replace("\\", "/").upper()
    if not normalized:
        return False
    name = normalized.rsplit("/", 1)[-1]
    return "SOURCE_OFX" in name or "CAISSE_D_EPARGNE_SOURCE_OFX" in name


def _movement(row: dict[str, Any]) -> Decimal | None:
    amount = _decimal(row.get("amount_ttc"))
    if amount is not None:
        return amount
    debit = _decimal(row.get("debit_amount"))
    credit = _decimal(row.get("credit_amount"))
    if debit is not None or credit is not None:
        return abs((debit or Decimal("0")) - (credit or Decimal("0"))).quantize(
            _CENT
        )
    return None


def _update(store: Any, tenant_id: str, row: dict[str, Any], **changes: Any) -> bool:
    line_id = str(row.get("line_id") or "")
    if not line_id:
        return False
    updated = dict(row)
    updated.pop("line_id", None)
    changed = False
    for key, value in changes.items():
        if str(updated.get(key) or "") != str(value or ""):
            changed = True
        updated[key] = value
    if not changed:
        return False
    store.force_upsert_document_line(tenant_id, line_id, updated)
    return True


def _fact_date_in_label(label: Any) -> date | None:
    match = re.search(
        r"FACT(?:URE)?\s*(\d{2})(\d{2})(\d{2})(?!\d)",
        str(label or "").upper(),
    )
    if match is None:
        return None
    try:
        return date(2000 + int(match.group(3)), int(match.group(2)), int(match.group(1)))
    except ValueError:
        return None


def _openrouter_purchase_date(row: dict[str, Any]) -> date | None:
    explicit = _parse_date(row.get("operation_date"))
    fact = _fact_date_in_label(row.get("label"))
    if fact is not None and explicit is not None and abs((explicit - fact).days) <= 5:
        return fact
    return explicit or fact


def repair_p27nonies(
    store: Any,
    tenant_id: str,
    *,
    tenant_dir: str | os.PathLike[str] | None = None,
    export_dir: str | os.PathLike[str] | None = None,
    tier_reference: TierReferenceService | None = None,
    workbook: Any | None = None,
) -> P27NoniesReport:
    """Apply the P27-nonies corrections directly to the SQLite ledger."""
    del tenant_dir, export_dir  # reserved for future PDF archiving
    tiers = tier_reference or TierReferenceService(
        store=store, tenant_id=tenant_id
    )
    try:
        tiers.bootstrap()
    except Exception:  # noqa: BLE001 - the workbook is best effort
        pass

    rows = store.list_master_lines(tenant_id)
    insurance = mise = action = technical = receipts = unified = fake = 0

    # 1. BPCE IARD insurance premiums ------------------------------------
    for row in rows:
        haystack = _fold(
            f"{row.get('counterparty') or ''} {row.get('label') or ''}"
        )
        if _BPCE_IARD.casefold() not in haystack:
            continue
        amount = _movement(row) or Decimal("0.00")
        changes = {
            "counterparty": _BPCE_CANONICAL,
            "category": DocumentCategory.FRAIS_GENERAUX.value,
            "expense_subcategory": "ASSURANCES_SOCIETE",
            "amount_ht": str(amount),
            "amount_tva": "0.00",
            "amount_ttc": str(amount),
            "payment_source": "BANQUE_PRO",
            "counterparty_account": _INSURANCE_ACCOUNT,
            "is_classified_marker": _ASSURANCE_MARKER,
        }
        if _update(store, tenant_id, row, **changes):
            insurance += 1

    # 2. MISE SUR ORBITE invoice 19544 -----------------------------------
    for row in rows:
        reference = _fold(
            f"{row.get('invoice_id') or ''} {row.get('invoice_url') or ''} "
            f"{row.get('piece_pdf') or ''} {row.get('label') or ''}"
        )
        is_target = (
            str(row.get("invoice_id") or "").strip() == "19544"
            or "19544" in reference
            or (
                _MISE_SUR_ORBITE.casefold().replace(" ", "")
                in reference.replace(" ", "")
            )
        )
        if not is_target:
            continue
        changes = {
            "counterparty": _MISE_SUR_ORBITE,
            "category": DocumentCategory.FRAIS_GENERAUX.value,
            "expense_subcategory": "INFORMATIQUE_WEB",
            "is_classified_marker": _MISE_MARKER,
        }
        if not _is_bank_row(row):
            changes.update(
                {
                    "document_type": "FACTURE",
                    "status": DocumentStatus.VALIDE.value,
                }
            )
        if _update(store, tenant_id, row, **changes):
            mise += 1

    # 3. ACTION invoice 18417 leaves the IT/web table --------------------
    for row in rows:
        if _fold(row.get("counterparty")) != _fold(_ACTION):
            continue
        if not _is_bank_row(row) and str(row.get("invoice_id") or "").strip() == "18417":
            if _update(
                store,
                tenant_id,
                row,
                category=DocumentCategory.FRAIS_GENERAUX.value,
                expense_subcategory=None,
                is_classified_marker=_ACTION_MARKER,
            ):
                action += 1

    # 4. DetailRemise_1_ is an LCR remittance, never an expense ----------
    for row in rows:
        haystack = _fold(
            f"{row.get('label') or ''} {row.get('invoice_id') or ''} "
            f"{row.get('invoice_url') or ''} {row.get('piece_pdf') or ''}"
        )
        if "detailremise" not in haystack.replace(" ", ""):
            continue
        if _update(
            store,
            tenant_id,
            row,
            status=DocumentStatus.REJETE.value,
            document_type="DOUBLON_TECHNIQUE",
            expense_subcategory=None,
            piece_pdf=None,
            invoice_url=None,
        ):
            technical += 1

    # 5. OpenRouter receipt / invoice duplicate --------------------------
    openrouter = [
        row
        for row in rows
        if _fold(row.get("counterparty")) == _fold(_OPENROUTER)
    ]
    invoices = [
        row
        for row in openrouter
        if not _is_bank_row(row)
        and "invoice" in _fold(
            row.get("label") or row.get("invoice_id") or ""
        )
    ]
    for row in openrouter:
        if _is_bank_row(row):
            continue
        label = _fold(row.get("label") or row.get("invoice_id") or "")
        if "receipt" not in label:
            continue
        row_date = _parse_date(row.get("operation_date"))
        row_amount = _movement(row)
        twin = next(
            (
                candidate
                for candidate in invoices
                if _parse_date(candidate.get("operation_date")) == row_date
                and _movement(candidate) == row_amount
            ),
            None,
        )
        if twin is None:
            continue
        if _update(
            store,
            tenant_id,
            row,
            status=DocumentStatus.REJETE.value,
            document_type="DOUBLON_TECHNIQUE",
            is_classified_marker=_OPENROUTER_MARKER,
        ):
            receipts += 1

    # 6. OpenRouter card debits: real EUR amount + payment date ----------
    invoice_by_date: dict[date, dict[str, Any]] = {}
    for row in invoices:
        if str(row.get("status") or "").upper() == DocumentStatus.REJETE.value:
            continue
        purchase = _openrouter_purchase_date(row)
        if purchase is not None:
            invoice_by_date.setdefault(purchase, row)
    for row in openrouter:
        if not _is_bank_row(row):
            continue
        amount = _movement(row)
        fact_date = _fact_date_in_label(row.get("label"))
        purchase = fact_date or _parse_date(row.get("operation_date"))
        twin = invoice_by_date.get(purchase) if purchase is not None else None
        changes: dict[str, Any] = {
            "counterparty": _OPENROUTER,
            "category": DocumentCategory.FRAIS_GENERAUX.value,
            "expense_subcategory": "INFORMATIQUE_WEB",
            "is_classified_marker": _OPENROUTER_MARKER,
        }
        if amount is not None:
            changes.update(
                {
                    "amount_ht": str(amount),
                    "amount_tva": "0.00",
                    "amount_ttc": str(amount),
                }
            )
        if twin is not None:
            changes.update(
                {
                    "reconciliation_status": "PAYEE_RAPPROCHEE",
                    "reconciliation_date": row.get("operation_date"),
                    "reconciliation_reference": twin.get("invoice_id"),
                    "reconciliation_amount": str(amount or "0.00"),
                    "already_paid_on_invoice": "1",
                    "balance_due": "0.00",
                }
            )
            unified += 1
        if _update(store, tenant_id, row, **changes):
            pass

    # 7. Never keep a raw OFX statement as a piece PDF -------------------
    for row in store.list_master_lines(tenant_id):
        piece = row.get("piece_pdf")
        url = row.get("invoice_url")
        if not _is_fake_ofx(piece) and not _is_fake_ofx(url):
            continue
        if _update(
            store,
            tenant_id,
            row,
            piece_pdf=None if _is_fake_ofx(piece) else piece,
            invoice_url=None if _is_fake_ofx(url) else url,
        ):
            fake += 1

    # 8. Keep the hidden technical workbook aligned so a later human-decision
    # pass cannot resurrect the corrected values.
    if workbook is not None:
        try:
            technical_rows = list(workbook.read_rows())
        except Exception:  # noqa: BLE001 - locked workbook is best effort
            technical_rows = []
        for technical_row in technical_rows:
            name = str(technical_row.nom_fichier or "").upper()
            compact = name.replace("_", "")
            try:
                if "18417" in name:
                    workbook.update_p17_metadata(
                        technical_row.sha256, expense_subcategory=""
                    )
                if "DETAILREMISE" in compact:
                    workbook.align_statut(
                        technical_row.sha256, DocumentStatus.REJETE.value
                    )
                if name.startswith(
                    ("TIER__BPCE IARD", "TIER__MISE SUR ORBITE")
                ):
                    workbook.align_statut(
                        technical_row.sha256, DocumentStatus.VALIDE.value
                    )
            except Exception:  # noqa: BLE001 - one row never blocks
                continue

    return P27NoniesReport(
        insurance_rows=insurance,
        mise_sur_orbite_rows=mise,
        action_rows=action,
        technical_removed=technical,
        openrouter_receipts_removed=receipts,
        openrouter_payments_unified=unified,
        fake_ofx_links_removed=fake,
    )


__all__ = ["P27NoniesReport", "repair_p27nonies"]
