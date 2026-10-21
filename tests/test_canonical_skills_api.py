import hashlib
import importlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_routes import canonical_skills
from repositories.canonical_skill_repository import CanonicalSkillRepository
from schemas.skill_manifest_schema import SkillManifest, SkillStatus


NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
SKILL_ID = UUID("2b19a1d2-80c1-4c91-a121-9bd1d920c421")


def make_manifest(
    status: SkillStatus = SkillStatus.CANDIDATE,
    version: str = "1.0.0",
) -> SkillManifest:
    content = "skill content for API tests"
    data: dict[str, object] = {
        "skill_id": SKILL_ID,
        "name": "API skill",
        "slug": "api-skill",
        "domain": "GENERAL",
        "version": version,
        "status": status,
        "description": "A skill for route tests.",
        "content": content,
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "author": "api-test",
        "created_at": NOW,
        "updated_at": NOW,
    }
    if status in {SkillStatus.APPROVED, SkillStatus.PUBLISHED}:
        data.update(
            {
                "approver": "reviewer",
                "test_evidence": [
                    {
                        "test_id": "passing-test",
                        "test_name": "Passing test",
                        "passed": True,
                        "executed_at": NOW,
                        "test_version": "1.0.0",
                    }
                ],
            }
        )
    if status is SkillStatus.PUBLISHED:
        data["published_at"] = NOW
    return SkillManifest.model_validate(data)


def make_client(tmp_path: Path) -> tuple[TestClient, CanonicalSkillRepository]:
    repository = CanonicalSkillRepository(tmp_path)
    app = FastAPI()
    app.include_router(canonical_skills.router)
    app.dependency_overrides[canonical_skills.get_repository] = lambda: repository
    return TestClient(app), repository


def save_revision(
    repository: CanonicalSkillRepository,
    status: SkillStatus = SkillStatus.CANDIDATE,
    version: str = "1.0.0",
) -> SkillManifest:
    manifest = make_manifest(status=status, version=version)
    repository.save_revision(manifest)
    return manifest


def test_lists_versions(tmp_path: Path) -> None:
    client, repository = make_client(tmp_path)
    save_revision(repository)
    save_revision(repository, version="1.1.0")

    response = client.get(f"/api/canonical-skills/{SKILL_ID}/versions")

    assert response.status_code == 200
    assert response.json() == {
        "skill_id": str(SKILL_ID),
        "versions": ["1.0.0", "1.1.0"],
    }


def test_reads_exact_version_and_latest(tmp_path: Path) -> None:
    client, repository = make_client(tmp_path)
    first = save_revision(repository)
    latest = save_revision(repository, version="1.1.0")

    exact = client.get(f"/api/canonical-skills/{SKILL_ID}/versions/1.0.0")
    latest_response = client.get(f"/api/canonical-skills/{SKILL_ID}/latest")

    assert exact.status_code == 200
    assert exact.json()["version"] == first.version
    assert latest_response.status_code == 200
    assert latest_response.json()["version"] == latest.version


def test_verify_returns_valid_true(tmp_path: Path) -> None:
    client, repository = make_client(tmp_path)
    manifest = save_revision(repository)

    response = client.get(
        f"/api/canonical-skills/{SKILL_ID}/versions/{manifest.version}/verify"
    )

    assert response.status_code == 200
    assert response.json() == {
        "skill_id": str(SKILL_ID),
        "version": manifest.version,
        "valid": True,
    }


def test_verify_returns_only_false_for_hash_divergence(tmp_path: Path) -> None:
    client, repository = make_client(tmp_path)
    manifest = save_revision(repository)
    path = tmp_path / str(SKILL_ID) / "versions" / manifest.version / "skill.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["content"] = "tampered content"
    path.write_text(json.dumps(payload), encoding="utf-8")

    response = client.get(
        f"/api/canonical-skills/{SKILL_ID}/versions/{manifest.version}/verify"
    )

    assert response.status_code == 200
    assert response.json() == {"valid": False}


def test_missing_skill_returns_404(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)

    response = client.get(f"/api/canonical-skills/{SKILL_ID}/versions")

    assert response.status_code == 404


def test_missing_revision_returns_404(tmp_path: Path) -> None:
    client, repository = make_client(tmp_path)
    save_revision(repository)

    response = client.get(f"/api/canonical-skills/{SKILL_ID}/versions/2.0.0")

    assert response.status_code == 404


def test_invalid_version_returns_422(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)

    response = client.get(f"/api/canonical-skills/{SKILL_ID}/versions/not-semver")

    assert response.status_code == 422


def test_candidate_to_testing_returns_201(tmp_path: Path) -> None:
    client, repository = make_client(tmp_path)
    source = save_revision(repository)

    response = client.post(
        f"/api/canonical-skills/{SKILL_ID}/versions/{source.version}/transition",
        json={
            "new_version": "1.1.0",
            "target_status": "TESTING",
            "actor": "reviewer",
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "TESTING"


def test_testing_to_approved_and_approved_to_published(tmp_path: Path) -> None:
    client, repository = make_client(tmp_path)
    candidate = save_revision(repository)
    testing = client.post(
        f"/api/canonical-skills/{SKILL_ID}/versions/{candidate.version}/transition",
        json={
            "new_version": "1.1.0",
            "target_status": "TESTING",
            "actor": "reviewer",
        },
    )
    evidence = [
        {
            "test_id": "api-pass",
            "test_name": "API passing test",
            "passed": True,
            "executed_at": NOW.isoformat(),
            "test_version": "1.0.0",
        }
    ]

    approved = client.post(
        f"/api/canonical-skills/{SKILL_ID}/versions/1.1.0/transition",
        json={
            "new_version": "1.2.0",
            "target_status": "APPROVED",
            "actor": "reviewer",
            "approver": "approver",
            "test_evidence": evidence,
        },
    )
    published = client.post(
        f"/api/canonical-skills/{SKILL_ID}/versions/1.2.0/transition",
        json={
            "new_version": "2.0.0",
            "target_status": "PUBLISHED",
            "actor": "publisher",
            "approver": "publisher",
            "test_evidence": evidence,
            "published_at": NOW.isoformat(),
        },
    )

    assert testing.status_code == 201
    assert approved.status_code == 201
    assert published.status_code == 201
    assert published.json()["status"] == "PUBLISHED"


def test_forbidden_transition_returns_409(tmp_path: Path) -> None:
    client, repository = make_client(tmp_path)
    source = save_revision(repository)

    response = client.post(
        f"/api/canonical-skills/{SKILL_ID}/versions/{source.version}/transition",
        json={
            "new_version": "1.1.0",
            "target_status": "PUBLISHED",
            "actor": "publisher",
            "approver": "publisher",
            "published_at": NOW.isoformat(),
        },
    )

    assert response.status_code == 409


def test_existing_target_version_returns_409(tmp_path: Path) -> None:
    client, repository = make_client(tmp_path)
    source = save_revision(repository)
    save_revision(repository, version="1.1.0")

    response = client.post(
        f"/api/canonical-skills/{SKILL_ID}/versions/{source.version}/transition",
        json={
            "new_version": "1.1.0",
            "target_status": "TESTING",
            "actor": "reviewer",
        },
    )

    assert response.status_code == 409


def test_approval_and_publication_requirements_return_422(tmp_path: Path) -> None:
    client, repository = make_client(tmp_path)
    candidate = save_revision(repository)
    testing = client.post(
        f"/api/canonical-skills/{SKILL_ID}/versions/{candidate.version}/transition",
        json={
            "new_version": "1.1.0",
            "target_status": "TESTING",
            "actor": "reviewer",
        },
    )
    no_approver = client.post(
        f"/api/canonical-skills/{SKILL_ID}/versions/1.1.0/transition",
        json={
            "new_version": "1.2.0",
            "target_status": "APPROVED",
            "actor": "reviewer",
            "test_evidence": [
                {
                    "test_id": "pass",
                    "test_name": "Pass",
                    "passed": True,
                    "executed_at": NOW.isoformat(),
                    "test_version": "1.0.0",
                }
            ],
        },
    )
    no_published_at = client.post(
        f"/api/canonical-skills/{SKILL_ID}/versions/1.1.0/transition",
        json={
            "new_version": "1.2.0",
            "target_status": "APPROVED",
            "actor": "reviewer",
            "approver": "approver",
            "test_evidence": [
                {
                    "test_id": "pass",
                    "test_name": "Pass",
                    "passed": True,
                    "executed_at": NOW.isoformat(),
                    "test_version": "1.0.0",
                }
            ],
        },
    )
    assert testing.status_code == 201
    assert no_approver.status_code == 422
    assert no_published_at.status_code == 201
    publish_without_date = client.post(
        f"/api/canonical-skills/{SKILL_ID}/versions/1.2.0/transition",
        json={
            "new_version": "1.3.0",
            "target_status": "PUBLISHED",
            "actor": "publisher",
            "approver": "publisher",
        },
    )

    assert publish_without_date.status_code == 422


def test_corrupt_registry_returns_generic_500_without_local_path(
    tmp_path: Path,
) -> None:
    client, _ = make_client(tmp_path)
    skill_dir = tmp_path / str(SKILL_ID)
    skill_dir.mkdir()
    (skill_dir / "manifest.json").write_text("invalid-json", encoding="utf-8")

    response = client.get(f"/api/canonical-skills/{SKILL_ID}/latest")

    assert response.status_code == 500
    assert str(tmp_path) not in response.text
    assert "corrupted" in response.text


def test_corrupt_versions_registry_returns_generic_500_without_local_path(
    tmp_path: Path,
) -> None:
    client, _ = make_client(tmp_path)
    skill_dir = tmp_path / str(SKILL_ID)
    skill_dir.mkdir()
    (skill_dir / "manifest.json").write_text("invalid-json", encoding="utf-8")

    response = client.get(f"/api/canonical-skills/{SKILL_ID}/versions")

    assert response.status_code == 500
    assert str(tmp_path) not in response.text


def test_verify_missing_revision_returns_404(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)

    response = client.get(
        f"/api/canonical-skills/{SKILL_ID}/versions/1.0.0/verify"
    )

    assert response.status_code == 404


def test_latest_missing_skill_returns_404(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)

    response = client.get(f"/api/canonical-skills/{SKILL_ID}/latest")

    assert response.status_code == 404


def test_missing_transition_source_returns_404(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)

    response = client.post(
        f"/api/canonical-skills/{SKILL_ID}/versions/1.0.0/transition",
        json={
            "new_version": "1.1.0",
            "target_status": "TESTING",
            "actor": "reviewer",
        },
    )

    assert response.status_code == 404


def test_empty_actor_returns_422(tmp_path: Path) -> None:
    client, repository = make_client(tmp_path)
    source = save_revision(repository)

    response = client.post(
        f"/api/canonical-skills/{SKILL_ID}/versions/{source.version}/transition",
        json={
            "new_version": "1.1.0",
            "target_status": "TESTING",
            "actor": "   ",
        },
    )

    assert response.status_code == 422


def test_old_revision_remains_readable_after_transition(tmp_path: Path) -> None:
    client, repository = make_client(tmp_path)
    source = save_revision(repository)
    transitioned = client.post(
        f"/api/canonical-skills/{SKILL_ID}/versions/{source.version}/transition",
        json={
            "new_version": "1.1.0",
            "target_status": "TESTING",
            "actor": "reviewer",
        },
    )

    old = client.get(f"/api/canonical-skills/{SKILL_ID}/versions/1.0.0")

    assert transitioned.status_code == 201
    assert old.status_code == 200
    assert old.json()["status"] == "CANDIDATE"


def test_router_writes_only_under_tmp_path(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)

    response = client.get(f"/api/canonical-skills/{SKILL_ID}/versions")

    assert response.status_code == 404
    assert all(path.resolve().is_relative_to(tmp_path.resolve()) for path in tmp_path.rglob("*"))


def test_importing_router_does_not_create_registry_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "registry-not-created"
    monkeypatch.setenv("DSH_CANONICAL_SKILLS_ROOT", str(root))
    importlib.reload(canonical_skills)

    assert not root.exists()
