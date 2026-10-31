"""Deterministic document routing for the Document & Vision Pipeline.

``DocumentRouter`` combines the physical profile produced by
``DocumentProfiler`` with lightweight business signals (native text, tables,
annotations, spreadsheet headers and bank markers) to choose an extraction
lane. It never raises: unexpected failures produce a ``manual_review`` verdict
with a ``ROUTER_FAILED:<code>`` reason.
"""

from __future__ import annotations

import copy
import hashlib
import re
from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

import pymupdf

from schemas.document_perception import (
    ExtractionPlan,
    RoutingDecision,
    TaskSpec,
)
from schemas.profile_schema import DocumentProfile
from services.document_geometry import (
    AutoRotateResult,
    GeometryReport,
    auto_rotate_page,
    normalise_geometry,
)
from services.document_profiler import DocumentProfiler

ALLOWED_TOOLS = frozenset(
    {"pymupdf_text", "find_tables", "pandas", "docling", "paddleocr", "vision_text"}
)

FAMILY_PRIORITY = (
    "bank_statement",
    "ledger",
    "annotated",
    "receipt",
    "invoice",
    "complex_table",
)
FAMILY_THRESHOLDS = {
    "bank_statement": 0.5,
    "ledger": 0.5,
    "annotated": 0.5,
    "receipt": 0.5,
    "invoice": 0.5,
    "complex_table": 0.5,
}
HANDWRITING_PROFILES = frozenset({"handwritten", "handwriting", "manuscrit"})
BANK_SUFFIXES = frozenset({".ofx", ".qfx"})
SPREADSHEET_SUFFIXES = frozenset({".csv", ".xlsx", ".xls"})
RECEIPT_MIN_RATIO = 1.4
RECEIPT_MAX_WORDS = 400
TEXT_LIMIT = 2000
MAX_TABLE_PROBE_PAGES = 2
ROUTE_CACHE_SIZE = 64

_ROUTE_CACHE: OrderedDict[tuple[str, str], "RouterVerdict"] = OrderedDict()

_INVOICE_NUMBER_RE = re.compile(
    r"(?:FACTURE|INVOICE)\s*(?:N[°ºo]?|NO|NUM(?:ERO)?|#)?\s*[:\-]?\s*"
    r"((?=[A-Z0-9._/\-]*\d)[A-Z0-9][A-Z0-9._/\-]{2,})",
    re.IGNORECASE,
)
_INVOICE_WORD_RE = re.compile(r"\b(?:FACTURE|INVOICE)\b", re.IGNORECASE)
_SUPPLIER_RE = re.compile(
    r"\b(?:fournisseur|vendeur|siret|code client|tva intracommunautaire)\b",
    re.IGNORECASE,
)
_TAX_HT_RE = re.compile(r"\bht\b|h\.t\.?|hors taxe", re.IGNORECASE)
_TAX_TTC_RE = re.compile(
    r"\bttc\b|t\.t\.c\.?|net a payer|net à payer|total", re.IGNORECASE
)
_BANK_MARKER_PATTERNS = {
    "iban": re.compile(r"\biban\b", re.IGNORECASE),
    "solde": re.compile(r"\bsolde\b", re.IGNORECASE),
    "debit": re.compile(r"\b(?:débit|debit)\b", re.IGNORECASE),
    "credit": re.compile(r"\b(?:crédit|credit)\b", re.IGNORECASE),
    "releve": re.compile(r"\b(?:relevé|releve)\b", re.IGNORECASE),
    "statement": re.compile(r"\bstatement\b", re.IGNORECASE),
}


@dataclass(frozen=True)
class RouterVerdict:
    """Result of routing one document through the perception pipeline."""

    profile: DocumentProfile
    decision: RoutingDecision
    plan: ExtractionPlan
    geometry: GeometryReport | None


@dataclass(frozen=True)
class _DocumentSignals:
    readable: bool = False
    page_count: int = 0
    width: float = 0.0
    height: float = 0.0
    text: str = ""
    word_count: int = 0
    table_count: int = 0
    annotation_count: int = 0


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _default_profile(path: Path | None) -> DocumentProfile:
    extension = path.suffix.lower() if path is not None else ""
    return DocumentProfile(
        extension=extension,
        has_text_layer=False,
        word_count=0,
        has_tables=False,
        has_images=False,
        is_scanned=False,
        profile_type="unknown",
    )


def _router_error_code(exc: BaseException) -> str:
    name = type(exc).__name__
    if isinstance(exc, FileNotFoundError) or name == "FileNotFoundError":
        return "FILE_NOT_FOUND"
    if isinstance(exc, PermissionError):
        return "PERMISSION_DENIED"
    if isinstance(exc, TypeError):
        return "INVALID_INPUT"
    if isinstance(exc, ValueError):
        return "INVALID_ARGUMENT"
    if isinstance(exc, ImportError):
        return "DEPENDENCY_UNAVAILABLE"
    if isinstance(exc, OSError):
        return "IO_ERROR"
    return name.upper() or "UNKNOWN"


def _strong_business_signature(text: str) -> bool:
    if len(str(text).split()) < 50:
        return False
    lowered = str(text).casefold()
    has_tax_triplet = (
        _TAX_HT_RE.search(text) is not None
        and "tva" in lowered
        and _TAX_TTC_RE.search(text) is not None
    )
    first_line = next(
        (line for line in str(text).splitlines() if line.strip()), ""
    )
    has_spreadsheet_header = (
        len([part for part in re.split(r"[;,\t|]", first_line) if part]) >= 2
    )
    bank_markers = sum(
        1 for pattern in _BANK_MARKER_PATTERNS.values()
        if pattern.search(lowered)
    )
    return has_tax_triplet or has_spreadsheet_header or bank_markers >= 2


def _collect_signals(path: Path) -> _DocumentSignals:
    try:
        document = pymupdf.open(str(path))
    except Exception:
        return _DocumentSignals()
    try:
        page_count = len(document)
        if page_count <= 0:
            return _DocumentSignals(readable=True, page_count=page_count)
        page = document[0]
        text = page.get_text()[:TEXT_LIMIT]
        word_count = len(text.split())
        table_count = 0
        if not _strong_business_signature(text):
            for page_index in range(min(page_count, MAX_TABLE_PROBE_PAGES)):
                try:
                    table_count += len(
                        document[page_index].find_tables().tables
                    )
                except Exception:
                    continue
        try:
            annotation_count = len(list(page.annots() or []))
        except Exception:
            annotation_count = 0
        rectangle = page.rect
        return _DocumentSignals(
            readable=True,
            page_count=page_count,
            width=float(rectangle.width),
            height=float(rectangle.height),
            text=text,
            word_count=word_count,
            table_count=table_count,
            annotation_count=annotation_count,
        )
    except Exception:
        return _DocumentSignals()
    finally:
        document.close()


def _read_auxiliary_text(path: Path) -> str:
    if path.suffix.lower() not in {".csv", ".ofx", ".qfx", ".txt"}:
        return ""
    try:
        with path.open("r", encoding="utf-8-sig", errors="ignore") as stream:
            return stream.read(TEXT_LIMIT)
    except Exception:
        return ""


def _file_sha256(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except Exception:
        return ""


def reset_route_cache() -> None:
    """Clear the module-level routing verdict cache."""

    _ROUTE_CACHE.clear()


def _route_cache_get(
    key: tuple[str, str],
) -> "RouterVerdict | None":
    verdict = _ROUTE_CACHE.get(key)
    if verdict is not None:
        _ROUTE_CACHE.move_to_end(key)
    return verdict


def _route_cache_put(key: tuple[str, str], verdict: "RouterVerdict") -> None:
    _ROUTE_CACHE[key] = verdict
    _ROUTE_CACHE.move_to_end(key)
    while len(_ROUTE_CACHE) > ROUTE_CACHE_SIZE:
        _ROUTE_CACHE.popitem(last=False)


class DocumentRouter:
    """Route a document to a deterministic extraction plan."""

    def __init__(
        self,
        *,
        profiler: Any | None = None,
        ocr_scorer: Callable[[Any, int], float] | None = None,
        text_threshold: int = 50,
    ) -> None:
        self.profiler = profiler or DocumentProfiler(text_threshold=text_threshold)
        self.ocr_scorer = ocr_scorer

    def _scorer_id(self) -> str:
        if self.ocr_scorer is None:
            return "default"
        return str(id(self.ocr_scorer))

    def route(self, path: str | Path) -> RouterVerdict:
        """Return a routing verdict; internal failures become manual review."""

        try:
            source = Path(path)
            key = (_file_sha256(source), self._scorer_id())
            cached = _route_cache_get(key)
            if cached is not None:
                return copy.deepcopy(cached)
            verdict = self._route(source)
            _route_cache_put(key, copy.deepcopy(verdict))
            return verdict
        except Exception as exc:
            return self._failure_verdict(path, exc)

    def plan_for(
        self, decision: RoutingDecision, profile: DocumentProfile
    ) -> ExtractionPlan:
        """Build the extraction plan matching a routing decision."""

        family = profile.business_family or "unknown"
        if decision.route == "native_text":
            tasks = [("text", "pymupdf_text")]
            if family == "annotated":
                tasks.append(("annotation", "pymupdf_text"))
        elif decision.route == "docling":
            tasks = [
                ("table", "find_tables"),
                ("table", "docling"),
                ("text", "pymupdf_text"),
            ]
        elif decision.route == "local_ocr":
            tasks = [("ocr", "paddleocr"), ("vision", "vision_text")]
        elif decision.route == "local_vision":
            tasks = [("vision", "vision_text")]
        elif decision.route == "spreadsheet":
            tasks = [("table", "pandas")]
        else:
            tasks = [("classification", "pymupdf_text")]

        page_indices = (
            list(range(profile.page_count)) if profile.page_count > 0 else []
        )
        specs = [
            TaskSpec(
                task_id=f"task-{index}",
                kind=kind,
                tool=tool,
                page_indices=page_indices,
                priority=100 - index * 10,
            )
            for index, (kind, tool) in enumerate(tasks)
        ]
        tools = _dedupe([spec.tool for spec in specs])
        return ExtractionPlan(
            routing=decision,
            tasks=specs,
            source_sha256="",
            tools=tools,
            notes=[],
            metadata={"business_family": family},
        )

    def _route(self, source: Path) -> RouterVerdict:
        geometry_report = normalise_geometry(source, mode="light")
        geometry = None if geometry_report.fallback else geometry_report

        try:
            profile = self.profiler.profile(source)
        except Exception:
            profile = _default_profile(source)
        signals = _collect_signals(source)
        auxiliary_text = _read_auxiliary_text(source)
        text = signals.text or auxiliary_text
        profile = self._enrich_profile(profile, source, signals, geometry)

        scores, reasons = self._evaluate_families(source, profile, signals, text)
        family = self._business_family_from_scores(scores)
        profile = profile.model_copy(update={"business_family": family})
        profile, geometry, orientation_reason = self._resolve_orientation(
            source, profile, signals, text, geometry
        )
        if orientation_reason:
            reasons.append(orientation_reason)

        route = self._select_route(source, profile, family, signals)
        if route == "unsupported":
            reasons.append("route:unsupported")

        confidence = self._confidence(family, scores, profile)
        decision = RoutingDecision(
            route=route,
            confidence=confidence,
            reasons=_dedupe(reasons),
            tools=[],
            fallback=route in {"unsupported", "manual_review"},
        )
        plan = self.plan_for(decision, profile)
        decision = decision.model_copy(update={"tools": list(plan.tools)})
        rotation_method = (
            geometry.rotation_method if geometry is not None else "none"
        )
        plan = plan.model_copy(
            update={
                "routing": decision,
                "source_sha256": _file_sha256(source),
                "metadata": {
                    **plan.metadata,
                    "rotation_method": rotation_method,
                },
            }
        )
        return RouterVerdict(profile, decision, plan, geometry)

    def _failure_verdict(self, path: Any, exc: BaseException) -> RouterVerdict:
        code = _router_error_code(exc)
        try:
            source = Path(path)
        except Exception:
            source = None
        profile = _default_profile(source)
        decision = RoutingDecision(
            route="manual_review",
            confidence=0.0,
            reasons=[f"ROUTER_FAILED:{code}"],
            tools=["pymupdf_text"],
            fallback=True,
        )
        task = TaskSpec(
            task_id="task-0",
            kind="classification",
            tool="pymupdf_text",
            page_indices=[],
            priority=100,
        )
        plan = ExtractionPlan(
            routing=decision,
            tasks=[task],
            source_sha256="",
            tools=["pymupdf_text"],
            notes=["router_failure"],
            metadata={"business_family": "unknown", "rotation_method": "none"},
        )
        return RouterVerdict(profile, decision, plan, None)

    def _enrich_profile(
        self,
        profile: DocumentProfile,
        source: Path,
        signals: _DocumentSignals,
        geometry: GeometryReport | None,
    ) -> DocumentProfile:
        updates: dict[str, Any] = {}
        if geometry is not None:
            updates["orientation_degrees"] = geometry.rotation_degrees
        if signals.readable:
            updates["page_count"] = signals.page_count
        suffix = source.suffix.lower()
        if suffix in BANK_SUFFIXES:
            updates["profile_type"] = "bank_statement"
            updates["has_text_layer"] = True
        elif profile.profile_type == "unknown" and suffix in SPREADSHEET_SUFFIXES:
            updates["profile_type"] = "spreadsheet"
        return profile.model_copy(update=updates)

    def _evaluate_families(
        self,
        path: Path,
        profile: DocumentProfile,
        signals: _DocumentSignals,
        text: str,
    ) -> tuple[dict[str, float], list[str]]:
        reasons: list[str] = []
        if profile.profile_type:
            reasons.append(f"profile:{profile.profile_type}")
        if profile.word_count > 0:
            reasons.append(f"text_layer:{profile.word_count}_words")
        elif profile.has_text_layer:
            reasons.append("text_layer:present")
        if signals.table_count > 0:
            reasons.append(f"tables:{signals.table_count}")
        if signals.annotation_count > 0:
            reasons.append(f"annotations:{signals.annotation_count}")
        if signals.width > 0 and signals.height > 0:
            reasons.append(f"page:{signals.width:.0f}x{signals.height:.0f}")

        bank_score, bank_reasons = self._bank_signals(path, text)
        ledger_score, ledger_reasons = self._ledger_signals(path, text)
        annotated_score, annotated_reasons = self._annotated_signal_values(signals)
        receipt_score, receipt_reasons = self._receipt_signals(
            signals, profile, text
        )
        invoice_score, invoice_reasons = self._invoice_signals(text)
        table_score, table_reasons = self._complex_table_signals(profile, signals)

        reasons.extend(bank_reasons)
        reasons.extend(ledger_reasons)
        reasons.extend(annotated_reasons)
        reasons.extend(receipt_reasons)
        reasons.extend(invoice_reasons)
        reasons.extend(table_reasons)

        scores = {
            "bank_statement": bank_score,
            "ledger": ledger_score,
            "annotated": annotated_score,
            "receipt": receipt_score,
            "invoice": invoice_score,
            "complex_table": table_score,
        }
        for family in FAMILY_PRIORITY:
            if scores[family] > 0:
                reasons.append(f"{family}_score:{scores[family]:.2f}")
        return scores, reasons

    def _business_family_from_scores(self, scores: dict[str, float]) -> str:
        for family in FAMILY_PRIORITY:
            if scores.get(family, 0.0) >= FAMILY_THRESHOLDS[family]:
                return family
        return "unknown"

    def _business_family(
        self, path: Path, profile: DocumentProfile, text: str
    ) -> str:
        signals = _collect_signals(path)
        scores, _ = self._evaluate_families(path, profile, signals, text)
        return self._business_family_from_scores(scores)

    def _bank_signals(self, path: Path, text: str) -> tuple[float, list[str]]:
        suffix = path.suffix.lower()
        if suffix in BANK_SUFFIXES:
            return 1.0, [f"bank_suffix:{suffix.lstrip('.')}"]
        lowered = text.casefold()
        markers = [
            marker
            for marker, pattern in _BANK_MARKER_PATTERNS.items()
            if pattern.search(lowered)
        ]
        markers = _dedupe(markers)
        if suffix in SPREADSHEET_SUFFIXES and len(markers) >= 2:
            score = min(0.7 + 0.1 * len(markers), 1.0)
            return score, [f"bank_markers:{','.join(markers)}"]
        if len(markers) >= 3:
            score = min(0.7 + 0.1 * len(markers), 1.0)
            return score, [f"bank_markers:{','.join(markers)}"]
        return 0.0, []

    def _ledger_signals(self, path: Path, text: str) -> tuple[float, list[str]]:
        suffix = path.suffix.lower()
        if suffix not in SPREADSHEET_SUFFIXES:
            return 0.0, []
        header = next(
            (line for line in text.splitlines() if line.strip()), ""
        )
        columns = [column for column in re.split(r"[;,\t|]", header) if column]
        if len(columns) >= 2:
            return 0.8, [f"spreadsheet_header:{len(columns)}_columns"]
        return 0.6, [f"spreadsheet_extension:{suffix.lstrip('.')}"]

    def _receipt_signals(
        self,
        signals: _DocumentSignals,
        profile: DocumentProfile,
        text: str,
    ) -> tuple[float, list[str]]:
        if signals.width <= 0 or signals.height <= 0:
            return 0.0, []
        ratio = signals.height / signals.width
        words = profile.word_count or signals.word_count or len(text.split())
        if ratio <= RECEIPT_MIN_RATIO or words >= RECEIPT_MAX_WORDS:
            return 0.0, []
        upper = text.upper()
        markers = [
            marker for marker in ("TICKET", "TVA", "TOTAL") if marker in upper
        ]
        if not markers:
            return 0.0, []
        if _INVOICE_WORD_RE.search(text) or _SUPPLIER_RE.search(text):
            return 0.0, []
        if _INVOICE_NUMBER_RE.search(text):
            return 0.0, []
        return 0.82, [
            f"receipt_narrow:{ratio:.2f}",
            f"receipt_words:{words}",
            f"receipt_markers:{','.join(markers)}",
        ]

    def _invoice_signals(self, text: str) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []
        if _INVOICE_WORD_RE.search(text):
            score += 0.35
            reasons.append("invoice_keyword:facture")
        has_ht = _TAX_HT_RE.search(text) is not None
        has_tva = "tva" in text.casefold()
        has_ttc = _TAX_TTC_RE.search(text) is not None
        if has_ht and has_tva and has_ttc:
            score += 0.3
            reasons.append("invoice_tax_triplet:ht_tva_ttc")
        number = _INVOICE_NUMBER_RE.search(text)
        if number is not None:
            score += 0.2
            reasons.append(f"invoice_number:{number.group(1)}")
        if _SUPPLIER_RE.search(text):
            score += 0.15
            reasons.append("invoice_supplier_block")
        return min(score, 1.0), reasons

    def _annotated_signals(self, path: Path) -> int:
        try:
            document = pymupdf.open(str(path))
        except Exception:
            return 0
        try:
            if len(document) <= 0:
                return 0
            return len(list(document[0].annots() or []))
        except Exception:
            return 0
        finally:
            document.close()

    def _annotated_signal_values(
        self, signals: _DocumentSignals
    ) -> tuple[float, list[str]]:
        if signals.annotation_count > 0:
            return 1.0, [f"annotations:{signals.annotation_count}"]
        return 0.0, []

    def _complex_table_signals(
        self, profile: DocumentProfile, signals: _DocumentSignals
    ) -> tuple[float, list[str]]:
        if signals.table_count >= 2:
            return min(0.6 + 0.1 * signals.table_count, 1.0), [
                f"tables:{signals.table_count}"
            ]
        if signals.table_count == 1 or profile.has_tables:
            return 0.5, [f"tables:{max(signals.table_count, 1)}"]
        return 0.0, []

    def _resolve_orientation(
        self,
        path: Path,
        profile: DocumentProfile,
        signals: _DocumentSignals,
        text: str,
        geometry: GeometryReport | None,
    ) -> tuple[DocumentProfile, GeometryReport | None, str | None]:
        if profile.profile_type in {"scanned_pdf", "image"} and not text.strip():
            if self.ocr_scorer is None:
                result = AutoRotateResult(0, 0.0, "abstain:no_native_text")
            else:
                result = self._score_orientation(path)
            profile = profile.model_copy(
                update={"orientation_degrees": result.angle}
            )
            if result.method.startswith("abstain"):
                reason = "orientation:abstain:no_native_text"
            elif result.method.startswith("fallback"):
                reason = f"orientation:{result.method}"
            else:
                reason = f"rotation:{result.angle}cw:{result.method}"
            if geometry is not None:
                geometry = replace(
                    geometry,
                    rotation_degrees=result.angle,
                    rotation_confidence=result.confidence,
                    rotation_method=result.method,
                )
            return profile, geometry, reason
        if geometry is not None and geometry.rotation_degrees:
            profile = profile.model_copy(
                update={"orientation_degrees": geometry.rotation_degrees}
            )
            reason = (
                f"rotation:{geometry.rotation_degrees}cw:"
                f"{geometry.rotation_method}"
            )
            return profile, geometry, reason
        return profile, geometry, None

    def _score_orientation(self, path: Path) -> AutoRotateResult:
        try:
            document = pymupdf.open(str(path))
        except Exception as exc:
            return AutoRotateResult(
                0, 0.0, f"fallback:{_router_error_code(exc)}"
            )
        try:
            if len(document) <= 0:
                return AutoRotateResult(0, 0.0, "fallback:EMPTY_DOCUMENT")
            return auto_rotate_page(document[0], scorer=self.ocr_scorer)
        finally:
            document.close()

    def _select_route(
        self,
        source: Path,
        profile: DocumentProfile,
        family: str,
        signals: _DocumentSignals,
    ) -> str:
        if not source.is_file():
            return "unsupported"
        if family in {"bank_statement", "ledger"}:
            return "spreadsheet"
        if profile.profile_type == "spreadsheet":
            return "spreadsheet"
        if profile.profile_type in HANDWRITING_PROFILES:
            return "local_vision"
        if not signals.readable or signals.page_count <= 0:
            return "unsupported"
        if profile.profile_type == "complex_pdf":
            if family == "invoice" and profile.has_text_layer:
                return "native_text"
            return "docling"
        if profile.profile_type == "native_pdf":
            if not profile.has_text_layer:
                return "local_ocr"
            if family == "complex_table":
                return "docling"
            return "native_text"
        if profile.profile_type in {"scanned_pdf", "image"}:
            return "local_ocr"
        if profile.has_text_layer:
            return "native_text"
        if profile.is_scanned:
            return "local_ocr"
        return "unsupported"

    def _confidence(
        self,
        family: str,
        scores: dict[str, float],
        profile: DocumentProfile,
    ) -> float:
        if family != "unknown":
            value = scores.get(family, 0.0)
        elif profile.profile_type in {"scanned_pdf", "image"}:
            value = 0.4
        elif profile.profile_type == "native_pdf" and profile.has_text_layer:
            value = 0.5
        else:
            value = 0.0
        return round(min(max(value, 0.0), 1.0), 4)
