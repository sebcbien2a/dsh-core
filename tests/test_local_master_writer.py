from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

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
    ChequeReference,
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
    DocumentPaymentMethod,
    DuplicateOperationError,
    ExtractionStatus,
    FileMoveStatus,
    FileState,
    FiscalStatus,
    ImportBatchStatus,
    IngestionStatus,
    LockExpiredError,
    LockOwnershipError,
    LockStatus,
    LockUnavailableError,
    MasterTableError,
    MasterType,
    MasterValidationStatus,
    PaymentContribution,
    PaymentOrigin,
    ProcessingStatus,
    ReconciliationStatus as MasterReconciliationStatus,
    ResolutionStatus,
    SettlementRoute,
    SettlementStatus,
    StatusDomain,
    StatusHistory,
    VersionConflictError,
)
from services import local_master_writer as writer_module
from services.local_master_writer import (
    LocalMasterAtomicWriteError,
    LocalMasterCorruptionError,
    LocalMasterInitializationError,
    LocalMasterRecoveryError,
    LocalMasterRowConflictError,
    LocalMasterWriteResult,
    LocalMasterWriter,
    LocalMasterWriterError,
    LocalTransactionManifest,
)


NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=5)
DOCUMENT_ID = UUID("4db5089c-b022-4466-8636-d048dff7ec69")
TRANSACTION_ID = UUID("a928d84f-f8da-4ec1-a57c-87cc8d767dad")
OPERATION_ID = UUID("9e79b190-fec9-46e3-a833-31316604eb22")
BATCH_ID = UUID("ed302d75-37f7-4c3b-89f4-12f36d9a8fb8")
HASH = "a" * 64


def accounting_row(
    *,
    document_id: UUID = DOCUMENT_ID,
    row_version: int = 1,
    idempotency_key: str = "accounting-row-1",
    content_hash: str = HASH,
    **overrides: Any,
) -> AccountingDocumentMasterRow:
    values: dict[str, Any] = {
        "document_id": document_id,
        "row_version": row_version,
        "content_hash": content_hash,
        "created_at": NOW,
        "created_by": ActorSystem.LOCAL,
        "updated_at": LATER,
        "updated_by": ActorSystem.LOCAL,
        "last_operation_id": OPERATION_ID,
        "import_batch_id": BATCH_ID,
        "idempotency_key": idempotency_key,
        "company_id": "company-1",
        "direction": DocumentDirection.PURCHASE,
        "document_type": DocumentMasterType.INVOICE,
        "supplier_resolution_status": ResolutionStatus.UNRESOLVED,
        "customer_resolution_status": ResolutionStatus.UNRESOLVED,
        "currency": "EUR",
        "net_amount": Decimal("80.00"),
        "tax_amount": Decimal("20.00"),
        "gross_amount": Decimal("100.00"),
        "paid_amount": Decimal("0"),
        "remaining_amount": Decimal("100.00"),
        "discount_amount": Decimal("0"),
        "rounding_tolerance": Decimal("0.01"),
        "fiscal_status": FiscalStatus.NOT_EVALUATED,
        "ingestion_status": IngestionStatus.IMPORTED,
        "extraction_status": ExtractionStatus.EXTRACTED,
        "validation_status": MasterValidationStatus.PENDING,
        "processing_status": ProcessingStatus.READY,
        "reconciliation_status": MasterReconciliationStatus.UNMATCHED,
        "current_file_state": FileState.INCOMING,
        "file_move_status": FileMoveStatus.NOT_REQUIRED,
    }
    values.update(overrides)
    return AccountingDocumentMasterRow(**values)


def banking_row(
    *,
    transaction_id: UUID = TRANSACTION_ID,
    row_version: int = 1,
    content_hash: str = HASH,
    **overrides: Any,
) -> BankTransactionMasterRow:
    record = BankingRecord(
        transaction_id=transaction_id,
        account=BankAccountReference(
            account_id=UUID("12a6f53a-3010-4b4f-884a-bc508977a924"),
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
        unallocated_amount=Decimal("42500.00"),
        import_batch_id=BATCH_ID,
        row_version=row_version,
        content_hash=content_hash,
        created_at=NOW,
        updated_at=LATER,
    )
    values: dict[str, Any] = {
        "transaction_id": transaction_id,
        "banking_record": record,
        "row_version": row_version,
        "content_hash": content_hash,
        "created_at": NOW,
        "created_by": ActorSystem.LOCAL,
        "updated_at": LATER,
        "updated_by": ActorSystem.LOCAL,
        "last_operation_id": OPERATION_ID,
        "import_batch_id": BATCH_ID,
        "idempotency_key": "bank-row-1",
        "import_status": IngestionStatus.IMPORTED,
    }
    values.update(overrides)
    return BankTransactionMasterRow(**values)


@pytest.fixture
def initialized_writer(tmp_path: Path) -> LocalMasterWriter:
    writer = LocalMasterWriter(tmp_path)
    writer.initialize(
        initialized_at=NOW,
        initialized_by=ActorSystem.LOCAL,
        operation_id=OPERATION_ID,
    )
    return writer


def acquire(
    writer: LocalMasterWriter,
    master_type: MasterType = MasterType.ACCOUNTING,
    *,
    owner: ActorSystem = ActorSystem.LOCAL,
) -> Any:
    return writer.acquire_lock(
        master_type,
        owner_system=owner,
        owner_instance_id=f"{owner.value.lower()}-instance",
        operation_id=uuid4(),
        acquired_at=LATER,
        expires_at=LATER + timedelta(hours=1),
    )


def publish_accounting(
    writer: LocalMasterWriter,
    rows: list[AccountingDocumentMasterRow],
    *,
    lock: Any | None = None,
    expected_version: int = 0,
    operation_id: UUID = OPERATION_ID,
    idempotency_key: str = "publish-accounting-1",
    committed_at: datetime = LATER + timedelta(minutes=1),
    audit_events: list[AuditEvent] | None = None,
    status_history: list[StatusHistory] | None = None,
) -> LocalMasterWriteResult:
    held = lock or acquire(writer)
    return writer.publish_accounting_rows(
        rows,
        lock_id=held.lock_id,
        owner_system=held.owner_system,
        owner_instance_id=held.owner_instance_id,
        expected_base_version=expected_version,
        operation_id=operation_id,
        idempotency_key=idempotency_key,
        committed_at=committed_at,
        audit_events=audit_events,
        status_history=status_history,
    )


def test_constructor_does_not_create_files(tmp_path: Path) -> None:
    LocalMasterWriter(tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_initialize_creates_both_masters_and_starts_at_zero(tmp_path: Path) -> None:
    writer = LocalMasterWriter(tmp_path)
    writer.initialize(
        initialized_at=NOW,
        initialized_by=ActorSystem.LOCAL,
        operation_id=OPERATION_ID,
    )

    assert writer.read_accounting_rows() == []
    assert writer.read_banking_rows() == []
    assert writer.read_master_version(MasterType.ACCOUNTING).current_version == 0
    assert writer.read_master_version(MasterType.BANKING).current_version == 0
    assert (tmp_path / "audit" / "events.jsonl").read_bytes() == b""
    assert not (tmp_path / "accounting" / "lock.json").exists()


def test_initialize_is_idempotent_and_refuses_partial_or_corrupt_state(
    tmp_path: Path,
) -> None:
    writer = LocalMasterWriter(tmp_path)
    writer.initialize(
        initialized_at=NOW,
        initialized_by=ActorSystem.LOCAL,
        operation_id=OPERATION_ID,
    )
    writer.initialize(
        initialized_at=NOW,
        initialized_by=ActorSystem.LOCAL,
        operation_id=OPERATION_ID,
    )
    assert writer.read_master_version(MasterType.ACCOUNTING).current_version == 0

    partial_root = tmp_path / "partial"
    (partial_root / "accounting").mkdir(parents=True)
    (partial_root / "accounting" / "rows.json").write_text("[]", encoding="utf-8")
    with pytest.raises(LocalMasterInitializationError):
        LocalMasterWriter(partial_root).initialize(
            initialized_at=NOW,
            initialized_by=ActorSystem.LOCAL,
            operation_id=OPERATION_ID,
        )

    (tmp_path / "accounting" / "rows.json").write_text("not-json", encoding="utf-8")
    with pytest.raises(LocalMasterInitializationError):
        writer.initialize(
            initialized_at=NOW,
            initialized_by=ActorSystem.LOCAL,
            operation_id=OPERATION_ID,
        )


def test_all_writer_files_stay_under_tmp_path(tmp_path: Path) -> None:
    writer = LocalMasterWriter(tmp_path / "local")
    writer.initialize(
        initialized_at=NOW,
        initialized_by=ActorSystem.LOCAL,
        operation_id=OPERATION_ID,
    )

    for item in (tmp_path / "local").rglob("*"):
        assert item.resolve().is_relative_to(tmp_path.resolve())


@pytest.mark.parametrize("owner", [ActorSystem.LOCAL, ActorSystem.ORACLE])
def test_lock_acquisition_is_audited_without_incrementing_master_version(
    initialized_writer: LocalMasterWriter,
    owner: ActorSystem,
) -> None:
    before = initialized_writer.read_master_version(MasterType.ACCOUNTING)
    held = acquire(initialized_writer, owner=owner)

    assert held.owner_system == owner
    assert initialized_writer.read_master_version(MasterType.ACCOUNTING) == before
    assert initialized_writer.read_audit_events()[-1].action == AuditAction.LOCK_ACQUIRED


def test_active_lock_refused_and_expired_lock_can_be_replaced(
    initialized_writer: LocalMasterWriter,
) -> None:
    first = acquire(initialized_writer)
    with pytest.raises(LockUnavailableError):
        acquire(initialized_writer)

    replacement_time = first.expires_at + timedelta(seconds=1)
    replacement = initialized_writer.acquire_lock(
        MasterType.ACCOUNTING,
        owner_system=ActorSystem.ORACLE,
        owner_instance_id="oracle-instance",
        operation_id=uuid4(),
        acquired_at=replacement_time,
        expires_at=replacement_time + timedelta(hours=1),
    )
    assert replacement.owner_system == ActorSystem.ORACLE


def test_lock_renew_release_and_owner_checks(
    initialized_writer: LocalMasterWriter,
) -> None:
    held = acquire(initialized_writer)
    renewed_at = held.acquired_at + timedelta(minutes=1)
    renewed = initialized_writer.renew_lock(
        MasterType.ACCOUNTING,
        lock_id=held.lock_id,
        owner_system=held.owner_system,
        owner_instance_id=held.owner_instance_id,
        now=renewed_at,
        new_expires_at=renewed_at + timedelta(hours=2),
    )
    assert renewed.heartbeat_at == renewed_at
    with pytest.raises(LockOwnershipError):
        initialized_writer.renew_lock(
            MasterType.ACCOUNTING,
            lock_id=held.lock_id,
            owner_system=ActorSystem.ORACLE,
            owner_instance_id="oracle-instance",
            now=renewed_at,
            new_expires_at=renewed_at + timedelta(hours=2),
        )

    with pytest.raises(LockOwnershipError):
        initialized_writer.release_lock(
            MasterType.ACCOUNTING,
            lock_id=uuid4(),
            owner_system=held.owner_system,
            owner_instance_id=held.owner_instance_id,
            released_at=renewed_at,
        )
    initialized_writer.release_lock(
        MasterType.ACCOUNTING,
        lock_id=held.lock_id,
        owner_system=held.owner_system,
        owner_instance_id=held.owner_instance_id,
        released_at=renewed_at,
    )
    assert initialized_writer.read_lock(MasterType.ACCOUNTING).status == LockStatus.RELEASED
    actions = [event.action for event in initialized_writer.read_audit_events()]
    assert actions == [
        AuditAction.LOCK_ACQUIRED,
        AuditAction.LOCK_RENEWED,
        AuditAction.LOCK_RELEASED,
    ]


@pytest.mark.parametrize("count", [1, 2])
def test_publish_one_and_multiple_accounting_rows_creates_audit_and_version(
    initialized_writer: LocalMasterWriter,
    count: int,
) -> None:
    rows = [
        accounting_row(),
        accounting_row(
            document_id=UUID("b6c4b33c-f4d4-4f58-a871-4f5a395777da"),
            idempotency_key="accounting-row-2",
        ),
    ][:count]
    result = publish_accounting(initialized_writer, rows)

    assert result.created_row_ids == [row.document_id for row in rows]
    assert result.previous_master_version == 0
    assert result.new_master_version == 1
    assert initialized_writer.read_master_version(MasterType.ACCOUNTING).current_version == 1
    assert len(initialized_writer.read_accounting_rows()) == count
    creation_events = [
        event
        for event in initialized_writer.read_audit_events()
        if event.resource_type == AuditResourceType.ACCOUNTING_DOCUMENT
    ]
    assert sorted(event.resource_id for event in creation_events) == sorted(
        str(row.document_id) for row in rows
    )
    assert all(event.action == AuditAction.IMPORTED for event in creation_events)
    assert {event.event_id for event in creation_events} <= set(result.audit_event_ids)


def test_same_version_and_hash_is_unchanged_and_replay_key_is_rejected(
    initialized_writer: LocalMasterWriter,
) -> None:
    row = accounting_row()
    publish_accounting(initialized_writer, [row])
    result = publish_accounting(
        initialized_writer,
        [row],
        expected_version=1,
        operation_id=uuid4(),
        idempotency_key="publish-accounting-2",
    )
    assert result.unchanged_row_ids == [DOCUMENT_ID]
    assert result.new_master_version == 2

    with pytest.raises(DuplicateOperationError):
        publish_accounting(
            initialized_writer,
            [],
            expected_version=2,
            operation_id=uuid4(),
            idempotency_key="publish-accounting-2",
            audit_events=[
                AuditEvent(
                    event_id=uuid4(),
                    occurred_at=LATER + timedelta(minutes=3),
                    operation_id=uuid4(),
                    resource_type=AuditResourceType.MASTER_TABLE,
                    resource_id="ACCOUNTING",
                    action=AuditAction.UPDATED,
                    actor_system=ActorSystem.LOCAL,
                )
            ],
        )


@pytest.mark.parametrize("version", [0, 3])
def test_regressive_or_skipped_row_version_is_rejected(
    initialized_writer: LocalMasterWriter,
    version: int,
) -> None:
    publish_accounting(initialized_writer, [accounting_row()])
    if version == 0:
        with pytest.raises(ValidationError):
            accounting_row(row_version=version)
        return
    with pytest.raises(LocalMasterRowConflictError):
        publish_accounting(
            initialized_writer,
            [accounting_row(row_version=version)],
            expected_version=1,
            operation_id=uuid4(),
            idempotency_key=f"bad-row-version-{version}",
        )


def test_row_version_regression_is_refused_by_the_writer_without_writing(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
) -> None:
    publish_accounting(initialized_writer, [accounting_row()])
    publish_accounting(
        initialized_writer,
        [accounting_row(row_version=2, content_hash="b" * 64, updated_at=COMMIT_1)],
        expected_version=1,
        operation_id=uuid4(),
        idempotency_key="to-version-2",
    )
    lock = acquire(initialized_writer)
    before = snapshot(tmp_path)

    with pytest.raises(LocalMasterRowConflictError, match="regression"):
        publish_accounting(
            initialized_writer,
            [accounting_row(row_version=1, content_hash="c" * 64)],
            lock=lock,
            expected_version=2,
            operation_id=uuid4(),
            idempotency_key="regression",
        )

    assert snapshot(tmp_path) == before


def test_same_row_version_with_different_hash_is_rejected(
    initialized_writer: LocalMasterWriter,
) -> None:
    publish_accounting(initialized_writer, [accounting_row()])
    with pytest.raises(LocalMasterRowConflictError):
        publish_accounting(
            initialized_writer,
            [accounting_row(content_hash="b" * 64)],
            expected_version=1,
            operation_id=uuid4(),
            idempotency_key="different-hash",
        )


def test_optimistic_version_lock_expiry_and_owner_are_enforced(
    initialized_writer: LocalMasterWriter,
) -> None:
    held = acquire(initialized_writer)
    with pytest.raises(VersionConflictError):
        initialized_writer.publish_accounting_rows(
            [accounting_row()],
            lock_id=held.lock_id,
            owner_system=held.owner_system,
            owner_instance_id=held.owner_instance_id,
            expected_base_version=8,
            operation_id=uuid4(),
            idempotency_key="version-conflict",
            committed_at=LATER + timedelta(minutes=1),
        )

    with pytest.raises(LockOwnershipError):
        initialized_writer.publish_accounting_rows(
            [accounting_row()],
            lock_id=held.lock_id,
            owner_system=ActorSystem.ORACLE,
            owner_instance_id="oracle-instance",
            expected_base_version=0,
            operation_id=uuid4(),
            idempotency_key="wrong-owner",
            committed_at=LATER + timedelta(minutes=1),
        )

    with pytest.raises(LockExpiredError):
        initialized_writer.publish_accounting_rows(
            [accounting_row()],
            lock_id=held.lock_id,
            owner_system=held.owner_system,
            owner_instance_id=held.owner_instance_id,
            expected_base_version=0,
            operation_id=uuid4(),
            idempotency_key="expired",
            committed_at=held.expires_at + timedelta(seconds=1),
        )


def test_updates_preserve_unrelated_rows_and_write_status_history(
    initialized_writer: LocalMasterWriter,
) -> None:
    first = accounting_row()
    second = accounting_row(
        document_id=UUID("b6c4b33c-f4d4-4f58-a871-4f5a395777da"),
        idempotency_key="accounting-row-2",
    )
    publish_accounting(initialized_writer, [first, second])

    updated = accounting_row(
        row_version=2,
        updated_at=LATER + timedelta(minutes=2),
        updated_by=ActorSystem.HUMAN,
        content_hash="b" * 64,
        validation_status=MasterValidationStatus.VALIDATED,
    )
    result = publish_accounting(
        initialized_writer,
        [updated],
        expected_version=1,
        operation_id=uuid4(),
        idempotency_key="update-first",
    )
    assert result.updated_row_ids == [DOCUMENT_ID]
    saved_rows = {row.document_id: row for row in initialized_writer.read_accounting_rows()}
    assert len(saved_rows) == 2
    assert saved_rows[DOCUMENT_ID].row_version == 2
    assert saved_rows[DOCUMENT_ID_2].model_dump(mode="json") == second.model_dump(mode="json")
    update_events = [
        event
        for event in initialized_writer.read_audit_events()
        if event.action == AuditAction.UPDATED
    ]
    assert [event.resource_id for event in update_events] == [str(DOCUMENT_ID)]
    assert update_events[0].event_id in result.audit_event_ids
    history = initialized_writer.read_status_history()
    assert len(history) == 1
    assert history[0].status_domain == StatusDomain.VALIDATION
    assert history[0].status_to == MasterValidationStatus.VALIDATED.value
    assert history[0].changed_by == ActorSystem.HUMAN


def test_soft_delete_is_audited_and_hidden_until_restored(
    initialized_writer: LocalMasterWriter,
) -> None:
    publish_accounting(initialized_writer, [accounting_row()])
    deleted = accounting_row(
        row_version=2,
        content_hash="b" * 64,
        updated_at=LATER + timedelta(minutes=2),
        is_deleted=True,
        deleted_at=LATER + timedelta(minutes=2),
        deleted_by=ActorSystem.HUMAN,
        deletion_reason="Duplicate",
        deletion_operation_id=uuid4(),
    )
    publish_accounting(
        initialized_writer,
        [deleted],
        expected_version=1,
        operation_id=uuid4(),
        idempotency_key="soft-delete",
    )
    assert initialized_writer.read_accounting_rows() == []
    assert initialized_writer.read_accounting_rows(include_deleted=True)[0].is_deleted
    assert AuditAction.SOFT_DELETED in [
        event.action for event in initialized_writer.read_audit_events()
    ]

    restored = accounting_row(
        row_version=3,
        content_hash="c" * 64,
        updated_at=LATER + timedelta(minutes=3),
    )
    publish_accounting(
        initialized_writer,
        [restored],
        expected_version=2,
        operation_id=uuid4(),
        idempotency_key="restore",
    )
    assert len(initialized_writer.read_accounting_rows()) == 1
    assert AuditAction.RESTORED in [
        event.action for event in initialized_writer.read_audit_events()
    ]


def test_personal_and_mixed_expenses_publish_without_bank_records(
    initialized_writer: LocalMasterWriter,
) -> None:
    from schemas.master_tables_schema import (
        DocumentPaymentMethod,
        PaymentContribution,
        PaymentOrigin,
        SettlementRoute,
        SettlementStatus,
    )

    personal = accounting_row(
        payment_contributions=[
            PaymentContribution(
                contribution_id=uuid4(),
                payment_origin=PaymentOrigin.PERSONAL_FUNDS,
                payment_method=DocumentPaymentMethod.PERSONAL_CARD,
                payer_name="Director",
                amount=Decimal("100.00"),
                company_bank_reconciliation_required=False,
                settlement_route=SettlementRoute.EXPENSE_CLAIM,
                settlement_status=SettlementStatus.PENDING,
            )
        ],
        total_personal_amount=Decimal("100.00"),
        paid_amount=Decimal("100.00"),
        remaining_amount=Decimal("0.00"),
        reconciliation_status=MasterReconciliationStatus.NOT_APPLICABLE,
    )
    publish_accounting(initialized_writer, [personal])
    assert initialized_writer.read_banking_rows() == []


def test_explicit_audit_only_write_is_allowed_and_appended(
    initialized_writer: LocalMasterWriter,
) -> None:
    event = AuditEvent(
        event_id=uuid4(),
        occurred_at=LATER + timedelta(minutes=1),
        operation_id=OPERATION_ID,
        resource_type=AuditResourceType.MASTER_TABLE,
        resource_id="ACCOUNTING",
        action=AuditAction.CONFLICT_DETECTED,
        actor_system=ActorSystem.LOCAL,
    )
    result = publish_accounting(
        initialized_writer,
        [],
        audit_events=[event],
    )
    assert result.audit_event_ids[0] == event.event_id
    assert initialized_writer.read_audit_events()[-2] == event


def test_banking_publication_keeps_one_lcr_line_with_ten_items(
    initialized_writer: LocalMasterWriter,
) -> None:
    batch = PaymentBatch(
        batch_id=uuid4(),
        batch_type=PaymentBatchType.LCR_REMITTANCE,
        booking_date=date(2026, 10, 15),
        total_amount=Decimal("42500.00"),
        currency="EUR",
        expected_item_count=10,
        items=[
            PaymentBatchItem(
                item_id=uuid4(),
                counterparty=BankCounterparty(
                    counterparty_id=uuid4(),
                    display_name=f"Customer {index}",
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
    record = banking_row().banking_record.model_copy(update={"payment_batch": batch})
    bank = banking_row(banking_record=record)
    lock = acquire(initialized_writer, MasterType.BANKING)
    result = initialized_writer.publish_banking_rows(
        [bank],
        lock_id=lock.lock_id,
        owner_system=lock.owner_system,
        owner_instance_id=lock.owner_instance_id,
        expected_base_version=0,
        operation_id=OPERATION_ID,
        idempotency_key="publish-lcr",
        committed_at=LATER + timedelta(minutes=1),
    )

    saved = initialized_writer.read_banking_rows()
    assert result.created_row_ids == [TRANSACTION_ID]
    assert len(saved) == 1
    assert len(saved[0].banking_record.payment_batch.items) == 10
    assert saved[0].banking_record.allocations == []


def test_corrupt_rows_version_and_audit_are_detected(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
) -> None:
    (tmp_path / "accounting" / "rows.json").write_text("{}", encoding="utf-8")
    with pytest.raises(LocalMasterCorruptionError):
        initialized_writer.read_accounting_rows()

    (tmp_path / "accounting" / "rows.json").write_text("[]", encoding="utf-8")
    (tmp_path / "accounting" / "master_version.json").write_text("{}", encoding="utf-8")
    with pytest.raises(LocalMasterCorruptionError):
        initialized_writer.read_master_version(MasterType.ACCOUNTING)

    (tmp_path / "audit" / "events.jsonl").write_text("{", encoding="utf-8")
    with pytest.raises(LocalMasterCorruptionError):
        initialized_writer.read_audit_events()


def test_state_hash_is_stable_independent_of_input_row_order(
    initialized_writer: LocalMasterWriter,
) -> None:
    first = accounting_row()
    second = accounting_row(
        document_id=UUID("b6c4b33c-f4d4-4f58-a871-4f5a395777da"),
        idempotency_key="accounting-row-2",
    )
    result_a = publish_accounting(initialized_writer, [first, second])
    other_root = initialized_writer._root.parent / "other"
    other = LocalMasterWriter(other_root)
    other.initialize(
        initialized_at=NOW,
        initialized_by=ActorSystem.LOCAL,
        operation_id=OPERATION_ID,
    )
    result_b = publish_accounting(other, [second, first])

    assert result_a.content_hash == result_b.content_hash


def test_input_models_are_not_mutated_and_manifest_paths_are_relative(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
) -> None:
    row = accounting_row()
    before = row.model_dump(mode="json")
    publish_accounting(initialized_writer, [row])

    assert row.model_dump(mode="json") == before
    manifests = list((tmp_path / "recovery").glob("*.json"))
    assert manifests
    for path in manifests:
        manifest = LocalTransactionManifest.model_validate_json(path.read_bytes())
        assert all(not Path(name).is_absolute() for name in manifest.target_files)
        assert all(not Path(name).is_absolute() for name in manifest.backup_files)
        assert all(":" not in name for name in manifest.target_files)


def test_pre_replace_failure_leaves_state_unchanged(
    initialized_writer: LocalMasterWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = acquire(initialized_writer)
    before = (initialized_writer._root / "accounting" / "rows.json").read_bytes()
    original_replace = writer_module.os.replace
    failed = False

    def fail_replace(source: Any, target: Any) -> None:
        nonlocal failed
        if not failed and Path(target).name == "rows.json":
            failed = True
            raise OSError("simulated failure")
        original_replace(source, target)

    monkeypatch.setattr(writer_module.os, "replace", fail_replace)
    with pytest.raises(LocalMasterAtomicWriteError):
        publish_accounting(initialized_writer, [accounting_row()], lock=lock)
    monkeypatch.setattr(writer_module.os, "replace", original_replace)
    after = (initialized_writer._root / "accounting" / "rows.json").read_bytes()
    assert after == before


def test_preparation_failure_cleans_staging_and_backup_directories(
    initialized_writer: LocalMasterWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = acquire(initialized_writer)
    original_write = initialized_writer._write_fsynced

    def fail_staging_write(path: Path, data: bytes) -> None:
        if "staging" in path.parts:
            raise OSError("simulated preparation failure")
        original_write(path, data)

    monkeypatch.setattr(initialized_writer, "_write_fsynced", fail_staging_write)
    with pytest.raises(LocalMasterAtomicWriteError):
        publish_accounting(initialized_writer, [accounting_row()], lock=lock)

    staging_root = initialized_writer._root / "staging"
    assert not staging_root.exists() or list(staging_root.iterdir()) == []
    recovery_dirs = [
        item
        for item in (initialized_writer._root / "recovery").iterdir()
        if item.is_dir()
    ]
    assert recovery_dirs == []


def test_failure_during_replace_rolls_back_all_final_files(
    initialized_writer: LocalMasterWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = acquire(initialized_writer)
    targets = [
        "accounting/rows.json",
        "accounting/master_version.json",
        "accounting/lock.json",
        "audit/events.jsonl",
        "audit/status_history.jsonl",
    ]
    before = {
        target: (initialized_writer._root / target).read_bytes()
        for target in targets
        if (initialized_writer._root / target).exists()
    }
    before_audit_events = initialized_writer.read_audit_events()
    original_replace = writer_module.os.replace
    failed = False

    def fail_once(source: Any, target: Any) -> None:
        nonlocal failed
        if not failed and Path(target).name == "master_version.json":
            failed = True
            raise OSError("simulated replace failure")
        original_replace(source, target)

    monkeypatch.setattr(writer_module.os, "replace", fail_once)
    with pytest.raises(LocalMasterAtomicWriteError):
        publish_accounting(initialized_writer, [accounting_row()], lock=lock)
    monkeypatch.setattr(writer_module.os, "replace", original_replace)

    for target, contents in before.items():
        assert (initialized_writer._root / target).read_bytes() == contents
    assert initialized_writer.read_audit_events() == before_audit_events


def test_recovery_rolls_back_noncommitted_manifest_and_is_idempotent(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
) -> None:
    manifest_id = uuid4()
    recovery = tmp_path / "recovery" / str(manifest_id)
    backup = recovery / "backup" / "accounting" / "rows.json"
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"[]")
    (tmp_path / "accounting" / "rows.json").write_text(
        '[{"corrupt":true}]',
        encoding="utf-8",
    )
    manifest = LocalTransactionManifest(
        transaction_manifest_id=manifest_id,
        master_type=MasterType.ACCOUNTING,
        operation_id=uuid4(),
        idempotency_key="interrupted",
        phase="REPLACING",
        created_at=NOW,
        updated_at=NOW,
        target_files=["accounting/rows.json"],
        backup_files=[f"recovery/{manifest_id}/backup/accounting/rows.json"],
        content_hashes={},
        metadata={"originally_missing": []},
    )
    (tmp_path / "recovery" / f"{manifest_id}.json").write_text(
        manifest.model_dump_json(),
        encoding="utf-8",
    )

    recovered = initialized_writer.recover(
        recovered_at=LATER,
        recovered_by=ActorSystem.LOCAL,
    )
    assert recovered == [manifest_id]
    assert initialized_writer.read_accounting_rows() == []
    assert initialized_writer.recover(
        recovered_at=LATER,
        recovered_by=ActorSystem.LOCAL,
    ) == []


def test_committed_manifest_is_never_restored(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
) -> None:
    manifest_id = uuid4()
    recovery = tmp_path / "recovery" / str(manifest_id)
    backup = recovery / "backup" / "accounting" / "rows.json"
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"[1]")
    manifest = LocalTransactionManifest(
        transaction_manifest_id=manifest_id,
        master_type=MasterType.ACCOUNTING,
        operation_id=uuid4(),
        idempotency_key="already-committed",
        phase="COMMITTED",
        created_at=NOW,
        updated_at=NOW,
        target_files=["accounting/rows.json"],
        backup_files=[f"recovery/{manifest_id}/backup/accounting/rows.json"],
        content_hashes={},
    )
    (tmp_path / "recovery" / f"{manifest_id}.json").write_text(
        manifest.model_dump_json(),
        encoding="utf-8",
    )
    before = (tmp_path / "accounting" / "rows.json").read_bytes()

    assert initialized_writer.recover(
        recovered_at=LATER,
        recovered_by=ActorSystem.LOCAL,
    ) == []
    assert (tmp_path / "accounting" / "rows.json").read_bytes() == before


def test_result_model_rejects_duplicate_identifiers() -> None:
    with pytest.raises(ValidationError):
        LocalMasterWriteResult(
            master_type=MasterType.ACCOUNTING,
            operation_id=OPERATION_ID,
            previous_master_version=0,
            new_master_version=1,
            created_row_ids=[DOCUMENT_ID],
            updated_row_ids=[DOCUMENT_ID],
            committed_at=LATER,
            content_hash=HASH,
            idempotency_key="operation",
        )


# ---------------------------------------------------------------------------
# Additional helpers
# ---------------------------------------------------------------------------

TRANSACTION_ID_2 = UUID("c0b1d3f2-6f52-4a43-9f0a-5e1c0c3b9d11")
DOCUMENT_ID_2 = UUID("b6c4b33c-f4d4-4f58-a871-4f5a395777da")
ACCOUNT_ID = UUID("12a6f53a-3010-4b4f-884a-bc508977a924")
HASH_B = "b" * 64
HASH_C = "c" * 64
COMMIT_1 = LATER + timedelta(minutes=1)
COMMIT_2 = LATER + timedelta(minutes=2)
COMMIT_3 = LATER + timedelta(minutes=3)


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def state_snapshot(root: Path) -> dict[str, bytes]:
    return {
        name: data
        for name, data in snapshot(root).items()
        if name.split("/")[0] in {"accounting", "banking", "audit"}
    }


def manifest_phases(root: Path) -> list[str]:
    return sorted(
        LocalTransactionManifest.model_validate_json(path.read_bytes()).phase
        for path in (root / "recovery").glob("*.json")
    )


def exception_chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def fail_replace_at(
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    before_fail: Any = None,
) -> None:
    original = writer_module.os.replace
    state = {"failed": False}

    def hook(source: Any, target: Any) -> None:
        if not state["failed"] and Path(target).name == filename:
            state["failed"] = True
            if before_fail is not None:
                before_fail()
            raise OSError("simulated replace failure")
        original(source, target)

    monkeypatch.setattr(writer_module.os, "replace", hook)


def publish_banking(
    writer: LocalMasterWriter,
    rows: list[BankTransactionMasterRow],
    *,
    lock: Any | None = None,
    expected_version: int = 0,
    idempotency_key: str = "publish-bank-1",
    committed_at: datetime = COMMIT_1,
    audit_events: list[AuditEvent] | None = None,
    status_history: list[StatusHistory] | None = None,
) -> LocalMasterWriteResult:
    held = lock or acquire(writer, MasterType.BANKING)
    return writer.publish_banking_rows(
        rows,
        lock_id=held.lock_id,
        owner_system=held.owner_system,
        owner_instance_id=held.owner_instance_id,
        expected_base_version=expected_version,
        operation_id=uuid4(),
        idempotency_key=idempotency_key,
        committed_at=committed_at,
        audit_events=audit_events,
        status_history=status_history,
    )


def bank_record(
    *,
    transaction_id: UUID = TRANSACTION_ID,
    row_version: int = 1,
    content_hash: str = HASH,
    **overrides: Any,
) -> BankingRecord:
    values: dict[str, Any] = {
        "transaction_id": transaction_id,
        "account": BankAccountReference(
            account_id=ACCOUNT_ID,
            company_id="company-1",
            account_type=BankAccountType.CHECKING,
        ),
        "booking_date": date(2026, 10, 15),
        "direction": BankTransactionDirection.CREDIT,
        "transaction_type": BankTransactionType.TRANSFER,
        "payment_method": PaymentMethod.TRANSFER,
        "amount": Decimal("42500.00"),
        "currency": "EUR",
        "raw_label": "BANK LINE",
        "unallocated_amount": Decimal("42500.00"),
        "import_batch_id": BATCH_ID,
        "row_version": row_version,
        "content_hash": content_hash,
        "created_at": NOW,
        "updated_at": LATER,
    }
    values.update(overrides)
    return BankingRecord(**values)


def bank_master(
    *,
    transaction_id: UUID = TRANSACTION_ID,
    row_version: int = 1,
    content_hash: str = HASH,
    idempotency_key: str = "bank-row-1",
    record_overrides: dict[str, Any] | None = None,
    **overrides: Any,
) -> BankTransactionMasterRow:
    record = bank_record(
        transaction_id=transaction_id,
        row_version=row_version,
        content_hash=content_hash,
        **(record_overrides or {}),
    )
    return banking_row(
        transaction_id=transaction_id,
        row_version=row_version,
        content_hash=content_hash,
        banking_record=record,
        idempotency_key=idempotency_key,
        **overrides,
    )


def payment_batch(
    batch_type: PaymentBatchType,
    amounts: list[str],
    *,
    batch_id: UUID,
    status: PaymentBatchStatus = PaymentBatchStatus.NEEDS_REVIEW,
) -> PaymentBatch:
    values = [Decimal(item) for item in amounts]
    return PaymentBatch(
        batch_id=batch_id,
        batch_type=batch_type,
        booking_date=date(2026, 10, 15),
        total_amount=sum(values, Decimal("0")),
        currency="EUR",
        expected_item_count=len(values),
        items=[
            PaymentBatchItem(
                item_id=uuid4(),
                amount=value,
                status=PaymentItemStatus.UNMATCHED,
                unallocated_amount=value,
            )
            for value in values
        ],
        status=status,
        unexplained_amount=Decimal("0"),
        created_at=NOW,
        updated_at=LATER,
    )


def company_contribution() -> PaymentContribution:
    return PaymentContribution(
        contribution_id=uuid4(),
        payment_origin=PaymentOrigin.COMPANY_ACCOUNT,
        payment_method=DocumentPaymentMethod.COMPANY_CARD,
        amount=Decimal("300.00"),
        company_bank_reconciliation_required=True,
        settlement_route=SettlementRoute.BANK_RECONCILIATION,
        settlement_status=SettlementStatus.NOT_REQUIRED,
    )


def personal_contribution(
    amount: str = "200.00",
    **overrides: Any,
) -> PaymentContribution:
    values: dict[str, Any] = {
        "contribution_id": uuid4(),
        "payment_origin": PaymentOrigin.PERSONAL_FUNDS,
        "payment_method": DocumentPaymentMethod.PERSONAL_CARD,
        "payer_name": "Director",
        "amount": Decimal(amount),
        "company_bank_reconciliation_required": False,
        "settlement_route": SettlementRoute.EXPENSE_CLAIM,
        "settlement_status": SettlementStatus.PENDING,
    }
    values.update(overrides)
    return PaymentContribution(**values)


def mixed_row(
    company: PaymentContribution,
    personal: PaymentContribution,
    **overrides: Any,
) -> AccountingDocumentMasterRow:
    values: dict[str, Any] = {
        "net_amount": Decimal("400.00"),
        "tax_amount": Decimal("100.00"),
        "gross_amount": Decimal("500.00"),
        "paid_amount": Decimal("500.00"),
        "remaining_amount": Decimal("0.00"),
        "payment_contributions": [company, personal],
        "total_personal_amount": Decimal("200.00"),
        "total_company_paid_amount": Decimal("300.00"),
    }
    values.update(overrides)
    return accounting_row(**values)


def personal_row(
    contribution: PaymentContribution,
    **overrides: Any,
) -> AccountingDocumentMasterRow:
    values: dict[str, Any] = {
        "gross_amount": Decimal("100.00"),
        "net_amount": Decimal("80.00"),
        "tax_amount": Decimal("20.00"),
        "paid_amount": Decimal("100.00"),
        "remaining_amount": Decimal("0.00"),
        "payment_contributions": [contribution],
        "total_personal_amount": Decimal("100.00"),
        "reconciliation_status": MasterReconciliationStatus.NOT_APPLICABLE,
    }
    values.update(overrides)
    return accounting_row(**values)


def deleted_fields(at: datetime) -> dict[str, Any]:
    return {
        "is_deleted": True,
        "deleted_at": at,
        "deleted_by": ActorSystem.HUMAN,
        "deletion_reason": "Duplicate",
        "deletion_operation_id": uuid4(),
    }


def make_audit_event(**overrides: Any) -> AuditEvent:
    values: dict[str, Any] = {
        "event_id": uuid4(),
        "occurred_at": COMMIT_1,
        "operation_id": OPERATION_ID,
        "resource_type": AuditResourceType.MASTER_TABLE,
        "resource_id": "ACCOUNTING",
        "action": AuditAction.CONFLICT_DETECTED,
        "actor_system": ActorSystem.LOCAL,
    }
    values.update(overrides)
    return AuditEvent(**values)


def make_status_history(**overrides: Any) -> StatusHistory:
    values: dict[str, Any] = {
        "status_history_id": uuid4(),
        "resource_type": AuditResourceType.ACCOUNTING_DOCUMENT,
        "resource_id": str(DOCUMENT_ID),
        "status_domain": StatusDomain.VALIDATION,
        "status_from": "PENDING",
        "status_to": "VALIDATED",
        "changed_at": COMMIT_1,
        "changed_by": ActorSystem.HUMAN,
        "operation_id": OPERATION_ID,
        "row_version_after_change": 1,
    }
    values.update(overrides)
    return StatusHistory(**values)


def write_interrupted_manifest(
    root: Path,
    phase: str,
    *,
    with_backup: bool = True,
) -> UUID:
    manifest_id = uuid4()
    backup_relative = f"recovery/{manifest_id}/backup/accounting/rows.json"
    if with_backup:
        backup = root.joinpath(*backup_relative.split("/"))
        backup.parent.mkdir(parents=True)
        backup.write_bytes(b"[]")
    (root / "accounting" / "rows.json").write_text('[{"partial": true}]', encoding="utf-8")
    manifest = LocalTransactionManifest(
        transaction_manifest_id=manifest_id,
        master_type=MasterType.ACCOUNTING,
        operation_id=uuid4(),
        idempotency_key=f"interrupted-{phase}",
        phase=phase,
        created_at=NOW,
        updated_at=NOW,
        target_files=["accounting/rows.json"],
        backup_files=[backup_relative],
        content_hashes={},
        metadata={"originally_missing": []},
    )
    (root / "recovery").mkdir(exist_ok=True)
    (root / "recovery" / f"{manifest_id}.json").write_text(
        manifest.model_dump_json(),
        encoding="utf-8",
    )
    return manifest_id


def read_manifest(root: Path, manifest_id: UUID) -> LocalTransactionManifest:
    return LocalTransactionManifest.model_validate_json(
        (root / "recovery" / f"{manifest_id}.json").read_bytes()
    )


# ---------------------------------------------------------------------------
# Accounting publication
# ---------------------------------------------------------------------------


def test_mixed_payment_300_company_200_personal_is_published_and_read_back(
    initialized_writer: LocalMasterWriter,
) -> None:
    company, personal = company_contribution(), personal_contribution()
    publish_accounting(initialized_writer, [mixed_row(company, personal)])

    saved = initialized_writer.read_accounting_rows()[0]
    assert [item.amount for item in saved.payment_contributions] == [
        Decimal("300.00"),
        Decimal("200.00"),
    ]
    assert saved.total_company_paid_amount == Decimal("300.00")
    assert saved.total_personal_amount == Decimal("200.00")
    assert saved.payment_contributions[0].company_bank_reconciliation_required is True
    assert saved.payment_contributions[1].company_bank_reconciliation_required is False


def test_personal_contribution_never_creates_a_bank_transaction(
    initialized_writer: LocalMasterWriter,
) -> None:
    publish_accounting(
        initialized_writer,
        [mixed_row(company_contribution(), personal_contribution())],
    )

    saved = initialized_writer.read_accounting_rows()[0]
    assert saved.payment_contributions[1].banking_transaction_id is None
    assert initialized_writer.read_banking_rows(include_deleted=True) == []
    assert initialized_writer.read_master_version(MasterType.BANKING).current_version == 0


def test_later_personal_reimbursement_is_kept_as_reference_without_execution(
    initialized_writer: LocalMasterWriter,
) -> None:
    contribution = personal_contribution("100.00")
    publish_accounting(initialized_writer, [personal_row(contribution)])
    reimbursement_id = uuid4()
    reimbursed = contribution.model_copy(
        update={
            "settlement_status": SettlementStatus.REIMBURSED,
            "reimbursement_transaction_id": reimbursement_id,
        }
    )
    publish_accounting(
        initialized_writer,
        [
            personal_row(
                reimbursed,
                row_version=2,
                content_hash=HASH_B,
                updated_at=COMMIT_1,
            )
        ],
        expected_version=1,
        idempotency_key="reimbursement",
    )

    saved_row = initialized_writer.read_accounting_rows()[0]
    saved = saved_row.payment_contributions[0]
    assert saved.settlement_status == SettlementStatus.REIMBURSED
    assert saved.reimbursement_transaction_id == reimbursement_id
    assert saved.amount == contribution.amount
    assert saved_row.paid_amount == Decimal("100.00")
    assert saved_row.remaining_amount == Decimal("0.00")
    assert initialized_writer.read_banking_rows(include_deleted=True) == []


@pytest.mark.parametrize(
    ("supplier", "method", "amount"),
    [
        ("McDonald's", DocumentPaymentMethod.PERSONAL_CARD, "18.40"),
        ("Leroy Merlin", DocumentPaymentMethod.PERSONAL_CASH, "64.90"),
    ],
    ids=["mcdonalds-personal-card", "leroy-merlin-personal-cash"],
)
def test_personal_purchase_is_published_with_not_applicable_reconciliation(
    initialized_writer: LocalMasterWriter,
    supplier: str,
    method: DocumentPaymentMethod,
    amount: str,
) -> None:
    contribution = personal_contribution(amount, payment_method=method)
    row = personal_row(
        contribution,
        supplier_name_observed=supplier,
        gross_amount=Decimal(amount),
        net_amount=None,
        tax_amount=None,
        remaining_amount=Decimal("0.00"),
        paid_amount=Decimal(amount),
        total_personal_amount=Decimal(amount),
    )

    result = publish_accounting(initialized_writer, [row])

    saved = initialized_writer.read_accounting_rows()
    assert result.created_row_ids == [DOCUMENT_ID]
    assert saved[0].supplier_name_observed == supplier
    assert saved[0].payment_contributions[0].payment_method == method
    assert saved[0].payment_contributions[0].payment_origin == PaymentOrigin.PERSONAL_FUNDS
    assert saved[0].reconciliation_status == MasterReconciliationStatus.NOT_APPLICABLE
    assert initialized_writer.read_banking_rows(include_deleted=True) == []


def test_accounting_update_preserves_payment_contributions(
    initialized_writer: LocalMasterWriter,
) -> None:
    company, personal = company_contribution(), personal_contribution()
    publish_accounting(initialized_writer, [mixed_row(company, personal)])
    before = initialized_writer.read_accounting_rows()[0].payment_contributions

    publish_accounting(
        initialized_writer,
        [
            mixed_row(
                company,
                personal,
                row_version=2,
                content_hash=HASH_B,
                updated_at=COMMIT_1,
                validation_status=MasterValidationStatus.VALIDATED,
            )
        ],
        expected_version=1,
        idempotency_key="update-mixed",
    )

    after = initialized_writer.read_accounting_rows()[0]
    assert after.row_version == 2
    assert after.payment_contributions == before


def test_deleted_personal_expense_can_be_restored_by_new_version(
    initialized_writer: LocalMasterWriter,
) -> None:
    contribution = personal_contribution("100.00")
    publish_accounting(initialized_writer, [personal_row(contribution)])
    publish_accounting(
        initialized_writer,
        [
            personal_row(
                contribution,
                row_version=2,
                content_hash=HASH_B,
                updated_at=COMMIT_1,
                **deleted_fields(COMMIT_1),
            )
        ],
        expected_version=1,
        idempotency_key="delete-personal",
    )
    assert initialized_writer.read_accounting_rows() == []

    publish_accounting(
        initialized_writer,
        [
            personal_row(
                contribution,
                row_version=3,
                content_hash=HASH_C,
                updated_at=COMMIT_2,
            )
        ],
        expected_version=2,
        idempotency_key="restore-personal",
        committed_at=COMMIT_2,
    )

    restored = initialized_writer.read_accounting_rows()[0]
    assert restored.is_deleted is False
    assert restored.payment_contributions == [contribution]
    assert restored.reconciliation_status == MasterReconciliationStatus.NOT_APPLICABLE


def test_supplied_audit_event_is_appended_exactly_once(
    initialized_writer: LocalMasterWriter,
) -> None:
    event = make_audit_event()
    result = publish_accounting(
        initialized_writer,
        [accounting_row()],
        audit_events=[event],
    )

    stored = [item for item in initialized_writer.read_audit_events() if item.event_id == event.event_id]
    assert stored == [event]
    assert result.audit_event_ids.count(event.event_id) == 1


def test_invalid_supplied_audit_event_is_refused_without_writing(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
) -> None:
    lock = acquire(initialized_writer)
    before = snapshot(tmp_path)

    with pytest.raises(LocalMasterRowConflictError):
        publish_accounting(
            initialized_writer,
            [accounting_row()],
            lock=lock,
            audit_events=[{"event_id": "not-an-event"}],  # type: ignore[list-item]
        )

    assert snapshot(tmp_path) == before


def test_supplied_status_history_is_appended(
    initialized_writer: LocalMasterWriter,
) -> None:
    history = make_status_history()
    result = publish_accounting(
        initialized_writer,
        [accounting_row()],
        status_history=[history],
    )

    assert initialized_writer.read_status_history() == [history]
    assert result.status_history_ids == [history.status_history_id]


def test_invalid_supplied_status_history_is_refused_without_writing(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
) -> None:
    lock = acquire(initialized_writer)
    before = snapshot(tmp_path)

    with pytest.raises(LocalMasterRowConflictError):
        publish_accounting(
            initialized_writer,
            [accounting_row()],
            lock=lock,
            status_history=[{"status_history_id": "bad"}],  # type: ignore[list-item]
        )

    assert snapshot(tmp_path) == before


def test_existing_audit_event_identifier_is_refused(
    initialized_writer: LocalMasterWriter,
) -> None:
    event = make_audit_event()
    publish_accounting(initialized_writer, [accounting_row()], audit_events=[event])

    with pytest.raises(LocalMasterRowConflictError, match="audit event identifier"):
        publish_accounting(
            initialized_writer,
            [],
            expected_version=1,
            idempotency_key="duplicate-event",
            audit_events=[event],
        )


def test_existing_status_history_identifier_is_refused(
    initialized_writer: LocalMasterWriter,
) -> None:
    history = make_status_history()
    publish_accounting(initialized_writer, [accounting_row()], status_history=[history])

    with pytest.raises(LocalMasterRowConflictError, match="status history identifier"):
        publish_accounting(
            initialized_writer,
            [accounting_row()],
            expected_version=1,
            idempotency_key="duplicate-history",
            status_history=[history],
        )


# ---------------------------------------------------------------------------
# Banking publication
# ---------------------------------------------------------------------------


def test_simple_bank_row_is_created(initialized_writer: LocalMasterWriter) -> None:
    result = publish_banking(initialized_writer, [bank_master()])

    saved = initialized_writer.read_banking_rows()
    assert result.created_row_ids == [TRANSACTION_ID]
    assert [row.transaction_id for row in saved] == [TRANSACTION_ID]
    assert saved[0].banking_record.amount == Decimal("42500.00")
    assert initialized_writer.read_master_version(MasterType.BANKING).current_version == 1
    assert initialized_writer.read_master_version(MasterType.ACCOUNTING).current_version == 0


def test_bank_row_update_n_to_n_plus_one_preserves_other_row_and_records_status(
    initialized_writer: LocalMasterWriter,
) -> None:
    first = bank_master(idempotency_key="bank-1")
    second = bank_master(
        transaction_id=TRANSACTION_ID_2,
        idempotency_key="bank-2",
    )
    publish_banking(initialized_writer, [first, second])
    other_before = next(
        row for row in initialized_writer.read_banking_rows() if row.transaction_id == TRANSACTION_ID_2
    ).model_dump(mode="json")

    updated = bank_master(
        row_version=2,
        content_hash=HASH_B,
        updated_at=COMMIT_1,
        record_overrides={"reconciliation_status": ReconciliationStatus.NEEDS_REVIEW},
    )
    result = publish_banking(
        initialized_writer,
        [updated],
        expected_version=1,
        idempotency_key="bank-update",
    )

    by_id = {row.transaction_id: row for row in initialized_writer.read_banking_rows()}
    assert result.updated_row_ids == [TRANSACTION_ID]
    assert by_id[TRANSACTION_ID].row_version == 2
    assert by_id[TRANSACTION_ID_2].model_dump(mode="json") == other_before
    history = initialized_writer.read_status_history()
    assert len(history) == 1
    assert history[0].resource_type == AuditResourceType.BANK_TRANSACTION
    assert history[0].status_domain == StatusDomain.RECONCILIATION
    assert (history[0].status_from, history[0].status_to) == ("UNMATCHED", "NEEDS_REVIEW")


def test_bank_row_keeps_multiple_allocations(
    initialized_writer: LocalMasterWriter,
) -> None:
    allocations = [
        ReconciliationAllocation(
            allocation_id=uuid4(),
            target_type=AllocationTargetType.INVOICE,
            target_id=uuid4(),
            allocated_amount=Decimal(amount),
            allocation_status=ReconciliationStatus.PROPOSED,
            created_at=NOW,
            created_by="LOCAL",
        )
        for amount in ("10000.00", "12500.00")
    ]
    row = bank_master(
        record_overrides={
            "allocations": allocations,
            "unallocated_amount": Decimal("20000.00"),
            "reconciliation_status": ReconciliationStatus.PARTIALLY_MATCHED,
        }
    )
    publish_banking(initialized_writer, [row])

    record = initialized_writer.read_banking_rows()[0].banking_record
    assert [item.allocated_amount for item in record.allocations] == [
        Decimal("10000.00"),
        Decimal("12500.00"),
    ]
    assert record.unallocated_amount == Decimal("20000.00")


def test_individual_cheque_is_kept(initialized_writer: LocalMasterWriter) -> None:
    row = bank_master(
        record_overrides={
            "transaction_type": BankTransactionType.CHEQUE,
            "payment_method": PaymentMethod.CHEQUE,
            "cheque": ChequeReference(cheque_number="0001234", issuer_name="Client"),
        }
    )
    publish_banking(initialized_writer, [row])

    cheque = initialized_writer.read_banking_rows()[0].banking_record.cheque
    assert cheque is not None
    assert (cheque.cheque_number, cheque.issuer_name) == ("0001234", "Client")


def test_cheque_deposit_with_several_cheques_stays_one_bank_line(
    initialized_writer: LocalMasterWriter,
) -> None:
    batch = payment_batch(
        PaymentBatchType.CHEQUE_DEPOSIT,
        ["10000.00", "12500.00", "20000.00"],
        batch_id=uuid4(),
    )
    row = bank_master(
        record_overrides={
            "transaction_type": BankTransactionType.CHEQUE_DEPOSIT,
            "payment_method": PaymentMethod.CHEQUE,
            "amount": batch.total_amount,
            "unallocated_amount": batch.total_amount,
            "payment_batch": batch,
            "cheque": ChequeReference(
                deposit_reference="REM-1",
                cheque_count=3,
                total_deposit_amount=batch.total_amount,
            ),
        }
    )
    publish_banking(initialized_writer, [row])

    saved = initialized_writer.read_banking_rows()
    assert len(saved) == 1
    assert saved[0].banking_record.cheque.cheque_count == 3
    assert len(saved[0].banking_record.payment_batch.items) == 3
    assert saved[0].banking_record.allocations == []


def test_payment_batch_status_change_is_recorded_in_history(
    initialized_writer: LocalMasterWriter,
) -> None:
    batch_id = uuid4()
    amounts = ["1000.00", "2000.00"]
    open_batch = payment_batch(PaymentBatchType.GROUPED_TRANSFER, amounts, batch_id=batch_id)
    rejected = payment_batch(
        PaymentBatchType.GROUPED_TRANSFER,
        amounts,
        batch_id=batch_id,
        status=PaymentBatchStatus.REJECTED,
    )

    def row(batch: PaymentBatch, **overrides: Any) -> BankTransactionMasterRow:
        return bank_master(
            record_overrides={
                "amount": batch.total_amount,
                "unallocated_amount": batch.total_amount,
                "payment_batch": batch,
            },
            **overrides,
        )

    publish_banking(initialized_writer, [row(open_batch)])
    publish_banking(
        initialized_writer,
        [row(rejected, row_version=2, content_hash=HASH_B, updated_at=COMMIT_1)],
        expected_version=1,
        idempotency_key="batch-rejected",
    )

    history = initialized_writer.read_status_history()
    assert len(history) == 1
    assert history[0].resource_type == AuditResourceType.PAYMENT_BATCH
    assert history[0].resource_id == str(batch_id)
    assert (history[0].status_from, history[0].status_to) == ("NEEDS_REVIEW", "REJECTED")


def test_identical_statuses_produce_no_status_history(
    initialized_writer: LocalMasterWriter,
) -> None:
    publish_banking(initialized_writer, [bank_master()])
    publish_banking(
        initialized_writer,
        [
            bank_master(
                row_version=2,
                content_hash=HASH_B,
                updated_at=COMMIT_1,
                metadata={"note": "label edited"},
            )
        ],
        expected_version=1,
        idempotency_key="no-status-change",
    )

    assert initialized_writer.read_banking_rows()[0].row_version == 2
    assert initialized_writer.read_status_history() == []


def test_bank_soft_delete_hides_row_and_restore_makes_it_visible(
    initialized_writer: LocalMasterWriter,
) -> None:
    publish_banking(initialized_writer, [bank_master()])
    publish_banking(
        initialized_writer,
        [
            bank_master(
                row_version=2,
                content_hash=HASH_B,
                updated_at=COMMIT_1,
                record_overrides={
                    "is_deleted": True,
                    "deleted_at": COMMIT_1,
                    "deleted_by": "HUMAN",
                    "deletion_reason": "Duplicate",
                },
                **deleted_fields(COMMIT_1),
            )
        ],
        expected_version=1,
        idempotency_key="bank-delete",
    )

    assert initialized_writer.read_banking_rows() == []
    assert len(initialized_writer.read_banking_rows(include_deleted=True)) == 1
    assert AuditAction.SOFT_DELETED in [e.action for e in initialized_writer.read_audit_events()]

    publish_banking(
        initialized_writer,
        [
            bank_master(
                row_version=3,
                content_hash=HASH_C,
                updated_at=COMMIT_2,
            )
        ],
        expected_version=2,
        idempotency_key="bank-restore",
        committed_at=COMMIT_2,
    )

    assert len(initialized_writer.read_banking_rows()) == 1
    assert AuditAction.RESTORED in [e.action for e in initialized_writer.read_audit_events()]


# ---------------------------------------------------------------------------
# Locks, versions and idempotency
# ---------------------------------------------------------------------------


def test_releasing_an_expired_lock_is_refused(
    initialized_writer: LocalMasterWriter,
) -> None:
    held = acquire(initialized_writer)
    with pytest.raises(LockExpiredError):
        initialized_writer.release_lock(
            MasterType.ACCOUNTING,
            lock_id=held.lock_id,
            owner_system=held.owner_system,
            owner_instance_id=held.owner_instance_id,
            released_at=held.expires_at + timedelta(seconds=1),
        )


def test_publication_with_wrong_lock_id_is_refused(
    initialized_writer: LocalMasterWriter,
) -> None:
    held = acquire(initialized_writer)
    with pytest.raises(LockOwnershipError):
        initialized_writer.publish_accounting_rows(
            [accounting_row()],
            lock_id=uuid4(),
            owner_system=held.owner_system,
            owner_instance_id=held.owner_instance_id,
            expected_base_version=0,
            operation_id=uuid4(),
            idempotency_key="wrong-lock-id",
            committed_at=COMMIT_1,
        )


def test_publication_with_released_lock_is_refused(
    initialized_writer: LocalMasterWriter,
) -> None:
    held = acquire(initialized_writer)
    publish_accounting(initialized_writer, [accounting_row()], lock=held)

    with pytest.raises(LockUnavailableError):
        publish_accounting(
            initialized_writer,
            [accounting_row()],
            lock=held,
            expected_version=1,
            idempotency_key="after-release",
        )


def test_lock_with_diverging_base_version_is_refused(
    initialized_writer: LocalMasterWriter,
) -> None:
    held = acquire(initialized_writer)
    diverged = held.model_copy(update={"base_version": 5})
    (initialized_writer._root / "accounting" / "lock.json").write_bytes(
        initialized_writer._canonical_bytes(diverged.model_dump(mode="json"))
    )

    with pytest.raises(VersionConflictError):
        publish_accounting(initialized_writer, [accounting_row()], lock=held)


def test_new_lock_can_be_acquired_after_release(
    initialized_writer: LocalMasterWriter,
) -> None:
    publish_accounting(initialized_writer, [accounting_row()])
    second = acquire(initialized_writer)

    assert second.status == LockStatus.ACTIVE
    assert second.base_version == 1


def test_acquiring_after_expiry_keeps_an_explicit_expiry_audit(
    initialized_writer: LocalMasterWriter,
) -> None:
    first = acquire(initialized_writer)
    later = first.expires_at + timedelta(hours=1)
    initialized_writer.acquire_lock(
        MasterType.ACCOUNTING,
        owner_system=ActorSystem.ORACLE,
        owner_instance_id="oracle-instance",
        operation_id=uuid4(),
        acquired_at=later,
        expires_at=later + timedelta(hours=1),
    )

    events = initialized_writer.read_audit_events()
    assert [event.action for event in events] == [
        AuditAction.LOCK_ACQUIRED,
        AuditAction.LOCK_EXPIRED,
        AuditAction.LOCK_ACQUIRED,
    ]
    assert events[1].actor_system == ActorSystem.LOCAL
    assert events[1].resource_type == AuditResourceType.LOCK


@pytest.mark.parametrize("master_type", [MasterType.ACCOUNTING, MasterType.BANKING])
def test_lock_operations_never_change_business_master_version(
    initialized_writer: LocalMasterWriter,
    master_type: MasterType,
) -> None:
    before = initialized_writer.read_master_version(master_type)
    held = acquire(initialized_writer, master_type)
    renewed_at = held.acquired_at + timedelta(minutes=1)
    initialized_writer.renew_lock(
        master_type,
        lock_id=held.lock_id,
        owner_system=held.owner_system,
        owner_instance_id=held.owner_instance_id,
        now=renewed_at,
        new_expires_at=renewed_at + timedelta(hours=1),
    )
    initialized_writer.release_lock(
        master_type,
        lock_id=held.lock_id,
        owner_system=held.owner_system,
        owner_instance_id=held.owner_instance_id,
        released_at=renewed_at,
    )

    assert initialized_writer.read_master_version(master_type) == before


@pytest.mark.parametrize("key", ["", "   "])
def test_blank_idempotency_key_is_refused(
    initialized_writer: LocalMasterWriter,
    key: str,
) -> None:
    with pytest.raises(MasterTableError, match="idempotency_key"):
        publish_accounting(initialized_writer, [accounting_row()], idempotency_key=key)
    assert initialized_writer.read_accounting_rows() == []


def test_applied_idempotency_key_modifies_no_file(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
) -> None:
    publish_accounting(initialized_writer, [accounting_row()], idempotency_key="applied-key")
    lock = acquire(initialized_writer)
    before = snapshot(tmp_path)

    with pytest.raises(DuplicateOperationError):
        publish_accounting(
            initialized_writer,
            [accounting_row(row_version=2, content_hash=HASH_B)],
            lock=lock,
            expected_version=1,
            idempotency_key="applied-key",
        )

    assert snapshot(tmp_path) == before


# ---------------------------------------------------------------------------
# Unchanged-only publication (strategy A, documented in _publish)
# ---------------------------------------------------------------------------


def test_unchanged_only_publication_creates_a_version_and_consumes_the_key(
    initialized_writer: LocalMasterWriter,
) -> None:
    row = accounting_row()
    publish_accounting(initialized_writer, [row])
    row_events_before = [
        event
        for event in initialized_writer.read_audit_events()
        if event.resource_type == AuditResourceType.ACCOUNTING_DOCUMENT
    ]
    rows_bytes = (initialized_writer._root / "accounting" / "rows.json").read_bytes()

    result = publish_accounting(
        initialized_writer,
        [row],
        expected_version=1,
        idempotency_key="no-op-key",
    )

    version = initialized_writer.read_master_version(MasterType.ACCOUNTING)
    assert result.unchanged_row_ids == [DOCUMENT_ID]
    assert result.created_row_ids == [] and result.updated_row_ids == []
    assert (result.previous_master_version, result.new_master_version) == (1, 2)
    assert version.current_version == 2
    assert "no-op-key" in version.applied_idempotency_keys
    assert (initialized_writer._root / "accounting" / "rows.json").read_bytes() == rows_bytes
    assert [
        event
        for event in initialized_writer.read_audit_events()
        if event.resource_type == AuditResourceType.ACCOUNTING_DOCUMENT
    ] == row_events_before
    assert initialized_writer.read_status_history() == []
    assert initialized_writer.read_lock(MasterType.ACCOUNTING).status == LockStatus.RELEASED

    with pytest.raises(DuplicateOperationError):
        publish_accounting(
            initialized_writer,
            [row],
            expected_version=2,
            idempotency_key="no-op-key",
        )


# ---------------------------------------------------------------------------
# Atomicity, rollback and recovery
# ---------------------------------------------------------------------------


def test_failure_after_rows_replaced_but_before_master_version_restores_everything(
    initialized_writer: LocalMasterWriter,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lock = acquire(initialized_writer)
    before = state_snapshot(tmp_path)
    seen: dict[str, bool] = {}

    def observe_partial_state() -> None:
        seen["rows_replaced"] = (tmp_path / "accounting" / "rows.json").read_bytes() != before[
            "accounting/rows.json"
        ]
        seen["version_untouched"] = (
            tmp_path / "accounting" / "master_version.json"
        ).read_bytes() == before["accounting/master_version.json"]

    fail_replace_at(monkeypatch, "master_version.json", observe_partial_state)
    with pytest.raises(LocalMasterAtomicWriteError):
        publish_accounting(
            initialized_writer,
            [accounting_row()],
            lock=lock,
            status_history=[make_status_history()],
            audit_events=[make_audit_event()],
        )

    assert seen == {"rows_replaced": True, "version_untouched": True}
    assert state_snapshot(tmp_path) == before
    assert initialized_writer.read_lock(MasterType.ACCOUNTING).lock_id == lock.lock_id
    assert initialized_writer.read_lock(MasterType.ACCOUNTING).status == LockStatus.ACTIVE
    assert initialized_writer.read_master_version(MasterType.ACCOUNTING).current_version == 0
    assert initialized_writer.read_status_history() == []
    assert len(initialized_writer.read_audit_events()) == 1  # lock acquisition only

    # The same lock and idempotency key remain usable after a clean rollback.
    result = publish_accounting(initialized_writer, [accounting_row()], lock=lock)
    assert result.new_master_version == 1


@pytest.mark.parametrize(
    "failing_file",
    ["rows.json", "events.jsonl", "status_history.jsonl", "lock.json", "master_version.json"],
)
def test_failure_at_each_final_file_rolls_back_and_cleans_up(
    initialized_writer: LocalMasterWriter,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failing_file: str,
) -> None:
    lock = acquire(initialized_writer)
    before = state_snapshot(tmp_path)
    fail_replace_at(monkeypatch, failing_file)

    with pytest.raises(LocalMasterAtomicWriteError):
        publish_accounting(initialized_writer, [accounting_row()], lock=lock)

    assert state_snapshot(tmp_path) == before
    staging = tmp_path / "staging"
    assert not staging.exists() or list(staging.iterdir()) == []
    assert [item for item in (tmp_path / "recovery").iterdir() if item.is_dir()] == []
    assert list(tmp_path.rglob(".local-master-*")) == []
    assert manifest_phases(tmp_path).count("ROLLED_BACK") == 1


def test_recover_restores_prepared_and_replacing_manifests_idempotently(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
) -> None:
    prepared = write_interrupted_manifest(tmp_path, "PREPARED")
    replacing = write_interrupted_manifest(tmp_path, "REPLACING")

    recovered = initialized_writer.recover(recovered_at=LATER, recovered_by=ActorSystem.LOCAL)

    assert sorted(recovered) == sorted([prepared, replacing])
    assert initialized_writer.read_accounting_rows() == []
    for manifest_id in (prepared, replacing):
        assert read_manifest(tmp_path, manifest_id).phase == "ROLLED_BACK"
        assert not (tmp_path / "recovery" / str(manifest_id)).exists()
    events_after_first = initialized_writer.read_audit_events()
    assert initialized_writer.recover(recovered_at=LATER, recovered_by=ActorSystem.LOCAL) == []
    assert initialized_writer.read_audit_events() == events_after_first
    recovered_ids = [event.metadata["recovered_manifest_id"] for event in events_after_first]
    assert sorted(recovered_ids) == sorted([str(prepared), str(replacing)])


def test_recover_with_missing_backup_marks_recovery_required_and_keeps_evidence(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
) -> None:
    manifest_id = uuid4()
    backup_dir = tmp_path / "recovery" / str(manifest_id) / "backup"
    (backup_dir / "accounting").mkdir(parents=True)
    kept = backup_dir / "accounting" / "rows.json"
    kept.write_bytes(b"[]")
    manifest = LocalTransactionManifest(
        transaction_manifest_id=manifest_id,
        master_type=MasterType.ACCOUNTING,
        operation_id=uuid4(),
        idempotency_key="missing-backup",
        phase="REPLACING",
        created_at=NOW,
        updated_at=NOW,
        target_files=["accounting/rows.json", "banking/rows.json"],
        backup_files=[f"recovery/{manifest_id}/backup/accounting/rows.json"],
        content_hashes={},
        metadata={"originally_missing": []},
    )
    (tmp_path / "recovery" / f"{manifest_id}.json").write_text(
        manifest.model_dump_json(),
        encoding="utf-8",
    )

    with pytest.raises(LocalMasterRecoveryError) as excinfo:
        initialized_writer.recover(recovered_at=LATER, recovered_by=ActorSystem.LOCAL)

    assert isinstance(excinfo.value.__cause__, LocalMasterRecoveryError)
    assert read_manifest(tmp_path, manifest_id).phase == "RECOVERY_REQUIRED"
    assert kept.exists()
    with pytest.raises(LocalMasterRecoveryError):
        initialized_writer.recover(recovered_at=LATER, recovered_by=ActorSystem.LOCAL)


def test_rollback_failure_raises_recovery_error_and_keeps_all_evidence(
    initialized_writer: LocalMasterWriter,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lock = acquire(initialized_writer)

    def destroy_one_backup() -> None:
        for path in tmp_path.glob("recovery/*/backup/accounting/rows.json"):
            path.unlink()

    fail_replace_at(monkeypatch, "master_version.json", destroy_one_backup)
    with pytest.raises(LocalMasterRecoveryError) as excinfo:
        publish_accounting(initialized_writer, [accounting_row()], lock=lock)

    assert any(
        isinstance(error, OSError) and "simulated replace failure" in str(error)
        for error in exception_chain(excinfo.value)
    )
    assert excinfo.value.__cause__ is not None
    assert "RECOVERY_REQUIRED" in manifest_phases(tmp_path)
    remaining_backups = list(tmp_path.glob("recovery/*/backup/**/*"))
    assert any(path.name == "master_version.json" for path in remaining_backups)
    assert any((tmp_path / "staging").rglob("master_version.json"))


# ---------------------------------------------------------------------------
# Corruption: never repaired silently
# ---------------------------------------------------------------------------

CORRUPTION_CASES = [
    ("accounting/rows.json", "{broken", lambda w: w.read_accounting_rows()),
    ("banking/rows.json", "{broken", lambda w: w.read_banking_rows()),
    ("accounting/master_version.json", "{broken", lambda w: w.read_master_version(MasterType.ACCOUNTING)),
    ("banking/master_version.json", "{broken", lambda w: w.read_master_version(MasterType.BANKING)),
    ("audit/events.jsonl", "{broken\n", lambda w: w.read_audit_events()),
    ("audit/status_history.jsonl", "{broken\n", lambda w: w.read_status_history()),
    ("accounting/lock.json", "{broken", lambda w: w.read_lock(MasterType.ACCOUNTING)),
    ("accounting/rows.json", '[{"unexpected": 1}]', lambda w: w.read_accounting_rows()),
    ("banking/rows.json", '[{"unexpected": 1}]', lambda w: w.read_banking_rows()),
    ("accounting/master_version.json", '{"a": 1}', lambda w: w.read_master_version(MasterType.ACCOUNTING)),
    ("banking/master_version.json", '{"a": 1}', lambda w: w.read_master_version(MasterType.BANKING)),
    ("audit/events.jsonl", '{"a": 1}\n', lambda w: w.read_audit_events()),
    ("audit/status_history.jsonl", '{"a": 1}\n', lambda w: w.read_status_history()),
    ("accounting/lock.json", '{"a": 1}', lambda w: w.read_lock(MasterType.ACCOUNTING)),
]


@pytest.mark.parametrize(
    ("relative", "content", "reader"),
    CORRUPTION_CASES,
    ids=[f"{case[0]}-{index}" for index, case in enumerate(CORRUPTION_CASES)],
)
def test_corrupt_or_incompatible_files_raise_and_are_not_repaired(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
    relative: str,
    content: str,
    reader: Any,
) -> None:
    target = tmp_path.joinpath(*relative.split("/"))
    target.write_text(content, encoding="utf-8")

    with pytest.raises(LocalMasterCorruptionError):
        reader(initialized_writer)

    assert target.read_text(encoding="utf-8") == content


def test_both_masters_roundtrip_exactly_with_decimals_dates_and_uuids(
    initialized_writer: LocalMasterWriter,
) -> None:
    accounting = mixed_row(
        company_contribution(),
        personal_contribution(),
        issue_date=date(2026, 10, 14),
    )
    batch = payment_batch(
        PaymentBatchType.GROUPED_TRANSFER,
        ["1000.50", "2000.25"],
        batch_id=BATCH_ID,
    )
    bank = bank_master(
        record_overrides={
            "amount": batch.total_amount,
            "unallocated_amount": batch.total_amount,
            "payment_batch": batch,
        }
    )
    publish_accounting(initialized_writer, [accounting])
    publish_banking(initialized_writer, [bank])

    saved_accounting = initialized_writer.read_accounting_rows()
    saved_bank = initialized_writer.read_banking_rows()
    assert saved_accounting == [accounting]
    assert saved_bank == [bank]

    assert [str(item.amount) for item in saved_accounting[0].payment_contributions] == [
        "300.00",
        "200.00",
    ]
    assert str(saved_accounting[0].gross_amount) == "500.00"
    assert str(saved_bank[0].banking_record.amount) == "3000.75"
    assert saved_accounting[0].issue_date == date(2026, 10, 14)
    assert saved_bank[0].banking_record.booking_date == date(2026, 10, 15)
    assert saved_accounting[0].created_at == NOW
    assert saved_accounting[0].document_id == DOCUMENT_ID
    assert isinstance(saved_accounting[0].document_id, UUID)
    assert isinstance(saved_bank[0].transaction_id, UUID)
    assert saved_bank[0].banking_record.payment_batch.batch_id == BATCH_ID


def test_no_file_is_created_outside_the_writer_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cwd, system_temp, root = tmp_path / "cwd", tmp_path / "system-temp", tmp_path / "root"
    cwd.mkdir()
    system_temp.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(writer_module.tempfile, "tempdir", str(system_temp))
    writer = fresh_writer(root)

    publish_accounting(writer, [accounting_row()])
    publish_banking(writer, [bank_master()])
    lock = acquire(writer)
    fail_replace_at(monkeypatch, "master_version.json")
    with pytest.raises(LocalMasterAtomicWriteError):
        publish_accounting(
            writer,
            [accounting_row(row_version=2, content_hash=HASH_B, updated_at=COMMIT_1)],
            lock=lock,
            expected_version=1,
            idempotency_key="rolled-back",
        )
    writer.recover(recovered_at=LATER, recovered_by=ActorSystem.LOCAL)

    assert sorted(item.name for item in tmp_path.iterdir()) == ["cwd", "root", "system-temp"]
    assert list(cwd.iterdir()) == []
    assert list(system_temp.iterdir()) == []
    assert any(root.rglob("*"))


def test_corrupt_manifest_raises_recovery_error_and_is_left_untouched(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "recovery" / f"{uuid4()}.json"
    manifest.write_text("{broken", encoding="utf-8")

    with pytest.raises(LocalMasterRecoveryError):
        initialized_writer.recover(recovered_at=LATER, recovered_by=ActorSystem.LOCAL)

    assert manifest.read_text(encoding="utf-8") == "{broken"


def test_publication_on_corrupt_rows_is_refused_without_repair(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
) -> None:
    lock = acquire(initialized_writer)
    (tmp_path / "accounting" / "rows.json").write_text("{broken", encoding="utf-8")

    with pytest.raises(LocalMasterCorruptionError):
        publish_accounting(initialized_writer, [accounting_row()], lock=lock)

    assert (tmp_path / "accounting" / "rows.json").read_text(encoding="utf-8") == "{broken"


# ---------------------------------------------------------------------------
# Hashes and paths
# ---------------------------------------------------------------------------


def fresh_writer(root: Path) -> LocalMasterWriter:
    writer = LocalMasterWriter(root)
    writer.initialize(
        initialized_at=NOW,
        initialized_by=ActorSystem.LOCAL,
        operation_id=OPERATION_ID,
    )
    return writer


def test_bank_state_hash_is_independent_of_input_order(tmp_path: Path) -> None:
    first = bank_master(idempotency_key="bank-1")
    second = bank_master(transaction_id=TRANSACTION_ID_2, idempotency_key="bank-2")
    writer_a, writer_b = fresh_writer(tmp_path / "a"), fresh_writer(tmp_path / "b")

    result_a = publish_banking(writer_a, [first, second])
    result_b = publish_banking(writer_b, [second, first])

    assert result_a.content_hash == result_b.content_hash
    assert (tmp_path / "a" / "banking" / "rows.json").read_bytes() == (
        tmp_path / "b" / "banking" / "rows.json"
    ).read_bytes()


def test_real_row_change_changes_global_hash_and_matches_version_file(
    initialized_writer: LocalMasterWriter,
) -> None:
    first = publish_accounting(initialized_writer, [accounting_row()])
    second = publish_accounting(
        initialized_writer,
        [accounting_row(row_version=2, content_hash=HASH_B, updated_at=COMMIT_1)],
        expected_version=1,
        idempotency_key="changed",
    )

    assert first.content_hash != second.content_hash
    version = initialized_writer.read_master_version(MasterType.ACCOUNTING)
    assert version.content_hash == second.content_hash
    rows_bytes = (initialized_writer._root / "accounting" / "rows.json").read_bytes()
    assert hashlib.sha256(rows_bytes).hexdigest() == version.content_hash
    assert version.current_version == second.new_master_version == 2


def test_json_keys_are_sorted_deterministically(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
) -> None:
    assert LocalMasterWriter._canonical_bytes({"b": 1, "a": {"d": 1, "c": 2}}) == (
        b'{"a":{"c":2,"d":1},"b":1}'
    )
    publish_accounting(initialized_writer, [accounting_row()])
    raw = (tmp_path / "accounting" / "rows.json").read_bytes()
    assert raw == json.dumps(
        json.loads(raw),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


@pytest.mark.parametrize(
    "bad_path",
    [
        "C:\\Users\\bob\\invoice.pdf",
        "D:/data/invoice.pdf",
        "\\\\server\\share\\invoice.pdf",
        "/home/bob/invoice.pdf",
    ],
    ids=["windows-backslash", "windows-slash", "unc", "posix"],
)
def test_absolute_local_paths_are_never_persisted(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
    bad_path: str,
) -> None:
    lock = acquire(initialized_writer)
    before = (tmp_path / "accounting" / "rows.json").read_bytes()

    with pytest.raises(LocalMasterWriterError):
        publish_accounting(
            initialized_writer,
            [accounting_row(current_file_uri=bad_path)],
            lock=lock,
        )

    assert (tmp_path / "accounting" / "rows.json").read_bytes() == before


def test_logical_onedrive_uri_is_allowed_and_no_root_path_is_persisted(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
) -> None:
    uri = "onedrive://dsh/Factures/2026/invoice.pdf"
    publish_accounting(initialized_writer, [accounting_row(current_file_uri=uri)])

    assert initialized_writer.read_accounting_rows()[0].current_file_uri == uri
    forbidden = {
        str(tmp_path),
        tmp_path.as_posix(),
        str(tmp_path).replace("\\", "\\\\"),
    }
    for relative, data in snapshot(tmp_path).items():
        text = data.decode("utf-8", errors="ignore")
        assert all(item not in text for item in forbidden), relative


def test_manifest_hashes_match_staged_data_and_paths_are_relative(
    initialized_writer: LocalMasterWriter,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lock = acquire(initialized_writer)
    fail_replace_at(monkeypatch, "master_version.json")
    monkeypatch.setattr(
        initialized_writer,
        "_restore_manifest",
        lambda manifest: (_ for _ in ()).throw(LocalMasterRecoveryError("blocked")),
    )

    with pytest.raises(LocalMasterRecoveryError):
        publish_accounting(initialized_writer, [accounting_row()], lock=lock)

    manifests = [
        LocalTransactionManifest.model_validate_json(path.read_bytes())
        for path in (tmp_path / "recovery").glob("*.json")
    ]
    pending = next(item for item in manifests if item.phase == "RECOVERY_REQUIRED")
    assert set(pending.content_hashes) == set(pending.target_files)
    for target in pending.target_files:
        staged = tmp_path / "staging" / str(pending.transaction_manifest_id) / "new" / target
        final = tmp_path.joinpath(*target.split("/"))
        data = staged.read_bytes() if staged.exists() else final.read_bytes()
        assert hashlib.sha256(data).hexdigest() == pending.content_hashes[target]
    for name in [*pending.target_files, *pending.backup_files]:
        assert not Path(name).is_absolute() and ":" not in name and "\\" not in name


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


def result_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "master_type": MasterType.ACCOUNTING,
        "operation_id": OPERATION_ID,
        "previous_master_version": 0,
        "new_master_version": 1,
        "created_row_ids": [DOCUMENT_ID],
        "committed_at": LATER,
        "content_hash": HASH,
        "idempotency_key": "operation",
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    "overrides",
    [
        {"previous_master_version": -1, "new_master_version": 0},
        {"new_master_version": 2},
        {"content_hash": "not-a-sha256"},
        {"committed_at": datetime(2026, 10, 15, 12, 0)},
        {"committed_at": datetime(2026, 10, 15, 12, 0, tzinfo=timezone(timedelta(hours=2)))},
        {"idempotency_key": "  "},
        {"created_row_ids": [DOCUMENT_ID], "updated_row_ids": [DOCUMENT_ID]},
        {"created_row_ids": [DOCUMENT_ID], "audit_event_ids": [DOCUMENT_ID]},
        {"unexpected": True},
    ],
    ids=[
        "negative-version",
        "version-jump",
        "invalid-hash",
        "naive-datetime",
        "non-utc-datetime",
        "blank-idempotency-key",
        "duplicate-created-updated",
        "duplicate-row-audit",
        "extra-field",
    ],
)
def test_result_model_rejects_invalid_values(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        LocalMasterWriteResult(**result_values(**overrides))


def test_result_model_json_roundtrip_is_lossless() -> None:
    result = LocalMasterWriteResult(
        **result_values(
            updated_row_ids=[DOCUMENT_ID_2],
            audit_event_ids=[uuid4()],
            status_history_ids=[uuid4()],
        )
    )

    assert LocalMasterWriteResult.model_validate_json(result.model_dump_json()) == result


# ---------------------------------------------------------------------------
# Argon audit counter-validation
# ---------------------------------------------------------------------------

REQUIRED_STATE_FILES = [
    "accounting/rows.json",
    "accounting/master_version.json",
    "banking/rows.json",
    "banking/master_version.json",
    "audit/events.jsonl",
    "audit/status_history.jsonl",
]


class SimulatedCrash(BaseException):
    """Stands for a killed process: 'except Exception' blocks must not catch it."""


def row_for(master_type: MasterType, **overrides: Any) -> Any:
    if master_type == MasterType.ACCOUNTING:
        return accounting_row(**overrides)
    return bank_master(**overrides)


def publish_for(
    master_type: MasterType,
    writer: LocalMasterWriter,
    rows: list[Any],
    **kwargs: Any,
) -> LocalMasterWriteResult:
    if master_type == MasterType.ACCOUNTING:
        return publish_accounting(writer, rows, **kwargs)
    return publish_banking(writer, rows, **kwargs)


def initialize_writer(writer: LocalMasterWriter) -> None:
    writer.initialize(
        initialized_at=NOW,
        initialized_by=ActorSystem.LOCAL,
        operation_id=OPERATION_ID,
    )


def write_pending_manifest(root: Path, phase: str) -> UUID:
    """Writes an unresolved manifest whose backup equals the current rows file."""
    manifest_id = uuid4()
    backup_relative = f"recovery/{manifest_id}/backup/accounting/rows.json"
    backup = root.joinpath(*backup_relative.split("/"))
    backup.parent.mkdir(parents=True)
    backup.write_bytes((root / "accounting" / "rows.json").read_bytes())
    manifest = LocalTransactionManifest(
        transaction_manifest_id=manifest_id,
        master_type=MasterType.ACCOUNTING,
        operation_id=uuid4(),
        idempotency_key=f"pending-{phase}",
        phase=phase,
        created_at=NOW,
        updated_at=NOW,
        target_files=["accounting/rows.json"],
        backup_files=[backup_relative],
        content_hashes={},
        metadata={"originally_missing": []},
    )
    (root / "recovery").mkdir(exist_ok=True)
    (root / "recovery" / f"{manifest_id}.json").write_text(
        manifest.model_dump_json(),
        encoding="utf-8",
    )
    return manifest_id


def crash_replace_once(
    monkeypatch: pytest.MonkeyPatch,
    matches: Any,
) -> None:
    original = writer_module.os.replace
    state = {"crashed": False}

    def hook(source: Any, target: Any) -> None:
        if not state["crashed"] and matches(Path(target)):
            state["crashed"] = True
            raise SimulatedCrash()
        original(source, target)

    monkeypatch.setattr(writer_module.os, "replace", hook)


# B. Unresolved manifests block new transactions ------------------------------


@pytest.mark.parametrize("phase", ["PREPARED", "REPLACING", "RECOVERY_REQUIRED"])
def test_unresolved_manifest_blocks_every_new_transaction(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
    phase: str,
) -> None:
    lock = acquire(initialized_writer)
    manifest_id = write_pending_manifest(tmp_path, phase)
    before = snapshot(tmp_path)

    with pytest.raises(LocalMasterRecoveryError):
        publish_accounting(initialized_writer, [accounting_row()], lock=lock)
    with pytest.raises(LocalMasterRecoveryError):
        acquire(initialized_writer, MasterType.BANKING)
    with pytest.raises(LocalMasterRecoveryError):
        initialized_writer.release_lock(
            MasterType.ACCOUNTING,
            lock_id=lock.lock_id,
            owner_system=lock.owner_system,
            owner_instance_id=lock.owner_instance_id,
            released_at=COMMIT_1,
        )

    assert snapshot(tmp_path) == before
    assert read_manifest(tmp_path, manifest_id).phase == phase


@pytest.mark.parametrize("phase", ["COMMITTED", "ROLLED_BACK"])
def test_resolved_manifests_do_not_block_transactions(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
    phase: str,
) -> None:
    write_pending_manifest(tmp_path, phase)

    result = publish_accounting(initialized_writer, [accounting_row()])

    assert result.new_master_version == 1
    assert initialized_writer.recover(recovered_at=LATER, recovered_by=ActorSystem.LOCAL) == []
    assert [row.document_id for row in initialized_writer.read_accounting_rows()] == [
        DOCUMENT_ID
    ]


def test_old_recovery_cannot_overwrite_a_later_publication(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
) -> None:
    lock = acquire(initialized_writer)
    manifest_id = write_pending_manifest(tmp_path, "REPLACING")
    with pytest.raises(LocalMasterRecoveryError):
        publish_accounting(initialized_writer, [accounting_row()], lock=lock)

    assert initialized_writer.recover(
        recovered_at=LATER,
        recovered_by=ActorSystem.LOCAL,
    ) == [manifest_id]
    result = publish_accounting(initialized_writer, [accounting_row()], lock=lock)

    assert result.new_master_version == 1
    assert initialized_writer.recover(recovered_at=LATER, recovered_by=ActorSystem.LOCAL) == []
    assert [row.document_id for row in initialized_writer.read_accounting_rows()] == [
        DOCUMENT_ID
    ]
    assert read_manifest(tmp_path, manifest_id).phase == "ROLLED_BACK"


# C. Interrupted initialize ----------------------------------------------------


@pytest.mark.parametrize("phase", ["PREPARED", "REPLACING"])
def test_interrupted_initialize_is_recovered_to_the_uninitialized_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    writer = LocalMasterWriter(tmp_path)
    manifest_writes = {"count": 0}

    def second_manifest_write(target: Path) -> bool:
        if target.parent.name == "recovery" and target.suffix == ".json":
            manifest_writes["count"] += 1
            return manifest_writes["count"] == 2
        return False

    def accounting_version_replace(target: Path) -> bool:
        return target.parent.name == "accounting" and target.name == "master_version.json"

    crash_replace_once(
        monkeypatch,
        second_manifest_write if phase == "PREPARED" else accounting_version_replace,
    )
    with pytest.raises(SimulatedCrash):
        initialize_writer(writer)
    monkeypatch.undo()

    assert manifest_phases(tmp_path) == [phase]
    assert (tmp_path / "accounting" / "rows.json").exists() is (phase == "REPLACING")
    with pytest.raises(LocalMasterInitializationError):
        initialize_writer(writer)

    recovered = writer.recover(recovered_at=LATER, recovered_by=ActorSystem.LOCAL)

    assert len(recovered) == 1
    assert manifest_phases(tmp_path) == ["ROLLED_BACK"]
    assert [name for name in REQUIRED_STATE_FILES if (tmp_path / name).exists()] == []
    initialize_writer(writer)
    assert writer.read_master_version(MasterType.ACCOUNTING).current_version == 0
    assert writer.read_master_version(MasterType.BANKING).current_version == 0
    assert writer.recover(recovered_at=LATER, recovered_by=ActorSystem.LOCAL) == []


# D. Operation timestamps before the lock timestamps ---------------------------


@pytest.mark.parametrize("action", ["release", "publish"])
@pytest.mark.parametrize("reference", ["acquired_at", "heartbeat_at"])
def test_operation_before_lock_timestamps_is_refused_before_writing(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
    action: str,
    reference: str,
) -> None:
    held = acquire(initialized_writer)
    if reference == "heartbeat_at":
        renewed_at = held.acquired_at + timedelta(minutes=10)
        initialized_writer.renew_lock(
            MasterType.ACCOUNTING,
            lock_id=held.lock_id,
            owner_system=held.owner_system,
            owner_instance_id=held.owner_instance_id,
            now=renewed_at,
            new_expires_at=renewed_at + timedelta(hours=2),
        )
        operation_time = held.acquired_at + timedelta(minutes=5)
    else:
        operation_time = held.acquired_at - timedelta(minutes=5)
    before = snapshot(tmp_path)

    with pytest.raises(MasterTableError):
        if action == "release":
            initialized_writer.release_lock(
                MasterType.ACCOUNTING,
                lock_id=held.lock_id,
                owner_system=held.owner_system,
                owner_instance_id=held.owner_instance_id,
                released_at=operation_time,
            )
        else:
            publish_accounting(
                initialized_writer,
                [accounting_row()],
                lock=held,
                committed_at=operation_time,
            )

    assert snapshot(tmp_path) == before
    current = initialized_writer.read_lock(MasterType.ACCOUNTING)
    assert current is not None
    assert current.lock_id == held.lock_id
    assert current.status == LockStatus.ACTIVE


# E. Same version and hash, different payload; N -> N+1 rules ----------------


@pytest.mark.parametrize("master_type", [MasterType.ACCOUNTING, MasterType.BANKING])
def test_same_version_and_hash_with_a_different_payload_is_refused(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
    master_type: MasterType,
) -> None:
    publish_for(master_type, initialized_writer, [row_for(master_type)])
    lock = acquire(initialized_writer, master_type)
    before = snapshot(tmp_path)
    changed = (
        row_for(master_type, business_purpose="Changed after publication")
        if master_type == MasterType.ACCOUNTING
        else row_for(master_type, metadata={"note": "changed after publication"})
    )

    with pytest.raises(LocalMasterRowConflictError, match="payload"):
        publish_for(
            master_type,
            initialized_writer,
            [changed],
            lock=lock,
            expected_version=1,
            idempotency_key="payload-changed",
        )

    assert snapshot(tmp_path) == before


def test_new_row_version_must_change_the_content_hash(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
) -> None:
    publish_accounting(initialized_writer, [accounting_row()])
    lock = acquire(initialized_writer)
    before = snapshot(tmp_path)

    with pytest.raises(LocalMasterRowConflictError, match="content_hash"):
        publish_accounting(
            initialized_writer,
            [accounting_row(row_version=2, updated_at=COMMIT_1, business_purpose="Edited")],
            lock=lock,
            expected_version=1,
            idempotency_key="same-hash-new-version",
        )

    assert snapshot(tmp_path) == before


# L. Creation metadata and updated_at monotonicity -----------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [("created_at", NOW + timedelta(minutes=1)), ("created_by", ActorSystem.HUMAN)],
)
def test_update_cannot_change_creation_metadata(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    publish_accounting(initialized_writer, [accounting_row()])
    lock = acquire(initialized_writer)
    before = snapshot(tmp_path)

    with pytest.raises(LocalMasterRowConflictError, match="creation"):
        publish_accounting(
            initialized_writer,
            [
                accounting_row(
                    row_version=2,
                    content_hash=HASH_B,
                    updated_at=COMMIT_1,
                    **{field: value},
                )
            ],
            lock=lock,
            expected_version=1,
            idempotency_key=f"change-{field}",
        )

    assert snapshot(tmp_path) == before


def test_update_cannot_move_updated_at_backwards(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
) -> None:
    publish_accounting(initialized_writer, [accounting_row()])
    lock = acquire(initialized_writer)
    before = snapshot(tmp_path)

    with pytest.raises(LocalMasterRowConflictError, match="updated_at"):
        publish_accounting(
            initialized_writer,
            [accounting_row(row_version=2, content_hash=HASH_B, updated_at=NOW)],
            lock=lock,
            expected_version=1,
            idempotency_key="updated-at-backwards",
        )

    assert snapshot(tmp_path) == before


# F. rows.json / MasterVersion integrity ----------------------------------------

TAMPERED_FIELDS = {
    MasterType.ACCOUNTING: ("business_purpose", "tampered"),
    MasterType.BANKING: ("metadata", {"note": "tampered"}),
}


@pytest.mark.parametrize("master_type", [MasterType.ACCOUNTING, MasterType.BANKING])
def test_rows_diverging_from_the_master_version_hash_are_detected(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
    master_type: MasterType,
) -> None:
    publish_for(master_type, initialized_writer, [row_for(master_type)])
    lock = acquire(initialized_writer, master_type)
    field, value = TAMPERED_FIELDS[master_type]
    rows_path = tmp_path / LocalMasterWriter._master_name(master_type) / "rows.json"
    rows = json.loads(rows_path.read_text(encoding="utf-8"))
    rows[0][field] = value
    rows_path.write_bytes(LocalMasterWriter._canonical_bytes(rows))
    before = snapshot(tmp_path)

    with pytest.raises(LocalMasterInitializationError):
        initialize_writer(initialized_writer)
    with pytest.raises(LocalMasterCorruptionError):
        publish_for(
            master_type,
            initialized_writer,
            [row_for(master_type, row_version=2, content_hash=HASH_B, updated_at=COMMIT_2)],
            lock=lock,
            expected_version=1,
            idempotency_key="after-tamper",
        )

    assert snapshot(tmp_path) == before


def test_noncanonical_but_equivalent_rows_json_is_not_a_divergence(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
) -> None:
    publish_accounting(initialized_writer, [accounting_row()])
    lock = acquire(initialized_writer)
    rows_path = tmp_path / "accounting" / "rows.json"
    rows_path.write_text(
        json.dumps(json.loads(rows_path.read_text(encoding="utf-8")), indent=4),
        encoding="utf-8",
    )

    initialize_writer(initialized_writer)
    result = publish_accounting(
        initialized_writer,
        [accounting_row(row_version=2, content_hash=HASH_B, updated_at=COMMIT_2)],
        lock=lock,
        expected_version=1,
        idempotency_key="after-reformat",
    )

    assert result.new_master_version == 2


def test_recovery_refuses_a_restore_that_diverges_from_the_master_version(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
) -> None:
    publish_accounting(initialized_writer, [accounting_row()])
    manifest_id = write_interrupted_manifest(tmp_path, "REPLACING")

    with pytest.raises(LocalMasterRecoveryError):
        initialized_writer.recover(recovered_at=LATER, recovered_by=ActorSystem.LOCAL)

    assert read_manifest(tmp_path, manifest_id).phase == "RECOVERY_REQUIRED"


# G. Path confinement ------------------------------------------------------------


@pytest.mark.parametrize(
    "reference",
    [
        "C:\\chemin\\facture.pdf",
        "C:/chemin/facture.pdf",
        "\\\\serveur\\partage\\facture.pdf",
        "/home/bob/facture.pdf",
        "file:///C:/chemin/facture.pdf",
        "FILE:///tmp/facture.pdf",
        "../facture.pdf",
        "dossier/../../facture.pdf",
        "dossier\\..\\facture.pdf",
        "onedrive://dsh/../facture.pdf",
    ],
)
def test_forbidden_file_references_are_refused_before_writing(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
    reference: str,
) -> None:
    lock = acquire(initialized_writer)
    before = snapshot(tmp_path)

    with pytest.raises(LocalMasterWriterError):
        publish_accounting(
            initialized_writer,
            [accounting_row(current_file_uri=reference)],
            lock=lock,
        )

    assert snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "reference",
    [
        "onedrive://dsh/Factures/2026/facture.pdf",
        "onedrive://dsh/Factures/2026..10/facture.pdf",
    ],
)
def test_logical_onedrive_references_remain_allowed(
    initialized_writer: LocalMasterWriter,
    reference: str,
) -> None:
    publish_accounting(initialized_writer, [accounting_row(current_file_uri=reference)])

    assert initialized_writer.read_accounting_rows()[0].current_file_uri == reference


@pytest.mark.parametrize(
    "relative",
    ["../outside.json", "/outside.json", "C:/outside.json", "a/../../outside.json", "a\\b.json"],
)
def test_internal_path_resolution_never_leaves_the_root(
    initialized_writer: LocalMasterWriter,
    relative: str,
) -> None:
    with pytest.raises(LocalMasterWriterError):
        initialized_writer._path(relative)


@pytest.mark.parametrize(
    "target",
    ["../{name}", "C:/{name}", "/{name}", "sub/../../{name}"],
    ids=["parent-segment", "drive-letter", "absolute", "nested-parent-segment"],
)
def test_forged_manifest_cannot_reach_files_outside_the_root(
    tmp_path: Path,
    target: str,
) -> None:
    root = tmp_path / "root"
    writer = fresh_writer(root)
    name = f"dsh-escape-{uuid4().hex}.txt"
    outside = tmp_path / name
    outside.write_text("keep", encoding="utf-8")
    relative = target.format(name=name)
    manifest_id = uuid4()
    forged = LocalTransactionManifest.model_construct(
        transaction_manifest_id=manifest_id,
        master_type=MasterType.ACCOUNTING,
        operation_id=uuid4(),
        idempotency_key="forged",
        phase="REPLACING",
        created_at=NOW,
        updated_at=NOW,
        target_files=[relative],
        backup_files=[],
        content_hashes={},
        metadata={"originally_missing": [relative]},
    ).model_dump_json()
    manifest_path = root / "recovery" / f"{manifest_id}.json"
    manifest_path.write_text(forged, encoding="utf-8")

    with pytest.raises(LocalMasterRecoveryError):
        writer.recover(recovered_at=LATER, recovered_by=ActorSystem.LOCAL)

    assert outside.read_text(encoding="utf-8") == "keep"
    assert manifest_path.read_text(encoding="utf-8") == forged


# H. Result validated before commit ------------------------------------------------


@pytest.mark.parametrize("colliding", ["audit-event", "status-history"])
def test_result_identifier_collision_is_refused_before_any_write(
    initialized_writer: LocalMasterWriter,
    tmp_path: Path,
    colliding: str,
) -> None:
    lock = acquire(initialized_writer)
    before = snapshot(tmp_path)
    kwargs: dict[str, Any] = (
        {"audit_events": [make_audit_event(event_id=DOCUMENT_ID)]}
        if colliding == "audit-event"
        else {"status_history": [make_status_history(status_history_id=DOCUMENT_ID)]}
    )

    with pytest.raises(LocalMasterRowConflictError):
        publish_accounting(initialized_writer, [accounting_row()], lock=lock, **kwargs)

    assert snapshot(tmp_path) == before
    assert initialized_writer.read_master_version(MasterType.ACCOUNTING).current_version == 0


# A (writer level). Reconciled payment batch -----------------------------------------


def test_batch_reconciliation_is_published_with_matched_parent_and_history(
    initialized_writer: LocalMasterWriter,
) -> None:
    batch_id = uuid4()

    def lcr_row(matched: bool, **overrides: Any) -> BankTransactionMasterRow:
        items = [
            PaymentBatchItem(
                item_id=UUID(int=900 + index),
                amount=Decimal("4250.00"),
                status=PaymentItemStatus.MATCHED if matched else PaymentItemStatus.UNMATCHED,
                allocations=(
                    [
                        ReconciliationAllocation(
                            allocation_id=UUID(int=1000 + index),
                            target_type=AllocationTargetType.INVOICE,
                            target_id=UUID(int=1100 + index),
                            allocated_amount=Decimal("4250.00"),
                            allocation_status=ReconciliationStatus.MATCHED,
                            created_at=NOW,
                            created_by="LOCAL",
                        )
                    ]
                    if matched
                    else []
                ),
                unallocated_amount=Decimal("0") if matched else Decimal("4250.00"),
            )
            for index in range(10)
        ]
        batch = PaymentBatch(
            batch_id=batch_id,
            batch_type=PaymentBatchType.LCR_REMITTANCE,
            booking_date=date(2026, 10, 15),
            total_amount=Decimal("42500.00"),
            currency="EUR",
            expected_item_count=10,
            items=items,
            status=PaymentBatchStatus.RECONCILED if matched else PaymentBatchStatus.NEEDS_REVIEW,
            unexplained_amount=Decimal("0"),
            created_at=NOW,
            updated_at=LATER,
        )
        return bank_master(
            record_overrides={
                "payment_batch": batch,
                "reconciliation_status": (
                    ReconciliationStatus.MATCHED if matched else ReconciliationStatus.UNMATCHED
                ),
                "unallocated_amount": Decimal("0") if matched else Decimal("42500.00"),
            },
            **overrides,
        )

    publish_banking(initialized_writer, [lcr_row(False)])
    publish_banking(
        initialized_writer,
        [lcr_row(True, row_version=2, content_hash=HASH_B, updated_at=COMMIT_1)],
        expected_version=1,
        idempotency_key="lcr-reconciled",
    )

    saved = initialized_writer.read_banking_rows()[0].banking_record
    assert saved.reconciliation_status == ReconciliationStatus.MATCHED
    assert saved.allocations == []
    assert saved.unallocated_amount == Decimal("0")
    assert saved.payment_batch.status == PaymentBatchStatus.RECONCILED
    assert {
        (item.resource_type, item.status_from, item.status_to)
        for item in initialized_writer.read_status_history()
    } == {
        (AuditResourceType.BANK_TRANSACTION, "UNMATCHED", "MATCHED"),
        (AuditResourceType.PAYMENT_BATCH, "NEEDS_REVIEW", "RECONCILED"),
    }
