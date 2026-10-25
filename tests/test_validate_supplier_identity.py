import ast
import json
import socket
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from repositories.supplier_identity_repository import SupplierIdentityRepository
from repositories.supplier_registration_repository import SupplierRegistrationRepository
from schemas.supplier_identity_schema import SupplierIdentifier, SupplierIdentity
from services.supplier_registration_coordinator import (
    SupplierRegistrationCoordinator,
    SupplierRegistrationCoordinatorError,
)
from services.supplier_registration_service import ProposalStatus, RegistrationAction
from tools import validate_supplier_identity as tool
from tools.validate_supplier_identity import (
    StoragePaths,
    SupplierFormError,
    build_candidate,
    form_from_candidate,
    has_unsaved_draft,
    load_invoice_party,
    parse_args,
    parse_form,
    preview,
    submit,
)

SHA = "a" * 64
VAT = ("DE", "VAT", "DE123456789")


@pytest.fixture
def ctx(tmp_path):
    party_file = tmp_path / "party.json"
    party_file.write_text(
        json.dumps({"name": "ACME GmbH", "vat_number": "DE 123 456 789", "address": "1 Strasse, Berlin"}),
        encoding="utf-8",
    )
    paths = StoragePaths(tmp_path / "s.json", tmp_path / "p.json", tmp_path / "j.json")
    base = build_candidate(load_invoice_party(party_file), "DE")
    return paths, base, party_file


def parsed_of(base, **changes):
    parsed = parse_form(base, form_from_candidate(base))
    return replace(parsed, **changes) if changes else parsed


def confirmed_vat_form(base):
    form = form_from_candidate(base)
    form["identifiers"][0]["confirm"] = True
    return form


def do(paths, parsed, action, **kwargs):
    kwargs.setdefault("justification", "Checked against source")
    kwargs.setdefault("human_confirmed", True)
    return submit(paths, parsed, SHA, action=action, **kwargs)


def untouched(paths):
    return not any(p.exists() for p in (paths.suppliers, paths.proposals, paths.journal))


def existing(paths, **kwargs):
    return SupplierIdentityRepository(paths.suppliers).create(SupplierIdentity(**kwargs))


def test_load_invoice_party_and_conversion(ctx):
    paths, base, party_file = ctx
    party = load_invoice_party(party_file)
    assert party.name == "ACME GmbH" and party.siret is None
    assert base.legal_name == "ACME GmbH" and base.country == "DE"
    assert [(i.identifier_type, i.normalized_value) for i in base.identifiers] == [
        ("VAT", "DE123456789")
    ]
    assert not base.identifiers[0].human_confirmed and not base.identifiers[0].is_primary
    assert untouched(paths)


def test_invalid_party_file_refused(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(SupplierFormError, match="INVALID_PARTY_FILE"):
        load_invoice_party(bad)


def test_custom_identifier_type_scope_and_label(ctx):
    _, base, _ = ctx
    form = form_from_candidate(base)
    form["identifiers"].append(
        {
            "identifier_type": "cif",
            "scope": "Tax",
            "country": "es",
            "local_label": "CIF local",
            "raw_value": "B-1234 5678",
            "normalized_value": "B12345678",
            "confirm": True,
        }
    )
    parsed = parse_form(base, form)
    added = parsed.candidate.identifiers[-1]
    assert (added.identifier_type, added.scope, added.country) == ("CIF", "tax", "ES")
    assert added.local_label == "CIF local" and added.raw_value == "B-1234 5678"
    assert parsed.confirmed_keys == [("ES", "CIF", "B12345678")]


def test_foreign_supplier_without_siret_or_siren(ctx):
    _, base, _ = ctx
    parsed = parsed_of(base)
    types = {i.identifier_type for i in parsed.candidate.identifiers}
    assert types == {"VAT"} and "SIRET" not in types and "SIREN" not in types
    assert parsed.candidate.country == "DE"
    assert not any(i.is_primary for i in parsed.candidate.identifiers)


def test_country_is_not_deduced(tmp_path):
    party_file = tmp_path / "p.json"
    party_file.write_text(json.dumps({"name": "X", "vat_number": "FR123"}), encoding="utf-8")
    candidate = build_candidate(load_invoice_party(party_file))
    assert candidate.country is None and candidate.identifiers[0].country is None


def test_dynamic_custom_field(ctx):
    _, base, _ = ctx
    form = form_from_candidate(base)
    form["custom_fields"] = [{"key": "customer_code", "value": "X1"}]
    assert parse_form(base, form).candidate.custom_fields["customer_code"].raw == "X1"
    form["custom_fields"] = [{"key": "Bad Key", "value": "v"}]
    with pytest.raises(SupplierFormError):
        parse_form(base, form)


def test_address_and_payment_edits(ctx):
    _, base, _ = ctx
    form = form_from_candidate(base)
    form["addresses"] = [{"lines": "Strasse 1 | Hinterhaus", "city": "Berlin", "country": "de"}]
    form["payment_profiles"] = [{"payment_terms_label": "30 jours", "delay_days": "30"}]
    candidate = parse_form(base, form).candidate
    assert candidate.addresses[0].lines == ["Strasse 1", "Hinterhaus"]
    assert candidate.addresses[0].country == "DE" and not candidate.addresses[0].human_confirmed
    assert candidate.payment_profiles[0].delay_days == 30
    form["payment_profiles"] = [{"delay_days": "abc"}]
    with pytest.raises(SupplierFormError, match="INVALID_FORM"):
        parse_form(base, form)


def test_suggestions_do_not_merge(ctx):
    paths, base, _ = ctx
    other = existing(paths, legal_name="ACME GmbH")
    before = paths.suppliers.read_bytes()
    shown = preview(SupplierIdentityRepository(paths.suppliers), base, SHA)
    assert [s.supplier_id for s in shown.suggestions] == [other.supplier_id]
    assert shown.conflicts == []
    assert paths.suppliers.read_bytes() == before
    assert not paths.proposals.exists() and not paths.journal.exists()


def test_collision_displayed_separately(ctx):
    paths, base, _ = ctx
    owner = existing(
        paths,
        legal_name="Other Co",
        identifiers=[
            SupplierIdentifier(
                identifier_type="VAT", country="DE", normalized_value=VAT[2], human_confirmed=True
            )
        ],
    )
    shown = preview(SupplierIdentityRepository(paths.suppliers), base, SHA)
    assert shown.suggestions == []
    assert [(c.identifier_key, c.supplier_id) for c in shown.conflicts] == [(VAT, owner.supplier_id)]
    with pytest.raises(ValueError, match="CONFIRMED_IDENTIFIER_COLLISION"):
        do(paths, parse_form(base, confirmed_vat_form(base)), RegistrationAction.CREATE)


def test_create_explicit(ctx):
    paths, base, _ = ctx
    parsed = parse_form(base, confirmed_vat_form(base))
    assert do(paths, parsed, RegistrationAction.CREATE) == "COMPLETED"
    stored = SupplierIdentityRepository(paths.suppliers).get(base.supplier_id)
    assert stored.identifiers[0].human_confirmed and stored.legal_name == "ACME GmbH"


def test_create_persists_all_editable_supplier_fields(ctx):
    paths, base, _ = ctx
    form = confirmed_vat_form(base)
    form["addresses"] = [
        {"lines": "Edited Road 2", "city": "Munich", "country": "DE"}
    ]
    form["payment_profiles"] = [
        {"payment_terms_label": "Net 45", "delay_days": "45", "payment_method": "SEPA"}
    ]
    form["custom_fields"] = [{"key": "vendor_ref", "value": "C-42"}]
    form["legal_name"] = "Edited GmbH"
    form["printed_names"] = ["Edited Print Name"]
    assert do(paths, parse_form(base, form), RegistrationAction.CREATE) == "COMPLETED"
    stored = SupplierIdentityRepository(paths.suppliers).get(base.supplier_id)
    assert stored.legal_name == "Edited GmbH"
    assert stored.printed_names == ["Edited Print Name"]
    assert stored.addresses[0].city == "Munich"
    assert stored.payment_profiles[0].delay_days == 45
    assert stored.custom_fields["vendor_ref"].raw == "C-42"


def test_update_with_explicit_target(ctx):
    paths, base, _ = ctx
    target = existing(paths, **base.model_dump(exclude={"supplier_id"}))
    parsed = parse_form(base, confirmed_vat_form(base))
    result = do(paths, parsed, RegistrationAction.UPDATE, target_supplier_id=target.supplier_id)
    assert result == "COMPLETED"
    stored = SupplierIdentityRepository(paths.suppliers).get(target.supplier_id)
    assert stored.identifiers[0].human_confirmed
    assert len(SupplierIdentityRepository(paths.suppliers).list_active()) == 1


def test_update_identifiers_only_accepted(ctx):
    paths, base, _ = ctx
    target = existing(paths, **base.model_dump(exclude={"supplier_id", "identifiers"}))
    form = form_from_candidate(base)
    form["identifiers"][0]["confirm"] = True
    parsed = parse_form(base, form)
    assert do(paths, parsed, RegistrationAction.UPDATE, target_supplier_id=target.supplier_id) == "COMPLETED"
    stored = SupplierIdentityRepository(paths.suppliers).get(target.supplier_id)
    assert stored.identifiers[0].human_confirmed


def test_update_aliases_only_accepted(ctx):
    paths, base, _ = ctx
    target = existing(paths, **base.model_dump(exclude={"supplier_id"}))
    form = form_from_candidate(base)
    form["aliases"] = [{"alias": "Acme Trading", "accepted": True}]
    parsed = parse_form(base, form)
    assert do(paths, parsed, RegistrationAction.UPDATE, target_supplier_id=target.supplier_id) == "COMPLETED"
    stored = SupplierIdentityRepository(paths.suppliers).get(target.supplier_id)
    assert stored.aliases == ["Acme Trading"]


@pytest.mark.parametrize(
    ("field", "value", "category"),
    [
        ("legal_name", "Changed GmbH", "legal_name"),
        ("printed_names", ["Changed print"], "printed_names"),
        ("addresses", [{"lines": "Changed street", "country": "DE"}], "addresses"),
        ("payment_profiles", [{"payment_terms_label": "Net 45"}], "payment_profiles"),
        ("custom_fields", [{"key": "account_code", "value": "9"}], "custom_fields"),
        ("country", "AT", "country"),
    ],
)
def test_update_unsupported_field_rejected_before_pending(ctx, field, value, category):
    paths, base, _ = ctx
    target = existing(paths, **base.model_dump(exclude={"supplier_id"}))
    form = form_from_candidate(base)
    form[field] = value
    parsed = parse_form(base, form)
    with pytest.raises(SupplierFormError, match="UPDATE_UNSUPPORTED_FIELD_CHANGE") as error:
        do(paths, parsed, RegistrationAction.UPDATE, target_supplier_id=target.supplier_id)
    assert category in str(error.value)
    assert not paths.proposals.exists() and not paths.journal.exists()


def test_multiple_unsupported_update_fields_listed(ctx):
    paths, base, _ = ctx
    target = existing(paths, **base.model_dump(exclude={"supplier_id"}))
    form = form_from_candidate(base)
    form["legal_name"] = "Changed GmbH"
    form["country"] = "AT"
    parsed = parse_form(base, form)
    with pytest.raises(SupplierFormError, match="UPDATE_UNSUPPORTED_FIELD_CHANGE") as error:
        do(paths, parsed, RegistrationAction.UPDATE, target_supplier_id=target.supplier_id)
    assert "legal_name" in str(error.value) and "country" in str(error.value)
    assert not paths.proposals.exists() and not paths.journal.exists()


def test_update_target_missing_or_inactive_rejected_before_pending(ctx):
    paths, base, _ = ctx
    missing = uuid4()
    with pytest.raises(SupplierFormError, match="UPDATE_TARGET_NOT_FOUND"):
        do(paths, parsed_of(base), RegistrationAction.UPDATE, target_supplier_id=missing)
    inactive = existing(paths, **base.model_dump(exclude={"supplier_id"}))
    SupplierIdentityRepository(paths.suppliers).deactivate(inactive.supplier_id)
    with pytest.raises(SupplierFormError, match="UPDATE_TARGET_INACTIVE"):
        do(paths, parsed_of(base), RegistrationAction.UPDATE, target_supplier_id=inactive.supplier_id)
    assert not paths.proposals.exists() and not paths.journal.exists()


def test_update_requires_explicit_selection_without_auto_selection(ctx):
    paths, base, _ = ctx
    existing(paths, **base.model_dump(exclude={"supplier_id"}))
    with pytest.raises(SupplierFormError, match="TARGET_SUPPLIER_REQUIRED"):
        do(paths, parsed_of(base), RegistrationAction.UPDATE)
    assert not paths.proposals.exists() and not paths.journal.exists()


def test_update_without_target_refused_and_nothing_written(ctx):
    paths, base, _ = ctx
    with pytest.raises(SupplierFormError, match="TARGET_SUPPLIER_REQUIRED"):
        do(paths, parsed_of(base), RegistrationAction.UPDATE)
    assert untouched(paths)


def test_target_refused_for_create(ctx):
    paths, base, _ = ctx
    with pytest.raises(SupplierFormError, match="TARGET_ONLY_FOR_UPDATE"):
        do(paths, parsed_of(base), RegistrationAction.CREATE, target_supplier_id=uuid4())
    assert untouched(paths)


def test_cancel_writes_no_supplier(ctx):
    paths, base, _ = ctx
    assert do(paths, parsed_of(base), RegistrationAction.CANCEL) == "CANCELLED"
    assert not paths.suppliers.exists()
    stored = SupplierRegistrationRepository(paths.proposals).list_completed()
    assert [p.status for p in stored] == [ProposalStatus.CANCELLED]


def test_cancel_ignores_edited_form_and_does_not_write_supplier(ctx):
    paths, base, _ = ctx
    edited = base.model_copy(update={"legal_name": "Not persisted"})
    parsed = parse_form(edited, form_from_candidate(edited))
    assert do(
        paths,
        parsed,
        RegistrationAction.CANCEL,
        base_candidate=base,
    ) == "CANCELLED"
    assert not paths.suppliers.exists()
    proposal = SupplierRegistrationRepository(paths.proposals).list_completed()[0]
    assert proposal.candidate.legal_name == base.legal_name


@pytest.mark.parametrize("justification", [None, "", "   "])
def test_justification_mandatory(ctx, justification):
    paths, base, _ = ctx
    with pytest.raises(SupplierFormError, match="JUSTIFICATION_REQUIRED"):
        do(paths, parsed_of(base), RegistrationAction.CREATE, justification=justification)
    assert untouched(paths)


def test_human_confirmation_mandatory(ctx):
    paths, base, _ = ctx
    with pytest.raises(SupplierFormError, match="HUMAN_CONFIRMATION_REQUIRED"):
        do(paths, parsed_of(base), RegistrationAction.CREATE, human_confirmed=False)
    assert untouched(paths)


def test_only_selected_identifiers_confirmed(ctx):
    paths, base, _ = ctx
    form = confirmed_vat_form(base)
    form["identifiers"].append(
        {"identifier_type": "CIF", "country": "DE", "normalized_value": "B1", "confirm": False}
    )
    assert do(paths, parse_form(base, form), RegistrationAction.CREATE) == "COMPLETED"
    stored = SupplierIdentityRepository(paths.suppliers).get(base.supplier_id)
    assert {i.identifier_type: i.human_confirmed for i in stored.identifiers} == {
        "VAT": True,
        "CIF": False,
    }


def test_confirmed_identifier_requires_normalized_value(ctx):
    _, base, _ = ctx
    form = form_from_candidate(base)
    form["identifiers"].append({"identifier_type": "X", "raw_value": "r", "confirm": True})
    with pytest.raises(SupplierFormError, match="REQUIRES_NORMALIZED_VALUE"):
        parse_form(base, form)


def test_only_selected_aliases_accepted(ctx):
    paths, base, _ = ctx
    form = form_from_candidate(base)
    form["aliases"] = [{"alias": "Acme", "accepted": True}, {"alias": "Other", "accepted": False}]
    parsed = parse_form(base, form)
    assert parsed.accepted_aliases == ["Acme"]
    assert do(paths, parsed, RegistrationAction.CREATE) == "COMPLETED"
    assert SupplierIdentityRepository(paths.suppliers).get(base.supplier_id).aliases == ["Acme"]


def test_accepted_alias_must_exist_in_candidate(ctx):
    paths, base, _ = ctx
    parsed = parsed_of(base, accepted_aliases=["Invented"])
    with pytest.raises(SupplierFormError, match="ACCEPTED_ALIAS_NOT_IN_CANDIDATE"):
        do(paths, parsed, RegistrationAction.CREATE)
    assert untouched(paths)


def test_pending_proposal_written_before_coordinator(ctx, monkeypatch):
    paths, base, _ = ctx
    seen = {}

    def fake_execute(self, proposal_id, decision):
        stored = SupplierRegistrationRepository(paths.proposals).get(proposal_id)
        seen["status"] = stored.status
        seen["journal"] = paths.journal.exists()
        seen["supplier"] = paths.suppliers.exists()
        raise RuntimeError("stop")

    monkeypatch.setattr(SupplierRegistrationCoordinator, "execute", fake_execute)
    with pytest.raises(RuntimeError):
        do(paths, parsed_of(base), RegistrationAction.CREATE)
    assert seen == {"status": ProposalStatus.PENDING, "journal": False, "supplier": False}


def test_recovery_required_propagated(ctx, monkeypatch):
    paths, base, _ = ctx

    def fake_execute(self, proposal_id, decision):
        raise SupplierRegistrationCoordinatorError("RECOVERY_REQUIRED", "SUPPLIER_STATE_DIVERGED")

    monkeypatch.setattr(SupplierRegistrationCoordinator, "execute", fake_execute)
    assert do(paths, parsed_of(base), RegistrationAction.CREATE) == "RECOVERY_REQUIRED"


def test_other_coordinator_errors_are_not_masked(ctx, monkeypatch):
    paths, base, _ = ctx

    def fake_execute(self, proposal_id, decision):
        raise SupplierRegistrationCoordinatorError("PROPOSAL_ALREADY_COMPLETED")

    monkeypatch.setattr(SupplierRegistrationCoordinator, "execute", fake_execute)
    with pytest.raises(SupplierRegistrationCoordinatorError):
        do(paths, parsed_of(base), RegistrationAction.CREATE)


def test_unsaved_draft_detection(ctx):
    _, base, _ = ctx
    baseline = form_from_candidate(base)
    assert not has_unsaved_draft(baseline, form_from_candidate(base))
    edited = form_from_candidate(base)
    edited["legal_name"] = "Changed"
    assert has_unsaved_draft(baseline, edited)


def test_cli_requires_explicit_storage_and_valid_sha(tmp_path):
    base_args = [
        "party.json",
        "--source-sha256",
        SHA,
        "--suppliers",
        str(tmp_path / "s"),
        "--proposals",
        str(tmp_path / "p"),
        "--journal",
        str(tmp_path / "j"),
    ]
    args = parse_args(base_args + ["--country", "DE"])
    assert args.country == "DE" and args.suppliers == tmp_path / "s"
    assert parse_args(base_args).country is None
    with pytest.raises(SystemExit):
        parse_args(base_args[:4])
    with pytest.raises(SystemExit):
        parse_args(["party.json", "--source-sha256", "xyz"] + base_args[3:])


def test_no_network(ctx, monkeypatch):
    paths, base, _ = ctx

    def forbidden(*args, **kwargs):
        raise AssertionError("network access")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    preview(SupplierIdentityRepository(paths.suppliers), base, SHA)
    assert do(paths, parse_form(base, confirmed_vat_form(base)), RegistrationAction.CREATE) == "COMPLETED"


def test_source_has_no_legacy_repository_and_no_amount_logic():
    source = Path(tool.__file__).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert not any("supplier_repository" in name for name in imported)
    lowered = source.lower()
    for token in ("decimal", "total_ht", "total_ttc", "vat_rate", "tax_rate", "amount"):
        assert token not in lowered
