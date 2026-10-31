"""Contract tests for ``schemas.master_tables_schema``.

Covers hash/UTC/identity validation (``content_hash``, ``source_hash``,
``company_id``, ``supplier_id``), soft deletion, versioning, locks and the
pure write-precondition helpers.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from schemas.banking_schema import (
    BankAccountReference,
    BankAccountType,
    BankingRecord,
    BankTransactionDirection,
    BankTransactionType,
    PaymentMethod,
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
    PaymentContribution,
    PaymentOrigin,
    ProcessingStatus,
    ResolutionStatus,
    ResourceLock,
    SettlementRoute,
    SettlementStatus,
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
SUPPLIER_ID = UUID("0d6a7b0e-9a0b-4c1e-8d36-1c6f6b0a1a11")
ACCOUNT_ID = UUID("99a9a625-8742-4779-90a5-7b207af9cfd6")
TRANSACTION_ID = UUID("3a5115c1-8195-45a4-b3cd-0a38f15191ef")

PARIS = timezone(timedelta(hours=2))
BAD_HASHES = ["", "a" * 63, "a" * 65, "g" * 64, " " + "a" * 63, "é" * 64]
BLANKS = ["", "   ", "\t\n"]


# --- Builders ----------------------------------------------------------------


def accounting_data(**overrides: Any) -> dict[str, Any]:
    """Return valid accounting row data.

    Args:
        **overrides: Values replacing the defaults.

    Returns:
        A keyword dictionary for ``AccountingDocumentMasterRow``.
    """
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
    """Build a valid accounting row.

    Args:
        **overrides: Values replacing the defaults.

    Returns:
        A validated row.
    """
    return AccountingDocumentMasterRow(**accounting_data(**overrides))


def contribution(**overrides: Any) -> PaymentContribution:
    """Build a valid personal-funds contribution.

    Args:
        **overrides: Values replacing the defaults.

    Returns:
        A validated contribution.
    """
    values: dict[str, Any] = {
        "contribution_id": UUID("37ec1eb8-180e-490a-8b6c-4299051eecc1"),
        "payment_origin": PaymentOrigin.PERSONAL_FUNDS,
        "payment_method": DocumentPaymentMethod.PERSONAL_CARD,
        "payer_name": "Director",
        "amount": Decimal("100.00"),
        "company_bank_reconciliation_required": False,
        "settlement_route": SettlementRoute.EXPENSE_CLAIM,
        "settlement_status": SettlementStatus.PENDING,
    }
    values.update(overrides)
    return PaymentContribution(**values)


def banking_record(**overrides: Any) -> BankingRecord:
    """Build a valid banking record.

    Args:
        **overrides: Values replacing the defaults.

    Returns:
        A validated banking record.
    """
    values: dict[str, Any] = {
        "transaction_id": TRANSACTION_ID,
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
        "raw_label": "LCR REMITTANCE",
        "reconciliation_status": ReconciliationStatus.UNMATCHED,
        "allocations": [],
        "unallocated_amount": Decimal("42500.00"),
        "import_batch_id": BATCH_ID,
        "source_evidence_ids": [EVIDENCE_ID],
        "row_version": 1,
        "content_hash": HASH,
        "created_at": NOW,
        "updated_at": LATER,
    }
    values.update(overrides)
    return BankingRecord(**values)


def bank_row(**overrides: Any) -> BankTransactionMasterRow:
    """Build a valid bank transaction row.

    Args:
        **overrides: Values replacing the defaults.

    Returns:
        A validated bank row.
    """
    values: dict[str, Any] = {
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
    }
    values.update(overrides)
    return BankTransactionMasterRow(**values)


def evidence(**overrides: Any) -> SourceEvidence:
    """Build valid source evidence.

    Args:
        **overrides: Values replacing the defaults.

    Returns:
        A validated evidence.
    """
    values: dict[str, Any] = {
        "evidence_id": EVIDENCE_ID,
        "source_type": EvidenceSourceType.FILE,
        "created_at": NOW,
    }
    values.update(overrides)
    return SourceEvidence(**values)


def batch(**overrides: Any) -> ImportBatch:
    """Build a valid import batch.

    Args:
        **overrides: Values replacing the defaults.

    Returns:
        A validated import batch.
    """
    values: dict[str, Any] = {
        "batch_id": BATCH_ID,
        "master_type": MasterType.ACCOUNTING,
        "source_system": ActorSystem.LOCAL,
        "source_hash": HASH,
        "idempotency_key": "batch-001",
        "base_master_version": 0,
        "started_at": NOW,
        "status": ImportBatchStatus.READY,
        "record_count": 3,
        "accepted_count": 1,
        "rejected_count": 1,
        "duplicate_count": 1,
        "operation_id": OPERATION_ID,
    }
    values.update(overrides)
    return ImportBatch(**values)


def audit(**overrides: Any) -> AuditEvent:
    """Build a valid audit event.

    Args:
        **overrides: Values replacing the defaults.

    Returns:
        A validated audit event.
    """
    values: dict[str, Any] = {
        "event_id": UUID("f0c4d3a0-3b0e-4f2d-9d58-0d1f0d0d0d0d"),
        "occurred_at": NOW,
        "operation_id": OPERATION_ID,
        "resource_type": AuditResourceType.ACCOUNTING_DOCUMENT,
        "resource_id": str(DOCUMENT_ID),
        "action": AuditAction.UPDATED,
        "actor_system": ActorSystem.LOCAL,
    }
    values.update(overrides)
    return AuditEvent(**values)


def history(**overrides: Any) -> StatusHistory:
    """Build a valid status history entry.

    Args:
        **overrides: Values replacing the defaults.

    Returns:
        A validated status history.
    """
    values: dict[str, Any] = {
        "status_history_id": UUID("a1c4d3a0-3b0e-4f2d-9d58-0d1f0d0d0d0d"),
        "resource_type": AuditResourceType.ACCOUNTING_DOCUMENT,
        "resource_id": str(DOCUMENT_ID),
        "status_domain": StatusDomain.VALIDATION,
        "status_from": "PENDING",
        "status_to": "VALIDATED",
        "changed_at": NOW,
        "changed_by": ActorSystem.HUMAN,
        "operation_id": OPERATION_ID,
        "row_version_after_change": 2,
    }
    values.update(overrides)
    return StatusHistory(**values)


def lock(**overrides: Any) -> ResourceLock:
    """Build a valid active lock.

    Args:
        **overrides: Values replacing the defaults.

    Returns:
        A validated lock.
    """
    values: dict[str, Any] = {
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


def version(**overrides: Any) -> MasterVersion:
    """Build a valid master version.

    Args:
        **overrides: Values replacing the defaults.

    Returns:
        A validated master version.
    """
    values: dict[str, Any] = {
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


def write(**overrides: Any) -> None:
    """Call ``validate_write_preconditions`` with valid defaults.

    Args:
        **overrides: Arguments replacing the defaults.
    """
    kwargs: dict[str, Any] = {
        "lock": lock(),
        "now": NOW + timedelta(minutes=1),
        "owner_system": ActorSystem.LOCAL,
        "owner_instance_id": "local-instance-1",
        "expected_base_version": 3,
        "master_version": version(),
        "idempotency_key": "new-key",
    }
    kwargs.update(overrides)
    validate_write_preconditions(**kwargs)


# --- Accounting row: identity, hash, supplier_id -----------------------------


def test_valid_accounting_row() -> None:
    """The reference row is valid and keeps supplier_id optional."""
    row = accounting_row()
    assert row.supplier_id is None
    assert row.is_deleted is False


def test_supplier_id_accepts_uuid_and_string() -> None:
    """supplier_id accepts a UUID or its string form."""
    assert accounting_row(supplier_id=SUPPLIER_ID).supplier_id == SUPPLIER_ID
    assert accounting_row(supplier_id=str(SUPPLIER_ID)).supplier_id == SUPPLIER_ID


@pytest.mark.parametrize("bad", ["not-a-uuid", "", 123, "1234"])
def test_supplier_id_rejects_invalid_values(bad: Any) -> None:
    """A malformed supplier_id is rejected."""
    with pytest.raises(ValidationError):
        accounting_row(supplier_id=bad)


@pytest.mark.parametrize("field", ["document_id", "last_operation_id", "import_batch_id"])
def test_required_identifiers_are_mandatory(field: str) -> None:
    """Missing identifiers are rejected."""
    data = accounting_data()
    del data[field]
    with pytest.raises(ValidationError, match=field):
        AccountingDocumentMasterRow(**data)


@pytest.mark.parametrize("value", BLANKS)
def test_company_id_must_not_be_blank(value: str) -> None:
    """A blank company_id is rejected."""
    with pytest.raises(ValidationError, match="company_id"):
        accounting_row(company_id=value)


def test_company_id_is_required_and_not_none() -> None:
    """company_id cannot be missing or None."""
    data = accounting_data()
    del data["company_id"]
    with pytest.raises(ValidationError):
        AccountingDocumentMasterRow(**data)
    with pytest.raises(ValidationError):
        accounting_row(company_id=None)


@pytest.mark.parametrize("digest", BAD_HASHES)
def test_content_hash_must_be_64_hex(digest: str) -> None:
    """A malformed content_hash is rejected."""
    with pytest.raises(ValidationError, match="content hash"):
        accounting_row(content_hash=digest)


def test_content_hash_accepts_upper_case_hex() -> None:
    """Hexadecimal digests are case-insensitive."""
    assert accounting_row(content_hash="A" * 64).content_hash == "A" * 64


def test_content_hash_trailing_newline_is_rejected() -> None:
    """``fullmatch`` forbids a trailing newline."""
    with pytest.raises(ValidationError):
        accounting_row(content_hash="a" * 64 + "\n")


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "1", None])
def test_row_version_must_be_positive_strict_int(value: Any) -> None:
    """row_version must be a strictly positive integer."""
    with pytest.raises(ValidationError):
        accounting_row(row_version=value)


@pytest.mark.parametrize("value", BLANKS)
def test_idempotency_key_must_not_be_blank(value: str) -> None:
    """A blank idempotency key is rejected."""
    with pytest.raises(ValidationError, match="idempotency_key"):
        accounting_row(idempotency_key=value)


def test_unknown_fields_are_forbidden() -> None:
    """Extra fields are rejected."""
    with pytest.raises(ValidationError, match="extra"):
        accounting_row(tenant_id="t1")


@pytest.mark.parametrize("field", ["created_at", "updated_at"])
def test_timestamps_must_be_utc(field: str) -> None:
    """Naive and non-UTC datetimes are rejected."""
    with pytest.raises(ValidationError, match="UTC"):
        accounting_row(**{field: datetime(2026, 10, 15, 12, 0)})
    with pytest.raises(ValidationError, match="UTC"):
        accounting_row(**{field: datetime(2026, 10, 15, 12, 0, tzinfo=PARIS)})


def test_updated_at_cannot_precede_created_at() -> None:
    """updated_at must not be earlier than created_at."""
    with pytest.raises(ValidationError, match="precede"):
        accounting_row(updated_at=NOW - timedelta(seconds=1))


def test_updated_at_equal_to_created_at_is_valid() -> None:
    """Equal timestamps are accepted."""
    assert accounting_row(updated_at=NOW).updated_at == NOW


def test_utc_equivalent_offset_zero_is_accepted() -> None:
    """Any timezone with a zero offset is treated as UTC."""
    zero = timezone(timedelta(0), "custom")
    assert accounting_row(created_at=NOW.astimezone(zero)).created_at == NOW


# --- Soft deletion -----------------------------------------------------------

DELETION = {
    "is_deleted": True,
    "deleted_at": LATER,
    "deleted_by": ActorSystem.HUMAN,
    "deletion_reason": "duplicate",
    "deletion_operation_id": OPERATION_ID,
}


def test_complete_soft_deletion_is_valid() -> None:
    """All deletion fields together are valid."""
    assert accounting_row(**DELETION).is_deleted is True


@pytest.mark.parametrize("missing", list(DELETION)[1:])
def test_soft_deletion_requires_every_field(missing: str) -> None:
    """Dropping any deletion field is rejected."""
    values = {k: v for k, v in DELETION.items() if k != missing}
    with pytest.raises(ValidationError, match="soft deletion requires"):
        accounting_row(**values)


@pytest.mark.parametrize("field", list(DELETION)[1:])
def test_deletion_fields_forbidden_when_not_deleted(field: str) -> None:
    """Deletion fields without is_deleted are rejected."""
    with pytest.raises(ValidationError, match="must be absent"):
        accounting_row(**{field: DELETION[field]})


@pytest.mark.parametrize("value", BLANKS)
def test_blank_deletion_reason_is_rejected(value: str) -> None:
    """A blank deletion reason is rejected."""
    with pytest.raises(ValidationError):
        accounting_row(**{**DELETION, "deletion_reason": value})


def test_deleted_at_must_be_utc() -> None:
    """deleted_at must be UTC."""
    with pytest.raises(ValidationError, match="UTC"):
        accounting_row(**{**DELETION, "deleted_at": datetime(2026, 10, 15)})


# --- Accounting amounts ------------------------------------------------------


@pytest.mark.parametrize("value", ["EUR ", "eur", "EU", "EURO", "€€€", "E1R", ""])
def test_currency_must_be_three_uppercase_letters(value: str) -> None:
    """Malformed currencies are rejected."""
    with pytest.raises(ValidationError, match="currency"):
        accounting_row(currency=value)


@pytest.mark.parametrize("value", ["abc", "NaN", "Infinity", None, [], {}])
def test_gross_amount_must_be_finite_decimal(value: Any) -> None:
    """Non-numeric or non-finite gross amounts are rejected."""
    with pytest.raises(ValidationError):
        accounting_row(gross_amount=value)


@pytest.mark.parametrize(
    "field", ["gross_amount", "paid_amount", "remaining_amount", "discount_amount"]
)
def test_negative_amounts_are_rejected(field: str) -> None:
    """Negative amounts are rejected."""
    with pytest.raises(ValidationError, match="non-negative"):
        accounting_row(**{field: Decimal("-1")})


@pytest.mark.parametrize("value", ["0", "-0.01"])
def test_rounding_tolerance_must_be_positive(value: str) -> None:
    """A non-positive rounding tolerance is rejected."""
    with pytest.raises(ValidationError):
        accounting_row(rounding_tolerance=value)


def test_amounts_accept_strings_and_numbers() -> None:
    """Amounts are coerced to Decimal."""
    row = accounting_row(gross_amount="100", remaining_amount=100, net_amount=None)
    assert row.gross_amount == Decimal("100")
    assert row.net_amount is None


def test_net_plus_tax_must_match_gross() -> None:
    """net + tax must equal gross within tolerance."""
    with pytest.raises(ValidationError, match="net_amount"):
        accounting_row(net_amount=Decimal("70.00"))


def test_remaining_amount_must_match_paid_and_discount() -> None:
    """remaining_amount must be gross - paid - discount."""
    with pytest.raises(ValidationError, match="remaining_amount"):
        accounting_row(remaining_amount=Decimal("90.00"))


def test_paid_amount_cannot_exceed_payable() -> None:
    """paid_amount cannot exceed gross - discount."""
    with pytest.raises(ValidationError):
        accounting_row(paid_amount=Decimal("150.00"), remaining_amount=Decimal("0"))


def test_due_date_cannot_precede_issue_date() -> None:
    """due_date must not precede issue_date."""
    with pytest.raises(ValidationError, match="due_date"):
        accounting_row(issue_date=date(2026, 10, 15), due_date=date(2026, 10, 14))


@pytest.mark.parametrize("value", ["1234567890123", "123456789012345", "1234567890123A", "١٢٣٤٥٦٧٨٩٠١٢٣٤"])
@pytest.mark.parametrize("field", ["supplier_siret_observed", "customer_siret_observed"])
def test_siret_must_be_14_ascii_digits(field: str, value: str) -> None:
    """Malformed SIRET values are rejected."""
    with pytest.raises(ValidationError, match="SIRET"):
        accounting_row(**{field: value})


def test_valid_siret_is_accepted() -> None:
    """A 14-digit SIRET is accepted."""
    assert accounting_row(supplier_siret_observed="12345678901234")


@pytest.mark.parametrize("value", [-0.1, 1.1, float("inf")])
def test_category_confidence_range(value: float) -> None:
    """category_confidence must lie in [0, 1]."""
    with pytest.raises(ValidationError):
        accounting_row(category_confidence=value)


@pytest.mark.parametrize("value", BLANKS)
def test_optional_texts_reject_blank(value: str) -> None:
    """Optional text fields reject blank strings."""
    with pytest.raises(ValidationError):
        accounting_row(document_number=value)


def test_attendees_reject_blank_entries() -> None:
    """Blank attendees are rejected."""
    with pytest.raises(ValidationError, match="attendees"):
        accounting_row(attendees_or_beneficiaries=["Ann", " "])


# --- Payment contributions ---------------------------------------------------


def test_contribution_totals_must_match() -> None:
    """Personal contributions require matching totals and N/A reconciliation."""
    row = accounting_row(
        paid_amount=Decimal("100.00"),
        remaining_amount=Decimal("0.00"),
        payment_contributions=[contribution()],
        total_personal_amount=Decimal("100.00"),
        reconciliation_status=ReconciliationStatus.NOT_APPLICABLE,
    )
    assert row.total_personal_amount == Decimal("100.00")


def test_contribution_totals_mismatch_is_rejected() -> None:
    """Wrong totals are rejected."""
    with pytest.raises(ValidationError, match="total_personal_amount"):
        accounting_row(
            paid_amount=Decimal("100.00"),
            remaining_amount=Decimal("0.00"),
            payment_contributions=[contribution()],
            reconciliation_status=ReconciliationStatus.NOT_APPLICABLE,
        )


def test_contributions_must_equal_paid_amount() -> None:
    """Contributions must sum to paid_amount."""
    with pytest.raises(ValidationError):
        accounting_row(
            paid_amount=Decimal("50.00"),
            remaining_amount=Decimal("50.00"),
            payment_contributions=[contribution()],
            total_personal_amount=Decimal("100.00"),
            reconciliation_status=ReconciliationStatus.NOT_APPLICABLE,
        )


def test_all_personal_requires_not_applicable_reconciliation() -> None:
    """All-personal contributions require NOT_APPLICABLE reconciliation."""
    with pytest.raises(ValidationError, match="NOT_APPLICABLE"):
        accounting_row(
            paid_amount=Decimal("100.00"),
            remaining_amount=Decimal("0.00"),
            payment_contributions=[contribution()],
            total_personal_amount=Decimal("100.00"),
        )


def test_unknown_origin_requires_review() -> None:
    """UNKNOWN origin forces payment_origin_review_required."""
    unknown = contribution(
        payment_origin=PaymentOrigin.UNKNOWN,
        payment_method=DocumentPaymentMethod.UNKNOWN,
    )
    with pytest.raises(ValidationError, match="requires review"):
        accounting_row(
            paid_amount=Decimal("100.00"),
            remaining_amount=Decimal("0.00"),
            payment_contributions=[unknown],
        )
    assert accounting_row(
        paid_amount=Decimal("100.00"),
        remaining_amount=Decimal("0.00"),
        payment_contributions=[unknown],
        payment_origin_review_required=True,
    )


@pytest.mark.parametrize("field", ["total_personal_amount", "total_company_paid_amount"])
def test_payment_totals_cannot_be_negative(field: str) -> None:
    """Negative payment totals are rejected."""
    with pytest.raises(ValidationError):
        accounting_row(**{field: "-1"})


@pytest.mark.parametrize("amount", ["0", "-5", "abc", "NaN", None])
def test_contribution_amount_must_be_positive_decimal(amount: Any) -> None:
    """Contribution amounts must be positive finite decimals."""
    with pytest.raises(ValidationError):
        contribution(amount=amount)


def test_personal_funds_require_identified_payer() -> None:
    """Personal funds need a payer id or name."""
    with pytest.raises(ValidationError, match="identified payer"):
        contribution(payer_name=None)
    assert contribution(payer_name=None, payer_id=SUPPLIER_ID)


def test_blank_payer_name_is_rejected() -> None:
    """Blank payer names are rejected."""
    with pytest.raises(ValidationError):
        contribution(payer_name="  ")


def test_personal_funds_cannot_require_company_reconciliation() -> None:
    """Personal funds cannot require company reconciliation."""
    with pytest.raises(ValidationError, match="company bank reconciliation"):
        contribution(company_bank_reconciliation_required=True)


def test_personal_funds_cannot_reference_banking_transaction() -> None:
    """Personal funds cannot reference a company transaction."""
    with pytest.raises(ValidationError, match="banking transaction"):
        contribution(banking_transaction_id=TRANSACTION_ID)


def test_company_account_requires_reconciliation() -> None:
    """Company account contributions must be reconciled."""
    with pytest.raises(ValidationError, match="require bank reconciliation"):
        contribution(
            payment_origin=PaymentOrigin.COMPANY_ACCOUNT,
            payment_method=DocumentPaymentMethod.COMPANY_CARD,
            settlement_route=SettlementRoute.BANK_RECONCILIATION,
        )


def test_bank_reconciliation_route_requires_company_origin() -> None:
    """BANK_RECONCILIATION routing needs a company account origin."""
    with pytest.raises(ValidationError, match="company account origin"):
        contribution(settlement_route=SettlementRoute.BANK_RECONCILIATION)


def test_reimbursed_requires_reimbursement_transaction() -> None:
    """REIMBURSED needs a reimbursement transaction."""
    with pytest.raises(ValidationError, match="reimbursement transaction"):
        contribution(settlement_status=SettlementStatus.REIMBURSED)
    assert contribution(
        settlement_status=SettlementStatus.REIMBURSED,
        reimbursement_transaction_id=TRANSACTION_ID,
    )


# --- Bank transaction row ----------------------------------------------------


def test_valid_bank_row() -> None:
    """The reference bank row is valid."""
    assert bank_row().transaction_id == TRANSACTION_ID


def test_bank_row_transaction_id_must_match_record() -> None:
    """transaction_id must match the embedded record."""
    with pytest.raises(ValidationError, match="transaction_id"):
        bank_row(transaction_id=DOCUMENT_ID)


@pytest.mark.parametrize(
    "field,value",
    [
        ("row_version", 2),
        ("content_hash", "b" * 64),
        ("import_batch_id", DOCUMENT_ID),
    ],
)
def test_bank_row_envelope_must_match_record(field: str, value: Any) -> None:
    """Envelope and record metadata must agree."""
    with pytest.raises(ValidationError, match=field):
        bank_row(**{field: value})


def test_bank_row_requires_banking_record() -> None:
    """The banking record is mandatory."""
    with pytest.raises(ValidationError):
        bank_row(banking_record=None)


# --- SourceEvidence ----------------------------------------------------------


def test_valid_evidence_and_source_hash() -> None:
    """Evidence accepts a valid source_hash."""
    assert evidence(source_hash=HASH).source_hash == HASH
    assert evidence().source_hash is None


@pytest.mark.parametrize("digest", BAD_HASHES)
def test_evidence_source_hash_invalid(digest: str) -> None:
    """Malformed source_hash is rejected."""
    with pytest.raises(ValidationError, match="content hash"):
        evidence(source_hash=digest)


@pytest.mark.parametrize("field", ["page_number", "row_number"])
@pytest.mark.parametrize("value", [0, -1, 1.5, True, "1"])
def test_evidence_positions_must_be_positive_ints(field: str, value: Any) -> None:
    """Positions must be strictly positive integers."""
    with pytest.raises(ValidationError):
        evidence(**{field: value})


@pytest.mark.parametrize("value", [-0.01, 1.01])
def test_evidence_confidence_range(value: float) -> None:
    """Confidence must lie in [0, 1]."""
    with pytest.raises(ValidationError):
        evidence(confidence_score=value)


@pytest.mark.parametrize("value", [0, 1, 0.5])
def test_evidence_confidence_bounds_accepted(value: float) -> None:
    """Boundary confidences are accepted."""
    assert evidence(confidence_score=value).confidence_score == value


@pytest.mark.parametrize("value", BLANKS)
def test_evidence_texts_reject_blank(value: str) -> None:
    """Blank evidence texts are rejected."""
    with pytest.raises(ValidationError):
        evidence(raw_text=value)


def test_evidence_created_at_must_be_utc() -> None:
    """created_at must be UTC."""
    with pytest.raises(ValidationError, match="UTC"):
        evidence(created_at=datetime(2026, 10, 15))


def test_evidence_rejects_unknown_source_type() -> None:
    """Unknown source types are rejected."""
    with pytest.raises(ValidationError):
        evidence(source_type="FAX")


# --- ImportBatch -------------------------------------------------------------


def test_valid_batch() -> None:
    """The reference batch is valid."""
    assert batch().record_count == 3


@pytest.mark.parametrize("digest", BAD_HASHES)
def test_batch_source_hash_invalid(digest: str) -> None:
    """Malformed batch source_hash is rejected."""
    with pytest.raises(ValidationError):
        batch(source_hash=digest)


@pytest.mark.parametrize(
    "field",
    [
        "base_master_version",
        "record_count",
        "accepted_count",
        "rejected_count",
        "duplicate_count",
    ],
)
def test_batch_counters_non_negative(field: str) -> None:
    """Negative counters are rejected."""
    with pytest.raises(ValidationError):
        batch(**{field: -1})


def test_batch_counters_cannot_exceed_record_count() -> None:
    """Processed counters cannot exceed record_count."""
    with pytest.raises(ValidationError, match="record_count"):
        batch(accepted_count=3, rejected_count=1)


def test_batch_completed_before_started_is_rejected() -> None:
    """completed_at cannot precede started_at."""
    with pytest.raises(ValidationError, match="completed_at"):
        batch(completed_at=NOW - timedelta(seconds=1))


def test_completed_batch_requirements() -> None:
    """COMPLETED needs completion time and published version."""
    with pytest.raises(ValidationError, match="COMPLETED"):
        batch(status=ImportBatchStatus.COMPLETED)
    with pytest.raises(ValidationError, match="COMPLETED"):
        batch(status=ImportBatchStatus.COMPLETED, completed_at=LATER)
    assert batch(
        status=ImportBatchStatus.COMPLETED,
        completed_at=LATER,
        published_master_version=1,
    )


def test_failed_batch_requirements() -> None:
    """FAILED needs completion time and an error summary."""
    with pytest.raises(ValidationError, match="FAILED"):
        batch(status=ImportBatchStatus.FAILED, completed_at=LATER)
    assert batch(
        status=ImportBatchStatus.FAILED, completed_at=LATER, error_summary="boom"
    )


@pytest.mark.parametrize("value", BLANKS)
def test_batch_idempotency_key_not_blank(value: str) -> None:
    """A blank batch idempotency key is rejected."""
    with pytest.raises(ValidationError):
        batch(idempotency_key=value)


def test_batch_timestamps_must_be_utc() -> None:
    """Batch timestamps must be UTC."""
    with pytest.raises(ValidationError, match="UTC"):
        batch(started_at=datetime(2026, 10, 15))


def test_batch_requires_operation_id() -> None:
    """operation_id is mandatory."""
    with pytest.raises(ValidationError, match="operation_id"):
        batch(operation_id=None)


# --- AuditEvent / StatusHistory ----------------------------------------------


def test_audit_event_is_frozen() -> None:
    """Audit events are immutable."""
    event = audit()
    with pytest.raises(ValidationError):
        event.reason = "x"


@pytest.mark.parametrize("value", BLANKS)
def test_audit_resource_id_not_blank(value: str) -> None:
    """A blank resource_id is rejected."""
    with pytest.raises(ValidationError, match="resource_id"):
        audit(resource_id=value)


@pytest.mark.parametrize("field", ["before_hash", "after_hash"])
def test_audit_hashes_validated(field: str) -> None:
    """Audit hashes must be 64 hex chars."""
    assert audit(**{field: HASH})
    with pytest.raises(ValidationError, match="content hash"):
        audit(**{field: "zz"})


def test_audit_versions() -> None:
    """Versions are non-negative and ordered."""
    with pytest.raises(ValidationError):
        audit(previous_version=-1)
    with pytest.raises(ValidationError, match="new_version"):
        audit(previous_version=3, new_version=2)
    assert audit(previous_version=3, new_version=3)


def test_audit_changed_fields_not_blank() -> None:
    """Blank changed_fields entries are rejected."""
    with pytest.raises(ValidationError, match="changed_fields"):
        audit(changed_fields=["a", ""])


def test_audit_occurred_at_must_be_utc() -> None:
    """occurred_at must be UTC."""
    with pytest.raises(ValidationError, match="UTC"):
        audit(occurred_at=datetime(2026, 10, 15, tzinfo=PARIS))


def test_audit_json_roundtrip() -> None:
    """Audit events survive a JSON round-trip."""
    event = audit(before_hash=HASH, changed_fields=["a"])
    assert AuditEvent.model_validate_json(event.model_dump_json()) == event


def test_status_history_valid_and_frozen() -> None:
    """Status history is valid and immutable."""
    entry = history(status_from=None)
    assert entry.status_from is None
    with pytest.raises(ValidationError):
        entry.status_to = "X"


def test_status_history_rules() -> None:
    """Status history validators reject invalid input."""
    with pytest.raises(ValidationError, match="differ"):
        history(status_from="A", status_to="A")
    with pytest.raises(ValidationError):
        history(status_to=" ")
    with pytest.raises(ValidationError):
        history(status_from=" ")
    with pytest.raises(ValidationError):
        history(reason=" ")
    with pytest.raises(ValidationError):
        history(row_version_after_change=0)
    with pytest.raises(ValidationError, match="UTC"):
        history(changed_at=datetime(2026, 10, 15))
    with pytest.raises(ValidationError):
        history(status_domain="UNKNOWN_DOMAIN")


# --- ResourceLock ------------------------------------------------------------


def test_valid_lock() -> None:
    """The reference lock is valid."""
    assert lock().status == LockStatus.ACTIVE


@pytest.mark.parametrize("field", ["resource_name", "owner_instance_id"])
@pytest.mark.parametrize("value", BLANKS)
def test_lock_text_not_blank(field: str, value: str) -> None:
    """Blank lock identifiers are rejected."""
    with pytest.raises(ValidationError):
        lock(**{field: value})


@pytest.mark.parametrize("owner", [ActorSystem.HUMAN, ActorSystem.EMAIL, ActorSystem.N8N, ActorSystem.API])
def test_lock_owner_must_be_local_or_oracle(owner: ActorSystem) -> None:
    """Only LOCAL and ORACLE can own a lock."""
    with pytest.raises(ValidationError, match="LOCAL or ORACLE"):
        lock(owner_system=owner)
    assert lock(owner_system=ActorSystem.ORACLE)


def test_lock_base_version_non_negative() -> None:
    """base_version cannot be negative."""
    with pytest.raises(ValidationError):
        lock(base_version=-1)
    assert lock(base_version=0)


def test_lock_time_ordering() -> None:
    """Heartbeat and expiry ordering is enforced."""
    with pytest.raises(ValidationError, match="heartbeat_at"):
        lock(heartbeat_at=NOW - timedelta(seconds=1))
    with pytest.raises(ValidationError, match="expire after"):
        lock(expires_at=NOW)
    assert lock(status=LockStatus.RELEASED, expires_at=NOW)


def test_lock_timestamps_must_be_utc() -> None:
    """Lock timestamps must be UTC."""
    with pytest.raises(ValidationError, match="UTC"):
        lock(acquired_at=datetime(2026, 10, 15, 12, 0))


def test_lock_is_frozen() -> None:
    """Locks are immutable."""
    with pytest.raises(ValidationError):
        lock().status = LockStatus.RELEASED


def test_lock_is_expired() -> None:
    """is_expired reflects status and expiry."""
    assert lock().is_expired(LATER) is True
    assert lock().is_expired(NOW) is False
    assert lock(status=LockStatus.EXPIRED).is_expired(NOW) is True
    assert lock(status=LockStatus.RELEASED).is_expired(LATER) is False
    with pytest.raises(ValueError, match="UTC"):
        lock().is_expired(datetime(2026, 10, 15))


def test_lock_can_be_renewed() -> None:
    """can_be_renewed requires active, unexpired and the same owner."""
    now = NOW + timedelta(minutes=1)
    args = (now, ActorSystem.LOCAL, "local-instance-1")
    assert lock().can_be_renewed(*args) is True
    assert lock().can_be_renewed(now, ActorSystem.ORACLE, "local-instance-1") is False
    assert lock().can_be_renewed(now, ActorSystem.LOCAL, "other") is False
    assert lock().can_be_renewed(LATER, ActorSystem.LOCAL, "local-instance-1") is False
    assert lock(status=LockStatus.RELEASED).can_be_renewed(*args) is False
    with pytest.raises(ValueError):
        lock().can_be_renewed(datetime(2026, 10, 15), ActorSystem.LOCAL, "x")


def test_lock_renew_success() -> None:
    """renew returns a new lock with updated heartbeat and expiry."""
    now = NOW + timedelta(minutes=1)
    new_expiry = now + timedelta(minutes=10)
    renewed = lock().renew(now, new_expiry)
    assert renewed.heartbeat_at == now
    assert renewed.expires_at == new_expiry
    assert lock().heartbeat_at == NOW


def test_lock_renew_errors() -> None:
    """renew rejects expired, inactive and past-expiry requests."""
    now = NOW + timedelta(minutes=1)
    with pytest.raises(LockExpiredError):
        lock().renew(LATER, LATER + timedelta(minutes=1))
    with pytest.raises(LockExpiredError):
        lock(status=LockStatus.EXPIRED).renew(now, LATER)
    with pytest.raises(LockUnavailableError):
        lock(status=LockStatus.RELEASED).renew(now, LATER)
    with pytest.raises(LockUnavailableError):
        lock(status=LockStatus.ABORTED).renew(now, LATER)
    with pytest.raises(LockExpiredError):
        lock().renew(now, now)
    with pytest.raises(ValueError, match="UTC"):
        lock().renew(now, datetime(2026, 10, 15, 13))
    with pytest.raises(ValueError, match="UTC"):
        lock().renew(datetime(2026, 10, 15), LATER)


def test_can_acquire_lock() -> None:
    """Acquisition depends on the existing lock state."""
    assert can_acquire_lock(None, now=NOW) is True
    assert can_acquire_lock(lock(), now=NOW) is False
    assert can_acquire_lock(lock(), now=LATER) is True
    assert can_acquire_lock(lock(status=LockStatus.RELEASED), now=NOW) is True
    assert can_acquire_lock(lock(status=LockStatus.ABORTED), now=NOW) is True
    assert can_acquire_lock(lock(status=LockStatus.EXPIRED), now=NOW) is True
    with pytest.raises(ValueError, match="UTC"):
        can_acquire_lock(None, now=datetime(2026, 10, 15))


# --- MasterVersion -----------------------------------------------------------


def test_valid_master_version() -> None:
    """The reference master version is valid."""
    assert version().current_version == 3
    assert version(current_version=0)


def test_master_version_validation() -> None:
    """MasterVersion validators reject invalid data."""
    with pytest.raises(ValidationError):
        version(current_version=-1)
    with pytest.raises(ValidationError):
        version(current_version=True)
    with pytest.raises(ValidationError, match="content hash"):
        version(content_hash="nope")
    with pytest.raises(ValidationError, match="UTC"):
        version(updated_at=datetime(2026, 10, 15))
    with pytest.raises(ValidationError):
        version(last_snapshot_uri=" ")
    with pytest.raises(ValidationError, match="blank"):
        version(applied_idempotency_keys=["a", " "])
    with pytest.raises(ValidationError, match="unique"):
        version(applied_idempotency_keys=["a", "a"])
    with pytest.raises(ValidationError):
        version(master_name="OTHER")


# --- validate_write_preconditions -------------------------------------------


def test_write_preconditions_success() -> None:
    """Valid preconditions do not raise."""
    write()


def test_write_rejects_expired_lock() -> None:
    """An expired lock is rejected."""
    with pytest.raises(LockExpiredError):
        write(now=LATER)
    with pytest.raises(LockExpiredError):
        write(lock=lock(status=LockStatus.EXPIRED))


@pytest.mark.parametrize("status", [LockStatus.RELEASED, LockStatus.ABORTED])
def test_write_rejects_inactive_lock(status: LockStatus) -> None:
    """A released or aborted lock is rejected."""
    with pytest.raises(LockUnavailableError):
        write(lock=lock(status=status))


def test_write_rejects_foreign_owner() -> None:
    """The caller must own the lock."""
    with pytest.raises(LockOwnershipError):
        write(owner_system=ActorSystem.ORACLE)
    with pytest.raises(LockOwnershipError):
        write(owner_instance_id="intruder")


@pytest.mark.parametrize("key", BLANKS)
def test_write_rejects_blank_idempotency_key(key: str) -> None:
    """A blank idempotency key is rejected."""
    with pytest.raises(MasterTableError, match="blank"):
        write(idempotency_key=key)


def test_write_rejects_version_conflicts() -> None:
    """Both stale expected and lock base versions conflict."""
    with pytest.raises(VersionConflictError):
        write(expected_base_version=2)
    with pytest.raises(VersionConflictError):
        write(lock=lock(base_version=2))
    with pytest.raises(VersionConflictError):
        write(master_version=version(current_version=4))


def test_write_rejects_duplicate_operation() -> None:
    """An already-applied key is rejected."""
    with pytest.raises(DuplicateOperationError):
        write(idempotency_key="already-applied")


def test_write_requires_utc_now() -> None:
    """now must be UTC."""
    with pytest.raises(ValueError, match="UTC"):
        write(now=datetime(2026, 10, 15, 12, 1))


def test_error_hierarchy() -> None:
    """All domain errors derive from MasterTableError."""
    for error in (
        LockUnavailableError,
        LockOwnershipError,
        LockExpiredError,
        VersionConflictError,
        DuplicateOperationError,
    ):
        assert issubclass(error, MasterTableError)


# --- next_master_version -----------------------------------------------------


def advance(current: MasterVersion | None = None, **overrides: Any) -> MasterVersion:
    """Call ``next_master_version`` with valid defaults.

    Args:
        current: Version to advance.
        **overrides: Arguments replacing the defaults.

    Returns:
        The next master version.
    """
    kwargs: dict[str, Any] = {
        "operation_id": BATCH_ID,
        "actor": ActorSystem.ORACLE,
        "idempotency_key": "new-key",
        "content_hash": "b" * 64,
        "updated_at": LATER,
    }
    kwargs.update(overrides)
    return next_master_version(current or version(), **kwargs)


def test_next_master_version_success() -> None:
    """The next version increments and appends the key."""
    current = version(metadata={"nested": {"k": 1}})
    result = advance(current, snapshot_uri="s3://snap")
    assert result.current_version == 4
    assert result.applied_idempotency_keys == ["already-applied", "new-key"]
    assert result.content_hash == "b" * 64
    assert result.last_operation_id == BATCH_ID
    assert result.updated_by == ActorSystem.ORACLE
    assert result.last_snapshot_uri == "s3://snap"
    assert current.current_version == 3
    assert current.applied_idempotency_keys == ["already-applied"]


def test_next_master_version_deep_copies_metadata() -> None:
    """Metadata is not shared with the previous version."""
    current = version(metadata={"nested": {"k": 1}})
    result = advance(current)
    result.metadata["nested"]["k"] = 2
    assert current.metadata["nested"]["k"] == 1


def test_next_master_version_accepts_equal_timestamp() -> None:
    """An equal updated_at is accepted."""
    assert advance(updated_at=NOW).updated_at == NOW


@pytest.mark.parametrize("key", BLANKS)
def test_next_master_version_blank_key(key: str) -> None:
    """A blank idempotency key is rejected."""
    with pytest.raises(MasterTableError, match="blank"):
        advance(idempotency_key=key)


def test_next_master_version_duplicate_key() -> None:
    """A replayed key is rejected."""
    with pytest.raises(DuplicateOperationError):
        advance(idempotency_key="already-applied")


@pytest.mark.parametrize("digest", BAD_HASHES)
def test_next_master_version_invalid_hash(digest: str) -> None:
    """A malformed content hash is rejected."""
    with pytest.raises(ValueError, match="content hash"):
        advance(content_hash=digest)


def test_next_master_version_requires_utc() -> None:
    """updated_at must be UTC."""
    with pytest.raises(ValueError, match="UTC"):
        advance(updated_at=datetime(2026, 10, 15, 12, 5))


def test_next_master_version_cannot_go_back_in_time() -> None:
    """updated_at cannot precede the current version."""
    with pytest.raises(MasterTableError, match="precede"):
        advance(updated_at=NOW - timedelta(seconds=1))
