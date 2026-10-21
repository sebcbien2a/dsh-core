import hashlib
from pathlib import Path
from uuid import UUID

import pytest

from repositories.canonical_skill_repository import CanonicalSkillRepository
from schemas.skill_manifest_schema import (
    ArtifactType,
    SkillArtifact,
    SkillDependency,
    SkillDomain,
    SkillStatus,
)
from services.canonical_skill_creation_service import (
    CanonicalSkillCreationService,
    SkillCreationError,
    SkillIdentityAlreadyExistsError,
)


SKILL_ID = UUID("6438cfa0-96c2-45ac-b706-1ebde255c799")


def create_candidate(repository: CanonicalSkillRepository, **overrides):
    values = {
        "name": "  Canonical Skill  ",
        "slug": "canonical-skill",
        "domain": SkillDomain.GENERAL,
        "version": "1.0.0",
        "description": "A candidate skill.",
        "content": "  exact content\n",
        "author": "  creator  ",
    }
    values.update(overrides)
    return CanonicalSkillCreationService(repository).create_candidate(**values)


def test_creates_candidate_with_generated_uuid_and_manifest_defaults(
    tmp_path: Path,
) -> None:
    repository = CanonicalSkillRepository(tmp_path)

    manifest = create_candidate(repository)

    assert isinstance(manifest.skill_id, UUID)
    assert manifest.status is SkillStatus.CANDIDATE
    assert manifest.approver is None
    assert manifest.published_at is None
    assert manifest.supersedes is None
    assert manifest.usage_count == 0
    assert manifest.test_evidence == []
    assert manifest.tags == []
    assert manifest.triggers == []
    assert manifest.artifacts == []
    assert manifest.dependencies == []
    assert manifest.metadata == {}
    assert manifest.version == "1.0.0"


def test_creates_candidate_with_supplied_uuid_and_normalizes_name_author(
    tmp_path: Path,
) -> None:
    manifest = create_candidate(CanonicalSkillRepository(tmp_path), skill_id=SKILL_ID)

    assert manifest.skill_id == SKILL_ID
    assert manifest.name == "Canonical Skill"
    assert manifest.author == "creator"


def test_content_hash_and_utc_timestamps_are_correct(tmp_path: Path) -> None:
    content = "  exact content\n"
    manifest = create_candidate(
        CanonicalSkillRepository(tmp_path),
        content=content,
        skill_id=SKILL_ID,
    )

    assert manifest.content == content
    assert manifest.content_hash == hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert manifest.created_at == manifest.updated_at
    assert manifest.created_at.tzinfo is not None
    assert manifest.created_at.utcoffset().total_seconds() == 0


def test_preserves_artifacts_dependencies_and_embedding_parameters(
    tmp_path: Path,
) -> None:
    artifact = SkillArtifact(
        artifact_type=ArtifactType.PYTHON,
        relative_path="run.py",
        content_hash="a" * 64,
        entrypoint="run",
    )
    dependency = SkillDependency(
        dependency_id="base-skill",
        dependency_type="skill",
        version_constraint="^1.0.0",
        required=True,
    )
    manifest = create_candidate(
        CanonicalSkillRepository(tmp_path),
        skill_id=SKILL_ID,
        artifacts=[artifact],
        dependencies=[dependency],
        embedding_model="test-embedding",
        embedding_dimension=768,
    )

    assert manifest.artifacts == [artifact]
    assert manifest.dependencies == [dependency]
    assert manifest.embedding_model == "test-embedding"
    assert manifest.embedding_dimension == 768


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "  "),
        ("author", ""),
        ("content", "\t\n"),
    ],
)
def test_rejects_blank_required_values(
    tmp_path: Path, field: str, value: str
) -> None:
    with pytest.raises(SkillCreationError):
        create_candidate(CanonicalSkillRepository(tmp_path), **{field: value})


def test_rejects_existing_skill_id_and_version(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)
    first = create_candidate(repository, skill_id=SKILL_ID)

    with pytest.raises(SkillIdentityAlreadyExistsError):
        create_candidate(repository, skill_id=first.skill_id)


def test_candidate_is_saved_and_readable_from_repository(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)

    created = create_candidate(repository, skill_id=SKILL_ID)
    loaded = repository.get_revision(SKILL_ID, "1.0.0")

    assert loaded == created
    assert repository.list_versions(SKILL_ID) == ["1.0.0"]


def test_optional_collections_are_not_shared_between_candidates(
    tmp_path: Path,
) -> None:
    repository = CanonicalSkillRepository(tmp_path)

    first = create_candidate(repository, skill_id=SKILL_ID)
    second = create_candidate(
        repository,
        skill_id=UUID("e4926b7b-e3cb-4930-8c54-536a0aa01856"),
    )

    assert first.tags is not second.tags
    assert first.triggers is not second.triggers
    assert first.artifacts is not second.artifacts
    assert first.dependencies is not second.dependencies
    assert first.metadata is not second.metadata


def test_creation_writes_only_under_tmp_path(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)

    create_candidate(repository, skill_id=SKILL_ID)

    assert all(
        path.resolve().is_relative_to(tmp_path.resolve())
        for path in tmp_path.rglob("*")
    )
