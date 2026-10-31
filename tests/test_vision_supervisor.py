"""Tests for the strict Gemini supervisor escalation, Ollama timeout and quota."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import pytest

from model_router import (
    DEFAULT_ESCALATION_BUDGET_RATIO,
    DEFAULT_MULTIMODAL_FALLBACK_MODEL,
    resolve_escalation_budget_ratio,
    resolve_multimodal_fallback_model,
)
from scripts.run_batch_audit import run_batch_audit
from services.cloud_vision_extractor import (
    ESCALATION_REASON,
    CloudEscalationBudget,
    CloudVisionExtractorError,
    HybridVisionExtractor,
    should_escalate_to_supervisor,
)
from services.local_invoice_extractor import (
    LocalInvoiceExtractor,
    LocalInvoiceExtractorError,
)


def _payload() -> dict[str, Any]:
    return {
        "document_type": "INVOICE",
        "direction": "PURCHASE",
        "supplier": {
            "name": "VISION SAS",
            "vat_number": "FR12345678901",
        },
        "reference": {
            "invoice_number": "V-2026-001",
            "issue_date": "2026-09-30",
        },
        "amount": {
            "currency": "EUR",
            "net_amount": "100.00",
            "tax_amount": "20.00",
            "gross_amount": "120.00",
        },
        "metadata": {
            "supplier_country": "FR",
            "extraction_tool": "vision_extractor",
        },
    }


class _RaisingLocal:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def __call__(self, source: Path) -> dict[str, Any]:
        raise self.error


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("INVALID_VISION_PAYLOAD: montant TTC absent", True),
        ("INVALID_PRODUCER_PAYLOAD: gross mismatch", True),
        ("RÉPONSE_OLLAMA_VIDE", True),
        ("ollama timeout", False),
        ("OCR_TEXT_UNAVAILABLE", False),
        ("LOCAL_INFERENCE_TIMEOUT: 90s dépassés", True),
    ],
)
def test_should_escalate_to_supervisor_is_strict(
    error: str, expected: bool
) -> None:
    assert should_escalate_to_supervisor(error) is expected


def test_hybrid_escalates_on_schema_anchoring_failure() -> None:
    hybrid = HybridVisionExtractor(
        local=_RaisingLocal(
            RuntimeError("INVALID_VISION_PAYLOAD: montant TTC absent")
        ),
        cloud=lambda source: _payload(),
    )

    payload = hybrid(Path("invoice.pdf"))

    assert payload["metadata"]["fallback_reason"] == ESCALATION_REASON
    assert "montant TTC absent" in payload["metadata"]["local_vision_error"]


def test_hybrid_escalates_on_empty_ollama_response() -> None:
    hybrid = HybridVisionExtractor(
        local=_RaisingLocal(RuntimeError("RÉPONSE_OLLAMA_VIDE")),
        cloud=lambda source: _payload(),
    )

    payload = hybrid(Path("invoice.pdf"))

    assert payload["metadata"]["fallback_reason"] == ESCALATION_REASON


def test_hybrid_escalates_on_local_timeout_when_budget_available() -> None:
    budget = CloudEscalationBudget(1)
    hybrid = HybridVisionExtractor(
        local=_RaisingLocal(
            TimeoutError("LOCAL_INFERENCE_TIMEOUT: 90s dépassés")
        ),
        cloud=lambda source: _payload(),
        budget=budget,
    )

    payload = hybrid(Path("invoice.pdf"))

    assert payload["metadata"]["fallback_reason"] == ESCALATION_REASON
    assert (
        payload["metadata"]["escalation_cause"] == "local_inference_timeout"
    )
    assert budget.used == 1


def test_hybrid_stops_escalation_when_budget_exhausted() -> None:
    def cloud(source: Path) -> dict[str, Any]:
        raise AssertionError("the supervisor must not be called")

    hybrid = HybridVisionExtractor(
        local=_RaisingLocal(
            RuntimeError("INVALID_VISION_PAYLOAD: montant TTC absent")
        ),
        cloud=cloud,
        budget=CloudEscalationBudget(0),
    )

    with pytest.raises(
        CloudVisionExtractorError, match="ESCALATION_BUDGET_EXCEEDED"
    ):
        hybrid(Path("invoice.pdf"))


def test_resolve_multimodal_fallback_model_default_and_override() -> None:
    assert (
        resolve_multimodal_fallback_model(environ={})
        == DEFAULT_MULTIMODAL_FALLBACK_MODEL
    )
    assert (
        resolve_multimodal_fallback_model(
            environ={
                "CLOUD_MULTIMODAL_FALLBACK_MODEL": "google/gemini-custom"
            }
        )
        == "google/gemini-custom"
    )
    assert (
        resolve_multimodal_fallback_model(
            "explicit-model",
            environ={"CLOUD_MULTIMODAL_FALLBACK_MODEL": "ignored-model"},
        )
        == "explicit-model"
    )


def test_local_extractor_enforces_strict_timeout(tmp_path: Path) -> None:
    def slow_chat(**kwargs: Any) -> dict[str, Any]:
        time.sleep(0.2)
        return {"message": {"content": "{}"}}

    extractor = LocalInvoiceExtractor(
        model="test-extraction",
        chat=slow_chat,
        timeout_seconds=0.02,
    )

    with pytest.raises(LocalInvoiceExtractorError, match="LOCAL_INFERENCE_TIMEOUT"):
        extractor.extract_payload(
            "ACME SAS FACTURE F-2026-001 100,00",
            source=tmp_path / "invoice.pdf",
            ocr_source="text_layer",
        )


def test_batch_audit_warns_when_escalation_ratio_exceeds_quota(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    pymupdf = pytest.importorskip("pymupdf")
    corpus = tmp_path / "input"
    corpus.mkdir()
    document = pymupdf.open()
    document.new_page()
    document.save(str(corpus / "scan.pdf"))
    document.close()

    def escalating_extractor(source: Path) -> dict[str, Any]:
        payload = _payload()
        payload["metadata"]["fallback_reason"] = ESCALATION_REASON
        return payload

    with caplog.at_level(logging.WARNING, logger="DSH-Core.BatchAudit"):
        result = run_batch_audit(
            corpus,
            output_dir=tmp_path / "audit",
            vision="hybrid",
            vision_extractor=escalating_extractor,
        )

    guard = result.summary["cloud_escalations"]
    assert guard["count"] == 1
    assert guard["ratio"] == 1.0
    assert guard["exceeded"] is True
    assert guard["budget"]["max"] == 1
    assert guard["budget"]["used"] == 0
    assert guard["budget"]["external_extractor"] is True
    assert result.manifest["escalation_guard"]["exceeded"] is True
    assert any("Quota d'escalade" in record.message for record in caplog.records)
    report = (result.run_dir / "report.md").read_text(encoding="utf-8")
    assert "DÉPASSÉ" in report


def test_resolve_escalation_budget_ratio() -> None:
    variable = "CLOUD_ESCALATION_BUDGET_RATIO"
    assert (
        resolve_escalation_budget_ratio(environ={})
        == DEFAULT_ESCALATION_BUDGET_RATIO
    )
    assert resolve_escalation_budget_ratio(environ={variable: "0.2"}) == 0.2
    assert resolve_escalation_budget_ratio(environ={variable: "2"}) == 1.0
    assert resolve_escalation_budget_ratio(environ={variable: "-1"}) == 0.0
    assert (
        resolve_escalation_budget_ratio(environ={variable: "invalid"})
        == DEFAULT_ESCALATION_BUDGET_RATIO
    )


def test_batch_audit_budget_cuts_off_escalations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pymupdf = pytest.importorskip("pymupdf")
    corpus = tmp_path / "input"
    corpus.mkdir()
    for index, size in enumerate((200, 260), start=1):
        document = pymupdf.open()
        document.new_page(width=size, height=size)
        document.save(str(corpus / f"scan_{index}.pdf"))
        document.close()

    def factory(mode: str, budget: Any = None) -> HybridVisionExtractor:
        assert mode == "hybrid"
        return HybridVisionExtractor(
            local=_RaisingLocal(
                RuntimeError("INVALID_VISION_PAYLOAD: montant TTC absent")
            ),
            cloud=lambda source: _payload(),
            budget=budget,
        )

    monkeypatch.setattr("scripts.run_batch_audit.build_vision_extractor", factory)

    result = run_batch_audit(
        corpus,
        output_dir=tmp_path / "audit",
        vision="hybrid",
        max_cloud_escalations=1,
    )

    assert result.summary["decisions"] == {"VALIDATED": 1, "HITL": 1, "REJECTED": 0}
    guard = result.summary["cloud_escalations"]
    assert guard["count"] == 1
    assert guard["budget"]["max"] == 1
    assert guard["budget"]["used"] == 1
    assert guard["budget"]["exhausted"] is True
    codes = {
        point["code"]
        for record in result.records
        for point in record["blocking_points"]
    }
    assert "ESCALATED_TO_CLOUD" in codes
    assert "ESCALATION_BUDGET_EXCEEDED" in codes
    report = (result.run_dir / "report.md").read_text(encoding="utf-8")
    assert "Budget escalade : 1/1" in report
