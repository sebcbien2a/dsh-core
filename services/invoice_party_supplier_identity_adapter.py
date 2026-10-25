"""Pure, read-only conversion from InvoiceParty to SupplierIdentity."""

from __future__ import annotations

import re
from uuid import UUID

from schemas.invoice_schema import InvoiceParty
from schemas.supplier_identity_schema import (
    SupplierAddress,
    SupplierIdentity,
    SupplierIdentifier,
)

_SEPARATORS = re.compile(r"[\s.\-_/]+")


def _normalize_identifier(value: str, *, uppercase: bool = False) -> str:
    normalized = _SEPARATORS.sub("", value)
    return normalized.upper() if uppercase else normalized


def invoice_party_to_supplier_identity(
    party: InvoiceParty,
    *,
    supplier_id: UUID | None = None,
    country: str | None = None,
) -> SupplierIdentity:
    """Convert party data without lookup, persistence, logging, or inference."""
    printed_names: list[str] = []
    if (
        party.normalized_name
        and party.normalized_name.strip()
        and party.normalized_name.strip().casefold() != party.name.strip().casefold()
    ):
        printed_names.append(party.normalized_name)

    identifiers: list[SupplierIdentifier] = []
    if party.siret is not None:
        identifiers.append(
            SupplierIdentifier(
                identifier_type="SIRET",
                scope="establishment",
                country=country,
                raw_value=party.siret,
                normalized_value=_normalize_identifier(party.siret),
                human_confirmed=False,
                is_primary=False,
            )
        )
    if party.vat_number is not None:
        identifiers.append(
            SupplierIdentifier(
                identifier_type="VAT",
                scope="vat",
                country=country,
                raw_value=party.vat_number,
                normalized_value=_normalize_identifier(party.vat_number, uppercase=True),
                human_confirmed=False,
                is_primary=False,
            )
        )

    addresses = []
    if party.address is not None:
        addresses.append(
            SupplierAddress(
                lines=[party.address],
                country=country,
                human_confirmed=False,
            )
        )

    return SupplierIdentity(
        **({"supplier_id": supplier_id} if supplier_id is not None else {}),
        legal_name=party.name,
        printed_names=printed_names,
        aliases=[],
        country=country,
        identifiers=identifiers,
        addresses=addresses,
    )
