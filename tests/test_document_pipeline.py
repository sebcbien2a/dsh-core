"""Tests for the document pipeline facade and the PdfInvoiceProducer bridge."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf
import pytest

import services.document_pipeline as pipeline
from schemas.document_perception import (
    BBox,
    Block,
    DocumentPerception,
    PagePerception,
    TaskSpec,
    VisualAnnotation,
)
from schemas.profile_schema import DocumentProfile
from services.document_engine import HybridDocumentEngine, LocalDocumentEngine
from services.document_pipeline import (
    is_pipeline_enabled,
    perception_summary,
    perceive_document,
)
from services.cloud_vision_extractor import budget_max
from services.document_router import reset_route_cache
from services.invoice_producer_protocol import PdfInvoiceProducer
from services.template_skill_store import TemplateSkillStore
from scripts.run_batch_audit import DEFAULT_RULES_DIR, run_batch_audit

REPO_ROOT = Path(__file__).resolve().parents[1]


def _invoice_pdf(tmp_path: Path, supplier: str = "ACME SAS") -> Path:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    lines = [
        supplier,
        "12 rue des Lilas",
        "75001 Paris",
        "SIRET: 123 456 789 00012",
        "Numero TVA: FR12345678901",
        "Facture N: F-2026-001",
        "Date: 14/09/2026",
        "Net HT 100,00 20,00 120,00",
    ]
    for index, line in enumerate(lines):
        page.insert_text((50, 80 + index * 20), line, fontsize=11)
    path = tmp_path / f"{supplier.replace(' ', '_')}.pdf"
    document.save(path)
    document.close()
    return path


def _perception(
    raw_text: str,
    *,
    annotations: int = 0,
) -> DocumentPerception:
    profile = DocumentProfile(
        extension=".pdf",
        has_text_layer=True,
        word_count=len(raw_text.split()),
        has_tables=False,
        has_images=False,
        is_scanned=False,
        profile_type="native_pdf",
        business_family="invoice",
        page_count=1,
    )
    page_annotations = [
        VisualAnnotation(
            kind="square",
            page_index=0,
            bbox=BBox(x0=10, y0=10, x1=20, y1=20),
            confidence=0.9,
            anchor_block_id="b1",
            raw_text_target=raw_text[:200],
        )
        for _ in range(annotations)
    ]
    page = PagePerception(
        page_index=0,
        width=595,
        height=842,
        blocks=[
            Block(
                block_id="b1",
                kind="text",
                bbox=BBox(x0=0, y0=0, x1=300, y1=20),
                text=raw_text,
            )
        ],
        annotations=page_annotations,
    )
    return DocumentPerception(
        source_name="perception.pdf",
        source_sha256="a" * 64,
        profile=profile,
        pages=[page],
        raw_text=raw_text,
        tools_used=["pymupdf_text"],
        processing_notes=["native_text_extracted"],
        metadata={
            "routing_route": "native_text",
            "routing_family": "invoice",
            "rotation_applied": 0,
        },
    )


def test_perceive_document_returns_full_perception(tmp_path):
    source = _invoice_pdf(tmp_path)

    perception = perceive_document(source, engine=LocalDocumentEngine())

    assert isinstance(perception, DocumentPerception)
    assert perception.pages
    assert perception.raw_text.strip()
    assert "pymupdf_text" in perception.tools_used


def test_perceive_document_on_corrupt_file_never_raises(tmp_path):
    source = tmp_path / "corrupt.pdf"
    source.write_bytes(b"not a pdf at all")

    perception = perceive_document(source)

    assert perception.tools_used == ["legacy_fallback"]
    assert any(
        note.startswith("PERCEPTION_FAILED:")
        for note in perception.processing_notes
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, True),
        ("", True),
        ("on", True),
        ("1", True),
        ("true", True),
        ("yes", True),
        ("off", False),
        ("0", False),
        ("false", False),
        ("no", False),
    ],
)
def test_is_pipeline_enabled_parsing(value, expected):
    env = {} if value is None else {"DOCUMENT_PIPELINE": value}
    assert is_pipeline_enabled(env) is expected


def test_bridge_pipeline_off_keeps_legacy_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMENT_PIPELINE", "off")
    source = _invoice_pdf(tmp_path)

    invoice = PdfInvoiceProducer(source).produce()
    metadata = invoice.metadata

    assert not any(key.startswith("perception_") for key in metadata)
    assert metadata["extraction_tool"] == "pymupdf_text_regex"
    assert metadata["producer"] == "pdf_native_or_vision"
    assert metadata["raw_text_chars"] > 0


def test_bridge_pipeline_on_adds_perception_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMENT_PIPELINE", "on")
    source = _invoice_pdf(tmp_path)
    fake = _perception(
        "ACME SAS\n12 rue des Lilas\n75001 Paris\n"
        "Facture N: F-2026-001\nDate: 14/09/2026\n"
        "Net HT 100,00 20,00 120,00"
    )
    monkeypatch.setattr(pipeline, "perceive_document", lambda source: fake)

    invoice = PdfInvoiceProducer(source).produce()
    metadata = invoice.metadata

    assert metadata["perception_profile"] == "native_pdf"
    assert metadata["routing_route"] == "native_text"
    assert metadata["extraction_tool"] == "pymupdf_text_regex"
    assert metadata["producer"] == "pdf_native_or_vision"
    assert metadata["raw_text_chars"] > 0


def test_bridge_rescues_text_from_perception(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMENT_PIPELINE", "on")
    source = tmp_path / "scan.png"
    source.write_bytes(b"fake-image-without-text-layer")
    rescued_text = (
        "ACME SAS\n12 rue des Lilas\n75001 Paris\n"
        "Facture N: F-2026-001\nDate: 14/09/2026\n"
        "Net HT 100,00 20,00 120,00"
    )
    fake = _perception(rescued_text)
    monkeypatch.setattr(pipeline, "perceive_document", lambda source: fake)

    invoice = PdfInvoiceProducer(source).produce()

    assert invoice.supplier is not None
    assert invoice.supplier.name == "ACME SAS"
    assert invoice.metadata["raw_text_chars"] == len(
        " ".join(rescued_text.split())
    )


def test_bridge_keeps_legacy_text_when_it_is_already_rich(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DOCUMENT_PIPELINE", "on")
    source = _invoice_pdf(tmp_path, supplier="ACME SAS")
    fake = _perception(
        "OTHER SAS\n1 avenue du Test\n75002 Paris\n"
        "Facture N: X-999\nDate: 01/01/2026\n"
        "Net HT 999,00 0,00 999,00"
    )
    monkeypatch.setattr(pipeline, "perceive_document", lambda source: fake)

    invoice = PdfInvoiceProducer(source).produce()

    assert invoice.supplier is not None
    assert invoice.supplier.name == "ACME SAS"
    assert invoice.reference.invoice_number == "F-2026-001"


def test_get_default_engine_caches_and_honours_env(monkeypatch):
    monkeypatch.delenv("DOCUMENT_ENGINE", raising=False)
    pipeline.reset_default_engine()

    first = pipeline.get_default_engine()
    second = pipeline.get_default_engine()
    assert isinstance(first, LocalDocumentEngine)
    assert first is second

    monkeypatch.setenv("DOCUMENT_ENGINE", "hybrid")
    hybrid = pipeline.get_default_engine()
    assert isinstance(hybrid, HybridDocumentEngine)
    assert pipeline.get_default_engine() is hybrid


def test_perceive_document_falls_back_when_engine_raises(tmp_path):
    class RaisingEngine:
        def perceive(self, source):
            raise RuntimeError("engine exploded")

        def extract_text(self, perception):
            return ""

        def structure(self, text, task):
            return {}

    perception = perceive_document(
        _invoice_pdf(tmp_path), engine=RaisingEngine()
    )

    assert perception.tools_used == ["legacy_fallback"]
    assert perception.processing_notes == [
        "PERCEPTION_FAILED:RUNTIMEERROR"
    ]


def test_perception_summary_has_stable_keys():
    perception = _perception(
        "Facture N F-2026-001 Total TTC 120,00", annotations=2
    )

    summary = perception_summary(perception)

    assert set(summary) == {
        "perception_profile",
        "perception_tools",
        "perception_annotations",
        "routing_route",
        "routing_family",
        "rotation_applied",
    }
    assert summary["perception_annotations"] == 2
    assert summary["perception_tools"] == ["pymupdf_text"]
    assert summary["routing_route"] == "native_text"


class _CountingEngine:
    dpi = 170

    def __init__(self):
        self.calls = 0
        self.perception = _perception("Facture N F-2026-001 Total TTC 120,00")

    def perceive(self, source):
        self.calls += 1
        return self.perception

    def extract_text(self, perception):
        return perception.raw_text

    def structure(self, text, task):
        return {}


def test_perception_cache_hits_on_second_call(tmp_path):
    engine = _CountingEngine()
    source = _invoice_pdf(tmp_path)
    pipeline.reset_perception_cache()

    first = perceive_document(source, engine=engine)
    second = perceive_document(source, engine=engine)

    assert engine.calls == 1
    assert first is second


def test_set_default_engine_and_document_count_budget(monkeypatch):
    monkeypatch.setenv("DOCUMENT_ENGINE", "hybrid")
    monkeypatch.setenv("CLOUD_ESCALATION_BUDGET_RATIO", "0.5")
    pipeline.reset_default_engine()

    engine = pipeline.get_default_engine(document_count=6)
    assert isinstance(engine, HybridDocumentEngine)
    assert engine.budget.max_escalations == budget_max(0.5, 6) == 3

    custom = LocalDocumentEngine()
    pipeline.set_default_engine(custom)
    assert pipeline.get_default_engine() is custom
    pipeline.reset_default_engine()
    pipeline.reset_perception_cache()


def _audit_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _invoice_pdf(corpus)
    return corpus


def test_batch_audit_summary_contains_perception_metrics(tmp_path):
    result = run_batch_audit(
        _audit_corpus(tmp_path),
        rules_dir=DEFAULT_RULES_DIR,
        output_dir=tmp_path / "audit",
        document_pipeline=True,
        vision="none",
    )

    perception = result.summary["perception"]
    assert perception["enabled"] is True
    assert perception["profiles"].get("native_pdf", 0) >= 1
    assert perception["routes"].get("native_text", 0) >= 1
    assert perception["failures"] == 0
    assert result.records[0]["perception"]["route"] == "native_text"


def test_batch_audit_report_contains_perception_section(tmp_path):
    result = run_batch_audit(
        _audit_corpus(tmp_path),
        rules_dir=DEFAULT_RULES_DIR,
        output_dir=tmp_path / "audit",
        document_pipeline=True,
        vision="none",
    )

    report = (result.run_dir / "report.md").read_text(encoding="utf-8")
    assert "## Perception documentaire" in report
    assert "### Profils" in report
    assert "### Routes" in report


def _rich_pdf(tmp_path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    for row in range(10):
        words = " ".join(f"mot{row}_{column}" for column in range(6))
        page.insert_text((50, 80 + row * 22), words, fontsize=10)
    path = tmp_path / "rich.pdf"
    document.save(path)
    document.close()
    return path


def test_default_engine_has_template_skill_store(monkeypatch):
    monkeypatch.setenv("DOCUMENT_ENGINE", "local")
    pipeline.reset_default_engine()

    engine = pipeline.get_default_engine()

    assert isinstance(engine, LocalDocumentEngine)
    assert isinstance(engine.skill_store, TemplateSkillStore)
    pipeline.reset_default_engine()
    pipeline.reset_perception_cache()


@pytest.mark.skipif(
    not (REPO_ROOT / "workspace/invoices/input/facture_paintcar.pdf").is_file(),
    reason="Crisol control document is absent",
)
def test_crisol_perception_and_structure_use_template_without_llm():
    source = REPO_ROOT / "workspace/invoices/input/facture_paintcar.pdf"
    calls: list[tuple[str, TaskSpec]] = []

    def structured(text: str, task: TaskSpec) -> dict[str, object]:
        calls.append((text, task))
        return {"fallback": True}

    store = TemplateSkillStore()
    engine = LocalDocumentEngine(skill_store=store, structured=structured)
    pipeline.reset_perception_cache()
    perception = perceive_document(source, engine=engine)
    task = TaskSpec(task_id="t1", kind="text", tool="pymupdf_text")

    result = engine.structure(
        perception.raw_text, task, perception=perception
    )

    assert calls == []
    assert result["source"] == "template_skill"
    assert result["fields"]["numero_facture"] == "AB/53"
    assert result["fields"]["ttc"] == "-83,43"


def test_batch_audit_writes_signed_baseline(tmp_path):
    corpus = _audit_corpus(tmp_path)
    baseline_path = tmp_path / "baseline.json"

    result = run_batch_audit(
        corpus,
        rules_dir=DEFAULT_RULES_DIR,
        output_dir=tmp_path / "audit",
        document_pipeline=True,
        vision="none",
        baseline_out=baseline_path,
        tests_passed=1321,
    )

    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    contract_path = REPO_ROOT / "docs" / "DOCUMENT_PERCEPTION_CONTRACT.md"
    contract_sha = hashlib.sha256(contract_path.read_bytes()).hexdigest()
    unsigned = {
        key: value
        for key, value in payload.items()
        if key not in {"signed_at", "signature"}
    }
    unsigned_sha = hashlib.sha256(
        json.dumps(
            unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()

    assert payload["contract_version"] == "1.0.1"
    assert payload["contract_sha256"] == contract_sha
    assert payload["signature"] == unsigned_sha
    assert payload["run_id"] == result.run_id
    assert payload["tests_passed"] == 1321
    assert set(payload["timing"]) == {"off_ms", "on_ms", "ratio"}
    assert "## Baseline" in (result.run_dir / "report.md").read_text(
        encoding="utf-8"
    )


def test_fast_path_and_full_path_produce_same_perception(tmp_path, monkeypatch):
    import services.document_geometry as geometry

    source = _rich_pdf(tmp_path)
    engine = LocalDocumentEngine()
    pipeline.reset_perception_cache()
    fast = perceive_document(source, engine=engine)

    original = geometry._fast_path_eligible
    monkeypatch.setattr(
        geometry,
        "_fast_path_eligible",
        lambda page: (False, "forced_full_path"),
    )
    pipeline.reset_perception_cache()
    full = perceive_document(source, engine=engine)
    monkeypatch.setattr(geometry, "_fast_path_eligible", original)

    assert fast.pages == full.pages
    assert fast.raw_text == full.raw_text
    assert all(page.rotation_applied == 0 for page in fast.pages)
    assert all(page.rotation_applied == 0 for page in full.pages)


def test_vector_drawings_env_controls_default_engine(monkeypatch):
    monkeypatch.setenv("PERCEPTION_VECTOR_DRAWINGS", "on")
    pipeline.reset_default_engine()
    enabled = pipeline.get_default_engine()
    assert isinstance(enabled, LocalDocumentEngine)
    assert enabled.include_vector_drawings is True

    monkeypatch.setenv("PERCEPTION_VECTOR_DRAWINGS", "off")
    pipeline.reset_default_engine()
    disabled = pipeline.get_default_engine()
    assert disabled.include_vector_drawings is False
    pipeline.reset_default_engine()


def test_route_cache_is_visible_through_perceive_document(tmp_path, monkeypatch):
    calls = {"count": 0}
    original = pymupdf.Page.find_tables

    def spy(page):
        calls["count"] += 1
        return original(page)

    monkeypatch.setattr(pymupdf.Page, "find_tables", spy)
    source = _invoice_pdf(tmp_path)
    reset_route_cache()
    pipeline.reset_perception_cache()
    engine = LocalDocumentEngine()

    engine.router.route(source)
    first = calls["count"]
    assert first > 0
    perceive_document(source, engine=engine)

    assert calls["count"] == first
