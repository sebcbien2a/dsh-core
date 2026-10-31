"""Certified supplier memory: resolution, backfill and identity anti-corruption.

The service resolves an invoice against the ``SupplierRepository`` in cascade
(SIREN -> intra-EU VAT number -> fuzzy legal name/alias), then backfills the
certified identity fields into the ``InvoiceSchema`` before business rules run.
A contradicting extracted identifier never overrides a certified value: the
certified value is kept and an ``IDENTITY_CONFLICT`` warning is recorded.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Mapping

from schemas.invoice_schema import InvoiceParty, InvoiceSchema
from services.invoice_producer_protocol import normalise_country, normalise_vat
from services.supplier_repository import SupplierRepository

logger = logging.getLogger(__name__)

KNOWN_VAT_REGIMES = frozenset(
    {"INTRACOM_AUTOLIQUIDATION", "EXONERE_FRANCHISE_293B"}
)
DEFAULT_NAME_THRESHOLD = 0.82
_REGIME_KEYS = ("vat_regime", "regime_tva", "tva_regime", "vat_regime_certified")


@dataclass(frozen=True)
class SupplierMatch:
    raison_sociale: str
    siret: str | None
    vat_number: str | None
    country: str | None
    vat_regime: str | None
    supplier_id: str | None
    matched_by: str
    score: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "raison_sociale": self.raison_sociale,
            "siret": self.siret,
            "vat_number": self.vat_number,
            "country": self.country,
            "vat_regime": self.vat_regime,
            "supplier_id": self.supplier_id,
            "matched_by": self.matched_by,
            "score": self.score,
        }


def _extract_siren(siret: str | None) -> str | None:
    digits = re.sub(r"\D", "", str(siret or ""))
    return digits[:9] if len(digits) >= 9 else None


def _siren_from_vat(vat_number: str | None) -> str | None:
    if not vat_number:
        return None
    value = normalise_vat(vat_number)
    if len(value) == 13 and value.startswith("FR") and value[4:].isdigit():
        return value[4:]
    return None


def _certified_siret(entry: Mapping[str, Any]) -> str | None:
    digits = re.sub(r"\D", "", str(entry.get("siret") or ""))
    return digits if len(digits) == 14 else None


def _certified_vat(entry: Mapping[str, Any]) -> str | None:
    raw = entry.get("tva_intra") or entry.get("vat_number")
    if not raw:
        return None
    value = normalise_vat(str(raw))
    return value or None


def _certified_country(
    entry: Mapping[str, Any], vat_number: str | None
) -> str | None:
    country = normalise_country(entry.get("country") or entry.get("pays"))
    if country is None and vat_number and vat_number[:2].isalpha():
        country = normalise_country(vat_number[:2])
    return country


def _certified_regime(entry: Mapping[str, Any]) -> str | None:
    for key in _REGIME_KEYS:
        value = str(entry.get(key) or "").strip().upper()
        if value in KNOWN_VAT_REGIMES:
            return value
    return None


def _to_match(
    entry: Mapping[str, Any], matched_by: str, score: float | None
) -> SupplierMatch | None:
    name = str(entry.get("raison_sociale") or entry.get("name") or "").strip()
    if not name:
        return None
    vat_number = _certified_vat(entry)
    return SupplierMatch(
        raison_sociale=name,
        siret=_certified_siret(entry),
        vat_number=vat_number,
        country=_certified_country(entry, vat_number),
        vat_regime=_certified_regime(entry),
        supplier_id=(
            str(entry["supplier_id"]) if entry.get("supplier_id") else None
        ),
        matched_by=matched_by,
        score=score,
    )


def resolve_supplier(
    repository: SupplierRepository,
    invoice: InvoiceSchema,
    *,
    name_threshold: float = DEFAULT_NAME_THRESHOLD,
) -> tuple[SupplierMatch | None, list[dict[str, Any]]]:
    """Resolve an invoice supplier through the certified identity cascade."""

    supplier = invoice.supplier
    siret = supplier.siret if supplier else None
    vat_number = (
        normalise_vat(supplier.vat_number)
        if supplier and supplier.vat_number
        else None
    )
    name = supplier.name if supplier else None
    attempts: list[dict[str, Any]] = []

    siren = _extract_siren(siret) or _siren_from_vat(vat_number)
    if siren:
        entry = repository.find_by_siren(siren)
        attempts.append(
            {"strategy": "siren", "value": siren, "matched": entry is not None}
        )
        if entry is not None:
            match = _to_match(entry, "siren", 1.0)
            if match is not None:
                return match, attempts

    if vat_number:
        entry = repository.find_by_tva(vat_number)
        attempts.append(
            {
                "strategy": "vat",
                "value": vat_number,
                "matched": entry is not None,
            }
        )
        if entry is not None:
            match = _to_match(entry, "vat", 1.0)
            if match is not None:
                return match, attempts

    if name:
        entry = repository.find_by_name(name, threshold=name_threshold)
        attempts.append(
            {"strategy": "name", "value": name, "matched": entry is not None}
        )
        if entry is not None:
            match = _to_match(entry, "name", None)
            if match is not None:
                return match, attempts

    return None, attempts


def enrich_invoice(
    invoice: InvoiceSchema,
    repository: SupplierRepository,
    *,
    name_threshold: float = DEFAULT_NAME_THRESHOLD,
) -> tuple[InvoiceSchema, dict[str, Any]]:
    """Backfill certified supplier fields and flag identity conflicts."""

    match, attempts = resolve_supplier(
        repository, invoice, name_threshold=name_threshold
    )
    memory: dict[str, Any] = {
        "matched": match is not None,
        "matched_by": match.matched_by if match else None,
        "score": match.score if match else None,
        "supplier_id": match.supplier_id if match else None,
        "legal_name": match.raison_sociale if match else None,
        "siret": match.siret if match else None,
        "vat_number": match.vat_number if match else None,
        "country": match.country if match else None,
        "vat_regime": match.vat_regime if match else None,
        "attempts": attempts,
        "backfilled": [],
        "conflicts": [],
    }
    invoice.metadata["supplier_memory"] = memory
    if match is None:
        return invoice, memory

    supplier = invoice.supplier
    if supplier is None:
        supplier = InvoiceParty(name=match.raison_sociale)
        invoice.supplier = supplier
        memory["backfilled"].append("supplier")
    else:
        current_name = str(supplier.name or "").strip()
        if not current_name or current_name.upper() == "FOURNISSEUR_INCONNU":
            supplier.name = match.raison_sociale
            memory["backfilled"].append("supplier_name")
        supplier.normalized_name = match.raison_sociale

    if match.siret:
        current_siret = re.sub(r"\D", "", str(supplier.siret or "")) or None
        if current_siret is None:
            supplier.siret = match.siret
            memory["backfilled"].append("siret")
        elif current_siret != match.siret:
            memory["conflicts"].append(
                f"siret extracted={current_siret} certified={match.siret}"
            )
            supplier.siret = match.siret
            memory["backfilled"].append("siret")

    if match.vat_number:
        current_vat = (
            normalise_vat(supplier.vat_number) if supplier.vat_number else None
        )
        if current_vat is None:
            supplier.vat_number = match.vat_number
            memory["backfilled"].append("vat_number")
        elif current_vat != match.vat_number:
            memory["conflicts"].append(
                f"vat_number extracted={current_vat} "
                f"certified={match.vat_number}"
            )
            supplier.vat_number = match.vat_number
            memory["backfilled"].append("vat_number")

    if match.country:
        current_country = normalise_country(
            invoice.metadata.get("supplier_country")
        )
        invoice.metadata["supplier_country"] = match.country
        if current_country is None:
            memory["backfilled"].append("country")
        elif current_country != match.country:
            memory["conflicts"].append(
                f"country extracted={current_country} certified={match.country}"
            )
            memory["backfilled"].append("country")

    if match.vat_regime:
        current_regime = str(invoice.metadata.get("vat_regime") or "").strip().upper()
        invoice.metadata["vat_regime"] = match.vat_regime
        if current_regime != match.vat_regime:
            memory["backfilled"].append("vat_regime")

    for conflict in memory["conflicts"]:
        warning = f"IDENTITY_CONFLICT: {conflict}"
        if warning not in invoice.extraction_warnings:
            invoice.extraction_warnings.append(warning)

    logger.info(
        "[SUPPLIER] matched %s by %s (backfilled=%s, conflicts=%s)",
        match.raison_sociale,
        match.matched_by,
        ",".join(memory["backfilled"]) or "-",
        len(memory["conflicts"]),
    )
    return invoice, memory


__all__ = [
    "DEFAULT_NAME_THRESHOLD",
    "KNOWN_VAT_REGIMES",
    "SupplierMatch",
    "enrich_invoice",
    "resolve_supplier",
]
