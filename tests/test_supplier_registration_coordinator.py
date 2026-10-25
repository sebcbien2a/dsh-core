import json
import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest

from repositories.supplier_identity_repository import SupplierIdentityRepository
from repositories.supplier_registration_repository import SupplierRegistrationRepository
from schemas.supplier_identity_schema import SupplierIdentifier, SupplierIdentity
from services.supplier_registration_coordinator import (
    SupplierRegistrationCoordinator,
    SupplierRegistrationCoordinatorError,
    SupplierTransactionJournal,
    SupplierTransactionJournalError,
    TransactionStatus,
    supplier_hash,
)
from services.supplier_registration_service import (
    HumanRegistrationDecision,
    ProposalStatus,
    RegistrationAction,
    SupplierRegistrationService,
)

SOURCE_SHA = "b" * 64
KEY = ("FR", "SIRET", "12345678900012")


class Crash(Exception):
    pass


class Fault:
    """Simulates a process interruption on one method, before it runs."""

    def __init__(self, monkeypatch, obj, name, when=None, armed=True):
        self.original = getattr(obj, name)
        self.when = when or (lambda *a, **k: True)
        self.armed = armed
        self.calls = 0

        def wrapper(*args, **kwargs):
            if self.armed and self.when(*args, **kwargs):
                raise Crash(name)
            self.calls += 1
            return self.original(*args, **kwargs)

        monkeypatch.setattr(obj, name, wrapper)


def status_is(status):
    return lambda record: record.status == status


@pytest.fixture
def env(tmp_path):
    identities = SupplierIdentityRepository(tmp_path / "suppliers.json")
    proposals = SupplierRegistrationRepository(tmp_path / "proposals.json")
    journal = SupplierTransactionJournal(tmp_path / "journal" / "transactions.json")
    return SimpleNamespace(
        tmp=tmp_path,
        identities=identities,
        proposals=proposals,
        journal=journal,
        service=SupplierRegistrationService(identities),
        coordinator=SupplierRegistrationCoordinator(identities, proposals, journal),
    )


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


def make_decision(proposal, action, **kwargs):
    return HumanRegistrationDecision(
        proposal_id=proposal.proposal_id,
        action=action,
        human_confirmed=True,
        justification="Reviewed against source",
        **kwargs,
    )


def pending(env):
    proposal = env.service.propose(candidate(), SOURCE_SHA)
    env.proposals.create(proposal)
    return proposal


def create_case(env):
    proposal = pending(env)
    return proposal, make_decision(
        proposal, RegistrationAction.CREATE, confirmed_identifier_keys=[KEY]
    )


def update_case(env):
    target = env.identities.create(SupplierIdentity(legal_name="Existing"))
    proposal = pending(env)
    return target, proposal, make_decision(
        proposal,
        RegistrationAction.UPDATE,
        target_supplier_id=target.supplier_id,
        confirmed_identifier_keys=[KEY],
    )


def cancel_case(env):
    proposal = pending(env)
    return proposal, make_decision(proposal, RegistrationAction.CANCEL)


def tamper(env, supplier_id):
    current = env.identities.get(supplier_id)
    env.identities.update(current.model_copy(update={"legal_name": "Tampered"}))


def snapshot(env):
    return [
        p.read_bytes() if p.exists() else None
        for p in (env.identities.path, env.proposals.path, env.journal.path)
    ]


def raises(code):
    return pytest.raises(SupplierRegistrationCoordinatorError, match=code)


# --- normal flows -------------------------------------------------------


def test_create_to_completed(env):
    proposal, human = create_case(env)
    done = env.coordinator.execute(proposal.proposal_id, human)
    assert done.status == ProposalStatus.PERSISTED
    assert done.resulting_supplier_id == proposal.candidate.supplier_id
    assert env.proposals.get(proposal.proposal_id) == done
    stored = env.identities.get(proposal.candidate.supplier_id)
    assert stored.identifiers[0].human_confirmed is True
    record = env.journal.get(proposal.proposal_id)
    assert record.status == TransactionStatus.COMPLETED
    assert record.transaction_id == record.proposal_id == proposal.proposal_id
    assert record.supplier_before_hash is None
    assert record.supplier_after_hash == supplier_hash(stored)
    assert record.source_sha256 == SOURCE_SHA
    assert record.decision == human


def test_update_to_completed(env):
    target, proposal, human = update_case(env)
    before = supplier_hash(target)
    done = env.coordinator.execute(proposal.proposal_id, human)
    assert done.status == ProposalStatus.PERSISTED
    assert done.resulting_supplier_id == target.supplier_id
    updated = env.identities.get(target.supplier_id)
    assert len(updated.identifiers) == 1
    record = env.journal.get(proposal.proposal_id)
    assert record.status == TransactionStatus.COMPLETED
    assert record.target_supplier_id == target.supplier_id
    assert record.supplier_before_hash == before
    assert record.supplier_after_hash == supplier_hash(updated) != before


def test_cancel_never_writes_supplier(env, monkeypatch):
    proposal, human = cancel_case(env)
    create = Fault(monkeypatch, env.identities, "create", armed=True)
    update = Fault(monkeypatch, env.identities, "update", armed=True)
    done = env.coordinator.execute(proposal.proposal_id, human)
    assert done.status == ProposalStatus.CANCELLED
    assert done.resulting_supplier_id is None
    assert not env.identities.path.exists()
    assert create.calls == update.calls == 0
    record = env.journal.get(proposal.proposal_id)
    assert record.status == TransactionStatus.COMPLETED
    assert record.expected_supplier_id is None


def test_prepared_is_journaled_before_supplier_write(env, monkeypatch):
    proposal, human = create_case(env)
    seen = []
    original = env.identities.create

    def spy(identity):
        seen.append(env.journal.get(proposal.proposal_id).status)
        return original(identity)

    monkeypatch.setattr(env.identities, "create", spy)
    env.coordinator.execute(proposal.proposal_id, human)
    assert seen == [TransactionStatus.PREPARED]


# --- interruptions and recovery ----------------------------------------


def test_crash_after_prepared_before_create(env, monkeypatch):
    proposal, human = create_case(env)
    fault = Fault(monkeypatch, env.identities, "create")
    with pytest.raises(Crash):
        env.coordinator.execute(proposal.proposal_id, human)
    assert env.journal.get(proposal.proposal_id).status == TransactionStatus.PREPARED
    assert not env.identities.path.exists()
    fault.armed = False
    done = env.coordinator.recover(proposal.proposal_id)
    assert done.status == ProposalStatus.PERSISTED
    assert fault.calls == 1
    assert len(env.identities.list_active()) == 1


def test_crash_after_create_before_supplier_applied(env, monkeypatch):
    proposal, human = create_case(env)
    create = Fault(monkeypatch, env.identities, "create", when=lambda *a: False)
    fault = Fault(monkeypatch, env.journal, "put", when=status_is(TransactionStatus.SUPPLIER_APPLIED))
    with pytest.raises(Crash):
        env.coordinator.execute(proposal.proposal_id, human)
    assert env.identities.get(proposal.candidate.supplier_id) is not None
    assert env.journal.get(proposal.proposal_id).status == TransactionStatus.PREPARED
    fault.armed = False
    done = env.coordinator.recover(proposal.proposal_id)
    assert done.status == ProposalStatus.PERSISTED
    assert create.calls == 1
    assert len(env.identities.list_active()) == 1
    assert env.journal.get(proposal.proposal_id).status == TransactionStatus.COMPLETED


def test_crash_after_supplier_applied_before_proposal_complete(env, monkeypatch):
    proposal, human = create_case(env)
    create = Fault(monkeypatch, env.identities, "create", when=lambda *a: False)
    fault = Fault(monkeypatch, env.proposals, "complete")
    with pytest.raises(Crash):
        env.coordinator.execute(proposal.proposal_id, human)
    assert env.journal.get(proposal.proposal_id).status == TransactionStatus.SUPPLIER_APPLIED
    assert env.proposals.get(proposal.proposal_id).status == ProposalStatus.PENDING
    fault.armed = False
    done = env.coordinator.recover(proposal.proposal_id)
    assert done.status == ProposalStatus.PERSISTED
    assert create.calls == 1
    assert env.journal.get(proposal.proposal_id).status == TransactionStatus.COMPLETED


def test_crash_after_proposal_complete_before_journal_completed(env, monkeypatch):
    proposal, human = create_case(env)
    fault = Fault(monkeypatch, env.journal, "put", when=status_is(TransactionStatus.COMPLETED))
    with pytest.raises(Crash):
        env.coordinator.execute(proposal.proposal_id, human)
    assert env.proposals.get(proposal.proposal_id).status == ProposalStatus.PERSISTED
    fault.armed = False
    done = env.coordinator.recover(proposal.proposal_id)
    assert done.status == ProposalStatus.PERSISTED
    assert env.journal.get(proposal.proposal_id).status == TransactionStatus.COMPLETED
    assert len(env.identities.list_active()) == 1


def test_update_crash_before_write(env, monkeypatch):
    target, proposal, human = update_case(env)
    fault = Fault(monkeypatch, env.identities, "update")
    with pytest.raises(Crash):
        env.coordinator.execute(proposal.proposal_id, human)
    assert env.identities.get(target.supplier_id) == target
    fault.armed = False
    done = env.coordinator.recover(proposal.proposal_id)
    assert done.status == ProposalStatus.PERSISTED
    assert fault.calls == 1
    assert len(env.identities.get(target.supplier_id).identifiers) == 1


def test_update_crash_after_write_no_double_mutation(env, monkeypatch):
    target, proposal, human = update_case(env)
    update = Fault(monkeypatch, env.identities, "update", when=lambda *a: False)
    fault = Fault(monkeypatch, env.journal, "put", when=status_is(TransactionStatus.SUPPLIER_APPLIED))
    with pytest.raises(Crash):
        env.coordinator.execute(proposal.proposal_id, human)
    mutated = env.identities.get(target.supplier_id)
    assert len(mutated.identifiers) == 1
    fault.armed = False
    done = env.coordinator.recover(proposal.proposal_id)
    assert done.status == ProposalStatus.PERSISTED
    assert update.calls == 1
    assert env.identities.get(target.supplier_id) == mutated


def test_diverged_supplier_before_update_requires_recovery(env, monkeypatch):
    target, proposal, human = update_case(env)
    fault = Fault(monkeypatch, env.identities, "update")
    with pytest.raises(Crash):
        env.coordinator.execute(proposal.proposal_id, human)
    fault.armed = False
    tamper(env, target.supplier_id)
    tampered = env.identities.get(target.supplier_id)
    with raises("RECOVERY_REQUIRED"):
        env.coordinator.recover(proposal.proposal_id)
    record = env.journal.get(proposal.proposal_id)
    assert record.status == TransactionStatus.RECOVERY_REQUIRED
    assert record.error_code == "SUPPLIER_STATE_DIVERGED"
    assert env.proposals.get(proposal.proposal_id).status == ProposalStatus.PENDING
    assert env.identities.get(target.supplier_id) == tampered
    before = snapshot(env)
    with raises("RECOVERY_REQUIRED"):
        env.coordinator.recover(proposal.proposal_id)
    assert snapshot(env) == before


def test_diverged_supplier_after_supplier_applied_requires_recovery(env, monkeypatch):
    proposal, human = create_case(env)
    fault = Fault(monkeypatch, env.proposals, "complete")
    with pytest.raises(Crash):
        env.coordinator.execute(proposal.proposal_id, human)
    fault.armed = False
    tamper(env, proposal.candidate.supplier_id)
    with raises("RECOVERY_REQUIRED"):
        env.coordinator.recover(proposal.proposal_id)
    record = env.journal.get(proposal.proposal_id)
    assert record.status == TransactionStatus.RECOVERY_REQUIRED
    assert record.error_code == "SUPPLIER_STATE_DIVERGED"
    assert env.proposals.get(proposal.proposal_id).status == ProposalStatus.PENDING
    assert env.identities.get(proposal.candidate.supplier_id).legal_name == "Tampered"


# --- guards -------------------------------------------------------------


def test_already_terminal_proposal_refused(env):
    proposal, human = cancel_case(env)
    env.coordinator.execute(proposal.proposal_id, human)
    other = pending(env)
    done = env.service.propose(candidate("Other"), SOURCE_SHA)
    env.proposals.create(done)
    working = done.model_copy(deep=True)
    cancel = make_decision(done, RegistrationAction.CANCEL)
    env.service.cancel(working, cancel)
    env.proposals.complete(working)
    with raises("PROPOSAL_ALREADY_COMPLETED"):
        env.coordinator.execute(done.proposal_id, cancel)
    assert env.journal.get(done.proposal_id) is None
    assert env.journal.get(other.proposal_id) is None


def test_completed_replay_writes_nothing(env, monkeypatch):
    proposal, human = create_case(env)
    first = env.coordinator.execute(proposal.proposal_id, human)
    before = snapshot(env)
    create = Fault(monkeypatch, env.identities, "create")
    put = Fault(monkeypatch, env.journal, "put")
    assert env.coordinator.execute(proposal.proposal_id, human) == first
    assert env.coordinator.recover(proposal.proposal_id) == first
    assert snapshot(env) == before
    assert create.calls == put.calls == 0


def test_different_decision_refused_same_decision_resumes(env, monkeypatch):
    proposal, human = create_case(env)
    fault = Fault(monkeypatch, env.identities, "create")
    with pytest.raises(Crash):
        env.coordinator.execute(proposal.proposal_id, human)
    fault.armed = False
    different = make_decision(proposal, RegistrationAction.CANCEL)
    with raises("TRANSACTION_DECISION_MISMATCH"):
        env.coordinator.execute(proposal.proposal_id, different)
    assert env.coordinator.execute(proposal.proposal_id, human).status == ProposalStatus.PERSISTED
    assert len(env.identities.list_active()) == 1


def test_invalid_decision_leaves_no_trace(env):
    proposal = pending(env)
    bad = HumanRegistrationDecision(
        proposal_id=proposal.proposal_id,
        action=RegistrationAction.CREATE,
        human_confirmed=True,
    )
    with pytest.raises(Exception):
        env.coordinator.execute(proposal.proposal_id, bad)
    assert not env.journal.path.exists()
    assert not env.identities.path.exists()
    assert env.proposals.get(proposal.proposal_id).status == ProposalStatus.PENDING


def test_unknown_proposal_and_transaction(env):
    unknown = uuid4()
    human = HumanRegistrationDecision(
        proposal_id=unknown, action=RegistrationAction.CANCEL, human_confirmed=True
    )
    with raises("PROPOSAL_NOT_FOUND"):
        env.coordinator.execute(unknown, human)
    with raises("TRANSACTION_NOT_FOUND"):
        env.coordinator.recover(unknown)


# --- hash and journal storage ------------------------------------------


def test_supplier_hash_is_deterministic():
    one = candidate()
    clone = SupplierIdentity.model_validate(json.loads(one.model_dump_json()))
    assert supplier_hash(one) == supplier_hash(clone)
    assert len(supplier_hash(one)) == 64
    assert supplier_hash(one.model_copy(update={"legal_name": "Other"})) != supplier_hash(one)


def test_journal_atomic_write_with_backup(env, monkeypatch):
    replaced = []
    real_replace = __import__("os").replace

    def spy(src, dst):
        if str(dst) == str(env.journal.path):
            replaced.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr("services.supplier_registration_coordinator.os.replace", spy)
    proposal, human = cancel_case(env)
    env.coordinator.execute(proposal.proposal_id, human)
    path = env.journal.path
    assert replaced and all(
        src == str(path) + ".tmp" and dst == str(path) for src, dst in replaced
    )
    assert path.with_name(path.name + ".bak").exists()
    assert not path.with_name(path.name + ".tmp").exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1 and str(proposal.proposal_id) in payload["transactions"]


def test_invalid_journal_json_refused_without_overwrite(env):
    proposal, human = cancel_case(env)
    env.journal.path.parent.mkdir(parents=True, exist_ok=True)
    env.journal.path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SupplierTransactionJournalError, match="INVALID_STORAGE"):
        env.coordinator.execute(proposal.proposal_id, human)
    assert env.journal.path.read_text(encoding="utf-8") == "{not json"
    assert not env.journal.path.with_name("transactions.json.bak").exists()
    assert env.proposals.get(proposal.proposal_id).status == ProposalStatus.PENDING


# --- hygiene ------------------------------------------------------------


def test_no_transactional_custom_fields(env):
    proposal, human = create_case(env)
    env.coordinator.execute(proposal.proposal_id, human)
    raw = env.identities.path.read_text(encoding="utf-8")
    for token in ("transaction", "PREPARED", "SUPPLIER_APPLIED", "supplier_after_hash"):
        assert token not in raw
    assert env.identities.get(proposal.candidate.supplier_id).custom_fields == {}


def test_existing_repository_formats_unchanged(env):
    proposal, human = create_case(env)
    env.coordinator.execute(proposal.proposal_id, human)
    assert set(json.loads(env.identities.path.read_text(encoding="utf-8"))) == {
        "version",
        "suppliers",
    }
    assert set(json.loads(env.proposals.path.read_text(encoding="utf-8"))) == {
        "version",
        "proposals",
    }


def test_no_network(env, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network access")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    proposal, human = create_case(env)
    assert env.coordinator.execute(proposal.proposal_id, human).status == ProposalStatus.PERSISTED
