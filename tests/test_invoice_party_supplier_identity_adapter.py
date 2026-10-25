import builtins
import json
from pathlib import Path
from uuid import uuid4

import pytest

from schemas.invoice_schema import InvoiceParty
from services.invoice_party_supplier_identity_adapter import (
    invoice_party_to_supplier_identity,
)


def test_maps_name_and_suggested_printed_name_without_alias():
    party = InvoiceParty(name="Crisol Color SL", normalized_name="CRISOL COLOR")
    result = invoice_party_to_supplier_identity(party)
    assert result.legal_name == "Crisol Color SL"
    assert result.printed_names == ["CRISOL COLOR"]
    assert result.aliases == []


def test_normalized_name_equal_ignoring_case_is_not_duplicated():
    party = InvoiceParty(name="Crisol Color SL", normalized_name="CRISOL COLOR SL")
    assert invoice_party_to_supplier_identity(party).printed_names == []


def test_blank_normalized_name_is_not_added():
    party = InvoiceParty.model_construct(
        name="Supplier", normalized_name="   ", siret=None, vat_number=None, address=None
    )
    assert invoice_party_to_supplier_identity(party).printed_names == []


def test_siret_mapping_is_optional_raw_and_unconfirmed():
    party = InvoiceParty(name="Supplier", siret="12345678900012")
    result = invoice_party_to_supplier_identity(party, country="FR")
    assert len(result.identifiers) == 1
    identifier = result.identifiers[0]
    assert identifier.identifier_type == "SIRET"
    assert identifier.scope == "establishment"
    assert identifier.country == "FR"
    assert identifier.raw_value == "12345678900012"
    assert identifier.normalized_value == "12345678900012"
    assert not identifier.is_primary and not identifier.human_confirmed


def test_siret_absent_creates_no_identifier():
    assert invoice_party_to_supplier_identity(InvoiceParty(name="Supplier")).identifiers == []


def test_siret_normalization_removes_common_separators_without_changing_raw():
    party = InvoiceParty.model_construct(
        name="Supplier", normalized_name=None,
        siret="123 456 789-00012", vat_number=None, address=None,
    )
    identifier = invoice_party_to_supplier_identity(party).identifiers[0]
    assert identifier.raw_value == "123 456 789-00012"
    assert identifier.normalized_value == "12345678900012"


def test_vat_mapping_normalizes_separators_and_case_but_keeps_raw():
    raw = "es b-123 456.78"
    result = invoice_party_to_supplier_identity(
        InvoiceParty(name="Supplier", vat_number=raw), country="ES"
    )
    identifier = result.identifiers[0]
    assert identifier.identifier_type == "VAT"
    assert identifier.scope == "vat"
    assert identifier.country == "ES"
    assert identifier.raw_value == raw
    assert identifier.normalized_value == "ESB12345678"
    assert not identifier.is_primary and not identifier.human_confirmed


def test_vat_absent_creates_no_identifier():
    result = invoice_party_to_supplier_identity(
        InvoiceParty(name="Supplier", siret="12345678900012")
    )
    assert [identifier.identifier_type for identifier in result.identifiers] == ["SIRET"]


def test_foreign_vat_without_siret_and_country_is_only_explicit_input():
    result = invoice_party_to_supplier_identity(
        InvoiceParty(name="Proveedor", vat_number="ESB12345678")
    )
    assert result.country is None
    assert len(result.identifiers) == 1
    assert result.identifiers[0].country is None
    assert result.identifiers[0].identifier_type == "VAT"


def test_country_is_not_inferred_from_vat_prefix():
    result = invoice_party_to_supplier_identity(
        InvoiceParty(name="Proveedor", vat_number="ESB12345678")
    )
    assert result.country is None
    assert result.identifiers[0].country is None


def test_address_is_preserved_as_one_raw_line_without_parsing():
    raw_address = "12 rue Exemple\n75001 Paris"
    result = invoice_party_to_supplier_identity(
        InvoiceParty(name="Supplier", address=raw_address)
    )
    address = result.addresses[0]
    assert address.lines == [raw_address]
    assert address.city is None and address.postal_code is None and address.region is None
    assert address.country is None and not address.human_confirmed


def test_explicit_country_is_used_for_supplier_and_address():
    result = invoice_party_to_supplier_identity(
        InvoiceParty(name="Supplier", address="Somewhere"), country="gb"
    )
    assert result.country == "GB"
    assert result.addresses[0].country == "GB"


def test_supplied_supplier_id_is_preserved_and_missing_id_is_generated():
    supplier_id = uuid4()
    assert invoice_party_to_supplier_identity(
        InvoiceParty(name="Supplier"), supplier_id=supplier_id
    ).supplier_id == supplier_id
    generated = invoice_party_to_supplier_identity(InvoiceParty(name="Supplier")).supplier_id
    assert generated != supplier_id
    assert isinstance(generated, type(supplier_id))


def test_identifiers_are_never_primary_or_human_confirmed_and_siren_is_not_inferred():
    result = invoice_party_to_supplier_identity(
        InvoiceParty(name="Supplier", siret="12345678900012", vat_number="FR12123456789")
    )
    assert {identifier.identifier_type for identifier in result.identifiers} == {"SIRET", "VAT"}
    assert all(not i.is_primary and not i.human_confirmed for i in result.identifiers)


def test_source_party_is_unchanged():
    party = InvoiceParty(
        name="Supplier", normalized_name="SUPPLIER INC",
        siret="12345678900012", vat_number="ESB12345678",
        address="1 Main Street",
    )
    before = party.model_dump()
    invoice_party_to_supplier_identity(party, country="ES")
    assert party.model_dump() == before


def test_adapter_has_no_file_or_repository_interactions(monkeypatch):
    party = InvoiceParty(name="Supplier", vat_number="ESB12345678")

    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected file access")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    result = invoice_party_to_supplier_identity(party)
    assert result.legal_name == "Supplier"


def test_result_serializes_and_reloads_as_json():
    result = invoice_party_to_supplier_identity(
        InvoiceParty(
            name="Proveedor", normalized_name="PROVEEDOR",
            vat_number="es b-123 456.78", address="Calle Uno 1",
        ),
        country="ES",
    )
    serialized = result.model_dump_json()
    restored = type(result).model_validate_json(serialized)
    assert json.loads(serialized)["identifiers"][0]["raw_value"] == "es b-123 456.78"
    assert restored == result
