"""Tests for the spreadsheet (CSV/XLSX) invoice producer."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from schemas.invoice_schema import InvoiceDocumentType, InvoiceSchema
from services.invoice_producer_protocol import InvoiceProducer, InvoiceProducerError
from services.spreadsheet_invoice_producer import SpreadsheetInvoiceProducer

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "spreadsheet"
CSV_FILE = FIXTURES / "sample_journal.csv"
XLSX_FILE = FIXTURES / "sample_journal.xlsx"


def _producer(source: Path, **kwargs) -> SpreadsheetInvoiceProducer:
    return SpreadsheetInvoiceProducer(source, **kwargs)


def test_spreadsheet_producer_satisfies_canonical_protocol() -> None:
    assert isinstance(_producer(XLSX_FILE), InvoiceProducer)


def test_xlsx_detects_header_below_title_row() -> None:
    rows = _producer(XLSX_FILE).parse_rows()

    assert len(rows) == 2
    first, second = rows
    assert first.supplier == "FOURNISSEUR ALPHA"
    assert first.invoice_date is not None
    assert first.invoice_date.isoformat() == "2026-10-01"
    assert first.net == Decimal("1000")
    assert first.tax == Decimal("200")
    assert first.gross == Decimal("1200")
    assert first.reference == "F-2026-001"
    assert first.siret == "40138136300019"
    assert second.supplier == "FOURNISSEUR BETA"


def test_xlsx_produce_all_and_selected_row() -> None:
    producer = _producer(XLSX_FILE)

    invoices = producer.produce_all()
    assert len(invoices) == 2
    assert all(isinstance(invoice, InvoiceSchema) for invoice in invoices)
    assert all(invoice.direction.value == "PURCHASE" for invoice in invoices)
    assert invoices[0].supplier is not None
    assert invoices[0].supplier.name == "FOURNISSEUR ALPHA"
    assert invoices[0].supplier.siret == "40138136300019"
    assert invoices[0].document_type is InvoiceDocumentType.INVOICE
    assert invoices[0].metadata["source_kind"] == "spreadsheet_row"
    assert invoices[0].metadata["row_index"] == 0

    selected = producer.produce(row_index=1)
    assert selected.supplier is not None
    assert selected.supplier.name == "FOURNISSEUR BETA"


def test_csv_parses_french_amounts_and_derives_gross() -> None:
    rows = _producer(CSV_FILE).parse_rows()

    assert len(rows) == 3
    first = rows[0]
    assert first.net == Decimal("1000.00")
    assert first.tax == Decimal("200.00")
    assert first.gross == Decimal("1200.00")
    gamma = rows[2]
    assert gamma.gross == Decimal("120.00")
    assert gamma.net == Decimal("100.00")
    assert gamma.tax == Decimal("20.00")


def test_csv_produce_batch_returns_all_invoices() -> None:
    invoices = _producer(CSV_FILE).produce_batch()

    assert len(invoices) == 3
    assert [invoice.supplier.name for invoice in invoices if invoice.supplier] == [
        "FOURNISSEUR ALPHA",
        "FOURNISSEUR BETA",
        "FOURNISSEUR GAMMA",
    ]
    assert str(invoices[0].amount.gross_amount) == "1200.00"


def test_produce_summary_aggregates_journal() -> None:
    summary = _producer(CSV_FILE).produce_summary()

    assert isinstance(summary, InvoiceSchema)
    assert summary.supplier is not None
    assert summary.supplier.name == "JOURNAL_ACHATS"
    assert summary.metadata["row_count"] == 3
    assert summary.metadata["gross_total"] == "1620.00"
    assert str(summary.amount.gross_amount) == "1620.00"


def test_flexible_column_mapping(tmp_path: Path) -> None:
    source = tmp_path / "custom.csv"
    source.write_text(
        "Vendeur;Date pièce;N° pièce;Total HT;Taxe;Total TTC\n"
        "ALPHA;2026-10-01;X-1;10,00;2,00;12,00\n",
        encoding="utf-8",
    )
    producer = _producer(
        source,
        column_map={
            "supplier": "Vendeur",
            "date": "Date pièce",
            "reference": "N° pièce",
            "net": "Total HT",
            "tax": "Taxe",
            "gross": "Total TTC",
        },
    )

    invoice = producer.produce()

    assert invoice.supplier is not None
    assert invoice.supplier.name == "ALPHA"
    assert str(invoice.amount.net_amount) == "10.00"
    assert str(invoice.amount.tax_amount) == "2.00"
    assert str(invoice.amount.gross_amount) == "12.00"
    assert invoice.reference.invoice_number == "X-1"


def test_supplier_override_fills_missing_supplier(tmp_path: Path) -> None:
    source = tmp_path / "no_supplier.csv"
    source.write_text(
        "Date;Reference;HT;TVA;TTC\n2026-10-01;F-1;100,00;20,00;120,00\n",
        encoding="utf-8",
    )

    invoice = _producer(source, supplier_override="FOURNISSEUR IMPOSE").produce()

    assert invoice.supplier is not None
    assert invoice.supplier.name == "FOURNISSEUR IMPOSE"


def test_empty_journal_raises_no_rows(tmp_path: Path) -> None:
    source = tmp_path / "empty.csv"
    source.write_text("Date;Fournisseur;HT;TVA;TTC\n", encoding="utf-8")
    producer = _producer(source)

    assert producer.parse_rows() == []
    with pytest.raises(InvoiceProducerError) as exc_info:
        producer.produce_all()

    assert exc_info.value.code == "NO_SPREADSHEET_ROWS"


def test_row_index_out_of_range_is_rejected() -> None:
    with pytest.raises(InvoiceProducerError) as exc_info:
        _producer(CSV_FILE).produce(row_index=99)

    assert exc_info.value.code == "ROW_INDEX_OUT_OF_RANGE"


def test_invalid_amount_is_rejected_with_row_context(tmp_path: Path) -> None:
    source = tmp_path / "bad_amount.csv"
    source.write_text(
        "Date;Fournisseur;HT;TVA;TTC\n2026-10-01;ALPHA;abc;20,00;120,00\n",
        encoding="utf-8",
    )

    with pytest.raises(InvoiceProducerError) as exc_info:
        _producer(source).produce_all()

    assert exc_info.value.code == "INVALID_SPREADSHEET_ROW"
    assert "ligne 0" in str(exc_info.value)


def test_unsupported_format_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "journal.json"
    source.write_text("{}", encoding="utf-8")

    with pytest.raises(InvoiceProducerError) as exc_info:
        _producer(source).produce()

    assert exc_info.value.code == "UNSUPPORTED_SPREADSHEET_FORMAT"


def test_missing_source_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InvoiceProducerError) as exc_info:
        _producer(tmp_path / "missing.xlsx").produce()

    assert exc_info.value.code == "SOURCE_NOT_FOUND"
