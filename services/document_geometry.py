"""Document geometry utilities for rasterisation, orientation and deskew.

Heavy dependencies (OpenCV, NumPy) are imported lazily so that importing this
module stays cheap. ``normalise_geometry`` is the guarded entry point: every
internal failure is converted into a ``GeometryReport`` carrying a
``GEOMETRY_FAILED:<code>`` note instead of leaking an exception.
"""

from __future__ import annotations

import hashlib
import importlib
import io
import math
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, Mapping

from schemas.document_perception import BBox, PagePerception

if TYPE_CHECKING:
    from benchmarks.orientation_benchmark import OrientationCandidate

ANGLES = (0, 90, 180, 270)
DEFAULT_DPI = 170
DESKEW_MIN_ANGLE = 0.1
FAST_PATH_MIN_WORDS = 50
FAST_PATH_HORIZONTAL_MIN = 0.9
FAST_PATH_MAX_IMAGE_COVERAGE = 0.30
RASTER_CACHE_SIZE = 64

_RASTER_CACHE: OrderedDict[tuple[str, int, int], bytes] = OrderedDict()


class GeometryError(RuntimeError):
    """Explicit geometry failure carrying a stable error code."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class OrientationScore:
    """Best orientation and the score associated with each candidate."""

    angle: int
    confidence: float
    method: str
    scores: Mapping[int, float] = field(
        default_factory=dict, compare=False, repr=False
    )

    @property
    def score(self) -> float:
        return self.scores.get(self.angle, 0.0)


@dataclass(frozen=True)
class AutoRotateResult:
    """Rotation to apply so that page content becomes upright."""

    angle: int
    confidence: float
    method: str


@dataclass(frozen=True)
class GeometryReport:
    """Summary of the geometry normalisation performed on a source."""

    source_name: str = ""
    page_count: int = 0
    page_index: int = 0
    width: float = 0.0
    height: float = 0.0
    rotation_degrees: int = 0
    rotation_confidence: float = 0.0
    rotation_method: str = "none"
    raster_dpi: int = DEFAULT_DPI
    raster_bytes: int = 0
    deskew_applied: bool = False
    fallback: bool = False
    notes: list[str] = field(default_factory=list)
    pages: list[dict[str, Any]] = field(default_factory=list)
    error_code: str | None = None


def rasterize_page(page: Any, *, dpi: int = DEFAULT_DPI) -> bytes:
    """Render a PyMuPDF page to in-memory PNG bytes."""

    if isinstance(dpi, bool) or not isinstance(dpi, int) or dpi <= 0:
        raise ValueError("dpi must be a positive integer")
    if not hasattr(page, "get_pixmap"):
        raise TypeError("page must be a PyMuPDF Page")
    return page.get_pixmap(dpi=dpi).tobytes("png")


def reset_raster_cache() -> None:
    """Clear the in-memory raster page cache."""

    _RASTER_CACHE.clear()


def source_sha256(path: str | Path) -> str:
    """Return the SHA-256 of a source file (empty when unreadable)."""

    try:
        source = Path(path)
        if not source.is_file():
            return ""
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except Exception:
        return ""


def rasterize_page_cached(
    page: Any,
    *,
    dpi: int = DEFAULT_DPI,
    source_sha256: str = "",
    page_index: int = 0,
) -> bytes:
    """Render a page once per (source, page, dpi) and reuse the bytes."""

    if not source_sha256 or int(getattr(page, "rotation", 0)) % 360 != 0:
        return rasterize_page(page, dpi=dpi)
    key = (source_sha256, page_index, dpi)
    cached = _RASTER_CACHE.get(key)
    if cached is not None:
        _RASTER_CACHE.move_to_end(key)
        return cached
    png = rasterize_page(page, dpi=dpi)
    _RASTER_CACHE[key] = png
    _RASTER_CACHE.move_to_end(key)
    while len(_RASTER_CACHE) > RASTER_CACHE_SIZE:
        _RASTER_CACHE.popitem(last=False)
    return png


def rotation_candidates(image: Any) -> tuple["OrientationCandidate", ...]:
    """Return the four in-memory rotations used by the orientation benchmark."""

    from benchmarks.orientation_benchmark import generate_rotation_candidates

    return generate_rotation_candidates(image)


def _rotate_vector_clockwise(dx: float, dy: float, angle: int) -> tuple[float, float]:
    radians = math.radians(angle)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    return dx * cosine - dy * sine, dx * sine + dy * cosine


def _text_direction_scores(page: Any) -> tuple[dict[int, float], float]:
    scores = {angle: 0.0 for angle in ANGLES}
    total = 0.0
    try:
        data = page.get_text("dict")
    except Exception:
        return scores, total
    for block in data.get("blocks", ()):
        for line in block.get("lines", ()):
            spans = line.get("spans", ())
            text = "".join(str(span.get("text", "")) for span in spans).strip()
            if not text:
                continue
            length = float(len(text))
            total += length
            direction = line.get("dir", (1.0, 0.0))
            dx, dy = float(direction[0]), float(direction[1])
            for angle in ANGLES:
                rotated_x, rotated_y = _rotate_vector_clockwise(dx, dy, angle)
                if rotated_x > 0.9 and abs(rotated_y) < 0.2:
                    scores[angle] += length
    return scores, total


def _select_orientation(
    scores: Mapping[int, float], total: float
) -> tuple[int, float]:
    best_angle = ANGLES[0]
    best_score = float(scores.get(best_angle, 0.0))
    for angle in ANGLES[1:]:
        score = float(scores.get(angle, 0.0))
        if score > best_score:
            best_angle = angle
            best_score = score
    if total <= 0 or best_score <= 0:
        return ANGLES[0], 0.0
    return best_angle, min(1.0, best_score / total)


def _is_page(source: Any) -> bool:
    return hasattr(source, "get_text") and hasattr(source, "get_pixmap")


def _is_pil_image(source: Any) -> bool:
    from PIL import Image

    return isinstance(source, Image.Image)


def _open_source_page(source: Any) -> tuple[Any | None, Any | None]:
    if _is_page(source):
        return source, None
    if not isinstance(source, (str, Path, bytes, bytearray)):
        return None, None
    pymupdf = importlib.import_module("pymupdf")
    if isinstance(source, (bytes, bytearray)):
        document = pymupdf.open(stream=bytes(source), filetype="pdf")
    else:
        document = pymupdf.open(str(source))
    if len(document) == 0:
        document.close()
        raise GeometryError("EMPTY_DOCUMENT")
    return document[0], document


def _native_text_score(source: Any) -> tuple[dict[int, float], float, str]:
    zeros = {angle: 0.0 for angle in ANGLES}
    page, document = _open_source_page(source)
    if page is None:
        return zeros, 0.0, "native_text_unavailable"
    try:
        scores, total = _text_direction_scores(page)
    finally:
        if document is not None:
            document.close()
    method = "native_text" if total > 0 else "native_text_unavailable"
    return scores, total, method


def _pil_from_source(source: Any) -> Any:
    from PIL import Image

    if isinstance(source, Image.Image):
        return source.copy()
    if isinstance(source, (bytes, bytearray)):
        try:
            with Image.open(io.BytesIO(bytes(source))) as image:
                return image.convert("RGB").copy()
        except Exception:
            page, document = _open_source_page(source)
            if page is None:
                raise TypeError("UNSUPPORTED_IMAGE_SOURCE") from None
            try:
                png = rasterize_page(page, dpi=DEFAULT_DPI)
            finally:
                if document is not None:
                    document.close()
            with Image.open(io.BytesIO(png)) as image:
                return image.convert("RGB").copy()
    page, document = _open_source_page(source)
    if page is None:
        raise TypeError("UNSUPPORTED_IMAGE_SOURCE")
    try:
        png = rasterize_page(page, dpi=DEFAULT_DPI)
    finally:
        if document is not None:
            document.close()
    with Image.open(io.BytesIO(png)) as image:
        return image.convert("RGB").copy()


def _invoke_scorer(
    scorer: Callable[..., Any], image: Any, angle: int
) -> float:
    try:
        return float(scorer(image, angle))
    except TypeError:
        result = scorer(image)
        if isinstance(result, Mapping):
            return float(result.get(angle, result.get(str(angle), 0.0)))
        return float(result)


def score_orientation(
    image: Any,
    *,
    scorer: Callable[[Any, int], float] | None = None,
) -> OrientationScore:
    """Score the four orientations and return the most likely correction.

    The default scorer inspects native PyMuPDF text directions, which works on
    PDF pages and PDF paths without invoking OCR. An injected scorer receives
    ``(rotated_image, clockwise_correction_angle)`` for each candidate.
    """

    scores = {angle: 0.0 for angle in ANGLES}
    total = 0.0
    method = "native_text_unavailable"
    try:
        if scorer is None:
            scores, total, method = _native_text_score(image)
        else:
            method = "injected_scorer"
            candidates = rotation_candidates(_pil_from_source(image))
            by_angle = {
                candidate.orientation_degrees: candidate for candidate in candidates
            }
            for pil_angle in ANGLES:
                candidate = by_angle.get(pil_angle)
                if candidate is None:
                    continue
                correction = (360 - pil_angle) % 360
                try:
                    value = max(0.0, _invoke_scorer(scorer, candidate.image, correction))
                except Exception:
                    value = 0.0
                scores[correction] = value
            total = sum(scores.values())
    except Exception as exc:
        return OrientationScore(
            angle=0,
            confidence=0.0,
            method=f"fallback:{_error_code(exc)}",
            scores=scores,
        )

    angle, confidence = _select_orientation(scores, total)
    return OrientationScore(angle, confidence, method, scores)


def auto_rotate_page(
    page: Any,
    *,
    scorer: Callable[[Any, int], float] | None = None,
) -> AutoRotateResult:
    """Return the clockwise rotation that makes page content upright."""

    try:
        score = score_orientation(page, scorer=scorer)
    except Exception as exc:
        return AutoRotateResult(0, 0.0, f"fallback:{_error_code(exc)}")
    return AutoRotateResult(score.angle, score.confidence, score.method)


def _rotate_point(
    x: float, y: float, width: float, height: float, angle: int
) -> tuple[float, float, float, float]:
    if angle == 90:
        return height - y, x, height, width
    if angle == 180:
        return width - x, height - y, width, height
    if angle == 270:
        return y, width - x, height, width
    return x, y, width, height


def _rotate_bbox(
    bbox: BBox, width: float, height: float, angle: int
) -> tuple[BBox, float, float]:
    if angle == 90:
        return (
            BBox(
                x0=height - bbox.y1,
                y0=bbox.x0,
                x1=height - bbox.y0,
                y1=bbox.x1,
            ),
            height,
            width,
        )
    if angle == 180:
        return (
            BBox(
                x0=width - bbox.x1,
                y0=height - bbox.y1,
                x1=width - bbox.x0,
                y1=height - bbox.y0,
            ),
            width,
            height,
        )
    if angle == 270:
        return (
            BBox(
                x0=bbox.y0,
                y0=width - bbox.x1,
                x1=bbox.y1,
                y1=width - bbox.x0,
            ),
            height,
            width,
        )
    return bbox, width, height


def apply_rotation(page_perception: PagePerception, angle: int) -> PagePerception:
    """Rotate page geometry clockwise by ``angle`` degrees."""

    if isinstance(angle, bool) or angle not in ANGLES:
        raise ValueError("angle must be one of 0, 90, 180, 270")
    if angle == 0:
        return page_perception.model_copy(deep=True)

    width = page_perception.width
    height = page_perception.height
    rotated_blocks = []
    for block in page_perception.blocks:
        bbox, _, _ = _rotate_bbox(block.bbox, width, height, angle)
        rotated_blocks.append(block.model_copy(update={"bbox": bbox}))

    rotated_annotations = []
    for annotation in page_perception.annotations:
        bbox = None
        if annotation.bbox is not None:
            bbox, _, _ = _rotate_bbox(annotation.bbox, width, height, angle)
        points = []
        for x, y in annotation.points:
            rotated_x, rotated_y, _, _ = _rotate_point(x, y, width, height, angle)
            points.append((rotated_x, rotated_y))
        rotated_annotations.append(
            annotation.model_copy(update={"bbox": bbox, "points": points})
        )

    new_width, new_height = (height, width) if angle in (90, 270) else (width, height)
    rotation = (page_perception.rotation_applied + angle) % 360
    return PagePerception(
        page_index=page_perception.page_index,
        width=new_width,
        height=new_height,
        rotation_applied=rotation,
        blocks=rotated_blocks,
        annotations=rotated_annotations,
    )


def _detect_skew(gray: Any, cv2: Any) -> float | None:
    threshold = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU
    )[1]
    coordinates = cv2.findNonZero(threshold)
    if coordinates is None or len(coordinates) == 0:
        return None
    _, (width, height), angle = cv2.minAreaRect(coordinates)
    long_side_angle = angle if width >= height else angle - 90.0
    return ((long_side_angle + 45.0) % 90.0) - 45.0


def cleanup_image(
    image: Any, *, deskew: bool = True
) -> tuple[Any, list[str]]:
    """Convert to grayscale, equalise and optionally deskew with OpenCV."""

    try:
        cv2 = importlib.import_module("cv2")
        numpy = importlib.import_module("numpy")
    except ImportError:
        return image, ["opencv_unavailable"]
    except Exception as exc:
        return image, [f"opencv_failed:{_error_code(exc)}"]

    notes: list[str] = []
    try:
        from_pil = _is_pil_image(image)
        array = numpy.asarray(image)
        if array.ndim == 3 and array.shape[2] == 4:
            gray = cv2.cvtColor(array, cv2.COLOR_RGBA2GRAY)
        elif array.ndim == 3:
            gray = cv2.cvtColor(array[..., :3], cv2.COLOR_RGB2GRAY)
        elif array.ndim == 2:
            gray = array
        else:
            return image, ["opencv_failed:UNSUPPORTED_IMAGE_SHAPE"]
        if gray.dtype != numpy.uint8:
            gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX).astype(
                numpy.uint8
            )

        equalized = cv2.equalizeHist(gray)
        notes.extend(["grayscale_applied", "equalization_applied"])
        result = equalized
        if deskew:
            skew = _detect_skew(equalized, cv2)
            if skew is None:
                notes.append("deskew_skipped_no_content")
            elif abs(skew) < DESKEW_MIN_ANGLE:
                notes.append("deskew_not_required")
            else:
                height, width = equalized.shape[:2]
                matrix = cv2.getRotationMatrix2D(
                    (width / 2.0, height / 2.0), skew, 1.0
                )
                result = cv2.warpAffine(
                    equalized,
                    matrix,
                    (width, height),
                    flags=cv2.INTER_CUBIC,
                    borderMode=cv2.BORDER_REPLICATE,
                )
                notes.append("deskew_applied")

        if from_pil:
            from PIL import Image

            result = Image.fromarray(result)
        return result, notes
    except Exception as exc:
        return image, [f"opencv_failed:{_error_code(exc)}"]


def _error_code(exc: BaseException) -> str:
    if isinstance(exc, GeometryError):
        return exc.code
    name = type(exc).__name__
    if isinstance(exc, FileNotFoundError) or name == "FileNotFoundError":
        return "FILE_NOT_FOUND"
    if isinstance(exc, PermissionError):
        return "PERMISSION_DENIED"
    if name == "FileDataError":
        return "INVALID_DOCUMENT"
    if isinstance(exc, ImportError):
        return "DEPENDENCY_UNAVAILABLE"
    if isinstance(exc, ValueError):
        return "INVALID_GEOMETRY"
    if isinstance(exc, OSError):
        return "IO_ERROR"
    return name.upper() or "UNKNOWN"


def _rect_area(rectangle: Any) -> float:
    try:
        if hasattr(rectangle, "x0"):
            width = float(rectangle.x1) - float(rectangle.x0)
            height = float(rectangle.y1) - float(rectangle.y0)
        elif isinstance(rectangle, (list, tuple)) and len(rectangle) >= 4:
            width = float(rectangle[2]) - float(rectangle[0])
            height = float(rectangle[3]) - float(rectangle[1])
        else:
            return 0.0
    except Exception:
        return 0.0
    return max(0.0, width) * max(0.0, height)


def _image_coverage(page: Any) -> float:
    try:
        page_area = float(page.rect.width) * float(page.rect.height)
        if page_area <= 0:
            return 0.0
        images = page.get_image_info() or []
        image_area = 0.0
        for info in images:
            if not isinstance(info, Mapping):
                continue
            rectangle = info.get("bbox", info.get("rect"))
            image_area += _rect_area(rectangle)
        return min(1.0, image_area / page_area)
    except Exception:
        return 1.0


def _fast_path_eligible(page: Any) -> tuple[bool, str]:
    """Return whether a page can skip rasterisation and deskew."""

    try:
        if int(getattr(page, "rotation", 0)) % 360 != 0:
            return False, "page_rotation"
        text = page.get_text()
        if len(str(text).split()) < FAST_PATH_MIN_WORDS:
            return False, "text_layer_too_short"
        scores, total = _text_direction_scores(page)
        horizontal = scores.get(0, 0.0) / total if total > 0 else 0.0
        if horizontal < FAST_PATH_HORIZONTAL_MIN:
            return False, "orientation_not_horizontal"
        if _image_coverage(page) > FAST_PATH_MAX_IMAGE_COVERAGE:
            return False, "image_coverage"
        return True, "fast_path_native_text"
    except Exception:
        return False, "fast_path_evaluation_error"


def normalise_geometry(
    path: str | Path, *, mode: Literal["full", "light"] = "full"
) -> GeometryReport:
    """Rasterise and normalise the first page without leaking exceptions."""

    source_name = ""
    try:
        if mode not in {"full", "light"}:
            raise ValueError("mode must be 'full' or 'light'")
        source = Path(path)
        source_name = source.name
        pymupdf = importlib.import_module("pymupdf")
        document = pymupdf.open(str(source))
        try:
            page_count = len(document)
            if page_count <= 0:
                raise GeometryError("EMPTY_DOCUMENT")
            page = document[0]
            fast_path, fast_reason = _fast_path_eligible(page)
            if mode == "light":
                orientation = auto_rotate_page(page)
                png = b""
                cleanup_notes = (
                    ["geometry_fast_path"]
                    if fast_path
                    else ["geometry_light_mode"]
                )
                deskew_applied = False
            elif fast_path:
                scores, total = _text_direction_scores(page)
                confidence = (
                    scores.get(0, 0.0) / total if total > 0 else 0.0
                )
                orientation = AutoRotateResult(
                    0, confidence, "fast_path_native_text"
                )
                png = b""
                cleanup_notes = ["geometry_fast_path"]
                deskew_applied = False
            else:
                orientation = auto_rotate_page(page)
                if orientation.angle:
                    page.set_rotation(
                        (int(page.rotation) + orientation.angle) % 360
                    )
                source_sha = source_sha256(source)
                png = rasterize_page_cached(
                    page,
                    dpi=DEFAULT_DPI,
                    source_sha256=source_sha,
                    page_index=0,
                )
                from PIL import Image

                needs_deskew = len(page.get_text().split()) < FAST_PATH_MIN_WORDS
                with Image.open(io.BytesIO(png)) as rendered:
                    _, cleanup_notes = cleanup_image(
                        rendered, deskew=needs_deskew
                    )
                if not needs_deskew:
                    cleanup_notes.append("deskew_skipped_no_consumer")
                deskew_applied = "deskew_applied" in cleanup_notes
            rectangle = page.rect
            notes = list(cleanup_notes)
            page_report = {
                "page_index": 0,
                "notes": list(notes),
                "rotation_degrees": orientation.angle,
                "rotation_method": orientation.method,
                "deskew_applied": deskew_applied,
                "raster_bytes": len(png),
                "fast_path": bool(fast_path),
                "fast_path_reason": fast_reason,
            }
            return GeometryReport(
                source_name=source_name,
                page_count=page_count,
                width=float(rectangle.width),
                height=float(rectangle.height),
                rotation_degrees=orientation.angle,
                rotation_confidence=orientation.confidence,
                rotation_method=orientation.method,
                raster_dpi=DEFAULT_DPI,
                raster_bytes=len(png),
                deskew_applied=deskew_applied,
                fallback=False,
                notes=notes,
                pages=[page_report],
            )
        finally:
            document.close()
    except Exception as exc:
        code = _error_code(exc)
        return GeometryReport(
            source_name=source_name,
            fallback=True,
            notes=[f"GEOMETRY_FAILED:{code}"],
            error_code=code,
        )
