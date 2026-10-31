"""Perception contracts for the Document & Vision Pipeline.

The models in this module describe what was seen on a document before any
business extraction happens: geometry, blocks, visual annotations and the plan
used to route the document through local or cloud tools. ``DocumentResult``
remains the legacy extraction envelope; the bridge methods on
``DocumentPerception`` keep both contracts interoperable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemas.document_result import DocumentResult
from schemas.profile_schema import DocumentProfile

AnnotationKind = Literal[
    "circle",
    "ellipse",
    "square",
    "highlight",
    "strike",
    "underline",
    "arrow",
    "handwrite",
    "margin_note",
    "ink",
    "unknown",
]
BlockKind = Literal[
    "text",
    "table",
    "image",
    "stamp",
    "signature",
    "barcode",
    "form_field",
]
RotationAngle = Literal[0, 90, 180, 270]
RouteKind = Literal[
    "native_text",
    "local_ocr",
    "local_vision",
    "cloud_vision",
    "docling",
    "spreadsheet",
    "manual_review",
    "unsupported",
]
TaskKind = Literal[
    "text",
    "table",
    "ocr",
    "vision",
    "handwriting",
    "annotation",
    "classification",
]

_LEGACY_BUSINESS_TYPE_KEY = "__document_result_business_type__"
_LEGACY_CONFIDENCE_KEY = "__document_result_confidence__"
_RESERVED_METADATA_KEYS = frozenset(
    {_LEGACY_BUSINESS_TYPE_KEY, _LEGACY_CONFIDENCE_KEY}
)


class StrictModel(BaseModel):
    """Base model rejecting unknown fields to keep contracts explicit."""

    model_config = ConfigDict(extra="forbid")


class BBox(StrictModel):
    """Axis-aligned rectangle expressed in page pixel coordinates."""

    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def validate_coordinate_order(self) -> Self:
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError("bbox requires x0 <= x1 and y0 <= y1")
        return self

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.width * self.height

    def intersection_area(self, other: "BBox") -> float:
        if not isinstance(other, BBox):
            raise TypeError("other must be a BBox")
        width = min(self.x1, other.x1) - max(self.x0, other.x0)
        height = min(self.y1, other.y1) - max(self.y0, other.y0)
        if width <= 0 or height <= 0:
            return 0.0
        return width * height

    def overlap_ratio(self, other: "BBox") -> float:
        """Return the intersection-over-union ratio with ``other``."""

        if not isinstance(other, BBox):
            raise TypeError("other must be a BBox")
        intersection = self.intersection_area(other)
        union = self.area + other.area - intersection
        if union <= 0:
            return 0.0
        return intersection / union

    def union(self, other: "BBox") -> "BBox":
        if not isinstance(other, BBox):
            raise TypeError("other must be a BBox")
        return BBox(
            x0=min(self.x0, other.x0),
            y0=min(self.y0, other.y0),
            x1=max(self.x1, other.x1),
            y1=max(self.y1, other.y1),
        )


class VisualAnnotation(StrictModel):
    """A mark drawn on a page by a human or an annotation layer."""

    kind: AnnotationKind
    page_index: int = Field(default=0, ge=0)
    bbox: BBox | None = None
    points: list[tuple[float, float]] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    anchor_block_id: str | None = None
    raw_text_target: str | None = None


class Block(StrictModel):
    """A layout block produced by native extraction, OCR or vision."""

    block_id: str
    kind: BlockKind
    page_index: int = Field(default=0, ge=0)
    bbox: BBox
    text: str = ""
    table: list[list[str]] | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    reading_order: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_table_grid(self) -> Self:
        if self.table is not None:
            row_lengths = {len(row) for row in self.table}
            if len(row_lengths) > 1:
                raise ValueError("table grid must be rectangular")
        return self


class PagePerception(StrictModel):
    """Geometry, blocks and annotations for a single page."""

    page_index: int = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation_applied: RotationAngle = 0
    blocks: list[Block] = Field(default_factory=list)
    annotations: list[VisualAnnotation] = Field(default_factory=list)


class DocumentPerception(StrictModel):
    """Full perception result for one source document."""

    source_name: str
    source_sha256: str = ""
    profile: DocumentProfile
    pages: list[PagePerception] = Field(default_factory=list)
    raw_text: str = ""
    tools_used: list[str] = Field(default_factory=list)
    processing_notes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def _block_text(self) -> str:
        return "\n".join(
            block.text
            for page in self.pages
            for block in page.blocks
            if block.text
        )

    def total_text(self) -> int:
        """Return the number of characters available for text routing."""

        if self.raw_text:
            return len(self.raw_text)
        return len(self._block_text())

    def to_document_result(self) -> DocumentResult:
        """Project the perception result onto the legacy extraction envelope."""

        metadata = {
            key: value
            for key, value in self.metadata.items()
            if key not in _RESERVED_METADATA_KEYS
        }
        business_type = self.metadata.get(_LEGACY_BUSINESS_TYPE_KEY, "unknown")
        confidence = self.metadata.get(_LEGACY_CONFIDENCE_KEY)
        return DocumentResult(
            source_name=self.source_name,
            profile_type=self.profile.profile_type,
            extraction_tool=", ".join(self.tools_used) or "DocumentPerception",
            raw_text=self.raw_text or self._block_text(),
            business_type=str(business_type),
            confidence=float(confidence) if confidence is not None else None,
            metadata=metadata,
            processing_notes=list(self.processing_notes),
        )

    @classmethod
    def from_document_result(
        cls,
        result: DocumentResult,
        *,
        profile: DocumentProfile | None = None,
    ) -> "DocumentPerception":
        """Build a perception shell from a legacy extraction envelope."""

        metadata = dict(result.metadata)
        if profile is None:
            extension = metadata.get("extension")
            if not isinstance(extension, str) or not extension:
                extension = Path(result.source_name).suffix.lower()
            try:
                word_count = int(metadata.get("word_count", 0) or 0)
            except (TypeError, ValueError):
                word_count = 0
            try:
                page_count = int(metadata.get("page_count", 0) or 0)
            except (TypeError, ValueError):
                page_count = 0
            orientation = metadata.get("orientation_degrees")
            profile = DocumentProfile(
                extension=extension,
                has_text_layer=bool(result.raw_text.strip())
                or bool(metadata.get("has_text_layer", False))
                or word_count > 0,
                word_count=word_count,
                has_tables=bool(metadata.get("has_tables", False)),
                has_images=bool(metadata.get("has_images", False)),
                is_scanned=bool(metadata.get("is_scanned", False)),
                profile_type=result.profile_type,
                orientation_degrees=orientation if isinstance(orientation, int) else None,
                page_count=page_count,
                business_family=metadata.get("business_family")
                if isinstance(metadata.get("business_family"), str)
                else None,
            )

        if result.business_type != "unknown":
            metadata[_LEGACY_BUSINESS_TYPE_KEY] = result.business_type
        if result.confidence is not None:
            metadata[_LEGACY_CONFIDENCE_KEY] = result.confidence

        return cls(
            source_name=result.source_name,
            source_sha256="",
            profile=profile,
            pages=[],
            raw_text=result.raw_text,
            tools_used=[result.extraction_tool] if result.extraction_tool else [],
            processing_notes=list(result.processing_notes),
            metadata=metadata,
        )


class RoutingDecision(StrictModel):
    """Decision produced by the document router before task planning."""

    route: RouteKind
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    model: str | None = None
    fallback: bool = False
    requires_cloud: bool = False


class TaskSpec(StrictModel):
    """One extraction unit that can be scheduled independently."""

    task_id: str
    kind: TaskKind
    tool: str
    page_indices: list[int] = Field(default_factory=list)
    block_ids: list[str] = Field(default_factory=list)
    model: str | None = None
    priority: int = Field(default=100, ge=0)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("page_indices")
    @classmethod
    def validate_page_indices(cls, value: list[int]) -> list[int]:
        if any(index < 0 for index in value):
            raise ValueError("page_indices must be non-negative")
        return value


class ExtractionPlan(StrictModel):
    """Ordered extraction tasks derived from a routing decision."""

    routing: RoutingDecision
    tasks: list[TaskSpec] = Field(default_factory=list)
    source_sha256: str = ""
    tools: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
