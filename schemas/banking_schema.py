"""Canonical contracts for bank accounts, transactions, and reconciliations."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
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


class BankTransactionDirection(str, Enum):
    CREDIT = "CREDIT"
    DEBIT = "DEBIT"


class BankTransactionType(str, Enum):
    TRANSFER = "TRANSFER"
    DIRECT_DEBIT = "DIRECT_DEBIT"
    CARD = "CARD"
    CHEQUE = "CHEQUE"
    CHEQUE_DEPOSIT = "CHEQUE_DEPOSIT"
    CASH = "CASH"
    FEE = "FEE"
    INTEREST = "INTEREST"
    REFUND = "REFUND"
    UNKNOWN = "UNKNOWN"


class BankAccountType(str, Enum):
    CHECKING = "CHECKING"
    SAVINGS = "SAVINGS"
    CARD = "CARD"
    CASH = "CASH"
    OTHER = "OTHER"


class CounterpartyType(str, Enum):
    SUPPLIER = "SUPPLIER"
    CUSTOMER = "CUSTOMER"
    EMPLOYEE = "EMPLOYEE"
    BANK = "BANK"
    TAX_AUTHORITY = "TAX_AUTHORITY"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class ReconciliationStatus(str, Enum):
    UNMATCHED = "UNMATCHED"
    PROPOSED = "PROPOSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    PARTIALLY_MATCHED = "PARTIALLY_MATCHED"
    MATCHED = "MATCHED"
    REJECTED = "REJECTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class AllocationTargetType(str, Enum):
    INVOICE = "INVOICE"
    CREDIT_NOTE = "CREDIT_NOTE"
    EXPENSE = "EXPENSE"
    PAYROLL = "PAYROLL"
    TAX = "TAX"
    BANK_FEE = "BANK_FEE"
    OTHER = "OTHER"


class PaymentMethod(str, Enum):
    TRANSFER = "TRANSFER"
    DIRECT_DEBIT = "DIRECT_DEBIT"
    CARD = "CARD"
    CHEQUE = "CHEQUE"
    CASH = "CASH"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class PaymentBatchType(str, Enum):
    LCR_REMITTANCE = "LCR_REMITTANCE"
    CHEQUE_DEPOSIT = "CHEQUE_DEPOSIT"
    GROUPED_TRANSFER = "GROUPED_TRANSFER"
    DIRECT_DEBIT_BATCH = "DIRECT_DEBIT_BATCH"
    CARD_SETTLEMENT = "CARD_SETTLEMENT"
    OTHER = "OTHER"


class PaymentBatchStatus(str, Enum):
    DETAIL_MISSING = "DETAIL_MISSING"
    PARTIALLY_DETAILED = "PARTIALLY_DETAILED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    RECONCILED = "RECONCILED"
    REJECTED = "REJECTED"


class PaymentItemStatus(str, Enum):
    UNMATCHED = "UNMATCHED"
    PROPOSED = "PROPOSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    PARTIALLY_MATCHED = "PARTIALLY_MATCHED"
    MATCHED = "MATCHED"
    REJECTED = "REJECTED"


_CURRENCY_PATTERN = re.compile(r"[A-Z]{3}", re.ASCII)
_IBAN_PATTERN = re.compile(r"[A-Z0-9]{15,34}", re.ASCII)
_SIRET_PATTERN = re.compile(r"[0-9]{14}", re.ASCII)
_HASH_PATTERN = re.compile(r"[0-9a-fA-F]{64}", re.ASCII)
_MONEY_TOLERANCE = Decimal("0.01")


def _decimal_from_value(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("value must be a valid decimal") from exc


def _validate_currency(value: str) -> str:
    if not _CURRENCY_PATTERN.fullmatch(value):
        raise ValueError("currency must contain exactly three uppercase ASCII letters")
    return value


def _normalize_iban(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = "".join(value.split()).upper()
    if not _IBAN_PATTERN.fullmatch(normalized):
        raise ValueError("IBAN must contain 15 to 34 ASCII alphanumeric characters")
    return normalized


def _require_nonblank(value: str | None, field_name: str) -> str | None:
    if value is not None and not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


class _StrictBankingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BankAccountReference(_StrictBankingModel):
    account_id: UUID
    company_id: str
    account_type: BankAccountType
    iban: str | None = None
    bic: str | None = None
    bank_name: str | None = None
    account_label: str | None = None
    holder_name: str | None = None
    currency: str = "EUR"
    active_from: date | None = None
    active_to: date | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("company_id")
    @classmethod
    def validate_company_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("company_id must not be blank")
        return value

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        return _validate_currency(value)

    @field_validator("iban")
    @classmethod
    def normalize_iban(cls, value: str | None) -> str | None:
        return _normalize_iban(value)

    @field_validator("bic", "bank_name", "account_label", "holder_name")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _require_nonblank(value, "text")

    @model_validator(mode="after")
    def validate_active_period(self) -> "BankAccountReference":
        if (
            self.active_from is not None
            and self.active_to is not None
            and self.active_to < self.active_from
        ):
            raise ValueError("active_to cannot precede active_from")
        return self


class BankCounterparty(_StrictBankingModel):
    counterparty_id: UUID | None = None
    counterparty_type: CounterpartyType = CounterpartyType.UNKNOWN
    display_name: str
    normalized_name: str | None = None
    siret: str | None = None
    iban: str | None = None
    aliases: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("display_name must not be blank")
        return value

    @field_validator("normalized_name")
    @classmethod
    def validate_normalized_name(cls, value: str | None) -> str | None:
        return _require_nonblank(value, "normalized_name")

    @field_validator("siret")
    @classmethod
    def validate_siret(cls, value: str | None) -> str | None:
        if value is not None and not _SIRET_PATTERN.fullmatch(value):
            raise ValueError("siret must contain exactly 14 ASCII digits")
        return value

    @field_validator("iban")
    @classmethod
    def normalize_iban(cls, value: str | None) -> str | None:
        return _normalize_iban(value)

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("aliases must not contain blank values")
        return values


class ChequeReference(_StrictBankingModel):
    cheque_number: str | None = None
    issuer_name: str | None = None
    deposit_reference: str | None = None
    deposit_date: date | None = None
    cheque_count: int | None = None
    total_deposit_amount: Decimal | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("cheque_number", "issuer_name", "deposit_reference")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _require_nonblank(value, "text")

    @field_validator("cheque_count")
    @classmethod
    def validate_cheque_count(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("cheque_count must be positive")
        return value

    @field_validator("total_deposit_amount", mode="before")
    @classmethod
    def convert_total_deposit_amount(cls, value: Any) -> Any:
        return None if value is None else _decimal_from_value(value)

    @field_validator("total_deposit_amount")
    @classmethod
    def validate_total_deposit_amount(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and (not value.is_finite() or value < 0):
            raise ValueError("total_deposit_amount must be non-negative and finite")
        return value


class ReconciliationAllocation(_StrictBankingModel):
    allocation_id: UUID
    target_type: AllocationTargetType
    target_id: UUID
    allocated_amount: Decimal
    confidence_score: float | None = None
    allocation_status: ReconciliationStatus
    reason: str | None = None
    created_at: datetime
    created_by: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("allocated_amount", mode="before")
    @classmethod
    def convert_allocated_amount(cls, value: Any) -> Decimal:
        return _decimal_from_value(value)

    @field_validator("allocated_amount")
    @classmethod
    def validate_allocated_amount(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError("allocated_amount must be positive and finite")
        return value

    @field_validator("confidence_score")
    @classmethod
    def validate_confidence_score(cls, value: float | None) -> float | None:
        if value is not None and (not 0 <= value <= 1):
            raise ValueError("confidence_score must be between 0 and 1")
        return value

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str | None) -> str | None:
        return _require_nonblank(value, "reason")

    @field_validator("created_by")
    @classmethod
    def validate_created_by(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("created_by must not be blank")
        return value

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("created_at must be timezone-aware UTC")
        return value


class PaymentBatchItem(_StrictBankingModel):
    item_id: UUID
    counterparty: BankCounterparty | None = None
    observed_name: str | None = None
    effect_reference: str | None = None
    invoice_reference: str | None = None
    due_date: date | None = None
    amount: Decimal
    status: PaymentItemStatus
    allocations: list[ReconciliationAllocation] = Field(default_factory=list)
    unallocated_amount: Decimal
    confidence_score: float | None = None
    source_evidence_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("amount", "unallocated_amount", mode="before")
    @classmethod
    def convert_money(cls, value: Any) -> Decimal:
        return _decimal_from_value(value)

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError("amount must be positive and finite")
        return value

    @field_validator("unallocated_amount")
    @classmethod
    def validate_unallocated_amount(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0:
            raise ValueError("unallocated_amount must be non-negative and finite")
        return value

    @field_validator(
        "observed_name",
        "effect_reference",
        "invoice_reference",
        "source_evidence_ref",
    )
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _require_nonblank(value, "text")

    @field_validator("confidence_score")
    @classmethod
    def validate_confidence_score(cls, value: float | None) -> float | None:
        if value is not None and (not 0 <= value <= 1):
            raise ValueError("confidence_score must be between 0 and 1")
        return value

    @model_validator(mode="after")
    def validate_item_consistency(self) -> PaymentBatchItem:
        allocated_amount = sum(
            (allocation.allocated_amount for allocation in self.allocations),
            start=Decimal("0"),
        )
        if allocated_amount > self.amount:
            raise ValueError("allocations cannot exceed item amount")
        expected_unallocated = self.amount - allocated_amount
        if abs(self.unallocated_amount - expected_unallocated) > _MONEY_TOLERANCE:
            raise ValueError("unallocated_amount does not match item allocations")

        if self.status == PaymentItemStatus.MATCHED:
            if self.unallocated_amount != 0 or allocated_amount != self.amount:
                raise ValueError("MATCHED item requires allocations covering its amount")
            if any(
                allocation.allocation_status != ReconciliationStatus.MATCHED
                for allocation in self.allocations
            ):
                raise ValueError("MATCHED item requires validated allocations")
        elif self.status == PaymentItemStatus.PARTIALLY_MATCHED:
            if self.unallocated_amount <= 0 or allocated_amount <= 0:
                raise ValueError("PARTIALLY_MATCHED item requires allocations and a remainder")
        return self


class PaymentBatch(_StrictBankingModel):
    batch_id: UUID
    batch_type: PaymentBatchType
    batch_reference: str | None = None
    booking_date: date
    total_amount: Decimal
    currency: str
    expected_item_count: int | None = None
    items: list[PaymentBatchItem] = Field(default_factory=list)
    status: PaymentBatchStatus
    unexplained_amount: Decimal
    source_evidence_refs: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("total_amount", "unexplained_amount", mode="before")
    @classmethod
    def convert_money(cls, value: Any) -> Decimal:
        return _decimal_from_value(value)

    @field_validator("total_amount")
    @classmethod
    def validate_total_amount(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError("total_amount must be positive and finite")
        return value

    @field_validator("unexplained_amount")
    @classmethod
    def validate_unexplained_amount(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0:
            raise ValueError("unexplained_amount must be non-negative and finite")
        return value

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        return _validate_currency(value)

    @field_validator("batch_reference")
    @classmethod
    def validate_batch_reference(cls, value: str | None) -> str | None:
        return _require_nonblank(value, "batch_reference")

    @field_validator("expected_item_count")
    @classmethod
    def validate_expected_item_count(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("expected_item_count must be positive")
        return value

    @field_validator("source_evidence_refs")
    @classmethod
    def validate_source_evidence_refs(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("source_evidence_refs must not contain blank values")
        return values

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_timestamps(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("timestamps must be timezone-aware UTC")
        return value

    @model_validator(mode="after")
    def validate_batch_consistency(self) -> PaymentBatch:
        try:
            timestamps_are_out_of_order = self.updated_at < self.created_at
        except TypeError as exc:
            raise ValueError("created_at and updated_at must be comparable") from exc
        if timestamps_are_out_of_order:
            raise ValueError("updated_at cannot precede created_at")

        items_total = sum(
            (item.amount for item in self.items),
            start=Decimal("0"),
        )
        if items_total > self.total_amount:
            raise ValueError("items cannot exceed batch total_amount")
        expected_unexplained = self.total_amount - items_total
        if abs(self.unexplained_amount - expected_unexplained) > _MONEY_TOLERANCE:
            raise ValueError("unexplained_amount does not match item total")

        if self.expected_item_count is not None and len(self.items) > self.expected_item_count:
            raise ValueError("items cannot exceed expected_item_count")

        if self.status == PaymentBatchStatus.DETAIL_MISSING and self.items:
            raise ValueError("DETAIL_MISSING batch cannot contain items")
        if self.status == PaymentBatchStatus.PARTIALLY_DETAILED:
            if not self.items or self.unexplained_amount <= 0:
                raise ValueError("PARTIALLY_DETAILED requires items and a positive remainder")
        if self.status == PaymentBatchStatus.RECONCILED:
            if items_total != self.total_amount or self.unexplained_amount != 0:
                raise ValueError("RECONCILED requires items covering the full batch total")
            if any(item.status != PaymentItemStatus.MATCHED for item in self.items):
                raise ValueError("RECONCILED requires all items to be MATCHED")
        return self


class BankingRecord(_StrictBankingModel):
    transaction_id: UUID
    account: BankAccountReference
    booking_date: date
    value_date: date | None = None
    direction: BankTransactionDirection
    transaction_type: BankTransactionType
    payment_method: PaymentMethod
    amount: Decimal
    currency: str = "EUR"
    raw_label: str
    normalized_label: str | None = None
    bank_reference: str | None = None
    counterparty: BankCounterparty | None = None
    cheque: ChequeReference | None = None
    reconciliation_status: ReconciliationStatus = ReconciliationStatus.UNMATCHED
    allocations: list[ReconciliationAllocation] = Field(default_factory=list)
    payment_batch: PaymentBatch | None = None
    unallocated_amount: Decimal
    statement_id: UUID | None = None
    source_file: str | None = None
    import_batch_id: UUID
    source_evidence_ids: list[UUID] = Field(default_factory=list)
    row_version: StrictInt
    content_hash: str
    is_deleted: bool = False
    deleted_at: datetime | None = None
    deleted_by: str | None = None
    deletion_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("amount", "unallocated_amount", mode="before")
    @classmethod
    def convert_money(cls, value: Any) -> Decimal:
        return _decimal_from_value(value)

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError("amount must be positive and finite")
        return value

    @field_validator("unallocated_amount")
    @classmethod
    def validate_unallocated_amount(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0:
            raise ValueError("unallocated_amount must be non-negative and finite")
        return value

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        return _validate_currency(value)

    @field_validator("raw_label")
    @classmethod
    def validate_raw_label(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("raw_label must not be blank")
        return value

    @field_validator("row_version")
    @classmethod
    def validate_row_version(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("row_version must be positive")
        return value

    @field_validator("content_hash")
    @classmethod
    def validate_content_hash(cls, value: str) -> str:
        if not _HASH_PATTERN.fullmatch(value):
            raise ValueError("content_hash must be a 64-character SHA-256 hex digest")
        return value

    @field_validator("normalized_label", "bank_reference", "source_file")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _require_nonblank(value, "text")

    @field_validator("deleted_by", "deletion_reason")
    @classmethod
    def validate_deletion_text(cls, value: str | None) -> str | None:
        return _require_nonblank(value, "deletion detail")

    @field_validator("created_at", "updated_at", "deleted_at")
    @classmethod
    def validate_utc_timestamps(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() != timedelta(0)
        ):
            raise ValueError("timestamps must be timezone-aware UTC")
        return value

    @model_validator(mode="after")
    def validate_record_consistency(self) -> "BankingRecord":
        try:
            timestamp_is_out_of_order = self.updated_at < self.created_at
        except TypeError as exc:
            raise ValueError("created_at and updated_at must be comparable") from exc
        if timestamp_is_out_of_order:
            raise ValueError("updated_at cannot precede created_at")
        if self.is_deleted:
            if (
                self.deleted_at is None
                or self.deleted_by is None
                or self.deletion_reason is None
            ):
                raise ValueError(
                    "soft deletion requires deleted_at, deleted_by, and deletion_reason"
                )
        elif any(
            value is not None
            for value in (self.deleted_at, self.deleted_by, self.deletion_reason)
        ):
            raise ValueError("deleted_* fields must be absent when is_deleted is false")

        if (
            self.cheque is not None
            and self.transaction_type
            not in {BankTransactionType.CHEQUE, BankTransactionType.CHEQUE_DEPOSIT}
        ):
            raise ValueError("cheque is only valid for cheque transaction types")

        if self.payment_batch is not None:
            if self.allocations:
                raise ValueError(
                    "direct allocations and payment_batch cannot be combined"
                )
            if abs(self.payment_batch.total_amount - self.amount) > _MONEY_TOLERANCE:
                raise ValueError("payment_batch total must match transaction amount")
            if self.transaction_type == BankTransactionType.CHEQUE_DEPOSIT:
                if self.payment_batch.batch_type != PaymentBatchType.CHEQUE_DEPOSIT:
                    raise ValueError(
                        "CHEQUE_DEPOSIT transaction requires a CHEQUE_DEPOSIT batch"
                    )

        allocated_amount = sum(
            (allocation.allocated_amount for allocation in self.allocations),
            start=Decimal("0"),
        )
        if allocated_amount > self.amount:
            raise ValueError("allocations cannot exceed amount")

        expected_unallocated = self.amount - allocated_amount
        if abs(self.unallocated_amount - expected_unallocated) > _MONEY_TOLERANCE:
            raise ValueError("unallocated_amount does not match the allocation total")

        if not self.allocations:
            allowed_without_allocations = {
                ReconciliationStatus.UNMATCHED,
                ReconciliationStatus.PROPOSED,
                ReconciliationStatus.NEEDS_REVIEW,
                ReconciliationStatus.REJECTED,
            }
            if self.reconciliation_status not in allowed_without_allocations:
                raise ValueError("reconciliation status requires allocations")

        if allocated_amount > 0 and allocated_amount < self.amount:
            if self.reconciliation_status not in {
                ReconciliationStatus.PARTIALLY_MATCHED,
                ReconciliationStatus.NEEDS_REVIEW,
            }:
                raise ValueError(
                    "partial allocations require PARTIALLY_MATCHED or NEEDS_REVIEW"
                )

        if self.reconciliation_status == ReconciliationStatus.MATCHED:
            if self.unallocated_amount != 0 or allocated_amount != self.amount:
                raise ValueError("MATCHED requires allocations covering the full amount")
            if any(
                allocation.allocation_status != ReconciliationStatus.MATCHED
                for allocation in self.allocations
            ):
                raise ValueError("MATCHED requires validated allocations")

        if (
            self.reconciliation_status == ReconciliationStatus.PARTIALLY_MATCHED
            and self.unallocated_amount <= 0
        ):
            raise ValueError("PARTIALLY_MATCHED requires a positive unallocated amount")

        return self
