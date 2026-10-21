"""Transactional local JSON writer for the accounting and banking master tables."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from schemas.banking_schema import PaymentBatchStatus
from schemas.master_tables_schema import (
    AccountingDocumentMasterRow,
    ActorSystem,
    AuditAction,
    AuditEvent,
    AuditResourceType,
    BankTransactionMasterRow,
    DuplicateOperationError,
    LockExpiredError,
    LockOwnershipError,
    LockStatus,
    LockUnavailableError,
    MasterTableError,
    MasterType,
    MasterVersion,
    ResourceLock,
    StatusDomain,
    StatusHistory,
    can_acquire_lock,
    next_master_version,
    validate_write_preconditions,
)


_HASH_PATTERN = re.compile(r"[0-9a-fA-F]{64}", re.ASCII)
_TRANSACTION_PHASES = {
    "PREPARED",
    "REPLACING",
    "COMMITTED",
    "ROLLED_BACK",
    "RECOVERY_REQUIRED",
}
_RESOLVED_PHASES = {"COMMITTED", "ROLLED_BACK"}
_STATE_FILES = (
    "accounting/rows.json",
    "accounting/master_version.json",
    "banking/rows.json",
    "banking/master_version.json",
    "audit/events.jsonl",
    "audit/status_history.jsonl",
)
_ACCOUNTING_STATUSES = (
    ("ingestion_status", StatusDomain.INGESTION),
    ("extraction_status", StatusDomain.EXTRACTION),
    ("validation_status", StatusDomain.VALIDATION),
    ("processing_status", StatusDomain.PROCESSING),
    ("reconciliation_status", StatusDomain.RECONCILIATION),
    ("current_file_state", StatusDomain.FILE_STATE),
)
_BANKING_STATUSES = (
    ("import_status", StatusDomain.INGESTION),
    ("banking_record.reconciliation_status", StatusDomain.RECONCILIATION),
)


class LocalMasterWriterError(Exception):
    """Base exception for local master writer operations."""


class LocalMasterInitializationError(LocalMasterWriterError):
    """Raised when the local master tables cannot be initialized safely."""


class LocalMasterCorruptionError(LocalMasterWriterError):
    """Raised when persisted local master data is invalid or incomplete."""


class LocalMasterAtomicWriteError(LocalMasterWriterError):
    """Raised when a local transaction cannot be completed or rolled back."""


class LocalMasterRowConflictError(LocalMasterWriterError):
    """Raised when a row update has a conflicting or invalid version."""


class LocalMasterRecoveryError(LocalMasterWriterError):
    """Raised when an interrupted write cannot be safely recovered."""


class LocalMasterWriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    master_type: MasterType
    operation_id: UUID
    previous_master_version: StrictInt
    new_master_version: StrictInt
    created_row_ids: list[UUID] = Field(default_factory=list)
    updated_row_ids: list[UUID] = Field(default_factory=list)
    unchanged_row_ids: list[UUID] = Field(default_factory=list)
    audit_event_ids: list[UUID] = Field(default_factory=list)
    status_history_ids: list[UUID] = Field(default_factory=list)
    committed_at: datetime
    content_hash: str
    idempotency_key: str

    @field_validator(
        "previous_master_version",
        "new_master_version",
    )
    @classmethod
    def validate_versions(cls, value: int) -> int:
        if value < 0:
            raise ValueError("master versions must be non-negative")
        return value

    @field_validator("committed_at")
    @classmethod
    def validate_committed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("committed_at must be timezone-aware UTC")
        return value

    @field_validator("content_hash")
    @classmethod
    def validate_content_hash(cls, value: str) -> str:
        if not _HASH_PATTERN.fullmatch(value):
            raise ValueError("content_hash must be a SHA-256 hex digest")
        return value

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("idempotency_key must not be blank")
        return value

    @model_validator(mode="after")
    def validate_result(self) -> LocalMasterWriteResult:
        if self.new_master_version != self.previous_master_version + 1:
            raise ValueError("new master version must increment by one")
        lists = (
            self.created_row_ids,
            self.updated_row_ids,
            self.unchanged_row_ids,
            self.audit_event_ids,
            self.status_history_ids,
        )
        all_ids = [item for values in lists for item in values]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("result identifiers must be unique across lists")
        return self


class LocalTransactionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_manifest_id: UUID
    master_type: MasterType
    operation_id: UUID
    idempotency_key: str
    phase: str
    created_at: datetime
    updated_at: datetime
    target_files: list[str]
    backup_files: list[str]
    content_hashes: dict[str, str]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("phase")
    @classmethod
    def validate_phase(cls, value: str) -> str:
        if value not in _TRANSACTION_PHASES:
            raise ValueError("invalid transaction phase")
        return value

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("manifest timestamps must be timezone-aware UTC")
        return value

    @field_validator("target_files", "backup_files")
    @classmethod
    def validate_relative_paths(cls, paths: list[str]) -> list[str]:
        for value in paths:
            path = PurePosixPath(value)
            if (
                not value
                or path.is_absolute()
                or "\\" in value
                or any(part == ".." or ":" in part for part in path.parts)
            ):
                raise ValueError("manifest paths must be relative POSIX paths")
        return paths


class LocalMasterWriter:
    def __init__(self, root_path: str | Path):
        self._root = Path(root_path)

    @staticmethod
    def _master_name(master_type: MasterType) -> str:
        return "accounting" if master_type == MasterType.ACCOUNTING else "banking"

    def _relative(self, master_type: MasterType, filename: str) -> str:
        return f"{self._master_name(master_type)}/{filename}"

    def _path(self, relative: str) -> Path:
        pure = PurePosixPath(relative)
        if (
            not relative
            or pure.is_absolute()
            or "\\" in relative
            or any(part == ".." or ":" in part for part in pure.parts)
        ):
            raise LocalMasterWriterError("path must be relative to the local master root")
        candidate = self._root.joinpath(*pure.parts)
        if not candidate.resolve().is_relative_to(self._root.resolve()):
            raise LocalMasterWriterError("path resolves outside the local master root")
        return candidate

    @staticmethod
    def _canonical_bytes(value: Any) -> bytes:
        return json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    @classmethod
    def _hash_value(cls, value: Any) -> str:
        return hashlib.sha256(cls._canonical_bytes(value)).hexdigest()

    @staticmethod
    def _model_json(model: BaseModel) -> dict[str, Any]:
        return model.model_dump(mode="json")

    @staticmethod
    def _ensure_no_absolute_paths(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                LocalMasterWriter._ensure_no_absolute_paths(item)
        elif isinstance(value, list):
            for item in value:
                LocalMasterWriter._ensure_no_absolute_paths(item)
        elif isinstance(value, str):
            LocalMasterWriter._reject_unsafe_reference(value)

    @staticmethod
    def _reject_unsafe_reference(value: str) -> None:
        remainder = value.split("://", 1)[1] if "://" in value else value
        segments = re.split(r"[\\/]", remainder)
        if (
            re.match(r"^[A-Za-z]:[\\/]", value)
            or value.startswith("\\\\")
            or value.lower().startswith("file:")
            or (value.startswith("/") and "://" not in value)
            or (len(segments) > 1 and ".." in segments)
        ):
            raise LocalMasterWriterError(
                "absolute local paths and parent segments are not permitted in master data"
            )

    @staticmethod
    def _exception_message(operation: str) -> str:
        return f"local master {operation} failed"

    def _read_bytes(self, relative: str) -> bytes:
        try:
            return self._path(relative).read_bytes()
        except OSError as exc:
            raise LocalMasterCorruptionError(
                self._exception_message("file read")
            ) from exc

    def _read_json(self, relative: str) -> Any:
        try:
            return json.loads(self._read_bytes(relative))
        except LocalMasterCorruptionError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LocalMasterCorruptionError(
                self._exception_message("JSON deserialization")
            ) from exc

    def _read_model(self, relative: str, model_type: type[BaseModel]) -> Any:
        try:
            return model_type.model_validate(self._read_json(relative))
        except LocalMasterCorruptionError:
            raise
        except Exception as exc:
            raise LocalMasterCorruptionError(
                self._exception_message("model validation")
            ) from exc

    def _read_rows(
        self,
        master_type: MasterType,
        include_deleted: bool,
    ) -> list[AccountingDocumentMasterRow] | list[BankTransactionMasterRow]:
        relative = self._relative(master_type, "rows.json")
        raw = self._read_json(relative)
        if not isinstance(raw, list):
            raise LocalMasterCorruptionError("master rows must be a JSON array")
        model_type: type[BaseModel] = (
            AccountingDocumentMasterRow
            if master_type == MasterType.ACCOUNTING
            else BankTransactionMasterRow
        )
        try:
            rows = [model_type.model_validate(item) for item in raw]
        except Exception as exc:
            raise LocalMasterCorruptionError(
                self._exception_message("row validation")
            ) from exc
        if not include_deleted:
            rows = [row for row in rows if not row.is_deleted]
        return rows  # type: ignore[return-value]

    def _read_jsonl(self, relative: str, model_type: type[BaseModel]) -> list[Any]:
        raw = self._read_bytes(relative)
        result = []
        try:
            for line in raw.splitlines():
                if not line.strip():
                    continue
                result.append(model_type.model_validate_json(line))
        except Exception as exc:
            raise LocalMasterCorruptionError(
                self._exception_message("JSONL validation")
            ) from exc
        return result

    @staticmethod
    def _write_fsynced(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

    def _atomic_write(self, path: Path, data: bytes) -> None:
        temporary_name: str | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=".local-master-",
                delete=False,
            ) as stream:
                temporary_name = stream.name
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, path)
        except OSError as exc:
            if temporary_name is not None:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass
            raise LocalMasterAtomicWriteError(
                self._exception_message("atomic file write")
            ) from exc

    def _write_manifest(self, manifest: LocalTransactionManifest) -> None:
        relative = f"recovery/{manifest.transaction_manifest_id}.json"
        self._atomic_write(
            self._path(relative),
            self._canonical_bytes(self._model_json(manifest)),
        )

    def _restore_manifest(self, manifest: LocalTransactionManifest) -> None:
        backup_dir = f"recovery/{manifest.transaction_manifest_id}/backup"
        missing = set(manifest.metadata.get("originally_missing", []))
        for target in manifest.target_files:
            target_path = self._path(target)
            if target in missing:
                target_path.unlink(missing_ok=True)
                continue
            backup = f"{backup_dir}/{target}"
            backup_path = self._path(backup)
            if not backup_path.is_file():
                raise LocalMasterRecoveryError(
                    "transaction backup is missing"
                )
            self._atomic_write(target_path, backup_path.read_bytes())

    @staticmethod
    def _replacement_rank(relative: str) -> tuple[int, str]:
        # Rows first, audit next, lock after, and master_version.json last so
        # that the version file acts as the final commit marker of a publication.
        name = PurePosixPath(relative).name
        rank = {
            "rows.json": 0,
            "events.jsonl": 1,
            "status_history.jsonl": 1,
            "lock.json": 2,
            "master_version.json": 3,
        }.get(name, 4)
        return rank, relative

    def _ensure_no_unresolved_transaction(self) -> None:
        recovery_dir = self._path("recovery")
        if not recovery_dir.exists():
            return
        for manifest_path in sorted(recovery_dir.glob("*.json")):
            try:
                manifest = LocalTransactionManifest.model_validate_json(
                    manifest_path.read_bytes()
                )
            except Exception as exc:
                raise LocalMasterRecoveryError("transaction manifest is corrupt") from exc
            if manifest.phase not in _RESOLVED_PHASES:
                raise LocalMasterRecoveryError(
                    "an unresolved local transaction must be recovered first"
                )

    def _verify_rows_hash(
        self,
        master_type: MasterType,
        rows: list[Any],
        version: MasterVersion,
    ) -> None:
        payloads = [
            row.model_dump(mode="json")
            for row in sorted(rows, key=lambda row: str(self._row_id(master_type, row)))
        ]
        if self._hash_value(payloads) != version.content_hash.lower():
            raise LocalMasterCorruptionError(
                "master rows do not match the master version content hash"
            )

    def _verify_integrity(self, master_type: MasterType) -> None:
        self._verify_rows_hash(
            master_type,
            self._read_rows(master_type, include_deleted=True),
            self.read_master_version(master_type),
        )

    def _transaction(
        self,
        *,
        master_type: MasterType,
        operation_id: UUID,
        idempotency_key: str,
        now: datetime,
        files: dict[str, bytes],
    ) -> UUID:
        self._ensure_no_unresolved_transaction()
        manifest_id = uuid4()
        targets = sorted(files, key=self._replacement_rank)
        recovery_base = f"recovery/{manifest_id}"
        staging_base = f"staging/{manifest_id}"
        backup_files: list[str] = []
        originally_missing: list[str] = []
        hashes = {key: hashlib.sha256(value).hexdigest() for key, value in files.items()}
        try:
            for target in targets:
                path = self._path(target)
                if path.exists():
                    backup = f"{recovery_base}/backup/{target}"
                    self._write_fsynced(self._path(backup), path.read_bytes())
                    backup_files.append(backup)
                else:
                    originally_missing.append(target)
                self._write_fsynced(
                    self._path(f"{staging_base}/new/{target}"),
                    files[target],
                )

            manifest = LocalTransactionManifest(
                transaction_manifest_id=manifest_id,
                master_type=master_type,
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                phase="PREPARED",
                created_at=now,
                updated_at=now,
                target_files=targets,
                backup_files=backup_files,
                content_hashes=hashes,
                metadata={"originally_missing": originally_missing},
            )
            self._write_manifest(manifest)
            manifest = manifest.model_copy(
                update={"phase": "REPLACING", "updated_at": now}
            )
            self._write_manifest(manifest)

            for target in targets:
                staged_path = self._path(f"{staging_base}/new/{target}")
                target_path = self._path(target)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged_path, target_path)

            manifest = manifest.model_copy(
                update={"phase": "COMMITTED", "updated_at": now}
            )
            self._write_manifest(manifest)
            shutil.rmtree(self._path(staging_base), ignore_errors=True)
            shutil.rmtree(self._path(recovery_base), ignore_errors=True)
            return manifest_id
        except Exception as exc:
            if "manifest" not in locals():
                shutil.rmtree(self._path(staging_base), ignore_errors=True)
                shutil.rmtree(self._path(recovery_base), ignore_errors=True)
                raise LocalMasterAtomicWriteError(
                    self._exception_message("transaction preparation")
                ) from exc
            try:
                self._restore_manifest(manifest)
                rolled_back = manifest.model_copy(
                    update={"phase": "ROLLED_BACK", "updated_at": now}
                )
                self._write_manifest(rolled_back)
                shutil.rmtree(self._path(staging_base), ignore_errors=True)
                shutil.rmtree(self._path(recovery_base), ignore_errors=True)
            except Exception as recovery_exc:
                try:
                    required = manifest.model_copy(
                        update={"phase": "RECOVERY_REQUIRED", "updated_at": now}
                    )
                    self._write_manifest(required)
                except Exception:
                    pass
                raise LocalMasterRecoveryError(
                    "transaction failed and rollback needs recovery"
                ) from recovery_exc
            raise LocalMasterAtomicWriteError(
                self._exception_message("transaction")
            ) from exc

    def initialize(
        self,
        *,
        initialized_at: datetime,
        initialized_by: ActorSystem,
        operation_id: UUID,
    ) -> None:
        if initialized_at.tzinfo is None or initialized_at.utcoffset() != timedelta(0):
            raise LocalMasterInitializationError(
                "initialization time must be timezone-aware UTC"
            )
        required = list(_STATE_FILES)
        existing = [self._path(item).exists() for item in required]
        if any(existing):
            if not all(existing):
                raise LocalMasterInitializationError(
                    "existing local master state is incomplete"
                )
            try:
                for master in (MasterType.ACCOUNTING, MasterType.BANKING):
                    self._verify_integrity(master)
                self.read_audit_events()
                self.read_status_history()
            except Exception as exc:
                raise LocalMasterInitializationError(
                    "existing local master state is invalid"
                ) from exc
            return

        empty_hash = self._hash_value([])
        files = {
            "accounting/rows.json": b"[]",
            "banking/rows.json": b"[]",
            "audit/events.jsonl": b"",
            "audit/status_history.jsonl": b"",
        }
        for master in (MasterType.ACCOUNTING, MasterType.BANKING):
            version = MasterVersion(
                master_name=master,
                current_version=0,
                content_hash=empty_hash,
                last_operation_id=operation_id,
                updated_at=initialized_at,
                updated_by=initialized_by,
            )
            files[f"{self._master_name(master)}/master_version.json"] = (
                self._canonical_bytes(self._model_json(version))
            )
        try:
            self._transaction(
                master_type=MasterType.ACCOUNTING,
                operation_id=operation_id,
                idempotency_key=f"initialize:{operation_id}",
                now=initialized_at,
                files=files,
            )
        except Exception as exc:
            raise LocalMasterInitializationError(
                "local master initialization failed"
            ) from exc

    def read_master_version(self, master_type: MasterType) -> MasterVersion:
        version = self._read_model(
            self._relative(master_type, "master_version.json"),
            MasterVersion,
        )
        if version.master_name != master_type:
            raise LocalMasterCorruptionError("master version has an invalid master type")
        return version

    def read_accounting_rows(
        self,
        *,
        include_deleted: bool = False,
    ) -> list[AccountingDocumentMasterRow]:
        return self._read_rows(MasterType.ACCOUNTING, include_deleted)  # type: ignore[return-value]

    def read_banking_rows(
        self,
        *,
        include_deleted: bool = False,
    ) -> list[BankTransactionMasterRow]:
        return self._read_rows(MasterType.BANKING, include_deleted)  # type: ignore[return-value]

    def read_audit_events(self) -> list[AuditEvent]:
        return self._read_jsonl("audit/events.jsonl", AuditEvent)

    def read_status_history(self) -> list[StatusHistory]:
        return self._read_jsonl("audit/status_history.jsonl", StatusHistory)

    def read_lock(self, master_type: MasterType) -> ResourceLock | None:
        relative = self._relative(master_type, "lock.json")
        if not self._path(relative).exists():
            return None
        return self._read_model(relative, ResourceLock)

    def _append_files(
        self,
        *,
        master_type: MasterType,
        operation_id: UUID,
        idempotency_key: str,
        now: datetime,
        files: dict[str, bytes],
    ) -> None:
        self._transaction(
            master_type=master_type,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            now=now,
            files=files,
        )

    @staticmethod
    def _jsonl_bytes(events: list[BaseModel]) -> bytes:
        return b"".join(
            json.dumps(
                event.model_dump(mode="json"),
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
            for event in events
        )

    def _record_lock_event(
        self,
        master_type: MasterType,
        action: AuditAction,
        *,
        operation_id: UUID,
        occurred_at: datetime,
        lock: ResourceLock,
    ) -> AuditEvent:
        return AuditEvent(
            event_id=uuid4(),
            occurred_at=occurred_at,
            operation_id=operation_id,
            resource_type=AuditResourceType.LOCK,
            resource_id=lock.resource_name,
            action=action,
            actor_system=lock.owner_system,
            actor_id=lock.owner_instance_id,
            previous_version=lock.base_version,
            new_version=lock.base_version,
            reason=f"{master_type.value} lock {action.value.lower()}",
        )

    def acquire_lock(
        self,
        master_type: MasterType,
        *,
        owner_system: ActorSystem,
        owner_instance_id: str,
        operation_id: UUID,
        acquired_at: datetime,
        expires_at: datetime,
    ) -> ResourceLock:
        current = self.read_master_version(master_type)
        existing = self.read_lock(master_type)
        if not can_acquire_lock(existing, now=acquired_at):
            raise LockUnavailableError("master resource is already locked")
        lock = ResourceLock(
            lock_id=uuid4(),
            resource_name=master_type.value,
            owner_system=owner_system,
            owner_instance_id=owner_instance_id,
            operation_id=operation_id,
            acquired_at=acquired_at,
            heartbeat_at=acquired_at,
            expires_at=expires_at,
            base_version=current.current_version,
            status=LockStatus.ACTIVE,
        )
        event = self._record_lock_event(
            master_type,
            AuditAction.LOCK_ACQUIRED,
            operation_id=operation_id,
            occurred_at=acquired_at,
            lock=lock,
        )
        events = self.read_audit_events()
        if existing is not None and existing.status == LockStatus.ACTIVE:
            # can_acquire_lock only allows an ACTIVE lock here when it has expired.
            events.append(
                self._record_lock_event(
                    master_type,
                    AuditAction.LOCK_EXPIRED,
                    operation_id=existing.operation_id,
                    occurred_at=acquired_at,
                    lock=existing,
                )
            )
        events.append(event)
        self._append_files(
            master_type=master_type,
            operation_id=operation_id,
            idempotency_key=f"lock-acquire:{lock.lock_id}",
            now=acquired_at,
            files={
                self._relative(master_type, "lock.json"): self._canonical_bytes(
                    self._model_json(lock)
                ),
                "audit/events.jsonl": self._jsonl_bytes(events),
            },
        )
        return lock

    def _owned_lock(
        self,
        master_type: MasterType,
        *,
        lock_id: UUID,
        owner_system: ActorSystem,
        owner_instance_id: str,
        now: datetime,
    ) -> ResourceLock:
        lock = self.read_lock(master_type)
        if lock is None:
            raise LockUnavailableError("master resource has no lock")
        if lock.lock_id != lock_id:
            raise LockOwnershipError("lock identifier does not match")
        if lock.owner_system != owner_system or lock.owner_instance_id != owner_instance_id:
            raise LockOwnershipError("caller does not own the lock")
        if lock.is_expired(now):
            raise LockExpiredError("master resource lock has expired")
        if lock.status != LockStatus.ACTIVE:
            raise LockUnavailableError("master resource lock is not active")
        if now < lock.heartbeat_at:
            raise MasterTableError("operation time precedes the lock heartbeat")
        return lock

    @staticmethod
    def _released_lock(lock: ResourceLock, released_at: datetime) -> ResourceLock:
        values = lock.model_dump()
        values.update(
            status=LockStatus.RELEASED,
            heartbeat_at=released_at,
            expires_at=max(lock.expires_at, released_at + timedelta(microseconds=1)),
        )
        return ResourceLock.model_validate(values)

    def renew_lock(
        self,
        master_type: MasterType,
        *,
        lock_id: UUID,
        owner_system: ActorSystem,
        owner_instance_id: str,
        now: datetime,
        new_expires_at: datetime,
    ) -> ResourceLock:
        old = self._owned_lock(
            master_type,
            lock_id=lock_id,
            owner_system=owner_system,
            owner_instance_id=owner_instance_id,
            now=now,
        )
        renewed = old.renew(now, new_expires_at)
        event = self._record_lock_event(
            master_type,
            AuditAction.LOCK_RENEWED,
            operation_id=old.operation_id,
            occurred_at=now,
            lock=renewed,
        )
        events = self.read_audit_events() + [event]
        self._append_files(
            master_type=master_type,
            operation_id=old.operation_id,
            idempotency_key=f"lock-renew:{lock_id}:{now.isoformat()}",
            now=now,
            files={
                self._relative(master_type, "lock.json"): self._canonical_bytes(
                    self._model_json(renewed)
                ),
                "audit/events.jsonl": self._jsonl_bytes(events),
            },
        )
        return renewed

    def release_lock(
        self,
        master_type: MasterType,
        *,
        lock_id: UUID,
        owner_system: ActorSystem,
        owner_instance_id: str,
        released_at: datetime,
    ) -> None:
        old = self._owned_lock(
            master_type,
            lock_id=lock_id,
            owner_system=owner_system,
            owner_instance_id=owner_instance_id,
            now=released_at,
        )
        released = self._released_lock(old, released_at)
        event = self._record_lock_event(
            master_type,
            AuditAction.LOCK_RELEASED,
            operation_id=old.operation_id,
            occurred_at=released_at,
            lock=released,
        )
        events = self.read_audit_events() + [event]
        self._append_files(
            master_type=master_type,
            operation_id=old.operation_id,
            idempotency_key=f"lock-release:{lock_id}",
            now=released_at,
            files={
                self._relative(master_type, "lock.json"): self._canonical_bytes(
                    self._model_json(released)
                ),
                "audit/events.jsonl": self._jsonl_bytes(events),
            },
        )

    @staticmethod
    def _row_id(master_type: MasterType, row: BaseModel) -> UUID:
        return row.document_id if master_type == MasterType.ACCOUNTING else row.transaction_id

    @staticmethod
    def _status_events(
        master_type: MasterType,
        old: BaseModel | None,
        new: BaseModel,
        *,
        operation_id: UUID,
        changed_at: datetime,
    ) -> list[StatusHistory]:
        if old is None:
            return []
        specs = (
            _ACCOUNTING_STATUSES
            if master_type == MasterType.ACCOUNTING
            else _BANKING_STATUSES
        )
        result = []
        for name, domain in specs:
            old_value: Any = old
            new_value: Any = new
            if "." in name:
                first, second = name.split(".", 1)
                old_value, new_value = getattr(old_value, first), getattr(new_value, first)
                old_status, new_status = getattr(old_value, second), getattr(new_value, second)
            else:
                old_status, new_status = getattr(old_value, name), getattr(new_value, name)
            if old_status == new_status:
                continue
            result.append(
                StatusHistory(
                    status_history_id=uuid4(),
                    resource_type=(
                        AuditResourceType.ACCOUNTING_DOCUMENT
                        if master_type == MasterType.ACCOUNTING
                        else AuditResourceType.BANK_TRANSACTION
                    ),
                    resource_id=str(LocalMasterWriter._row_id(master_type, new)),
                    status_domain=domain,
                    status_from=str(old_status.value),
                    status_to=str(new_status.value),
                    changed_at=changed_at,
                    changed_by=new.updated_by,
                    operation_id=operation_id,
                    row_version_after_change=new.row_version,
                )
            )
        if master_type == MasterType.BANKING:
            old_batch = old.banking_record.payment_batch
            new_batch = new.banking_record.payment_batch
            old_status = old_batch.status if old_batch is not None else None
            new_status = new_batch.status if new_batch is not None else None
            if old_status != new_status and new_status is not None:
                result.append(
                    StatusHistory(
                        status_history_id=uuid4(),
                        resource_type=AuditResourceType.PAYMENT_BATCH,
                        resource_id=str(new_batch.batch_id),
                        status_domain=StatusDomain.RECONCILIATION,
                        status_from=old_status.value if old_status is not None else None,
                        status_to=new_status.value,
                        changed_at=changed_at,
                        changed_by=new.updated_by,
                        operation_id=operation_id,
                        row_version_after_change=new.row_version,
                    )
                )
        return result

    @staticmethod
    def _changed_fields(old: BaseModel, new: BaseModel) -> list[str]:
        old_data = old.model_dump(mode="json")
        new_data = new.model_dump(mode="json")
        return sorted(
            key
            for key in old_data.keys() | new_data.keys()
            if old_data.get(key) != new_data.get(key)
        )

    def _publish(
        self,
        master_type: MasterType,
        rows: list[BaseModel],
        *,
        lock_id: UUID,
        owner_system: ActorSystem,
        owner_instance_id: str,
        expected_base_version: int,
        operation_id: UUID,
        idempotency_key: str,
        committed_at: datetime,
        audit_events: list[AuditEvent] | None,
        status_history: list[StatusHistory] | None,
    ) -> LocalMasterWriteResult:
        """Publish rows, audit, status history and the version in one local transaction.

        Decision for publications containing only unchanged rows (strategy A):
        the publication is still a write. It creates a new MasterVersion, consumes
        the idempotency_key, releases the lock and appends its audit event, and the
        result lists the rows under unchanged_row_ids. No row is rewritten with new
        content. This keeps LocalMasterWriteResult consistent (new version is always
        previous + 1) and makes every acknowledged operation replay-detectable.

        The transaction is recoverable, not ACID: os.replace is atomic per file only,
        and multi-file consistency relies on the manifest, backups and recovery.
        """
        if committed_at.tzinfo is None or committed_at.utcoffset() != timedelta(0):
            raise MasterTableError("committed_at must be timezone-aware UTC")
        if not rows and not audit_events:
            raise MasterTableError("publication requires rows or explicit audit events")

        lock = self._owned_lock(
            master_type,
            lock_id=lock_id,
            owner_system=owner_system,
            owner_instance_id=owner_instance_id,
            now=committed_at,
        )
        current_version = self.read_master_version(master_type)
        validate_write_preconditions(
            lock=lock,
            now=committed_at,
            owner_system=owner_system,
            owner_instance_id=owner_instance_id,
            expected_base_version=expected_base_version,
            master_version=current_version,
            idempotency_key=idempotency_key,
        )

        model_type: type[BaseModel] = (
            AccountingDocumentMasterRow
            if master_type == MasterType.ACCOUNTING
            else BankTransactionMasterRow
        )
        existing_rows = self._read_rows(master_type, include_deleted=True)
        self._verify_rows_hash(master_type, existing_rows, current_version)
        existing_by_id = {self._row_id(master_type, row): row for row in existing_rows}
        supplied_ids: set[UUID] = set()
        created_ids: list[UUID] = []
        updated_ids: list[UUID] = []
        unchanged_ids: list[UUID] = []
        output_by_id = dict(existing_by_id)
        generated_events: list[AuditEvent] = []
        generated_history: list[StatusHistory] = []

        for supplied in rows:
            try:
                row = model_type.model_validate(supplied.model_dump(mode="json"))
            except Exception as exc:
                raise LocalMasterRowConflictError("published row failed validation") from exc
            self._ensure_no_absolute_paths(row.model_dump(mode="json"))
            row_id = self._row_id(master_type, row)
            if row_id in supplied_ids:
                raise LocalMasterRowConflictError("duplicate row identifier in publication")
            supplied_ids.add(row_id)
            previous = existing_by_id.get(row_id)
            if previous is None:
                if row.row_version != 1:
                    raise LocalMasterRowConflictError(
                        "new rows must start at row_version 1"
                    )
                output_by_id[row_id] = row
                created_ids.append(row_id)
                action = (
                    AuditAction.IMPORTED
                    if (
                        row.ingestion_status.value == "IMPORTED"
                        if master_type == MasterType.ACCOUNTING
                        else row.import_status.value == "IMPORTED"
                    )
                    else AuditAction.CREATED
                )
                generated_events.append(
                    self._row_audit_event(
                        master_type,
                        row,
                        operation_id=operation_id,
                        committed_at=committed_at,
                        action=action,
                        previous=None,
                    )
                )
            elif row.row_version == previous.row_version:
                if row.content_hash != previous.content_hash:
                    raise LocalMasterRowConflictError(
                        "same row version has a different content hash"
                    )
                if row.model_dump(mode="json") != previous.model_dump(mode="json"):
                    raise LocalMasterRowConflictError(
                        "same row version and content hash with a different payload"
                    )
                unchanged_ids.append(row_id)
                continue
            elif row.row_version < previous.row_version:
                raise LocalMasterRowConflictError("row version regression")
            elif row.row_version != previous.row_version + 1:
                raise LocalMasterRowConflictError("row version must increment by one")
            else:
                if row.content_hash == previous.content_hash:
                    raise LocalMasterRowConflictError(
                        "a new row version must change content_hash"
                    )
                if (
                    row.created_at != previous.created_at
                    or row.created_by != previous.created_by
                ):
                    raise LocalMasterRowConflictError(
                        "row creation metadata is immutable"
                    )
                if row.updated_at < previous.updated_at:
                    raise LocalMasterRowConflictError(
                        "updated_at cannot move backwards"
                    )
                output_by_id[row_id] = row
                updated_ids.append(row_id)
                action = (
                    AuditAction.RESTORED
                    if previous.is_deleted and not row.is_deleted
                    else AuditAction.SOFT_DELETED
                    if not previous.is_deleted and row.is_deleted
                    else AuditAction.UPDATED
                )
                generated_events.append(
                    self._row_audit_event(
                        master_type,
                        row,
                        operation_id=operation_id,
                        committed_at=committed_at,
                        action=action,
                        previous=previous,
                    )
                )
                generated_history.extend(
                    self._status_events(
                        master_type,
                        previous,
                        row,
                        operation_id=operation_id,
                        changed_at=committed_at,
                    )
                )

        existing_events = self.read_audit_events()
        existing_history = self.read_status_history()
        event_ids = {event.event_id for event in existing_events}
        history_ids = {item.status_history_id for item in existing_history}
        supplied_events = list(audit_events or [])
        supplied_history = list(status_history or [])
        for event in supplied_events:
            try:
                validated = AuditEvent.model_validate(event.model_dump(mode="json"))
            except Exception as exc:
                raise LocalMasterRowConflictError("supplied audit event is invalid") from exc
            if validated.event_id in event_ids:
                raise LocalMasterRowConflictError("duplicate audit event identifier")
            event_ids.add(validated.event_id)
            self._ensure_no_absolute_paths(validated.model_dump(mode="json"))
        for item in supplied_history:
            try:
                validated = StatusHistory.model_validate(item.model_dump(mode="json"))
            except Exception as exc:
                raise LocalMasterRowConflictError(
                    "supplied status history is invalid"
                ) from exc
            if validated.status_history_id in history_ids:
                raise LocalMasterRowConflictError("duplicate status history identifier")
            history_ids.add(validated.status_history_id)
            self._ensure_no_absolute_paths(validated.model_dump(mode="json"))

        all_new_events = generated_events + supplied_events
        all_new_history = generated_history + supplied_history
        new_rows = sorted(output_by_id.values(), key=lambda row: str(self._row_id(master_type, row)))
        row_payloads = [row.model_dump(mode="json") for row in new_rows]
        state_hash = self._hash_value(row_payloads)
        next_version = next_master_version(
            current_version,
            operation_id=operation_id,
            actor=owner_system,
            idempotency_key=idempotency_key,
            content_hash=state_hash,
            updated_at=committed_at,
        )
        released_lock = self._released_lock(lock, committed_at)
        release_event = self._record_lock_event(
            master_type,
            AuditAction.LOCK_RELEASED,
            operation_id=operation_id,
            occurred_at=committed_at,
            lock=released_lock,
        )
        all_new_events.append(release_event)
        combined_events = existing_events + all_new_events
        combined_history = existing_history + all_new_history

        targets = {
            self._relative(master_type, "rows.json"): self._canonical_bytes(row_payloads),
            self._relative(master_type, "master_version.json"): self._canonical_bytes(
                self._model_json(next_version)
            ),
            self._relative(master_type, "lock.json"): self._canonical_bytes(
                self._model_json(released_lock)
            ),
            "audit/events.jsonl": self._jsonl_bytes(combined_events),
            "audit/status_history.jsonl": self._jsonl_bytes(combined_history),
        }
        self._ensure_no_absolute_paths(row_payloads)
        try:
            result = LocalMasterWriteResult(
                master_type=master_type,
                operation_id=operation_id,
                previous_master_version=current_version.current_version,
                new_master_version=next_version.current_version,
                created_row_ids=created_ids,
                updated_row_ids=updated_ids,
                unchanged_row_ids=unchanged_ids,
                audit_event_ids=[event.event_id for event in all_new_events],
                status_history_ids=[item.status_history_id for item in all_new_history],
                committed_at=committed_at,
                content_hash=state_hash,
                idempotency_key=idempotency_key,
            )
        except ValidationError as exc:
            raise LocalMasterRowConflictError(
                "publication result identifiers or versions are inconsistent"
            ) from exc
        self._append_files(
            master_type=master_type,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            now=committed_at,
            files=targets,
        )
        return result

    @staticmethod
    def _row_audit_event(
        master_type: MasterType,
        row: BaseModel,
        *,
        operation_id: UUID,
        committed_at: datetime,
        action: AuditAction,
        previous: BaseModel | None,
    ) -> AuditEvent:
        row_id = LocalMasterWriter._row_id(master_type, row)
        return AuditEvent(
            event_id=uuid4(),
            occurred_at=committed_at,
            operation_id=operation_id,
            resource_type=(
                AuditResourceType.ACCOUNTING_DOCUMENT
                if master_type == MasterType.ACCOUNTING
                else AuditResourceType.BANK_TRANSACTION
            ),
            resource_id=str(row_id),
            action=action,
            actor_system=(
                row.updated_by if master_type == MasterType.ACCOUNTING else row.updated_by
            ),
            previous_version=previous.row_version if previous is not None else 0,
            new_version=row.row_version,
            before_hash=previous.content_hash if previous is not None else None,
            after_hash=row.content_hash,
            changed_fields=(
                LocalMasterWriter._changed_fields(previous, row)
                if previous is not None
                else sorted(row.model_dump(mode="json").keys())
            ),
        )

    def publish_accounting_rows(
        self,
        rows: list[AccountingDocumentMasterRow],
        *,
        lock_id: UUID,
        owner_system: ActorSystem,
        owner_instance_id: str,
        expected_base_version: int,
        operation_id: UUID,
        idempotency_key: str,
        committed_at: datetime,
        audit_events: list[AuditEvent] | None = None,
        status_history: list[StatusHistory] | None = None,
    ) -> LocalMasterWriteResult:
        return self._publish(
            MasterType.ACCOUNTING,
            list(rows),
            lock_id=lock_id,
            owner_system=owner_system,
            owner_instance_id=owner_instance_id,
            expected_base_version=expected_base_version,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            committed_at=committed_at,
            audit_events=audit_events,
            status_history=status_history,
        )

    def publish_banking_rows(
        self,
        rows: list[BankTransactionMasterRow],
        *,
        lock_id: UUID,
        owner_system: ActorSystem,
        owner_instance_id: str,
        expected_base_version: int,
        operation_id: UUID,
        idempotency_key: str,
        committed_at: datetime,
        audit_events: list[AuditEvent] | None = None,
        status_history: list[StatusHistory] | None = None,
    ) -> LocalMasterWriteResult:
        return self._publish(
            MasterType.BANKING,
            list(rows),
            lock_id=lock_id,
            owner_system=owner_system,
            owner_instance_id=owner_instance_id,
            expected_base_version=expected_base_version,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            committed_at=committed_at,
            audit_events=audit_events,
            status_history=status_history,
        )

    def _verify_recovered_state(self) -> bool:
        """Validates the restored state; returns False when no state file exists."""
        present = [self._path(name).exists() for name in _STATE_FILES]
        if not any(present):
            return False
        if not all(present):
            raise LocalMasterRecoveryError("local master state is incomplete after recovery")
        for master in (MasterType.ACCOUNTING, MasterType.BANKING):
            self._verify_integrity(master)
        self.read_audit_events()
        self.read_status_history()
        return True

    def recover(
        self,
        *,
        recovered_at: datetime,
        recovered_by: ActorSystem,
    ) -> list[UUID]:
        if recovered_at.tzinfo is None or recovered_at.utcoffset() != timedelta(0):
            raise LocalMasterRecoveryError("recovery timestamp must be UTC")
        recovery_dir = self._path("recovery")
        if not recovery_dir.exists():
            return []
        recovered: list[UUID] = []
        for manifest_path in sorted(recovery_dir.glob("*.json")):
            try:
                manifest = LocalTransactionManifest.model_validate_json(
                    manifest_path.read_bytes()
                )
            except Exception as exc:
                raise LocalMasterRecoveryError(
                    "transaction manifest is corrupt"
                ) from exc
            if manifest.phase in _RESOLVED_PHASES:
                continue
            try:
                self._restore_manifest(manifest)
                initialized = self._verify_recovered_state()

                events = self.read_audit_events() if initialized else []
                if initialized and not any(
                    event.metadata.get("recovered_manifest_id")
                    == str(manifest.transaction_manifest_id)
                    for event in events
                ):
                    events.append(
                        AuditEvent(
                            event_id=uuid4(),
                            occurred_at=recovered_at,
                            operation_id=manifest.operation_id,
                            resource_type=AuditResourceType.MASTER_TABLE,
                            resource_id=manifest.master_type.value,
                            action=AuditAction.WRITE_REJECTED,
                            actor_system=recovered_by,
                            reason="Recovered incomplete local transaction",
                            metadata={
                                "recovered_manifest_id": str(
                                    manifest.transaction_manifest_id
                                )
                            },
                        )
                    )
                    self._atomic_write(
                        self._path("audit/events.jsonl"),
                        self._jsonl_bytes(events),
                    )
                rolled_back = manifest.model_copy(
                    update={"phase": "ROLLED_BACK", "updated_at": recovered_at}
                )
                self._write_manifest(rolled_back)
                shutil.rmtree(
                    self._path(f"staging/{manifest.transaction_manifest_id}"),
                    ignore_errors=True,
                )
                shutil.rmtree(
                    self._path(f"recovery/{manifest.transaction_manifest_id}"),
                    ignore_errors=True,
                )
                recovered.append(manifest.transaction_manifest_id)
            except Exception as exc:
                try:
                    self._write_manifest(
                        manifest.model_copy(
                            update={"phase": "RECOVERY_REQUIRED", "updated_at": recovered_at}
                        )
                    )
                except Exception:
                    pass
                raise LocalMasterRecoveryError(
                    "local transaction recovery failed"
                ) from exc
        return recovered
