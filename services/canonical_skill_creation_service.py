"""Creation of the first immutable candidate revision for a canonical skill."""

import hashlib
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from repositories.canonical_skill_repository import (
    CanonicalSkillRepository,
    SkillRevisionAlreadyExistsError,
)
from schemas.skill_manifest_schema import (
    SkillArtifact,
    SkillDependency,
    SkillDomain,
    SkillManifest,
    SkillStatus,
)


class SkillCreationError(Exception):
    """Base exception for canonical skill creation failures."""


class SkillIdentityAlreadyExistsError(SkillCreationError):
    """Raised when the requested skill identity and version already exist."""


class CanonicalSkillCreationService:
    def __init__(self, repository: CanonicalSkillRepository):
        self.repository = repository

    def create_candidate(
        self,
        name: str,
        slug: str,
        domain: SkillDomain,
        version: str,
        description: str,
        content: str,
        author: str,
        skill_id: UUID | None = None,
        tags: list[str] | None = None,
        triggers: list[str] | None = None,
        artifacts: list[SkillArtifact] | None = None,
        dependencies: list[SkillDependency] | None = None,
        embedding_model: str | None = None,
        embedding_dimension: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SkillManifest:
        if not isinstance(name, str) or not name.strip():
            raise SkillCreationError("name must not be empty or whitespace")
        if not isinstance(author, str) or not author.strip():
            raise SkillCreationError("author must not be empty or whitespace")
        if not isinstance(content, str) or not content.strip():
            raise SkillCreationError("content must not be empty or whitespace")

        resolved_skill_id = skill_id or uuid4()
        now = datetime.now(timezone.utc)
        try:
            manifest = SkillManifest.model_validate(
                {
                    "skill_id": resolved_skill_id,
                    "name": name.strip(),
                    "slug": slug,
                    "domain": domain,
                    "version": version,
                    "status": SkillStatus.CANDIDATE,
                    "description": description,
                    "content": content,
                    "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "author": author.strip(),
                    "approver": None,
                    "created_at": now,
                    "updated_at": now,
                    "published_at": None,
                    "supersedes": None,
                    "tags": list(tags) if tags is not None else [],
                    "triggers": list(triggers) if triggers is not None else [],
                    "artifacts": list(artifacts) if artifacts is not None else [],
                    "dependencies": (
                        list(dependencies) if dependencies is not None else []
                    ),
                    "test_evidence": [],
                    "usage_count": 0,
                    "embedding_model": embedding_model,
                    "embedding_dimension": embedding_dimension,
                    "metadata": dict(metadata) if metadata is not None else {},
                }
            )
        except ValidationError as exc:
            raise SkillCreationError(
                "The candidate does not satisfy the skill manifest contract"
            ) from exc

        if self.repository.exists(resolved_skill_id, version):
            raise SkillIdentityAlreadyExistsError(
                "The skill identity and version already exist"
            )

        try:
            self.repository.save_revision(manifest)
        except SkillRevisionAlreadyExistsError as exc:
            raise SkillIdentityAlreadyExistsError(
                "The skill identity and version already exist"
            ) from exc

        return self.repository.get_revision(resolved_skill_id, version)
