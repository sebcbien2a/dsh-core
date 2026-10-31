"""Annotation perception and editing targets for the Document & Vision Pipeline.

Annotations and vector drawings are projected onto the raw page frame: no
rotation is applied here. The P5 engine remains responsible for calling
``apply_rotation`` when it normalises a page.
"""

from __future__ import annotations

import logging
import math
import warnings
from dataclasses import dataclass
from typing import Any, Iterable, Literal

from schemas.document_perception import (
    AnnotationKind,
    BBox,
    Block,
    DocumentPerception,
    VisualAnnotation,
)

logger = logging.getLogger(__name__)

_ANNOT_KIND: dict[str, AnnotationKind] = {
    "Square": "square",
    "Circle": "circle",
    "Highlight": "highlight",
    "StrikeOut": "strike",
    "Underline": "underline",
    "Squiggly": "underline",
    "Ink": "ink",
    "Line": "arrow",
    "FreeText": "margin_note",
}


def _normalise_annotation_name(name: str) -> str:
    return name.replace("PDF_ANNOT_", "").replace("_", "").casefold()


_ANNOT_KIND_LOOKUP = {
    _normalise_annotation_name(name): kind
    for name, kind in _ANNOT_KIND.items()
}


class AnnotationList(list[VisualAnnotation]):
    """List of annotations exposing extraction notes for callers."""

    def __init__(
        self,
        values: Iterable[VisualAnnotation] = (),
        *,
        notes: Iterable[str] = (),
    ) -> None:
        super().__init__(values)
        self.notes = list(notes)


def _annotation_error_code(exc: BaseException) -> str:
    name = type(exc).__name__
    if isinstance(exc, ValueError):
        return "INVALID_ANNOTATION"
    if isinstance(exc, AttributeError):
        return "INVALID_PAGE"
    return name.upper() or "UNKNOWN"


def _annotation_kind(annotation: Any) -> AnnotationKind:
    raw_type = getattr(annotation, "type", None)
    if isinstance(raw_type, (tuple, list)) and len(raw_type) > 1:
        name = str(raw_type[1])
    else:
        name = str(raw_type)
    return _ANNOT_KIND_LOOKUP.get(_normalise_annotation_name(name), "unknown")


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


def _padded_bbox(bbox: BBox, padding: float = 1.0) -> BBox:
    if bbox.area > 0:
        return bbox
    return BBox(
        x0=bbox.x0 - padding,
        y0=bbox.y0 - padding,
        x1=bbox.x1 + padding,
        y1=bbox.y1 + padding,
    )


def _anchor_score(annotation_bbox: BBox, block_bbox: BBox) -> float:
    candidate = _padded_bbox(annotation_bbox)
    iou = candidate.overlap_ratio(block_bbox)
    if candidate.area <= 0:
        return iou
    containment = candidate.intersection_area(block_bbox) / candidate.area
    return max(iou, containment)


def _find_anchor(
    bbox: BBox, blocks: list[Block], min_overlap: float
) -> Block | None:
    threshold = min(max(float(min_overlap), 0.0), 1.0)
    best_block: Block | None = None
    best_score = 0.0
    for block in blocks:
        score = _anchor_score(bbox, block.bbox)
        if score > best_score:
            best_score = score
            best_block = block
    if best_block is None or best_score < threshold:
        return None
    return best_block


def find_anchor_block(
    bbox: BBox, blocks: list[Block], *, min_overlap: float = 0.2
) -> str | None:
    """Return the block whose geometry best anchors ``bbox``."""

    block = _find_anchor(bbox, blocks, min_overlap)
    return block.block_id if block is not None else None


def _raw_text_target(bbox: BBox, blocks: list[Block], min_overlap: float) -> str | None:
    block = _find_anchor(bbox, blocks, min_overlap)
    if block is None or not block.text:
        return None
    return block.text[:200]


def _as_point(value: Any) -> tuple[float, float] | None:
    if hasattr(value, "x") and hasattr(value, "y"):
        return float(value.x), float(value.y)
    if (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and all(isinstance(axis, (int, float)) for axis in value[:2])
    ):
        return float(value[0]), float(value[1])
    return None


def _annotation_points(annotation: Any) -> list[tuple[float, float]]:
    vertices = getattr(annotation, "vertices", None)
    if vertices is None:
        return []
    points: list[tuple[float, float]] = []
    if isinstance(vertices, (list, tuple)):
        for item in vertices:
            point = _as_point(item)
            if point is not None:
                points.append(point)
                continue
            if isinstance(item, (list, tuple)):
                for candidate in item:
                    nested = _as_point(candidate)
                    if nested is not None:
                        points.append(nested)
    return points


def _drawing_points(items: Iterable[Any]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for item in items:
        if not isinstance(item, (list, tuple)) or not item:
            continue
        command = item[0]
        if command == "l" and len(item) >= 3:
            coordinates = [item[1], item[2]]
        elif command == "c" and len(item) >= 5:
            coordinates = [item[1], item[2], item[3], item[4]]
        elif command == "re" and len(item) >= 2:
            rectangle = item[1]
            if hasattr(rectangle, "x0"):
                coordinates = [
                    (rectangle.x0, rectangle.y0),
                    (rectangle.x1, rectangle.y0),
                    (rectangle.x1, rectangle.y1),
                    (rectangle.x0, rectangle.y1),
                ]
            elif isinstance(rectangle, (list, tuple)) and len(rectangle) >= 4:
                x0, y0, x1, y1 = rectangle[:4]
                coordinates = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            else:
                coordinates = []
        elif command == "qu" and len(item) >= 2:
            quad = item[1]
            if all(hasattr(quad, name) for name in ("ul", "ur", "lr", "ll")):
                coordinates = [quad.ul, quad.ur, quad.lr, quad.ll]
            elif isinstance(quad, (list, tuple)):
                coordinates = list(quad)
            else:
                coordinates = []
        else:
            coordinates = []
        for value in coordinates:
            point = _as_point(value)
            if point is not None:
                points.append(point)
    return points


def _drawing_bbox(drawing: dict[str, Any], points: list[tuple[float, float]]) -> BBox:
    rectangle = drawing.get("rect")
    if rectangle is not None:
        try:
            return _rect_to_bbox(rectangle)
        except Exception:
            pass
    if points:
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        return BBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys))
    return BBox(x0=0.0, y0=0.0, x1=0.0, y1=0.0)


def _is_ellipse(items: list[Any], bbox: BBox) -> bool:
    curves = [
        item
        for item in items
        if isinstance(item, (list, tuple)) and item and item[0] == "c"
    ]
    if len(curves) < 4 or bbox.width <= 0 or bbox.height <= 0:
        return False
    start = _as_point(curves[0][1]) if len(curves[0]) >= 2 else None
    end = _as_point(curves[-1][4]) if len(curves[-1]) >= 5 else None
    if start is None or end is None:
        return False
    tolerance = max(1.0, 0.02 * math.hypot(bbox.width, bbox.height))
    return _distance(start, end) <= tolerance


def _is_strike(bbox: BBox, blocks: list[Block], min_overlap: float) -> bool:
    if bbox.width <= 0:
        return False
    if bbox.height > 2.0 and bbox.width < bbox.height * 3.0:
        return False
    return _find_anchor(bbox, blocks, min_overlap) is not None


def _segments(items: Iterable[Any]) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    segments = []
    for item in items:
        if (
            isinstance(item, (list, tuple))
            and len(item) >= 3
            and item[0] == "l"
        ):
            first, second = item[1], item[2]
            if (
                isinstance(first, (list, tuple))
                and isinstance(second, (list, tuple))
                and len(first) >= 2
                and len(second) >= 2
            ):
                segments.append(
                    (
                        (float(first[0]), float(first[1])),
                        (float(second[0]), float(second[1])),
                    )
                )
    return segments


def _distance(
    first: tuple[float, float], second: tuple[float, float]
) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _angle(
    first: tuple[float, float], second: tuple[float, float]
) -> float:
    dot = first[0] * second[0] + first[1] * second[1]
    norms = math.hypot(*first) * math.hypot(*second)
    if norms == 0:
        return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, dot / norms))))


def _is_arrow(items: list[Any]) -> bool:
    segments = _segments(items)
    if len(segments) < 3:
        return False
    lengths = [_distance(start, end) for start, end in segments]
    longest_index = max(range(len(segments)), key=lambda index: lengths[index])
    longest_start, longest_end = segments[longest_index]
    longest_length = lengths[longest_index]
    if longest_length <= 0:
        return False
    longest_vector = (
        longest_end[0] - longest_start[0],
        longest_end[1] - longest_start[1],
    )
    heads = 0
    for index, (start, end) in enumerate(segments):
        if index == longest_index:
            continue
        if (
            _distance(start, longest_start) <= 2.0
            or _distance(start, longest_end) <= 2.0
            or _distance(end, longest_start) <= 2.0
            or _distance(end, longest_end) <= 2.0
        ):
            vector = (end[0] - start[0], end[1] - start[1])
            angle = _angle(longest_vector, vector)
            angle = min(angle, 180.0 - angle)
            if lengths[index] < longest_length * 0.6 and 15.0 <= angle <= 90.0:
                heads += 1
    return heads >= 2


def _drawing_kind(
    items: list[Any],
    bbox: BBox,
    blocks: list[Block],
    min_overlap: float,
) -> tuple[AnnotationKind, float]:
    if _is_ellipse(items, bbox):
        ratio = bbox.width / bbox.height if bbox.height else 0.0
        kind: AnnotationKind = "circle" if 0.9 <= ratio <= 1.1 else "ellipse"
        return kind, 0.6
    if _is_strike(bbox, blocks, min_overlap):
        return "strike", 0.5
    if _is_arrow(items):
        return "arrow", 0.5
    return "ink", 0.4


def _annotation_from_pdf(
    annotation: Any,
    *,
    page_index: int,
    blocks: list[Block],
    min_overlap: float,
) -> VisualAnnotation:
    bbox = _rect_to_bbox(annotation.rect)
    anchor = _find_anchor(bbox, blocks, min_overlap)
    return VisualAnnotation(
        kind=_annotation_kind(annotation),
        page_index=page_index,
        bbox=bbox,
        points=_annotation_points(annotation),
        confidence=1.0,
        anchor_block_id=anchor.block_id if anchor is not None else None,
        raw_text_target=(
            anchor.text[:200]
            if anchor is not None and anchor.text
            else None
        ),
    )


def _annotation_from_drawing(
    drawing: dict[str, Any],
    *,
    page_index: int,
    blocks: list[Block],
    min_overlap: float,
) -> VisualAnnotation:
    items = list(drawing.get("items", []) or [])
    points = _drawing_points(items)
    bbox = _drawing_bbox(drawing, points)
    kind, confidence = _drawing_kind(
        items, bbox, blocks, min_overlap
    )
    anchor = _find_anchor(bbox, blocks, min_overlap)
    return VisualAnnotation(
        kind=kind,
        page_index=page_index,
        bbox=bbox,
        points=points,
        confidence=confidence,
        anchor_block_id=anchor.block_id if anchor is not None else None,
        raw_text_target=(
            anchor.text[:200]
            if anchor is not None and anchor.text
            else None
        ),
    )


def _overlaps_existing(
    drawing: VisualAnnotation, annotations: list[VisualAnnotation]
) -> bool:
    if drawing.bbox is None:
        return False
    for annotation in annotations:
        if annotation.bbox is None:
            continue
        if drawing.bbox.overlap_ratio(annotation.bbox) > 0.7:
            return True
    return False


def _page_annotations(page: Any) -> list[Any]:
    annots = getattr(page, "annots", None)
    if callable(annots):
        return list(annots() or [])
    first = getattr(page, "first_annot", None)
    result: list[Any] = []
    seen: set[int] = set()
    current = first
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        result.append(current)
        current = getattr(current, "next", None)
    return result


def extract_annotations_with_notes(
    page: Any,
    *,
    page_index: int,
    blocks: list[Block],
    min_overlap: float = 0.2,
    include_vector_drawings: bool = False,
) -> tuple[AnnotationList, list[str]]:
    """Extract annotations and drawings, returning notes separately."""

    try:
        annotations: AnnotationList = AnnotationList()
        for annotation in _page_annotations(page):
            try:
                annotations.append(
                    _annotation_from_pdf(
                        annotation,
                        page_index=page_index,
                        blocks=blocks,
                        min_overlap=min_overlap,
                    )
                )
            except Exception:
                continue
        existing = list(annotations)
        drawings = (
            page.get_cdrawings() if include_vector_drawings else []
        )
        for drawing in drawings:
            if not isinstance(drawing, dict):
                continue
            try:
                annotation = _annotation_from_drawing(
                    drawing,
                    page_index=page_index,
                    blocks=blocks,
                    min_overlap=min_overlap,
                )
            except Exception:
                continue
            if _overlaps_existing(annotation, existing):
                continue
            annotations.append(annotation)
            existing.append(annotation)
        return annotations, annotations.notes
    except Exception as exc:
        note = f"ANNOTATION_FAILED:{_annotation_error_code(exc)}"
        return AnnotationList(notes=[note]), [note]


def extract_annotations(
    page: Any,
    *,
    page_index: int,
    blocks: list[Block],
    min_overlap: float = 0.2,
    include_vector_drawings: bool = False,
) -> list[VisualAnnotation]:
    """Extract PDF annotations and vector drawings without raising."""

    annotations, notes = extract_annotations_with_notes(
        page,
        page_index=page_index,
        blocks=blocks,
        min_overlap=min_overlap,
        include_vector_drawings=include_vector_drawings,
    )
    for note in notes:
        warnings.warn(note, RuntimeWarning, stacklevel=2)
        logger.warning(note)
    return annotations


_ACTION_BY_KIND: dict[AnnotationKind, Literal["delete", "modify", "extract"]] = {
    "strike": "delete",
    "handwrite": "delete",
    "margin_note": "modify",
    "highlight": "modify",
    "underline": "modify",
    "ink": "modify",
    "unknown": "modify",
    "circle": "extract",
    "ellipse": "extract",
    "square": "extract",
    "arrow": "extract",
}


@dataclass(frozen=True)
class EditingTarget:
    """Editing operation suggested for one visual annotation."""

    annotation: VisualAnnotation
    anchor_block_id: str | None
    raw_text_target: str | None
    suggested_action: Literal["delete", "modify", "extract"]


def _editing_target(annotation: VisualAnnotation) -> EditingTarget:
    return EditingTarget(
        annotation=annotation,
        anchor_block_id=annotation.anchor_block_id,
        raw_text_target=annotation.raw_text_target,
        suggested_action=_ACTION_BY_KIND.get(annotation.kind, "extract"),
    )


def _targets_overlap(first: EditingTarget, second: EditingTarget) -> bool:
    first_bbox = first.annotation.bbox
    second_bbox = second.annotation.bbox
    if first_bbox is None or second_bbox is None:
        return False
    if first.annotation.page_index != second.annotation.page_index:
        return False
    return first_bbox.overlap_ratio(second_bbox) > 0.7


class EditingTaskAdapter:
    """Build and filter editing targets from a perception result."""

    @staticmethod
    def resolve_targets(
        perception: DocumentPerception,
    ) -> list[EditingTarget]:
        candidates: list[tuple[int, VisualAnnotation]] = []
        order = 0
        for page in perception.pages:
            for annotation in page.annotations:
                candidates.append((order, annotation))
                order += 1
        candidates.sort(
            key=lambda item: (
                -item[1].confidence,
                item[1].page_index,
                item[0],
            )
        )
        targets: list[EditingTarget] = []
        for _, annotation in candidates:
            target = _editing_target(annotation)
            if any(_targets_overlap(target, kept) for kept in targets):
                continue
            targets.append(target)
        return targets

    @staticmethod
    def locate(
        perception: DocumentPerception,
        *,
        query: str | None = None,
    ) -> list[EditingTarget]:
        targets = EditingTaskAdapter.resolve_targets(perception)
        if query is None or not query.strip():
            return targets
        needle = query.casefold()
        return [
            target
            for target in targets
            if needle in (target.raw_text_target or "").casefold()
            or needle in (target.anchor_block_id or "").casefold()
            or needle in target.annotation.kind.casefold()
        ]
