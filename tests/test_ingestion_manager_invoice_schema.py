from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys
from types import ModuleType
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from agents.invoice_agent import InvoiceAgent
from schemas.invoice_schema import InvoiceSchema


langfuse_stub = ModuleType("langfuse")


def _observe(*, name: str):
    def decorate(function):
        return function

    return decorate


langfuse_stub.observe = _observe
sys.modules.setdefault("langfuse", langfuse_stub)

from services.ingestion_manager import (
    CanonicalInvoiceIngestionResult,
    InvoiceIngestionValidationError,
    IngestionManager,
)


DOCUMENT_ID = UUID("2bb34e76-76b7-40d2-94a0-b04301e15709")
CREATED_AT = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
UPDATED_AT = datetime(2026, 10, 21, 12, 5, tzinfo=timezone.utc)


def invoice_payload() -> dict[str, Any]:
    return {
        "fournisseur": "Legacy Supplier",
        "date_facture": "2026-10-01",
        "numero_facture": "ING-2026-01",
        "categorie": "office",
        "montant_ht": 100.0,
        "montant_tva": 20.0,
        "montant_ttc": 120.0,
        "devise": "EUR",
    }


class FakeInvoiceAgent:
    def __init__(self, result: Any):
        self.result = result
        self.process_calls: list[str] = []
        self.canonical_agent = InvoiceAgent.__new__(InvoiceAgent)

    def process_invoice(self, file_path: str) -> Any:
        self.process_calls.append(file_path)
        return self.result

    def validate_canonical_invoice(self, payload: dict[str, Any], **kwargs: Any):
        return self.canonical_agent.validate_canonical_invoice(payload, **kwargs)


def make_manager(result: Any) -> tuple[IngestionManager, FakeInvoiceAgent]:
    manager = IngestionManager()
    agent = FakeInvoiceAgent(result)
    manager._agents["invoice"] = agent
    return manager, agent


def test_canonical_ingestion_preserves_invoice_and_legacy_data(
    tmp_path: Path,
) -> None:
    source = tmp_path / "invoice.pdf"
    source.write_bytes(b"original invoice")
    payload = invoice_payload()
    result = {"status": "success", "data": payload}
    manager, agent = make_manager(result)

    ingestion = manager.ingest_canonical_invoice(
        source,
        document_id=DOCUMENT_ID,
        created_at=CREATED_AT,
        updated_at=UPDATED_AT,
    )

    assert isinstance(ingestion, CanonicalInvoiceIngestionResult)
    assert isinstance(ingestion.invoice, InvoiceSchema)
    assert ingestion.invoice.document_id == DOCUMENT_ID
    assert ingestion.invoice.created_at == CREATED_AT
    assert ingestion.invoice.updated_at == UPDATED_AT
    assert ingestion.invoice.supplier.name == "Legacy Supplier"
    assert ingestion.invoice.reference.invoice_number == "ING-2026-01"
    assert ingestion.invoice.reference.issue_date.isoformat() == "2026-10-01"
    assert ingestion.invoice.category == "office"
    assert ingestion.invoice.amount.net_amount == Decimal("100.0")
    assert ingestion.invoice.amount.tax_amount == Decimal("20.0")
    assert ingestion.invoice.amount.gross_amount == Decimal("120.0")
    assert ingestion.invoice.amount.currency == "EUR"
    assert ingestion.legacy_data == payload
    assert ingestion.source_file == str(source)
    assert agent.process_calls == [str(source)]


def test_legacy_data_is_independent_from_canonical_invoice(tmp_path: Path) -> None:
    payload = invoice_payload()
    manager, _ = make_manager({"status": "success", "data": payload})

    result = manager.ingest_canonical_invoice(
        tmp_path / "invoice.pdf",
        document_id=DOCUMENT_ID,
        created_at=CREATED_AT,
        updated_at=UPDATED_AT,
    )
    result.legacy_data["fournisseur"] = "changed"
    result.legacy_data["montant_ht"] = -500

    assert result.invoice.supplier.name == "Legacy Supplier"
    assert result.invoice.amount.net_amount == Decimal("100.0")
    assert payload["fournisseur"] == "Legacy Supplier"
    assert payload["montant_ht"] == 100.0


@pytest.mark.parametrize(
    "agent_result",
    [
        {},
        {"data": invoice_payload()},
        {"status": "error", "message": "opaque"},
        {"status": "success"},
        {"status": "success", "data": None},
        {"status": "success", "data": []},
        None,
    ],
)
def test_malformed_agent_results_are_rejected_without_path_leak(
    tmp_path: Path,
    agent_result: Any,
) -> None:
    manager, agent = make_manager(agent_result)

    with pytest.raises(InvoiceIngestionValidationError) as error:
        manager.ingest_canonical_invoice(
            tmp_path / "private" / "invoice.pdf",
            document_id=DOCUMENT_ID,
            created_at=CREATED_AT,
            updated_at=UPDATED_AT,
        )

    assert str(tmp_path) not in str(error.value)
    assert len(agent.process_calls) == 1


def test_invalid_invoice_data_preserves_validation_error_cause(tmp_path: Path) -> None:
    payload = invoice_payload()
    payload["montant_ttc"] = 125.0
    manager, _ = make_manager({"status": "success", "data": payload})

    with pytest.raises(InvoiceIngestionValidationError) as error:
        manager.ingest_canonical_invoice(
            tmp_path / "invoice.pdf",
            document_id=DOCUMENT_ID,
            created_at=CREATED_AT,
            updated_at=UPDATED_AT,
        )

    assert isinstance(error.value.__cause__, ValidationError)
    assert str(tmp_path) not in str(error.value)


def test_historical_ingest_keeps_success_envelope(tmp_path: Path) -> None:
    source = tmp_path / "invoice.pdf"
    source.write_bytes(b"unchanged")
    legacy = invoice_payload()
    manager, agent = make_manager({"status": "success", "data": legacy})

    result = manager.ingest(str(source))

    assert result["status"] == "success"
    assert result["asset_type"] == "invoice"
    assert result["extracted_data"] == legacy
    assert agent.process_calls == [str(source)]
    assert source.read_bytes() == b"unchanged"


def test_historical_ingest_keeps_error_envelope(tmp_path: Path) -> None:
    source = tmp_path / "invoice.pdf"
    source.write_bytes(b"unchanged")
    manager, agent = make_manager(
        {"status": "error", "message": "legacy failure"}
    )

    result = manager.ingest(str(source))

    assert result["status"] == "error"
    assert result["error_log"] == "legacy failure"
    assert agent.process_calls == [str(source)]
    assert source.read_bytes() == b"unchanged"


def test_no_model_or_network_call_or_file_write_during_canonical_ingestion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "invoice.pdf"
    source.write_bytes(b"unchanged")
    manager, _ = make_manager({"status": "success", "data": invoice_payload()})

    def fail_if_called(*args: Any, **kwargs: Any) -> None:
        pytest.fail("External model/network calls are forbidden in this test")

    monkeypatch.setattr("agents.invoice_agent.ollama.chat", fail_if_called)
    initial_files = set(tmp_path.rglob("*"))

    manager.ingest_canonical_invoice(
        source,
        document_id=DOCUMENT_ID,
        created_at=CREATED_AT,
        updated_at=UPDATED_AT,
    )

    assert set(tmp_path.rglob("*")) == initial_files
    assert source.read_bytes() == b"unchanged"
    assert all(
        path.resolve().is_relative_to(tmp_path.resolve())
        for path in tmp_path.rglob("*")
    )
