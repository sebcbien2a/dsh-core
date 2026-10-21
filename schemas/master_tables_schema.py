"""Contracts and pure concurrency checks for the accounting and banking masters."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from schemas.banking_schema import BankingRecord, ReconciliationStatus


class ActorSystem(str, Enum):
    LOCAL = "LOCAL"
    ORACLE = "ORACLE"
    HUMAN = "HUMAN"
    EMAIL = "EMAIL"
    N8N = "N8N"
    API = "API"


class MasterType(str, Enum):
    ACCOUNTING = "ACCOUNTING"
    BANKING = "BANKING"


class ImportBatchStatus(str, Enum):
    RECEIVED = "RECEIVED"
    VALIDATING = "VALIDATING"
    READY = "READY"
    WRITING = "WRITING"
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"


class EvidenceSourceType(str, Enum):
    FILE = "FILE"
    EMAIL = "EMAIL"
    CHORUS_PRO = "CHORUS_PRO"
    SCAN = "SCAN"
    PHOTO = "PHOTO"
    SCREENSHOT = "SCREENSHOT"
    CSV = "CSV"
    EXCEL = "EXCEL"
    CAMT = "CAMT"
    PDF_TEXT = "PDF_TEXT"
    PDF_SCAN = "PDF_SCAN"
    MANUAL = "MANUAL"
    API = "API"
    ORACLE = "ORACLE"
    N8N = "N8N"


class DocumentDirection(str, Enum):
    PURCHASE = "PURCHASE"
    SALE = "SALE"
    EXPENSE = "EXPENSE"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class DocumentMasterType(str, Enum):
    INVOICE = "INVOICE"
    CREDIT_NOTE = "CREDIT_NOTE"
    RECEIPT = "RECEIPT"
    FUEL_RECEIPT = "FUEL_RECEIPT"
    MEAL_RECEIPT = "MEAL_RECEIPT"
    BANK_DOCUMENT = "BANK_DOCUMENT"
    PAYROLL = "PAYROLL"
    TAX_DOCUMENT = "TAX_DOCUMENT"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class IngestionStatus(str, Enum):
    RECEIVED = "RECEIVED"
    IMPORTED = "IMPORTED"
    DUPLICATE_SUSPECTED = "DUPLICATE_SUSPECTED"
    IMPORT_FAILED = "IMPORT_FAILED"


class ExtractionStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    EXTRACTING = "EXTRACTING"
    EXTRACTED = "EXTRACTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"


class MasterValidationStatus(str, Enum):
    PENDING = "PENDING"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"


class ProcessingStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    READY = "READY"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    ARCHIVED = "ARCHIVED"
    ERROR = "ERROR"


class FileState(str, Enum):
    INCOMING = "INCOMING"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    VALIDATED = "VALIDATED"
    PROCESSED = "PROCESSED"
    REJECTED = "REJECTED"
    ARCHIVED = "ARCHIVED"


class FileMoveStatus(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    MOVING = "MOVING"
    MOVED = "MOVED"
    FAILED = "FAILED"


class ResolutionStatus(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    PROPOSED = "PROPOSED"
    RESOLVED = "RESOLVED"
    CONFLICT = "CONFLICT"


class FiscalStatus(str, Enum):
    NOT_EVALUATED = "NOT_EVALUATED"
    PROPOSED = "PROPOSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"


class PaymentOrigin(str, Enum):
    COMPANY_ACCOUNT = "COMPANY_ACCOUNT"
    PERSONAL_FUNDS = "PERSONAL_FUNDS"
    THIRD_PARTY_FUNDS = "THIRD_PARTY_FUNDS"
    CASH_REGISTER = "CASH_REGISTER"
    UNKNOWN = "UNKNOWN"


class DocumentPaymentMethod(str, Enum):
    COMPANY_CARD = "COMPANY_CARD"
    COMPANY_TRANSFER = "COMPANY_TRANSFER"
    COMPANY_DIRECT_DEBIT = "COMPANY_DIRECT_DEBIT"
    COMPANY_CHEQUE = "COMPANY_CHEQUE"
    COMPANY_CASH = "COMPANY_CASH"
    PERSONAL_CARD = "PERSONAL_CARD"
    PERSONAL_CASH = "PERSONAL_CASH"
    PERSONAL_TRANSFER = "PERSONAL_TRANSFER"
    THIRD_PARTY_CARD = "THIRD_PARTY_CARD"
    THIRD_PARTY_CASH = "THIRD_PARTY_CASH"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class SettlementRoute(str, Enum):
    BANK_RECONCILIATION = "BANK_RECONCILIATION"
    DIRECTOR_CURRENT_ACCOUNT = "DIRECTOR_CURRENT_ACCOUNT"
    SHAREHOLDER_CURRENT_ACCOUNT = "SHAREHOLDER_CURRENT_ACCOUNT"
    EXPENSE_CLAIM = "EXPENSE_CLAIM"
    EMPLOYEE_REIMBURSEMENT = "EMPLOYEE_REIMBURSEMENT"
    THIRD_PARTY_REIMBURSEMENT = "THIRD_PARTY_REIMBURSEMENT"
    NO_REIMBURSEMENT = "NO_REIMBURSEMENT"
    ACCOUNTANT_DECISION_REQUIRED = "ACCOUNTANT_DECISION_REQUIRED"
    UNKNOWN = "UNKNOWN"


class SettlementStatus(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    APPROVED = "APPROVED"
    POSTED = "POSTED"
    PARTIALLY_REIMBURSED = "PARTIALLY_REIMBURSED"
    REIMBURSED = "REIMBURSED"
    REJECTED = "REJECTED"


class AuditResourceType(str, Enum):
    ACCOUNTING_DOCUMENT = "ACCOUNTING_DOCUMENT"
    BANK_TRANSACTION = "BANK_TRANSACTION"
    PAYMENT_BATCH = "PAYMENT_BATCH"
    PAYMENT_ITEM = "PAYMENT_ITEM"
    ALLOCATION = "ALLOCATION"
    SOURCE_FILE = "SOURCE_FILE"
    MASTER_TABLE = "MASTER_TABLE"
    IMPORT_BATCH = "IMPORT_BATCH"
    LOCK = "LOCK"


class AuditAction(str, Enum):
    CREATED = "CREATED"
    IMPORTED = "IMPORTED"
    UPDATED = "UPDATED"
    STATUS_CHANGED = "STATUS_CHANGED"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    ALLOCATED = "ALLOCATED"
    UNALLOCATED = "UNALLOCATED"
    FILE_MOVED = "FILE_MOVED"
    SOFT_DELETED = "SOFT_DELETED"
    RESTORED = "RESTORED"
    LOCK_ACQUIRED = "LOCK_ACQUIRED"
    LOCK_RENEWED = "LOCK_RENEWED"
    LOCK_RELEASED = "LOCK_RELEASED"
    LOCK_EXPIRED = "LOCK_EXPIRED"
    WRITE_REJECTED = "WRITE_REJECTED"
    CONFLICT_DETECTED = "CONFLICT_DETECTED"


class StatusDomain(str, Enum):
    INGESTION = "INGESTION"
    EXTRACTION = "EXTRACTION"
    VALIDATION = "VALIDATION"
    PROCESSING = "PROCESSING"
    RECONCILIATION = "RECONCILIATION"
    FILE_STATE = "FILE_STATE"
    IMPORT_BATCH = "IMPORT_BATCH"


class LockStatus(str, Enum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"
    ABORTED = "ABORTED"


_HASH_PATTERN = re.compile(r"[0-9a-fA-F]{64}", re.ASCII)
_CURRENCY_PATTERN = re.compile(r"[A-Z]{3}", re.ASCII)
_SIRET_PATTERN = re.compile(r"[0-9]{14}", re.ASCII)
_CENT = Decimal("0.01")


def _is_utc(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() == timedelta(0)


def _require_utc(value: datetime) -> datetime:
    if not _is_utc(value):
        raise ValueError("datetime must be timezone-aware UTC")
    return value


def _require_hash(value: str) -> str:
    if not _HASH_PATTERN.fullmatch(value):
        raise ValueError("content hash must be 64 hexadecimal characters")
    return value


def _decimal(value: Any) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("amount must be a valid Decimal") from exc
    if not result.is_finite():
        raise ValueError("amount must be finite")
    return result


def _nonblank(value: str | None, name: str) -> str | None:
    if value is not None and not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value


def _validate_soft_deletion(
    is_deleted: bool,
    deleted_at: datetime | None,
    deleted_by: ActorSystem | str | None,
    deletion_reason: str | None,
    deletion_operation_id: UUID | None,
) -> None:
    deletion_values = (
        deleted_at,
        deleted_by,
        deletion_reason,
        deletion_operation_id,
    )
    if is_deleted and any(value is None for value in deletion_values):
        raise ValueError("soft deletion requires timestamp, actor, reason, and operation")
    if not is_deleted and any(value is not None for value in deletion_values):
        raise ValueError("deleted_* fields must be absent when is_deleted is false")
    if deletion_reason is not None and not deletion_reason.strip():
        raise ValueError("deletion_reason must not be blank")


class _MasterModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceEvidence(_MasterModel):
    evidence_id: UUID
    source_type: EvidenceSourceType
    source_file_uri: str | None = None
    source_file_name: str | None = None
    source_hash: str | None = None
    source_email_id: str | None = None
    source_attachment_id: str | None = None
    page_number: StrictInt | None = None
    row_number: StrictInt | None = None
    raw_text: str | None = None
    extraction_method: str | None = None
    extraction_model: str | None = None
    confidence_score: float | None = None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "source_file_uri",
        "source_file_name",
        "source_email_id",
        "source_attachment_id",
        "raw_text",
        "extraction_method",
        "extraction_model",
    )
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _nonblank(value, "text")

    @field_validator("source_hash")
    @classmethod
    def validate_source_hash(cls, value: str | None) -> str | None:
        return None if value is None else _require_hash(value)

    @field_validator("page_number", "row_number")
    @classmethod
    def validate_positive_position(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("source positions must be positive")
        return value

    @field_validator("confidence_score")
    @classmethod
    def validate_confidence_score(cls, value: float | None) -> float | None:
        if value is not None and (not 0 <= value <= 1):
            raise ValueError("confidence_score must be between 0 and 1")
        return value

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return _require_utc(value)


class PaymentContribution(_MasterModel):
    contribution_id: UUID
    payment_origin: PaymentOrigin
    payment_method: DocumentPaymentMethod
    payer_id: UUID | None = None
    payer_name: str | None = None
    amount: Decimal
    company_bank_reconciliation_required: bool
    banking_transaction_id: UUID | None = None
    settlement_route: SettlementRoute
    settlement_status: SettlementStatus
    reimbursement_transaction_id: UUID | None = None
    evidence_ids: list[UUID] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("amount", mode="before")
    @classmethod
    def convert_amount(cls, value: Any) -> Decimal:
        return _decimal(value)

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("contribution amount must be positive")
        return value

    @field_validator("payer_name")
    @classmethod
    def validate_payer_name(cls, value: str | None) -> str | None:
        return _nonblank(value, "payer_name")

    @model_validator(mode="after")
    def validate_payment_contribution(self) -> PaymentContribution:
        if self.payment_origin == PaymentOrigin.PERSONAL_FUNDS:
            if self.payer_id is None and self.payer_name is None:
                raise ValueError("personal funds require an identified payer")
            if self.company_bank_reconciliation_required:
                raise ValueError(
                    "personal funds cannot require company bank reconciliation"
                )
            if self.banking_transaction_id is not None:
                raise ValueError(
                    "personal funds cannot reference a company banking transaction"
                )
        if (
            self.payment_origin == PaymentOrigin.COMPANY_ACCOUNT
            and not self.company_bank_reconciliation_required
        ):
            raise ValueError(
                "company account contributions require bank reconciliation"
            )
        if (
            self.settlement_route == SettlementRoute.BANK_RECONCILIATION
            and self.payment_origin != PaymentOrigin.COMPANY_ACCOUNT
        ):
            raise ValueError(
                "bank reconciliation settlement requires company account origin"
            )
        if (
            self.settlement_status == SettlementStatus.REIMBURSED
            and self.reimbursement_transaction_id is None
        ):
            raise ValueError(
                "reimbursed contributions require a reimbursement transaction"
            )
        return self


class _VersionedRow(_MasterModel):
    row_version: StrictInt
    content_hash: str
    created_at: datetime
    created_by: ActorSystem
    updated_at: datetime
    updated_by: ActorSystem
    last_operation_id: UUID
    import_batch_id: UUID
    idempotency_key: str
    source_evidence_ids: list[UUID] = Field(default_factory=list)
    is_deleted: bool = False
    deleted_at: datetime | None = None
    deleted_by: ActorSystem | None = None
    deletion_reason: str | None = None
    deletion_operation_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("row_version")
    @classmethod
    def validate_row_version(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("row_version must be positive")
        return value

    @field_validator("content_hash")
    @classmethod
    def validate_content_hash(cls, value: str) -> str:
        return _require_hash(value)

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("idempotency_key must not be blank")
        return value

    @field_validator("created_at", "updated_at", "deleted_at")
    @classmethod
    def validate_timestamps(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_utc(value)

    @field_validator("deletion_reason")
    @classmethod
    def validate_deletion_reason(cls, value: str | None) -> str | None:
        return _nonblank(value, "deletion_reason")

    @model_validator(mode="after")
    def validate_row_consistency(self) -> _VersionedRow:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        _validate_soft_deletion(
            self.is_deleted,
            self.deleted_at,
            self.deleted_by,
            self.deletion_reason,
            self.deletion_operation_id,
        )
        return self


class AccountingDocumentMasterRow(_VersionedRow):
    document_id: UUID
    company_id: str
    company_name_snapshot: str | None = None
    accounting_period: str | None = None
    business_unit: str | None = None
    direction: DocumentDirection
    document_type: DocumentMasterType
    document_number: str | None = None
    issue_date: date | None = None
    due_date: date | None = None
    purchase_order: str | None = None
    external_reference: str | None = None
    supplier_id: UUID | None = None
    supplier_name_observed: str | None = None
    supplier_siret_observed: str | None = None
    supplier_vat_number: str | None = None
    supplier_resolution_status: ResolutionStatus
    customer_id: UUID | None = None
    customer_name_observed: str | None = None
    customer_siret_observed: str | None = None
    customer_vat_number: str | None = None
    customer_resolution_status: ResolutionStatus
    currency: str
    net_amount: Decimal | None = None
    tax_amount: Decimal | None = None
    gross_amount: Decimal
    paid_amount: Decimal | None = None
    remaining_amount: Decimal
    discount_amount: Decimal
    rounding_tolerance: Decimal
    tax_regime: str | None = None
    category_id: UUID | None = None
    category_proposed: str | None = None
    category_confidence: float | None = None
    fiscal_rule_id: UUID | None = None
    fiscal_status: FiscalStatus
    accounting_account: str | None = None
    vat_account: str | None = None
    analytic_code: str | None = None
    ingestion_status: IngestionStatus
    extraction_status: ExtractionStatus
    validation_status: MasterValidationStatus
    processing_status: ProcessingStatus
    reconciliation_status: ReconciliationStatus
    current_file_state: FileState
    current_file_uri: str | None = None
    previous_file_uri: str | None = None
    file_move_status: FileMoveStatus
    file_move_operation_id: UUID | None = None
    payment_contributions: list[PaymentContribution] = Field(default_factory=list)
    business_purpose: str | None = None
    attendees_or_beneficiaries: list[str] = Field(default_factory=list)
    total_personal_amount: Decimal = Decimal("0")
    total_company_paid_amount: Decimal = Decimal("0")
    payment_origin_review_required: bool = False
    settlement_notes: str | None = None

    @field_validator("company_id")
    @classmethod
    def validate_company_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("company_id must not be blank")
        return value

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        if not _CURRENCY_PATTERN.fullmatch(value):
            raise ValueError("currency must contain three uppercase ASCII letters")
        return value

    @field_validator(
        "net_amount",
        "tax_amount",
        "gross_amount",
        "paid_amount",
        "remaining_amount",
        "discount_amount",
        "rounding_tolerance",
        mode="before",
    )
    @classmethod
    def convert_amounts(cls, value: Any) -> Decimal | None:
        return None if value is None else _decimal(value)

    @field_validator(
        "net_amount",
        "tax_amount",
        "gross_amount",
        "paid_amount",
        "remaining_amount",
        "discount_amount",
        "rounding_tolerance",
    )
    @classmethod
    def validate_nonnegative_amounts(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value < 0:
            raise ValueError("amounts must be non-negative")
        return value

    @field_validator("rounding_tolerance")
    @classmethod
    def validate_rounding_tolerance(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("rounding_tolerance must be positive")
        return value

    @field_validator("supplier_siret_observed", "customer_siret_observed")
    @classmethod
    def validate_siret(cls, value: str | None) -> str | None:
        if value is not None and not _SIRET_PATTERN.fullmatch(value):
            raise ValueError("SIRET must contain exactly 14 ASCII digits")
        return value

    @field_validator("category_confidence")
    @classmethod
    def validate_category_confidence(cls, value: float | None) -> float | None:
        if value is not None and (not 0 <= value <= 1):
            raise ValueError("category_confidence must be between 0 and 1")
        return value

    @field_validator(
        "company_name_snapshot",
        "accounting_period",
        "business_unit",
        "document_number",
        "purchase_order",
        "external_reference",
        "supplier_name_observed",
        "supplier_vat_number",
        "customer_name_observed",
        "customer_vat_number",
        "tax_regime",
        "category_proposed",
        "accounting_account",
        "vat_account",
        "analytic_code",
        "current_file_uri",
        "previous_file_uri",
        "business_purpose",
        "settlement_notes",
    )
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _nonblank(value, "text")

    @field_validator("attendees_or_beneficiaries")
    @classmethod
    def validate_attendees_or_beneficiaries(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("attendees_or_beneficiaries cannot contain blank values")
        return values

    @field_validator("total_personal_amount", "total_company_paid_amount", mode="before")
    @classmethod
    def convert_payment_totals(cls, value: Any) -> Decimal:
        return _decimal(value)

    @field_validator("total_personal_amount", "total_company_paid_amount")
    @classmethod
    def validate_payment_totals(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("payment totals must be non-negative")
        return value

    @model_validator(mode="after")
    def validate_document_consistency(self) -> AccountingDocumentMasterRow:
        if self.issue_date and self.due_date and self.due_date < self.issue_date:
            raise ValueError("due_date cannot precede issue_date")
        expected_remaining = (
            self.gross_amount
            - (self.paid_amount or Decimal("0"))
            - self.discount_amount
        )
        allowed_tolerance = min(self.rounding_tolerance, _CENT)
        if abs(self.remaining_amount - expected_remaining) > allowed_tolerance:
            raise ValueError("remaining_amount is inconsistent with paid and discount amounts")

        contributions_total = sum(
            (contribution.amount for contribution in self.payment_contributions),
            start=Decimal("0"),
        )
        personal_total = sum(
            (
                contribution.amount
                for contribution in self.payment_contributions
                if contribution.payment_origin == PaymentOrigin.PERSONAL_FUNDS
            ),
            start=Decimal("0"),
        )
        company_total = sum(
            (
                contribution.amount
                for contribution in self.payment_contributions
                if contribution.payment_origin == PaymentOrigin.COMPANY_ACCOUNT
            ),
            start=Decimal("0"),
        )
        if self.payment_contributions and abs(
            contributions_total - self.gross_amount
        ) > _CENT:
            raise ValueError("payment contributions must equal gross_amount")
        if abs(self.total_personal_amount - personal_total) > _CENT:
            raise ValueError("total_personal_amount does not match contributions")
        if abs(self.total_company_paid_amount - company_total) > _CENT:
            raise ValueError("total_company_paid_amount does not match contributions")
        if (
            self.payment_contributions
            and all(
                contribution.payment_origin == PaymentOrigin.PERSONAL_FUNDS
                for contribution in self.payment_contributions
            )
            and self.reconciliation_status != ReconciliationStatus.NOT_APPLICABLE
        ):
            raise ValueError(
                "all-personal contributions require NOT_APPLICABLE reconciliation"
            )
        if (
            any(
                contribution.payment_origin == PaymentOrigin.UNKNOWN
                for contribution in self.payment_contributions
            )
            and not self.payment_origin_review_required
        ):
            raise ValueError("unknown payment origin requires review")
        return self


class BankTransactionMasterRow(_VersionedRow):
    transaction_id: UUID
    banking_record: BankingRecord
    import_status: IngestionStatus

    @model_validator(mode="after")
    def validate_banking_record(self) -> BankTransactionMasterRow:
        if self.transaction_id != self.banking_record.transaction_id:
            raise ValueError("transaction_id must match banking_record.transaction_id")
        return self


class ImportBatch(_MasterModel):
    batch_id: UUID
    master_type: MasterType
    source_system: ActorSystem
    source_evidence_ids: list[UUID] = Field(default_factory=list)
    source_hash: str | None = None
    idempotency_key: str
    base_master_version: StrictInt
    started_at: datetime
    completed_at: datetime | None = None
    status: ImportBatchStatus
    record_count: StrictInt
    accepted_count: StrictInt
    rejected_count: StrictInt
    duplicate_count: StrictInt
    published_master_version: StrictInt | None = None
    operation_id: UUID
    error_summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("idempotency_key must not be blank")
        return value

    @field_validator("source_hash")
    @classmethod
    def validate_source_hash(cls, value: str | None) -> str | None:
        return None if value is None else _require_hash(value)

    @field_validator(
        "base_master_version",
        "record_count",
        "accepted_count",
        "rejected_count",
        "duplicate_count",
        "published_master_version",
    )
    @classmethod
    def validate_nonnegative_counters(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("batch counters and versions must be non-negative")
        return value

    @field_validator("started_at", "completed_at")
    @classmethod
    def validate_timestamps(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_utc(value)

    @field_validator("error_summary")
    @classmethod
    def validate_error_summary(cls, value: str | None) -> str | None:
        return _nonblank(value, "error_summary")

    @model_validator(mode="after")
    def validate_batch(self) -> ImportBatch:
        if (
            self.accepted_count + self.rejected_count + self.duplicate_count
            > self.record_count
        ):
            raise ValueError("processed counters cannot exceed record_count")
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        if self.status == ImportBatchStatus.COMPLETED and (
            self.completed_at is None or self.published_master_version is None
        ):
            raise ValueError("COMPLETED requires completion time and published version")
        if self.status == ImportBatchStatus.FAILED and (
            self.completed_at is None or self.error_summary is None
        ):
            raise ValueError("FAILED requires completion time and error summary")
        return self


class AuditEvent(_MasterModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID
    occurred_at: datetime
    operation_id: UUID
    batch_id: UUID | None = None
    resource_type: AuditResourceType
    resource_id: str
    action: AuditAction
    actor_system: ActorSystem
    actor_id: str | None = None
    previous_version: StrictInt | None = None
    new_version: StrictInt | None = None
    before_hash: str | None = None
    after_hash: str | None = None
    changed_fields: list[str] = Field(default_factory=list)
    reason: str | None = None
    evidence_refs: list[UUID] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @field_validator("resource_id")
    @classmethod
    def validate_resource_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("resource_id must not be blank")
        return value

    @field_validator("before_hash", "after_hash")
    @classmethod
    def validate_hashes(cls, value: str | None) -> str | None:
        return None if value is None else _require_hash(value)

    @field_validator("previous_version", "new_version")
    @classmethod
    def validate_versions(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("versions must be non-negative")
        return value

    @field_validator("changed_fields")
    @classmethod
    def validate_changed_fields(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("changed_fields cannot contain blank values")
        return values

    @field_validator("actor_id", "reason")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _nonblank(value, "text")

    @model_validator(mode="after")
    def validate_version_order(self) -> AuditEvent:
        if (
            self.previous_version is not None
            and self.new_version is not None
            and self.new_version < self.previous_version
        ):
            raise ValueError("new_version cannot precede previous_version")
        return self


class StatusHistory(_MasterModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status_history_id: UUID
    resource_type: AuditResourceType
    resource_id: str
    status_domain: StatusDomain
    status_from: str | None = None
    status_to: str
    changed_at: datetime
    changed_by: ActorSystem
    reason: str | None = None
    operation_id: UUID
    row_version_after_change: StrictInt

    @field_validator("resource_id", "status_to")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("resource_id and status_to must not be blank")
        return value

    @field_validator("status_from")
    @classmethod
    def validate_status_from(cls, value: str | None) -> str | None:
        return _nonblank(value, "status_from")

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str | None) -> str | None:
        return _nonblank(value, "reason")

    @field_validator("changed_at")
    @classmethod
    def validate_changed_at(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @field_validator("row_version_after_change")
    @classmethod
    def validate_row_version(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("row_version_after_change must be positive")
        return value

    @model_validator(mode="after")
    def validate_transition(self) -> StatusHistory:
        if self.status_from is not None and self.status_from == self.status_to:
            raise ValueError("status_from and status_to must differ")
        return self


class ResourceLock(_MasterModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lock_id: UUID
    resource_name: str
    owner_system: ActorSystem
    owner_instance_id: str
    operation_id: UUID
    acquired_at: datetime
    heartbeat_at: datetime
    expires_at: datetime
    base_version: StrictInt
    status: LockStatus
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("resource_name", "owner_instance_id")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("resource_name and owner_instance_id must not be blank")
        return value

    @field_validator("base_version")
    @classmethod
    def validate_base_version(cls, value: int) -> int:
        if value < 0:
            raise ValueError("base_version must be non-negative")
        return value

    @field_validator("acquired_at", "heartbeat_at", "expires_at")
    @classmethod
    def validate_timestamps(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def validate_lock(self) -> ResourceLock:
        if self.owner_system not in {ActorSystem.LOCAL, ActorSystem.ORACLE}:
            raise ValueError("lock owner must be LOCAL or ORACLE")
        if self.heartbeat_at < self.acquired_at:
            raise ValueError("heartbeat_at cannot precede acquired_at")
        if self.status == LockStatus.ACTIVE and self.expires_at <= self.heartbeat_at:
            raise ValueError("active lock must expire after heartbeat_at")
        return self

    def is_expired(self, now: datetime) -> bool:
        _require_utc(now)
        return self.status == LockStatus.EXPIRED or (
            self.status == LockStatus.ACTIVE and self.expires_at <= now
        )

    def can_be_renewed(
        self,
        now: datetime,
        owner_system: ActorSystem,
        owner_instance_id: str,
    ) -> bool:
        _require_utc(now)
        return (
            self.status == LockStatus.ACTIVE
            and self.expires_at > now
            and self.owner_system == owner_system
            and self.owner_instance_id == owner_instance_id
        )

    def renew(self, now: datetime, new_expires_at: datetime) -> ResourceLock:
        _require_utc(now)
        _require_utc(new_expires_at)
        if self.status == LockStatus.EXPIRED or (
            self.status == LockStatus.ACTIVE and self.expires_at <= now
        ):
            raise LockExpiredError("lock is not active or has expired")
        if self.status != LockStatus.ACTIVE:
            raise LockUnavailableError("lock is not active")
        if new_expires_at <= now:
            raise LockExpiredError("new lock expiry must be in the future")
        values = self.model_dump()
        values["heartbeat_at"] = now
        values["expires_at"] = new_expires_at
        return ResourceLock.model_validate(values)


class MasterVersion(_MasterModel):
    master_name: MasterType
    current_version: StrictInt
    content_hash: str
    last_operation_id: UUID
    updated_at: datetime
    updated_by: ActorSystem
    last_snapshot_uri: str | None = None
    applied_idempotency_keys: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("current_version")
    @classmethod
    def validate_current_version(cls, value: int) -> int:
        if value < 0:
            raise ValueError("current_version must be non-negative")
        return value

    @field_validator("content_hash")
    @classmethod
    def validate_content_hash(cls, value: str) -> str:
        return _require_hash(value)

    @field_validator("updated_at")
    @classmethod
    def validate_updated_at(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @field_validator("last_snapshot_uri")
    @classmethod
    def validate_snapshot_uri(cls, value: str | None) -> str | None:
        return _nonblank(value, "last_snapshot_uri")

    @field_validator("applied_idempotency_keys")
    @classmethod
    def validate_idempotency_keys(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("idempotency keys must not be blank")
        if len(set(values)) != len(values):
            raise ValueError("idempotency keys must be unique")
        return values


class MasterTableError(Exception):
    """Base exception for master table validation and write preconditions."""


class LockUnavailableError(MasterTableError):
    """Raised when a resource is locked by another active writer."""


class LockOwnershipError(MasterTableError):
    """Raised when the caller does not own the resource lock."""


class LockExpiredError(MasterTableError):
    """Raised when an operation uses an expired lock."""


class VersionConflictError(MasterTableError):
    """Raised when optimistic concurrency versions do not match."""


class DuplicateOperationError(MasterTableError):
    """Raised when an idempotency key has already been applied."""


def can_acquire_lock(existing_lock: ResourceLock | None, *, now: datetime) -> bool:
    _require_utc(now)
    if existing_lock is None or existing_lock.status != LockStatus.ACTIVE:
        return True
    return existing_lock.is_expired(now)


def validate_write_preconditions(
    *,
    lock: ResourceLock,
    now: datetime,
    owner_system: ActorSystem,
    owner_instance_id: str,
    expected_base_version: int,
    master_version: MasterVersion,
    idempotency_key: str,
) -> None:
    _require_utc(now)
    if lock.status == LockStatus.EXPIRED or lock.is_expired(now):
        raise LockExpiredError("resource lock has expired")
    if lock.status != LockStatus.ACTIVE:
        raise LockUnavailableError("resource lock is not active")
    if (
        lock.owner_system != owner_system
        or lock.owner_instance_id != owner_instance_id
    ):
        raise LockOwnershipError("caller does not own the resource lock")
    if not idempotency_key.strip():
        raise MasterTableError("idempotency_key must not be blank")
    if (
        expected_base_version != master_version.current_version
        or lock.base_version != master_version.current_version
    ):
        raise VersionConflictError("master version changed since lock acquisition")
    if idempotency_key in master_version.applied_idempotency_keys:
        raise DuplicateOperationError("idempotency_key has already been applied")


def next_master_version(
    current: MasterVersion,
    *,
    operation_id: UUID,
    actor: ActorSystem,
    idempotency_key: str,
    content_hash: str,
    updated_at: datetime,
    snapshot_uri: str | None = None,
) -> MasterVersion:
    if not idempotency_key.strip():
        raise MasterTableError("idempotency_key must not be blank")
    if idempotency_key in current.applied_idempotency_keys:
        raise DuplicateOperationError("idempotency_key has already been applied")
    _require_hash(content_hash)
    _require_utc(updated_at)
    if updated_at < current.updated_at:
        raise MasterTableError("updated_at cannot precede current master version")
    return MasterVersion(
        master_name=current.master_name,
        current_version=current.current_version + 1,
        content_hash=content_hash,
        last_operation_id=operation_id,
        updated_at=updated_at,
        updated_by=actor,
        last_snapshot_uri=snapshot_uri,
        applied_idempotency_keys=[
            *current.applied_idempotency_keys,
            idempotency_key,
        ],
        metadata=deepcopy(current.metadata),
    )
