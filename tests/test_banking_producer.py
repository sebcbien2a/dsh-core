"""Tests for the OFX/CSV banking producer."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from schemas.banking_schema import (
    BankingRecord,
    BankTransactionDirection,
    ReconciliationStatus,
)
from schemas.invoice_schema import InvoiceDocumentType, InvoiceSchema
from services.banking_invoice_producer import BankingInvoiceProducer
from services.invoice_producer_protocol import InvoiceProducer, InvoiceProducerError

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "banking"
OFX_FILE = FIXTURES / "sample.ofx"
CSV_FILE = FIXTURES / "sample.csv"


def _producer(source: Path) -> BankingInvoiceProducer:
    return BankingInvoiceProducer(source, company_id="DSH_CORE_TEST")


def test_banking_producer_satisfies_canonical_protocol() -> None:
    assert isinstance(_producer(OFX_FILE), InvoiceProducer)


def test_ofx_parses_debit_and_credit_transactions() -> None:
    transactions = _producer(OFX_FILE).parse_transactions()

    assert len(transactions) == 2
    debit, credit = transactions
    assert debit.booking_date.isoformat() == "2026-10-01"
    assert debit.amount == Decimal("120.50")
    assert debit.direction is BankTransactionDirection.DEBIT
    assert debit.counterparty == "FOURNISSEUR ALPHA"
    assert debit.reference == "TX-2026-001"
    assert debit.currency == "EUR"

    assert credit.booking_date.isoformat() == "2026-10-02"
    assert credit.amount == Decimal("250.00")
    assert credit.direction is BankTransactionDirection.CREDIT
    assert credit.counterparty == "CLIENT BETA"


def test_ofx_produce_all_maps_purchase_and_sale_invoices() -> None:
    invoices = _producer(OFX_FILE).produce_all()

    assert len(invoices) == 2
    purchase, sale = invoices
    assert isinstance(purchase, InvoiceSchema)
    assert isinstance(sale, InvoiceSchema)
    assert purchase.direction.value == "PURCHASE"
    assert purchase.supplier is not None
    assert purchase.supplier.name == "FOURNISSEUR ALPHA"
    assert str(purchase.amount.gross_amount) == "120.50"
    assert purchase.document_type is InvoiceDocumentType.UNKNOWN
    assert purchase.metadata["source_kind"] == "bank_transaction"
    assert purchase.metadata["bank_direction"] == "DEBIT"

    assert sale.direction.value == "SALE"
    assert sale.customer is not None
    assert sale.customer.name == "CLIENT BETA"
    assert str(sale.amount.gross_amount) == "250.00"
    assert sale.metadata["bank_direction"] == "CREDIT"


def test_produce_returns_canonical_invoice_schema() -> None:
    invoice = _producer(OFX_FILE).produce()

    assert isinstance(invoice, InvoiceSchema)
    assert invoice.supplier is not None
    assert invoice.supplier.name == "FOURNISSEUR ALPHA"
    assert invoice.metadata["producer"] == "banking_ofx_csv"


def test_csv_parses_semicolon_french_amounts() -> None:
    transactions = _producer(CSV_FILE).parse_transactions()

    assert len(transactions) == 2
    debit, credit = transactions
    assert debit.amount == Decimal("12.34")
    assert debit.direction is BankTransactionDirection.DEBIT
    assert debit.counterparty == "BANQUE POPULAIRE"
    assert credit.amount == Decimal("500.00")
    assert credit.direction is BankTransactionDirection.CREDIT
    assert credit.counterparty == "CLIENT GAMMA"


def test_csv_signed_amount_infers_direction(tmp_path: Path) -> None:
    source = tmp_path / "signed.csv"
    source.write_text(
        "date,label,amount\n2026-10-05,Paiement fournisseur,-45.50\n",
        encoding="utf-8",
    )

    transaction = _producer(source).parse_transactions()[0]

    assert transaction.amount == Decimal("45.50")
    assert transaction.direction is BankTransactionDirection.DEBIT


def test_produce_records_returns_valid_banking_records() -> None:
    records = _producer(OFX_FILE).produce_records()

    assert len(records) == 2
    debit, credit = records
    assert isinstance(debit, BankingRecord)
    assert debit.amount == Decimal("120.50")
    assert debit.unallocated_amount == debit.amount
    assert debit.direction is BankTransactionDirection.DEBIT
    assert debit.reconciliation_status is ReconciliationStatus.UNMATCHED
    assert debit.raw_label == "Facture F-2026-001"
    assert re.fullmatch(r"[0-9a-f]{64}", debit.content_hash)
    assert debit.created_at.tzinfo is not None
    assert debit.updated_at >= debit.created_at
    assert credit.direction is BankTransactionDirection.CREDIT


def test_empty_statement_raises_no_transactions(tmp_path: Path) -> None:
    source = tmp_path / "empty.csv"
    source.write_text("date,label,amount\n", encoding="utf-8")
    producer = _producer(source)

    assert producer.parse_transactions() == []
    with pytest.raises(InvoiceProducerError) as exc_info:
        producer.produce_all()

    assert exc_info.value.code == "NO_BANK_TRANSACTIONS"


def test_unsupported_format_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "statement.mt940"
    source.write_text(":20:TEST\n", encoding="utf-8")

    with pytest.raises(InvoiceProducerError) as exc_info:
        _producer(source).produce()

    assert exc_info.value.code == "UNSUPPORTED_BANKING_FORMAT"


def test_missing_source_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InvoiceProducerError) as exc_info:
        _producer(tmp_path / "missing.ofx").produce()

    assert exc_info.value.code == "SOURCE_NOT_FOUND"
