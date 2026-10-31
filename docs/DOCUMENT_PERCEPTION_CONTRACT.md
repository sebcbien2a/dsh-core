---
master_doc: PROJECT_HANDOVER.md
branch_domain: Arête 1 — Perception & Vision (L0-L5)
contract_version: 1.0.1
status: FROZEN
upstream_dependencies: [schemas/profile_schema.py, schemas/document_result.py, benchmarks/orientation_benchmark.py, model_router.py]
downstream_consumers: [backend/app/graphs/invoice_graph.py, services/invoice_producer_protocol.py, scripts/run_batch_audit.py, services/template_skill_store.py]
verification_tests: [tests/test_document_perception.py, tests/test_document_geometry.py, tests/test_document_geometry_fastpath.py, tests/test_document_router.py, tests/test_document_extractors.py, tests/test_annotation_perception.py, tests/test_document_engine.py, tests/test_document_pipeline.py, tests/test_invoice_graph_perception.py]
---

# Document & Vision Perception Contract

**contract_version: 1.0.1**

Status: frozen after Phase 8.1. Any incompatible evolution requires a new phase,
a migration note in this file and a version increment.

## Migration 1.0.1

- `extract_annotations`/`extract_annotations_with_notes` add
  `include_vector_drawings=False`. The batch default now ignores vector drawings;
  editing/distillation passes must request `True`.
- `normalise_geometry` adds `mode="full"|"light"`. Routing uses `light` (no
  raster/deskew); final perception of scanned/image profiles uses `full`.
- `DocumentRouter` caches route verdicts (LRU 64) keyed by source SHA-256 and
  scorer id; `reset_route_cache()` clears it.
- `rasterize_page_cached` and `reset_raster_cache()` add a shared LRU raster
  memo; `normalise_geometry` and PaddleOCR reuse it.
- `find_tables` probing is bounded to two pages and is skipped when a strong
  business signature is detected.
- `PERCEPTION_VECTOR_DRAWINGS=on|off` controls the default engine (off).
- The P8.1 baseline is re-signed under `contract_version: 1.0.1`.

## Scope

The contract covers the local Document & Vision Pipeline from geometry
normalisation to template skills:

- L0 geometry and orientation;
- L1 perception schemas;
- L2 document routing;
- L3 extractors;
- L4 annotation perception and editing targets;
- L5 local/cloud/hybrid engines and pipeline facade;
- candidate template skills and teacher distillation.

## L0 — Geometry

Public symbols in `services/document_geometry.py`:

```python
rasterize_page(page: Any, *, dpi: int = 170) -> bytes
rotation_candidates(image: Any) -> tuple[OrientationCandidate, ...]
score_orientation(image: Any, *, scorer: Callable[[Any, int], float] | None = None) -> OrientationScore
auto_rotate_page(page: Any, *, scorer: Callable[[Any, int], float] | None = None) -> AutoRotateResult
apply_rotation(page_perception: PagePerception, angle: int) -> PagePerception
cleanup_image(image: Any, *, deskew: bool = True) -> tuple[Any, list[str]]
normalise_geometry(path: str | Path, *, mode: Literal["full", "light"] = "full") -> GeometryReport
rasterize_page_cached(page: Any, *, dpi: int = 170, source_sha256: str = "", page_index: int = 0) -> bytes
reset_raster_cache() -> None
```

Conventions:

- `angle` is a clockwise correction in `{0, 90, 180, 270}`.
- `apply_rotation` rotates block bounding boxes and annotation points clockwise.
- `score_orientation` accepts an injected scorer `(image, clockwise_angle) -> float`.
- `cleanup_image` returns `(image, ["opencv_unavailable"])` when OpenCV is absent.
- `normalise_geometry` never raises; failures use `GEOMETRY_FAILED:<code>`.

Fast-path conditions (all required), evaluated per processed page:

- `FAST_PATH_MIN_WORDS = 50`;
- `FAST_PATH_HORIZONTAL_MIN = 0.9`;
- `FAST_PATH_MAX_IMAGE_COVERAGE = 0.30`;
- `page.rotation == 0`;
- native word count >= `FAST_PATH_MIN_WORDS`;
- horizontal native-text score >= `FAST_PATH_HORIZONTAL_MIN`;
- image coverage <= `FAST_PATH_MAX_IMAGE_COVERAGE`.

When eligible, `normalise_geometry` does not call `rasterize_page` or
`cleanup_image`; it returns `AutoRotateResult(0, score, "fast_path_native_text")`
and adds the note `geometry_fast_path`. Any evaluation error falls back to the
full path; unrecoverable failures keep the `GEOMETRY_FAILED:<code>` fallback.

## L1 — Perception schemas

Frozen in `schemas/document_perception.py`:

- `BBox(x0, y0, x1, y1)` with ordered-coordinate validation, `overlap_ratio`
  (IoU), `union`, `intersection_area`;
- `VisualAnnotation(kind, page_index, bbox, points, confidence, anchor_block_id, raw_text_target)`;
- `Block(block_id, kind, page_index, bbox, text, table, confidence, reading_order)`;
- `PagePerception(page_index, width, height, rotation_applied, blocks, annotations)`;
- `DocumentPerception(source_name, source_sha256, profile, pages, raw_text, tools_used, processing_notes, metadata)`;
- bridges `DocumentPerception.to_document_result()` and
  `DocumentPerception.from_document_result(...)`.

Fields may not be removed or renamed. Optional extensions only.

## L2 — Router

```python
ALLOWED_TOOLS = frozenset({
    "pymupdf_text", "find_tables", "pandas", "docling", "paddleocr", "vision_text"
})
DocumentRouter(*, profiler=None, ocr_scorer=None, text_threshold=50)
DocumentRouter.route(path: str | Path) -> RouterVerdict
reset_route_cache() -> None
DocumentRouter.plan_for(decision: RoutingDecision, profile: DocumentProfile) -> ExtractionPlan
```

`RouteKind` values: `native_text`, `local_ocr`, `local_vision`, `cloud_vision`,
`docling`, `spreadsheet`, `manual_review`, `unsupported`.

Frozen mapping:

| Profile / family | Route | Task kinds → tools |
| --- | --- | --- |
| native text invoice / annotated | `native_text` | `text → pymupdf_text`; annotated adds `annotation → pymupdf_text` |
| complex table | `docling` | `table → find_tables`, `table → docling`, `text → pymupdf_text` |
| scan / image | `local_ocr` | `ocr → paddleocr`, `vision → vision_text` |
| handwriting / OCR impossible | `local_vision` | `vision → vision_text` |
| spreadsheet / bank statement | `spreadsheet` | `table → pandas` |
| unsupported / manual review | `unsupported` or `manual_review` | `classification → pymupdf_text` |

`native_pdf` with a text layer routes to `native_text`, never to `docling`.
`route()` never raises; internal failure returns `manual_review` with
`ROUTER_FAILED:<code>`.

## L3 — Extractors

```python
class TextExtractor(Protocol):
    name: str
    def extract(self, source: str | Path, *, page_indices: list[int] | None = None, dpi: int = 170) -> list[Block]: ...

ExtractorRegistry.register(extractor) -> None
ExtractorRegistry.get(name: str) -> TextExtractor
ExtractorRegistry.run_plan(plan: ExtractionPlan, source: str | Path, *, dpi: int = 170) -> PlanOutcome
```

`ExtractionUnavailable(code, detail)` codes include `PANDAS_UNAVAILABLE`,
`DOCLING_UNAVAILABLE`, `PADDLEOCR_UNAVAILABLE`, `VISION_TRANSCRIBER_MISSING`,
`UNKNOWN_TOOL`, `EXTRACTOR_FAILED:<type>`.

`TaskSpec.kind` contract:

| kind | tools |
| --- | --- |
| `text` | `pymupdf_text` |
| `table` | `find_tables`, `docling`, `pandas` |
| `ocr` | `paddleocr` |
| `vision` | `vision_text` |
| `handwriting` | `vision_text`, `paddleocr` |
| `annotation` | `pymupdf_text` |
| `classification` | `pymupdf_text` |

`run_plan` returns `PlanOutcome(blocks, notes)` and never raises.

## L4 — Annotations

```python
extract_annotations(page, *, page_index, blocks, min_overlap=0.2, include_vector_drawings=False) -> list[VisualAnnotation]
extract_annotations_with_notes(page, *, page_index, blocks, min_overlap=0.2, include_vector_drawings=False) -> tuple[AnnotationList, list[str]]
find_anchor_block(bbox: BBox, blocks: list[Block], *, min_overlap: float = 0.2) -> str | None
EditingTaskAdapter.resolve_targets(perception) -> list[EditingTarget]
EditingTaskAdapter.locate(perception, *, query=None) -> list[EditingTarget]
```

Frozen annotation mapping: Square→square, Circle→circle, Highlight→highlight,
StrikeOut→strike, Underline/Squiggly→underline, Ink→ink, Line→arrow,
FreeText→margin_note, otherwise unknown. Annotation geometries remain in the raw
page frame; `apply_rotation` is the only rotation step.

## L5 — Engines and pipeline

```python
class DocumentEngine(Protocol):
    def perceive(self, source: Path) -> DocumentPerception: ...
    def extract_text(self, perception: DocumentPerception) -> str: ...
    def structure(self, text: str, task: TaskSpec, *, perception: DocumentPerception | None = None) -> Mapping[str, Any]: ...

LocalDocumentEngine(*, router=None, registry=None, scorer=None, structured=None, skill_store=None, dpi=170)
CloudDocumentEngine(*, model=None, client=None, dpi=150)
HybridDocumentEngine(local, cloud, budget=None)

is_pipeline_enabled(env=None) -> bool
get_default_engine(*, document_count: int = 1) -> DocumentEngine
perceive_document(source: str | Path, *, engine: DocumentEngine | None = None) -> DocumentPerception
perception_summary(perception: DocumentPerception) -> dict[str, Any]
```

- `DOCUMENT_PIPELINE` is on by default; explicit `off/0/false/no` disables it.
- `DOCUMENT_ENGINE` accepts `local` (default), `hybrid`, `cloud`.
- `PERCEPTION_VECTOR_DRAWINGS` defaults off; `on` includes `get_cdrawings()`.
- `perceive_document` never raises and caches at most 64 perceptions by
  `(source_sha256, engine type, dpi)`.
- `budget_max(ratio: float, total: int) -> int` returns
  `max(0, ceil(ratio * max(0, total)))`.
- `CloudEscalationBudget` accepts only `int | None`; other values raise
  `BUDGET_MAX_MUST_BE_INT`.
- `SUPERVISOR_ESCALATION_MARKERS` remains the escalation gate.
- `LocalDocumentEngine.structure` may use a template skill before falling back
  to the injected structured extractor.

## Skills

```python
TemplateAnchor(field_key, anchor_text, relative_position, confidence, page_index)
TemplateSkill(skill_name, supplier_id, domain, version, tenant, lifecycle, extraction_rules, layout_anchors, visual_signature, created_from)
filter_teacher_anchors(perception, *, cap=12, min_confidence=0.5) -> tuple[list[dict], dict]
TemplateSkillStore.save(skill) -> Path
TemplateSkillStore.load(skill_name) -> TemplateSkill | None
TemplateSkillStore.find_by_supplier(*, supplier_id=None, siret=None, vat_number=None, name=None) -> TemplateSkill | None
TemplateSkillStore.find_by_signature(perception) -> TemplateSkill | None
TemplateSkillStore.inject_supplier_layout_rules(skill) -> list[SupplierLayoutRule]
TemplateSkillApplier.match(perception, *, supplier_hint=None) -> TemplateSkill | None
TemplateSkillApplier.extract(perception, skill) -> dict[str, Any]
```

Filtering rules: `raw_text_target` required; kinds limited to circle, ellipse,
square, highlight, strike, arrow; confidence >= 0.5; IoU deduplication > 0.7;
maximum 12 anchors. `TemplateSkill.layout_anchors` is capped at 12.

Governance:

- distilled `SupplierLayoutRule` instances are candidates only:
  `active=False` and `human_confirmed=False`;
- activation requires human confirmation, a valid `source_sha256` and
  `confirmed_at`;
- any activation increments the skill/rule version through a dedicated
  migration phase.

## Frozen producer metadata keys

The following keys may not be renamed or removed:

`extraction_tool`, `raw_text_chars`, `supplier_country`, `fallback_reason`,
`producer`, `supplier_memory`, `perception_profile`, `perception_tools`,
`perception_annotations`, `routing_route`, `routing_family`,
`rotation_applied`.

## Non-regression

- Full test suite must stay green; the P8 baseline is the signed reference.
- Batch `--document-pipeline off` and `on` must keep identical decisions and
  rule outcomes.
- Existing producer metadata keys remain unchanged; pipeline keys are additive.
- Any contract change requires a version increment and a migration note here.
