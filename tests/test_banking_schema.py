from datetime import date, datetime, timezone
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
    ChequeReference,
    CounterpartyType,
    PaymentMethod,
    PaymentBatch,
    PaymentBatchItem,
    PaymentBatchStatus,
    PaymentBatchType,
    PaymentItemStatus,
    ReconciliationAllocation,
    ReconciliationStatus,
)


CREATED_AT = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
UPDATED_AT = datetime(2026, 10, 15, 9, 5, tzinfo=timezone.utc)
TRANSACTION_ID = UUID("804fab25-8e30-4cf5-9b2b-5b753822d047")
ACCOUNT_ID = UUID("6b3e25f2-3b5e-45fa-8de0-c7e89e12d341")
INVOICE_ID = UUID("da6df403-5abd-4fe4-bc72-640d01c3a1d9")


def account_data(**overrides: Any) -> dict[str, Any]:
    return {
        "account_id": ACCOUNT_ID,
        "company_id": "company-1",
        "account_type": BankAccountType.CHECKING,
        **overrides,
    }


def account(**overrides: Any) -> BankAccountReference:
    return BankAccountReference(**account_data(**overrides))


def allocation_data(**overrides: Any) -> dict[str, Any]:
    return {
        "allocation_id": UUID("f44a70a9-493e-4775-9dfa-d5c9284242aa"),
        "target_type": AllocationTargetType.INVOICE,
        "target_id": INVOICE_ID,
        "allocated_amount": Decimal("100.00"),
        "allocation_status": ReconciliationStatus.MATCHED,
        "created_at": CREATED_AT,
        "created_by": "reviewer-1",
        **overrides,
    }


def allocation(**overrides: Any) -> ReconciliationAllocation:
    return ReconciliationAllocation(**allocation_data(**overrides))


def record_data(**overrides: Any) -> dict[str, Any]:
    return {
        "transaction_id": TRANSACTION_ID,
        "account": account(),
        "booking_date": date(2026, 10, 14),
        "direction": BankTransactionDirection.DEBIT,
        "transaction_type": BankTransactionType.TRANSFER,
        "payment_method": PaymentMethod.TRANSFER,
        "amount": Decimal("100.00"),
        "raw_label": "Supplier payment",
        "unallocated_amount": Decimal("100.00"),
        "import_batch_id": UUID("c43d3549-c605-4a98-9e68-bef2d144c922"),
        "row_version": 1,
        "content_hash": "0" * 64,
        "created_at": CREATED_AT,
        "updated_at": UPDATED_AT,
        **overrides,
    }


def record(**overrides: Any) -> BankingRecord:
    return BankingRecord(**record_data(**overrides))


def batch_item_data(**overrides: Any) -> dict[str, Any]:
    return {
        "item_id": UUID("41ef44b5-8ea1-44d8-9023-c302cab20710"),
        "amount": Decimal("4250.00"),
        "status": PaymentItemStatus.UNMATCHED,
        "unallocated_amount": Decimal("4250.00"),
        **overrides,
    }


def batch_item(**overrides: Any) -> PaymentBatchItem:
    return PaymentBatchItem(**batch_item_data(**overrides))


def batch_data(**overrides: Any) -> dict[str, Any]:
    return {
        "batch_id": UUID("27ab391c-3bc4-4a98-b785-b46a8a1ce418"),
        "batch_type": PaymentBatchType.LCR_REMITTANCE,
        "batch_reference": "LCR-2026-10-15",
        "booking_date": date(2026, 10, 15),
        "total_amount": Decimal("42500.00"),
        "currency": "EUR",
        "expected_item_count": 10,
        "status": PaymentBatchStatus.DETAIL_MISSING,
        "unexplained_amount": Decimal("42500.00"),
        "created_at": CREATED_AT,
        "updated_at": UPDATED_AT,
        **overrides,
    }


def payment_batch(**overrides: Any) -> PaymentBatch:
    return PaymentBatch(**batch_data(**overrides))


def ten_lcr_items(*, matched: bool = False) -> list[PaymentBatchItem]:
    items: list[PaymentBatchItem] = []
    for index in range(10):
        amount = Decimal("4250.00")
        target_id = UUID(int=index + 1)
        matching_allocation = allocation(
            allocation_id=UUID(int=100 + index),
            target_id=target_id,
            allocated_amount=amount,
        )
        items.append(
            batch_item(
                item_id=UUID(int=200 + index),
                counterparty=BankCounterparty(
                    counterparty_id=UUID(int=300 + index),
                    counterparty_type=CounterpartyType.CUSTOMER,
                    display_name=f"Customer {index + 1}",
                ),
                observed_name=f"Customer {index + 1}",
                effect_reference=f"EFFECT-{index + 1:02}",
                amount=amount,
                status=(
                    PaymentItemStatus.MATCHED
                    if matched
                    else PaymentItemStatus.UNMATCHED
                ),
                allocations=[matching_allocation] if matched else [],
                unallocated_amount=Decimal("0") if matched else amount,
                source_evidence_ref=f"statement-row-{index + 1}",
            )
        )
    return items


def test_valid_debit_and_credit_keep_direction_separate_from_positive_amount() -> None:
    debit = record()
    credit = record(
        transaction_id=UUID("144fb69c-fcb1-4c07-9eb8-a3c95099d87c"),
        direction=BankTransactionDirection.CREDIT,
        amount="75.50",
        unallocated_amount="75.50",
    )

    assert debit.direction == BankTransactionDirection.DEBIT
    assert debit.amount == Decimal("100.00")
    assert credit.direction == BankTransactionDirection.CREDIT
    assert credit.amount == Decimal("75.50")


@pytest.mark.parametrize("amount", ["-1", "0"])
def test_non_positive_amount_is_rejected(amount: str) -> None:
    with pytest.raises(ValidationError):
        record(amount=amount, unallocated_amount=amount)


@pytest.mark.parametrize("currency", ["eur", "EU", "€12", "ＡＢＣ"])
def test_invalid_non_ascii_currency_is_rejected(currency: str) -> None:
    with pytest.raises(ValidationError):
        record(currency=currency)


def test_blank_raw_label_is_rejected() -> None:
    with pytest.raises(ValidationError):
        record(raw_label=" \t ")


def test_account_holder_may_differ_from_company_id_and_period_is_valid() -> None:
    value = account(
        company_id="company-1",
        holder_name="Separate legal holder",
        active_from=date(2020, 1, 1),
        active_to=date(2026, 10, 15),
    )

    assert value.holder_name == "Separate legal holder"
    assert value.company_id == "company-1"


def test_inverted_account_period_is_rejected() -> None:
    with pytest.raises(ValidationError):
        account(active_from=date(2026, 10, 16), active_to=date(2026, 10, 15))


def test_siret_accepts_exactly_fourteen_ascii_digits() -> None:
    counterparty = BankCounterparty(display_name="Supplier", siret="12345678901234")

    assert counterparty.siret == "12345678901234"


@pytest.mark.parametrize("siret", ["123", "1234567890123A", "１２３４５６７８９０１２３４"])
def test_invalid_siret_is_rejected(siret: str) -> None:
    with pytest.raises(ValidationError):
        BankCounterparty(display_name="Supplier", siret=siret)


def test_iban_is_normalized_for_accounts_and_counterparties() -> None:
    account_ref = account(iban=" fr76 3000 6000 0112 3456 7890 189 ")
    counterparty = BankCounterparty(
        display_name="Supplier",
        iban="fr7630006000011234567890189",
    )

    assert account_ref.iban == "FR7630006000011234567890189"
    assert counterparty.iban == "FR7630006000011234567890189"


def test_complete_allocation_can_be_matched() -> None:
    result = record(
        allocations=[allocation()],
        reconciliation_status=ReconciliationStatus.MATCHED,
        unallocated_amount="0.00",
    )

    assert result.reconciliation_status == ReconciliationStatus.MATCHED
    assert result.unallocated_amount == Decimal("0.00")


def test_partial_allocation_is_represented_explicitly() -> None:
    result = record(
        amount="100.00",
        allocations=[allocation(allocated_amount="40.00")],
        reconciliation_status=ReconciliationStatus.PARTIALLY_MATCHED,
        unallocated_amount="60.00",
    )

    assert result.allocations[0].allocated_amount == Decimal("40.00")
    assert result.unallocated_amount == Decimal("60.00")


def test_multiple_allocations_can_cover_one_transaction() -> None:
    first = allocation(allocated_amount="45.00")
    second = allocation(
        allocation_id=UUID("d56f1767-f7cd-4463-ae28-8e6aa5788221"),
        target_id=UUID("b57d37b8-4da1-49d6-a190-a60502bf5611"),
        allocated_amount="55.00",
    )

    result = record(
        allocations=[first, second],
        reconciliation_status=ReconciliationStatus.MATCHED,
        unallocated_amount="0",
    )

    assert len(result.allocations) == 2
    assert sum((item.allocated_amount for item in result.allocations), Decimal()) == result.amount


def test_allocations_cannot_exceed_transaction_amount() -> None:
    with pytest.raises(ValidationError):
        record(
            allocations=[allocation(allocated_amount="100.01")],
            reconciliation_status=ReconciliationStatus.NEEDS_REVIEW,
            unallocated_amount="0",
        )


def test_unallocated_amount_must_match_allocations_with_at_most_one_cent_tolerance() -> None:
    tolerated = record(
        allocations=[allocation(allocated_amount="40.00")],
        reconciliation_status=ReconciliationStatus.NEEDS_REVIEW,
        unallocated_amount="60.01",
    )
    assert tolerated.unallocated_amount == Decimal("60.01")

    with pytest.raises(ValidationError):
        record(
            allocations=[allocation(allocated_amount="40.00")],
            reconciliation_status=ReconciliationStatus.NEEDS_REVIEW,
            unallocated_amount="60.02",
        )


def test_matched_record_cannot_have_positive_remainder() -> None:
    with pytest.raises(ValidationError):
        record(
            allocations=[allocation(allocated_amount="99.50")],
            reconciliation_status=ReconciliationStatus.MATCHED,
            unallocated_amount="0.50",
        )


def test_partially_matched_record_requires_positive_remainder() -> None:
    with pytest.raises(ValidationError):
        record(
            allocations=[allocation()],
            reconciliation_status=ReconciliationStatus.PARTIALLY_MATCHED,
            unallocated_amount="0",
        )


def test_payment_without_allocation_is_unmatched() -> None:
    result = record()

    assert result.allocations == []
    assert result.reconciliation_status == ReconciliationStatus.UNMATCHED


def test_distinct_payments_can_reference_the_same_invoice() -> None:
    first = record(
        transaction_id=UUID("144fb69c-fcb1-4c07-9eb8-a3c95099d87c"),
        allocations=[allocation(allocated_amount="20.00")],
        reconciliation_status=ReconciliationStatus.PARTIALLY_MATCHED,
        amount="100.00",
        unallocated_amount="80.00",
    )
    second = record(
        transaction_id=UUID("d39fbd81-4628-499e-879d-f3344a019753"),
        allocations=[allocation(
            allocation_id=UUID("d56f1767-f7cd-4463-ae28-8e6aa5788221"),
            allocated_amount="30.00",
        )],
        reconciliation_status=ReconciliationStatus.PARTIALLY_MATCHED,
        amount="100.00",
        unallocated_amount="70.00",
    )

    assert first.allocations[0].target_id == second.allocations[0].target_id


def test_partial_payments_can_accumulate_on_one_invoice() -> None:
    payments = [
        record(
            transaction_id=UUID("144fb69c-fcb1-4c07-9eb8-a3c95099d87c"),
            amount="100.00",
            allocations=[allocation(allocated_amount="35.00")],
            reconciliation_status=ReconciliationStatus.PARTIALLY_MATCHED,
            unallocated_amount="65.00",
        ),
        record(
            transaction_id=UUID("d39fbd81-4628-499e-879d-f3344a019753"),
            amount="100.00",
            allocations=[allocation(
                allocation_id=UUID("d56f1767-f7cd-4463-ae28-8e6aa5788221"),
                allocated_amount="65.00",
            )],
            reconciliation_status=ReconciliationStatus.PARTIALLY_MATCHED,
            unallocated_amount="35.00",
        ),
    ]

    assert sum(
        (payment.allocations[0].allocated_amount for payment in payments),
        Decimal(),
    ) == Decimal("100.00")


def test_one_payment_can_cover_multiple_invoices() -> None:
    result = record(
        allocations=[
            allocation(allocated_amount="40.00"),
            allocation(
                allocation_id=UUID("d56f1767-f7cd-4463-ae28-8e6aa5788221"),
                target_id=UUID("b57d37b8-4da1-49d6-a190-a60502bf5611"),
                allocated_amount="60.00",
            ),
        ],
        reconciliation_status=ReconciliationStatus.MATCHED,
        unallocated_amount="0",
    )

    assert len({item.target_id for item in result.allocations}) == 2


def test_individual_cheque_reference_does_not_require_a_number() -> None:
    result = record(
        transaction_type=BankTransactionType.CHEQUE,
        payment_method=PaymentMethod.CHEQUE,
        cheque=ChequeReference(issuer_name="Issuer"),
    )

    assert result.cheque is not None
    assert result.cheque.cheque_number is None


def test_multiple_cheque_deposit_can_be_allocated_to_multiple_invoices() -> None:
    result = record(
        direction=BankTransactionDirection.CREDIT,
        transaction_type=BankTransactionType.CHEQUE_DEPOSIT,
        payment_method=PaymentMethod.CHEQUE,
        cheque=ChequeReference(
            deposit_reference="DEP-2026-10",
            cheque_count=2,
            total_deposit_amount="100.00",
        ),
        allocations=[
            allocation(allocated_amount="45.00"),
            allocation(
                allocation_id=UUID("d56f1767-f7cd-4463-ae28-8e6aa5788221"),
                target_id=UUID("b57d37b8-4da1-49d6-a190-a60502bf5611"),
                allocated_amount="55.00",
            ),
        ],
        reconciliation_status=ReconciliationStatus.MATCHED,
        unallocated_amount="0",
    )

    assert result.cheque is not None
    assert result.cheque.cheque_count == 2
    assert len(result.allocations) == 2


def test_uncertain_cheque_deposit_can_require_human_review() -> None:
    result = record(
        transaction_type=BankTransactionType.CHEQUE_DEPOSIT,
        payment_method=PaymentMethod.CHEQUE,
        cheque=ChequeReference(cheque_count=3),
        allocations=[allocation(
            allocated_amount="70.00",
            allocation_status=ReconciliationStatus.NEEDS_REVIEW,
            confidence_score=0.51,
        )],
        reconciliation_status=ReconciliationStatus.NEEDS_REVIEW,
        unallocated_amount="30.00",
    )

    assert result.reconciliation_status == ReconciliationStatus.NEEDS_REVIEW


@pytest.mark.parametrize("count", [0, -1])
def test_cheque_count_must_be_positive_when_provided(count: int) -> None:
    with pytest.raises(ValidationError):
        ChequeReference(cheque_count=count)


@pytest.mark.parametrize("score", [-0.01, 1.01])
def test_confidence_score_outside_unit_interval_is_rejected(score: float) -> None:
    with pytest.raises(ValidationError):
        allocation(confidence_score=score)


def test_high_confidence_does_not_automatically_validate_allocation() -> None:
    result = record(
        allocations=[allocation(
            allocation_status=ReconciliationStatus.PROPOSED,
            confidence_score=0.99,
        )],
        reconciliation_status=ReconciliationStatus.PROPOSED,
        unallocated_amount="0",
    )

    assert result.reconciliation_status == ReconciliationStatus.PROPOSED
    assert result.allocations[0].allocation_status == ReconciliationStatus.PROPOSED


def test_updated_at_cannot_precede_created_at() -> None:
    with pytest.raises(ValidationError):
        record(
            updated_at=datetime(2026, 10, 15, 8, 59, tzinfo=timezone.utc)
        )


@pytest.mark.parametrize("row_version", [0, -1, True])
def test_banking_master_row_version_must_be_strictly_positive(
    row_version: int,
) -> None:
    with pytest.raises(ValidationError):
        record(row_version=row_version)


def test_banking_record_requires_sha256_content_hash() -> None:
    with pytest.raises(ValidationError):
        record(content_hash="not-a-sha256")


def test_banking_record_soft_deletion_requires_complete_details() -> None:
    deleted = record(
        is_deleted=True,
        deleted_at=UPDATED_AT,
        deleted_by="reviewer",
        deletion_reason="Duplicate source",
    )

    assert deleted.is_deleted
    assert deleted.deleted_by == "reviewer"

    with pytest.raises(ValidationError):
        record(is_deleted=True, deleted_at=UPDATED_AT)


def test_banking_record_rejects_deletion_details_when_not_deleted() -> None:
    with pytest.raises(ValidationError):
        record(deletion_reason="Unexpected")


def test_json_serialization_roundtrip_preserves_decimal_and_dates() -> None:
    original = record(
        amount="100.20",
        unallocated_amount="100.20",
        value_date=date(2026, 10, 13),
        counterparty=BankCounterparty(
            display_name="Supplier",
            counterparty_type=CounterpartyType.SUPPLIER,
        ),
    )

    encoded = original.model_dump_json()
    restored = BankingRecord.model_validate_json(encoded)

    assert restored.amount == Decimal("100.2")
    assert restored.amount == original.amount
    assert str(restored.amount) == "100.20"
    assert restored.value_date == date(2026, 10, 13)
    assert restored.counterparty == original.counterparty


def test_decimal_trailing_zeros_are_preserved() -> None:
    result = record(amount="100.20", unallocated_amount="100.20")

    assert result.amount == Decimal("100.20")
    assert str(result.amount) == "100.20"


def test_default_lists_and_metadata_are_not_shared() -> None:
    first = record()
    second = record(
        transaction_id=UUID("144fb69c-fcb1-4c07-9eb8-a3c95099d87c")
    )

    first.metadata["source"] = "one"
    first.allocations.append(allocation())

    assert second.metadata == {}
    assert second.allocations == []


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (BankAccountReference, {**account_data(), "unexpected": True}),
        (BankCounterparty, {"display_name": "Supplier", "unexpected": True}),
        (ChequeReference, {"unexpected": True}),
        (ReconciliationAllocation, {**allocation_data(), "unexpected": True}),
        (BankingRecord, {**record_data(), "unexpected": True}),
    ],
)
def test_extra_fields_are_forbidden(
    model: type[Any],
    payload: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_blank_account_counterparty_and_alias_values_are_rejected() -> None:
    with pytest.raises(ValidationError):
        account(company_id=" ")
    with pytest.raises(ValidationError):
        BankCounterparty(display_name=" ")
    with pytest.raises(ValidationError):
        BankCounterparty(display_name="Supplier", aliases=["   "])


def test_unallocated_record_cannot_be_marked_matched() -> None:
    with pytest.raises(ValidationError):
        record(
            allocations=[],
            reconciliation_status=ReconciliationStatus.MATCHED,
        )


def test_cheque_reference_is_limited_to_cheque_transaction_types() -> None:
    with pytest.raises(ValidationError):
        record(cheque=ChequeReference(cheque_number="123"))


def test_contract_validation_does_not_create_files(tmp_path: Path) -> None:
    record()
    assert list(tmp_path.iterdir()) == []


def test_lcr_credit_record_can_reference_one_batch_of_42500_euros() -> None:
    lcr_batch = payment_batch()
    bank_line = record(
        direction=BankTransactionDirection.CREDIT,
        transaction_type=BankTransactionType.TRANSFER,
        amount="42500.00",
        unallocated_amount="42500.00",
        payment_batch=lcr_batch,
    )

    assert bank_line.amount == Decimal("42500.00")
    assert bank_line.payment_batch is not None
    assert bank_line.payment_batch.total_amount == bank_line.amount


def test_lcr_batch_can_contain_ten_customer_items() -> None:
    items = ten_lcr_items()
    lcr_batch = payment_batch(
        items=items,
        status=PaymentBatchStatus.NEEDS_REVIEW,
        unexplained_amount="0",
    )

    assert len(lcr_batch.items) == 10
    assert all(item.counterparty is not None for item in lcr_batch.items)


def test_batch_item_can_allocate_one_effect_to_one_invoice() -> None:
    item = batch_item(
        counterparty=BankCounterparty(
            counterparty_id=UUID("3fc94982-d3a9-496e-a913-b5cbf1bfbd82"),
            counterparty_type=CounterpartyType.CUSTOMER,
            display_name="Customer",
        ),
        allocations=[allocation(allocated_amount="4250.00")],
        status=PaymentItemStatus.MATCHED,
        unallocated_amount="0",
    )

    assert len(item.allocations) == 1
    assert item.allocations[0].target_type == AllocationTargetType.INVOICE


def test_one_lcr_item_can_cover_multiple_invoices() -> None:
    item = batch_item(
        allocations=[
            allocation(allocated_amount="2500.00"),
            allocation(
                allocation_id=UUID("d56f1767-f7cd-4463-ae28-8e6aa5788221"),
                target_id=UUID("b57d37b8-4da1-49d6-a190-a60502bf5611"),
                allocated_amount="1750.00",
            ),
        ],
        status=PaymentItemStatus.MATCHED,
        unallocated_amount="0",
    )

    assert len({allocation.target_id for allocation in item.allocations}) == 2
    assert sum((allocation.allocated_amount for allocation in item.allocations), Decimal()) == item.amount


def test_distinct_lcr_items_can_reference_the_same_customer() -> None:
    customer_id = UUID("3fc94982-d3a9-496e-a913-b5cbf1bfbd82")
    customer = BankCounterparty(
        counterparty_id=customer_id,
        counterparty_type=CounterpartyType.CUSTOMER,
        display_name="Repeat Customer",
    )
    first = batch_item(counterparty=customer)
    second = batch_item(
        item_id=UUID("d39fbd81-4628-499e-879d-f3344a019753"),
        counterparty=customer,
        effect_reference="EFFECT-SECOND",
    )

    assert first.counterparty.counterparty_id == second.counterparty.counterparty_id


def test_similar_customer_names_remain_distinct_by_identifier() -> None:
    first = BankCounterparty(
        counterparty_id=UUID("3fc94982-d3a9-496e-a913-b5cbf1bfbd82"),
        counterparty_type=CounterpartyType.CUSTOMER,
        display_name="Atelier Martin",
    )
    second = BankCounterparty(
        counterparty_id=UUID("a9c3ce25-53cb-420e-9081-9ab625c35581"),
        counterparty_type=CounterpartyType.CUSTOMER,
        display_name="Atelier Martins",
    )

    assert first.counterparty_id != second.counterparty_id
    assert first.display_name != second.display_name


def test_lcr_detail_may_be_absent_without_inventing_customer_or_invoice() -> None:
    missing = payment_batch(
        items=[],
        status=PaymentBatchStatus.DETAIL_MISSING,
        unexplained_amount="42500.00",
    )

    assert missing.items == []
    assert missing.unexplained_amount == Decimal("42500.00")


def test_six_of_ten_lcr_items_can_be_partially_detailed() -> None:
    items = ten_lcr_items()[:6]
    lcr_batch = payment_batch(
        items=items,
        status=PaymentBatchStatus.PARTIALLY_DETAILED,
        unexplained_amount="17000.00",
    )

    assert len(lcr_batch.items) == 6
    assert lcr_batch.unexplained_amount == Decimal("17000.00")


def test_batch_unexplained_amount_is_checked_against_item_total() -> None:
    with pytest.raises(ValidationError):
        payment_batch(
            items=ten_lcr_items()[:6],
            status=PaymentBatchStatus.PARTIALLY_DETAILED,
            unexplained_amount="16999.98",
        )


def test_batch_rejects_items_that_exceed_bank_credit() -> None:
    items = ten_lcr_items()
    items[-1] = batch_item(
        item_id=items[-1].item_id,
        amount="4250.01",
        unallocated_amount="4250.01",
    )
    with pytest.raises(ValidationError):
        payment_batch(
            items=items,
            status=PaymentBatchStatus.NEEDS_REVIEW,
            unexplained_amount="0",
        )


def test_batch_total_must_match_linked_bank_line() -> None:
    with pytest.raises(ValidationError):
        record(
            direction=BankTransactionDirection.CREDIT,
            amount="42500.00",
            unallocated_amount="42500.00",
            payment_batch=payment_batch(total_amount="42499.98"),
        )


def test_reconciled_batch_rejects_an_item_needing_review() -> None:
    items = ten_lcr_items(matched=True)
    items[4] = batch_item(
        item_id=items[4].item_id,
        counterparty=items[4].counterparty,
        amount="4250.00",
        status=PaymentItemStatus.NEEDS_REVIEW,
        allocations=[
            allocation(
                allocation_id=UUID(int=104),
                target_id=UUID(int=5),
                allocated_amount="4250.00",
                allocation_status=ReconciliationStatus.NEEDS_REVIEW,
            )
        ],
        unallocated_amount="0",
    )
    with pytest.raises(ValidationError):
        payment_batch(
            items=items,
            status=PaymentBatchStatus.RECONCILED,
            unexplained_amount="0",
        )


def test_ten_validated_lcr_effects_and_allocations_can_be_reconciled() -> None:
    lcr_batch = payment_batch(
        items=ten_lcr_items(matched=True),
        status=PaymentBatchStatus.RECONCILED,
        unexplained_amount="0",
    )

    assert lcr_batch.status == PaymentBatchStatus.RECONCILED
    assert all(item.status == PaymentItemStatus.MATCHED for item in lcr_batch.items)
    assert all(item.unallocated_amount == Decimal("0") for item in lcr_batch.items)


def test_source_evidence_is_preserved_per_effect() -> None:
    items = ten_lcr_items()

    assert [item.source_evidence_ref for item in items] == [
        f"statement-row-{index}" for index in range(1, 11)
    ]


def test_lcr_batch_links_many_clients_and_multiple_invoice_allocations() -> None:
    items = ten_lcr_items(matched=True)
    items[0] = batch_item(
        item_id=items[0].item_id,
        counterparty=items[0].counterparty,
        observed_name=items[0].observed_name,
        effect_reference=items[0].effect_reference,
        amount="4250.00",
        status=PaymentItemStatus.MATCHED,
        allocations=[
            allocation(
                allocation_id=UUID(int=100),
                target_id=UUID(int=1),
                allocated_amount="2000.00",
            ),
            allocation(
                allocation_id=UUID(int=1000),
                target_id=UUID(int=1001),
                allocated_amount="2250.00",
            ),
        ],
        unallocated_amount="0",
        source_evidence_ref=items[0].source_evidence_ref,
    )
    lcr_batch = payment_batch(
        items=items,
        status=PaymentBatchStatus.RECONCILED,
        unexplained_amount="0",
    )

    assert len(lcr_batch.items) == 10
    assert len(lcr_batch.items[0].allocations) == 2


def test_lcr_batch_cannot_be_combined_with_direct_record_allocations() -> None:
    with pytest.raises(ValidationError):
        record(
            direction=BankTransactionDirection.CREDIT,
            amount="42500.00",
            allocations=[allocation(allocated_amount="100.00")],
            reconciliation_status=ReconciliationStatus.NEEDS_REVIEW,
            unallocated_amount="42400.00",
            payment_batch=payment_batch(),
        )


def test_cheque_deposit_requires_a_cheque_deposit_batch() -> None:
    with pytest.raises(ValidationError):
        record(
            transaction_type=BankTransactionType.CHEQUE_DEPOSIT,
            payment_method=PaymentMethod.CHEQUE,
            direction=BankTransactionDirection.CREDIT,
            amount="42500.00",
            unallocated_amount="42500.00",
            payment_batch=payment_batch(batch_type=PaymentBatchType.LCR_REMITTANCE),
        )


def test_batch_and_lcr_record_json_roundtrip_preserves_decimal_amounts() -> None:
    original = record(
        direction=BankTransactionDirection.CREDIT,
        amount="42500.00",
        unallocated_amount="42500.00",
        payment_batch=payment_batch(
            total_amount="42500.00",
            items=ten_lcr_items()[:6],
            status=PaymentBatchStatus.PARTIALLY_DETAILED,
            unexplained_amount="17000.00",
        ),
    )

    restored = BankingRecord.model_validate_json(original.model_dump_json())

    assert restored.payment_batch is not None
    assert restored.payment_batch.total_amount == Decimal("42500.00")
    assert str(restored.payment_batch.total_amount) == "42500.00"
    assert restored.payment_batch.items[0].amount == Decimal("4250.00")
    assert restored.payment_batch.items[0].source_evidence_ref == "statement-row-1"
