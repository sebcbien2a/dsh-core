import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from repositories.canonical_skill_repository import (
    CanonicalSkillRepository,
    SkillArtifactHashMismatchError,
    SkillContentHashMismatchError,
    SkillRegistryCorruptionError,
    SkillRevisionAlreadyExistsError,
)
from schemas.skill_manifest_schema import (
    ArtifactType,
    SkillArtifact,
    SkillManifest,
    SkillRevisionReference,
    SkillStatus,
)


NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def make_manifest(
    skill_id: UUID | None = None,
    version: str = "1.0.0",
    content: str = "skill content",
    artifacts: list[SkillArtifact] | None = None,
    supersedes: SkillRevisionReference | None = None,
) -> SkillManifest:
    return SkillManifest.model_validate(
        {
            "skill_id": skill_id or UUID("2b19a1d2-80c1-4c91-a121-9bd1d920c421"),
            "name": "Example skill",
            "slug": "example-skill",
            "domain": "GENERAL",
            "version": version,
            "status": SkillStatus.CANDIDATE,
            "description": "A test skill.",
            "content": content,
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "author": "test-author",
            "created_at": NOW,
            "updated_at": NOW,
            "artifacts": artifacts or [],
            "supersedes": supersedes,
        }
    )


def artifact_for(path: str, content: bytes) -> SkillArtifact:
    return SkillArtifact(
        artifact_type=ArtifactType.JSON,
        relative_path=path,
        content_hash=hashlib.sha256(content).hexdigest(),
    )


def skill_directory(root: Path, skill_id: UUID) -> Path:
    return root / str(skill_id)


def revision_file(root: Path, manifest: SkillManifest) -> Path:
    return (
        skill_directory(root, manifest.skill_id)
        / "versions"
        / manifest.version
        / "skill.json"
    )


def test_saves_first_revision_and_creates_canonical_files(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)
    manifest = make_manifest()

    saved_path = repository.save_revision(manifest)

    catalog_path = skill_directory(tmp_path, manifest.skill_id) / "manifest.json"
    assert saved_path == revision_file(tmp_path, manifest)
    assert json.loads(catalog_path.read_text(encoding="utf-8")) == {
        "skill_id": str(manifest.skill_id),
        "name": manifest.name,
        "slug": manifest.slug,
        "domain": manifest.domain.value,
        "latest_version": manifest.version,
        "latest_status": manifest.status.value,
        "updated_at": manifest.updated_at.isoformat(),
        "versions": [manifest.version],
    }
    assert SkillManifest.model_validate_json(saved_path.read_text(encoding="utf-8")) == manifest


def test_reads_exact_revision_and_latest_revision(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)
    manifest = make_manifest()
    repository.save_revision(manifest)

    assert repository.get_revision(manifest.skill_id, manifest.version) == manifest
    assert repository.get_latest(manifest.skill_id) == manifest


def test_saves_and_reads_revision_reference_without_loss(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)
    skill_id = uuid4()
    previous = make_manifest(skill_id=skill_id, version="1.0.0")
    repository.save_revision(previous)
    supersedes = SkillRevisionReference(skill_id=skill_id, version="1.0.0")
    current = make_manifest(
        skill_id=skill_id,
        version="1.1.0",
        supersedes=supersedes,
    )

    repository.save_revision(current)
    reloaded = repository.get_revision(skill_id, "1.1.0")

    assert reloaded.supersedes == supersedes


def test_exists_returns_true_and_false(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)
    manifest = make_manifest()
    repository.save_revision(manifest)

    assert repository.exists(manifest.skill_id, manifest.version)
    assert not repository.exists(manifest.skill_id, "2.0.0")


def test_rejects_duplicate_skill_revision(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)
    manifest = make_manifest()
    repository.save_revision(manifest)

    with pytest.raises(SkillRevisionAlreadyExistsError):
        repository.save_revision(manifest)


def test_rejects_content_hash_mismatch(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)
    manifest = make_manifest().model_copy(update={"content_hash": "0" * 64})

    with pytest.raises(SkillContentHashMismatchError):
        repository.save_revision(manifest)

    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


def test_verifies_intact_revision(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)
    manifest = make_manifest()
    repository.save_revision(manifest)

    assert repository.verify_revision(manifest.skill_id, manifest.version)


def test_verify_revision_detects_altered_skill_json(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)
    manifest = make_manifest()
    repository.save_revision(manifest)
    path = revision_file(tmp_path, manifest)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["content"] = "altered"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert not repository.verify_revision(manifest.skill_id, manifest.version)


def test_saves_multiple_versions_and_semver_sorts_them(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)
    skill_id = uuid4()
    for version in ("1.0.0", "1.9.0", "1.10.0", "2.0.0"):
        repository.save_revision(make_manifest(skill_id=skill_id, version=version))

    assert repository.list_versions(skill_id) == ["1.0.0", "1.9.0", "1.10.0", "2.0.0"]
    assert repository.get_latest(skill_id).version == "2.0.0"


def test_semver_sort_places_prerelease_before_release(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)
    skill_id = uuid4()
    for version in ("1.0.0", "1.0.0-rc.2", "1.0.0-rc.10", "1.0.0-alpha"):
        repository.save_revision(make_manifest(skill_id=skill_id, version=version))

    assert repository.list_versions(skill_id) == [
        "1.0.0-alpha",
        "1.0.0-rc.2",
        "1.0.0-rc.10",
        "1.0.0",
    ]


def test_get_latest_returns_highest_semver_even_if_saved_first(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)
    skill_id = uuid4()
    repository.save_revision(make_manifest(skill_id=skill_id, version="1.9.0"))
    repository.save_revision(make_manifest(skill_id=skill_id, version="1.10.0"))
    repository.save_revision(make_manifest(skill_id=skill_id, version="1.0.0"))

    assert repository.get_latest(skill_id).version == "1.10.0"


def test_skill_ids_are_isolated(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)
    first = make_manifest(skill_id=uuid4())
    second = make_manifest(skill_id=uuid4())
    repository.save_revision(first)
    repository.save_revision(second)

    assert repository.get_latest(first.skill_id) == first
    assert repository.get_latest(second.skill_id) == second


def test_missing_skill_raises_file_not_found(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)

    with pytest.raises(FileNotFoundError):
        repository.get_latest(uuid4())
    with pytest.raises(FileNotFoundError):
        repository.list_versions(uuid4())


def test_missing_version_raises_file_not_found(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)

    with pytest.raises(FileNotFoundError):
        repository.get_revision(uuid4(), "1.0.0")


def test_rejects_corrupt_manifest_json(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)
    skill_id = uuid4()
    skill_directory(tmp_path, skill_id).mkdir(parents=True)
    (skill_directory(tmp_path, skill_id) / "manifest.json").write_text(
        "{not-json", encoding="utf-8"
    )

    with pytest.raises(SkillRegistryCorruptionError):
        repository.get_latest(skill_id)


def test_existing_revision_is_never_overwritten(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)
    original = make_manifest()
    repository.save_revision(original)
    original_bytes = revision_file(tmp_path, original).read_bytes()

    changed = make_manifest(content="different content")
    with pytest.raises(SkillRevisionAlreadyExistsError):
        repository.save_revision(changed)

    assert revision_file(tmp_path, original).read_bytes() == original_bytes


def test_no_temporary_files_remain_after_success(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)
    manifest = make_manifest()
    repository.save_revision(manifest)

    assert list(tmp_path.rglob("*.tmp")) == []


def test_existing_artifact_with_correct_hash_is_accepted(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)
    version_dir = (
        skill_directory(tmp_path, UUID("2b19a1d2-80c1-4c91-a121-9bd1d920c421"))
        / "versions"
        / "1.0.0"
    )
    version_dir.mkdir(parents=True)
    artifact_bytes = b'{"rules": true}'
    (version_dir / "rules.json").write_bytes(artifact_bytes)
    manifest = make_manifest(artifacts=[artifact_for("rules.json", artifact_bytes)])

    repository.save_revision(manifest)

    assert repository.verify_revision(manifest.skill_id, manifest.version)


def test_existing_artifact_with_wrong_hash_is_rejected(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)
    version_dir = (
        skill_directory(tmp_path, UUID("2b19a1d2-80c1-4c91-a121-9bd1d920c421"))
        / "versions"
        / "1.0.0"
    )
    version_dir.mkdir(parents=True)
    (version_dir / "rules.json").write_bytes(b"changed")
    manifest = make_manifest(artifacts=[artifact_for("rules.json", b"original")])

    with pytest.raises(SkillArtifactHashMismatchError):
        repository.save_revision(manifest)

    assert not (version_dir / "skill.json").exists()


def test_missing_referenced_python_artifact_does_not_block_or_execute(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)
    manifest = make_manifest(
        artifacts=[artifact_for("not-created.py", b"print('must not run')")]
    )

    repository.save_revision(manifest)

    assert repository.get_revision(manifest.skill_id, manifest.version) == manifest
    assert not (revision_file(tmp_path, manifest).parent / "not-created.py").exists()


def test_verify_revision_detects_modified_present_artifact(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)
    artifact_bytes = b"original artifact"
    manifest = make_manifest(artifacts=[artifact_for("rules.json", artifact_bytes)])
    repository.save_revision(manifest)
    artifact_path = revision_file(tmp_path, manifest).parent / "rules.json"
    artifact_path.write_bytes(b"modified artifact")

    assert not repository.verify_revision(manifest.skill_id, manifest.version)


def test_round_trip_preserves_all_manifest_data(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)
    manifest = SkillManifest.model_validate(
        {
            **make_manifest().model_dump(mode="json"),
            "tags": ["tag-a"],
            "triggers": ["trigger-a"],
            "quality_score": 95,
            "embedding_model": "model-v1",
            "embedding_dimension": 768,
            "metadata": {"nested": {"key": "value"}},
            "test_evidence": [
                {
                    "test_id": "test-a",
                    "test_name": "Test A",
                    "passed": True,
                    "executed_at": NOW.isoformat(),
                    "test_version": "1.0.0",
                }
            ],
            "dependencies": [
                {
                    "dependency_id": "dep-a",
                    "dependency_type": "skill",
                    "required": True,
                }
            ],
        }
    )
    repository.save_revision(manifest)

    assert repository.get_revision(manifest.skill_id, manifest.version) == manifest


def test_all_repository_files_are_created_under_tmp_path(tmp_path: Path) -> None:
    repository = CanonicalSkillRepository(tmp_path)
    manifest = make_manifest()

    repository.save_revision(manifest)

    created_files = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert created_files
    assert all(path.resolve().is_relative_to(tmp_path.resolve()) for path in created_files)


def test_failed_atomic_write_cleans_temporary_file_and_partial_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = CanonicalSkillRepository(tmp_path)
    manifest = make_manifest()
    original_replace = os.replace
    replacements = 0

    def fail_catalog_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        nonlocal replacements
        replacements += 1
        if replacements == 2:
            raise OSError("simulated catalog replacement failure")
        original_replace(source, destination)

    monkeypatch.setattr("repositories.canonical_skill_repository.os.replace", fail_catalog_replace)

    with pytest.raises(OSError, match="simulated"):
        repository.save_revision(manifest)

    assert not revision_file(tmp_path, manifest).exists()
    assert list(tmp_path.rglob("*.tmp")) == []
