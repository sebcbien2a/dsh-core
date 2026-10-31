"""Shared schemas for human-confirmed supplier registration proposals."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemas.supplier_identity_schema import SupplierIdentity

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
IdentifierKey = tuple[str | None, str, str]


class RegistrationAction(str, Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    CANCEL = "CANCEL"


class ProposalStatus(str, Enum):
    PENDING = "PENDING"
    PERSISTED = "PERSISTED"
    CANCELLED = "CANCELLED"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IdentifierConflict(StrictModel):
    identifier_key: IdentifierKey
    supplier_id: UUID


class RegistrationDecisionRecord(StrictModel):
    proposal_id: UUID
    action: RegistrationAction
    resulting_supplier_id: UUID | None = None
    target_supplier_id: UUID | None = None
    source_sha256: str
    confirmed_identifier_keys: list[IdentifierKey]
    accepted_aliases: list[str]
    justification: str | None
    confirmed_at: datetime

    @field_validator("source_sha256")
    @classmethod
    def valid_source_sha256(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("source_sha256 must be 64 lowercase hexadecimal characters")
        return value


class RegistrationProposal(StrictModel):
    proposal_id: UUID = Field(default_factory=uuid4)
    candidate: SupplierIdentity
    suggested_supplier_ids: list[UUID] = Field(default_factory=list)
    identifier_conflicts: list[IdentifierConflict] = Field(default_factory=list)
    source_sha256: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: ProposalStatus = ProposalStatus.PENDING
    completed_at: datetime | None = None
    resulting_supplier_id: UUID | None = None
    decision_record: RegistrationDecisionRecord | None = None

    @field_validator("source_sha256")
    @classmethod
    def valid_source_sha256(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("source_sha256 must be 64 lowercase hexadecimal characters")
        return value

    @model_validator(mode="after")
    def validate_terminal_state(self) -> "RegistrationProposal":
        if self.status == ProposalStatus.PENDING:
            if any((self.completed_at, self.resulting_supplier_id, self.decision_record)):
                raise ValueError("pending proposal cannot contain terminal decision data")
            return self
        if self.completed_at is None or self.decision_record is None:
            raise ValueError("completed proposal requires completion time and decision record")
        if self.decision_record.proposal_id != self.proposal_id:
            raise ValueError("decision record proposal_id does not match")
        if self.decision_record.source_sha256 != self.source_sha256:
            raise ValueError("decision record source hash does not match")
        if self.status == ProposalStatus.PERSISTED:
            if (
                self.decision_record.action == RegistrationAction.CANCEL
                or self.resulting_supplier_id is None
                or self.decision_record.resulting_supplier_id != self.resulting_supplier_id
            ):
                raise ValueError("persisted proposal requires a resulting supplier")
        elif (
            self.decision_record.action != RegistrationAction.CANCEL
            or self.resulting_supplier_id is not None
        ):
            raise ValueError("cancelled proposal must have a CANCEL record and no result")
        return self
