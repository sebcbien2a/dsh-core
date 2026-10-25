import socket
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from repositories.supplier_identity_repository import SupplierIdentityRepository
from schemas.supplier_identity_schema import SupplierIdentifier, SupplierIdentity
from services.supplier_registration_service import (
    HumanRegistrationDecision,
    RegistrationAction,
    RegistrationProposal,
    ProposalStatus,
    SupplierRegistrationError,
    SupplierRegistrationService,
)

SOURCE_SHA = "a" * 64


def identifier(
    kind="SIRET", value="12345678900012", country="FR", confirmed=False
):
    return SupplierIdentifier(
        identifier_type=kind,
        raw_value=value,
        normalized_value=value,
        country=country,
        human_confirmed=confirmed,
    )


def candidate(name="Candidate", identifiers=(), **kwargs):
    return SupplierIdentity(
        legal_name=name, identifiers=list(identifiers), **kwargs
    )


@pytest.fixture
def setup(tmp_path):
    repo = SupplierIdentityRepository(tmp_path / "suppliers.json")
    return repo, SupplierRegistrationService(repo)


def decision(proposal, action, **kwargs):
    return HumanRegistrationDecision(
        proposal_id=proposal.proposal_id,
        action=action,
        human_confirmed=True,
        justification="Reviewed against source document",
        **kwargs,
    )


def test_propose_does_not_write_and_keeps_candidate_unchanged(setup):
    repo, service = setup
    source = candidate(identifiers=[identifier()], aliases=["Unapproved"])
    before = source.model_dump(mode="json")
    proposal = service.propose(source, SOURCE_SHA)
    assert proposal.status == ProposalStatus.PENDING
    assert source.model_dump(mode="json") == before
    assert not repo.path.exists()
    assert proposal.candidate.model_dump(mode="json") == before


def test_propose_suggests_by_legal_name_and_alias(setup):
    repo, service = setup
    existing = repo.create(candidate("Known", aliases=["Trade name"]))
    proposal = service.propose(candidate("Known", aliases=["Trade name"]), SOURCE_SHA)
    assert proposal.suggested_supplier_ids == [existing.supplier_id]


def test_propose_returns_multiple_suggestions_without_choosing(setup):
    repo, service = setup
    a = repo.create(candidate("Same"))
    b = repo.create(candidate("Same", aliases=["Other"]))
    proposal = service.propose(candidate("Same"), SOURCE_SHA)
    assert set(proposal.suggested_supplier_ids) == {a.supplier_id, b.supplier_id}
    assert proposal.identifier_conflicts == []


def test_propose_reports_confirmed_identifier_conflict_separately(setup):
    repo, service = setup
    existing = repo.create(candidate("Existing", identifiers=[identifier(confirmed=True)]))
    proposal = service.propose(candidate("New", identifiers=[identifier()]), SOURCE_SHA)
    assert proposal.suggested_supplier_ids == []
    assert len(proposal.identifier_conflicts) == 1
    assert proposal.identifier_conflicts[0].supplier_id == existing.supplier_id


def test_propose_rejects_invalid_source_hash(setup):
    _, service = setup
    with pytest.raises(ValueError):
        service.propose(candidate(), "bad")


def test_create_requires_human_confirmation(setup):
    repo, service = setup
    proposal = service.propose(candidate(), SOURCE_SHA)
    unconfirmed = HumanRegistrationDecision(
        proposal_id=proposal.proposal_id,
        action=RegistrationAction.CREATE,
        justification="Reviewed",
    )
    with pytest.raises(SupplierRegistrationError) as exc:
        service.confirm(proposal, unconfirmed)
    assert exc.value.code == "HUMAN_CONFIRMATION_REQUIRED"
    assert repo.list_active() == []


def test_create_requires_justification(setup):
    repo, service = setup
    proposal = service.propose(candidate(), SOURCE_SHA)
    with pytest.raises(SupplierRegistrationError) as exc:
        service.confirm(
            proposal,
            HumanRegistrationDecision(
                proposal_id=proposal.proposal_id,
                action=RegistrationAction.CREATE,
                human_confirmed=True,
            ),
        )
    assert exc.value.code == "JUSTIFICATION_REQUIRED"
    assert repo.list_active() == []


def test_create_rechecks_confirmed_identifier_collision(setup):
    repo, service = setup
    repo.create(candidate("Existing", identifiers=[identifier(confirmed=True)]))
    proposal = service.propose(candidate("New", identifiers=[identifier()]), SOURCE_SHA)
    with pytest.raises(SupplierRegistrationError) as exc:
        service.confirm(
            proposal,
            decision(
                proposal,
                RegistrationAction.CREATE,
                confirmed_identifier_keys=[("FR", "SIRET", "12345678900012")],
            ),
        )
    assert exc.value.code == "CONFIRMED_IDENTIFIER_COLLISION"
    assert len(repo.list_active()) == 1
    assert proposal.status == ProposalStatus.PENDING


def test_create_confirms_only_selected_identifiers(setup):
    repo, service = setup
    selected = identifier()
    unselected = identifier("VAT", "DE123456789", "DE")
    proposal = service.propose(candidate(identifiers=[selected, unselected]), SOURCE_SHA)
    service.confirm(
        proposal,
        decision(
            proposal,
            RegistrationAction.CREATE,
            confirmed_identifier_keys=[("FR", "SIRET", "12345678900012")],
        ),
    )
    stored = repo.get(proposal.candidate.supplier_id)
    assert [item.human_confirmed for item in stored.identifiers] == [True, False]
    assert stored.identifiers[0].confirmed_at is not None
    assert stored.identifiers[0].justification == "Reviewed against source document"
    assert stored.identifiers[1].confirmed_at is None


def test_create_uses_only_accepted_aliases_and_not_printed_names(setup):
    repo, service = setup
    proposal = service.propose(
        candidate(
            printed_names=["Printed Name"],
            aliases=["Candidate alias"],
        ),
        SOURCE_SHA,
    )
    service.confirm(
        proposal,
        decision(
            proposal,
            RegistrationAction.CREATE,
            accepted_aliases=["Accepted Alias"],
        ),
    )
    stored = repo.get(proposal.candidate.supplier_id)
    assert stored.aliases == ["Accepted Alias"]
    assert stored.printed_names == ["Printed Name"]


def test_create_siret_absent_is_allowed(setup):
    repo, service = setup
    proposal = service.propose(candidate("Foreign Corp", country="DE"), SOURCE_SHA)
    service.confirm(proposal, decision(proposal, RegistrationAction.CREATE))
    assert repo.get(proposal.candidate.supplier_id).identifiers == []


@pytest.mark.parametrize(
    ("kind", "value", "country"),
    [("VAT", "DE123456789", "DE"), ("CUSTOM_REG", "XY-9", "NL")],
)
def test_create_supports_foreign_vat_and_custom_identifier(
    setup, kind, value, country
):
    repo, service = setup
    proposal = service.propose(
        candidate("Foreign", identifiers=[identifier(kind, value, country)]),
        SOURCE_SHA,
    )
    service.confirm(
        proposal,
        decision(
            proposal,
            RegistrationAction.CREATE,
            confirmed_identifier_keys=[(country, kind, value)],
        ),
    )
    stored = repo.get(proposal.candidate.supplier_id)
    assert stored.identifiers[0].human_confirmed
    assert stored.identifiers[0].identifier_type == kind


def test_update_requires_existing_target(setup):
    _, service = setup
    proposal = service.propose(candidate(), SOURCE_SHA)
    with pytest.raises(SupplierRegistrationError) as exc:
        service.confirm(proposal, decision(proposal, RegistrationAction.UPDATE))
    assert exc.value.code == "TARGET_SUPPLIER_REQUIRED"


def test_update_requires_target_to_exist(setup):
    _, service = setup
    proposal = service.propose(candidate(), SOURCE_SHA)
    with pytest.raises(SupplierRegistrationError) as exc:
        service.confirm(
            proposal,
            decision(
                proposal,
                RegistrationAction.UPDATE,
                target_supplier_id=uuid4(),
            ),
        )
    assert exc.value.code == "TARGET_SUPPLIER_NOT_FOUND"


def test_update_keeps_target_id_and_adds_only_accepted_data(setup):
    repo, service = setup
    target = repo.create(candidate("Existing", printed_names=["Existing Print"]))
    proposal = service.propose(
        candidate(
            "Different candidate name",
            identifiers=[identifier("VAT", "DE123456789", "DE")],
            aliases=["Unapproved"],
        ),
        SOURCE_SHA,
    )
    service.confirm(
        proposal,
        decision(
            proposal,
            RegistrationAction.UPDATE,
            target_supplier_id=target.supplier_id,
            confirmed_identifier_keys=[("DE", "VAT", "DE123456789")],
            accepted_aliases=["Approved"],
        ),
    )
    stored = repo.get(target.supplier_id)
    assert stored.supplier_id == target.supplier_id
    assert stored.legal_name == "Existing"
    assert stored.printed_names == ["Existing Print"]
    assert stored.aliases == ["Approved"]
    assert len(stored.identifiers) == 1
    assert stored.identifiers[0].human_confirmed


def test_update_does_not_overwrite_existing_identifier(setup):
    repo, service = setup
    original = identifier("VAT", "DE123456789", "DE", confirmed=False)
    target = repo.create(candidate("Existing", identifiers=[original]))
    proposal = service.propose(
        candidate("Different", identifiers=[identifier("VAT", "DE123456789", "DE")]),
        SOURCE_SHA,
    )
    service.confirm(
        proposal,
        decision(
            proposal,
            RegistrationAction.UPDATE,
            target_supplier_id=target.supplier_id,
            confirmed_identifier_keys=[("DE", "VAT", "DE123456789")],
        ),
    )
    stored = repo.get(target.supplier_id)
    assert len(stored.identifiers) == 1
    assert stored.identifiers[0].raw_value == original.raw_value
    assert stored.identifiers[0].human_confirmed


def test_cancel_does_not_write_or_mutate_candidate(setup):
    repo, service = setup
    source = candidate(identifiers=[identifier()])
    before = source.model_dump(mode="json")
    proposal = service.propose(source, SOURCE_SHA)
    proposal_before = proposal.candidate.model_dump(mode="json")
    result = service.cancel(
        proposal, decision(proposal, RegistrationAction.CANCEL)
    )
    assert result is proposal
    assert source.model_dump(mode="json") == before
    assert proposal.candidate.model_dump(mode="json") == proposal_before
    assert proposal.status == ProposalStatus.CANCELLED
    assert not repo.path.exists()


def test_wrong_proposal_id_is_refused(setup):
    _, service = setup
    proposal = service.propose(candidate(), SOURCE_SHA)
    wrong = decision(proposal, RegistrationAction.CANCEL).model_copy(
        update={"proposal_id": uuid4()}
    )
    with pytest.raises(SupplierRegistrationError) as exc:
        service.cancel(proposal, wrong)
    assert exc.value.code == "PROPOSAL_ID_MISMATCH"


def test_double_confirmation_is_refused(setup):
    repo, service = setup
    proposal = service.propose(candidate(), SOURCE_SHA)
    create_decision = decision(proposal, RegistrationAction.CREATE)
    service.confirm(proposal, create_decision)
    with pytest.raises(SupplierRegistrationError) as exc:
        service.confirm(proposal, create_decision)
    assert exc.value.code == "PROPOSAL_ALREADY_COMPLETED"
    assert len(repo.list_active()) == 1


def test_cancel_decision_cannot_be_used_for_confirmation(setup):
    _, service = setup
    proposal = service.propose(candidate(), SOURCE_SHA)
    with pytest.raises(SupplierRegistrationError) as exc:
        service.confirm(proposal, decision(proposal, RegistrationAction.CANCEL))
    assert exc.value.code == "USE_CANCEL_FOR_CANCEL_ACTION"


def test_proposal_and_decision_json_serialization(setup):
    _, service = setup
    proposal = service.propose(
        candidate(identifiers=[identifier()]), SOURCE_SHA
    )
    restored_proposal = RegistrationProposal.model_validate_json(
        proposal.model_dump_json()
    )
    human_decision = decision(
        proposal,
        RegistrationAction.CREATE,
        confirmed_identifier_keys=[("FR", "SIRET", "12345678900012")],
    )
    restored_decision = HumanRegistrationDecision.model_validate_json(
        human_decision.model_dump_json()
    )
    assert restored_proposal.proposal_id == proposal.proposal_id
    assert restored_proposal.candidate == proposal.candidate
    assert restored_decision == human_decision


def test_no_legacy_repository_interaction_or_amount_calculation(setup):
    repo, service = setup
    proposal = service.propose(candidate(), SOURCE_SHA)
    service.confirm(proposal, decision(proposal, RegistrationAction.CREATE))
    assert not hasattr(service, "_legacy_repository")
    assert not {"amount", "vat", "total_ht", "total_ttc"} & set(
        repo.get(proposal.candidate.supplier_id).model_dump()
    )


def test_no_network_dependency(setup, monkeypatch):
    _, service = setup

    def forbidden(*args, **kwargs):
        raise AssertionError("network access")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    proposal = service.propose(candidate(), SOURCE_SHA)
    service.confirm(proposal, decision(proposal, RegistrationAction.CREATE))


def test_confirmation_timestamp_is_explicitly_stored(setup):
    repo, service = setup
    proposal = service.propose(candidate(identifiers=[identifier()]), SOURCE_SHA)
    timestamp = datetime(2026, 1, 2, tzinfo=timezone.utc)
    service.confirm(
        proposal,
        decision(
            proposal,
            RegistrationAction.CREATE,
            confirmed_at=timestamp,
            confirmed_identifier_keys=[("FR", "SIRET", "12345678900012")],
        ),
    )
    assert repo.get(proposal.candidate.supplier_id).identifiers[0].confirmed_at == timestamp


def test_create_persists_terminal_status_and_complete_audit_record(setup):
    repo, service = setup
    proposal = service.propose(candidate(identifiers=[identifier()]), SOURCE_SHA)
    human_decision = decision(
        proposal,
        RegistrationAction.CREATE,
        confirmed_identifier_keys=[("FR", "SIRET", "12345678900012")],
        accepted_aliases=["Reviewed alias"],
    )
    created = service.confirm(proposal, human_decision)
    record = proposal.decision_record
    assert proposal.status == ProposalStatus.PERSISTED
    assert proposal.completed_at == human_decision.confirmed_at
    assert proposal.resulting_supplier_id == created.supplier_id
    assert record.proposal_id == proposal.proposal_id
    assert record.action == RegistrationAction.CREATE
    assert record.resulting_supplier_id == created.supplier_id
    assert record.target_supplier_id is None
    assert record.source_sha256 == SOURCE_SHA
    assert record.confirmed_identifier_keys == [("FR", "SIRET", "12345678900012")]
    assert record.accepted_aliases == ["Reviewed alias"]
    assert record.justification == human_decision.justification
    assert record.confirmed_at == human_decision.confirmed_at
    assert repo.get(created.supplier_id) == created


def test_update_persists_terminal_status_and_complete_audit_record(setup):
    repo, service = setup
    target = repo.create(candidate("Existing"))
    proposal = service.propose(candidate("Updated", identifiers=[identifier()]), SOURCE_SHA)
    human_decision = decision(
        proposal,
        RegistrationAction.UPDATE,
        target_supplier_id=target.supplier_id,
        confirmed_identifier_keys=[("FR", "SIRET", "12345678900012")],
        accepted_aliases=["New alias"],
    )
    updated = service.confirm(proposal, human_decision)
    record = proposal.decision_record
    assert proposal.status == ProposalStatus.PERSISTED
    assert proposal.completed_at == human_decision.confirmed_at
    assert proposal.resulting_supplier_id == target.supplier_id
    assert updated.supplier_id == target.supplier_id
    assert record.action == RegistrationAction.UPDATE
    assert record.target_supplier_id == target.supplier_id
    assert record.resulting_supplier_id == target.supplier_id
    assert record.source_sha256 == SOURCE_SHA
    assert record.confirmed_identifier_keys == [("FR", "SIRET", "12345678900012")]
    assert record.accepted_aliases == ["New alias"]
    assert record.justification == human_decision.justification
    assert record.confirmed_at == human_decision.confirmed_at


def test_cancel_is_terminal_and_has_complete_audit_record_without_writing(setup):
    repo, service = setup
    proposal = service.propose(candidate(), SOURCE_SHA)
    before = repo.path.read_bytes() if repo.path.exists() else None
    human_decision = decision(
        proposal,
        RegistrationAction.CANCEL,
        accepted_aliases=["Not persisted"],
    )
    result = service.cancel(proposal, human_decision)
    record = result.decision_record
    assert result.status == ProposalStatus.CANCELLED
    assert result.completed_at == human_decision.confirmed_at
    assert result.resulting_supplier_id is None
    assert record.action == RegistrationAction.CANCEL
    assert record.proposal_id == proposal.proposal_id
    assert record.target_supplier_id is None
    assert record.resulting_supplier_id is None
    assert record.source_sha256 == SOURCE_SHA
    assert record.confirmed_identifier_keys == []
    assert record.accepted_aliases == ["Not persisted"]
    assert record.justification == human_decision.justification
    assert record.confirmed_at == human_decision.confirmed_at
    assert (repo.path.read_bytes() if repo.path.exists() else None) == before
    assert repo.list_active() == []


@pytest.mark.parametrize("operation", ["confirm", "cancel"])
def test_cancelled_proposal_cannot_be_reused(setup, operation):
    _, service = setup
    proposal = service.propose(candidate(), SOURCE_SHA)
    cancel_decision = decision(proposal, RegistrationAction.CANCEL)
    service.cancel(proposal, cancel_decision)
    with pytest.raises(SupplierRegistrationError) as exc:
        if operation == "confirm":
            service.confirm(proposal, decision(proposal, RegistrationAction.CREATE))
        else:
            service.cancel(proposal, cancel_decision)
    assert exc.value.code == "PROPOSAL_ALREADY_COMPLETED"


def test_terminal_proposal_survives_serialization_and_service_recreation(setup):
    repo, service = setup
    proposal = service.propose(candidate(), SOURCE_SHA)
    create_decision = decision(proposal, RegistrationAction.CREATE)
    service.confirm(proposal, create_decision)
    restored = RegistrationProposal.model_validate_json(proposal.model_dump_json())
    recreated_service = SupplierRegistrationService(repo)
    with pytest.raises(SupplierRegistrationError) as exc:
        recreated_service.confirm(restored, create_decision)
    assert exc.value.code == "PROPOSAL_ALREADY_COMPLETED"
    assert restored.status == ProposalStatus.PERSISTED
    assert len(repo.list_active()) == 1


def test_repository_failure_keeps_proposal_pending_without_decision_record(
    setup, monkeypatch
):
    repo, service = setup
    proposal = service.propose(candidate(), SOURCE_SHA)

    def fail_create(identity):
        raise OSError("simulated persistence failure")

    monkeypatch.setattr(repo, "create", fail_create)
    with pytest.raises(OSError, match="simulated persistence failure"):
        service.confirm(proposal, decision(proposal, RegistrationAction.CREATE))
    assert proposal.status == ProposalStatus.PENDING
    assert proposal.completed_at is None
    assert proposal.resulting_supplier_id is None
    assert proposal.decision_record is None


def test_terminal_proposal_json_round_trip_preserves_decision_journal(setup):
    _, service = setup
    proposal = service.propose(candidate(), SOURCE_SHA)
    service.cancel(proposal, decision(proposal, RegistrationAction.CANCEL))
    restored = RegistrationProposal.model_validate_json(proposal.model_dump_json())
    assert restored.status == ProposalStatus.CANCELLED
    assert restored.decision_record == proposal.decision_record
    assert restored.completed_at == proposal.completed_at
