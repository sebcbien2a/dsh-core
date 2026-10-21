"""Local, file-backed persistence for canonical skill revisions."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from schemas.skill_manifest_schema import SkillArtifact, SkillManifest, SkillStatus


_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)"
    r"(?:-((?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_CATALOG_FIELDS = {
    "skill_id",
    "name",
    "slug",
    "domain",
    "latest_version",
    "latest_status",
    "updated_at",
    "versions",
}


class SkillRepositoryError(Exception):
    """Base exception for canonical skill repository errors."""


class SkillRevisionAlreadyExistsError(SkillRepositoryError):
    """Raised when attempting to persist an existing skill revision."""


class SkillContentHashMismatchError(SkillRepositoryError):
    """Raised when manifest content does not match its declared hash."""


class SkillArtifactHashMismatchError(SkillRepositoryError):
    """Raised when a present local artifact does not match its declared hash."""


class SkillRegistryCorruptionError(SkillRepositoryError):
    """Raised when repository metadata or a stored revision is malformed."""


def _semver_key(version: str) -> tuple[Any, ...]:
    """Return a SemVer precedence key, ignoring build metadata."""
    match = _SEMVER_PATTERN.fullmatch(version)
    if match is None:
        raise ValueError(f"Invalid SemVer version: {version!r}")

    major, minor, patch, prerelease, _build = match.groups()
    if prerelease is None:
        prerelease_key: tuple[tuple[int, Any], ...] = ()
        release_rank = 1
    else:
        prerelease_key = tuple(
            (0, (len(identifier), identifier))
            if identifier.isascii() and identifier.isdigit()
            else (1, identifier)
            for identifier in prerelease.split(".")
        )
        release_rank = 0

    return (
        (len(major), major),
        (len(minor), minor),
        (len(patch), patch),
        release_rank,
        prerelease_key,
    )


class CanonicalSkillRepository:
    """Persist immutable skill revisions under an injectable local root."""

    def __init__(self, root_path: Path):
        self.root_path = Path(root_path)

    def save_revision(self, manifest: SkillManifest) -> Path:
        skill_dir = self.root_path / str(manifest.skill_id)
        versions_dir = skill_dir / "versions"
        version_dir = versions_dir / manifest.version
        revision_path = version_dir / "skill.json"
        catalog_path = skill_dir / "manifest.json"

        if revision_path.exists():
            raise SkillRevisionAlreadyExistsError(
                f"Revision {manifest.skill_id}@{manifest.version} already exists"
            )

        content_hash = hashlib.sha256(manifest.content.encode("utf-8")).hexdigest()
        if content_hash != manifest.content_hash:
            raise SkillContentHashMismatchError(
                f"Content hash mismatch for {manifest.skill_id}@{manifest.version}"
            )

        versions = self._existing_versions(skill_dir)
        if manifest.version in versions:
            raise SkillRevisionAlreadyExistsError(
                f"Revision {manifest.skill_id}@{manifest.version} already exists"
            )

        previous_catalog: dict[str, Any] | None = None
        if catalog_path.exists():
            previous_catalog = self._load_catalog(manifest.skill_id)

        self._verify_present_artifacts(manifest, version_dir)
        version_dir.mkdir(parents=True, exist_ok=True)

        new_versions = sorted([*versions, manifest.version], key=_semver_key)
        latest_manifest = manifest
        if previous_catalog is not None and new_versions[-1] != manifest.version:
            latest_manifest = self.get_revision(
                manifest.skill_id, str(previous_catalog["latest_version"])
            )

        catalog_data = self._catalog_from_manifest(
            latest_manifest,
            new_versions,
        )
        revision_written = False
        try:
            self._write_atomically(
                revision_path,
                manifest.model_dump_json(indent=2),
                refuse_existing=True,
            )
            revision_written = True
            self._write_atomically(
                catalog_path,
                json.dumps(catalog_data, ensure_ascii=False, indent=2) + "\n",
            )
        except Exception:
            if revision_written:
                revision_path.unlink(missing_ok=True)
            self._remove_empty_version_directories(version_dir, skill_dir)
            raise

        return revision_path

    def get_revision(self, skill_id: UUID, version: str) -> SkillManifest:
        revision_path = self._revision_path(skill_id, version)
        if not revision_path.is_file():
            raise FileNotFoundError(revision_path)
        try:
            data = json.loads(revision_path.read_text(encoding="utf-8"))
            manifest = SkillManifest.model_validate(data)
            if manifest.skill_id != skill_id or manifest.version != version:
                raise ValueError("revision identity does not match its repository path")
            return manifest
        except (OSError, json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
            raise SkillRegistryCorruptionError(
                f"Invalid revision file: {revision_path}"
            ) from exc

    def get_latest(self, skill_id: UUID) -> SkillManifest:
        catalog = self._load_catalog(skill_id)
        return self.get_revision(skill_id, str(catalog["latest_version"]))

    def list_versions(self, skill_id: UUID) -> list[str]:
        catalog = self._load_catalog(skill_id)
        return list(catalog["versions"])

    def exists(self, skill_id: UUID, version: str) -> bool:
        return self._revision_path(skill_id, version).is_file()

    def verify_revision(self, skill_id: UUID, version: str) -> bool:
        try:
            manifest = self.get_revision(skill_id, version)
            if hashlib.sha256(manifest.content.encode("utf-8")).hexdigest() != manifest.content_hash:
                return False
            self._verify_present_artifacts(
                manifest,
                self._revision_path(skill_id, version).parent,
            )
        except (FileNotFoundError, SkillRepositoryError):
            return False
        return True

    def _revision_path(self, skill_id: UUID, version: str) -> Path:
        if _SEMVER_PATTERN.fullmatch(version) is None:
            raise ValueError(f"Invalid SemVer version: {version!r}")
        return self.root_path / str(skill_id) / "versions" / version / "skill.json"

    def _load_catalog(self, skill_id: UUID) -> dict[str, Any]:
        catalog_path = self.root_path / str(skill_id) / "manifest.json"
        if not catalog_path.is_file():
            raise FileNotFoundError(catalog_path)
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            if not isinstance(catalog, dict) or set(catalog) != _CATALOG_FIELDS:
                raise ValueError("catalog fields do not match the expected schema")
            if catalog["skill_id"] != str(skill_id):
                raise ValueError("catalog skill_id does not match its directory")
            if not isinstance(catalog["versions"], list) or not catalog["versions"]:
                raise ValueError("catalog versions must be a non-empty list")
            versions = catalog["versions"]
            sorted_versions = sorted(versions, key=_semver_key)
            if versions != sorted_versions or len(versions) != len(set(versions)):
                raise ValueError("catalog versions must be unique and SemVer-sorted")
            if catalog["latest_version"] != versions[-1]:
                raise ValueError("latest_version does not match the highest SemVer")
            if catalog["latest_status"] not in {status.value for status in SkillStatus}:
                raise ValueError("latest_status is invalid")
            return catalog
        except (
            OSError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise SkillRegistryCorruptionError(
                f"Invalid skill catalog: {catalog_path}"
            ) from exc

    def _existing_versions(self, skill_dir: Path) -> list[str]:
        catalog_path = skill_dir / "manifest.json"
        if not catalog_path.exists():
            return []
        return list(self._load_catalog(UUID(skill_dir.name))["versions"])

    def _catalog_from_manifest(
        self, manifest: SkillManifest, versions: list[str]
    ) -> dict[str, Any]:
        return {
            "skill_id": str(manifest.skill_id),
            "name": manifest.name,
            "slug": manifest.slug,
            "domain": manifest.domain.value,
            "latest_version": manifest.version,
            "latest_status": manifest.status.value,
            "updated_at": manifest.updated_at.isoformat(),
            "versions": versions,
        }

    def _verify_present_artifacts(
        self, manifest: SkillManifest, version_dir: Path
    ) -> None:
        resolved_version_dir = version_dir.resolve()
        for artifact in manifest.artifacts:
            artifact_path = self._artifact_path(
                artifact,
                version_dir,
                resolved_version_dir,
            )
            if not artifact_path.exists():
                continue
            if not artifact_path.is_file():
                raise SkillArtifactHashMismatchError(
                    f"Artifact is not a file: {artifact.relative_path}"
                )
            actual_hash = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
            if actual_hash != artifact.content_hash:
                raise SkillArtifactHashMismatchError(
                    f"Artifact hash mismatch: {artifact.relative_path}"
                )

    def _artifact_path(
        self,
        artifact: SkillArtifact,
        version_dir: Path,
        resolved_version_dir: Path,
    ) -> Path:
        parts = artifact.relative_path.replace("\\", "/").split("/")
        artifact_path = version_dir.joinpath(*parts)
        try:
            artifact_path.resolve().relative_to(resolved_version_dir)
        except ValueError as exc:
            raise SkillRepositoryError(
                f"Artifact path escapes revision directory: {artifact.relative_path}"
            ) from exc
        return artifact_path

    def _write_atomically(
        self,
        destination: Path,
        content: str,
        *,
        refuse_existing: bool = False,
    ) -> None:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())

            if refuse_existing and destination.exists():
                raise SkillRevisionAlreadyExistsError(
                    f"Destination already exists: {destination}"
                )
            os.replace(temporary_path, destination)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _remove_empty_version_directories(self, version_dir: Path, skill_dir: Path) -> None:
        try:
            version_dir.rmdir()
            (skill_dir / "versions").rmdir()
            skill_dir.rmdir()
        except OSError:
            pass
