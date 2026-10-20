"""Canonical local data contract for individual skill revisions.

Repository/service code is responsible for uniqueness, lifecycle transitions,
storage immutability, and validating referenced artifact contents.
"""

import re
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)"
    r"(?:-((?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_SLUG_PATTERN = re.compile(r"^[a-z0-9-]+$")


class SkillStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    TESTING = "TESTING"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"


class SkillDomain(str, Enum):
    GENERAL = "GENERAL"
    RESEARCH = "RESEARCH"
    ACCOUNTING = "ACCOUNTING"
    INVOICE = "INVOICE"
    BANKING = "BANKING"
    DOCUMENT = "DOCUMENT"
    DEVELOPMENT = "DEVELOPMENT"


class ArtifactType(str, Enum):
    PYTHON = "PYTHON"
    JSON = "JSON"
    PROMPT = "PROMPT"
    WORKFLOW = "WORKFLOW"
    EXTERNAL = "EXTERNAL"


class SkillArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_type: ArtifactType
    relative_path: str
    content_hash: str
    entrypoint: str | None = None

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("relative_path must not be empty")
        if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
            raise ValueError("relative_path must be relative")
        if ".." in value.replace("\\", "/").split("/"):
            raise ValueError("relative_path must not traverse parent directories")
        return value

    @field_validator("content_hash")
    @classmethod
    def validate_content_hash(cls, value: str) -> str:
        if not _SHA256_PATTERN.fullmatch(value):
            raise ValueError("content_hash must be exactly 64 hexadecimal characters")
        return value.lower()


class SkillTestEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    test_id: str
    test_name: str
    passed: bool
    executed_at: datetime
    test_version: str
    dataset_version: str | None = None
    details: str | None = None

    @field_validator("test_id", "test_name", "test_version")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value:
            raise ValueError("value must not be empty")
        return value


class SkillDependency(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dependency_id: str
    dependency_type: str
    version_constraint: str | None = None
    required: bool

    @field_validator("dependency_id", "dependency_type")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value:
            raise ValueError("value must not be empty")
        return value


class SkillManifest(BaseModel):
    """Validated data for one skill revision; persistence rules live elsewhere."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: UUID
    name: str
    slug: str
    domain: SkillDomain
    version: str
    status: SkillStatus
    description: str
    content: str
    content_hash: str
    author: str
    approver: str | None = None
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None = None
    supersedes: UUID | None = None
    tags: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    artifacts: list[SkillArtifact] = Field(default_factory=list)
    dependencies: list[SkillDependency] = Field(default_factory=list)
    test_evidence: list[SkillTestEvidence] = Field(default_factory=list)
    usage_count: int = Field(default=0, ge=0)
    quality_score: float | None = Field(default=None, ge=0, le=100)
    embedding_model: str | None = None
    embedding_dimension: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "description", "content", "author")
    @classmethod
    def validate_non_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty or whitespace")
        return value

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        if not value or not _SLUG_PATTERN.fullmatch(value):
            raise ValueError("slug must contain only lowercase letters, digits, and hyphens")
        return value

    @field_validator("version")
    @classmethod
    def validate_semver(cls, value: str) -> str:
        if not _SEMVER_PATTERN.fullmatch(value):
            raise ValueError("version must be strict SemVer")
        return value

    @field_validator("content_hash")
    @classmethod
    def validate_content_hash(cls, value: str) -> str:
        if not _SHA256_PATTERN.fullmatch(value):
            raise ValueError("content_hash must be exactly 64 hexadecimal characters")
        return value.lower()

    @field_validator("embedding_model")
    @classmethod
    def validate_embedding_model(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("embedding_model must not be empty")
        return value

    @field_validator("embedding_dimension")
    @classmethod
    def validate_embedding_dimension(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("embedding_dimension must be strictly positive")
        return value

    @model_validator(mode="after")
    def validate_lifecycle_and_timestamps(self) -> "SkillManifest":
        try:
            if self.updated_at < self.created_at:
                raise ValueError("updated_at must be greater than or equal to created_at")
        except TypeError as exc:
            raise ValueError("created_at and updated_at must use compatible time zones") from exc

        if self.status is SkillStatus.PUBLISHED:
            if self.published_at is None:
                raise ValueError("published_at is required for PUBLISHED skills")
        elif self.status in {
            SkillStatus.CANDIDATE,
            SkillStatus.TESTING,
            SkillStatus.APPROVED,
            SkillStatus.REJECTED,
            SkillStatus.RETIRED,
        } and self.published_at is not None:
            raise ValueError(f"published_at is not allowed for {self.status.value} skills")

        if self.supersedes == self.skill_id:
            raise ValueError("supersedes must be different from skill_id")

        if self.status in {SkillStatus.APPROVED, SkillStatus.PUBLISHED}:
            if not self.approver:
                raise ValueError(f"approver is required for {self.status.value} skills")
            if not any(evidence.passed for evidence in self.test_evidence):
                raise ValueError(
                    f"at least one successful test is required for {self.status.value} skills"
                )

        if self.published_at is not None:
            try:
                if self.published_at < self.created_at:
                    raise ValueError("published_at must not be before created_at")
                if self.published_at < self.updated_at:
                    raise ValueError("published_at must not be before updated_at")
            except TypeError as exc:
                raise ValueError(
                    "created_at, updated_at, and published_at must use compatible time zones"
                ) from exc

        if (self.embedding_model is None) != (self.embedding_dimension is None):
            raise ValueError(
                "embedding_model and embedding_dimension must be provided together"
            )

        return self
