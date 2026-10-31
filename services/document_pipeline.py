"""Document perception facade and pipeline activation (Phase 5).

The facade never raises: engine failures become a minimal perception carrying a
``PERCEPTION_FAILED:<code>`` note. ``perception_summary`` exposes the stable
``perception_*`` bridge keys consumed by ``PdfInvoiceProducer``.
"""

from __future__ import annotations

import hashlib
import os
from collections import OrderedDict
from pathlib import Path
from typing import Any, Mapping

from model_router import resolve_escalation_budget_ratio
from schemas.document_perception import DocumentPerception
from services.cloud_vision_extractor import (
    CloudEscalationBudget,
    budget_max,
)
from services.document_engine import (
    CloudDocumentEngine,
    DocumentEngine,
    HybridDocumentEngine,
    LocalDocumentEngine,
    build_fallback_perception,
)
from services.template_skill_store import TemplateSkillStore

DEFAULT_PIPELINE_ENV = "DOCUMENT_PIPELINE"
DEFAULT_ENGINE_ENV = "DOCUMENT_ENGINE"
PERCEPTION_VECTOR_DRAWINGS_ENV = "PERCEPTION_VECTOR_DRAWINGS"
TRUE_VALUES = frozenset({"on", "1", "true", "yes"})
FALSE_VALUES = frozenset({"off", "0", "false", "no"})
PERCEPTION_CACHE_SIZE = 64
_EXPLICIT_ENGINE_KEY = "__explicit__"

_DEFAULT_ENGINE_CACHE: tuple[str, DocumentEngine] | None = None
_PERCEPTION_CACHE: OrderedDict[
    tuple[str, str, Any], DocumentPerception
] = OrderedDict()


def is_pipeline_enabled(
    env: Mapping[str, str] | None = None,
) -> bool:
    """Return whether the document pipeline is active (default: on)."""

    source = os.environ if env is None else env
    raw = source.get(DEFAULT_PIPELINE_ENV)
    if raw is None or not str(raw).strip():
        return True
    value = str(raw).strip().casefold()
    if value in FALSE_VALUES:
        return False
    if value in TRUE_VALUES:
        return True
    return True


def is_vector_drawings_enabled(
    env: Mapping[str, str] | None = None,
) -> bool:
    """Return whether vector drawings are included (default: off)."""

    source = os.environ if env is None else env
    raw = source.get(PERCEPTION_VECTOR_DRAWINGS_ENV)
    if raw is None or not str(raw).strip():
        return False
    return str(raw).strip().casefold() in TRUE_VALUES


def _engine_name() -> str:
    return os.getenv(DEFAULT_ENGINE_ENV, "local").strip().casefold() or "local"


def get_default_engine(*, document_count: int = 1) -> DocumentEngine:
    """Return the configured engine singleton with a corpus-sized budget."""

    global _DEFAULT_ENGINE_CACHE
    name = _engine_name()
    vector_drawings = is_vector_drawings_enabled()
    if name == "hybrid":
        max_escalations = budget_max(
            resolve_escalation_budget_ratio(), document_count
        )
        cache_key = (
            f"hybrid:{max_escalations}:vectors:{int(vector_drawings)}"
        )
    else:
        max_escalations = None
        cache_key = f"{name}:vectors:{int(vector_drawings)}"
    if _DEFAULT_ENGINE_CACHE is not None and _DEFAULT_ENGINE_CACHE[0] in {
        _EXPLICIT_ENGINE_KEY,
        cache_key,
    }:
        return _DEFAULT_ENGINE_CACHE[1]

    if name == "cloud":
        engine: DocumentEngine = CloudDocumentEngine()
    elif name == "hybrid":
        budget = CloudEscalationBudget(max_escalations=max_escalations)
        skill_store = TemplateSkillStore()
        engine = HybridDocumentEngine(
            LocalDocumentEngine(
                skill_store=skill_store,
                include_vector_drawings=vector_drawings,
            ),
            CloudDocumentEngine(),
            budget=budget,
        )
    else:
        engine = LocalDocumentEngine(
            skill_store=TemplateSkillStore(),
            include_vector_drawings=vector_drawings,
        )
    _DEFAULT_ENGINE_CACHE = (cache_key, engine)
    return engine


def set_default_engine(engine: DocumentEngine | None) -> None:
    """Override the default engine explicitly (None clears the override)."""

    global _DEFAULT_ENGINE_CACHE
    if engine is None:
        _DEFAULT_ENGINE_CACHE = None
        return
    _DEFAULT_ENGINE_CACHE = (_EXPLICIT_ENGINE_KEY, engine)


def _source_sha256(source: Any) -> str:
    try:
        path = Path(source)
    except Exception:
        return ""
    if not path.is_file():
        return ""
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except Exception:
        return ""


def _cache_key(
    source: Any, engine: DocumentEngine
) -> tuple[str, str, Any]:
    return (
        _source_sha256(source),
        type(engine).__name__,
        getattr(engine, "dpi", None),
    )


def _cache_get(
    key: tuple[str, str, Any],
) -> DocumentPerception | None:
    perception = _PERCEPTION_CACHE.get(key)
    if perception is not None:
        _PERCEPTION_CACHE.move_to_end(key)
    return perception


def _cache_put(
    key: tuple[str, str, Any], perception: DocumentPerception
) -> None:
    _PERCEPTION_CACHE[key] = perception
    _PERCEPTION_CACHE.move_to_end(key)
    while len(_PERCEPTION_CACHE) > PERCEPTION_CACHE_SIZE:
        _PERCEPTION_CACHE.popitem(last=False)


def reset_perception_cache() -> None:
    """Clear the bounded perception cache."""

    _PERCEPTION_CACHE.clear()


def perceive_document(
    source: str | Path,
    *,
    engine: DocumentEngine | None = None,
) -> DocumentPerception:
    """Perceive a document through an engine; cached and never raises."""

    try:
        path = Path(source)
        active = engine if engine is not None else get_default_engine()
        key = _cache_key(path, active)
        cached = _cache_get(key)
        if cached is not None:
            return cached
        perception = active.perceive(path)
        if not isinstance(perception, DocumentPerception):
            raise TypeError("engine did not return a DocumentPerception")
    except Exception as exc:
        perception = build_fallback_perception(source, exc)
        key = (
            _source_sha256(source),
            type(engine).__name__ if engine is not None else _engine_name(),
            getattr(engine, "dpi", None),
        )
    _cache_put(key, perception)
    return perception


def perception_summary(
    perception: DocumentPerception,
) -> dict[str, Any]:
    """Return the stable perception_* bridge keys."""

    metadata = perception.metadata if isinstance(perception.metadata, dict) else {}
    annotation_count = sum(
        len(page.annotations) for page in perception.pages
    )
    rotation = metadata.get("rotation_applied")
    if not isinstance(rotation, int):
        rotation = max(
            (
                page.rotation_applied
                for page in perception.pages
            ),
            default=0,
        )
    return {
        "perception_profile": perception.profile.profile_type,
        "perception_tools": list(perception.tools_used),
        "perception_annotations": annotation_count,
        "routing_route": metadata.get("routing_route", "unknown"),
        "routing_family": metadata.get(
            "routing_family",
            perception.profile.business_family or "unknown",
        ),
        "rotation_applied": rotation,
    }


def reset_default_engine() -> None:
    """Clear the module-level engine cache (test and reprocessing hook)."""

    global _DEFAULT_ENGINE_CACHE
    _DEFAULT_ENGINE_CACHE = None
