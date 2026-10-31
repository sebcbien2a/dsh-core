"""Contract tests for the Document & Vision perception schemas."""

import pytest
from pydantic import ValidationError

from schemas.document_perception import (
    BBox,
    Block,
    DocumentPerception,
    PagePerception,
    VisualAnnotation,
)
from schemas.document_result import DocumentResult
from schemas.profile_schema import DocumentProfile


@pytest.mark.parametrize(
    "coordinates",
    [
        {"x0": 10.0, "y0": 0.0, "x1": 5.0, "y1": 10.0},
        {"x0": 0.0, "y0": 10.0, "x1": 10.0, "y1": 5.0},
    ],
)
def test_bbox_rejects_inverted_coordinates(coordinates):
    with pytest.raises(ValidationError, match="x0 <= x1 and y0 <= y1"):
        BBox(**coordinates)


def test_annotation_kind_and_confidence_validation():
    with pytest.raises(ValidationError):
        VisualAnnotation(kind="scribble")
    with pytest.raises(ValidationError):
        VisualAnnotation(kind="circle", confidence=1.5)
    with pytest.raises(ValidationError):
        VisualAnnotation(kind="highlight", confidence=-0.1)

    annotation = VisualAnnotation(
        kind="circle",
        page_index=0,
        bbox=BBox(x0=1, y0=2, x1=3, y1=4),
        points=[(1.0, 2.0), (3.0, 4.0)],
        confidence=0.42,
        anchor_block_id="block-1",
        raw_text_target="Total TTC",
    )
    assert annotation.kind == "circle"
    assert annotation.confidence == pytest.approx(0.42)


def test_block_table_grid_must_be_rectangular():
    with pytest.raises(ValidationError, match="table grid must be rectangular"):
        Block(
            block_id="table-1",
            kind="table",
            page_index=0,
            bbox=BBox(x0=0, y0=0, x1=100, y1=50),
            table=[["a"], ["b", "c"]],
        )

    block = Block(
        block_id="table-2",
        kind="table",
        page_index=0,
        bbox=BBox(x0=0, y0=0, x1=100, y1=50),
        table=[["a", "b"], ["c", "d"]],
    )
    assert block.table == [["a", "b"], ["c", "d"]]


def _sample_perception() -> DocumentPerception:
    profile = DocumentProfile(
        extension=".pdf",
        has_text_layer=True,
        word_count=42,
        has_tables=True,
        has_images=False,
        is_scanned=False,
        profile_type="complex_pdf",
        orientation_degrees=90,
        page_count=1,
        business_family="supplier_invoice",
    )
    page = PagePerception(
        page_index=0,
        width=595.0,
        height=842.0,
        rotation_applied=90,
        blocks=[
            Block(
                block_id="block-1",
                kind="text",
                page_index=0,
                bbox=BBox(x0=10, y0=20, x1=200, y1=40),
                text="Facture 2026",
                confidence=0.99,
                reading_order=0,
            ),
            Block(
                block_id="block-2",
                kind="table",
                page_index=0,
                bbox=BBox(x0=10, y0=60, x1=300, y1=180),
                table=[["Designation", "Montant"], ["Prestation", "100.00"]],
                confidence=0.95,
                reading_order=1,
            ),
        ],
        annotations=[
            VisualAnnotation(
                kind="circle",
                page_index=0,
                bbox=BBox(x0=40, y0=65, x1=90, y1=85),
                points=[(40.0, 65.0), (90.0, 85.0)],
                confidence=0.8,
                anchor_block_id="block-2",
                raw_text_target="Total TTC",
            )
        ],
    )
    return DocumentPerception(
        source_name="facture.pdf",
        source_sha256="a" * 64,
        profile=profile,
        pages=[page],
        raw_text="Facture 2026\nPrestation 100.00",
        tools_used=["PyMuPDF", "PaddleOCR"],
        processing_notes=["native_text_extracted", "table_detected"],
        metadata={"locale": "fr-FR", "batch_id": "batch-1"},
    )


def test_document_perception_json_round_trip():
    perception = _sample_perception()
    restored = DocumentPerception.model_validate_json(perception.model_dump_json())
    assert restored == perception
    assert restored.total_text() == len(perception.raw_text)
    assert perception.pages[0].blocks[1].table == [
        ["Designation", "Montant"],
        ["Prestation", "100.00"],
    ]


def test_bridge_to_and_from_document_result():
    legacy = DocumentResult(
        source_name="facture.pdf",
        profile_type="native_pdf",
        extraction_tool="PyMuPDF",
        raw_text="Facture 2026 Prestation 100.00",
        business_type="supplier_invoice",
        confidence=0.93,
        metadata={
            "extension": ".pdf",
            "word_count": 4,
            "has_tables": False,
            "has_images": False,
            "is_scanned": False,
        },
        processing_notes=["native_text_extracted"],
    )

    perception = DocumentPerception.from_document_result(legacy)
    assert perception.profile.profile_type == "native_pdf"
    assert perception.total_text() == len(legacy.raw_text)
    assert perception.to_document_result() == legacy
