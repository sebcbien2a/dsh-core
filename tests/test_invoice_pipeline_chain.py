"""End-to-end contract test: producer -> InvoiceSchema -> rules -> HITL."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.graphs.invoice_graph import run_invoice_graph
from schemas.invoice_schema import InvoiceSchema
from services.cloud_vision_extractor import (
    CloudVisionExtractor,
    ESCALATION_REASON,
    HybridVisionExtractor,
)
from services.invoice_producer_protocol import IInvoiceProducer, PdfInvoiceProducer
from services.ollama_vision_extractor import OllamaVisionExtractor
from services.tva_zero_rule_evaluator import RuleDecision

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS = REPO_ROOT / "workspace" / "invoices" / "input"
CORPUS_INVOICE = CORPUS / "facture_emaux.pdf"
PAINTCAR_INVOICE = CORPUS / "facture_paintcar.pdf"


def _paintcar_vision_payload() -> dict[str, object]:
    return {
        "document_type": "CREDIT_NOTE",
        "direction": "PURCHASE",
        "supplier": {
            "name": "CRISOL COLOR, S.L.",
            "vat_number": "ESB17443482",
        },
        "reference": {
            "invoice_number": "AB/53",
            "issue_date": "2026-09-14",
        },
        "amount": {
            "currency": "EUR",
            "net_amount": "83.43",
            "tax_amount": "0.00",
            "gross_amount": "83.43",
        },
        "metadata": {"supplier_country": "ES", "vision_source": "paintcar"},
    }


def test_pdf_producer_contract_returns_invoice_schema() -> None:
    if not CORPUS_INVOICE.is_file():
        pytest.skip("corpus facture_emaux.pdf absent")

    producer = PdfInvoiceProducer(CORPUS_INVOICE)

    assert isinstance(producer, IInvoiceProducer)
    invoice = producer.produce()
    assert isinstance(invoice, InvoiceSchema)
    assert invoice.supplier is not None
    assert invoice.supplier.name == "Les Emaux de l'Atlantique"


def test_real_corpus_invoice_routes_to_hitl_when_rule_is_not_approved(
    tmp_path: Path,
) -> None:
    if not CORPUS_INVOICE.is_file():
        pytest.skip("corpus facture_emaux.pdf absent")

    (tmp_path / "RULE_TVA_ZERO_DISAMBIGUATION_001.md").write_text(
        "---\n"
        "rule_id: RULE-TVA-ZERO-001\n"
        "status: draft\n"
        "---\n"
        "# Règle non approuvée\n",
        encoding="utf-8",
    )

    result = run_invoice_graph(
        CORPUS_INVOICE,
        rules_dir=tmp_path,
        persistence_manager=None,
        thread_id="chain-hitl",
    )

    assert result["file_type"] == "pdf"
    assert isinstance(result["invoice"], InvoiceSchema)
    assert result["validation_passed"] is True
    assert result["invoice"].supplier is not None
    assert result["invoice"].supplier.name == "Les Emaux de l'Atlantique"

    assert isinstance(result["rule_decision"], RuleDecision)
    assert result["rule_decision"].status == "HITL"
    assert result["rule_decision"].reason == "RULE_NOT_APPROVED"

    assert result["hitl_required"] is True
    assert result["commit_status"] == "PENDING_HITL"
    assert result["qdrant_payload"] is None


def test_paintcar_falls_back_to_vision_and_completes_graph() -> None:
    if not PAINTCAR_INVOICE.is_file():
        pytest.skip("corpus facture_paintcar.pdf absent")

    calls: list[Path] = []

    def extractor(source: Path) -> dict[str, object]:
        calls.append(source)
        return _paintcar_vision_payload()

    result = run_invoice_graph(
        PAINTCAR_INVOICE,
        vision_extractor=extractor,
        persistence_manager=None,
        thread_id="paintcar-vision",
    )

    assert calls == [PAINTCAR_INVOICE]
    assert result["error_message"] == ""
    assert isinstance(result["invoice"], InvoiceSchema)
    assert result["validation_passed"] is True
    assert result["invoice"].supplier is not None
    assert result["invoice"].supplier.name == "CRISOL COLOR, S.L."
    assert result["invoice"].metadata["fallback_reason"] == (
        "native_text_extraction_failed"
    )
    assert isinstance(result["rule_decision"], RuleDecision)
    assert result["rule_decision"].status == "VALIDATED"
    assert result["rule_decision"].vat_regime == "INTRACOM_AUTOLIQUIDATION"
    assert result["commit_status"] == "COMMITTED"


def test_paintcar_graph_uses_concrete_ollama_vision_extractor() -> None:
    if not PAINTCAR_INVOICE.is_file():
        pytest.skip("corpus facture_paintcar.pdf absent")

    def fake_chat(**kwargs: object) -> dict[str, object]:
        payload = {
            "fournisseur": "CRISOL COLOR, S.L.",
            "date_facture": "14/09/2026",
            "montant_ht": -83.43,
            "montant_tva": -83.43,
            "montant_ttc": -83.43,
            "devise": "EUR",
            "numero_facture": "AB/53",
            "tva_intra": "ESB17443482",
        }
        return {"message": {"content": json.dumps(payload)}}

    extractor = OllamaVisionExtractor(model="test-vision", chat=fake_chat)
    result = run_invoice_graph(
        PAINTCAR_INVOICE,
        vision_extractor=extractor,
        persistence_manager=None,
        thread_id="paintcar-ollama",
    )

    assert result["error_message"] == ""
    assert isinstance(result["invoice"], InvoiceSchema)
    assert result["validation_passed"] is True
    assert result["invoice"].document_type.value == "CREDIT_NOTE"
    assert result["invoice"].supplier is not None
    assert result["invoice"].supplier.name == "CRISOL COLOR, S.L."
    assert result["invoice"].metadata["extraction_tool"] == "ollama_vision"
    assert result["invoice"].metadata["fallback_reason"] == (
        "native_text_extraction_failed"
    )
    assert isinstance(result["rule_decision"], RuleDecision)
    assert result["rule_decision"].status == "VALIDATED"
    assert result["rule_decision"].vat_regime == "INTRACOM_AUTOLIQUIDATION"


def test_paintcar_graph_escalates_local_vision_to_cloud() -> None:
    if not PAINTCAR_INVOICE.is_file():
        pytest.skip("corpus facture_paintcar.pdf absent")

    class FakeCloudResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            payload = {
                "document_type": "CREDIT_NOTE",
                "direction": "PURCHASE",
                "supplier": {
                    "name": "CRISOL COLOR, S.L.",
                    "vat_number": "ESB17443482",
                },
                "reference": {
                    "invoice_number": "AB/53",
                    "issue_date": "2026-09-14",
                },
                "amount": {
                    "currency": "EUR",
                    "net_amount": 83.43,
                    "tax_amount": 0.0,
                    "gross_amount": 83.43,
                },
                "supplier_country": "ES",
            }
            return {
                "choices": [
                    {"message": {"content": json.dumps(payload)}}
                ]
            }

    class FakeCloudPost:
        def __call__(self, url: str, **kwargs: object) -> FakeCloudResponse:
            return FakeCloudResponse()

    class FailingLocal:
        def __call__(self, source: Path) -> dict[str, object]:
            raise RuntimeError("INVALID_VISION_PAYLOAD: montant TTC absent")

    cloud = CloudVisionExtractor(
        model="test-cloud-vision",
        api_key="test-key",
        post=FakeCloudPost(),
    )
    hybrid = HybridVisionExtractor(local=FailingLocal(), cloud=cloud)

    result = run_invoice_graph(
        PAINTCAR_INVOICE,
        vision_extractor=hybrid,
        persistence_manager=None,
        thread_id="paintcar-cloud-fallback",
    )

    assert result["error_message"] == ""
    assert isinstance(result["invoice"], InvoiceSchema)
    assert result["invoice"].document_type.value == "CREDIT_NOTE"
    assert result["invoice"].supplier is not None
    assert result["invoice"].supplier.name == "CRISOL COLOR, S.L."
    assert result["invoice"].metadata["fallback_reason"] == ESCALATION_REASON
    assert result["invoice"].metadata["extraction_tool"] == "openrouter_vision"
    assert isinstance(result["rule_decision"], RuleDecision)
    assert result["rule_decision"].status == "VALIDATED"
    assert result["rule_decision"].vat_regime == "INTRACOM_AUTOLIQUIDATION"
    assert result["commit_status"] == "COMMITTED"
