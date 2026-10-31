"""Tests for annotation perception and editing targets."""

from __future__ import annotations

import pymupdf
import pytest

from schemas.document_perception import (
    BBox,
    Block,
    DocumentPerception,
    PagePerception,
    VisualAnnotation,
)
from schemas.profile_schema import DocumentProfile
from services.annotation_perception import (
    EditingTaskAdapter,
    extract_annotations,
    extract_annotations_with_notes,
    find_anchor_block,
)


class FakeRect:
    def __init__(self, x0, y0, x1, y1):
        self.x0 = x0
        self.y0 = y0
        self.x1 = x1
        self.y1 = y1


class FakeAnnot:
    def __init__(self, type_name, rect, vertices=None):
        self.type = (1, type_name)
        self.rect = rect
        self.vertices = vertices


class FakePage:
    def __init__(self, annotations=(), drawings=(), rotation=0):
        self._annotations = list(annotations)
        self._drawings = list(drawings)
        self.rotation = rotation

    def annots(self):
        return list(self._annotations)

    def get_cdrawings(self):
        return list(self._drawings)


def _block(block_id="b1", bbox=None, text="Total TTC 100 EUR"):
    return Block(
        block_id=block_id,
        kind="text",
        page_index=0,
        bbox=bbox or BBox(x0=40, y0=60, x1=260, y1=110),
        text=text,
    )


@pytest.mark.parametrize(
    ("type_name", "expected"),
    [
        ("Square", "square"),
        ("Circle", "circle"),
        ("Highlight", "highlight"),
        ("StrikeOut", "strike"),
        ("Underline", "underline"),
        ("Squiggly", "underline"),
        ("Ink", "ink"),
        ("Line", "arrow"),
        ("FreeText", "margin_note"),
        ("Polygon", "unknown"),
    ],
)
def test_annotation_type_mapping(type_name, expected):
    page = FakePage(
        [FakeAnnot(type_name, FakeRect(50, 70, 100, 90))]
    )

    annotations = extract_annotations(page, page_index=0, blocks=[])

    assert len(annotations) == 1
    assert annotations[0].kind == expected
    assert annotations[0].confidence == 1.0


def test_square_annotation_is_anchored_to_text_block():
    block = _block()
    page = FakePage([FakeAnnot("Square", FakeRect(50, 70, 120, 90))])

    annotations = extract_annotations(
        page, page_index=0, blocks=[block]
    )

    assert annotations[0].bbox == BBox(x0=50, y0=70, x1=120, y1=90)
    assert annotations[0].anchor_block_id == "b1"
    assert annotations[0].raw_text_target == "Total TTC 100 EUR"


def test_oval_drawing_is_circle_or_ellipse():
    document = pymupdf.open()
    page = document.new_page(width=300, height=300)
    page.draw_oval(pymupdf.Rect(50, 50, 150, 150))

    annotations = extract_annotations(
        page, page_index=0, blocks=[], include_vector_drawings=True
    )
    document.close()

    assert annotations
    assert annotations[0].kind in {"circle", "ellipse"}
    assert annotations[0].confidence == pytest.approx(0.6)


def test_line_through_text_is_strike():
    document = pymupdf.open()
    page = document.new_page(width=300, height=300)
    page.draw_line((50, 85), (250, 85))
    block = _block(bbox=BBox(x0=40, y0=70, x1=260, y1=100))

    annotations = extract_annotations(
        page,
        page_index=0,
        blocks=[block],
        include_vector_drawings=True,
    )
    document.close()

    assert annotations
    assert annotations[0].kind == "strike"
    assert annotations[0].confidence == pytest.approx(0.5)
    assert annotations[0].anchor_block_id == "b1"


def test_arrow_drawing_is_detected():
    drawing = {
        "rect": FakeRect(0, 0, 100, 20),
        "items": [
            ("l", (0.0, 10.0), (100.0, 10.0)),
            ("l", (100.0, 10.0), (85.0, 2.0)),
            ("l", (100.0, 10.0), (85.0, 18.0)),
            ("l", (85.0, 18.0), (100.0, 10.0)),
        ],
    }
    page = FakePage(drawings=[drawing])

    annotations = extract_annotations(
        page, page_index=0, blocks=[], include_vector_drawings=True
    )

    assert annotations
    assert annotations[0].kind == "arrow"
    assert annotations[0].confidence == pytest.approx(0.5)


def test_unclassified_drawing_falls_back_to_ink():
    drawing = {
        "rect": FakeRect(0, 0, 40, 40),
        "items": [("c", (0.0, 20.0), (10.0, 0.0), (30.0, 40.0), (40.0, 20.0))],
    }
    page = FakePage(drawings=[drawing])

    annotations = extract_annotations(
        page, page_index=0, blocks=[], include_vector_drawings=True
    )

    assert annotations
    assert annotations[0].kind == "ink"
    assert annotations[0].confidence == pytest.approx(0.4)


def test_find_anchor_block_uses_overlap_and_threshold():
    blocks = [
        _block("b1", BBox(x0=0, y0=0, x1=100, y1=100)),
        _block("b2", BBox(x0=200, y0=200, x1=300, y1=300)),
    ]

    assert find_anchor_block(BBox(x0=10, y0=10, x1=50, y1=50), blocks) == "b1"
    assert find_anchor_block(
        BBox(x0=400, y0=400, x1=450, y1=450), blocks
    ) is None


def test_raw_text_target_is_truncated_to_200_chars():
    long_text = "A" * 300
    block = _block(text=long_text)
    page = FakePage([FakeAnnot("Square", FakeRect(50, 70, 120, 90))])

    annotations = extract_annotations(
        page, page_index=0, blocks=[block]
    )

    assert annotations[0].raw_text_target is not None
    assert len(annotations[0].raw_text_target) == 200


def _perception(annotations):
    profile = DocumentProfile(
        extension=".pdf",
        has_text_layer=True,
        word_count=10,
        has_tables=False,
        has_images=False,
        is_scanned=False,
        profile_type="native_pdf",
    )
    page = PagePerception(
        page_index=0,
        width=600,
        height=800,
        blocks=[],
        annotations=annotations,
    )
    return DocumentPerception(
        source_name="annotated.pdf",
        source_sha256="a" * 64,
        profile=profile,
        pages=[page],
        raw_text="Total TTC 100 EUR",
    )


def _annotation(kind, bbox, confidence, text="Total TTC 100 EUR"):
    return VisualAnnotation(
        kind=kind,
        page_index=0,
        bbox=bbox,
        confidence=confidence,
        anchor_block_id="b1",
        raw_text_target=text,
    )


def test_editing_targets_expose_suggested_actions():
    perception = _perception(
        [
            _annotation("strike", BBox(x0=0, y0=0, x1=10, y1=10), 0.9),
            _annotation("margin_note", BBox(x0=20, y0=0, x1=30, y1=10), 0.9),
            _annotation("circle", BBox(x0=40, y0=0, x1=50, y1=10), 0.9),
        ]
    )

    targets = EditingTaskAdapter.resolve_targets(perception)
    actions = {target.annotation.kind: target.suggested_action for target in targets}

    assert actions["strike"] == "delete"
    assert actions["margin_note"] == "modify"
    assert actions["circle"] == "extract"


def test_resolve_targets_sorts_and_dedupes_overlaps():
    perception = _perception(
        [
            _annotation("circle", BBox(x0=0, y0=0, x1=100, y1=100), 0.5),
            _annotation("circle", BBox(x0=0, y0=0, x1=100, y1=100), 0.9),
            _annotation("highlight", BBox(x0=200, y0=0, x1=300, y1=100), 0.8),
        ]
    )

    targets = EditingTaskAdapter.resolve_targets(perception)

    assert [target.annotation.confidence for target in targets] == [0.9, 0.8]


def test_locate_filters_targets_by_text():
    perception = _perception(
        [
            _annotation("highlight", BBox(x0=0, y0=0, x1=10, y1=10), 0.9),
            _annotation(
                "margin_note",
                BBox(x0=20, y0=0, x1=30, y1=10),
                0.8,
                text="Adresse fournisseur",
            ),
        ]
    )

    matches = EditingTaskAdapter.locate(perception, query="total")
    address = EditingTaskAdapter.locate(perception, query="adresse")

    assert len(matches) == 1
    assert len(address) == 1
    assert address[0].annotation.kind == "margin_note"
    assert len(EditingTaskAdapter.locate(perception)) == 2


def test_corrupt_page_returns_empty_list_and_note():
    class BrokenPage:
        def annots(self):
            raise RuntimeError("boom")

        def get_cdrawings(self):
            return []

    with pytest.warns(RuntimeWarning, match="ANNOTATION_FAILED:RUNTIMEERROR"):
        annotations = extract_annotations(
            BrokenPage(), page_index=0, blocks=[]
        )

    assert annotations == []
    assert annotations.notes == ["ANNOTATION_FAILED:RUNTIMEERROR"]

    with_note, notes = extract_annotations_with_notes(
        BrokenPage(), page_index=0, blocks=[]
    )
    assert with_note == []
    assert notes == ["ANNOTATION_FAILED:RUNTIMEERROR"]


def test_annotation_geometry_stays_in_raw_page_frame():
    page = FakePage(
        [FakeAnnot("Square", FakeRect(10, 20, 30, 40))],
        rotation=90,
    )

    annotations = extract_annotations(page, page_index=0, blocks=[])

    assert annotations[0].bbox == BBox(x0=10, y0=20, x1=30, y1=40)
    assert annotations[0].page_index == 0


def test_first_annot_fallback_when_annots_is_missing():
    class FirstAnnotPage:
        first_annot = FakeAnnot("Circle", FakeRect(5, 5, 25, 25))

        def get_cdrawings(self):
            return []

    annotations = extract_annotations(
        FirstAnnotPage(), page_index=0, blocks=[]
    )

    assert len(annotations) == 1
    assert annotations[0].kind == "circle"


def test_vector_drawings_are_lazy_by_default():
    drawing = {
        "rect": FakeRect(0, 0, 40, 40),
        "items": [("c", (0.0, 20.0), (10.0, 0.0), (30.0, 40.0), (40.0, 20.0))],
    }
    page = FakePage(drawings=[drawing])

    annotations = extract_annotations(page, page_index=0, blocks=[])

    assert annotations == []
