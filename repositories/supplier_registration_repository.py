"""Local JSON repository for supplier RegistrationProposal and their decision journal.

Keyed by proposal_id. A proposal is stored PENDING, then completed exactly once
as PERSISTED or CANCELLED. Nothing is ever deleted, and nothing here touches
supplier data, the legacy repository, the network, or amounts.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from uuid import UUID

from services.supplier_registration_service import ProposalStatus, RegistrationProposal

PROPOSAL_ALREADY_EXISTS = "PROPOSAL_ALREADY_EXISTS"
PROPOSAL_NOT_FOUND = "PROPOSAL_NOT_FOUND"
INVALID_PROPOSAL_TRANSITION = "INVALID_PROPOSAL_TRANSITION"
TERMINAL_PROPOSAL_IMMUTABLE = "TERMINAL_PROPOSAL_IMMUTABLE"
INVALID_STORAGE = "INVALID_STORAGE"
_FORMAT_VERSION = 1
_TERMINAL = (ProposalStatus.PERSISTED, ProposalStatus.CANCELLED)


class SupplierRegistrationRepositoryError(ValueError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


class SupplierRegistrationRepository:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    # --- persistence -------------------------------------------------

    def _load(self) -> dict[str, RegistrationProposal]:
        if not self._path.exists():
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            records = payload["proposals"]
            if not isinstance(records, dict):
                raise TypeError("proposals must be an object")
            proposals: dict[str, RegistrationProposal] = {}
            for key, record in records.items():
                proposal = RegistrationProposal.model_validate(record)
                if str(proposal.proposal_id) != key:
                    raise ValueError("storage key differs from proposal_id")
                proposals[key] = proposal
            return proposals
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise SupplierRegistrationRepositoryError(INVALID_STORAGE, str(exc)) from exc

    def _save(self, proposals: dict[str, RegistrationProposal]) -> None:
        payload = {
            "version": _FORMAT_VERSION,
            "proposals": {
                key: proposal.model_dump(mode="json") for key, proposal in proposals.items()
            },
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

    @staticmethod
    def _validated(proposal: RegistrationProposal) -> RegistrationProposal:
        return RegistrationProposal.model_validate(proposal.model_dump(mode="json"))

    # --- public API ---------------------------------------------------

    def create(self, proposal: RegistrationProposal) -> RegistrationProposal:
        proposal = self._validated(proposal)
        if proposal.status != ProposalStatus.PENDING:
            raise SupplierRegistrationRepositoryError(
                INVALID_PROPOSAL_TRANSITION, "only a PENDING proposal can be created"
            )
        proposals = self._load()
        key = str(proposal.proposal_id)
        if key in proposals:
            raise SupplierRegistrationRepositoryError(PROPOSAL_ALREADY_EXISTS, key)
        proposals[key] = proposal
        self._save(proposals)
        return proposal

    def get(self, proposal_id: UUID | str) -> RegistrationProposal | None:
        return self._load().get(str(UUID(str(proposal_id))))

    def complete(self, proposal: RegistrationProposal) -> RegistrationProposal:
        proposal = self._validated(proposal)
        if proposal.status not in _TERMINAL:
            raise SupplierRegistrationRepositoryError(
                INVALID_PROPOSAL_TRANSITION, "complete requires a PERSISTED or CANCELLED proposal"
            )
        proposals = self._load()
        key = str(proposal.proposal_id)
        stored = proposals.get(key)
        if stored is None:
            raise SupplierRegistrationRepositoryError(PROPOSAL_NOT_FOUND, key)
        if stored.status in _TERMINAL:
            if stored == proposal:
                return stored
            raise SupplierRegistrationRepositoryError(TERMINAL_PROPOSAL_IMMUTABLE, key)
        if stored.candidate != proposal.candidate or stored.source_sha256 != proposal.source_sha256:
            raise SupplierRegistrationRepositoryError(
                INVALID_PROPOSAL_TRANSITION, "candidate and source_sha256 are immutable"
            )
        proposals[key] = proposal
        self._save(proposals)
        return proposal

    def list_by_status(self, status: ProposalStatus) -> list[RegistrationProposal]:
        status = ProposalStatus(status)
        return [item for item in self._load().values() if item.status == status]

    def list_pending(self) -> list[RegistrationProposal]:
        return self.list_by_status(ProposalStatus.PENDING)

    def list_completed(self) -> list[RegistrationProposal]:
        return [item for item in self._load().values() if item.status in _TERMINAL]
