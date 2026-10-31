"""Contract tests for level-3 document extractors."""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pymupdf
import pytest
from PIL import Image

import services.document_extractors as extractors
from schemas.document_perception import (
    Block,
    BBox,
    ExtractionPlan,
    RoutingDecision,
    TaskKind,
    TaskSpec,
)
from services.document_extractors import (
    ALLOWED_TOOLS,
    DoclingTableExtractor,
    ExtractionUnavailable,
    ExtractorRegistry,
    NativeTableExtractor,
    PaddleOcrTextExtractor,
    PandasTableExtractor,
    PlanOutcome,
    PyMuPDFTextExtractor,
    TASK_EXTRACTOR_CONTRACT,
    VisionTextExtractor,
    build_default_extractors,
    build_paddle_orientation_scorer,
)
from services.document_geometry import score_orientation


def _text_pdf(tmp_path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page(width=300, height=300)
    page.insert_text((50, 60), "Hello native text", fontsize=12)
    path = tmp_path / "text.pdf"
    document.save(path)
    document.close()
    return path


def _two_page_pdf(tmp_path: Path) -> Path:
    document = pymupdf.open()
    first = document.new_page(width=300, height=300)
    first.insert_text((50, 60), "First page", fontsize=12)
    second = document.new_page(width=300, height=300)
    second.insert_text((50, 60), "Second page", fontsize=12)
    path = tmp_path / "two-pages.pdf"
    document.save(path)
    document.close()
    return path


def _table_pdf(tmp_path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page(width=300, height=300)
    for row in range(3):
        page.draw_line((50, 100 + row * 40), (250, 100 + row * 40))
    for column in range(3):
        page.draw_line((50 + column * 100, 100), (50 + column * 100, 180))
    for row in range(2):
        for column in range(2):
            page.insert_text(
                (55 + column * 100, 125 + row * 40),
                f"v{row}{column}",
                fontsize=9,
            )
    path = tmp_path / "table.pdf"
    document.save(path)
    document.close()
    return path


def _ledger_csv(tmp_path: Path) -> Path:
    path = tmp_path / "ledger.csv"
    path.write_text(
        "date;libelle;montant\n2026-01-01;Achat;120.00\n",
        encoding="utf-8",
    )
    return path


def test_pymupdf_text_extractor_returns_text_blocks(tmp_path):
    blocks = PyMuPDFTextExtractor().extract(_text_pdf(tmp_path))

    assert blocks
    assert all(block.kind == "text" for block in blocks)
    assert all(block.page_index == 0 for block in blocks)
    assert "Hello native text" in " ".join(block.text for block in blocks)
    assert blocks[0].bbox.x1 > blocks[0].bbox.x0
    assert blocks[0].bbox.y1 > blocks[0].bbox.y0


def test_pymupdf_text_extractor_filters_page_indices(tmp_path):
    blocks = PyMuPDFTextExtractor().extract(
        _two_page_pdf(tmp_path), page_indices=[1]
    )

    assert blocks
    assert {block.page_index for block in blocks} == {1}
    assert all("Second page" in block.text for block in blocks)


def test_find_tables_extractor_returns_rectangular_grid(tmp_path):
    blocks = NativeTableExtractor().extract(_table_pdf(tmp_path))

    assert blocks
    table = blocks[0]
    assert table.kind == "table"
    assert table.table is not None
    assert {len(row) for row in table.table} == {2}
    assert table.table == [["v00", "v01"], ["v10", "v11"]]
    assert table.bbox.x1 > table.bbox.x0


def test_pandas_extractor_reads_csv(tmp_path):
    blocks = PandasTableExtractor().extract(_ledger_csv(tmp_path))

    assert len(blocks) == 1
    table = blocks[0]
    assert table.kind == "table"
    assert table.table is not None
    assert table.table[0] == ["date", "libelle", "montant"]
    assert table.table[1][0] == "2026-01-01"
    assert {len(row) for row in table.table} == {3}


def test_pandas_extractor_reads_xlsx(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["date", "libelle", "montant"])
    sheet.append(["2026-01-01", "Achat", 120.0])
    path = tmp_path / "ledger.xlsx"
    workbook.save(path)

    blocks = PandasTableExtractor().extract(path)

    assert blocks
    assert blocks[0].table is not None
    assert blocks[0].table[0] == ["date", "libelle", "montant"]
    assert {len(row) for row in blocks[0].table} == {3}


def test_pandas_unavailable_code(monkeypatch, tmp_path):
    original_import = extractors.importlib.import_module

    def unavailable(name):
        if name == "pandas":
            raise ImportError("pandas unavailable")
        return original_import(name)

    monkeypatch.setattr(extractors.importlib, "import_module", unavailable)
    with pytest.raises(ExtractionUnavailable) as error:
        PandasTableExtractor().extract(_ledger_csv(tmp_path))
    assert error.value.code == "PANDAS_UNAVAILABLE"


def test_docling_unavailable_code(monkeypatch, tmp_path):
    original_import = extractors.importlib.import_module

    def unavailable(name):
        if name == "docling" or name.startswith("docling."):
            raise ImportError("docling unavailable")
        return original_import(name)

    monkeypatch.setattr(extractors.importlib, "import_module", unavailable)
    with pytest.raises(ExtractionUnavailable) as error:
        DoclingTableExtractor().extract(_text_pdf(tmp_path))
    assert error.value.code == "DOCLING_UNAVAILABLE"


def test_docling_uses_injected_converter(tmp_path):
    class Cell:
        def __init__(self, text):
            self.text = text

    class Data:
        grid = [[Cell("A"), Cell("B")], [Cell("1"), Cell("2")]]

    class Provenance:
        page_no = 1
        bbox = (10.0, 20.0, 110.0, 80.0)

    class Table:
        data = Data()
        prov = [Provenance()]

    class Document:
        tables = [Table()]

    class Result:
        document = Document()

    class Converter:
        def convert(self, source):
            return Result()

    blocks = DoclingTableExtractor(converter=Converter()).extract(
        _text_pdf(tmp_path)
    )

    assert blocks
    assert blocks[0].kind == "table"
    assert blocks[0].table == [["A", "B"], ["1", "2"]]
    assert blocks[0].bbox == BBox(x0=10.0, y0=20.0, x1=110.0, y1=80.0)


def test_paddleocr_unavailable_code(tmp_path):
    class UnavailableEngine:
        dpi = 100

        def _image_arrays(self, source):
            return [object()]

        def _load(self):
            raise RuntimeError("PADDLEOCR_UNAVAILABLE")

    with pytest.raises(ExtractionUnavailable) as error:
        PaddleOcrTextExtractor(engine=UnavailableEngine()).extract(
            _text_pdf(tmp_path)
        )
    assert error.value.code == "PADDLEOCR_UNAVAILABLE"


def test_paddleocr_converts_pixels_to_points(tmp_path):
    class Loaded:
        def ocr(self, array, cls=True):
            return [
                [
                    [[0, 0], [100, 0], [100, 50], [0, 50]],
                    ("Hello OCR", 0.9),
                ]
            ]

    class Engine:
        dpi = 100

        def _image_arrays(self, source):
            return [object()]

        def _load(self):
            return Loaded()

    blocks = PaddleOcrTextExtractor(engine=Engine()).extract(
        _text_pdf(tmp_path)
    )

    assert len(blocks) == 1
    assert blocks[0].text == "Hello OCR"
    assert blocks[0].bbox == BBox(x0=0.0, y0=0.0, x1=72.0, y1=36.0)
    assert blocks[0].confidence == pytest.approx(0.9)


def test_vision_text_extractor_uses_transcriber(tmp_path):
    received = []
    source = _text_pdf(tmp_path)

    def transcriber(path):
        received.append(path)
        return "Vision transcription"

    blocks = VisionTextExtractor(transcriber).extract(source)

    assert received and received[0] == source
    assert blocks
    assert blocks[0].kind == "text"
    assert blocks[0].text == "Vision transcription"


def test_vision_transcriber_missing(tmp_path):
    with pytest.raises(ExtractionUnavailable) as error:
        VisionTextExtractor().extract(_text_pdf(tmp_path))
    assert error.value.code == "VISION_TRANSCRIBER_MISSING"


def test_registry_run_plan_never_raises(tmp_path):
    source = _text_pdf(tmp_path)
    plan = ExtractionPlan(
        routing=RoutingDecision(route="native_text"),
        tasks=[
            TaskSpec(task_id="t1", kind="text", tool="pymupdf_text", page_indices=[0]),
            TaskSpec(task_id="t2", kind="text", tool="paddleocr"),
            TaskSpec(task_id="t3", kind="table", tool="docling"),
            TaskSpec(task_id="t4", kind="ocr", tool="unknown_tool"),
            TaskSpec(task_id="t5", kind="vision", tool="vision_text"),
        ],
    )
    registry = ExtractorRegistry(build_default_extractors())

    outcome = registry.run_plan(plan, source)

    assert isinstance(outcome, PlanOutcome)
    assert outcome.blocks
    assert any(note.startswith("pymupdf_text:") for note in outcome.notes)
    assert any("CONTRACT_VIOLATION" in note for note in outcome.notes)
    assert any("DOCLING_UNAVAILABLE" in note for note in outcome.notes)
    assert any("UNKNOWN_TOOL" in note for note in outcome.notes)
    assert any("VISION_TRANSCRIBER_MISSING" in note for note in outcome.notes)


def test_registry_rejects_unknown_extractor_name():
    class Unknown:
        name = "not_a_tool"

        def extract(self, source, *, page_indices=None, dpi=170):
            return []

    with pytest.raises(ExtractionUnavailable) as error:
        ExtractorRegistry().register(Unknown())
    assert error.value.code == "UNKNOWN_TOOL"


def test_all_extractor_names_are_router_allowed_tools():
    names = {extractor.name for extractor in build_default_extractors()}
    assert names == ALLOWED_TOOLS


def test_task_contract_covers_all_kinds():
    assert set(TASK_EXTRACTOR_CONTRACT) == set(get_args(TaskKind))
    for tools in TASK_EXTRACTOR_CONTRACT.values():
        assert tools <= ALLOWED_TOOLS


def test_paddle_orientation_scorer_measures_ocr_on_candidate():
    class Engine:
        def text_from_image(self, image):
            return "A" * 20 if image.getpixel((0, 0))[:3] == (255, 0, 0) else "B"

    scorer = build_paddle_orientation_scorer(Engine())
    image = Image.new("RGB", (40, 20), "white")
    image.putpixel((0, 0), (255, 0, 0))

    assert scorer(image, 0) == 20.0
    score = score_orientation(image, scorer=scorer)
    assert score.angle == 0
    assert score.scores[0] == 20.0

    rotated = Image.new("RGB", (40, 20), "white")
    rotated.putpixel((39, 0), (255, 0, 0))
    rotated_score = score_orientation(rotated, scorer=scorer)
    assert rotated_score.angle == 270
    assert rotated_score.scores[270] == 20.0


def test_paddle_orientation_scorer_returns_zero_on_engine_failure():
    class Engine:
        def text_from_image(self, image):
            raise RuntimeError("OCR failed")

    scorer = build_paddle_orientation_scorer(Engine())
    assert scorer(Image.new("RGB", (10, 10)), 90) == 0.0
