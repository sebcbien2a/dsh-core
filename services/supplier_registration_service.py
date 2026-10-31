"""Explicit human-confirmed registration for canonical supplier identities."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from pydantic import Field, field_validator

from repositories.supplier_identity_repository import (
    CONFIRMED_IDENTIFIER_COLLISION,
    SupplierIdentityRepository,
    SupplierIdentityRepositoryError,
)
from schemas.supplier_identity_schema import SupplierIdentifier, SupplierIdentity
from schemas.supplier_registration_schema import (
    IdentifierConflict,
    IdentifierKey,
    RegistrationAction,
    RegistrationDecisionRecord,
    RegistrationProposal,
    ProposalStatus,
    StrictModel,
)


class HumanRegistrationDecision(StrictModel):
    proposal_id: UUID
    action: RegistrationAction
    target_supplier_id: UUID | None = None
    confirmed_identifier_keys: list[IdentifierKey] = Field(default_factory=list)
    accepted_aliases: list[str] = Field(default_factory=list)
    justification: str | None = None
    confirmed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    human_confirmed: bool = False

    @field_validator("confirmed_identifier_keys")
    @classmethod
    def normalize_identifier_keys(cls, keys: list[IdentifierKey]) -> list[IdentifierKey]:
        normalized: list[IdentifierKey] = []
        for country, identifier_type, value in keys:
            key = (
                country.strip().upper() if country else None,
                identifier_type.strip().upper(),
                value.strip(),
            )
            if not key[1] or not key[2]:
                raise ValueError("confirmed identifier keys require type and value")
            if key not in normalized:
                normalized.append(key)
        return normalized

    @field_validator("accepted_aliases")
    @classmethod
    def clean_accepted_aliases(cls, aliases: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for alias in aliases:
            clean = alias.strip()
            if clean and clean.casefold() not in seen:
                result.append(clean)
                seen.add(clean.casefold())
        return result

    @field_validator("justification")
    @classmethod
    def clean_justification(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class SupplierRegistrationError(ValueError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


class SupplierRegistrationService:
    def __init__(self, repository: SupplierIdentityRepository) -> None:
        self._repository = repository

    @staticmethod
    def _identifier_key(identifier: SupplierIdentifier) -> IdentifierKey | None:
        if not identifier.normalized_value:
            return None
        return (
            identifier.country,
            identifier.identifier_type,
            identifier.normalized_value,
        )

    def propose(self, candidate: SupplierIdentity, source_sha256: str) -> RegistrationProposal:
        source = SupplierIdentity.model_validate(candidate.model_dump(mode="json"))
        suggested: dict[UUID, None] = {}
        for name in source.all_names():
            for identity in self._repository.suggest_by_name(name):
                suggested[identity.supplier_id] = None

        conflicts: dict[tuple[IdentifierKey, UUID], IdentifierConflict] = {}
        for identifier in source.identifiers:
            key = self._identifier_key(identifier)
            if key is None:
                continue
            country, identifier_type, value = key
            existing = self._repository.find_by_confirmed_identifier(
                identifier_type, value, country
            )
            if existing is not None:
                conflicts[(key, existing.supplier_id)] = IdentifierConflict(
                    identifier_key=key,
                    supplier_id=existing.supplier_id,
                )

        return RegistrationProposal(
            candidate=source,
            suggested_supplier_ids=list(suggested),
            identifier_conflicts=list(conflicts.values()),
            source_sha256=source_sha256,
        )

    @staticmethod
    def _check_decision(
        proposal: RegistrationProposal, decision: HumanRegistrationDecision
    ) -> None:
        if decision.proposal_id != proposal.proposal_id:
            raise SupplierRegistrationError("PROPOSAL_ID_MISMATCH")
        if not decision.human_confirmed:
            raise SupplierRegistrationError("HUMAN_CONFIRMATION_REQUIRED")

    @staticmethod
    def _require_justification(decision: HumanRegistrationDecision) -> None:
        if not decision.justification:
            raise SupplierRegistrationError("JUSTIFICATION_REQUIRED")

    @staticmethod
    def _selected_candidate_identifiers(
        candidate: SupplierIdentity,
        keys: set[IdentifierKey],
        decision: HumanRegistrationDecision,
    ) -> list[SupplierIdentifier]:
        selected: list[SupplierIdentifier] = []
        found: set[IdentifierKey] = set()
        for original in candidate.identifiers:
            key = SupplierRegistrationService._identifier_key(original)
            if key is None:
                selected.append(
                    original.model_copy(
                        update={"human_confirmed": False, "confirmed_at": None}
                    )
                )
            elif key in keys:
                found.add(key)
                selected.append(
                    original.model_copy(
                        update={
                            "human_confirmed": True,
                            "confirmed_at": decision.confirmed_at,
                            "justification": decision.justification,
                        }
                    )
                )
            else:
                selected.append(
                    original.model_copy(
                        update={"human_confirmed": False, "confirmed_at": None}
                    )
                )
        if found != keys:
            raise SupplierRegistrationError(
                "CONFIRMED_IDENTIFIER_NOT_IN_CANDIDATE",
                repr(sorted(keys - found, key=repr)),
            )
        return selected

    def _assert_no_collision(self, keys: set[IdentifierKey], target: UUID | None) -> None:
        for country, identifier_type, value in keys:
            existing = self._repository.find_by_confirmed_identifier(
                identifier_type, value, country
            )
            if existing is not None and existing.supplier_id != target:
                raise SupplierRegistrationError(
                    CONFIRMED_IDENTIFIER_COLLISION,
                    f"{country or '-'}/{identifier_type}/{value} belongs to {existing.supplier_id}",
                )

    def confirm(
        self, proposal: RegistrationProposal, decision: HumanRegistrationDecision
    ) -> SupplierIdentity:
        self._ensure_pending(proposal)
        self._check_decision(proposal, decision)
        if decision.action == RegistrationAction.CANCEL:
            raise SupplierRegistrationError("USE_CANCEL_FOR_CANCEL_ACTION")
        self._require_justification(decision)

        selected_keys = set(decision.confirmed_identifier_keys)
        identifiers = self._selected_candidate_identifiers(
            proposal.candidate, selected_keys, decision
        )

        if decision.action == RegistrationAction.CREATE:
            if decision.target_supplier_id is not None:
                raise SupplierRegistrationError("CREATE_MUST_NOT_HAVE_TARGET")
            self._assert_no_collision(selected_keys, target=None)
            candidate = SupplierIdentity.model_validate(
                proposal.candidate.model_dump(mode="json")
            )
            candidate = candidate.model_copy(
                update={
                    "identifiers": identifiers,
                    "aliases": list(decision.accepted_aliases),
                }
            )
            try:
                persisted = self._repository.create(candidate)
            except SupplierIdentityRepositoryError as exc:
                if exc.code == CONFIRMED_IDENTIFIER_COLLISION:
                    raise SupplierRegistrationError(exc.code, str(exc)) from exc
                raise
        elif decision.action == RegistrationAction.UPDATE:
            if decision.target_supplier_id is None:
                raise SupplierRegistrationError("TARGET_SUPPLIER_REQUIRED")
            target = self._repository.get(decision.target_supplier_id)
            if target is None:
                raise SupplierRegistrationError("TARGET_SUPPLIER_NOT_FOUND")
            self._assert_no_collision(selected_keys, target=target.supplier_id)

            merged_identifiers = list(target.identifiers)
            for identifier in identifiers:
                key = self._identifier_key(identifier)
                if key not in selected_keys:
                    continue
                matching_indexes = [
                    index
                    for index, existing in enumerate(merged_identifiers)
                    if self._identifier_key(existing) == key
                ]
                if not matching_indexes:
                    merged_identifiers.append(identifier)
                    continue
                for index in matching_indexes:
                    existing = merged_identifiers[index]
                    if not existing.human_confirmed:
                        merged_identifiers[index] = existing.model_copy(
                            update={
                                "human_confirmed": True,
                                "confirmed_at": decision.confirmed_at,
                                "justification": decision.justification,
                            }
                        )
            merged_aliases = list(target.aliases)
            known_aliases = {alias.casefold() for alias in merged_aliases}
            for alias in decision.accepted_aliases:
                if alias.casefold() not in known_aliases:
                    merged_aliases.append(alias)
                    known_aliases.add(alias.casefold())
            updated = target.model_copy(
                update={"identifiers": merged_identifiers, "aliases": merged_aliases}
            )
            try:
                persisted = self._repository.update(updated)
            except SupplierIdentityRepositoryError as exc:
                if exc.code == CONFIRMED_IDENTIFIER_COLLISION:
                    raise SupplierRegistrationError(exc.code, str(exc)) from exc
                raise
        else:
            raise SupplierRegistrationError("UNSUPPORTED_ACTION")

        self._complete(
            proposal,
            decision,
            resulting_supplier_id=persisted.supplier_id,
        )
        return persisted

    def cancel(
        self, proposal: RegistrationProposal, decision: HumanRegistrationDecision
    ) -> RegistrationProposal:
        self._ensure_pending(proposal)
        self._check_decision(proposal, decision)
        if decision.action != RegistrationAction.CANCEL:
            raise SupplierRegistrationError("CANCEL_ACTION_REQUIRED")
        self._complete(proposal, decision, resulting_supplier_id=None)
        return proposal

    @staticmethod
    def _ensure_pending(proposal: RegistrationProposal) -> None:
        if proposal.status != ProposalStatus.PENDING:
            raise SupplierRegistrationError("PROPOSAL_ALREADY_COMPLETED")

    @staticmethod
    def _complete(
        proposal: RegistrationProposal,
        decision: HumanRegistrationDecision,
        *,
        resulting_supplier_id: UUID | None,
    ) -> None:
        completed_at = decision.confirmed_at
        record = RegistrationDecisionRecord(
            proposal_id=proposal.proposal_id,
            action=decision.action,
            resulting_supplier_id=resulting_supplier_id,
            target_supplier_id=decision.target_supplier_id,
            source_sha256=proposal.source_sha256,
            confirmed_identifier_keys=decision.confirmed_identifier_keys,
            accepted_aliases=decision.accepted_aliases,
            justification=decision.justification,
            confirmed_at=decision.confirmed_at,
        )
        proposal.resulting_supplier_id = resulting_supplier_id
        proposal.decision_record = record
        proposal.completed_at = completed_at
        proposal.status = (
            ProposalStatus.CANCELLED
            if decision.action == RegistrationAction.CANCEL
            else ProposalStatus.PERSISTED
        )
