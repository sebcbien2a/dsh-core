"""Tests for the local, cloud and hybrid document engines."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from schemas.document_perception import (
    BBox,
    Block,
    DocumentPerception,
    PagePerception,
    TaskSpec,
)
from schemas.profile_schema import DocumentProfile
from services.cloud_vision_extractor import (
    CloudEscalationBudget,
    CloudVisionExtractorError,
)
from services.document_engine import (
    CloudDocumentEngine,
    HybridDocumentEngine,
    LocalDocumentEngine,
)
import services.document_engine as engine_module
from services.document_extractors import ExtractionUnavailable
from services.document_router import reset_route_cache


def _native_pdf(tmp_path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    lines = [
        "ACME SAS",
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
    path = tmp_path / "native.pdf"
    document.save(path)
    document.close()
    return path


def _table_pdf(tmp_path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page(width=400, height=400)
    page.insert_text((40, 60), "Rapport annuel de synthese", fontsize=11)
    for row in range(4):
        page.draw_line((40, 100 + row * 50), (360, 100 + row * 50))
    for column in range(4):
        page.draw_line((40 + column * 106, 100), (40 + column * 106, 250))
    for row in range(3):
        for column in range(3):
            page.insert_text(
                (45 + column * 106, 130 + row * 50),
                f"cell{row}{column}",
                fontsize=10,
            )
    path = tmp_path / "table.pdf"
    document.save(path)
    document.close()
    return path


def _rotated_annotated_pdf(tmp_path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page(width=500, height=800)
    page.insert_text(
        (100, 700),
        "FACTURE N FA-2026-001 TOTAL HT 100,00 TVA 20,00 TTC 120,00 "
        "FOURNISSEUR ACME",
        fontsize=12,
        rotate=90,
    )
    page.add_ink_annot([[(80, 600), (110, 650), (80, 700)]])
    path = tmp_path / "rotated-annotated.pdf"
    document.save(path)
    document.close()
    return path


def _perception(raw_text: str = "Facture N F-2026-001 Total TTC 120,00") -> DocumentPerception:
    profile = DocumentProfile(
        extension=".pdf",
        has_text_layer=True,
        word_count=10,
        has_tables=False,
        has_images=False,
        is_scanned=False,
        profile_type="native_pdf",
        business_family="invoice",
        page_count=1,
    )
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
    )
    return DocumentPerception(
        source_name="cloud.pdf",
        source_sha256="a" * 64,
        profile=profile,
        pages=[page],
        raw_text=raw_text,
        tools_used=["test/cloud-model"],
        metadata={
            "routing_route": "cloud_vision",
            "routing_family": "invoice",
            "rotation_applied": 0,
        },
    )


def test_local_engine_perceives_native_pdf(tmp_path):
    perception = LocalDocumentEngine().perceive(_native_pdf(tmp_path))

    assert isinstance(perception, DocumentPerception)
    assert perception.pages
    assert perception.raw_text.strip()
    assert "pymupdf_text" in perception.tools_used
    assert perception.profile.profile_type == "native_pdf"


def test_local_engine_extracts_table_blocks(tmp_path):
    perception = LocalDocumentEngine().perceive(_table_pdf(tmp_path))

    blocks = [block for page in perception.pages for block in page.blocks]
    assert any(block.kind == "table" for block in blocks)
    assert any("find_tables" == tool for tool in perception.tools_used)


def test_local_engine_rotates_annotations_and_blocks_upright(tmp_path):
    source = _rotated_annotated_pdf(tmp_path)
    with pymupdf.open(source) as document:
        page = document[0]
        raw_annotation = list(page.annots())[0]
        raw_points = [
            (float(point[0]), float(point[1]))
            for point in raw_annotation.vertices[0]
        ]

    perception = LocalDocumentEngine().perceive(source)
    page = perception.pages[0]

    assert page.rotation_applied == 90
    assert (page.width, page.height) == (800.0, 500.0)
    assert any(block.kind == "text" for block in page.blocks)
    expected_points = [(800.0 - y, x) for x, y in raw_points]
    assert any(
        annotation.points == expected_points
        for annotation in page.annotations
    )
    assert all(
        0 <= axis <= page.width
        for annotation in page.annotations
        for point in annotation.points
        for axis in (point[0],)
    )
    assert all(
        0 <= point[1] <= page.height
        for annotation in page.annotations
        for point in annotation.points
    )


def test_local_engine_falls_back_when_registry_fails(tmp_path):
    class FailingRegistry:
        def run_plan(self, plan, source, *, dpi=170):
            raise ExtractionUnavailable("EXTRACTOR_FAILED:TestRegistry", "boom")

    perception = LocalDocumentEngine(
        registry=FailingRegistry()
    ).perceive(_native_pdf(tmp_path))

    assert perception.pages == []
    assert perception.tools_used == ["legacy_fallback"]
    assert any(
        note.startswith("PERCEPTION_FAILED:")
        for note in perception.processing_notes
    )


def test_local_engine_structure_requires_extractor():
    engine = LocalDocumentEngine()
    task = TaskSpec(task_id="t1", kind="text", tool="pymupdf_text")

    with pytest.raises(ExtractionUnavailable) as error:
        engine.structure("some text", task)

    assert error.value.code == "STRUCTURED_EXTRACTOR_MISSING"


class FakeCloudClient:
    def __init__(self):
        self.perceive_calls = []
        self.structure_calls = []

    def perceive(self, *, images, model, prompt):
        self.perceive_calls.append(
            {"images": images, "model": model, "prompt": prompt}
        )
        return {
            "pages": [
                {
                    "page_index": 0,
                    "width": 600,
                    "height": 800,
                    "blocks": [
                        {
                            "block_id": "cloud-1",
                            "kind": "text",
                            "bbox": [0, 0, 120, 30],
                            "text": "Cloud text",
                            "confidence": 0.9,
                        }
                    ],
                }
            ],
            "raw_text": "Cloud text",
        }

    def structure(self, *, text, task, model):
        self.structure_calls.append(
            {"text": text, "task": task, "model": model}
        )
        return {"result": "structured"}


def test_cloud_engine_perceives_from_client_json(tmp_path):
    client = FakeCloudClient()
    engine = CloudDocumentEngine(
        model="google/gemini-3.8-flash", client=client
    )

    perception = engine.perceive(_native_pdf(tmp_path))

    assert client.perceive_calls
    assert perception.pages
    assert perception.pages[0].blocks[0].text == "Cloud text"
    assert "google/gemini-3.8-flash" in perception.tools_used
    assert perception.raw_text == "Cloud text"


def test_cloud_engine_structure_returns_mapping():
    client = FakeCloudClient()
    engine = CloudDocumentEngine(
        model="google/gemini-3.8-flash", client=client
    )
    task = TaskSpec(task_id="t1", kind="text", tool="pymupdf_text")

    result = engine.structure("some text", task)

    assert result == {"result": "structured"}
    assert client.structure_calls[0]["text"] == "some text"


def test_cloud_engine_teacher_distill_shape():
    engine = CloudDocumentEngine(
        model="google/gemini-3.8-flash", client=FakeCloudClient()
    )
    perception = _perception(
        "FACTURE N° FA-2026-001\nDate: 30/10/2026\nTotal TTC 120,00"
    )

    skill = engine.teacher_distill(perception, supplier_id="supplier-1")

    assert "trusted_labels" in skill["extraction_rules"]
    assert "regex_numero_facture" in skill["extraction_rules"]
    assert isinstance(skill["anchors"], list)
    assert skill["supplier_id"] == "supplier-1"


class FailingLocalEngine:
    def __init__(self, error):
        self.error = error
        self.calls = 0

    def perceive(self, source):
        self.calls += 1
        raise self.error

    def extract_text(self, perception):
        return ""

    def structure(self, text, task):
        return {}


class CountingCloudEngine:
    def __init__(self, perception):
        self.perception = perception
        self.calls = 0

    def perceive(self, source):
        self.calls += 1
        return self.perception

    def extract_text(self, perception):
        return perception.raw_text

    def structure(self, text, task):
        return {}


def test_hybrid_escalates_on_supervisor_marker(tmp_path):
    local = FailingLocalEngine(
        RuntimeError("INVALID_VISION_PAYLOAD: montant TTC absent")
    )
    cloud = CountingCloudEngine(_perception())
    budget = CloudEscalationBudget(5)
    hybrid = HybridDocumentEngine(local, cloud, budget)

    perception = hybrid.perceive(_native_pdf(tmp_path))

    assert perception.raw_text
    assert cloud.calls == 1
    assert budget.used == 1


def test_hybrid_does_not_escalate_on_other_errors(tmp_path):
    local = FailingLocalEngine(RuntimeError("unrelated failure"))
    cloud = CountingCloudEngine(_perception())
    hybrid = HybridDocumentEngine(local, cloud, CloudEscalationBudget(5))

    perception = hybrid.perceive(_native_pdf(tmp_path))

    assert cloud.calls == 0
    assert perception.tools_used == ["legacy_fallback"]
    assert any(
        note.startswith("PERCEPTION_FAILED:")
        for note in perception.processing_notes
    )


def test_hybrid_budget_exhaustion_raises(tmp_path):
    local = FailingLocalEngine(
        RuntimeError("INVALID_VISION_PAYLOAD: montant TTC absent")
    )
    cloud = CountingCloudEngine(_perception())
    hybrid = HybridDocumentEngine(local, cloud, CloudEscalationBudget(0))

    with pytest.raises(
        CloudVisionExtractorError, match="ESCALATION_BUDGET_EXCEEDED"
    ):
        hybrid.perceive(_native_pdf(tmp_path))


def _drawing_pdf(tmp_path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page(width=300, height=300)
    page.draw_oval(pymupdf.Rect(50, 50, 150, 150))
    path = tmp_path / "drawing.pdf"
    document.save(path)
    document.close()
    return path


def test_engine_reuses_verdict_geometry(tmp_path, monkeypatch):
    calls = []
    original = engine_module.normalise_geometry

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(engine_module, "normalise_geometry", spy)
    reset_route_cache()

    perception = LocalDocumentEngine().perceive(_native_pdf(tmp_path))

    assert calls == []
    assert perception.pages
    assert perception.processing_notes


def test_engine_ignores_vector_drawings_by_default(tmp_path):
    reset_route_cache()
    perception = LocalDocumentEngine().perceive(_drawing_pdf(tmp_path))

    annotations = [
        annotation
        for page in perception.pages
        for annotation in page.annotations
    ]
    assert annotations == []


def test_engine_can_include_vector_drawings(tmp_path):
    reset_route_cache()
    perception = LocalDocumentEngine(
        include_vector_drawings=True
    ).perceive(_drawing_pdf(tmp_path))

    annotations = [
        annotation
        for page in perception.pages
        for annotation in page.annotations
    ]
    assert annotations
