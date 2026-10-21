"""Lifecycle transitions for immutable canonical skill revisions."""

import hashlib
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from repositories.canonical_skill_repository import (
    CanonicalSkillRepository,
    SkillRevisionAlreadyExistsError,
    _semver_key,
)
from schemas.skill_manifest_schema import (
    SkillManifest,
    SkillRevisionReference,
    SkillStatus,
    SkillTestEvidence,
)


class SkillLifecycleError(Exception):
    """Base exception for skill lifecycle errors."""


class InvalidSkillTransitionError(SkillLifecycleError):
    """Raised when a requested status transition is not allowed."""


class SkillApprovalRequirementsError(SkillLifecycleError):
    """Raised when an approval transition lacks its required approver or evidence."""


class SkillPublicationRequirementsError(SkillLifecycleError):
    """Raised when publication requirements are not met."""


class InvalidSkillVersionError(SkillLifecycleError):
    """Raised when the requested revision version is invalid or not newer."""


_ALLOWED_TRANSITIONS = {
    (SkillStatus.CANDIDATE, SkillStatus.TESTING),
    (SkillStatus.CANDIDATE, SkillStatus.REJECTED),
    (SkillStatus.TESTING, SkillStatus.APPROVED),
    (SkillStatus.TESTING, SkillStatus.REJECTED),
    (SkillStatus.APPROVED, SkillStatus.PUBLISHED),
    (SkillStatus.APPROVED, SkillStatus.REJECTED),
    (SkillStatus.PUBLISHED, SkillStatus.RETIRED),
}


class SkillLifecycleService:
    def __init__(self, repository: CanonicalSkillRepository):
        self.repository = repository

    def transition(
        self,
        skill_id: UUID,
        current_version: str,
        new_version: str,
        target_status: SkillStatus,
        actor: str,
        approver: str | None = None,
        test_evidence: list[SkillTestEvidence] | None = None,
        quality_score: float | None = None,
        published_at: datetime | None = None,
    ) -> SkillManifest:
        source = self.repository.get_revision(skill_id, current_version)

        if (source.status, target_status) not in _ALLOWED_TRANSITIONS:
            raise InvalidSkillTransitionError(
                f"Transition {source.status.value} -> {target_status.value} is not allowed"
            )

        if not isinstance(actor, str) or not actor.strip():
            raise SkillLifecycleError("actor must not be empty or whitespace")
        normalized_actor = actor.strip()
        normalized_approver = approver.strip() if approver is not None else None

        try:
            source_key = _semver_key(current_version)
            new_key = _semver_key(new_version)
        except (TypeError, ValueError) as exc:
            raise InvalidSkillVersionError("Both versions must be valid strict SemVer") from exc

        if new_key <= source_key:
            raise InvalidSkillVersionError(
                "new_version must be strictly greater than current_version"
            )
        if self.repository.exists(skill_id, new_version):
            raise InvalidSkillVersionError(
                f"Revision {skill_id}@{new_version} already exists"
            )

        evidence = (
            list(source.test_evidence)
            if test_evidence is None
            else list(test_evidence)
        )
        self._validate_target_requirements(
            source=source,
            target_status=target_status,
            approver=normalized_approver,
            test_evidence=evidence,
            published_at=published_at,
        )

        now = datetime.now(timezone.utc)
        metadata: dict[str, Any] = dict(source.metadata)
        metadata.update(
            {
                "previous_version": current_version,
                "transition_actor": normalized_actor,
                "transition_from": source.status.value,
                "transition_to": target_status.value,
            }
        )
        if target_status is SkillStatus.RETIRED:
            metadata["retired_published_version"] = current_version

        normalized_published_at = (
            published_at.astimezone(timezone.utc)
            if target_status is SkillStatus.PUBLISHED and published_at is not None
            else None
        )
        manifest_data = source.model_dump()
        manifest_data.update(
            {
                "version": new_version,
                "status": target_status,
                "supersedes": SkillRevisionReference(
                    skill_id=skill_id,
                    version=current_version,
                ),
                "content_hash": hashlib.sha256(
                    source.content.encode("utf-8")
                ).hexdigest(),
                "author": normalized_actor,
                "approver": normalized_approver,
                "created_at": now,
                "updated_at": now,
                "published_at": normalized_published_at,
                "test_evidence": evidence,
                "usage_count": 0,
                "quality_score": (
                    source.quality_score if quality_score is None else quality_score
                ),
                "metadata": metadata,
            }
        )

        try:
            new_manifest = SkillManifest.model_validate(manifest_data)
        except ValidationError as exc:
            if target_status is SkillStatus.PUBLISHED:
                raise SkillPublicationRequirementsError(
                    "Published revision does not satisfy its manifest requirements"
                ) from exc
            if target_status is SkillStatus.APPROVED:
                raise SkillApprovalRequirementsError(
                    "Approved revision does not satisfy its manifest requirements"
                ) from exc
            raise SkillLifecycleError("New revision does not satisfy the manifest contract") from exc

        try:
            self.repository.save_revision(new_manifest)
        except SkillRevisionAlreadyExistsError as exc:
            raise InvalidSkillVersionError(
                f"Revision {skill_id}@{new_version} already exists"
            ) from exc

        return self.repository.get_revision(skill_id, new_version)

    def _validate_target_requirements(
        self,
        source: SkillManifest,
        target_status: SkillStatus,
        approver: str | None,
        test_evidence: list[SkillTestEvidence],
        published_at: datetime | None,
    ) -> None:
        if target_status is SkillStatus.APPROVED:
            if not approver:
                raise SkillApprovalRequirementsError(
                    "approver is required for APPROVED"
                )
            if not any(evidence.passed for evidence in test_evidence):
                raise SkillApprovalRequirementsError(
                    "at least one successful test is required for APPROVED"
                )
            if published_at is not None:
                raise SkillApprovalRequirementsError(
                    "published_at is not allowed for APPROVED"
                )
        elif target_status not in {SkillStatus.PUBLISHED, SkillStatus.RETIRED}:
            if published_at is not None:
                raise SkillLifecycleError(
                    f"published_at is not allowed for {target_status.value}"
                )

        if target_status is SkillStatus.PUBLISHED:
            if source.status is not SkillStatus.APPROVED:
                raise SkillPublicationRequirementsError(
                    "PUBLISHED must transition from APPROVED"
                )
            if not approver:
                raise SkillPublicationRequirementsError(
                    "approver is required for PUBLISHED"
                )
            if not any(evidence.passed for evidence in test_evidence):
                raise SkillPublicationRequirementsError(
                    "at least one successful test is required for PUBLISHED"
                )
            if published_at is None:
                raise SkillPublicationRequirementsError(
                    "published_at is required for PUBLISHED"
                )
            if (
                published_at.tzinfo is None
                or published_at.utcoffset() is None
                or published_at.utcoffset().total_seconds() != 0
            ):
                raise SkillPublicationRequirementsError(
                    "published_at must have a UTC timezone with zero offset"
                )

        if target_status is SkillStatus.RETIRED:
            if source.status is not SkillStatus.PUBLISHED:
                raise InvalidSkillTransitionError(
                    "RETIRED must transition from PUBLISHED"
                )
            if published_at is not None:
                raise InvalidSkillTransitionError(
                    "published_at must be absent for RETIRED"
                )
