from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from schemas.banking_schema import (
    AllocationTargetType,
    BankAccountReference,
    BankAccountType,
    BankCounterparty,
    BankingRecord,
    BankTransactionDirection,
    BankTransactionType,
    PaymentBatch,
    PaymentBatchItem,
    PaymentBatchStatus,
    PaymentBatchType,
    PaymentItemStatus,
    PaymentMethod,
    ReconciliationAllocation,
    ReconciliationStatus,
)
from schemas.master_tables_schema import (
    AccountingDocumentMasterRow,
    ActorSystem,
    AuditAction,
    AuditEvent,
    AuditResourceType,
    BankTransactionMasterRow,
    DocumentDirection,
    DocumentMasterType,
    DuplicateOperationError,
    EvidenceSourceType,
    ExtractionStatus,
    FileMoveStatus,
    FileState,
    FiscalStatus,
    ImportBatch,
    ImportBatchStatus,
    IngestionStatus,
    LockExpiredError,
    LockOwnershipError,
    LockStatus,
    LockUnavailableError,
    MasterTableError,
    MasterType,
    MasterValidationStatus,
    MasterVersion,
    ProcessingStatus,
    ResourceLock,
    ResolutionStatus,
    SourceEvidence,
    StatusDomain,
    StatusHistory,
    VersionConflictError,
    can_acquire_lock,
    next_master_version,
    validate_write_preconditions,
)


NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=5)
HASH = "a" * 64
OPERATION_ID = UUID("18ca8e26-e036-426f-84d5-48a679da94a9")
BATCH_ID = UUID("965d35f4-8366-47ec-a7cb-6e80c7970c94")
EVIDENCE_ID = UUID("fb20445f-183b-4bb8-a29d-74cc93230567")
DOCUMENT_ID = UUID("fbe07b82-a5b0-436f-bce6-8487f25daf67")
ACCOUNT_ID = UUID("99a9a625-8742-4779-90a5-7b207af9cfd6")
TRANSACTION_ID = UUID("3a5115c1-8195-45a4-b3cd-0a38f15191ef")


def accounting_row_data(**overrides: Any) -> dict[str, Any]:
    return {
        "document_id": DOCUMENT_ID,
        "row_version": 1,
        "content_hash": HASH,
        "created_at": NOW,
        "created_by": ActorSystem.LOCAL,
        "updated_at": LATER,
        "updated_by": ActorSystem.LOCAL,
        "last_operation_id": OPERATION_ID,
        "import_batch_id": BATCH_ID,
        "idempotency_key": "accounting-import-001",
        "company_id": "company-1",
        "direction": DocumentDirection.PURCHASE,
        "document_type": DocumentMasterType.INVOICE,
        "supplier_resolution_status": ResolutionStatus.UNRESOLVED,
        "customer_resolution_status": ResolutionStatus.UNRESOLVED,
        "currency": "EUR",
        "net_amount": Decimal("80.00"),
        "tax_amount": Decimal("20.00"),
        "gross_amount": Decimal("100.00"),
        "paid_amount": Decimal("0.00"),
        "remaining_amount": Decimal("100.00"),
        "discount_amount": Decimal("0.00"),
        "rounding_tolerance": Decimal("0.01"),
        "fiscal_status": FiscalStatus.NOT_EVALUATED,
        "ingestion_status": IngestionStatus.IMPORTED,
        "extraction_status": ExtractionStatus.EXTRACTED,
        "validation_status": MasterValidationStatus.PENDING,
        "processing_status": ProcessingStatus.READY,
        "reconciliation_status": ReconciliationStatus.UNMATCHED,
        "current_file_state": FileState.INCOMING,
        "file_move_status": FileMoveStatus.NOT_REQUIRED,
        "source_evidence_ids": [EVIDENCE_ID],
        **overrides,
    }


def accounting_row(**overrides: Any) -> AccountingDocumentMasterRow:
    return AccountingDocumentMasterRow(**accounting_row_data(**overrides))


def banking_record(**overrides: Any) -> BankingRecord:
    return BankingRecord(
        transaction_id=TRANSACTION_ID,
        account=BankAccountReference(
            account_id=ACCOUNT_ID,
            company_id="company-1",
            account_type=BankAccountType.CHECKING,
        ),
        booking_date=date(2026, 10, 15),
        direction=BankTransactionDirection.CREDIT,
        transaction_type=BankTransactionType.TRANSFER,
        payment_method=PaymentMethod.TRANSFER,
        amount=Decimal("42500.00"),
        currency="EUR",
        raw_label="LCR REMITTANCE",
        reconciliation_status=ReconciliationStatus.UNMATCHED,
        allocations=[],
        unallocated_amount=Decimal("42500.00"),
        import_batch_id=BATCH_ID,
        source_evidence_ids=[EVIDENCE_ID],
        row_version=1,
        content_hash=HASH,
        created_at=NOW,
        updated_at=LATER,
        **overrides,
    )


def bank_row(**overrides: Any) -> BankTransactionMasterRow:
    values = {
        "transaction_id": TRANSACTION_ID,
        "banking_record": banking_record(),
        "row_version": 1,
        "content_hash": HASH,
        "created_at": NOW,
        "created_by": ActorSystem.LOCAL,
        "updated_at": LATER,
        "updated_by": ActorSystem.LOCAL,
        "last_operation_id": OPERATION_ID,
        "import_batch_id": BATCH_ID,
        "idempotency_key": "bank-import-001",
        "source_evidence_ids": [EVIDENCE_ID],
        "import_status": IngestionStatus.IMPORTED,
        "is_deleted": False,
    }
    values.update(overrides)
    return BankTransactionMasterRow(**values)


def import_batch(**overrides: Any) -> ImportBatch:
    values = {
        "batch_id": BATCH_ID,
        "master_type": MasterType.ACCOUNTING,
        "source_system": ActorSystem.LOCAL,
        "source_evidence_ids": [EVIDENCE_ID],
        "source_hash": HASH,
        "idempotency_key": "batch-operation-001",
        "base_master_version": 0,
        "started_at": NOW,
        "status": ImportBatchStatus.READY,
        "record_count": 1,
        "accepted_count": 1,
        "rejected_count": 0,
        "duplicate_count": 0,
        "operation_id": OPERATION_ID,
    }
    values.update(overrides)
    return ImportBatch(**values)


def lock(**overrides: Any) -> ResourceLock:
    values = {
        "lock_id": UUID("d560b1fb-6c85-4f0b-b46d-75b2694ebf10"),
        "resource_name": "ACCOUNTING",
        "owner_system": ActorSystem.LOCAL,
        "owner_instance_id": "local-instance-1",
        "operation_id": OPERATION_ID,
        "acquired_at": NOW,
        "heartbeat_at": NOW,
        "expires_at": LATER,
        "base_version": 3,
        "status": LockStatus.ACTIVE,
    }
    values.update(overrides)
    return ResourceLock(**values)


def master_version(**overrides: Any) -> MasterVersion:
    values = {
        "master_name": MasterType.ACCOUNTING,
        "current_version": 3,
        "content_hash": HASH,
        "last_operation_id": OPERATION_ID,
        "updated_at": NOW,
        "updated_by": ActorSystem.LOCAL,
        "applied_idempotency_keys": ["already-applied"],
    }
    values.update(overrides)
    return MasterVersion(**values)


def validate_write(
    *,
    active_lock: ResourceLock | None = None,
    version: MasterVersion | None = None,
    now: datetime = NOW + timedelta(minutes=1),
    owner_system: ActorSystem = ActorSystem.LOCAL,
    owner_instance_id: str = "local-instance-1",
    expected_base_version: int = 3,
    idempotency_key: str = "new-operation",
) -> None:
    validate_write_preconditions(
        lock=active_lock or lock(),
        now=now,
        owner_system=owner_system,
        owner_instance_id=owner_instance_id,
        expected_base_version=expected_base_version,
        master_version=version or master_version(),
        idempotency_key=idempotency_key,
    )


def test_accounting_document_master_row_is_valid() -> None:
    row = accounting_row()

    assert row.document_id == DOCUMENT_ID
    assert row.row_version == 1
    assert row.source_evidence_ids == [EVIDENCE_ID]
    assert row.remaining_amount == Decimal("100.00")


def test_bank_transaction_master_row_embeds_banking_record() -> None:
    row = bank_row()

    assert row.transaction_id == row.banking_record.transaction_id
    assert row.banking_record.amount == Decimal("42500.00")
    assert "allocations" not in row.model_dump()
    assert "payment_batch" not in row.model_dump()


def test_master_status_domains_are_independent() -> None:
    row = accounting_row(
        ingestion_status=IngestionStatus.IMPORTED,
        extraction_status=ExtractionStatus.NEEDS_REVIEW,
        validation_status=MasterValidationStatus.PENDING,
        processing_status=ProcessingStatus.READY,
        reconciliation_status=ReconciliationStatus.UNMATCHED,
        current_file_state=FileState.NEEDS_REVIEW,
        file_move_status=FileMoveStatus.PENDING,
    )

    assert row.ingestion_status == IngestionStatus.IMPORTED
    assert row.extraction_status == ExtractionStatus.NEEDS_REVIEW
    assert row.validation_status == MasterValidationStatus.PENDING
    assert row.processing_status == ProcessingStatus.READY
    assert row.current_file_state == FileState.NEEDS_REVIEW


def test_accounting_row_can_reference_source_evidence_and_email() -> None:
    row = accounting_row(
        source_evidence_ids=[EVIDENCE_ID],
        external_reference="EMAIL:message-1/attachment-2",
    )

    assert row.source_evidence_ids == [EVIDENCE_ID]
    assert row.external_reference == "EMAIL:message-1/attachment-2"


@pytest.mark.parametrize("row_version", [0, -1])
def test_nonpositive_row_version_is_rejected(row_version: int) -> None:
    with pytest.raises(ValidationError):
        accounting_row(row_version=row_version)


@pytest.mark.parametrize("digest", ["bad", "g" * 64, "a" * 63])
def test_invalid_content_hash_is_rejected(digest: str) -> None:
    with pytest.raises(ValidationError):
        accounting_row(content_hash=digest)


def test_soft_deletion_requires_complete_audit_fields() -> None:
    deleted_at = LATER + timedelta(minutes=1)
    deleted = accounting_row(
        is_deleted=True,
        deleted_at=deleted_at,
        deleted_by=ActorSystem.HUMAN,
        deletion_reason="Duplicate document",
        deletion_operation_id=UUID("2d918b40-148d-4616-b27d-2d53c632cfe5"),
    )

    assert deleted.is_deleted
    assert deleted.deleted_by == ActorSystem.HUMAN


def test_incomplete_soft_deletion_is_rejected() -> None:
    with pytest.raises(ValidationError):
        accounting_row(is_deleted=True, deleted_at=LATER)


def test_deletion_fields_are_forbidden_when_row_is_not_deleted() -> None:
    with pytest.raises(ValidationError):
        accounting_row(deletion_reason="Unexpected")


def test_restore_is_represented_by_a_new_non_deleted_row_version() -> None:
    deleted = accounting_row(
        is_deleted=True,
        deleted_at=LATER,
        deleted_by=ActorSystem.HUMAN,
        deletion_reason="Duplicate",
        deletion_operation_id=UUID("2d918b40-148d-4616-b27d-2d53c632cfe5"),
    )
    restored = AccountingDocumentMasterRow.model_validate(
        deleted.model_dump()
        | {
            "row_version": deleted.row_version + 1,
            "updated_at": LATER + timedelta(minutes=1),
            "is_deleted": False,
            "deleted_at": None,
            "deleted_by": None,
            "deletion_reason": None,
            "deletion_operation_id": None,
        }
    )

    assert restored.row_version == 2
    assert not restored.is_deleted


def test_batch_local_and_oracle_are_both_valid_proposals() -> None:
    local = import_batch(status=ImportBatchStatus.READY)
    oracle = import_batch(
        batch_id=UUID("54375b91-15d0-49ed-9a44-ae8d94ac4d6e"),
        source_system=ActorSystem.ORACLE,
        idempotency_key="oracle-proposal-001",
        status=ImportBatchStatus.READY,
    )

    assert local.status == oracle.status == ImportBatchStatus.READY
    assert local.source_system == ActorSystem.LOCAL
    assert oracle.source_system == ActorSystem.ORACLE


def test_import_batch_rejects_blank_idempotency_key() -> None:
    with pytest.raises(ValidationError):
        import_batch(idempotency_key=" ")


def test_import_batch_rejects_inconsistent_counters() -> None:
    with pytest.raises(ValidationError):
        import_batch(record_count=1, accepted_count=1, rejected_count=1)


def test_completed_import_batch_requires_completion_and_publication() -> None:
    with pytest.raises(ValidationError):
        import_batch(status=ImportBatchStatus.COMPLETED)


def test_failed_import_batch_requires_error_summary() -> None:
    with pytest.raises(ValidationError):
        import_batch(
            status=ImportBatchStatus.FAILED,
            completed_at=LATER,
        )


def test_completed_and_failed_import_batches_can_be_fully_described() -> None:
    completed = import_batch(
        status=ImportBatchStatus.COMPLETED,
        completed_at=LATER,
        published_master_version=4,
    )
    failed = import_batch(
        batch_id=UUID("54375b91-15d0-49ed-9a44-ae8d94ac4d6e"),
        idempotency_key="failed-import-001",
        status=ImportBatchStatus.FAILED,
        completed_at=LATER,
        error_summary="Input validation failed",
    )

    assert completed.published_master_version == 4
    assert failed.error_summary == "Input validation failed"


@pytest.mark.parametrize(
    ("source_type", "fields"),
    [
        (EvidenceSourceType.FILE, {"source_file_name": "source.pdf"}),
        (
            EvidenceSourceType.PDF_TEXT,
            {
                "source_file_name": "invoice.pdf",
                "source_file_uri": "onedrive://docs/1",
            },
        ),
        (EvidenceSourceType.PHOTO, {"source_file_name": "photo.jpg"}),
        (
            EvidenceSourceType.EMAIL,
            {"source_email_id": "mail-1", "source_attachment_id": "attachment-1"},
        ),
        (EvidenceSourceType.CSV, {"source_file_name": "transactions.csv", "row_number": 2}),
        (EvidenceSourceType.SCREENSHOT, {"source_file_name": "capture.png"}),
    ],
)
def test_source_evidence_types_and_remote_uri_are_representable(
    source_type: EvidenceSourceType,
    fields: dict[str, Any],
) -> None:
    evidence = SourceEvidence(
        evidence_id=EVIDENCE_ID,
        source_type=source_type,
        created_at=NOW,
        **fields,
    )

    assert evidence.source_type == source_type
    assert evidence.source_file_uri == fields.get("source_file_uri")


def test_audit_event_is_append_only_and_json_roundtrips() -> None:
    event = AuditEvent(
        event_id=UUID("2790f3fc-5155-4ef2-9bd7-73405b1c8ceb"),
        occurred_at=NOW,
        operation_id=OPERATION_ID,
        batch_id=BATCH_ID,
        resource_type=AuditResourceType.ACCOUNTING_DOCUMENT,
        resource_id=str(DOCUMENT_ID),
        action=AuditAction.IMPORTED,
        actor_system=ActorSystem.LOCAL,
        previous_version=0,
        new_version=1,
        before_hash=None,
        after_hash=HASH,
        changed_fields=["document_id", "gross_amount"],
        evidence_refs=[EVIDENCE_ID],
    )
    restored = AuditEvent.model_validate_json(event.model_dump_json())

    assert restored == event
    with pytest.raises(ValidationError):
        event.action = AuditAction.UPDATED


def test_status_history_records_transitions() -> None:
    history = StatusHistory(
        status_history_id=UUID("cfc252b4-e22e-45ba-90e3-c15138b586db"),
        resource_type=AuditResourceType.ACCOUNTING_DOCUMENT,
        resource_id=str(DOCUMENT_ID),
        status_domain=StatusDomain.VALIDATION,
        status_from="PENDING",
        status_to="VALIDATED",
        changed_at=NOW,
        changed_by=ActorSystem.HUMAN,
        reason="Reviewed",
        operation_id=OPERATION_ID,
        row_version_after_change=2,
    )

    assert StatusHistory.model_validate_json(history.model_dump_json()) == history


def test_status_history_rejects_identity_transition() -> None:
    with pytest.raises(ValidationError):
        StatusHistory(
            status_history_id=UUID("cfc252b4-e22e-45ba-90e3-c15138b586db"),
            resource_type=AuditResourceType.ACCOUNTING_DOCUMENT,
            resource_id=str(DOCUMENT_ID),
            status_domain=StatusDomain.VALIDATION,
            status_from="PENDING",
            status_to="PENDING",
            changed_at=NOW,
            changed_by=ActorSystem.HUMAN,
            operation_id=OPERATION_ID,
            row_version_after_change=2,
        )


def test_resource_locks_support_local_and_oracle_owners() -> None:
    local = lock()
    oracle = lock(
        lock_id=UUID("aa415aca-968d-4ea5-a831-4369bb80c314"),
        owner_system=ActorSystem.ORACLE,
        owner_instance_id="oracle-instance-1",
    )

    assert local.owner_system == ActorSystem.LOCAL
    assert oracle.owner_system == ActorSystem.ORACLE
    assert local.status == oracle.status == LockStatus.ACTIVE


def test_expired_lock_is_detected_and_can_be_acquired() -> None:
    expired_at = NOW + timedelta(seconds=30)
    expired_lock = lock(
        heartbeat_at=expired_at - timedelta(seconds=5),
        expires_at=expired_at,
    )

    assert expired_lock.is_expired(expired_at)
    assert can_acquire_lock(expired_lock, now=expired_at)
    assert not expired_lock.can_be_renewed(
        expired_at,
        ActorSystem.LOCAL,
        "local-instance-1",
    )


def test_active_nonexpired_lock_cannot_be_acquired() -> None:
    active = lock()

    assert not can_acquire_lock(active, now=NOW + timedelta(minutes=1))


def test_lock_renewal_returns_new_instance_without_mutating_original() -> None:
    original = lock()
    renewed = original.renew(
        NOW + timedelta(minutes=1),
        NOW + timedelta(minutes=10),
    )

    assert original.heartbeat_at == NOW
    assert renewed.heartbeat_at == NOW + timedelta(minutes=1)
    assert renewed.expires_at == NOW + timedelta(minutes=10)
    assert renewed is not original


def test_write_preconditions_reject_wrong_owner() -> None:
    with pytest.raises(LockOwnershipError):
        validate_write(owner_system=ActorSystem.ORACLE)


def test_write_preconditions_reject_expected_base_version_conflict() -> None:
    with pytest.raises(VersionConflictError):
        validate_write(expected_base_version=2)


def test_write_preconditions_reject_lock_base_version_conflict() -> None:
    with pytest.raises(VersionConflictError):
        validate_write(active_lock=lock(base_version=2))


def test_write_preconditions_reject_previously_applied_idempotency_key() -> None:
    with pytest.raises(DuplicateOperationError):
        validate_write(idempotency_key="already-applied")


def test_write_preconditions_reject_expired_or_inactive_lock() -> None:
    with pytest.raises(LockExpiredError):
        validate_write(
            active_lock=lock(
                heartbeat_at=NOW,
                expires_at=NOW + timedelta(seconds=30),
            ),
            now=NOW + timedelta(minutes=1),
        )
    with pytest.raises(LockUnavailableError):
        validate_write(active_lock=lock(status=LockStatus.RELEASED))
    with pytest.raises(LockExpiredError):
        validate_write(active_lock=lock(status=LockStatus.EXPIRED))


def test_local_and_oracle_proposals_coexist_but_only_lock_owner_can_publish() -> None:
    local_batch = import_batch(status=ImportBatchStatus.READY)
    oracle_batch = import_batch(
        batch_id=UUID("54375b91-15d0-49ed-9a44-ae8d94ac4d6e"),
        source_system=ActorSystem.ORACLE,
        idempotency_key="oracle-proposal-002",
        status=ImportBatchStatus.READY,
    )
    assert local_batch.status == oracle_batch.status == ImportBatchStatus.READY

    with pytest.raises(LockOwnershipError):
        validate_write(
            active_lock=lock(),
            owner_system=ActorSystem.ORACLE,
            owner_instance_id="oracle-instance-1",
        )


def test_next_master_version_increments_without_mutating_original() -> None:
    original = master_version(applied_idempotency_keys=[])
    updated = next_master_version(
        original,
        operation_id=UUID("3e8b9379-57ce-4abf-8502-11b28de438c7"),
        actor=ActorSystem.LOCAL,
        idempotency_key="next-operation",
        content_hash="b" * 64,
        updated_at=LATER,
        snapshot_uri="onedrive://masters/accounting/v4.xlsx",
    )

    assert original.current_version == 3
    assert original.applied_idempotency_keys == []
    assert updated.current_version == 4
    assert updated.applied_idempotency_keys == ["next-operation"]
    assert updated.last_snapshot_uri == "onedrive://masters/accounting/v4.xlsx"


def test_next_master_version_rejects_duplicate_operation() -> None:
    with pytest.raises(DuplicateOperationError):
        next_master_version(
            master_version(),
            operation_id=OPERATION_ID,
            actor=ActorSystem.LOCAL,
            idempotency_key="already-applied",
            content_hash=HASH,
            updated_at=LATER,
        )


def test_master_version_hash_and_utc_are_validated() -> None:
    with pytest.raises(ValidationError):
        master_version(content_hash="invalid")
    with pytest.raises(ValidationError):
        master_version(updated_at=datetime(2026, 10, 15, 12, 0))


def test_banking_row_keeps_one_lcr_batch_without_duplicate_allocations() -> None:
    lcr_batch = PaymentBatch(
        batch_id=UUID("3e5f20e6-b51d-4368-b1db-2008e276b73b"),
        batch_type=PaymentBatchType.LCR_REMITTANCE,
        booking_date=date(2026, 10, 15),
        total_amount=Decimal("42500.00"),
        currency="EUR",
        expected_item_count=10,
        items=[
            PaymentBatchItem(
                item_id=UUID(int=100 + index),
                counterparty=BankCounterparty(
                    counterparty_id=UUID(int=200 + index),
                    display_name=f"Customer {index + 1}",
                ),
                amount=Decimal("4250.00"),
                status=PaymentItemStatus.UNMATCHED,
                unallocated_amount=Decimal("4250.00"),
            )
            for index in range(10)
        ],
        status=PaymentBatchStatus.NEEDS_REVIEW,
        unexplained_amount=Decimal("0"),
        created_at=NOW,
        updated_at=LATER,
    )
    record = banking_record(payment_batch=lcr_batch)
    row = bank_row(banking_record=record)

    assert row.banking_record.payment_batch is not None
    assert len(row.banking_record.payment_batch.items) == 10
    assert "allocations" not in row.model_dump()
    assert "payment_batch" not in row.model_dump()


def test_master_rows_json_roundtrip_preserves_values_and_decimal() -> None:
    row = accounting_row()
    restored = AccountingDocumentMasterRow.model_validate_json(row.model_dump_json())

    assert restored == row
    assert restored.gross_amount == Decimal("100.00")
    assert str(restored.gross_amount) == "100.00"


def test_schema_models_do_not_create_files(tmp_path: Path) -> None:
    accounting_row()
    bank_row()
    evidence = SourceEvidence(
        evidence_id=EVIDENCE_ID,
        source_type=EvidenceSourceType.FILE,
        created_at=NOW,
    )

    assert evidence.evidence_id == EVIDENCE_ID
    assert list(tmp_path.iterdir()) == []
