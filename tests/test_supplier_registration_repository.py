import json
import socket
from uuid import uuid4

import pytest

from repositories.supplier_identity_repository import SupplierIdentityRepository
from repositories.supplier_registration_repository import (
    INVALID_PROPOSAL_TRANSITION,
    INVALID_STORAGE,
    PROPOSAL_ALREADY_EXISTS,
    PROPOSAL_NOT_FOUND,
    TERMINAL_PROPOSAL_IMMUTABLE,
    SupplierRegistrationRepository,
    SupplierRegistrationRepositoryError,
)
from schemas.supplier_identity_schema import SupplierIdentifier, SupplierIdentity
from services.supplier_registration_service import (
    HumanRegistrationDecision,
    ProposalStatus,
    RegistrationAction,
    SupplierRegistrationService,
)

SOURCE_SHA = "b" * 64
KEY = ("FR", "SIRET", "12345678900012")


@pytest.fixture
def env(tmp_path):
    identities = SupplierIdentityRepository(tmp_path / "suppliers.json")
    service = SupplierRegistrationService(identities)
    repo = SupplierRegistrationRepository(tmp_path / "data" / "proposals.json")
    return identities, service, repo


def candidate(name="Candidate"):
    return SupplierIdentity(
        legal_name=name,
        identifiers=[
            SupplierIdentifier(
                identifier_type="SIRET",
                country="FR",
                raw_value=KEY[2],
                normalized_value=KEY[2],
            )
        ],
    )


def decision(proposal, action, **kwargs):
    return HumanRegistrationDecision(
        proposal_id=proposal.proposal_id,
        action=action,
        human_confirmed=True,
        justification="Reviewed against source",
        **kwargs,
    )


def make_pending(service, repo, name="Candidate"):
    proposal = service.propose(candidate(name), SOURCE_SHA)
    repo.create(proposal)
    return proposal


def raises(code):
    return pytest.raises(SupplierRegistrationRepositoryError, match=code)


def test_create_pending_and_get_by_uuid(env):
    _, service, repo = env
    proposal = make_pending(service, repo)
    assert repo.get(proposal.proposal_id) == proposal
    assert repo.get(str(proposal.proposal_id)) == proposal
    data = json.loads(repo.path.read_text(encoding="utf-8"))
    assert list(data["proposals"]) == [str(proposal.proposal_id)]


def test_create_duplicate_refused(env):
    _, service, repo = env
    proposal = make_pending(service, repo)
    with raises(PROPOSAL_ALREADY_EXISTS):
        repo.create(proposal)


def test_create_terminal_refused(env):
    identities, service, repo = env
    persisted = service.propose(candidate("A"), SOURCE_SHA)
    service.confirm(persisted, decision(persisted, RegistrationAction.CREATE))
    cancelled = service.propose(candidate("B"), SOURCE_SHA)
    service.cancel(cancelled, decision(cancelled, RegistrationAction.CANCEL))
    for proposal in (persisted, cancelled):
        with raises(INVALID_PROPOSAL_TRANSITION):
            repo.create(proposal)
    assert not repo.path.exists()


def test_unknown_proposal(env):
    _, service, repo = env
    assert repo.get(uuid4()) is None
    proposal = service.propose(candidate(), SOURCE_SHA)
    service.cancel(proposal, decision(proposal, RegistrationAction.CANCEL))
    with raises(PROPOSAL_NOT_FOUND):
        repo.complete(proposal)


def test_pending_to_persisted_keeps_create_journal(env):
    _, service, repo = env
    stored = make_pending(service, repo)
    working = stored.model_copy(deep=True)
    human = decision(
        working,
        RegistrationAction.CREATE,
        confirmed_identifier_keys=[KEY],
        accepted_aliases=["Alias"],
    )
    created = service.confirm(working, human)
    repo.complete(working)
    reloaded = repo.get(stored.proposal_id)
    record = reloaded.decision_record
    assert reloaded.status == ProposalStatus.PERSISTED
    assert reloaded.resulting_supplier_id == created.supplier_id
    assert record.action == RegistrationAction.CREATE
    assert record.source_sha256 == SOURCE_SHA
    assert record.confirmed_identifier_keys == [KEY]
    assert record.accepted_aliases == ["Alias"]
    assert record.justification == human.justification
    assert record.confirmed_at == human.confirmed_at
    assert reloaded.completed_at == human.confirmed_at


def test_update_journal_kept(env):
    identities, service, repo = env
    target = identities.create(SupplierIdentity(legal_name="Existing"))
    stored = make_pending(service, repo)
    working = stored.model_copy(deep=True)
    service.confirm(
        working,
        decision(
            working,
            RegistrationAction.UPDATE,
            target_supplier_id=target.supplier_id,
            confirmed_identifier_keys=[KEY],
        ),
    )
    repo.complete(working)
    record = repo.get(stored.proposal_id).decision_record
    assert record.action == RegistrationAction.UPDATE
    assert record.target_supplier_id == target.supplier_id
    assert record.resulting_supplier_id == target.supplier_id


def test_pending_to_cancelled_keeps_cancel_journal(env):
    _, service, repo = env
    stored = make_pending(service, repo)
    working = stored.model_copy(deep=True)
    human = decision(working, RegistrationAction.CANCEL)
    service.cancel(working, human)
    repo.complete(working)
    reloaded = repo.get(stored.proposal_id)
    assert reloaded.status == ProposalStatus.CANCELLED
    assert reloaded.resulting_supplier_id is None
    assert reloaded.decision_record.action == RegistrationAction.CANCEL
    assert reloaded.decision_record.source_sha256 == SOURCE_SHA
    assert reloaded.decision_record.justification == human.justification


@pytest.mark.parametrize("terminal", ["persist", "cancel"])
def test_no_transition_back_to_pending(env, terminal):
    _, service, repo = env
    stored = make_pending(service, repo)
    working = stored.model_copy(deep=True)
    if terminal == "persist":
        service.confirm(working, decision(working, RegistrationAction.CREATE))
    else:
        service.cancel(working, decision(working, RegistrationAction.CANCEL))
    repo.complete(working)
    with raises(INVALID_PROPOSAL_TRANSITION):
        repo.complete(stored)
    assert repo.get(stored.proposal_id).status != ProposalStatus.PENDING


def test_pending_cannot_be_completed(env):
    _, service, repo = env
    stored = make_pending(service, repo)
    with raises(INVALID_PROPOSAL_TRANSITION):
        repo.complete(stored)


def test_terminal_proposal_is_immutable_and_complete_is_idempotent(env):
    _, service, repo = env
    stored = make_pending(service, repo)
    working = stored.model_copy(deep=True)
    service.cancel(working, decision(working, RegistrationAction.CANCEL))
    repo.complete(working)
    before = repo.path.read_bytes()
    assert repo.complete(working) == working
    assert repo.path.read_bytes() == before

    other = stored.model_copy(deep=True)
    service.cancel(
        other,
        decision(other, RegistrationAction.CANCEL).model_copy(
            update={"justification": "Different reason"}
        ),
    )
    with raises(TERMINAL_PROPOSAL_IMMUTABLE):
        repo.complete(other)
    switched = stored.model_copy(deep=True)
    service.confirm(switched, decision(switched, RegistrationAction.CREATE))
    with raises(TERMINAL_PROPOSAL_IMMUTABLE):
        repo.complete(switched)
    assert repo.path.read_bytes() == before


def test_candidate_and_source_sha_are_immutable_during_transition(env):
    _, service, repo = env
    stored = make_pending(service, repo)
    altered = stored.model_copy(deep=True)
    service.cancel(altered, decision(altered, RegistrationAction.CANCEL))
    tampered_candidate = altered.model_copy(
        update={"candidate": candidate("Another supplier").model_copy(
            update={"supplier_id": altered.candidate.supplier_id}
        )}
    )
    with raises(INVALID_PROPOSAL_TRANSITION):
        repo.complete(tampered_candidate)
    assert repo.get(stored.proposal_id).status == ProposalStatus.PENDING


def test_source_sha_change_refused(env):
    _, service, repo = env
    stored = make_pending(service, repo)
    other = service.propose(stored.candidate, "c" * 64).model_copy(
        update={"proposal_id": stored.proposal_id, "created_at": stored.created_at}
    )
    service.cancel(other, decision(other, RegistrationAction.CANCEL))
    with raises(INVALID_PROPOSAL_TRANSITION):
        repo.complete(other)


def test_stored_candidate_unchanged_after_completion(env):
    _, service, repo = env
    stored = make_pending(service, repo)
    working = stored.model_copy(deep=True)
    service.cancel(working, decision(working, RegistrationAction.CANCEL))
    repo.complete(working)
    assert repo.get(stored.proposal_id).candidate == stored.candidate


def test_reload_keeps_terminal_state(env):
    _, service, repo = env
    stored = make_pending(service, repo)
    working = stored.model_copy(deep=True)
    service.cancel(working, decision(working, RegistrationAction.CANCEL))
    repo.complete(working)
    reloaded_repo = SupplierRegistrationRepository(repo.path)
    assert reloaded_repo.get(stored.proposal_id) == working
    with raises(INVALID_PROPOSAL_TRANSITION):
        reloaded_repo.complete(stored)


def test_filter_by_status(env):
    _, service, repo = env
    pending = make_pending(service, repo, "P")
    done_a = make_pending(service, repo, "A")
    done_b = make_pending(service, repo, "B")
    for item, persist in ((done_a, True), (done_b, False)):
        working = item.model_copy(deep=True)
        if persist:
            service.confirm(working, decision(working, RegistrationAction.CREATE))
        else:
            service.cancel(working, decision(working, RegistrationAction.CANCEL))
        repo.complete(working)
    assert [p.proposal_id for p in repo.list_pending()] == [pending.proposal_id]
    assert [p.proposal_id for p in repo.list_by_status(ProposalStatus.PERSISTED)] == [
        done_a.proposal_id
    ]
    assert [p.proposal_id for p in repo.list_by_status(ProposalStatus.CANCELLED)] == [
        done_b.proposal_id
    ]
    assert {p.proposal_id for p in repo.list_completed()} == {
        done_a.proposal_id,
        done_b.proposal_id,
    }


def test_atomic_save_and_backup(env):
    _, service, repo = env
    first = make_pending(service, repo, "A")
    backup = repo.path.with_name("proposals.json.bak")
    assert not backup.exists()
    make_pending(service, repo, "B")
    assert backup.exists()
    assert list(json.loads(backup.read_text(encoding="utf-8"))["proposals"]) == [
        str(first.proposal_id)
    ]
    assert not repo.path.with_name("proposals.json.tmp").exists()


def test_invalid_json_refused_without_overwrite(env):
    _, service, repo = env
    repo.path.parent.mkdir(parents=True)
    repo.path.write_text("{not json", encoding="utf-8")
    proposal = service.propose(candidate(), SOURCE_SHA)
    for action in (
        lambda: repo.create(proposal),
        lambda: repo.get(proposal.proposal_id),
        repo.list_pending,
        repo.list_completed,
    ):
        with raises(INVALID_STORAGE):
            action()
    assert repo.path.read_text(encoding="utf-8") == "{not json"
    assert not repo.path.with_name("proposals.json.bak").exists()


def test_invalid_record_refused(env):
    _, _, repo = env
    repo.path.parent.mkdir(parents=True)
    repo.path.write_text(
        json.dumps({"version": 1, "proposals": {str(uuid4()): {"x": 1}}}), encoding="utf-8"
    )
    with raises(INVALID_STORAGE):
        repo.list_pending()


def test_no_network_access(env, monkeypatch):
    _, service, repo = env

    def forbidden(*args, **kwargs):
        raise AssertionError("network access")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    stored = make_pending(service, repo)
    working = stored.model_copy(deep=True)
    service.cancel(working, decision(working, RegistrationAction.CANCEL))
    repo.complete(working)
    repo.list_completed()


def test_no_amount_fields_and_legacy_repository_not_imported(env):
    import repositories.supplier_registration_repository as module

    _, service, repo = env
    stored = make_pending(service, repo)
    assert not {"amount", "vat", "total_ht", "total_ttc"} & set(
        repo.get(stored.proposal_id).model_dump()
    )
    source = open(module.__file__, encoding="utf-8").read()
    assert "services.supplier_repository" not in source
    assert "import supplier_repository" not in source
    import sys

    assert "services.supplier_repository" not in sys.modules
