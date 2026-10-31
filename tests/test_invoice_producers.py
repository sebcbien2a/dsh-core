"""Tests for the canonical InvoiceProducer protocol and PDF adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from schemas.invoice_schema import InvoiceSchema
from services.invoice_producer_protocol import (
    InvoiceProducer,
    InvoiceProducerError,
    PdfInvoiceProducer,
    load_pymupdf,
)
from services.cloud_vision_extractor import (
    CloudVisionExtractor,
    CloudVisionExtractorError,
    ESCALATION_REASON,
    HybridVisionExtractor,
)
from services.ollama_vision_extractor import (
    OllamaVisionExtractor,
    OllamaVisionExtractorError,
)
from services.local_invoice_extractor import (
    LocalInvoiceExtractor,
    LocalInvoiceExtractorError,
    invoice_json_schema,
)

CORPUS = Path(__file__).resolve().parents[1] / "workspace" / "invoices" / "input"
NATIVE_PDF = CORPUS / "facture_emaux.pdf"
SCANNED_PDF = CORPUS / "facture_free.pdf"
PAINTCAR_PDF = CORPUS / "facture_paintcar.pdf"


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


def _consume(producer: InvoiceProducer) -> InvoiceSchema:
    return producer.produce()


def test_pdf_producer_satisfies_canonical_protocol() -> None:
    producer = PdfInvoiceProducer(NATIVE_PDF)
    assert isinstance(producer, InvoiceProducer)


def test_native_pdf_produces_valid_invoice_schema() -> None:
    if not NATIVE_PDF.is_file():
        pytest.skip("corpus facture_emaux.pdf absent")

    invoice = _consume(PdfInvoiceProducer(NATIVE_PDF))

    assert isinstance(invoice, InvoiceSchema)
    assert invoice.supplier is not None
    assert invoice.supplier.name == "Les Emaux de l'Atlantique"
    assert invoice.supplier.siret == "40138136300019"
    assert str(invoice.amount.tax_amount) == "351.48"
    assert invoice.metadata["producer"] == "pdf_native_or_vision"
    assert invoice.metadata["extraction_tool"] == "pymupdf_text_regex"


def test_native_pdf_does_not_call_vision_extractor() -> None:
    if not NATIVE_PDF.is_file():
        pytest.skip("corpus facture_emaux.pdf absent")

    def failing_extractor(source: Path) -> Mapping[str, Any]:
        raise AssertionError(f"vision extractor must not be called for {source}")

    invoice = PdfInvoiceProducer(
        NATIVE_PDF, vision_extractor=failing_extractor
    ).produce()

    assert isinstance(invoice, InvoiceSchema)


def test_scanned_pdf_requires_vision() -> None:
    if not SCANNED_PDF.is_file():
        pytest.skip("corpus facture_free.pdf absent")

    with pytest.raises(InvoiceProducerError) as exc_info:
        PdfInvoiceProducer(SCANNED_PDF).produce()

    assert exc_info.value.code == "SCANNED_PDF_REQUIRES_VISION"


def test_scanned_pdf_uses_injected_vision_extractor() -> None:
    if not SCANNED_PDF.is_file():
        pytest.skip("corpus facture_free.pdf absent")

    calls: list[Path] = []

    def extractor(source: Path) -> Mapping[str, Any]:
        calls.append(source)
        return _vision_payload()

    invoice = PdfInvoiceProducer(
        SCANNED_PDF, vision_extractor=extractor
    ).produce()

    assert calls == [SCANNED_PDF]
    assert invoice.supplier is not None
    assert invoice.supplier.name == "Vision Supplier"
    assert invoice.metadata["extraction_tool"] == "vision_extractor"
    assert invoice.metadata["producer"] == "pdf_native_or_vision"


def test_vision_payload_must_be_a_mapping() -> None:
    if not SCANNED_PDF.is_file():
        pytest.skip("corpus facture_free.pdf absent")

    def extractor(source: Path) -> Any:
        return ["not", "a", "mapping"]

    with pytest.raises(InvoiceProducerError) as exc_info:
        PdfInvoiceProducer(SCANNED_PDF, vision_extractor=extractor).produce()

    assert exc_info.value.code == "INVALID_PRODUCER_PAYLOAD"


def test_invalid_vision_payload_is_rejected_by_canonical_schema() -> None:
    if not SCANNED_PDF.is_file():
        pytest.skip("corpus facture_free.pdf absent")

    payload = _vision_payload()
    del payload["amount"]["gross_amount"]

    with pytest.raises(InvoiceProducerError) as exc_info:
        PdfInvoiceProducer(
            SCANNED_PDF, vision_extractor=lambda source: payload
        ).produce()

    assert exc_info.value.code == "INVALID_PRODUCER_PAYLOAD"
    assert "gross_amount" in str(exc_info.value)


def test_missing_source_file_is_rejected(tmp_path: Path) -> None:
    producer = PdfInvoiceProducer(tmp_path / "absent.pdf")

    with pytest.raises(InvoiceProducerError) as exc_info:
        producer.produce()

    assert exc_info.value.code == "SOURCE_NOT_FOUND"


def test_vision_extractor_failure_is_wrapped() -> None:
    if not SCANNED_PDF.is_file():
        pytest.skip("corpus facture_free.pdf absent")

    def extractor(source: Path) -> Mapping[str, Any]:
        raise RuntimeError("model offline")

    with pytest.raises(InvoiceProducerError) as exc_info:
        PdfInvoiceProducer(SCANNED_PDF, vision_extractor=extractor).produce()

    assert exc_info.value.code == "VISION_EXTRACTION_FAILED"


def _paintcar_vision_payload() -> dict[str, Any]:
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


def test_native_extraction_failure_falls_back_to_vision() -> None:
    if not PAINTCAR_PDF.is_file():
        pytest.skip("corpus facture_paintcar.pdf absent")

    calls: list[Path] = []

    def extractor(source: Path) -> Mapping[str, Any]:
        calls.append(source)
        return _paintcar_vision_payload()

    invoice = PdfInvoiceProducer(
        PAINTCAR_PDF, vision_extractor=extractor
    ).produce()

    assert calls == [PAINTCAR_PDF]
    assert isinstance(invoice, InvoiceSchema)
    assert invoice.supplier is not None
    assert invoice.supplier.name == "CRISOL COLOR, S.L."
    assert invoice.metadata["extraction_tool"] == "vision_extractor"
    assert invoice.metadata["fallback_reason"] == "native_text_extraction_failed"


def test_native_extraction_failure_without_vision_still_fails() -> None:
    if not PAINTCAR_PDF.is_file():
        pytest.skip("corpus facture_paintcar.pdf absent")

    with pytest.raises(InvoiceProducerError) as exc_info:
        PdfInvoiceProducer(PAINTCAR_PDF).produce()

    assert exc_info.value.code == "EXTRACTION_FAILED"


def test_zero_vat_simulation_is_applied_to_vision_payload() -> None:
    if not SCANNED_PDF.is_file():
        pytest.skip("corpus facture_free.pdf absent")

    invoice = PdfInvoiceProducer(
        SCANNED_PDF,
        vision_extractor=lambda source: _vision_payload(),
        simulate_zero_vat=True,
    ).produce()

    assert invoice.amount.tax_amount is not None
    assert str(invoice.amount.tax_amount) == "0.00"
    assert str(invoice.amount.gross_amount) == str(invoice.amount.net_amount)
    assert invoice.metadata["simulated_zero_vat"] is True


class FakeVisionChat:
    def __init__(self, payload: Mapping[str, Any] | str) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        content = (
            self.payload
            if isinstance(self.payload, str)
            else json.dumps(self.payload)
        )
        return {"message": {"content": content}}


class SequencedVisionChat:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        content = outcome if isinstance(outcome, str) else json.dumps(outcome)
        return {"message": {"content": content}}


def _legacy_payload() -> dict[str, Any]:
    return {
        "fournisseur": "ACME SAS",
        "date_facture": "14/09/2026",
        "montant_ht": 100.0,
        "montant_tva": 20.0,
        "montant_ttc": 120.0,
        "devise": "EUR",
        "numero_facture": "F-2026-001",
        "categorie": "Matériel",
    }


def test_ollama_vision_extractor_maps_legacy_payload(tmp_path: Path) -> None:
    source = tmp_path / "invoice.png"
    source.write_bytes(b"%PDF-1.4 fake")
    chat = FakeVisionChat(_legacy_payload())
    extractor = OllamaVisionExtractor(model="test-vision", chat=chat)

    payload = extractor(source)

    assert chat.calls[0]["model"] == "test-vision"
    assert chat.calls[0]["format"] == "json"
    assert chat.calls[0]["options"]["temperature"] == 0.0
    assert chat.calls[0]["messages"][1]["images"] == [b"%PDF-1.4 fake"]
    invoice = InvoiceSchema.model_validate(payload)
    assert invoice.supplier is not None
    assert invoice.supplier.name == "ACME SAS"
    assert str(invoice.amount.net_amount) == "100.0"
    assert str(invoice.amount.tax_amount) == "20.0"
    assert str(invoice.amount.gross_amount) == "120.0"
    assert invoice.reference.invoice_number == "F-2026-001"
    assert invoice.reference.issue_date is not None
    assert invoice.reference.issue_date.isoformat() == "2026-09-14"
    assert invoice.metadata["extraction_tool"] == "ollama_vision"


def test_ollama_vision_extractor_renders_pdf_pages(tmp_path: Path) -> None:
    pymupdf = load_pymupdf()
    source = tmp_path / "invoice.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "ACME SAS 100.00 20.00 120.00")
    document.save(source)
    document.close()
    chat = FakeVisionChat(_legacy_payload())
    extractor = OllamaVisionExtractor(model="test-vision", chat=chat)

    extractor(source)

    images = chat.calls[0]["messages"][1]["images"]
    assert images
    assert images[0].startswith(b"\x89PNG")


def test_ollama_vision_extractor_normalises_credit_note(tmp_path: Path) -> None:
    source = tmp_path / "rectificative.png"
    source.write_bytes(b"%PDF-1.4 fake")
    chat = FakeVisionChat(
        {
            "fournisseur": "CRISOL COLOR, S.L.",
            "date_facture": "2026-09-14",
            "montant_ht": -83.43,
            "montant_tva": -83.43,
            "montant_ttc": -83.43,
            "devise": "EUR",
            "numero_facture": "AB/53",
        }
    )
    extractor = OllamaVisionExtractor(model="test-vision", chat=chat)

    invoice = InvoiceSchema.model_validate(extractor(source))

    assert invoice.document_type.value == "CREDIT_NOTE"
    assert invoice.supplier is not None
    assert invoice.supplier.name == "CRISOL COLOR, S.L."
    assert str(invoice.amount.net_amount) == "83.43"
    assert invoice.amount.tax_amount == 0
    assert str(invoice.amount.gross_amount) == "83.43"


def test_ollama_vision_extractor_rejects_invalid_json(tmp_path: Path) -> None:
    source = tmp_path / "broken.png"
    source.write_bytes(b"%PDF-1.4 fake")
    extractor = OllamaVisionExtractor(
        model="test-vision", chat=FakeVisionChat("not-json")
    )

    with pytest.raises(InvoiceProducerError) as exc_info:
        extractor(source)

    assert exc_info.value.code == "INVALID_VISION_PAYLOAD"


def test_ollama_vision_extractor_model_resolution(monkeypatch) -> None:
    monkeypatch.setenv("VISION_MODEL", "legacy-vision")
    extractor = OllamaVisionExtractor(chat=FakeVisionChat({}))
    assert extractor.model == "legacy-vision"

    monkeypatch.delenv("VISION_MODEL")
    monkeypatch.setenv("LOCAL_VISION_MODEL", "local-vision")
    assert OllamaVisionExtractor(chat=FakeVisionChat({})).model == "local-vision"
    assert (
        OllamaVisionExtractor(
            model="explicit-vision", chat=FakeVisionChat({})
        ).model
        == "explicit-vision"
    )


def test_ollama_vision_extractor_rejects_retired_model() -> None:
    with pytest.raises(ValueError, match="VISION_MODEL_NOT_CONFIGURED"):
        OllamaVisionExtractor(model="qwen2.5vl:7b")


def test_ollama_vision_extractor_missing_model(monkeypatch) -> None:
    monkeypatch.setenv("VISION_MODEL", "")
    monkeypatch.setenv("LOCAL_VISION_MODEL", "")

    with pytest.raises(OllamaVisionExtractorError, match="VISION_MODEL_NOT_CONFIGURED"):
        OllamaVisionExtractor()


def test_paintcar_producer_uses_ollama_vision_adapter() -> None:
    if not PAINTCAR_PDF.is_file():
        pytest.skip("corpus facture_paintcar.pdf absent")

    chat = FakeVisionChat(
        {
            "document_type": "CREDIT_NOTE",
            "direction": "PURCHASE",
            "supplier": {
                "name": "CRISOL COLOR, S.L.",
                "vat_number": "ESB17443482",
            },
            "reference": {"invoice_number": "AB/53", "issue_date": "2026-09-14"},
            "amount": {
                "currency": "EUR",
                "net_amount": 83.43,
                "tax_amount": 0.0,
                "gross_amount": 83.43,
            },
            "supplier_country": "ES",
        }
    )
    extractor = OllamaVisionExtractor(model="test-vision", chat=chat)

    invoice = PdfInvoiceProducer(
        PAINTCAR_PDF, vision_extractor=extractor
    ).produce()

    assert invoice.metadata["fallback_reason"] == "native_text_extraction_failed"
    assert invoice.metadata["extraction_tool"] == "ollama_vision"
    assert invoice.supplier is not None
    assert invoice.supplier.name == "CRISOL COLOR, S.L."


class FakeCloudResponse:
    def __init__(self, payload: Mapping[str, Any] | str) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        content = (
            self.payload
            if isinstance(self.payload, str)
            else json.dumps(self.payload)
        )
        return {"choices": [{"message": {"content": content}}]}


class FakeCloudPost:
    def __init__(self, payload: Mapping[str, Any] | str) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, **kwargs: Any) -> FakeCloudResponse:
        self.calls.append({"url": url, **kwargs})
        return FakeCloudResponse(self.payload)


def _cloud_payload() -> dict[str, Any]:
    return {
        "document_type": "CREDIT_NOTE",
        "direction": "PURCHASE",
        "supplier": {"name": "CRISOL COLOR, S.L.", "vat_number": "ESB17443482"},
        "reference": {"invoice_number": "AB/53", "issue_date": "2026-09-14"},
        "amount": {
            "currency": "EUR",
            "net_amount": 83.43,
            "tax_amount": 0.0,
            "gross_amount": 83.43,
        },
        "supplier_country": "ES",
    }


def test_cloud_vision_extractor_parses_openrouter_response(tmp_path: Path) -> None:
    source = tmp_path / "paintcar.png"
    source.write_bytes(b"fake-image")
    post = FakeCloudPost(_cloud_payload())
    extractor = CloudVisionExtractor(
        model="test-cloud-vision",
        api_key="test-key",
        post=post,
    )

    invoice = InvoiceSchema.model_validate(extractor(source))

    assert invoice.document_type.value == "CREDIT_NOTE"
    assert invoice.supplier is not None
    assert invoice.supplier.name == "CRISOL COLOR, S.L."
    call = post.calls[0]
    assert call["url"].endswith("/chat/completions")
    assert call["headers"]["Authorization"] == "Bearer test-key"
    assert call["json"]["model"] == "test-cloud-vision"
    image_part = call["json"]["messages"][1]["content"][1]
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")
    assert invoice.metadata["extraction_tool"] == "openrouter_vision"


def test_cloud_vision_model_resolution(monkeypatch) -> None:
    monkeypatch.setenv("CLOUD_VISION_MODEL", "cloud-vision")
    monkeypatch.setenv("VISION_FAST", "fast-vision")
    extractor = CloudVisionExtractor(
        api_key="key", post=FakeCloudPost(_cloud_payload())
    )
    assert extractor.model == "cloud-vision"

    monkeypatch.setenv("CLOUD_VISION_MODEL", "")
    extractor = CloudVisionExtractor(
        api_key="key", post=FakeCloudPost(_cloud_payload())
    )
    assert extractor.model == "fast-vision"

    explicit = CloudVisionExtractor(
        model="explicit-cloud",
        api_key="key",
        post=FakeCloudPost(_cloud_payload()),
    )
    assert explicit.model == "explicit-cloud"


def test_cloud_vision_requires_api_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "")

    with pytest.raises(CloudVisionExtractorError, match="OPENROUTER_API_KEY"):
        CloudVisionExtractor(model="test-cloud-vision")


class FailingLocalVision:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error or OllamaVisionExtractorError(
            "INVALID_VISION_PAYLOAD: montant TTC absent"
        )

    def __call__(self, source: Path) -> dict[str, Any]:
        raise self.error


def test_hybrid_escalates_to_cloud_when_local_fails(tmp_path: Path) -> None:
    source = tmp_path / "paintcar.png"
    source.write_bytes(b"fake-image")
    cloud = CloudVisionExtractor(
        model="test-cloud-vision",
        api_key="key",
        post=FakeCloudPost(_cloud_payload()),
    )
    hybrid = HybridVisionExtractor(
        local=FailingLocalVision(), cloud=cloud
    )

    payload = hybrid(source)

    invoice = InvoiceSchema.model_validate(payload)
    assert invoice.supplier is not None
    assert invoice.supplier.name == "CRISOL COLOR, S.L."
    assert payload["metadata"]["fallback_reason"] == ESCALATION_REASON
    assert payload["metadata"]["extraction_tool"] == "openrouter_vision"
    assert "montant TTC absent" in payload["metadata"]["local_vision_error"]


def test_hybrid_escalates_when_local_payload_is_invalid(tmp_path: Path) -> None:
    source = tmp_path / "paintcar.png"
    source.write_bytes(b"fake-image")
    invalid_local_payload = _cloud_payload()
    invalid_local_payload["amount"] = {
        "currency": "EUR",
        "net_amount": 100.0,
        "tax_amount": 20.0,
        "gross_amount": 999.0,
    }
    cloud = CloudVisionExtractor(
        model="test-cloud-vision",
        api_key="key",
        post=FakeCloudPost(_cloud_payload()),
    )
    hybrid = HybridVisionExtractor(
        local=lambda source: invalid_local_payload,
        cloud=cloud,
    )

    payload = hybrid(source)

    assert payload["metadata"]["fallback_reason"] == ESCALATION_REASON
    assert str(payload["amount"]["gross_amount"]) == "83.43"


def test_hybrid_keeps_valid_local_payload(tmp_path: Path) -> None:
    source = tmp_path / "paintcar.png"
    source.write_bytes(b"fake-image")
    local_payload = _cloud_payload()
    local_payload.pop("supplier_country", None)
    local_payload["metadata"] = {
        "extraction_tool": "ollama_vision",
        "supplier_country": "ES",
    }

    class FailIfCalledCloud:
        def __call__(self, source: Path) -> dict[str, Any]:
            raise AssertionError("cloud must not be called")

    payload = HybridVisionExtractor(
        local=lambda source: local_payload, cloud=FailIfCalledCloud()
    )(source)

    assert "fallback_reason" not in payload["metadata"]
    assert payload["metadata"]["extraction_tool"] == "ollama_vision"


def test_paintcar_producer_uses_hybrid_cloud_fallback() -> None:
    if not PAINTCAR_PDF.is_file():
        pytest.skip("corpus facture_paintcar.pdf absent")

    cloud = CloudVisionExtractor(
        model="test-cloud-vision",
        api_key="key",
        post=FakeCloudPost(_cloud_payload()),
    )
    hybrid = HybridVisionExtractor(
        local=FailingLocalVision(), cloud=cloud
    )

    invoice = PdfInvoiceProducer(
        PAINTCAR_PDF, vision_extractor=hybrid
    ).produce()

    assert invoice.metadata["fallback_reason"] == ESCALATION_REASON
    assert invoice.metadata["extraction_tool"] == "openrouter_vision"
    assert invoice.supplier is not None
    assert invoice.supplier.name == "CRISOL COLOR, S.L."


def test_local_invoice_extractor_uses_text_layer_then_nuextract(
    tmp_path: Path,
) -> None:
    pymupdf = load_pymupdf()
    source = tmp_path / "native.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "ACME SAS\nFACTURE F-2026-001\nTOTAL HT 100.00 TVA 20.00 TTC 120.00",
    )
    document.save(source)
    document.close()
    chat = FakeVisionChat(_legacy_payload())
    extractor = LocalInvoiceExtractor(
        model="test-extraction",
        chat=chat,
        ocr_engine_name="disabled",
        baseline_path=tmp_path / "absent.json",
    )

    payload = extractor(source)

    invoice = InvoiceSchema.model_validate(payload)
    assert invoice.supplier is not None
    assert invoice.supplier.name == "ACME SAS"
    assert invoice.reference.invoice_number == "F-2026-001"
    assert invoice.metadata["extraction_tool"] == "local_text_extraction"
    assert invoice.metadata["extraction_model"] == "test-extraction"
    assert invoice.metadata["ocr_engine"] == "text_layer"
    assert invoice.metadata["constraint_mode"] == "schema"
    assert invoice.metadata["constraint_retries"] == 0
    call = chat.calls[0]
    assert call["model"] == "test-extraction"
    assert call["format"] == invoice_json_schema()
    assert "F-2026-001" in call["messages"][0]["content"]


def test_local_invoice_extractor_uses_ocr_baseline_for_scanned_pdf() -> None:
    if not SCANNED_PDF.is_file():
        pytest.skip("corpus facture_free.pdf absent")
    baseline = (
        Path(__file__).resolve().parents[1]
        / "benchmarks"
        / "data"
        / "ocr_baseline_texts.json"
    )
    if not baseline.is_file():
        pytest.skip("ocr_baseline_texts.json absent")

    chat = FakeVisionChat(_paintcar_vision_payload())
    extractor = LocalInvoiceExtractor(
        model="test-extraction",
        chat=chat,
        ocr_engine_name="disabled",
    )

    payload = extractor(SCANNED_PDF)

    assert payload["metadata"]["ocr_engine"] == "ocr_baseline_texts"
    text_section = chat.calls[0]["messages"][0]["content"].split(
        "### Text:\n", 1
    )[1]
    assert len(text_section.strip()) > 100


def _local_extractor(
    tmp_path: Path, chat: Any, **kwargs: Any
) -> LocalInvoiceExtractor:
    return LocalInvoiceExtractor(
        model="test-extraction",
        chat=chat,
        ocr_engine_name="disabled",
        baseline_path=tmp_path / "absent.json",
        **kwargs,
    )


def _local_payload(extractor: LocalInvoiceExtractor, tmp_path: Path) -> dict[str, Any]:
    return extractor.extract_payload(
        "ACME SAS FACTURE F-2026-001 TOTAL HT 100.00 TVA 20.00 TTC 120.00",
        source=tmp_path / "invoice.pdf",
        ocr_source="text_layer",
    )


def test_local_extractor_falls_back_to_json_on_schema_rejection(
    tmp_path: Path,
) -> None:
    chat = SequencedVisionChat(
        [RuntimeError("JSON schema conversion failed"), _legacy_payload()]
    )
    extractor = _local_extractor(tmp_path, chat)

    payload = _local_payload(extractor, tmp_path)

    assert chat.calls[0]["format"] == invoice_json_schema()
    assert chat.calls[1]["format"] == "json"
    assert payload["metadata"]["constraint_mode"] == "json"
    assert payload["metadata"]["constraint_retries"] == 1
    assert "conversion failed" in payload["metadata"]["constraint_fallback_reason"]
    assert payload["amount"]["gross_amount"] == "120.0"


def test_local_extractor_falls_back_when_schema_output_is_incomplete(
    tmp_path: Path,
) -> None:
    chat = SequencedVisionChat(
        [
            {"amount": {"currency": "EUR", "gross_amount": 120.0}},
            _legacy_payload(),
        ]
    )
    extractor = _local_extractor(tmp_path, chat)

    payload = _local_payload(extractor, tmp_path)

    assert chat.calls[0]["format"] == invoice_json_schema()
    assert chat.calls[1]["format"] == "json"
    assert payload["metadata"]["constraint_mode"] == "json"
    assert payload["metadata"]["constraint_retries"] == 1
    assert "fournisseur" in payload["metadata"]["constraint_fallback_reason"]


def test_local_extractor_falls_back_to_unconstrained_text(tmp_path: Path) -> None:
    fenced = f"```json\n{json.dumps(_legacy_payload())}\n```"
    chat = SequencedVisionChat(
        [
            RuntimeError("schema rejected"),
            TimeoutError("json mode failed"),
            fenced,
        ]
    )
    extractor = _local_extractor(tmp_path, chat)

    payload = _local_payload(extractor, tmp_path)

    assert chat.calls[0]["format"] == invoice_json_schema()
    assert chat.calls[1]["format"] == "json"
    assert chat.calls[2]["format"] is None
    assert payload["metadata"]["constraint_mode"] == "none"
    assert payload["metadata"]["constraint_retries"] == 2
    assert "json mode failed" in payload["metadata"]["constraint_fallback_reason"]


def test_local_extractor_raises_when_constraint_ladder_is_exhausted(
    tmp_path: Path,
) -> None:
    chat = SequencedVisionChat(
        [
            RuntimeError("schema rejected"),
            TimeoutError("json failed"),
            ValueError("none failed"),
        ]
    )
    extractor = _local_extractor(tmp_path, chat)

    with pytest.raises(LocalInvoiceExtractorError) as exc_info:
        _local_payload(extractor, tmp_path)

    message = str(exc_info.value)
    assert "schema:" in message
    assert "json:" in message
    assert "none:" in message
    assert len(chat.calls) == 3


def test_local_extractor_respects_forced_constraint_mode(tmp_path: Path) -> None:
    chat = FakeVisionChat(_legacy_payload())
    extractor = _local_extractor(tmp_path, chat, format_mode="json")

    payload = _local_payload(extractor, tmp_path)

    assert len(chat.calls) == 1
    assert chat.calls[0]["format"] == "json"
    assert payload["metadata"]["constraint_mode"] == "json"
    assert payload["metadata"]["constraint_retries"] == 0


def test_local_extractor_sticks_to_downgraded_mode(tmp_path: Path) -> None:
    chat = SequencedVisionChat(
        [RuntimeError("schema rejected"), _legacy_payload(), _legacy_payload()]
    )
    extractor = _local_extractor(tmp_path, chat)

    _local_payload(extractor, tmp_path)
    second = _local_payload(extractor, tmp_path)

    assert [call["format"] for call in chat.calls] == [
        invoice_json_schema(),
        "json",
        "json",
    ]
    assert second["metadata"]["constraint_mode"] == "json"
    assert second["metadata"]["constraint_retries"] == 0


def test_local_extractor_reads_format_mode_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCAL_EXTRACTION_FORMAT_MODE", "none")
    extractor = _local_extractor(tmp_path, FakeVisionChat(_legacy_payload()))

    assert extractor.format_mode == "none"


def test_hybrid_defaults_local_to_two_stage_extractor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_EXTRACTION_MODEL", "test-extraction")

    class FailIfCalledCloud:
        def __call__(self, source: Path) -> dict[str, Any]:
            raise AssertionError("cloud must not be called")

    hybrid = HybridVisionExtractor(cloud=FailIfCalledCloud())

    assert isinstance(hybrid.local, LocalInvoiceExtractor)
    assert hybrid.local.model == "test-extraction"
