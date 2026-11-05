"""Level-3 text and table extractors for the Document & Vision Pipeline.

The extractors normalise every tool behind the same ``TextExtractor`` protocol:
they return ``Block`` instances whose geometry is expressed in PDF points in the
upright page frame. Heavy dependencies (pandas, docling, paddleocr) are imported
lazily and surface explicit ``ExtractionUnavailable`` codes.
"""

from __future__ import annotations

import importlib
import io
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol, runtime_checkable

import pymupdf

if hasattr(pymupdf, "TOOLS"):
    pymupdf.TOOLS.mupdf_display_errors(False)

from schemas.document_perception import BBox, Block, ExtractionPlan
from services.document_router import ALLOWED_TOOLS

TASK_EXTRACTOR_CONTRACT: dict[str, frozenset[str]] = {
    "text": frozenset({"pymupdf_text"}),
    "table": frozenset({"find_tables", "docling", "pandas"}),
    "ocr": frozenset({"paddleocr"}),
    "vision": frozenset({"vision_text"}),
    "handwriting": frozenset({"vision_text", "paddleocr"}),
    "annotation": frozenset({"pymupdf_text"}),
    "classification": frozenset({"pymupdf_text"}),
}
TASK_TO_EXTRACTOR = TASK_EXTRACTOR_CONTRACT


class ExtractionUnavailable(RuntimeError):
    """Explicit extractor failure carrying a stable error code."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


@runtime_checkable
class TextExtractor(Protocol):
    """Common contract implemented by every extraction adapter."""

    name: str

    def extract(
        self,
        source: str | Path,
        *,
        page_indices: list[int] | None = None,
        dpi: int = 170,
    ) -> list[Block]: ...


@dataclass(frozen=True)
class PlanOutcome:
    """Blocks produced by an extraction plan plus execution notes."""

    blocks: list[Block]
    notes: list[str]


def _validate_dpi(dpi: int) -> int:
    if isinstance(dpi, bool) or not isinstance(dpi, int) or dpi <= 0:
        raise ValueError("dpi must be a positive integer")
    return dpi


def _selected_indices(
    page_count: int, page_indices: list[int] | None
) -> list[int]:
    if page_indices is None:
        return list(range(page_count))
    indices = sorted(set(page_indices))
    if any(index < 0 or index >= page_count for index in indices):
        raise ValueError("page_indices contains an out-of-range page")
    return indices


def _rect_to_bbox(rectangle: Any) -> BBox:
    if hasattr(rectangle, "x0"):
        return BBox(
            x0=float(rectangle.x0),
            y0=float(rectangle.y0),
            x1=float(rectangle.x1),
            y1=float(rectangle.y1),
        )
    x0, y0, x1, y1 = rectangle
    return BBox(x0=float(x0), y0=float(y0), x1=float(x1), y1=float(y1))


def _points_to_bbox(points: list[tuple[float, float]]) -> BBox | None:
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return BBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys))


def _full_page_bbox(source: str | Path, page_index: int = 0) -> BBox:
    try:
        document = pymupdf.open(str(source))
    except Exception:
        return BBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0)
    try:
        if len(document) <= 0:
            return BBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0)
        index = min(max(page_index, 0), len(document) - 1)
        rectangle = document[index].rect
        return BBox(
            x0=0.0,
            y0=0.0,
            x1=float(rectangle.width),
            y1=float(rectangle.height),
        )
    except Exception:
        return BBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0)
    finally:
        document.close()


def _rectangular_grid(rows: Iterable[Iterable[Any]]) -> list[list[str]]:
    cleaned = [
        ["" if cell is None else str(cell) for cell in row] for row in rows
    ]
    width = max((len(row) for row in cleaned), default=0)
    if width == 0:
        return []
    return [row + [""] * (width - len(row)) for row in cleaned]


def _dataframe_grid(dataframe: Any) -> list[list[str]]:
    columns = [str(column) for column in dataframe.columns]
    rows = [
        ["" if value is None else str(value) for value in row]
        for row in dataframe.astype(str).values.tolist()
    ]
    return _rectangular_grid([columns, *rows])


class PyMuPDFTextExtractor:
    """Extract native text blocks with PyMuPDF."""

    name = "pymupdf_text"

    def extract(
        self,
        source: str | Path,
        *,
        page_indices: list[int] | None = None,
        dpi: int = 170,
    ) -> list[Block]:
        _validate_dpi(dpi)
        path = Path(source)
        try:
            document = pymupdf.open(str(path))
        except Exception as exc:
            raise ExtractionUnavailable(
                f"EXTRACTOR_FAILED:{type(exc).__name__}", str(exc)
            ) from exc
        try:
            blocks: list[Block] = []
            for page_index in _selected_indices(len(document), page_indices):
                page = document[page_index]
                for block_number, raw_block in enumerate(
                    page.get_text("blocks")
                ):
                    if len(raw_block) < 5:
                        continue
                    x0, y0, x1, y1, text = raw_block[:5]
                    block_type = raw_block[6] if len(raw_block) > 6 else 0
                    if block_type != 0 or not str(text).strip():
                        continue
                    blocks.append(
                        Block(
                            block_id=f"pymupdf_text-{page_index}-{block_number}",
                            kind="text",
                            page_index=page_index,
                            bbox=BBox(
                                x0=float(x0),
                                y0=float(y0),
                                x1=float(x1),
                                y1=float(y1),
                            ),
                            text=str(text).strip(),
                            confidence=1.0,
                            reading_order=len(blocks),
                        )
                    )
            return blocks
        finally:
            document.close()


class NativeTableExtractor:
    """Extract native PDF tables through ``page.find_tables()``."""

    name = "find_tables"

    def extract(
        self,
        source: str | Path,
        *,
        page_indices: list[int] | None = None,
        dpi: int = 170,
    ) -> list[Block]:
        _validate_dpi(dpi)
        path = Path(source)
        try:
            document = pymupdf.open(str(path))
        except Exception as exc:
            raise ExtractionUnavailable(
                f"EXTRACTOR_FAILED:{type(exc).__name__}", str(exc)
            ) from exc
        try:
            blocks: list[Block] = []
            for page_index in _selected_indices(len(document), page_indices):
                page = document[page_index]
                try:
                    finder = page.find_tables()
                except Exception as exc:
                    raise ExtractionUnavailable(
                        f"EXTRACTOR_FAILED:{type(exc).__name__}", str(exc)
                    ) from exc
                for table_number, table in enumerate(finder.tables):
                    try:
                        raw_grid = table.extract()
                    except Exception:
                        continue
                    grid = _rectangular_grid(raw_grid)
                    if not grid:
                        continue
                    blocks.append(
                        Block(
                            block_id=f"find_tables-{page_index}-{table_number}",
                            kind="table",
                            page_index=page_index,
                            bbox=_rect_to_bbox(table.bbox),
                            text="\n".join(
                                "\t".join(row) for row in grid
                            ),
                            table=grid,
                            confidence=1.0,
                            reading_order=len(blocks),
                        )
                    )
            return blocks
        finally:
            document.close()


def _import_pandas() -> Any:
    module = importlib.import_module("pandas")
    if callable(getattr(module, "read_csv", None)):
        return module
    import sys

    for name in list(sys.modules):
        if name == "pandas" or name.startswith("pandas."):
            sys.modules.pop(name, None)
    importlib.invalidate_caches()
    module = importlib.import_module("pandas")
    if not callable(getattr(module, "read_csv", None)):
        raise ImportError("pandas module is a stub without read_csv")
    return module


class PandasTableExtractor:
    """Read CSV and Excel sheets as rectangular tables."""

    name = "pandas"

    def extract(
        self,
        source: str | Path,
        *,
        page_indices: list[int] | None = None,
        dpi: int = 170,
    ) -> list[Block]:
        _validate_dpi(dpi)
        path = Path(source)
        if _selected_indices(1, page_indices) == []:
            return []
        try:
            pandas = _import_pandas()
        except ImportError as exc:
            raise ExtractionUnavailable(
                "PANDAS_UNAVAILABLE", "pandas is not installed"
            ) from exc
        try:
            suffix = path.suffix.lower()
            if suffix == ".csv":
                dataframe = pandas.read_csv(
                    path,
                    dtype=str,
                    keep_default_na=False,
                    sep=None,
                    engine="python",
                )
            elif suffix in {".xlsx", ".xls"}:
                dataframe = pandas.read_excel(path, dtype=str).fillna("")
            else:
                raise ValueError(f"unsupported spreadsheet suffix: {suffix}")
            grid = _dataframe_grid(dataframe)
        except Exception as exc:
            raise ExtractionUnavailable(
                f"EXTRACTOR_FAILED:{type(exc).__name__}", str(exc)
            ) from exc
        if not grid:
            return []
        return [
            Block(
                block_id="pandas-0-0",
                kind="table",
                page_index=0,
                bbox=BBox(
                    x0=0.0,
                    y0=0.0,
                    x1=float(len(grid[0])),
                    y1=float(len(grid)),
                ),
                text="\n".join("\t".join(row) for row in grid),
                table=grid,
                confidence=1.0,
                reading_order=0,
            )
        ]


class DoclingTableExtractor:
    """Extract structured tables through the optional Docling dependency."""

    name = "docling"

    def __init__(self, *, converter: Any | None = None) -> None:
        self.converter = converter

    def extract(
        self,
        source: str | Path,
        *,
        page_indices: list[int] | None = None,
        dpi: int = 170,
    ) -> list[Block]:
        _validate_dpi(dpi)
        path = Path(source)
        if self.converter is None:
            try:
                module = importlib.import_module("docling")
            except ImportError as exc:
                raise ExtractionUnavailable(
                    "DOCLING_UNAVAILABLE", "docling is not installed"
                ) from exc
            try:
                converter_class = getattr(
                    module, "DocumentConverter", None
                )
                if converter_class is None:
                    converter_module = importlib.import_module(
                        "docling.document_converter"
                    )
                    converter_class = converter_module.DocumentConverter
                converter = converter_class()
            except Exception as exc:
                raise ExtractionUnavailable(
                    "DOCLING_UNAVAILABLE", str(exc)
                ) from exc
        else:
            converter = self.converter
        try:
            result = converter.convert(str(path))
            document = getattr(result, "document", result)
            tables = list(getattr(document, "tables", []) or [])
            blocks: list[Block] = []
            for table_number, table in enumerate(tables):
                grid = self._table_grid(table)
                if not grid:
                    continue
                page_index, bbox = self._table_geometry(table)
                blocks.append(
                    Block(
                        block_id=f"docling-{page_index}-{table_number}",
                        kind="table",
                        page_index=page_index,
                        bbox=bbox,
                        text="\n".join("\t".join(row) for row in grid),
                        table=grid,
                        confidence=1.0,
                        reading_order=len(blocks),
                    )
                )
            if page_indices is None:
                return blocks
            selected = set(page_indices)
            return [block for block in blocks if block.page_index in selected]
        except Exception as exc:
            raise ExtractionUnavailable(
                f"EXTRACTOR_FAILED:{type(exc).__name__}", str(exc)
            ) from exc

    def _table_grid(self, table: Any) -> list[list[str]]:
        exporter = getattr(table, "export_to_dataframe", None)
        if callable(exporter):
            try:
                return _dataframe_grid(exporter())
            except Exception:
                pass
        data = getattr(table, "data", None)
        grid = getattr(data, "grid", None)
        if grid is not None:
            rows = []
            for row in grid:
                cells = []
                for cell in row:
                    text = getattr(cell, "text", cell)
                    cells.append("" if text is None else str(text))
                rows.append(cells)
            return _rectangular_grid(rows)
        if isinstance(data, list):
            return _rectangular_grid(data)
        if isinstance(table, list):
            return _rectangular_grid(table)
        return []

    def _table_geometry(self, table: Any) -> tuple[int, BBox]:
        page_index = 0
        bbox = BBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0)
        provenance = getattr(table, "prov", None)
        if provenance:
            first = provenance[0]
            page_number = getattr(first, "page_no", None)
            if isinstance(page_number, int) and page_number > 0:
                page_index = page_number - 1
            raw_bbox = getattr(first, "bbox", None)
            if raw_bbox is not None:
                try:
                    bbox = _rect_to_bbox(raw_bbox)
                except Exception:
                    pass
        raw_bbox = getattr(table, "bbox", None)
        if raw_bbox is not None:
            try:
                bbox = _rect_to_bbox(raw_bbox)
            except Exception:
                pass
        return page_index, bbox


class PaddleOcrTextExtractor:
    """OCR pages with the existing PaddleOCR adapter."""

    name = "paddleocr"

    def __init__(
        self,
        *,
        engine: Any | None = None,
        lang: str = "fr",
        dpi: int = 170,
    ) -> None:
        self.engine = engine
        self.lang = lang
        self.dpi = _validate_dpi(dpi)

    def _active_engine(self) -> Any:
        if self.engine is None:
            from services.local_invoice_extractor import PaddleOCREngine

            self.engine = PaddleOCREngine(lang=self.lang, dpi=self.dpi)
        return self.engine

    def extract(
        self,
        source: str | Path,
        *,
        page_indices: list[int] | None = None,
        dpi: int = 170,
    ) -> list[Block]:
        _validate_dpi(dpi)
        path = Path(source)
        try:
            engine = self._active_engine()
            blocks = self._extract_blocks(engine, path, page_indices, dpi)
            return blocks
        except ExtractionUnavailable:
            raise
        except Exception as exc:
            text = str(exc)
            if "PADDLEOCR_UNAVAILABLE" in text or isinstance(exc, ImportError):
                raise ExtractionUnavailable(
                    "PADDLEOCR_UNAVAILABLE", text
                ) from exc
            raise ExtractionUnavailable(
                f"EXTRACTOR_FAILED:{type(exc).__name__}", text
            ) from exc

    def _extract_blocks(
        self,
        engine: Any,
        path: Path,
        page_indices: list[int] | None,
        dpi: int,
    ) -> list[Block]:
        custom = getattr(engine, "extract_blocks", None)
        if callable(custom):
            raw_blocks = custom(
                path, page_indices=page_indices, dpi=dpi
            )
            return list(raw_blocks)

        if hasattr(engine, "_load") and hasattr(engine, "_image_arrays"):
            return self._extract_paddle_engine(
                engine, path, page_indices, dpi
            )

        text = engine(path)
        if not str(text).strip():
            return []
        bbox = _full_page_bbox(path, 0)
        return [
            Block(
                block_id="paddleocr-0-0",
                kind="text",
                page_index=0,
                bbox=bbox,
                text=str(text).strip(),
                confidence=0.5,
                reading_order=0,
            )
        ]

    def _extract_paddle_engine(
        self,
        engine: Any,
        path: Path,
        page_indices: list[int] | None,
        dpi: int,
    ) -> list[Block]:
        engine_dpi = _validate_dpi(
            int(getattr(engine, "dpi", self.dpi) or dpi)
        )
        real_paddle = False
        try:
            from services.local_invoice_extractor import PaddleOCREngine

            real_paddle = isinstance(engine, PaddleOCREngine)
        except ImportError:
            real_paddle = False
        loaded = engine._load()
        if real_paddle and path.suffix.lower() == ".pdf":
            indices, page_arrays = _cached_pdf_arrays(
                path, page_indices, engine_dpi
            )
            arrays = page_arrays
            array_indices = indices
        else:
            arrays = engine._image_arrays(path)
            array_indices = _selected_indices(len(arrays), page_indices)
        blocks: list[Block] = []
        for page_index, array in zip(array_indices, arrays):
            try:
                if hasattr(loaded, "predict"):
                    outputs = loaded.predict(array)
                else:
                    outputs = loaded.ocr(array, cls=True)
            except TypeError:
                outputs = loaded.ocr(array, cls=True)
            parsed = _parse_paddle_outputs(
                outputs,
                page_index=page_index,
                dpi=engine_dpi,
                start_order=len(blocks),
                block_prefix="paddleocr",
            )
            if parsed:
                blocks.extend(parsed)
                continue
            text = _paddle_text(outputs)
            if text:
                blocks.append(
                    Block(
                        block_id=f"paddleocr-{page_index}-fallback",
                        kind="text",
                        page_index=page_index,
                        bbox=_full_page_bbox(path, page_index),
                        text=text,
                        confidence=0.5,
                        reading_order=len(blocks),
                    )
                )
        return blocks


class VisionTextExtractor:
    """Transcribe a document through an injected vision callable."""

    name = "vision_text"

    def __init__(
        self, transcriber: Callable[[Path], str] | None = None
    ) -> None:
        self.transcriber = transcriber

    def extract(
        self,
        source: str | Path,
        *,
        page_indices: list[int] | None = None,
        dpi: int = 170,
    ) -> list[Block]:
        _validate_dpi(dpi)
        if self.transcriber is None:
            raise ExtractionUnavailable(
                "VISION_TRANSCRIBER_MISSING",
                "VisionTextExtractor requires a transcriber",
            )
        path = Path(source)
        try:
            text = self.transcriber(path)
        except Exception as exc:
            raise ExtractionUnavailable(
                f"EXTRACTOR_FAILED:{type(exc).__name__}", str(exc)
            ) from exc
        if not str(text).strip():
            return []
        page_index = 0
        if page_indices:
            page_index = page_indices[0]
        return [
            Block(
                block_id=f"vision_text-{page_index}-0",
                kind="text",
                page_index=page_index,
                bbox=_full_page_bbox(path, page_index),
                text=str(text).strip(),
                confidence=0.5,
                reading_order=0,
            )
        ]


def _cached_pdf_arrays(
    path: Path,
    page_indices: list[int] | None,
    dpi: int,
) -> tuple[list[int], list[Any]]:
    import numpy
    from PIL import Image

    from services.document_geometry import (
        rasterize_page_cached,
        source_sha256,
    )

    sha = source_sha256(path)
    with pymupdf.open(str(path)) as document:
        indices = _selected_indices(len(document), page_indices)
        arrays = []
        for page_index in indices:
            png = rasterize_page_cached(
                document[page_index],
                dpi=dpi,
                source_sha256=sha,
                page_index=page_index,
            )
            with Image.open(io.BytesIO(png)) as image:
                arrays.append(
                    numpy.array(image.convert("RGB"))
                )
    return indices, arrays


def _coerce_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        return value
    for attribute in ("to_dict", "json"):
        converter = getattr(value, attribute, None)
        if callable(converter):
            try:
                converted = converter()
            except Exception:
                continue
            if isinstance(converted, dict):
                return converted
    return value


def _paddle_text(outputs: Any) -> str:
    try:
        from services.local_invoice_extractor import _coerce_text

        return _coerce_text(outputs)
    except Exception:
        return ""


def _parse_paddle_outputs(
    outputs: Any,
    *,
    page_index: int,
    dpi: int,
    start_order: int,
    block_prefix: str,
) -> list[Block]:
    blocks: list[Block] = []
    if outputs is None:
        return blocks
    if hasattr(outputs, "tolist") and not isinstance(outputs, (dict, str)):
        outputs = outputs.tolist()
    if not isinstance(outputs, (list, tuple)):
        outputs = [outputs]
    for item in outputs:
        if hasattr(item, "tolist") and not isinstance(item, (dict, str)):
            item = item.tolist()
        mapping = _coerce_mapping(item)
        if isinstance(mapping, dict) and "rec_texts" in mapping:
            texts = mapping.get("rec_texts") or []
            scores = mapping.get("rec_scores") or []
            polygons = (
                mapping.get("rec_polys")
                or mapping.get("dt_polys")
                or mapping.get("points")
                or []
            )
            for index, text in enumerate(texts):
                if not str(text).strip():
                    continue
                score = float(scores[index]) if index < len(scores) else 1.0
                points = _normalise_polygon(
                    polygons[index] if index < len(polygons) else None
                )
                bbox = _points_to_bbox(points)
                if bbox is None:
                    continue
                blocks.append(
                    Block(
                        block_id=(
                            f"{block_prefix}-{page_index}-{len(blocks)}"
                        ),
                        kind="text",
                        page_index=page_index,
                        bbox=_scale_bbox(bbox, dpi),
                        text=str(text).strip(),
                        confidence=min(max(score, 0.0), 1.0),
                        reading_order=start_order + len(blocks),
                    )
                )
            if blocks:
                continue
        line = _parse_legacy_paddle_line(item)
        if line is None:
            continue
        text, score, points = line
        bbox = _points_to_bbox(points)
        if bbox is None:
            continue
        blocks.append(
            Block(
                block_id=f"{block_prefix}-{page_index}-{len(blocks)}",
                kind="text",
                page_index=page_index,
                bbox=_scale_bbox(bbox, dpi),
                text=text,
                confidence=min(max(score, 0.0), 1.0),
                reading_order=start_order + len(blocks),
            )
        )
    return blocks


def _parse_legacy_paddle_line(
    item: Any,
) -> tuple[str, float, list[tuple[float, float]]] | None:
    if not isinstance(item, (list, tuple)) or len(item) < 2:
        return None
    polygon = item[0]
    recognition = item[1]
    if isinstance(recognition, (list, tuple)) and recognition:
        text = recognition[0]
        score = recognition[1] if len(recognition) > 1 else 1.0
    elif isinstance(recognition, str):
        text = recognition
        score = 1.0
    else:
        return None
    if not isinstance(text, str) or not text.strip():
        return None
    points = _normalise_polygon(polygon)
    if len(points) < 2:
        return None
    return text.strip(), float(score), points


def _normalise_polygon(polygon: Any) -> list[tuple[float, float]]:
    if polygon is None:
        return []
    if hasattr(polygon, "tolist"):
        polygon = polygon.tolist()
    points: list[tuple[float, float]] = []
    if isinstance(polygon, (list, tuple)):
        if (
            len(polygon) == 4
            and all(isinstance(value, (int, float)) for value in polygon)
        ):
            x0, y0, x1, y1 = polygon
            return [
                (float(x0), float(y0)),
                (float(x1), float(y0)),
                (float(x1), float(y1)),
                (float(x0), float(y1)),
            ]
        for point in polygon:
            if hasattr(point, "tolist"):
                point = point.tolist()
            if (
                isinstance(point, (list, tuple))
                and len(point) >= 2
                and all(
                    isinstance(axis, (int, float)) for axis in point[:2]
                )
            ):
                points.append((float(point[0]), float(point[1])))
    return points


def _scale_bbox(bbox: BBox, dpi: int) -> BBox:
    scale = 72.0 / float(dpi)
    return BBox(
        x0=bbox.x0 * scale,
        y0=bbox.y0 * scale,
        x1=bbox.x1 * scale,
        y1=bbox.y1 * scale,
    )


class ExtractorRegistry:
    """Registry mapping router tool names to extractor implementations."""

    def __init__(self, extractors: Iterable[TextExtractor] | None = None) -> None:
        self._extractors: dict[str, TextExtractor] = {}
        for extractor in extractors or ():
            self.register(extractor)

    def register(self, extractor: TextExtractor) -> None:
        name = getattr(extractor, "name", "")
        if name not in ALLOWED_TOOLS:
            raise ExtractionUnavailable("UNKNOWN_TOOL", str(name))
        self._extractors[name] = extractor

    def get(self, name: str) -> TextExtractor:
        extractor = self._extractors.get(name)
        if extractor is None:
            raise ExtractionUnavailable("UNKNOWN_TOOL", name)
        return extractor

    def run_plan(
        self,
        plan: ExtractionPlan,
        source: str | Path,
        *,
        dpi: int = 170,
    ) -> PlanOutcome:
        """Execute a plan without ever raising."""

        blocks: list[Block] = []
        notes: list[str] = []
        try:
            tasks = list(plan.tasks)
        except Exception as exc:
            return PlanOutcome(
                blocks=[], notes=[f"EXTRACTOR_FAILED:{type(exc).__name__}"]
            )
        for task in tasks:
            try:
                allowed = TASK_EXTRACTOR_CONTRACT.get(task.kind)
                if task.tool not in ALLOWED_TOOLS:
                    notes.append(f"UNKNOWN_TOOL:{task.tool}")
                    continue
                if allowed is None or task.tool not in allowed:
                    notes.append(
                        f"CONTRACT_VIOLATION:{task.kind}:{task.tool}"
                    )
                    continue
                extractor = self.get(task.tool)
                page_indices = task.page_indices or None
                extracted = extractor.extract(
                    source,
                    page_indices=page_indices,
                    dpi=dpi,
                )
                blocks.extend(extracted)
                notes.append(f"{task.tool}:{len(extracted)}_blocks")
            except ExtractionUnavailable as exc:
                notes.append(_note(exc))
            except Exception as exc:
                notes.append(f"EXTRACTOR_FAILED:{type(exc).__name__}")
        return PlanOutcome(blocks=blocks, notes=notes)


def _note(error: ExtractionUnavailable) -> str:
    if error.detail:
        return f"{error.code}:{error.detail}"
    return error.code


def build_default_extractors() -> list[TextExtractor]:
    """Return one default instance of every phase-3 extractor."""

    return [
        PyMuPDFTextExtractor(),
        NativeTableExtractor(),
        PandasTableExtractor(),
        DoclingTableExtractor(),
        PaddleOcrTextExtractor(),
        VisionTextExtractor(),
    ]


def _ocr_image_text(engine: Any, image: Any) -> str:
    custom = getattr(engine, "text_from_image", None)
    if callable(custom):
        return str(custom(image))

    loaded = engine._load() if hasattr(engine, "_load") else engine
    try:
        import numpy

        array = numpy.asarray(image.convert("RGB"))
        try:
            if hasattr(loaded, "predict"):
                outputs = loaded.predict(array)
            else:
                outputs = loaded.ocr(array, cls=True)
        except TypeError:
            outputs = loaded.ocr(array, cls=True)
        return _paddle_text(outputs)
    except Exception:
        if not callable(engine):
            raise
    suffix = ".png"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=suffix, delete=False
        ) as stream:
            temporary_name = stream.name
            image.save(temporary_name, format="PNG")
        return str(engine(Path(temporary_name)))
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def build_paddle_orientation_scorer(
    engine: Any | None = None, *, dpi: int = 150
) -> Callable[[Any, int], float]:
    """Build the ``(image, clockwise_angle) -> float`` router scorer.

    ``DocumentRouter`` already passes the candidate raster rotated clockwise by
    ``clockwise_angle``; the scorer therefore measures OCR text length on the
    supplied raster. The angle remains part of the contract so custom scorers
    can rotate explicitly when used outside the router.
    """

    _validate_dpi(dpi)
    holder: dict[str, Any] = {"engine": engine}

    def scorer(image: Any, clockwise_angle: int) -> float:
        try:
            active = holder["engine"]
            if active is None:
                from services.local_invoice_extractor import PaddleOCREngine

                active = PaddleOCREngine(dpi=dpi)
                holder["engine"] = active
            text = _ocr_image_text(active, image)
            return float(len(text.strip()))
        except Exception:
            return 0.0

    return scorer
