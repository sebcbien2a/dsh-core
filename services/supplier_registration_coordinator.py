"""Durable local saga coordinating supplier writes and proposal completion.

Two JSON files (suppliers, proposals) cannot be updated atomically together, so
an intent journal (PREPARED -> SUPPLIER_APPLIED -> COMPLETED) is written first
and lets recover() finish or classify any interruption. Transactional metadata
lives only in this journal, never in supplier data. No rollback, deletion,
merge, identifier deduction, network access or amount computation happens here.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import NoReturn
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from repositories.supplier_identity_repository import (
    SupplierIdentityRepository,
    SupplierIdentityRepositoryError,
)
from repositories.supplier_registration_repository import (
    SupplierRegistrationRepository,
    SupplierRegistrationRepositoryError,
)
from schemas.supplier_identity_schema import SupplierIdentity
from services.supplier_registration_service import (
    HumanRegistrationDecision,
    ProposalStatus,
    RegistrationAction,
    RegistrationProposal,
    SupplierRegistrationError,
    SupplierRegistrationService,
)

_FORMAT_VERSION = 1


class TransactionStatus(str, Enum):
    PREPARED = "PREPARED"
    SUPPLIER_APPLIED = "SUPPLIER_APPLIED"
    COMPLETED = "COMPLETED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def supplier_hash(identity: SupplierIdentity) -> str:
    """SHA-256 of the canonical JSON representation of a supplier."""
    canonical = json.dumps(
        identity.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TransactionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: UUID
    proposal_id: UUID
    action: RegistrationAction
    target_supplier_id: UUID | None = None
    expected_supplier_id: UUID | None = None
    source_sha256: str
    decision: HumanRegistrationDecision
    supplier_before_hash: str | None = None
    supplier_after_hash: str | None = None
    status: TransactionStatus = TransactionStatus.PREPARED
    error_code: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def consistent(self) -> "TransactionRecord":
        if self.transaction_id != self.proposal_id or self.decision.proposal_id != self.proposal_id:
            raise ValueError("transaction_id, proposal_id and decision must share the same id")
        if self.action != self.decision.action:
            raise ValueError("action must match the decision")
        if self.action == RegistrationAction.CANCEL:
            if any((self.expected_supplier_id, self.supplier_before_hash, self.supplier_after_hash)):
                raise ValueError("a CANCEL transaction never concerns a supplier")
        elif self.expected_supplier_id is None or self.supplier_after_hash is None:
            raise ValueError("CREATE and UPDATE require expected_supplier_id and supplier_after_hash")
        return self


class SupplierTransactionJournalError(ValueError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


class SupplierTransactionJournal:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> dict[str, TransactionRecord]:
        if not self._path.exists():
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            raw = payload["transactions"]
            if not isinstance(raw, dict):
                raise TypeError("transactions must be an object")
            records: dict[str, TransactionRecord] = {}
            for key, value in raw.items():
                record = TransactionRecord.model_validate(value)
                if str(record.transaction_id) != key:
                    raise ValueError("storage key differs from transaction_id")
                records[key] = record
            return records
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise SupplierTransactionJournalError("INVALID_STORAGE", str(exc)) from exc

    def _save(self, records: dict[str, TransactionRecord]) -> None:
        payload = {
            "version": _FORMAT_VERSION,
            "transactions": {key: item.model_dump(mode="json") for key, item in records.items()},
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        backup = self._path.with_name(self._path.name + ".bak")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        if self._path.exists():
            shutil.copy2(self._path, backup)
        os.replace(tmp, self._path)

    def get(self, proposal_id: UUID | str) -> TransactionRecord | None:
        return self._load().get(str(UUID(str(proposal_id))))

    def create(self, record: TransactionRecord) -> TransactionRecord:
        if record.status != TransactionStatus.PREPARED:
            raise SupplierTransactionJournalError("INVALID_TRANSACTION", "must start PREPARED")
        records = self._load()
        key = str(record.transaction_id)
        if key in records:
            raise SupplierTransactionJournalError("TRANSACTION_ALREADY_EXISTS", key)
        records[key] = record
        self._save(records)
        return record

    def put(self, record: TransactionRecord) -> TransactionRecord:
        records = self._load()
        key = str(record.transaction_id)
        stored = records.get(key)
        if stored is None:
            raise SupplierTransactionJournalError("TRANSACTION_NOT_FOUND", key)
        if stored.status == TransactionStatus.COMPLETED:
            if stored.model_dump(mode="json") == record.model_dump(mode="json"):
                return stored
            raise SupplierTransactionJournalError("COMPLETED_TRANSACTION_IMMUTABLE", key)
        records[key] = record
        self._save(records)
        return record


class SupplierRegistrationCoordinatorError(ValueError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


class SupplierRegistrationCoordinator:
    def __init__(
        self,
        identities: SupplierIdentityRepository,
        proposals: SupplierRegistrationRepository,
        journal: SupplierTransactionJournal,
    ) -> None:
        self._identities = identities
        self._proposals = proposals
        self._journal = journal

    # --- public API ---------------------------------------------------

    def execute(
        self, proposal_id: UUID | str, decision: HumanRegistrationDecision
    ) -> RegistrationProposal:
        proposal_id = UUID(str(proposal_id))
        existing = self._journal.get(proposal_id)
        if existing is not None:
            if existing.decision.model_dump(mode="json") != decision.model_dump(mode="json"):
                raise SupplierRegistrationCoordinatorError("TRANSACTION_DECISION_MISMATCH")
            return self.recover(proposal_id)
        if decision.proposal_id != proposal_id:
            raise SupplierRegistrationCoordinatorError("PROPOSAL_ID_MISMATCH")
        stored = self._proposals.get(proposal_id)
        if stored is None:
            raise SupplierRegistrationCoordinatorError("PROPOSAL_NOT_FOUND")
        if stored.status != ProposalStatus.PENDING:
            raise SupplierRegistrationCoordinatorError("PROPOSAL_ALREADY_COMPLETED")

        expected = self._dry_run(stored, decision)
        record = TransactionRecord(
            transaction_id=proposal_id,
            proposal_id=proposal_id,
            action=decision.action,
            target_supplier_id=decision.target_supplier_id,
            expected_supplier_id=expected.supplier_id if expected else None,
            source_sha256=stored.source_sha256,
            decision=decision,
            supplier_before_hash=self._before_hash(decision),
            supplier_after_hash=supplier_hash(expected) if expected else None,
        )
        self._journal.create(record)
        return self.recover(proposal_id)

    def recover(self, proposal_id: UUID | str) -> RegistrationProposal:
        record = self._journal.get(proposal_id)
        if record is None:
            raise SupplierRegistrationCoordinatorError("TRANSACTION_NOT_FOUND")
        if record.status == TransactionStatus.COMPLETED:
            stored = self._proposals.get(record.proposal_id)
            if stored is None:
                raise SupplierRegistrationCoordinatorError("PROPOSAL_NOT_FOUND")
            return stored
        if record.status == TransactionStatus.RECOVERY_REQUIRED:
            raise SupplierRegistrationCoordinatorError("RECOVERY_REQUIRED", record.error_code or "")
        if record.action == RegistrationAction.CANCEL:
            return self._finish(record)

        supplier = self._identities.get(record.expected_supplier_id)
        current = supplier_hash(supplier) if supplier is not None else None
        if record.status == TransactionStatus.SUPPLIER_APPLIED:
            if current != record.supplier_after_hash:
                self._fail(record, "SUPPLIER_STATE_DIVERGED")
            return self._finish(record)
        if current is not None and current == record.supplier_after_hash:
            return self._finish(self._mark_applied(record, current))
        if current == record.supplier_before_hash:
            return self._finish(self._apply(record))
        self._fail(record, "SUPPLIER_STATE_DIVERGED")

    # --- internals ----------------------------------------------------

    def _before_hash(self, decision: HumanRegistrationDecision) -> str | None:
        if decision.action != RegistrationAction.UPDATE:
            return None
        target = self._identities.get(decision.target_supplier_id)
        return supplier_hash(target) if target is not None else None

    @staticmethod
    def _pending_copy(stored: RegistrationProposal) -> RegistrationProposal:
        return stored.model_copy(
            deep=True,
            update={
                "status": ProposalStatus.PENDING,
                "completed_at": None,
                "resulting_supplier_id": None,
                "decision_record": None,
            },
        )

    def _dry_run(
        self, stored: RegistrationProposal, decision: HumanRegistrationDecision
    ) -> SupplierIdentity | None:
        """Validate and compute the expected supplier on a scratch copy; writes nothing real."""
        with tempfile.TemporaryDirectory() as tmp:
            scratch_path = Path(tmp) / "suppliers.json"
            if self._identities.path.exists():
                shutil.copy2(self._identities.path, scratch_path)
            scratch = SupplierRegistrationService(SupplierIdentityRepository(scratch_path))
            pending = self._pending_copy(stored)
            if decision.action == RegistrationAction.CANCEL:
                scratch.cancel(pending, decision)
                return None
            return scratch.confirm(pending, decision)

    @staticmethod
    def _advance(
        record: TransactionRecord, status: TransactionStatus, **updates: object
    ) -> TransactionRecord:
        return record.model_copy(update={"status": status, "updated_at": _now(), **updates})

    def _fail(self, record: TransactionRecord, code: str) -> NoReturn:
        self._journal.put(
            self._advance(record, TransactionStatus.RECOVERY_REQUIRED, error_code=code)
        )
        raise SupplierRegistrationCoordinatorError("RECOVERY_REQUIRED", code)

    def _mark_applied(self, record: TransactionRecord, after_hash: str) -> TransactionRecord:
        return self._journal.put(
            self._advance(record, TransactionStatus.SUPPLIER_APPLIED, supplier_after_hash=after_hash)
        )

    def _apply(self, record: TransactionRecord) -> TransactionRecord:
        try:
            stored = self._proposals.get(record.proposal_id)
            if stored is None:
                raise SupplierRegistrationRepositoryError("PROPOSAL_NOT_FOUND")
            expected = self._dry_run(stored, record.decision)
        except (
            SupplierRegistrationError,
            SupplierIdentityRepositoryError,
            SupplierRegistrationRepositoryError,
        ) as exc:
            self._fail(record, exc.code)
        if expected is None or supplier_hash(expected) != record.supplier_after_hash:
            self._fail(record, "EXPECTED_SUPPLIER_CHANGED")
        try:
            if record.action == RegistrationAction.CREATE:
                applied = self._identities.create(expected)
            else:
                applied = self._identities.update(expected)
        except SupplierIdentityRepositoryError as exc:
            self._fail(record, exc.code)
        return self._mark_applied(record, supplier_hash(applied))

    def _finish(self, record: TransactionRecord) -> RegistrationProposal:
        try:
            stored = self._proposals.get(record.proposal_id)
            if stored is None:
                raise SupplierRegistrationRepositoryError("PROPOSAL_NOT_FOUND")
            pending = self._pending_copy(stored)
            resulting = (
                None if record.action == RegistrationAction.CANCEL else record.expected_supplier_id
            )
            SupplierRegistrationService._complete(
                pending, record.decision, resulting_supplier_id=resulting
            )
            completed = self._proposals.complete(pending)
        except SupplierRegistrationRepositoryError as exc:
            self._fail(record, exc.code)
        self._journal.put(self._advance(record, TransactionStatus.COMPLETED))
        return completed
