from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from schemas.skill_manifest_schema import (
    ArtifactType,
    SkillArtifact,
    SkillDomain,
    SkillManifest,
    SkillStatus,
    SkillTestEvidence,
)


NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
CONTENT_HASH = "a" * 64
SKILL_ID = UUID("2b19a1d2-80c1-4c91-a121-9bd1d920c421")


def successful_evidence() -> dict[str, object]:
    return {
        "test_id": "test-skill-basic",
        "test_name": "Basic skill validation",
        "passed": True,
        "executed_at": NOW,
        "test_version": "1.0.0",
        "dataset_version": "dataset-1",
        "details": "Validated against the approved fixture.",
    }


def manifest_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "skill_id": SKILL_ID,
        "name": "Example skill",
        "slug": "example-skill",
        "domain": SkillDomain.GENERAL,
        "version": "1.2.3",
        "status": SkillStatus.CANDIDATE,
        "description": "A candidate skill for validation.",
        "content": "Use the supplied context to perform the task.",
        "content_hash": CONTENT_HASH,
        "author": "local-author",
        "created_at": NOW,
        "updated_at": NOW,
        "tags": ["example"],
        "triggers": ["example request"],
        "artifacts": [],
        "dependencies": [],
        "test_evidence": [],
        "usage_count": 0,
        "metadata": {"source": "unit-test"},
    }
    data.update(overrides)
    return data


def published_data(**overrides: object) -> dict[str, object]:
    data = manifest_data(
        status=SkillStatus.PUBLISHED,
        approver="human-approver",
        published_at=NOW,
        test_evidence=[successful_evidence()],
    )
    data.update(overrides)
    return data


def test_candidate_manifest_is_valid() -> None:
    manifest = SkillManifest.model_validate(manifest_data())

    assert manifest.status is SkillStatus.CANDIDATE
    assert manifest.skill_id == SKILL_ID


def test_published_manifest_is_valid() -> None:
    manifest = SkillManifest.model_validate(published_data())

    assert manifest.status is SkillStatus.PUBLISHED
    assert manifest.approver == "human-approver"


def test_approved_manifest_with_approver_and_successful_test_is_valid() -> None:
    manifest = SkillManifest.model_validate(
        manifest_data(
            status=SkillStatus.APPROVED,
            approver="human-approver",
            test_evidence=[successful_evidence()],
        )
    )

    assert manifest.status is SkillStatus.APPROVED


@pytest.mark.parametrize("slug", ["", "Upper", "two words", "accent-é", "bad_slug"])
def test_rejects_invalid_slug(slug: str) -> None:
    with pytest.raises(ValidationError):
        SkillManifest.model_validate(manifest_data(slug=slug))


@pytest.mark.parametrize("version", ["1.2", "v1.2.3", "01.2.3", "1.2.3-01"])
def test_rejects_non_semver_version(version: str) -> None:
    with pytest.raises(ValidationError):
        SkillManifest.model_validate(manifest_data(version=version))


@pytest.mark.parametrize("content_hash", ["", "g" * 64, "a" * 63, "a" * 65])
def test_rejects_invalid_manifest_hash(content_hash: str) -> None:
    with pytest.raises(ValidationError):
        SkillManifest.model_validate(manifest_data(content_hash=content_hash))


def test_rejects_quality_score_above_100() -> None:
    with pytest.raises(ValidationError):
        SkillManifest.model_validate(manifest_data(quality_score=100.1))


def test_rejects_negative_usage_count() -> None:
    with pytest.raises(ValidationError):
        SkillManifest.model_validate(manifest_data(usage_count=-1))


def test_rejects_published_without_approver() -> None:
    with pytest.raises(ValidationError):
        SkillManifest.model_validate(published_data(approver=None))


def test_rejects_published_without_successful_test() -> None:
    with pytest.raises(ValidationError):
        SkillManifest.model_validate(
            published_data(test_evidence=[{**successful_evidence(), "passed": False}])
        )


def test_rejects_published_without_published_at() -> None:
    with pytest.raises(ValidationError):
        SkillManifest.model_validate(published_data(published_at=None))


def test_rejects_candidate_with_published_at() -> None:
    with pytest.raises(ValidationError):
        SkillManifest.model_validate(manifest_data(published_at=NOW))


def test_rejects_embedding_dimension_without_model() -> None:
    with pytest.raises(ValidationError):
        SkillManifest.model_validate(manifest_data(embedding_dimension=768))


def test_rejects_embedding_model_without_dimension() -> None:
    with pytest.raises(ValidationError):
        SkillManifest.model_validate(manifest_data(embedding_model="model-v1"))


def test_json_serialization_round_trip_preserves_data() -> None:
    manifest = SkillManifest.model_validate(
        published_data(
            artifacts=[
                {
                    "artifact_type": ArtifactType.PYTHON,
                    "relative_path": "versions/1.2.3/implementation.py",
                    "content_hash": CONTENT_HASH,
                    "entrypoint": "run",
                }
            ],
            dependencies=[
                {
                    "dependency_id": "base-skill",
                    "dependency_type": "skill",
                    "version_constraint": "^1.0.0",
                    "required": True,
                }
            ],
        )
    )

    reloaded = SkillManifest.model_validate_json(manifest.model_dump_json())

    assert reloaded == manifest


def test_invoice_domain_accepts_json_artifact() -> None:
    manifest = SkillManifest.model_validate(
        manifest_data(
            domain=SkillDomain.INVOICE,
            artifacts=[
                SkillArtifact(
                    artifact_type=ArtifactType.JSON,
                    relative_path="invoice/rules.json",
                    content_hash=CONTENT_HASH,
                )
            ],
        )
    )

    assert manifest.domain is SkillDomain.INVOICE
    assert manifest.artifacts[0].artifact_type is ArtifactType.JSON


def test_rejects_artifact_with_invalid_hash() -> None:
    with pytest.raises(ValidationError):
        SkillArtifact(
            artifact_type=ArtifactType.JSON,
            relative_path="rules.json",
            content_hash="not-a-sha256",
        )


def test_rejects_updated_at_before_created_at() -> None:
    earlier = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

    with pytest.raises(ValidationError):
        SkillManifest.model_validate(manifest_data(updated_at=earlier))


def test_approved_requires_approver_and_successful_test() -> None:
    with pytest.raises(ValidationError):
        SkillManifest.model_validate(manifest_data(status=SkillStatus.APPROVED))


def test_rejects_non_positive_embedding_dimension() -> None:
    with pytest.raises(ValidationError):
        SkillManifest.model_validate(
            manifest_data(embedding_model="model-v1", embedding_dimension=0)
        )


def test_semver_accepts_prerelease_and_build_metadata() -> None:
    manifest = SkillManifest.model_validate(
        manifest_data(version="1.2.3-rc.1+build.7")
    )

    assert manifest.version == "1.2.3-rc.1+build.7"


@pytest.mark.parametrize(
    "status,overrides",
    [
        (SkillStatus.APPROVED, {"approver": "human-approver", "test_evidence": [successful_evidence()]}),
        (SkillStatus.TESTING, {}),
        (SkillStatus.REJECTED, {}),
        (SkillStatus.RETIRED, {}),
    ],
)
def test_rejects_published_at_for_non_published_statuses(
    status: SkillStatus, overrides: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        SkillManifest.model_validate(
            manifest_data(status=status, published_at=NOW, **overrides)
        )


def test_rejects_published_at_before_created_at() -> None:
    earlier = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

    with pytest.raises(ValidationError):
        SkillManifest.model_validate(published_data(published_at=earlier))


def test_rejects_published_at_before_updated_at() -> None:
    later_update = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)

    with pytest.raises(ValidationError):
        SkillManifest.model_validate(published_data(updated_at=later_update))


def test_rejects_supersedes_equal_to_skill_id() -> None:
    with pytest.raises(ValidationError):
        SkillManifest.model_validate(manifest_data(supersedes=SKILL_ID))


def test_normalizes_manifest_hash_to_lowercase() -> None:
    manifest = SkillManifest.model_validate(manifest_data(content_hash="A" * 64))

    assert manifest.content_hash == "a" * 64


def test_normalizes_artifact_hash_to_lowercase() -> None:
    artifact = SkillArtifact(
        artifact_type=ArtifactType.JSON,
        relative_path="rules.json",
        content_hash="B" * 64,
    )

    assert artifact.content_hash == "b" * 64


def test_rejects_unicode_digits_in_semver() -> None:
    with pytest.raises(ValidationError):
        SkillManifest.model_validate(manifest_data(version="１.2.3"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", ""),
        ("name", "   "),
        ("description", ""),
        ("description", "   "),
        ("content", ""),
        ("content", "   "),
        ("author", ""),
        ("author", "   "),
    ],
)
def test_rejects_empty_or_whitespace_manifest_text(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        SkillManifest.model_validate(manifest_data(**{field: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("test_id", ""),
        ("test_name", ""),
        ("test_version", ""),
        ("dependency_id", ""),
        ("dependency_type", ""),
    ],
)
def test_rejects_empty_evidence_and_dependency_identifiers(
    field: str, value: str
) -> None:
    evidence = successful_evidence()
    dependency: dict[str, object] = {
        "dependency_id": "base-skill",
        "dependency_type": "skill",
        "required": True,
    }
    if field in evidence:
        evidence[field] = value
        data = manifest_data(test_evidence=[evidence])
    else:
        dependency[field] = value
        data = manifest_data(dependencies=[dependency])

    with pytest.raises(ValidationError):
        SkillManifest.model_validate(data)


@pytest.mark.parametrize(
    "relative_path",
    [
        r"C:\skills\rules.json",
        "/skills/rules.json",
        "skills/../rules.json",
    ],
)
def test_rejects_absolute_or_traversing_artifact_path(relative_path: str) -> None:
    with pytest.raises(ValidationError):
        SkillArtifact(
            artifact_type=ArtifactType.JSON,
            relative_path=relative_path,
            content_hash=CONTENT_HASH,
        )


def test_rejects_additional_manifest_field() -> None:
    with pytest.raises(ValidationError):
        SkillManifest.model_validate(manifest_data(unrecognized_field="value"))


def test_validated_manifest_is_frozen() -> None:
    manifest = SkillManifest.model_validate(manifest_data())

    with pytest.raises(ValidationError):
        manifest.name = "Changed name"  # type: ignore[misc]


@pytest.mark.parametrize("quality_score", [0, 100])
def test_accepts_quality_score_boundaries(quality_score: int) -> None:
    manifest = SkillManifest.model_validate(
        manifest_data(quality_score=quality_score)
    )

    assert manifest.quality_score == quality_score


def test_rejects_empty_embedding_model() -> None:
    with pytest.raises(ValidationError):
        SkillManifest.model_validate(
            manifest_data(embedding_model="", embedding_dimension=768)
        )


def test_published_accepts_successful_evidence_among_multiple_results() -> None:
    failed_evidence = {**successful_evidence(), "test_id": "failed-test", "passed": False}
    manifest = SkillManifest.model_validate(
        published_data(test_evidence=[failed_evidence, successful_evidence()])
    )

    assert manifest.status is SkillStatus.PUBLISHED
