from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient


langfuse_stub = ModuleType("langfuse")


def _observe(*, name: str):
    def decorate(function):
        return function

    return decorate


langfuse_stub.observe = _observe
sys.modules.setdefault("langfuse", langfuse_stub)

import n8n_api
from agents.invoice_agent import InvoiceAgent
from services.ingestion_manager import InvoiceIngestionValidationError


def invoice_payload() -> dict[str, Any]:
    return {
        "fournisseur": "Webhook Supplier",
        "date_facture": "2026-10-01",
        "numero_facture": "WEB-2026-01",
        "categorie": "office",
        "montant_ht": 100.0,
        "montant_tva": 20.0,
        "montant_ttc": 120.0,
        "devise": "EUR",
    }


class FakeLegacyAgent:
    def __init__(self, result: dict[str, Any]):
        self.result = result
        self.calls: list[str] = []

    def process_invoice(self, file_path: str) -> dict[str, Any]:
        self.calls.append(file_path)
        return self.result


class FakeIngestionManager:
    def __init__(self, result: Any = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls: list[tuple[Path, dict[str, Any]]] = []

    def ingest_canonical_invoice(self, file_path: Path, **kwargs: Any) -> Any:
        self.calls.append((file_path, kwargs))
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(n8n_api, "INPUT_DIR", tmp_path)
    monkeypatch.setattr(n8n_api, "agent", None)
    monkeypatch.setattr(n8n_api, "ingestion_manager", FakeIngestionManager())
    return TestClient(n8n_api.app)


def upload(
    client: TestClient,
    *,
    data: dict[str, str] | None = None,
) -> Any:
    return client.post(
        "/api/v1/ingest",
        files={"file": ("invoice.pdf", b"test invoice bytes", "application/pdf")},
        data=data or {},
    )


def test_missing_and_explicit_legacy_mode_preserve_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_result = {"status": "success", "data": invoice_payload()}
    legacy_agent = FakeLegacyAgent(legacy_result)
    manager = FakeIngestionManager()
    monkeypatch.setattr(n8n_api, "INPUT_DIR", tmp_path)
    monkeypatch.setattr(n8n_api, "agent", legacy_agent)
    monkeypatch.setattr(n8n_api, "ingestion_manager", manager)
    client = TestClient(n8n_api.app)

    response_without_mode = upload(client)
    response_explicit_legacy = upload(client, data={"response_mode": "legacy"})

    assert response_without_mode.status_code == 200
    assert response_explicit_legacy.status_code == 200
    assert response_without_mode.json() == legacy_result
    assert response_explicit_legacy.json() == legacy_result
    assert set(response_without_mode.json()) == {"status", "data"}
    assert len(legacy_agent.calls) == 2
    assert manager.calls == []


def test_canonical_mode_returns_serialized_invoice_without_source_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = UUID("2bb34e76-76b7-40d2-94a0-b04301e15709")
    now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
    payload = invoice_payload()
    invoice = InvoiceAgent.__new__(InvoiceAgent).validate_canonical_invoice(
        payload,
        document_id=document_id,
        created_at=now,
        updated_at=now,
    )
    result = SimpleNamespace(
        invoice=invoice,
        legacy_data=payload.copy(),
        warnings=["review note"],
        metadata={"origin": "n8n"},
        source_file=str(tmp_path / "invoice.pdf"),
    )
    manager = FakeIngestionManager(result=result)
    legacy_agent = FakeLegacyAgent({"status": "unexpected"})
    monkeypatch.setattr(n8n_api, "INPUT_DIR", tmp_path)
    monkeypatch.setattr(n8n_api, "agent", legacy_agent)
    monkeypatch.setattr(n8n_api, "ingestion_manager", manager)

    response = upload(TestClient(n8n_api.app), data={"response_mode": "canonical"})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "status",
        "response_mode",
        "document_id",
        "invoice",
        "legacy_data",
        "warnings",
        "metadata",
    }
    assert body["status"] == "success"
    assert body["response_mode"] == "canonical"
    assert UUID(body["document_id"])
    assert body["invoice"]["amount"]["net_amount"] == "100.0"
    assert body["invoice"]["amount"]["tax_amount"] == "20.0"
    assert body["invoice"]["amount"]["gross_amount"] == "120.0"
    assert body["legacy_data"] == payload
    assert body["warnings"] == ["review note"]
    assert body["metadata"] == {"origin": "n8n"}
    assert "source_file" not in body
    assert str(tmp_path) not in response.text
    assert len(manager.calls) == 1
    called_path, kwargs = manager.calls[0]
    assert called_path == tmp_path / "invoice.pdf"
    assert kwargs["document_id"] == UUID(body["document_id"])
    assert kwargs["created_at"] == kwargs["updated_at"]
    assert kwargs["created_at"].tzinfo == timezone.utc
    assert legacy_agent.calls == []
    assert (tmp_path / "invoice.pdf").read_bytes() == b"test invoice bytes"


def test_invalid_response_mode_is_rejected_by_fastapi(client: TestClient) -> None:
    response = upload(client, data={"response_mode": "other"})

    assert response.status_code == 422


def test_canonical_validation_error_is_generic_and_does_not_expose_path(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = FakeIngestionManager(
        error=InvoiceIngestionValidationError(f"failure at {tmp_path}")
    )
    monkeypatch.setattr(n8n_api, "ingestion_manager", manager)

    response = upload(client, data={"response_mode": "canonical"})

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Invoice ingestion validation failed"
    }
    assert str(tmp_path) not in response.text


def test_unexpected_canonical_error_is_generic_and_has_no_traceback(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = FakeIngestionManager(error=RuntimeError(f"failed at {tmp_path}"))
    monkeypatch.setattr(n8n_api, "ingestion_manager", manager)

    response = upload(client, data={"response_mode": "canonical"})

    assert response.status_code == 500
    assert response.json() == {"detail": "Invoice ingestion failed"}
    assert str(tmp_path) not in response.text
    assert "Traceback" not in response.text


def test_existing_routes_remain_registered() -> None:
    routes = {
        (route.path, method)
        for route in n8n_api.app.routes
        for method in getattr(route, "methods", set())
    }

    assert ("/api/v1/ingest", "POST") in routes
    assert ("/docs", "GET") in routes
