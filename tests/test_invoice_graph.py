"""Integration tests for the official invoice LangGraph workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pytest

from backend.app.graphs.invoice_graph import run_invoice_graph
from schemas.invoice_schema import InvoiceSchema
from services.persistence_manager import PersistenceResult
from services.tva_zero_rule_evaluator import RuleDecision

CORPUS = Path(__file__).resolve().parents[1] / "workspace" / "invoices" / "input"
NATIVE_PDF = CORPUS / "facture_emaux.pdf"
SCANNED_PDF = CORPUS / "facture_free.pdf"


def _vision_payload() -> dict[str, Any]:
    return {
        "document_type": "INVOICE",
        "direction": "PURCHASE",
        "supplier": {
            "name": "Vision Supplier",
            "vat_number": "DE123456789",
        },
        "reference": {"issue_date": "2026-09-30"},
        "amount": {
            "currency": "EUR",
            "net_amount": "100.00",
            "tax_amount": "0.00",
            "gross_amount": "100.00",
        },
        "metadata": {"supplier_country": "DE"},
    }


def test_graph_commits_native_invoice_end_to_end() -> None:
    if not NATIVE_PDF.is_file():
        pytest.skip("corpus facture_emaux.pdf absent")

    result = run_invoice_graph(
        NATIVE_PDF, persistence_manager=None, thread_id="native-e2e"
    )

    assert result["file_type"] == "pdf"
    assert isinstance(result["producer_result"], Mapping)
    assert result["producer_result"]["supplier"]["siret"] == "40138136300019"
    assert isinstance(result["invoice"], InvoiceSchema)
    assert result["invoice"].supplier is not None
    assert result["invoice"].supplier.name == "Les Emaux de l'Atlantique"
    assert result["validation_passed"] is True
    assert isinstance(result["rule_decision"], RuleDecision)
    assert result["rule_decision"].status == "VALIDATED"
    assert result["rule_decision"].reason == "RULE_NOT_TRIGGERED"
    assert result["hitl_required"] is False
    assert result["commit_status"] == "COMMITTED"
    assert result["qdrant_payload"]["status"] == "COMMITTED"
    assert (
        result["qdrant_payload"]["invoice"]["supplier"]["name"]
        == "Les Emaux de l'Atlantique"
    )
    assert result["messages"]


def test_graph_routes_ambiguous_zero_vat_to_hitl() -> None:
    if not NATIVE_PDF.is_file():
        pytest.skip("corpus facture_emaux.pdf absent")

    result = run_invoice_graph(
        NATIVE_PDF,
        simulate_zero_vat=True,
        persistence_manager=None,
        thread_id="ambiguous-zero-vat",
    )

    assert isinstance(result["invoice"], InvoiceSchema)
    assert result["validation_passed"] is True
    assert isinstance(result["rule_decision"], RuleDecision)
    assert result["rule_decision"].status == "HITL"
    assert result["rule_decision"].reason == "AMBIGUOUS_ZERO_VAT"
    assert result["hitl_required"] is True
    assert result["commit_status"] == "PENDING_HITL"


def test_graph_routes_scanned_pdf_without_vision_to_hitl() -> None:
    if not SCANNED_PDF.is_file():
        pytest.skip("corpus facture_free.pdf absent")

    result = run_invoice_graph(
        SCANNED_PDF, persistence_manager=None, thread_id="scanned-no-vision"
    )

    assert result["invoice"] is None
    assert result["validation_passed"] is False
    assert result["hitl_required"] is True
    assert result["commit_status"] == "PENDING_HITL"
    assert "SCANNED_PDF_REQUIRES_VISION" in result["error_message"]


def test_graph_commits_scanned_pdf_with_injected_vision() -> None:
    if not SCANNED_PDF.is_file():
        pytest.skip("corpus facture_free.pdf absent")

    def extractor(source: Path) -> Mapping[str, Any]:
        return _vision_payload()

    result = run_invoice_graph(
        SCANNED_PDF,
        vision_extractor=extractor,
        persistence_manager=None,
        thread_id="scanned-vision",
    )

    assert isinstance(result["invoice"], InvoiceSchema)
    assert result["validation_passed"] is True
    assert isinstance(result["rule_decision"], RuleDecision)
    assert result["rule_decision"].status == "VALIDATED"
    assert result["rule_decision"].reason == "ZERO_VAT_DISAMBIGUATED"
    assert result["rule_decision"].vat_regime == "INTRACOM_AUTOLIQUIDATION"
    assert result["hitl_required"] is False
    assert result["commit_status"] == "COMMITTED"


def test_graph_rejects_unknown_file_type(tmp_path: Path) -> None:
    source = tmp_path / "facture.xyz"
    source.write_text("unsupported", encoding="utf-8")

    result = run_invoice_graph(
        source, persistence_manager=None, thread_id="unknown-type"
    )

    assert result["file_type"] == "unknown"
    assert result["commit_status"] == ""
    assert result["validation_passed"] is False


def test_commit_node_calls_persistence_manager() -> None:
    if not NATIVE_PDF.is_file():
        pytest.skip("corpus facture_emaux.pdf absent")

    class FakePersistenceManager:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def persist_invoice(
            self,
            invoice: InvoiceSchema,
            *,
            rule_decision: Any = None,
            source_file: str | None = None,
            source_sha256: str | None = None,
            tenant_id: str | None = None,
        ) -> PersistenceResult:
            self.calls.append(
                {
                    "invoice": invoice,
                    "rule_decision": rule_decision,
                    "source_file": source_file,
                    "source_sha256": source_sha256,
                    "tenant_id": tenant_id,
                }
            )
            return PersistenceResult(
                invoice_id="INV-TEST",
                graph_status="PERSISTED",
                vector_status="INDEXED",
                point_id="POINT-TEST",
                embedding_source="fake",
            )

    manager = FakePersistenceManager()
    result = run_invoice_graph(
        NATIVE_PDF,
        persistence_manager=manager,
        thread_id="persistence-e2e",
    )

    assert result["persistence_result"] == {
        "invoice_id": "INV-TEST",
        "graph_status": "PERSISTED",
        "vector_status": "INDEXED",
        "point_id": "POINT-TEST",
        "embedding_source": "fake",
        "errors": [],
    }
    assert result["qdrant_payload"]["persistence"]["graph_status"] == "PERSISTED"
    assert len(manager.calls) == 1
    assert manager.calls[0]["source_file"] == str(NATIVE_PDF)
    assert manager.calls[0]["source_sha256"] is not None
    assert len(manager.calls[0]["source_sha256"]) == 64
    assert manager.calls[0]["tenant_id"] == "DSH_CORE"
    assert manager.calls[0]["rule_decision"].rule_id == "RULE-TVA-ZERO-001"
