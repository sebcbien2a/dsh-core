"""Isolated API routes for the local canonical skill registry.

These routes have no authentication yet and must not be exposed to a network
until authentication and authorization are implemented.
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from repositories.canonical_skill_repository import (
    CanonicalSkillRepository,
    SkillRegistryCorruptionError,
)
from schemas.skill_manifest_schema import (
    SkillArtifact,
    SkillDependency,
    SkillDomain,
    SkillManifest,
    SkillStatus,
    SkillTestEvidence,
)
from services.canonical_skill_creation_service import (
    CanonicalSkillCreationService,
    SkillCreationError,
    SkillIdentityAlreadyExistsError,
)
from services.skill_lifecycle_service import (
    InvalidSkillTransitionError,
    InvalidSkillVersionError,
    SkillApprovalRequirementsError,
    SkillLifecycleError,
    SkillLifecycleService,
    SkillPublicationRequirementsError,
)


router = APIRouter(
    prefix="/api/canonical-skills",
    tags=["canonical-skills"],
)


class SkillTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_version: str
    target_status: SkillStatus
    actor: str
    approver: str | None = None
    test_evidence: list[SkillTestEvidence] | None = None
    quality_score: float | None = None
    published_at: datetime | None = None


class SkillCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: UUID | None = None
    name: str
    slug: str
    domain: SkillDomain
    version: str = "0.1.0"
    description: str
    content: str
    author: str
    tags: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    artifacts: list[SkillArtifact] = Field(default_factory=list)
    dependencies: list[SkillDependency] = Field(default_factory=list)
    embedding_model: str | None = None
    embedding_dimension: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def get_repository() -> CanonicalSkillRepository:
    root_path = Path(os.environ.get("DSH_CANONICAL_SKILLS_ROOT", "skills/catalog"))
    return CanonicalSkillRepository(root_path)


RepositoryDependency = Annotated[CanonicalSkillRepository, Depends(get_repository)]


def _raise_registry_corruption() -> None:
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Canonical skill registry is corrupted",
    )


def _raise_missing_resource() -> None:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Canonical skill resource not found",
    )


def _raise_invalid_version() -> None:
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="Invalid skill version",
    )


@router.post(
    "",
    response_model=SkillManifest,
    status_code=status.HTTP_201_CREATED,
)
def create_candidate_skill(
    request: SkillCandidateRequest,
    repository: RepositoryDependency,
) -> SkillManifest:
    creation = CanonicalSkillCreationService(repository)
    try:
        return creation.create_candidate(
            skill_id=request.skill_id,
            name=request.name,
            slug=request.slug,
            domain=request.domain,
            version=request.version,
            description=request.description,
            content=request.content,
            author=request.author,
            tags=request.tags,
            triggers=request.triggers,
            artifacts=request.artifacts,
            dependencies=request.dependencies,
            embedding_model=request.embedding_model,
            embedding_dimension=request.embedding_dimension,
            metadata=request.metadata,
        )
    except SkillIdentityAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Canonical skill identity and version already exist",
        ) from exc
    except SkillCreationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Canonical skill candidate is invalid",
        ) from exc
    except SkillRegistryCorruptionError:
        _raise_registry_corruption()


@router.get("/{skill_id}/versions")
def list_skill_versions(
    skill_id: UUID,
    repository: RepositoryDependency,
) -> dict[str, object]:
    try:
        versions = repository.list_versions(skill_id)
    except FileNotFoundError:
        _raise_missing_resource()
    except SkillRegistryCorruptionError:
        _raise_registry_corruption()
    return {"skill_id": str(skill_id), "versions": versions}


@router.get("/{skill_id}/versions/{version}", response_model=SkillManifest)
def get_skill_revision(
    skill_id: UUID,
    version: str,
    repository: RepositoryDependency,
) -> SkillManifest:
    try:
        return repository.get_revision(skill_id, version)
    except FileNotFoundError:
        _raise_missing_resource()
    except ValueError:
        _raise_invalid_version()
    except SkillRegistryCorruptionError:
        _raise_registry_corruption()


@router.get("/{skill_id}/latest", response_model=SkillManifest)
def get_latest_skill_revision(
    skill_id: UUID,
    repository: RepositoryDependency,
) -> SkillManifest:
    try:
        return repository.get_latest(skill_id)
    except FileNotFoundError:
        _raise_missing_resource()
    except SkillRegistryCorruptionError:
        _raise_registry_corruption()


@router.get("/{skill_id}/versions/{version}/verify")
def verify_skill_revision(
    skill_id: UUID,
    version: str,
    repository: RepositoryDependency,
) -> dict[str, object]:
    try:
        repository.get_revision(skill_id, version)
        valid = repository.verify_revision(skill_id, version)
    except FileNotFoundError:
        _raise_missing_resource()
    except ValueError:
        _raise_invalid_version()
    except SkillRegistryCorruptionError:
        _raise_registry_corruption()
    if valid:
        return {"skill_id": str(skill_id), "version": version, "valid": True}
    return {"valid": False}


@router.post(
    "/{skill_id}/versions/{current_version}/transition",
    response_model=SkillManifest,
    status_code=status.HTTP_201_CREATED,
)
def transition_skill_revision(
    skill_id: UUID,
    current_version: str,
    request: SkillTransitionRequest,
    repository: RepositoryDependency,
) -> SkillManifest:
    lifecycle = SkillLifecycleService(repository)
    try:
        return lifecycle.transition(
            skill_id=skill_id,
            current_version=current_version,
            new_version=request.new_version,
            target_status=request.target_status,
            actor=request.actor,
            approver=request.approver,
            test_evidence=request.test_evidence,
            quality_score=request.quality_score,
            published_at=request.published_at,
        )
    except FileNotFoundError:
        _raise_missing_resource()
    except ValueError:
        _raise_invalid_version()
    except InvalidSkillTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Invalid skill status transition",
        ) from exc
    except InvalidSkillVersionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Invalid or existing skill version",
        ) from exc
    except SkillApprovalRequirementsError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Approval requirements are not satisfied",
        ) from exc
    except SkillPublicationRequirementsError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Publication requirements are not satisfied",
        ) from exc
    except SkillLifecycleError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Skill lifecycle request is invalid",
        ) from exc
    except SkillRegistryCorruptionError:
        _raise_registry_corruption()
