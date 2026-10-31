"""Symmetric local, cloud and hybrid document engines (Phase 5).

Every engine exposes ``perceive``, ``extract_text`` and ``structure``. The local
engine orchestrates the deterministic P0-P4 stack; the cloud engine turns a
multimodal JSON answer into the same ``DocumentPerception`` contract; the hybrid
engine escalates only on supervisor-worthy local failures and consumes a shared
escalation budget.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

import pymupdf

from model_router import resolve_multimodal_fallback_model
from schemas.document_perception import (
    BBox,
    Block,
    DocumentPerception,
    PagePerception,
    TaskSpec,
)
from schemas.profile_schema import DocumentProfile
from services.annotation_perception import extract_annotations_with_notes
from services.cloud_vision_extractor import (
    CloudEscalationBudget,
    CloudVisionExtractorError,
    should_escalate_to_supervisor,
)
from services.document_extractors import (
    ALLOWED_TOOLS,
    ExtractionUnavailable,
    ExtractorRegistry,
    build_default_extractors,
    build_paddle_orientation_scorer,
)
from services.document_geometry import apply_rotation, normalise_geometry
from services.document_profiler import DocumentProfiler
from services.document_router import DocumentRouter
from services.invoice_producer_protocol import extract_document_text
from services.ollama_vision_extractor import document_images, strip_code_fences
from services.template_skill_store import (
    TemplateSkillApplier,
    TemplateSkillStore,
    filter_teacher_anchors,
)

DEFAULT_LOCAL_DPI = 170
DEFAULT_CLOUD_DPI = 150
SCANNED_PROFILES = frozenset({"scanned_pdf", "image"})
_ANGLES = (0, 90, 180, 270)
_SHARED_ROUTER: DocumentRouter | None = None


def get_shared_router() -> DocumentRouter:
    """Return the process-wide router used by triage and local perception."""

    global _SHARED_ROUTER
    if _SHARED_ROUTER is None:
        _SHARED_ROUTER = DocumentRouter(
            ocr_scorer=build_paddle_orientation_scorer()
        )
    return _SHARED_ROUTER

_CLOUD_PERCEPTION_PROMPT = (
    "Analyse ce document et retourne uniquement un objet JSON valide avec la "
    'forme {"pages":[{"page_index":0,"width":595,"height":842,"blocks":'
    '[{"block_id":"b0","kind":"text","bbox":[x0,y0,x1,y1],"text":"...",'
    '"confidence":0.9,"reading_order":0}]}],"raw_text":"..."}. '
    "Les coordonnées bbox sont en points PDF."
)
_CLOUD_STRUCTURE_PROMPT = (
    "Retourne uniquement un objet JSON correspondant à la tâche demandée."
)

_INVOICE_NUMBER_PATTERN = (
    r"(?:FACTURE|INVOICE)\s*(?:N[°ºo]?|NO|NUM(?:ERO)?|#)?\s*[:\-]?\s*"
    r"((?=[A-Z0-9._/\-]*\d)[A-Z0-9][A-Z0-9._/\-]{2,})"
)
_INVOICE_NUMBER_RE = re.compile(_INVOICE_NUMBER_PATTERN, re.IGNORECASE)
_DATE_RE = re.compile(r"(?<!\d)(\d{2})[/.-](\d{2})[/.-](\d{2,4})(?!\d)")
_LABEL_RE = re.compile(r"^\s*([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9 .'_-]{1,40})\s*:\s*(.+)$")


@runtime_checkable
class DocumentEngine(Protocol):
    """Symmetric contract shared by local, cloud and hybrid engines."""

    def perceive(self, source: Path) -> DocumentPerception: ...

    def extract_text(self, perception: DocumentPerception) -> str: ...

    def structure(
        self,
        text: str,
        task: TaskSpec,
        *,
        perception: DocumentPerception | None = None,
    ) -> Mapping[str, Any]: ...


def _error_code(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    name = type(exc).__name__
    if isinstance(exc, FileNotFoundError) or name == "FileNotFoundError":
        return "FILE_NOT_FOUND"
    if isinstance(exc, PermissionError):
        return "PERMISSION_DENIED"
    if isinstance(exc, (TypeError, ValueError, AttributeError)):
        return name.upper()
    return name.upper() or "UNKNOWN"


def _sha256(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except Exception:
        return ""


def _fallback_profile(source: Path | None) -> DocumentProfile:
    extension = source.suffix.lower() if source is not None else ""
    return DocumentProfile(
        extension=extension,
        has_text_layer=False,
        word_count=0,
        has_tables=False,
        has_images=False,
        is_scanned=False,
        profile_type="unknown",
    )


def _safe_source(source: Any) -> Path | None:
    try:
        return Path(source)
    except Exception:
        return None


def build_fallback_perception(
    source: str | Path, exc: BaseException
) -> DocumentPerception:
    """Minimal perception used whenever an engine cannot do better."""

    path = _safe_source(source)
    raw_text = ""
    if path is not None:
        try:
            raw_text = extract_document_text(path)
        except Exception:
            raw_text = ""
    return DocumentPerception(
        source_name=path.name if path is not None else "",
        source_sha256=_sha256(path),
        profile=_fallback_profile(path),
        pages=[],
        raw_text=raw_text,
        tools_used=["legacy_fallback"],
        processing_notes=[f"PERCEPTION_FAILED:{_error_code(exc)}"],
        metadata={
            "routing_route": "unknown",
            "routing_family": "unknown",
            "rotation_applied": 0,
        },
    )


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _blocks_text(blocks: Sequence[Block]) -> str:
    ordered = sorted(
        blocks, key=lambda block: (block.page_index, block.reading_order)
    )
    return "\n".join(block.text for block in ordered if block.text)


def _unique_blocks(blocks: Sequence[Block]) -> list[Block]:
    seen: set[tuple[Any, ...]] = set()
    result: list[Block] = []
    for block in blocks:
        table_key = (
            tuple(tuple(row) for row in block.table)
            if block.table is not None
            else None
        )
        key = (
            block.page_index,
            block.kind,
            block.bbox.x0,
            block.bbox.y0,
            block.bbox.x1,
            block.bbox.y1,
            block.text,
            table_key,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(block)
    return result


def _perception_text(perception: DocumentPerception) -> str:
    if perception.raw_text.strip():
        return perception.raw_text
    blocks = [
        block for page in perception.pages for block in page.blocks
    ]
    return _blocks_text(blocks)


def _successful_tools(notes: Sequence[str]) -> list[str]:
    tools: list[str] = []
    for note in notes:
        tool, separator, detail = note.partition(":")
        if separator and detail.endswith("_blocks") and tool in ALLOWED_TOOLS:
            tools.append(tool)
    return _dedupe(tools)


def _page_dimensions(
    source: Path, page_index: int
) -> tuple[float, float]:
    try:
        with pymupdf.open(str(source)) as document:
            if 0 <= page_index < len(document):
                rectangle = document[page_index].rect
                return float(rectangle.width), float(rectangle.height)
    except Exception:
        pass
    return 595.0, 842.0


class LocalDocumentEngine:
    """Offline sovereign engine orchestrating the P0-P4 pipeline."""

    def __init__(
        self,
        *,
        router: DocumentRouter | None = None,
        registry: ExtractorRegistry | None = None,
        scorer: Callable[[Any, int], float] | None = None,
        structured: Callable[[str, TaskSpec], Mapping[str, Any]] | None = None,
        skill_store: TemplateSkillStore | None = None,
        include_vector_drawings: bool = False,
        dpi: int = DEFAULT_LOCAL_DPI,
    ) -> None:
        if router is not None:
            self.router = router
        elif scorer is not None:
            self.router = DocumentRouter(ocr_scorer=scorer)
        else:
            self.router = get_shared_router()
        self.registry = registry or ExtractorRegistry(build_default_extractors())
        self.scorer = scorer if scorer is not None else self.router.ocr_scorer
        self.structured = structured
        self.skill_store = skill_store
        self.include_vector_drawings = include_vector_drawings
        self.dpi = dpi

    def perceive(self, source: Path) -> DocumentPerception:
        try:
            return self._perceive(Path(source))
        except Exception as exc:
            return build_fallback_perception(source, exc)

    def extract_text(self, perception: DocumentPerception) -> str:
        return _perception_text(perception)

    def structure(
        self,
        text: str,
        task: TaskSpec,
        *,
        perception: DocumentPerception | None = None,
    ) -> Mapping[str, Any]:
        if self.skill_store is not None and perception is not None:
            try:
                applier = TemplateSkillApplier(self.skill_store)
                parameters = (
                    task.parameters if isinstance(task.parameters, dict) else {}
                )
                supplier_hint = parameters.get("supplier_hint")
                if not isinstance(supplier_hint, dict):
                    supplier_hint = None
                skill = applier.match(
                    perception, supplier_hint=supplier_hint
                )
                if skill is not None:
                    extraction = applier.extract(perception, skill)
                    if float(extraction.get("coverage", 0.0)) >= 0.6:
                        self.skill_store.register_usage(skill.skill_name)
                        return extraction
            except Exception:
                pass
        if self.structured is None:
            raise ExtractionUnavailable(
                "STRUCTURED_EXTRACTOR_MISSING",
                "no structured extractor was injected",
            )
        try:
            result = self.structured(text, task)
        except ExtractionUnavailable:
            raise
        except Exception as exc:
            raise ExtractionUnavailable(
                f"STRUCTURED_EXTRACTOR_FAILED:{type(exc).__name__}",
                str(exc),
            ) from exc
        if not isinstance(result, Mapping):
            raise ExtractionUnavailable(
                "STRUCTURED_EXTRACTOR_FAILED",
                "structured extractor must return a mapping",
            )
        return result

    def _perceive(self, source: Path) -> DocumentPerception:
        verdict = self.router.route(source)
        if verdict.decision.route == "unsupported":
            raise ExtractionUnavailable(
                "UNSUPPORTED_SOURCE", "router rejected the source"
            )
        needs_full_geometry = (
            verdict.profile.profile_type in SCANNED_PROFILES
            or verdict.decision.route in {"local_ocr", "local_vision"}
        )
        if needs_full_geometry:
            geometry = normalise_geometry(source, mode="full")
        else:
            geometry = verdict.geometry
            if geometry is None or geometry.fallback:
                geometry = normalise_geometry(source, mode="light")
        outcome = self.registry.run_plan(
            verdict.plan, source, dpi=self.dpi
        )
        blocks = _unique_blocks(outcome.blocks)

        angle = verdict.profile.orientation_degrees
        if angle not in _ANGLES:
            angle = geometry.rotation_degrees
        if angle not in _ANGLES:
            angle = 0

        max_block_page = max(
            (block.page_index for block in blocks), default=-1
        )
        page_count = max(
            verdict.profile.page_count,
            max_block_page + 1,
            1,
        )
        page_notes: list[str] = []
        pages: list[PagePerception] = []
        document = None
        try:
            try:
                document = pymupdf.open(str(source))
            except Exception:
                document = None
            pdf_page_count = len(document) if document is not None else 0
            page_count = max(page_count, pdf_page_count)
            for page_index in range(page_count):
                page = (
                    document[page_index]
                    if document is not None and page_index < pdf_page_count
                    else None
                )
                page_blocks = [
                    block
                    for block in blocks
                    if block.page_index == page_index
                ]
                if page is not None:
                    width = float(page.rect.width)
                    height = float(page.rect.height)
                    annotations, notes = extract_annotations_with_notes(
                        page,
                        page_index=page_index,
                        blocks=page_blocks,
                        include_vector_drawings=self.include_vector_drawings,
                    )
                    page_notes.extend(notes)
                else:
                    width, height = _page_dimensions(source, page_index)
                    annotations = []
                raw_page = PagePerception(
                    page_index=page_index,
                    width=max(width, 1.0),
                    height=max(height, 1.0),
                    blocks=page_blocks,
                    annotations=annotations,
                )
                if angle:
                    raw_page = apply_rotation(raw_page, angle)
                pages.append(raw_page)
        finally:
            if document is not None:
                document.close()

        raw_text = _blocks_text(
            [block for page in pages for block in page.blocks]
        )
        tools_used = _successful_tools(outcome.notes)
        processing_notes = list(geometry.notes)
        processing_notes.extend(outcome.notes)
        processing_notes.extend(page_notes)
        if angle:
            processing_notes.append(f"rotation:{angle}cw")
        return DocumentPerception(
            source_name=source.name,
            source_sha256=_sha256(source),
            profile=verdict.profile,
            pages=pages,
            raw_text=raw_text,
            tools_used=tools_used,
            processing_notes=processing_notes,
            metadata={
                "routing_route": verdict.decision.route,
                "routing_family": verdict.profile.business_family or "unknown",
                "routing_confidence": verdict.decision.confidence,
                "rotation_applied": angle,
                "rotation_method": geometry.rotation_method,
                "geometry_fallback": geometry.fallback,
            },
        )


def _bbox_from_any(value: Any, width: float, height: float) -> BBox:
    if isinstance(value, Mapping):
        return BBox(
            x0=float(value.get("x0", 0.0)),
            y0=float(value.get("y0", 0.0)),
            x1=float(value.get("x1", width)),
            y1=float(value.get("y1", height)),
        )
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        return BBox(
            x0=float(value[0]),
            y0=float(value[1]),
            x1=float(value[2]),
            y1=float(value[3]),
        )
    return BBox(x0=0.0, y0=0.0, x1=width, y1=height)


def _rectangular_table(value: Any) -> list[list[str]] | None:
    if not isinstance(value, (list, tuple)):
        return None
    rows = [
        ["" if cell is None else str(cell) for cell in row]
        for row in value
        if isinstance(row, (list, tuple))
    ]
    if not rows:
        return None
    width = max(len(row) for row in rows)
    return [row + [""] * (width - len(row)) for row in rows]


def _cloud_blocks(
    raw_blocks: Any, page_index: int, width: float, height: float
) -> list[Block]:
    blocks: list[Block] = []
    if not isinstance(raw_blocks, (list, tuple)):
        return blocks
    for index, raw in enumerate(raw_blocks):
        if not isinstance(raw, Mapping):
            continue
        try:
            kind = str(raw.get("kind") or "text")
            if kind not in {
                "text", "table", "image", "stamp", "signature",
                "barcode", "form_field",
            }:
                kind = "text"
            blocks.append(
                Block(
                    block_id=str(raw.get("block_id") or f"cloud-{page_index}-{index}"),
                    kind=kind,
                    page_index=page_index,
                    bbox=_bbox_from_any(raw.get("bbox"), width, height),
                    text=str(raw.get("text") or ""),
                    table=_rectangular_table(raw.get("table")),
                    confidence=float(raw.get("confidence", 1.0)),
                    reading_order=int(raw.get("reading_order", index)),
                )
            )
        except Exception:
            continue
    return blocks


def _page_from_cloud(
    raw: Mapping[str, Any], fallback_index: int, source: Path
) -> PagePerception:
    page_index = int(raw.get("page_index", fallback_index))
    fallback_width, fallback_height = _page_dimensions(source, page_index)
    width = float(raw.get("width") or fallback_width)
    height = float(raw.get("height") or fallback_height)
    return PagePerception(
        page_index=max(page_index, 0),
        width=max(width, 1.0),
        height=max(height, 1.0),
        rotation_applied=int(raw.get("rotation_applied", 0))
        if raw.get("rotation_applied") in _ANGLES
        else 0,
        blocks=_cloud_blocks(raw.get("blocks"), page_index, width, height),
    )


def _cloud_perception(
    data: Mapping[str, Any], source: Path, model: str
) -> DocumentPerception:
    pages_data = data.get("pages")
    pages: list[PagePerception] = []
    if isinstance(pages_data, list) and pages_data:
        for index, raw in enumerate(pages_data):
            if isinstance(raw, Mapping):
                pages.append(_page_from_cloud(raw, index, source))
    if not pages:
        grouped: dict[int, list[Any]] = {}
        raw_blocks = data.get("blocks")
        if isinstance(raw_blocks, list):
            for raw in raw_blocks:
                if isinstance(raw, Mapping):
                    page_index = int(raw.get("page_index", 0))
                    grouped.setdefault(page_index, []).append(raw)
        page_indices = sorted(grouped) or [0]
        for page_index in page_indices:
            width, height = _page_dimensions(source, page_index)
            pages.append(
                PagePerception(
                    page_index=page_index,
                    width=width,
                    height=height,
                    blocks=_cloud_blocks(
                        grouped.get(page_index), page_index, width, height
                    ),
                )
            )

    try:
        profile = DocumentProfiler().profile(source)
    except Exception:
        profile = _fallback_profile(source)
    profile_updates: dict[str, Any] = {"page_count": len(pages)}
    profile_type = data.get("profile_type")
    if isinstance(profile_type, str) and profile_type:
        profile_updates["profile_type"] = profile_type
    business_family = data.get("business_family")
    if isinstance(business_family, str) and business_family:
        profile_updates["business_family"] = business_family
    profile = profile.model_copy(update=profile_updates)

    raw_text = data.get("raw_text") or data.get("text") or ""
    if not isinstance(raw_text, str):
        raw_text = ""
    if not raw_text:
        raw_text = _blocks_text([b for page in pages for b in page.blocks])
    notes = ["cloud_perception"]
    raw_notes = data.get("notes")
    if isinstance(raw_notes, list):
        notes.extend(str(note) for note in raw_notes)
    metadata = data.get("metadata")
    metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    metadata.update(
        {
            "routing_route": "cloud_vision",
            "routing_family": profile.business_family or "unknown",
            "rotation_applied": 0,
        }
    )
    return DocumentPerception(
        source_name=source.name,
        source_sha256=_sha256(source),
        profile=profile,
        pages=pages,
        raw_text=raw_text,
        tools_used=[model],
        processing_notes=notes,
        metadata=metadata,
    )


def _parse_json_payload(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(strip_code_fences(value))
        except json.JSONDecodeError as exc:
            raise CloudVisionExtractorError(
                "INVALID_CLOUD_JSON_PAYLOAD"
            ) from exc
        if isinstance(parsed, Mapping):
            return dict(parsed)
    raise CloudVisionExtractorError("INVALID_CLOUD_JSON_PAYLOAD")


def _default_openrouter_call(
    *,
    model: str,
    images: list[bytes],
    prompt: str,
    operation: str,
) -> str:
    import base64
    import os

    import requests

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise CloudVisionExtractorError(
            "OPENROUTER_API_KEY_NOT_CONFIGURED"
        )
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image in images:
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64,"
                    + base64.b64encode(image).decode("ascii")
                },
            }
        )
    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.0,
        },
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()
    try:
        return str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise CloudVisionExtractorError(
            "INVALID_CLOUD_RESPONSE"
        ) from exc


def _first_callable_result(
    attempts: Sequence[Callable[[], Any]],
) -> Any:
    last_error: TypeError | None = None
    for attempt in attempts:
        try:
            return attempt()
        except TypeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise CloudVisionExtractorError("CLOUD_CLIENT_UNSUPPORTED")


def _invoke_client(
    client: Any,
    operation: str,
    *,
    model: str,
    images: list[bytes] | None = None,
    text: str | None = None,
    task: TaskSpec | None = None,
    prompt: str | None = None,
) -> Any:
    if client is None:
        return _default_openrouter_call(
            model=model,
            images=images or [],
            prompt=prompt or _CLOUD_PERCEPTION_PROMPT,
            operation=operation,
        )
    method = getattr(client, operation, None)
    if callable(method):
        if operation == "perceive":
            attempts = [
                lambda: method(
                    images=images, model=model, prompt=prompt
                ),
                lambda: method(images, model, prompt),
                lambda: method(images, prompt),
                lambda: method(images),
            ]
        else:
            attempts = [
                lambda: method(text=text, task=task, model=model),
                lambda: method(text, task, model),
                lambda: method(text, task),
                lambda: method(text),
            ]
        return _first_callable_result(attempts)
    if callable(client):
        if operation == "perceive":
            attempts = [
                lambda: client(
                    images=images, model=model, prompt=prompt
                ),
                lambda: client(images, model, prompt),
                lambda: client(prompt, images),
                lambda: client(images, prompt),
                lambda: client(images),
            ]
        else:
            attempts = [
                lambda: client(text=text, task=task, model=model),
                lambda: client(text, task, model),
                lambda: client(text, task),
                lambda: client(text),
            ]
        return _first_callable_result(attempts)
    raise CloudVisionExtractorError("CLOUD_CLIENT_UNSUPPORTED")


def _teacher_anchors(perception: DocumentPerception) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    for page in perception.pages:
        page_anchor_count = len(anchors)
        for annotation in page.annotations:
            text = annotation.raw_text_target
            if not text:
                continue
            bbox = annotation.bbox
            relative: dict[str, float] = {}
            if bbox is not None:
                relative = {
                    "x": round(
                        ((bbox.x0 + bbox.x1) / 2.0) / page.width, 4
                    ),
                    "y": round(
                        ((bbox.y0 + bbox.y1) / 2.0) / page.height, 4
                    ),
                }
            anchors.append(
                {
                    "anchor_text": text[:200],
                    "relative_position": relative,
                    "confidence": annotation.confidence,
                }
            )
        if len(anchors) > page_anchor_count:
            continue
        for block in sorted(page.blocks, key=lambda item: item.reading_order):
            if not block.text:
                continue
            relative = {
                "x": round(
                    ((block.bbox.x0 + block.bbox.x1) / 2.0) / page.width, 4
                ),
                "y": round(
                    ((block.bbox.y0 + block.bbox.y1) / 2.0) / page.height, 4
                ),
            }
            anchors.append(
                {
                    "anchor_text": block.text[:200],
                    "relative_position": relative,
                    "confidence": block.confidence,
                }
            )
    return anchors


class CloudDocumentEngine:
    """Oracle engine backed by a multimodal JSON client (Gemini fallback)."""

    def __init__(
        self,
        *,
        model: str | None = None,
        client: Any | None = None,
        dpi: int = DEFAULT_CLOUD_DPI,
    ) -> None:
        self.model = resolve_multimodal_fallback_model(model)
        self.client = client
        self.dpi = dpi

    def perceive(self, source: Path) -> DocumentPerception:
        path = Path(source)
        try:
            images = document_images(path, dpi=self.dpi)
            payload = _invoke_client(
                self.client,
                "perceive",
                model=self.model,
                images=images,
                prompt=_CLOUD_PERCEPTION_PROMPT,
            )
            return _cloud_perception(
                _parse_json_payload(payload), path, self.model
            )
        except Exception as exc:
            return build_fallback_perception(path, exc)

    def extract_text(self, perception: DocumentPerception) -> str:
        return _perception_text(perception)

    def structure(
        self,
        text: str,
        task: TaskSpec,
        *,
        perception: DocumentPerception | None = None,
    ) -> Mapping[str, Any]:
        prompt = (
            f"{_CLOUD_STRUCTURE_PROMPT} Tâche: {task.kind}; "
            f"outil: {task.tool}.\nTexte:\n{text}"
        )
        payload = _invoke_client(
            self.client,
            "structure",
            model=self.model,
            text=text,
            task=task,
            prompt=prompt,
        )
        return _parse_json_payload(payload)

    def teacher_distill(
        self,
        perception: DocumentPerception,
        *,
        supplier_id: str | None = None,
    ) -> dict[str, Any]:
        text = _perception_text(perception)
        labels: dict[str, str] = {}
        for line in text.splitlines():
            match = _LABEL_RE.match(line)
            if match is not None:
                key = re.sub(r"\s+", "_", match.group(1).strip().lower())
                labels[key] = match.group(2).strip()[:200]
        number = _INVOICE_NUMBER_RE.search(text)
        if number is not None:
            labels["invoice_number"] = number.group(1)
        date_match = _DATE_RE.search(text)
        if date_match is not None:
            labels["issue_date"] = date_match.group(0)
        anchors, audit = filter_teacher_anchors(perception)
        first_page = perception.pages[0] if perception.pages else None
        aspect_ratio = (
            round(first_page.width / first_page.height, 4)
            if first_page is not None and first_page.height
            else None
        )
        visual_signature = {
            "label_fingerprint": [
                anchor["anchor_text"] for anchor in anchors
            ],
            "aspect_ratio": aspect_ratio,
            "page_count": len(perception.pages),
        }
        created_at = datetime.now(timezone.utc).isoformat()
        return {
            "extraction_rules": {
                "trusted_labels": labels,
                "regex_numero_facture": _INVOICE_NUMBER_PATTERN,
            },
            "layout_anchors": anchors,
            "anchors": anchors,
            "visual_signature": visual_signature,
            "supplier_id": supplier_id,
            "created_from": {
                "teacher_model": self.model,
                "raw_annotations_seen": audit["raw_annotations_seen"],
                "filtered_annotations": audit["kept"],
                "documents": [perception.source_name],
                "created_at": created_at,
                "audit_trail": audit,
            },
        }


class HybridDocumentEngine:
    """Local-first engine escalating to the cloud only on defined failures."""

    def __init__(
        self,
        local: LocalDocumentEngine,
        cloud: CloudDocumentEngine,
        budget: CloudEscalationBudget | None = None,
    ) -> None:
        self.local = local
        self.cloud = cloud
        self.budget = budget

    def perceive(self, source: Path) -> DocumentPerception:
        path = Path(source)
        local_perception: DocumentPerception | None = None
        try:
            local_perception = self.local.perceive(path)
        except Exception as exc:
            if not should_escalate_to_supervisor(exc):
                return build_fallback_perception(path, exc)
            reason = _error_code(exc)
        else:
            reason = self._escalation_reason(local_perception)
            if reason is None:
                return local_perception

        if self.budget is not None and not self.budget.consume():
            raise CloudVisionExtractorError(
                f"ESCALATION_BUDGET_EXCEEDED: {reason}"
            )
        try:
            cloud_perception = self.cloud.perceive(path)
        except Exception as exc:
            if local_perception is None:
                local_perception = build_fallback_perception(path, exc)
            notes = list(local_perception.processing_notes)
            notes.append(f"CLOUD_ESCALATION_FAILED:{_error_code(exc)}")
            return local_perception.model_copy(
                update={"processing_notes": notes}
            )
        notes = list(cloud_perception.processing_notes)
        notes.append(f"cloud_escalation:{reason}")
        return cloud_perception.model_copy(
            update={"processing_notes": notes}
        )

    def _escalation_reason(
        self, perception: DocumentPerception
    ) -> str | None:
        notes = " ".join(perception.processing_notes)
        if "PERCEPTION_FAILED:" in notes and should_escalate_to_supervisor(
            notes
        ):
            return "local_perception_failed"
        if (
            perception.total_text() == 0
            and perception.profile.profile_type in SCANNED_PROFILES
        ):
            return "scanned_without_text"
        return None

    def extract_text(self, perception: DocumentPerception) -> str:
        return self.local.extract_text(perception)

    def structure(
        self,
        text: str,
        task: TaskSpec,
        *,
        perception: DocumentPerception | None = None,
    ) -> Mapping[str, Any]:
        return self.local.structure(
            text, task, perception=perception
        )
