import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from repositories.canonical_skill_repository import CanonicalSkillRepository
from schemas.skill_manifest_schema import (
    ArtifactType,
    SkillArtifact,
    SkillDomain,
    SkillManifest,
    SkillRevisionReference,
    SkillStatus,
    SkillTestEvidence,
)
from services.skill_lifecycle_service import (
    InvalidSkillTransitionError,
    InvalidSkillVersionError,
    SkillApprovalRequirementsError,
    SkillLifecycleError,
    SkillLifecycleService,
    SkillPublicationRequirementsError,
)


NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def evidence(test_id: str = "passing-test", passed: bool = True) -> SkillTestEvidence:
    return SkillTestEvidence(
        test_id=test_id,
        test_name="Lifecycle test",
        passed=passed,
        executed_at=NOW,
        test_version="1.0.0",
    )


def make_manifest(
    status: SkillStatus = SkillStatus.CANDIDATE,
    skill_id: UUID | None = None,
    version: str = "1.0.0",
) -> SkillManifest:
    content = "canonical skill content"
    data: dict[str, object] = {
        "skill_id": skill_id or UUID("2b19a1d2-80c1-4c91-a121-9bd1d920c421"),
        "name": "Lifecycle skill",
        "slug": "lifecycle-skill",
        "domain": SkillDomain.GENERAL,
        "version": version,
        "status": status,
        "description": "A skill for lifecycle tests.",
        "content": content,
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "author": "initial-author",
        "created_at": NOW,
        "updated_at": NOW,
        "artifacts": [
            SkillArtifact(
                artifact_type=ArtifactType.JSON,
                relative_path="rules.json",
                content_hash="a" * 64,
            )
        ],
        "metadata": {"source": "fixture", "nested": {"retained": True}},
        "quality_score": 73,
    }
    if status in {SkillStatus.APPROVED, SkillStatus.PUBLISHED}:
        data.update(
            {
                "approver": "prior-approver",
                "test_evidence": [evidence()],
            }
        )
    if status is SkillStatus.PUBLISHED:
        data["published_at"] = NOW
    return SkillManifest.model_validate(data)


def setup_service(
    tmp_path: Path,
    source_status: SkillStatus = SkillStatus.CANDIDATE,
    source_version: str = "1.0.0",
) -> tuple[CanonicalSkillRepository, SkillLifecycleService, SkillManifest]:
    repository = CanonicalSkillRepository(tmp_path)
    source = make_manifest(status=source_status, version=source_version)
    repository.save_revision(source)
    return repository, SkillLifecycleService(repository), source


def transition(
    service: SkillLifecycleService,
    source: SkillManifest,
    target: SkillStatus,
    new_version: str = "1.1.0",
    **kwargs: object,
) -> SkillManifest:
    return service.transition(
        skill_id=source.skill_id,
        current_version=source.version,
        new_version=new_version,
        target_status=target,
        actor="  lifecycle-actor  ",
        **kwargs,
    )


@pytest.mark.parametrize(
    ("source_status", "target_status", "kwargs"),
    [
        (SkillStatus.CANDIDATE, SkillStatus.TESTING, {}),
        (SkillStatus.CANDIDATE, SkillStatus.REJECTED, {}),
        (
            SkillStatus.TESTING,
            SkillStatus.APPROVED,
            {"approver": "reviewer", "test_evidence": [evidence()]},
        ),
        (SkillStatus.TESTING, SkillStatus.REJECTED, {}),
        (
            SkillStatus.APPROVED,
            SkillStatus.PUBLISHED,
            {
                "approver": "publisher",
                "test_evidence": [evidence()],
                "published_at": NOW,
            },
        ),
        (SkillStatus.APPROVED, SkillStatus.REJECTED, {}),
        (SkillStatus.PUBLISHED, SkillStatus.RETIRED, {}),
    ],
)
def test_all_authorized_transitions(
    tmp_path: Path,
    source_status: SkillStatus,
    target_status: SkillStatus,
    kwargs: dict[str, object],
) -> None:
    repository, service, source = setup_service(tmp_path, source_status)

    result = transition(service, source, target_status, **kwargs)

    assert result.status is target_status
    assert result.version == "1.1.0"


@pytest.mark.parametrize(
    ("source_status", "target_status"),
    [
        (SkillStatus.CANDIDATE, SkillStatus.PUBLISHED),
        (SkillStatus.TESTING, SkillStatus.PUBLISHED),
        (SkillStatus.REJECTED, SkillStatus.TESTING),
        (SkillStatus.RETIRED, SkillStatus.PUBLISHED),
    ],
)
def test_rejects_unauthorized_transitions(
    tmp_path: Path, source_status: SkillStatus, target_status: SkillStatus
) -> None:
    _, service, source = setup_service(tmp_path, source_status)

    with pytest.raises(InvalidSkillTransitionError):
        transition(
            service,
            source,
            target_status,
            approver="reviewer",
            test_evidence=[evidence()],
            published_at=NOW if target_status is SkillStatus.PUBLISHED else None,
        )


@pytest.mark.parametrize("new_version", ["0.9.0", "1.0.0"])
def test_rejects_new_version_not_greater_than_current(
    tmp_path: Path, new_version: str
) -> None:
    _, service, source = setup_service(tmp_path)

    with pytest.raises(InvalidSkillVersionError):
        transition(service, source, SkillStatus.TESTING, new_version=new_version)


def test_rejects_existing_new_version(tmp_path: Path) -> None:
    repository, service, source = setup_service(tmp_path)
    repository.save_revision(make_manifest(version="1.2.0"))

    with pytest.raises(InvalidSkillVersionError):
        transition(service, source, SkillStatus.TESTING, new_version="1.2.0")


@pytest.mark.parametrize("actor", ["", "   "])
def test_rejects_empty_actor(tmp_path: Path, actor: str) -> None:
    _, service, source = setup_service(tmp_path)

    with pytest.raises(SkillLifecycleError):
        service.transition(
            source.skill_id,
            source.version,
            "1.1.0",
            SkillStatus.TESTING,
            actor,
        )


def test_rejects_approved_without_approver(tmp_path: Path) -> None:
    _, service, source = setup_service(tmp_path, SkillStatus.TESTING)

    with pytest.raises(SkillApprovalRequirementsError):
        transition(service, source, SkillStatus.APPROVED, test_evidence=[evidence()])


def test_rejects_approved_without_successful_evidence(tmp_path: Path) -> None:
    _, service, source = setup_service(tmp_path, SkillStatus.TESTING)

    with pytest.raises(SkillApprovalRequirementsError):
        transition(
            service,
            source,
            SkillStatus.APPROVED,
            approver="reviewer",
            test_evidence=[evidence(passed=False)],
        )


def test_rejects_published_without_published_at(tmp_path: Path) -> None:
    _, service, source = setup_service(tmp_path, SkillStatus.APPROVED)

    with pytest.raises(SkillPublicationRequirementsError):
        transition(
            service,
            source,
            SkillStatus.PUBLISHED,
            approver="publisher",
            test_evidence=[evidence()],
        )


@pytest.mark.parametrize(
    "published_at",
    [
        datetime(2026, 10, 21, 12, 0),
        datetime(2026, 10, 21, 12, 0, tzinfo=timezone(timedelta(hours=1))),
    ],
)
def test_rejects_published_with_non_utc_datetime(
    tmp_path: Path, published_at: datetime
) -> None:
    _, service, source = setup_service(tmp_path, SkillStatus.APPROVED)

    with pytest.raises(SkillPublicationRequirementsError):
        transition(
            service,
            source,
            SkillStatus.PUBLISHED,
            approver="publisher",
            test_evidence=[evidence()],
            published_at=published_at,
        )


def test_transition_preserves_content_and_artifacts_and_recalculates_hash(
    tmp_path: Path,
) -> None:
    _, service, source = setup_service(tmp_path)

    result = transition(service, source, SkillStatus.TESTING)

    assert result.content == source.content
    assert result.artifacts == source.artifacts
    assert result.content_hash == hashlib.sha256(source.content.encode("utf-8")).hexdigest()


def test_transition_leaves_source_revision_unchanged(tmp_path: Path) -> None:
    repository, service, source = setup_service(tmp_path)
    source_path = (
        tmp_path / str(source.skill_id) / "versions" / source.version / "skill.json"
    )
    original_bytes = source_path.read_bytes()
    original_source = repository.get_revision(source.skill_id, source.version)

    transition(service, source, SkillStatus.TESTING)

    assert source_path.read_bytes() == original_bytes
    assert repository.get_revision(source.skill_id, source.version) == original_source


def test_transition_resets_usage_count_and_preserves_metadata(tmp_path: Path) -> None:
    repository, service, source = setup_service(tmp_path)
    service.transition(
        source.skill_id,
        source.version,
        "1.1.0",
        SkillStatus.TESTING,
        "actor",
    )
    first_revision = repository.get_revision(source.skill_id, "1.1.0")
    changed = first_revision.model_copy(
        update={"usage_count": 9, "metadata": {**first_revision.metadata, "custom": 42}}
    )
    revision_path = tmp_path / str(source.skill_id) / "versions" / "1.1.0" / "skill.json"
    revision_path.write_text(changed.model_dump_json(indent=2), encoding="utf-8")

    result = service.transition(
        source.skill_id,
        "1.1.0",
        "1.2.0",
        SkillStatus.REJECTED,
        "actor",
    )

    assert result.usage_count == 0
    assert result.metadata["source"] == "fixture"
    assert result.metadata["nested"] == {"retained": True}
    assert result.metadata["custom"] == 42


def test_transition_metadata_and_supersedes_are_exact(tmp_path: Path) -> None:
    _, service, source = setup_service(tmp_path)

    result = transition(service, source, SkillStatus.TESTING)

    assert result.metadata["previous_version"] == source.version
    assert result.metadata["transition_actor"] == "lifecycle-actor"
    assert result.metadata["transition_from"] == "CANDIDATE"
    assert result.metadata["transition_to"] == "TESTING"
    assert result.supersedes == SkillRevisionReference(
        skill_id=source.skill_id,
        version=source.version,
    )


def test_retired_metadata_keeps_published_version_reference(tmp_path: Path) -> None:
    _, service, source = setup_service(tmp_path, SkillStatus.PUBLISHED)

    result = transition(service, source, SkillStatus.RETIRED)

    assert result.published_at is None
    assert result.metadata["retired_published_version"] == source.version


def test_supplied_evidence_replaces_source_evidence_and_none_preserves_it(
    tmp_path: Path,
) -> None:
    repository, service, source = setup_service(tmp_path, SkillStatus.TESTING)
    source_path = tmp_path / str(source.skill_id) / "versions" / source.version / "skill.json"
    initial = source.model_copy(update={"test_evidence": [evidence("old-test")]})
    source_path.write_text(initial.model_dump_json(indent=2), encoding="utf-8")
    replacement = evidence("new-test")

    replaced = service.transition(
        source.skill_id,
        source.version,
        "1.1.0",
        SkillStatus.APPROVED,
        "actor",
        approver=" reviewer ",
        test_evidence=[replacement],
    )
    preserved = service.transition(
        source.skill_id,
        "1.1.0",
        "1.2.0",
        SkillStatus.REJECTED,
        "actor",
        test_evidence=None,
    )

    assert [item.test_id for item in replaced.test_evidence] == ["new-test"]
    assert [item.test_id for item in preserved.test_evidence] == ["new-test"]


def test_quality_score_override_and_none_preserve_source(tmp_path: Path) -> None:
    repository, service, source = setup_service(tmp_path, SkillStatus.TESTING)
    first = service.transition(
        source.skill_id,
        source.version,
        "1.1.0",
        SkillStatus.APPROVED,
        "actor",
        approver="reviewer",
        test_evidence=[evidence()],
        quality_score=88,
    )
    second = service.transition(
        source.skill_id,
        "1.1.0",
        "1.2.0",
        SkillStatus.PUBLISHED,
        "actor",
        approver="publisher",
        published_at=NOW,
        quality_score=None,
    )

    assert first.quality_score == 88
    assert second.quality_score == 88


def test_all_versions_remain_readable_and_files_stay_under_tmp_path(
    tmp_path: Path,
) -> None:
    repository, service, source = setup_service(tmp_path)
    result = transition(service, source, SkillStatus.TESTING)

    assert repository.get_revision(source.skill_id, source.version) == source
    assert repository.get_revision(source.skill_id, result.version) == result
    files = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert files
    assert all(path.resolve().is_relative_to(tmp_path.resolve()) for path in files)


def test_actor_and_approver_are_normalized(tmp_path: Path) -> None:
    _, service, source = setup_service(tmp_path, SkillStatus.TESTING)

    result = service.transition(
        source.skill_id,
        source.version,
        "1.1.0",
        SkillStatus.APPROVED,
        " actor ",
        approver=" reviewer ",
        test_evidence=[evidence()],
    )

    assert result.author == "actor"
    assert result.approver == "reviewer"


def test_approved_rejects_published_at(tmp_path: Path) -> None:
    _, service, source = setup_service(tmp_path, SkillStatus.TESTING)

    with pytest.raises(SkillApprovalRequirementsError):
        transition(
            service,
            source,
            SkillStatus.APPROVED,
            approver="reviewer",
            test_evidence=[evidence()],
            published_at=NOW,
        )
