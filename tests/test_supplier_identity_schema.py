import json
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from schemas.supplier_identity_schema import (
    CustomFieldDefinition,
    FieldStatus,
    FieldValue,
    PaymentProfile,
    SupplierAddress,
    SupplierIdentifier,
    SupplierIdentity,
    SupplierLayoutRule,
    can_merge,
    shared_identifiers,
)

SHA = "a" * 64


def _identifier(
    identifier_type,
    value,
    country=None,
    primary=False,
    human_confirmed=False,
    scope="other",
):
    return SupplierIdentifier(
        identifier_type=identifier_type,
        scope=scope,
        country=country,
        raw_value=value,
        normalized_value=value.replace(" ", ""),
        is_primary=primary,
        human_confirmed=human_confirmed,
    )


def test_french_supplier_with_siren_siret_and_vat():
    supplier = SupplierIdentity(
        legal_name="Société Fictive SAS",
        country="fr",
        identifiers=[
            _identifier("siren", "123 456 789", "FR", True),
            _identifier("siret", "123 456 789 00012", "FR", True),
            _identifier("vat", "FR12123456789", "FR", True),
        ],
    )
    assert supplier.country == "FR"
    assert {i.identifier_type for i in supplier.identifiers} == {"SIREN", "SIRET", "VAT"}
    siren = next(i for i in supplier.identifiers if i.identifier_type == "SIREN")
    assert siren.raw_value == "123 456 789"


def test_foreign_supplier_without_siren_or_siret():
    supplier = SupplierIdentity(
        legal_name="Crisol Color SL",
        country="ES",
        identifiers=[_identifier("vat", "ESB12345678", "ES", True), _identifier("cif", "B12345678", "ES")],
    )
    types = {i.identifier_type for i in supplier.identifiers}
    assert "SIREN" not in types and "SIRET" not in types
    assert not any(hasattr(supplier, name) for name in ("siren", "siret"))


def test_identifiers_are_never_deduced():
    supplier = SupplierIdentity(
        legal_name="Fictive",
        identifiers=[_identifier("siret", "12345678900012", "FR"), _identifier("vat", "FR12123456789", "FR")],
    )
    assert all(i.identifier_type != "SIREN" for i in supplier.identifiers)


def test_aliases_share_one_supplier_id():
    supplier = SupplierIdentity(
        legal_name="Crisol Color SL",
        aliases=["Crisol Color", "Paintcar", "Euro Campi", "paintcar"],
        printed_names=["PAINTCAR"],
    )
    assert supplier.aliases == ["Crisol Color", "Paintcar", "Euro Campi"]
    assert {"Paintcar", "Euro Campi", "Crisol Color SL"} <= set(supplier.all_names())
    assert isinstance(supplier.supplier_id, type(uuid4()))


def test_no_merge_based_on_similar_names_only():
    a = SupplierIdentity(legal_name="Euro Campi", aliases=["Paintcar"])
    b = SupplierIdentity(legal_name="Euro Campi", aliases=["Paintcar"])
    assert not can_merge(a, b)
    assert shared_identifiers(a, b) == set()


def test_merge_allowed_with_shared_identifier_or_same_id():
    a = SupplierIdentity(legal_name="A", identifiers=[_identifier("vat", "ESB1", "ES", human_confirmed=True)])
    b = SupplierIdentity(legal_name="B", identifiers=[_identifier("vat", "ESB1", "ES", human_confirmed=True)])
    c = SupplierIdentity(legal_name="C", identifiers=[_identifier("vat", "ESB2", "ES", human_confirmed=True)])
    assert can_merge(a, b) and not can_merge(a, c)
    assert can_merge(a, a)


def test_merge_requires_both_shared_identifiers_to_be_human_confirmed():
    a = SupplierIdentity(legal_name="A", identifiers=[_identifier("vat", "ESB1", "ES")])
    b = SupplierIdentity(legal_name="B", identifiers=[_identifier("vat", "ESB1", "ES", human_confirmed=True)])
    assert shared_identifiers(a, b) == set()
    assert not can_merge(a, b)

    unconfirmed = SupplierIdentity(
        legal_name="Euro Campi", aliases=["Paintcar"],
        identifiers=[_identifier("vat", "ESB1", "ES", human_confirmed=True)],
    )
    confirmed = SupplierIdentity(
        legal_name="Euro Campi", aliases=["Paintcar"],
        identifiers=[_identifier("vat", "ESB1", "ES", human_confirmed=True)],
    )
    assert can_merge(unconfirmed, confirmed)


def test_identifier_scope_is_extensible_and_normalized():
    assert _identifier("vat", "x").scope == "other"
    for scope in ("legal", "tax", "registry", "custom_scope"):
        identifier = _identifier("custom", "x", scope=scope.upper())
        assert identifier.scope == scope


def test_identifier_correction_is_separate_and_requires_full_human_evidence_without_raw():
    corrected = SupplierIdentifier(
        identifier_type="tax_id",
        raw_value="raw",
        normalized_value="normalized",
        corrected_value="corrected",
    )
    assert (corrected.raw_value, corrected.normalized_value, corrected.corrected_value) == (
        "raw", "normalized", "corrected"
    )
    evidence = {
        "identifier_type": "tax_id",
        "corrected_value": "corrected",
        "human_confirmed": True,
        "justification": "verified against source",
        "confirmed_at": datetime(2026, 10, 25, tzinfo=timezone.utc),
    }
    assert SupplierIdentifier(**evidence).corrected_value == "corrected"
    for missing in ("human_confirmed", "justification", "confirmed_at"):
        invalid = dict(evidence)
        invalid.pop(missing)
        with pytest.raises(ValidationError):
            SupplierIdentifier(**invalid)


def test_primary_identifier_requires_normalized_value():
    with pytest.raises(ValidationError):
        SupplierIdentifier(identifier_type="vat", raw_value="raw", is_primary=True)


def test_multiple_addresses():
    supplier = SupplierIdentity(
        legal_name="Fictive",
        addresses=[
            SupplierAddress(address_type="registered", lines=["1 rue A"], city="Lyon", country="fr"),
            SupplierAddress(address_type="warehouse", lines=["2 calle B"], city="Valencia", country="ES"),
        ],
    )
    assert [a.country for a in supplier.addresses] == ["FR", "ES"]


def test_multiple_payment_profiles_without_any_amount_computation():
    supplier = SupplierIdentity(
        legal_name="Fictive",
        payment_profiles=[
            PaymentProfile(payment_terms_label="30 jours fin de mois", delay_days=30, calculation_rule="end_of_month"),
            PaymentProfile(issue_date=date(2026, 10, 1), due_date=date(2026, 11, 1), payment_method="transfer"),
        ],
    )
    assert len(supplier.payment_profiles) == 2
    fields = set(PaymentProfile.model_fields) | set(SupplierIdentity.model_fields)
    assert not fields & {"montant_ht", "montant_tva", "montant_ttc", "net_amount", "tax_amount", "gross_amount"}
    with pytest.raises(ValidationError):
        PaymentProfile(issue_date=date(2026, 11, 1), due_date=date(2026, 10, 1))
    with pytest.raises(ValidationError):
        PaymentProfile(delay_days=-1)


def test_custom_identifier_type_needs_no_code_change():
    identifier = _identifier("codice_fiscale", "RSSMRA80A01H501U", "IT")
    assert identifier.identifier_type == "CODICE_FISCALE"
    SupplierIdentity(legal_name="Fornitore", country="IT", identifiers=[identifier])


def test_two_primaries_same_type_and_country_refused():
    with pytest.raises(ValidationError):
        SupplierIdentity(
            legal_name="Fictive",
            identifiers=[_identifier("vat", "A", "ES", True), _identifier("vat", "B", "ES", True)],
        )
    SupplierIdentity(
        legal_name="Fictive",
        identifiers=[_identifier("vat", "A", "ES", True), _identifier("vat", "B", "PT", True)],
    )


def test_invalid_country_and_blank_values_refused():
    with pytest.raises(ValidationError):
        SupplierIdentifier(identifier_type="VAT", country="FRA")
    with pytest.raises(ValidationError):
        SupplierIdentity(legal_name="   ")


def test_custom_field_definition_and_dynamic_value():
    definition = CustomFieldDefinition(
        key="codice_destinatario", label="Codice destinatario", country_scope=["it"], validation_pattern=r"^[A-Z0-9]{7}$"
    )
    assert definition.country_scope == ["IT"] and definition.active
    with pytest.raises(ValidationError):
        CustomFieldDefinition(key="Bad Key", label="x")
    with pytest.raises(ValidationError):
        CustomFieldDefinition(key="ok", label="x", validation_pattern="[")
    supplier = SupplierIdentity(
        legal_name="Fornitore",
        custom_fields={"codice_destinatario": FieldValue(raw="ABC1234", status=FieldStatus.KNOWN)},
    )
    assert supplier.custom_fields["codice_destinatario"].raw == "ABC1234"
    with pytest.raises(ValidationError):
        SupplierIdentity(legal_name="X", custom_fields={"Bad Key": FieldValue()})


def test_layout_rule_inactive_without_human_confirmation():
    supplier_id = uuid4()
    rule = SupplierLayoutRule(supplier_id=supplier_id, field_key="due_date", anchor_text="Échéance", relative_position="right")
    assert rule.active is False and rule.human_confirmed is False
    with pytest.raises(ValidationError):
        SupplierLayoutRule(
            supplier_id=supplier_id, field_key="due_date", anchor_text="x", relative_position="right", active=True
        )
    confirmed = SupplierLayoutRule(
        supplier_id=supplier_id, field_key="due_date", anchor_text="x", relative_position="right",
        human_confirmed=True, active=True, source_sha256=SHA, confidence=0.9,
        confirmed_at=datetime(2026, 10, 25, tzinfo=timezone.utc),
    )
    assert confirmed.active


def test_active_layout_rule_requires_hash_and_confirmation_time():
    base = {
        "supplier_id": uuid4(), "field_key": "due_date", "anchor_text": "Due",
        "relative_position": "right", "human_confirmed": True, "active": True,
        "confirmed_at": datetime(2026, 10, 25, tzinfo=timezone.utc),
    }
    with pytest.raises(ValidationError):
        SupplierLayoutRule(**base)
    with pytest.raises(ValidationError):
        SupplierLayoutRule(**{**base, "source_sha256": SHA, "confirmed_at": None})
    accepted = SupplierLayoutRule(**{**base, "source_sha256": SHA, "corrected_value": "2026-10-25"})
    assert accepted.active and accepted.corrected_value == "2026-10-25"


def test_layout_rule_rollback_by_active_false():
    supplier = SupplierIdentity(legal_name="Fictive")
    rule = SupplierLayoutRule(
        supplier_id=supplier.supplier_id, field_key="ttc", anchor_text="Net à payer",
        relative_position="below", human_confirmed=True, active=True, version=2,
        source_sha256=SHA, confirmed_at=datetime(2026, 10, 25, tzinfo=timezone.utc),
    )
    supplier = supplier.model_copy(update={"layout_rules": [rule]})
    rolled = rule.model_copy(update={"active": False})
    supplier = supplier.model_copy(update={"layout_rules": [rolled]})
    assert supplier.layout_rules[0].active is False and supplier.layout_rules[0].version == 2
    with pytest.raises(ValidationError):
        SupplierIdentity(legal_name="Other", layout_rules=[rule])


def test_raw_normalized_corrected_are_separate():
    value = FieldValue(
        raw=" 123 456 789 ", normalized="123456789", corrected="123456780",
        status=FieldStatus.KNOWN, human_confirmed=True, source_sha256=SHA,
    )
    assert (value.raw, value.normalized, value.corrected) == (" 123 456 789 ", "123456789", "123456780")
    with pytest.raises(ValidationError):
        FieldValue(corrected="X")
    with pytest.raises(ValidationError):
        FieldValue(corrected="X", human_confirmed=True)
    assert FieldValue(corrected="X", human_confirmed=True, justification="Lu sur la source").corrected == "X"
    with pytest.raises(ValidationError):
        FieldValue(raw="x", source_sha256="bad")


def test_unknown_fields_are_refused():
    with pytest.raises(ValidationError):
        SupplierIdentity(legal_name="Fictive", siren="123456789")


def test_json_round_trip():
    supplier_id = uuid4()
    supplier = SupplierIdentity(
        supplier_id=supplier_id,
        legal_name="Crisol Color SL",
        country="ES",
        aliases=["Paintcar", "Euro Campi"],
        identifiers=[
            SupplierIdentifier(
                identifier_type="vat",
                scope="tax",
                country="ES",
                raw_value="ESB12345678",
                normalized_value="ESB12345678",
                corrected_value="ESB12345679",
                human_confirmed=True,
                is_primary=True,
            )
        ],
        addresses=[SupplierAddress(lines=["1 calle A"], city="Valencia", country="ES")],
        payment_profiles=[PaymentProfile(delay_days=30, due_date=date(2026, 11, 30))],
        custom_fields={"iban_hint": FieldValue(raw="ES00", status=FieldStatus.KNOWN)},
        layout_rules=[
            SupplierLayoutRule(
                supplier_id=supplier_id, field_key="due_date", anchor_text="Due",
                relative_position="right", corrected_value="2026-11-30",
                source_sha256=SHA, human_confirmed=True,
                confirmed_at=datetime(2026, 10, 25, tzinfo=timezone.utc), active=True,
            )
        ],
    )
    dumped = supplier.model_dump_json()
    json.loads(dumped)
    assert SupplierIdentity.model_validate_json(dumped) == supplier
