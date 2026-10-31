"""Batch audit of the invoice corpus through the official DSH-Core pipeline.

The script scans ``workspace/invoices/input`` (or ``--input``), runs every file
through the real LangGraph orchestration (producer -> ``InvoiceSchema`` ->
``RuleRegistryService`` -> routing) without persistence side effects, and writes
reproducible audit reports: manifest, JSONL records, summary and Markdown report.

The audit classifies each invoice as ``VALIDATED``, ``HITL`` or ``REJECTED``,
attributes the Obsidian rule outcome and records extraction metadata together
with semantic blocking points.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import platform
import re
import statistics
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml

from backend.app.graphs.invoice_graph import (
    IMAGE_SUFFIXES,
    PDF_SUFFIXES,
    run_invoice_graph,
)
from schemas.invoice_schema import InvoiceSchema
from schemas.rule_registry_schema import BusinessRuleModel
from model_router import resolve_escalation_budget_ratio
from services.cloud_vision_extractor import CloudEscalationBudget, budget_max
from services.document_engine import (
    CloudDocumentEngine,
    HybridDocumentEngine,
    LocalDocumentEngine,
)
from services.document_pipeline import (
    DEFAULT_PIPELINE_ENV,
    is_pipeline_enabled,
    reset_perception_cache,
    set_default_engine,
)
from services.rule_registry_service import RuleRegistryError, RuleRegistryService
from services.tva_zero_rule_evaluator import DEFAULT_RULES_DIR, RuleDecision

DEFAULT_INPUT_DIR = REPO_ROOT / "workspace" / "invoices" / "input"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "workspace" / "invoices" / "audit"
SUPPORTED_SUFFIXES = PDF_SUFFIXES | IMAGE_SUFFIXES
TARGET_RULE_ID = "RULE-TVA-ZERO-001"
VISION_MODES = ("none", "hybrid", "cloud", "ollama")
BASELINE_CONTRACT_VERSION = "1.0.1"
DEFAULT_BASELINE_TESTS_PASSED = 1321
DEFAULT_CONTRACT_PATH = REPO_ROOT / "docs" / "DOCUMENT_PERCEPTION_CONTRACT.md"

VALIDATED = "VALIDATED"
HITL = "HITL"
REJECTED = "REJECTED"

logger = logging.getLogger("DSH-Core.BatchAudit")

_FRONTMATTER = re.compile(
    r"\A---[ \t]*\r?\n(?P<body>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)",
    re.DOTALL,
)

_FIELD_CODES = {
    "supplier_name": "SUPPLIER_UNKNOWN",
    "siret": "MISSING_SIRET",
    "vat_number": "MISSING_VAT_NUMBER",
    "country": "MISSING_COUNTRY",
    "invoice_number": "MISSING_INVOICE_NUMBER",
    "issue_date": "MISSING_ISSUE_DATE",
    "net_amount": "MISSING_NET_AMOUNT",
    "tax_amount": "MISSING_TAX_AMOUNT",
    "gross_amount": "MISSING_GROSS_AMOUNT",
}

_RULE_OUTCOMES = {
    "RULE_NOT_TRIGGERED": "NOT_TRIGGERED",
    "ZERO_VAT_DISAMBIGUATED": "TRIGGERED_RESOLVED",
    "SUPPLIER_REGIME_APPLIED": "TRIGGERED_RESOLVED",
    "AMBIGUOUS_ZERO_VAT": "TRIGGERED_BLOCKED",
    "RULE_NOT_APPROVED": "RULE_MISSING",
    "RULE_NOT_APPLICABLE_TO_NODE": "RULE_MISSING",
    "ACTION_NOT_FOUND": "RULE_MISSING",
    "INVOICE_NOT_VALIDATED": "RULE_EVALUATION_SKIPPED",
    "TAX_AMOUNT_UNKNOWN": "RULE_EVALUATION_SKIPPED",
}

_CODE_LAYERS = {
    "SOURCE_NOT_FOUND": ("production", "blocker"),
    "EXTRACTION_FAILED": ("production", "blocker"),
    "VISION_EXTRACTION_FAILED": ("production", "blocker"),
    "SCANNED_PDF_REQUIRES_VISION": ("production", "blocker"),
    "OCR_TEXT_UNAVAILABLE": ("production", "blocker"),
    "INVALID_PRODUCER_PAYLOAD": ("validation", "blocker"),
    "INVOICE_NOT_PRODUCED": ("production", "blocker"),
    "INVOICE_NOT_VALIDATED": ("rule", "blocker"),
    "AMBIGUOUS_ZERO_VAT": ("rule", "blocker"),
    "RULE_NOT_APPROVED": ("rule", "blocker"),
    "RULE_NOT_APPLICABLE_TO_NODE": ("rule", "blocker"),
    "ACTION_NOT_FOUND": ("rule", "blocker"),
    "UNSUPPORTED_FILE_TYPE": ("triage", "blocker"),
    "AUDIT_EXCEPTION": ("run", "blocker"),
    "TAX_AMOUNT_UNKNOWN": ("rule", "blocker"),
    "ESCALATION_BUDGET_EXCEEDED": ("vision", "blocker"),
}

_NOTE_LAYERS = {
    "IDENTITY_CONFLICT": ("supplier_memory", "warning"),
}


class BatchAuditError(RuntimeError):
    """Raised when the audit cannot run or produce a trustworthy report."""

    def __init__(self, code: str, message: str = "") -> None:
        detail = f"{code}: {message}" if message else code
        super().__init__(detail)
        self.code = code


@dataclass(frozen=True)
class CorpusFile:
    path: Path
    name: str
    suffix: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class RuleContext:
    rule: BusinessRuleModel | None
    source_file: str | None


@dataclass(frozen=True)
class AuditRecord:
    seq: int
    run_id: str
    source: dict[str, Any]
    triage: dict[str, Any]
    extraction: dict[str, Any]
    perception: dict[str, Any]
    rule: dict[str, Any]
    decision: dict[str, Any]
    blocking_points: list[dict[str, Any]]
    timing_ms: dict[str, int]
    status: str
    exception: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "run_id": self.run_id,
            "source": self.source,
            "triage": self.triage,
            "extraction": self.extraction,
            "perception": self.perception,
            "rule": self.rule,
            "decision": self.decision,
            "blocking_points": self.blocking_points,
            "timing_ms": self.timing_ms,
            "status": self.status,
            "exception": self.exception,
        }


@dataclass(frozen=True)
class BatchAuditResult:
    run_id: str
    run_dir: Path
    records: list[dict[str, Any]]
    summary: dict[str, Any]
    manifest: dict[str, Any]
    exit_code: int


class CorpusScanner:
    """Deterministic inventory of the invoice corpus with SHA-256 fingerprints."""

    def __init__(self, root: Path, *, pattern: str = "*") -> None:
        self.root = Path(root)
        self.pattern = pattern

    def scan(self) -> list[CorpusFile]:
        if not self.root.is_dir():
            raise BatchAuditError("INPUT_DIRECTORY_NOT_FOUND", str(self.root))
        paths = sorted(
            (path for path in self.root.glob(self.pattern) if path.is_file()),
            key=lambda path: (path.name.casefold(), path.name),
        )
        return [
            CorpusFile(
                path=path,
                name=path.name,
                suffix=path.suffix.lower(),
                size_bytes=path.stat().st_size,
                sha256=_sha256_file(path),
            )
            for path in paths
        ]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def _scan_rule_files(rules_dir: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(Path(rules_dir).rglob("*.md")):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        match = _FRONTMATTER.match(text.lstrip())
        rule_id: str | None = None
        raw_status: Any = None
        has_frontmatter = match is not None
        if match is not None:
            try:
                data = yaml.safe_load(match.group("body"))
            except yaml.YAMLError:
                data = None
            if isinstance(data, Mapping):
                candidate = data.get("rule_id")
                if isinstance(candidate, str):
                    rule_id = candidate
                raw_status = data.get("status")
        entries.append(
            {
                "file": path.relative_to(rules_dir).as_posix(),
                "sha256": _sha256_file(path),
                "rule_id": rule_id,
                "raw_status": raw_status,
                "has_frontmatter": has_frontmatter,
            }
        )
    return entries


def load_rule_context(rules_dir: Path) -> tuple[dict[str, Any], RuleContext]:
    """Load the approved registry and snapshot every Markdown rule file."""

    registry = RuleRegistryService()
    approved_rules = registry.load_rules(Path(rules_dir))
    files = _scan_rule_files(Path(rules_dir))
    by_id = {rule.rule_id: rule for rule in approved_rules}
    file_by_id: dict[str, str] = {}
    for entry in files:
        rule_id = entry.get("rule_id")
        if isinstance(rule_id, str):
            file_by_id.setdefault(rule_id, entry["file"])
    entry_by_file = {entry["file"]: entry for entry in files}

    approved = []
    for rule in approved_rules:
        relative = file_by_id.get(rule.rule_id)
        entry = entry_by_file.get(relative or "")
        approved.append(
            {
                "rule_id": rule.rule_id,
                "version": rule.version,
                "file": relative,
                "sha256": entry["sha256"] if entry else None,
                "status": rule.status.value,
            }
        )
    ignored = [
        {"file": entry["file"], "raw_status": entry["raw_status"]}
        for entry in files
        if entry["has_frontmatter"] and entry.get("raw_status") != "approved"
    ]
    snapshot = {
        "directory": str(rules_dir),
        "files": files,
        "approved": approved,
        "ignored": ignored,
    }
    context = RuleContext(
        rule=by_id.get(TARGET_RULE_ID),
        source_file=file_by_id.get(TARGET_RULE_ID),
    )
    return snapshot, context


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "unknown"


def _pymupdf_version() -> str | None:
    try:
        import pymupdf
    except Exception:
        return None
    return getattr(pymupdf, "__version__", None)


def _environment() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "pymupdf": _pymupdf_version(),
        "git_commit": _git_commit(),
    }


def build_vision_extractor(
    mode: str, budget: CloudEscalationBudget | None = None
) -> Any:
    if mode == "none":
        return None
    if mode == "hybrid":
        from services.cloud_vision_extractor import HybridVisionExtractor

        return HybridVisionExtractor(budget=budget)
    if mode == "cloud":
        from services.cloud_vision_extractor import CloudVisionExtractor

        return CloudVisionExtractor()
    if mode == "ollama":
        from services.ollama_vision_extractor import OllamaVisionExtractor

        return OllamaVisionExtractor()
    raise BatchAuditError("INVALID_VISION_MODE", mode)


def classify_decision(state: Mapping[str, Any]) -> str:
    commit_status = str(state.get("commit_status") or "")
    if commit_status == "COMMITTED":
        return VALIDATED
    if commit_status == "PENDING_HITL":
        return HITL
    return REJECTED


def rule_outcome_for(decision: RuleDecision | None) -> str:
    if decision is None:
        return "NO_RULE_REACHED"
    return _RULE_OUTCOMES.get(decision.reason, "UNKNOWN")


def _triage_file_type(suffix: str) -> str:
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in PDF_SUFFIXES:
        return "pdf"
    return "unknown"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    return value


def _resolve_field(invoice: InvoiceSchema, field_path: str) -> Any:
    current: Any = invoice.model_dump(mode="python")
    for part in field_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            current = None
            break
        current = current[part]
    if current is None and field_path == "supplier.country":
        return invoice.metadata.get("supplier_country")
    return current


def _extraction_block(state: Mapping[str, Any]) -> dict[str, Any]:
    invoice = state.get("invoice")
    payload = state.get("extracted_data") or state.get("producer_result") or {}
    if not isinstance(payload, Mapping):
        payload = {}
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, Mapping):
        metadata = {}

    if isinstance(invoice, InvoiceSchema):
        supplier = invoice.supplier
        fields: dict[str, Any] = {
            "supplier_name": supplier.name if supplier else None,
            "siret": supplier.siret if supplier else None,
            "vat_number": supplier.vat_number if supplier else None,
            "country": invoice.metadata.get("supplier_country"),
            "invoice_number": invoice.reference.invoice_number,
            "issue_date": (
                invoice.reference.issue_date.isoformat()
                if invoice.reference.issue_date
                else None
            ),
            "net_amount": (
                str(invoice.amount.net_amount)
                if invoice.amount.net_amount is not None
                else None
            ),
            "tax_amount": (
                str(invoice.amount.tax_amount)
                if invoice.amount.tax_amount is not None
                else None
            ),
            "gross_amount": str(invoice.amount.gross_amount),
        }
    elif payload:
        supplier = payload.get("supplier")
        reference = payload.get("reference")
        amount = payload.get("amount")
        supplier = supplier if isinstance(supplier, Mapping) else {}
        reference = reference if isinstance(reference, Mapping) else {}
        amount = amount if isinstance(amount, Mapping) else {}
        fields = {
            "supplier_name": supplier.get("name"),
            "siret": supplier.get("siret"),
            "vat_number": supplier.get("vat_number"),
            "country": metadata.get("supplier_country"),
            "invoice_number": reference.get("invoice_number"),
            "issue_date": reference.get("issue_date"),
            "net_amount": amount.get("net_amount"),
            "tax_amount": amount.get("tax_amount"),
            "gross_amount": amount.get("gross_amount"),
        }
    else:
        fields = {key: None for key in _FIELD_CODES}

    has_source = bool(payload) or isinstance(invoice, InvoiceSchema)
    missing = _missing_fields(fields) if has_source else []
    confidence = invoice.confidence_score if isinstance(invoice, InvoiceSchema) else None
    if confidence is None:
        confidence = state.get("confidence_score")
    warnings = (
        list(invoice.extraction_warnings)
        if isinstance(invoice, InvoiceSchema)
        else []
    )
    return {
        "tool": metadata.get("extraction_tool"),
        "producer": metadata.get("producer"),
        "fallback_reason": metadata.get("fallback_reason"),
        "confidence_score": confidence,
        "notes": warnings,
        "payload_metadata": dict(metadata),
        "fields": fields,
        "missing_fields": missing,
        "supplier_memory": (
            state.get("supplier_memory") or metadata.get("supplier_memory")
        ),
    }


def _perception_block(state: Mapping[str, Any]) -> dict[str, Any]:
    perception = state.get("perception")
    routing = state.get("routing")
    routing = routing if isinstance(routing, Mapping) else {}
    if not isinstance(perception, Mapping):
        return {
            "profile_type": None,
            "route": routing.get("route"),
            "business_family": routing.get("business_family"),
            "rotation_applied": 0,
            "annotations_count": 0,
            "annotations_by_kind": {},
            "tools_used": [],
            "failed": False,
            "notes": [],
            "fast_path": False,
        }

    profile = perception.get("profile")
    profile = profile if isinstance(profile, Mapping) else {}
    metadata = perception.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    pages = perception.get("pages")
    pages = pages if isinstance(pages, list) else []
    annotations_by_kind: Counter[str] = Counter()
    annotations_count = 0
    rotation = metadata.get("rotation_applied")
    if not isinstance(rotation, int):
        rotation = 0
        for page in pages:
            if not isinstance(page, Mapping):
                continue
            page_rotation = page.get("rotation_applied")
            if isinstance(page_rotation, int):
                rotation = max(rotation, page_rotation)
    for page in pages:
        if not isinstance(page, Mapping):
            continue
        annotations = page.get("annotations")
        if not isinstance(annotations, list):
            continue
        for annotation in annotations:
            if not isinstance(annotation, Mapping):
                continue
            annotations_count += 1
            annotations_by_kind[str(annotation.get("kind") or "unknown")] += 1
    notes = perception.get("processing_notes")
    notes = notes if isinstance(notes, list) else []
    failed = any(
        str(note).startswith("PERCEPTION_FAILED:") for note in notes
    )
    tools = perception.get("tools_used")
    notes = [str(note) for note in notes]
    return {
        "profile_type": profile.get("profile_type"),
        "route": metadata.get("routing_route") or routing.get("route"),
        "business_family": (
            metadata.get("routing_family") or routing.get("business_family")
        ),
        "rotation_applied": rotation,
        "annotations_count": annotations_count,
        "annotations_by_kind": dict(sorted(annotations_by_kind.items())),
        "tools_used": list(tools) if isinstance(tools, list) else [],
        "failed": failed,
        "notes": notes,
        "fast_path": "geometry_fast_path" in notes,
    }


def _missing_fields(fields: Mapping[str, Any]) -> list[str]:
    missing: list[str] = []
    for key in _FIELD_CODES:
        value = fields.get(key)
        if key == "supplier_name":
            text = "" if value is None else str(value).strip().upper()
            if text in {"", "FOURNISSEUR_INCONNU"}:
                missing.append(key)
        elif value is None or (isinstance(value, str) and not value.strip()):
            missing.append(key)
    return missing


def _point(code: str, layer: str, severity: str, detail: str) -> dict[str, str]:
    return {"code": code, "layer": layer, "severity": severity, "detail": detail}


def _extraction_points(extraction: Mapping[str, Any]) -> list[dict[str, str]]:
    points: list[dict[str, str]] = []
    for key in extraction.get("missing_fields", []):
        code = _FIELD_CODES[key]
        points.append(_point(code, "extraction", "warning", f"champ absent: {key}"))
    fallback = extraction.get("fallback_reason")
    if fallback == "no_text_layer":
        points.append(
            _point(
                "NO_TEXT_LAYER",
                "production",
                "warning",
                "couche texte absente, production via vision",
            )
        )
    if fallback == "local_vision_failed_escalated_to_cloud":
        points.append(
            _point(
                "ESCALATED_TO_CLOUD",
                "vision",
                "info",
                "vision locale échouée, escalade cloud",
            )
        )
    for note in extraction.get("notes", []):
        code = str(note).split(":", 1)[0].strip()
        if code in _NOTE_LAYERS:
            layer, severity = _NOTE_LAYERS[code]
            points.append(_point(code, layer, severity, str(note)))
    fields = extraction.get("fields", {})
    values = [fields.get(key) for key in ("net_amount", "tax_amount", "gross_amount")]
    if all(value is not None for value in values):
        try:
            net, tax, gross = (Decimal(str(value)) for value in values)
            difference = abs(net + tax - gross)
        except (InvalidOperation, ValueError):
            difference = None
        if difference is not None and difference > Decimal("0.02"):
            points.append(
                _point(
                    "TOTALS_INCOHERENT",
                    "extraction",
                    "warning",
                    f"écart HT+TVA-TTC={difference}",
                )
            )
    return points


def _dedupe_points(points: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for point in points:
        code = str(point.get("code"))
        if code not in deduped:
            deduped[code] = dict(point)
    return list(deduped.values())


def _blocking_points(
    state: Mapping[str, Any],
    decision_status: str,
    suffix: str,
    extraction: Mapping[str, Any],
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    error_message = str(state.get("error_message") or "").strip()
    error_code = error_message.split(":", 1)[0].strip()
    if error_code in _CODE_LAYERS:
        layer, severity = _CODE_LAYERS[error_code]
        points.append(_point(error_code, layer, severity, error_message))
    if (
        "ESCALATION_BUDGET_EXCEEDED" in error_message
        and error_code != "ESCALATION_BUDGET_EXCEEDED"
    ):
        points.append(
            _point(
                "ESCALATION_BUDGET_EXCEEDED",
                "vision",
                "blocker",
                error_message,
            )
        )

    if decision_status == REJECTED and suffix not in SUPPORTED_SUFFIXES:
        points.append(
            _point(
                "UNSUPPORTED_FILE_TYPE",
                "triage",
                "blocker",
                f"suffixe non supporté: {suffix or '<inconnu>'}",
            )
        )

    points.extend(_extraction_points(extraction))

    decision = state.get("rule_decision")
    if isinstance(decision, RuleDecision) and decision.reason in _CODE_LAYERS:
        layer, severity = _CODE_LAYERS[decision.reason]
        if decision.reason != error_code:
            detail = "; ".join(decision.details) or decision.reason
            points.append(_point(decision.reason, layer, severity, detail))
    return _dedupe_points(points)


def _rule_block(state: Mapping[str, Any], context: RuleContext) -> dict[str, Any]:
    decision = state.get("rule_decision")
    invoice = state.get("invoice")
    outcome = rule_outcome_for(decision if isinstance(decision, RuleDecision) else None)
    block: dict[str, Any] = {
        "rule_id": None,
        "rule_version": None,
        "source_file": context.source_file,
        "outcome": outcome,
        "reason": None,
        "conditions": [],
        "vat_regime": None,
        "include_in_vat_balance": None,
        "accounting_treatment": None,
        "details": [],
    }
    if isinstance(decision, RuleDecision):
        block.update(
            {
                "rule_id": decision.rule_id,
                "rule_version": decision.rule_version,
                "reason": decision.reason,
                "vat_regime": decision.vat_regime,
                "include_in_vat_balance": decision.include_in_vat_balance,
                "accounting_treatment": decision.accounting_treatment,
                "details": list(decision.details),
            }
        )
    if outcome == "RULE_EVALUATION_SKIPPED" and not block["rule_id"] and context.rule:
        block["rule_id"] = context.rule.rule_id
        block["rule_version"] = context.rule.version
    if context.rule is not None and isinstance(invoice, InvoiceSchema):
        block["conditions"] = [
            {
                "field": condition.field,
                "operator": condition.operator,
                "value": _jsonable(condition.value),
                "actual": _jsonable(_resolve_field(invoice, condition.field)),
            }
            for condition in context.rule.conditions
        ]
    return block


def audit_invoice(
    corpus_file: CorpusFile,
    *,
    run_id: str,
    seq: int,
    rules_dir: Path,
    rule_context: RuleContext,
    vision_extractor: Any | None = None,
    simulate_zero_vat: bool = False,
) -> AuditRecord:
    started = time.perf_counter()
    state: Mapping[str, Any] = {}
    exception_text: str | None = None
    try:
        state = run_invoice_graph(
            corpus_file.path,
            rules_dir=rules_dir,
            vision_extractor=vision_extractor,
            simulate_zero_vat=simulate_zero_vat,
            persistence_manager=None,
            thread_id=f"audit-{corpus_file.sha256[:12]}",
        )
    except Exception as exc:
        exception_text = f"{type(exc).__name__}: {exc}"
    elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))

    if exception_text is not None:
        decision_status = REJECTED
        state = {}
    else:
        decision_status = classify_decision(state)

    file_type = state.get("file_type") or _triage_file_type(corpus_file.suffix)
    extraction = _extraction_block(state)
    perception = _perception_block(state)
    rule = _rule_block(state, rule_context)
    blocking_points = _blocking_points(
        state, decision_status, corpus_file.suffix, extraction
    )
    if exception_text is not None:
        blocking_points = _dedupe_points(
            blocking_points
            + [_point("AUDIT_EXCEPTION", "run", "blocker", exception_text)]
        )

    return AuditRecord(
        seq=seq,
        run_id=run_id,
        source={
            "path": str(corpus_file.path),
            "name": corpus_file.name,
            "sha256": corpus_file.sha256,
            "size_bytes": corpus_file.size_bytes,
            "suffix": corpus_file.suffix,
        },
        triage={"file_type": file_type},
        extraction=extraction,
        perception=perception,
        rule=rule,
        decision={
            "status": decision_status,
            "graph_commit_status": str(state.get("commit_status") or ""),
            "hitl_required": bool(state.get("hitl_required")),
            "error_message": str(state.get("error_message") or ""),
        },
        blocking_points=blocking_points,
        timing_ms={"total": elapsed_ms},
        status="error" if exception_text is not None else "ok",
        exception=exception_text,
    )


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _generate_run_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{_git_commit()}"


def _read_records(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _prepare_run_dir(
    output_dir: Path, run_id: str | None, resume: bool
) -> tuple[Path, str, list[dict[str, Any]]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if run_id is not None:
        if Path(run_id).name != run_id or run_id in {".", ".."}:
            raise BatchAuditError("INVALID_RUN_ID", run_id)
        run_dir = output_dir / run_id
        if run_dir.exists() and not resume:
            raise BatchAuditError("RUN_DIRECTORY_EXISTS", str(run_dir))
        run_dir.mkdir(parents=True, exist_ok=True)
        records = _read_records(run_dir / "records.jsonl") if resume else []
        return run_dir, run_id, records

    if resume:
        candidates = sorted(
            (
                path
                for path in output_dir.iterdir()
                if path.is_dir()
                and (
                    (path / "manifest.json").is_file()
                    or (path / "records.jsonl").is_file()
                )
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            chosen = candidates[0]
            return chosen, chosen.name, _read_records(chosen / "records.jsonl")

    base = _generate_run_id()
    candidate = output_dir / base
    suffix = 2
    while candidate.exists():
        candidate = output_dir / f"{base}-{suffix}"
        suffix += 1
    candidate.mkdir()
    return candidate, candidate.name, []


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _append_record(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def _summary_from_records(
    run_id: str,
    run_dir: Path,
    records: Sequence[Mapping[str, Any]],
    *,
    perception_enabled: bool = True,
) -> dict[str, Any]:
    decisions = {VALIDATED: 0, HITL: 0, REJECTED: 0}
    outcome_keys = sorted(
        set(_RULE_OUTCOMES.values()) | {"NO_RULE_REACHED", "UNKNOWN"}
    )
    rule_outcomes = {key: 0 for key in outcome_keys}
    blockers: dict[str, dict[str, Any]] = {}
    file_types: Counter[str] = Counter()
    suppliers: Counter[str] = Counter()
    coverage: Counter[str] = Counter()
    durations: list[int] = []
    records_with_blockers = 0
    cloud_escalations = 0
    perception_profiles: Counter[str] = Counter()
    perception_routes: Counter[str] = Counter()
    perception_families: Counter[str] = Counter()
    perception_rotations: Counter[str] = Counter()
    perception_annotations: Counter[str] = Counter()
    perception_annotations_total = 0
    perception_failures = 0
    perception_fast_path = 0

    for record in records:
        decision_status = str(record.get("decision", {}).get("status"))
        decisions[decision_status] = decisions.get(decision_status, 0) + 1
        outcome = str(record.get("rule", {}).get("outcome"))
        rule_outcomes[outcome] = rule_outcomes.get(outcome, 0) + 1
        points = record.get("blocking_points", [])
        if points:
            records_with_blockers += 1
        if any(str(point.get("code")) == "ESCALATED_TO_CLOUD" for point in points):
            cloud_escalations += 1
        for point in points:
            code = str(point.get("code"))
            entry = blockers.setdefault(
                code,
                {
                    "code": code,
                    "layer": point.get("layer"),
                    "severity": point.get("severity"),
                    "count": 0,
                },
            )
            entry["count"] += 1
        file_types[str(record.get("triage", {}).get("file_type"))] += 1
        extraction = record.get("extraction", {})
        fields = extraction.get("fields", {})
        missing = set(extraction.get("missing_fields", []))
        name = fields.get("supplier_name")
        if name and str(name).strip().upper() != "FOURNISSEUR_INCONNU":
            suppliers[str(name)] += 1
        for key in _FIELD_CODES:
            if key not in missing and fields.get(key) is not None:
                coverage[key] += 1
        durations.append(int(record.get("timing_ms", {}).get("total", 0)))
        perception = record.get("perception")
        if isinstance(perception, Mapping):
            profile_type = perception.get("profile_type")
            if profile_type is not None:
                perception_profiles[str(profile_type)] += 1
            route = perception.get("route")
            if route is not None:
                perception_routes[str(route)] += 1
            family = perception.get("business_family")
            if family is not None:
                perception_families[str(family)] += 1
            rotation = perception.get("rotation_applied")
            rotation_value = rotation if isinstance(rotation, int) else 0
            perception_rotations[str(rotation_value)] += 1
            annotation_count = perception.get("annotations_count")
            if isinstance(annotation_count, int):
                perception_annotations_total += annotation_count
            annotations_by_kind = perception.get("annotations_by_kind")
            if isinstance(annotations_by_kind, Mapping):
                for kind, count in annotations_by_kind.items():
                    if isinstance(count, int):
                        perception_annotations[str(kind)] += count
            if perception.get("failed"):
                perception_failures += 1
            if perception.get("fast_path"):
                perception_fast_path += 1

    total = len(records)
    field_coverage = {
        key: {
            "present": coverage[key],
            "total": total,
            "ratio": round(coverage[key] / total, 3) if total else 0.0,
        }
        for key in _FIELD_CODES
    }
    if durations:
        timing = {
            "min": min(durations),
            "median": int(statistics.median(durations)),
            "max": max(durations),
            "total": sum(durations),
        }
    else:
        timing = {"min": 0, "median": 0, "max": 0, "total": 0}
    escalation_ratio = round(cloud_escalations / total, 3) if total else 0.0
    return {
        "run_id": run_id,
        "output_dir": str(run_dir),
        "generated_at": _now(),
        "record_count": total,
        "decisions": decisions,
        "rule_outcomes": rule_outcomes,
        "blocking_codes": sorted(
            blockers.values(), key=lambda entry: (-entry["count"], entry["code"])
        ),
        "records_with_blockers": records_with_blockers,
        "file_types": dict(file_types),
        "extraction_field_coverage": field_coverage,
        "top_suppliers": [
            {"name": name, "count": count}
            for name, count in suppliers.most_common(10)
        ],
        "durations_ms": timing,
        "perception": {
            "enabled": perception_enabled,
            "profiles": dict(sorted(perception_profiles.items())),
            "routes": dict(sorted(perception_routes.items())),
            "families": dict(sorted(perception_families.items())),
            "rotations_applied": dict(sorted(perception_rotations.items())),
            "annotations_total": perception_annotations_total,
            "annotations_by_kind": dict(
                sorted(perception_annotations.items())
            ),
            "failures": perception_failures,
            "geometry_fast_path": perception_fast_path,
        },
        "cloud_escalations": {
            "count": cloud_escalations,
            "ratio": escalation_ratio,
            "threshold": 0.05,
            "exceeded": escalation_ratio > 0.05,
        },
    }


def _find_previous_off_ms(output_dir: Path, current_run_dir: Path) -> int | None:
    best_ms: int | None = None
    best_time = -1.0
    try:
        candidates = [
            path
            for path in output_dir.iterdir()
            if path.is_dir() and path.resolve() != current_run_dir.resolve()
        ]
    except OSError:
        return None
    for path in candidates:
        manifest_path = path / "manifest.json"
        summary_path = path / "summary.json"
        if not manifest_path.is_file() or not summary_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            config = manifest.get("config", {})
            if config.get("document_pipeline") is not False:
                continue
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            timing = summary.get("durations_ms", {})
            total = timing.get("total")
            if not isinstance(total, int):
                continue
            mtime = summary_path.stat().st_mtime
            if mtime > best_time:
                best_time = mtime
                best_ms = total
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return best_ms


def _skills_hashes() -> dict[str, str]:
    skills_dir = REPO_ROOT / "workspace" / "skills"
    if not skills_dir.is_dir():
        return {}
    return {
        path.stem: f"sha256:{_sha256_file(path)}"
        for path in sorted(skills_dir.glob("*.json"))
    }


def _build_baseline(
    *,
    run_id: str,
    decisions: Mapping[str, Any],
    rule_outcomes: Mapping[str, Any],
    perception: Mapping[str, Any],
    off_ms: int | None,
    on_ms: int,
    tests_passed: int,
) -> dict[str, Any]:
    contract_sha = (
        _sha256_file(DEFAULT_CONTRACT_PATH)
        if DEFAULT_CONTRACT_PATH.is_file()
        else ""
    )
    ratio = (
        round(on_ms / off_ms, 4)
        if off_ms is not None and off_ms > 0
        else None
    )
    payload: dict[str, Any] = {
        "contract_version": BASELINE_CONTRACT_VERSION,
        "run_id": run_id,
        "git_commit": _git_commit(),
        "decisions": dict(decisions),
        "rule_outcomes": dict(rule_outcomes),
        "perception": {
            "profiles": dict(perception.get("profiles", {})),
            "routes": dict(perception.get("routes", {})),
        },
        "timing": {"off_ms": off_ms, "on_ms": on_ms, "ratio": ratio},
        "tests_passed": tests_passed,
        "skills": _skills_hashes(),
        "contract_sha256": contract_sha,
        "counters": {
            "route_calls_per_file": 1,
            "raster_fast_path": int(
                perception.get("geometry_fast_path", 0)
            ),
            "raster_fast_path_renders": 0,
            "find_tables_probe_pages_max": 2,
            "get_cdrawings_called": False,
        },
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    payload["signature"] = hashlib.sha256(canonical).hexdigest()
    payload["signed_at"] = _now()
    return payload


def _md_cell(value: Any) -> str:
    if value is None:
        return "<absent>"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _render_report(
    manifest: Mapping[str, Any],
    summary: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
) -> str:
    config = manifest.get("config", {})
    escalation = summary.get("cloud_escalations", {})
    budget = escalation.get("budget", {})
    lines = [
        f"# Audit de lot DSH-Core — {summary.get('run_id')}",
        "",
        f"- Généré le : {summary.get('generated_at')}",
        f"- Corpus : `{config.get('input_dir')}`",
        f"- Règles : `{config.get('rules_dir')}`",
        f"- Mode vision : `{config.get('vision')}`",
        f"- Déterministe : {'oui' if manifest.get('deterministic') else 'non'}",
        f"- Run valide : {'oui' if manifest.get('run_valid') else 'non'}",
        f"- Factures auditées : {summary.get('record_count')}",
        (
            f"- Escalades cloud : {escalation.get('count', 0)} "
            f"({float(escalation.get('ratio', 0.0)) * 100:.1f} %) — quota 5 % "
            f"{'DÉPASSÉ' if escalation.get('exceeded') else 'respecté'}"
        ),
        (
            f"- Budget escalade : {budget.get('used', 0)}/{budget.get('max')} "
            f"(épuisé: {'oui' if budget.get('exhausted') else 'non'})"
        ),
        "",
        "## Synthèse des décisions",
        "",
        "| Décision | Nombre |",
        "| --- | ---: |",
    ]
    for decision, count in summary.get("decisions", {}).items():
        lines.append(f"| {decision} | {count} |")
    lines.extend(
        [
            "",
            "## Règles Obsidian",
            "",
            f"Règle cible : `{TARGET_RULE_ID}`",
            "",
            "| Résultat | Nombre |",
            "| --- | ---: |",
        ]
    )
    for outcome, count in summary.get("rule_outcomes", {}).items():
        lines.append(f"| {outcome} | {count} |")
    lines.extend(
        [
            "",
            "## Points de blocage",
            "",
            "| Code | Couche | Sévérité | Occurrences |",
            "| --- | --- | --- | ---: |",
        ]
    )
    for entry in summary.get("blocking_codes", []):
        lines.append(
            f"| {entry['code']} | {entry['layer']} | {entry['severity']} "
            f"| {entry['count']} |"
        )
    if not summary.get("blocking_codes"):
        lines.append("| <aucun> | - | - | 0 |")
    lines.extend(
        [
            "",
            "## Couverture d'extraction",
            "",
            "| Champ | Présents | Total | Ratio |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for field_name, coverage in summary.get(
        "extraction_field_coverage", {}
    ).items():
        lines.append(
            f"| {field_name} | {coverage['present']} | {coverage['total']} "
            f"| {coverage['ratio']} |"
        )

    perception = summary.get("perception", {})
    lines.extend(["", "## Perception documentaire", ""])
    if not perception.get("enabled"):
        lines.append("Pipeline de perception désactivé pour ce run.")
    else:
        lines.append(
            f"- Annotations détectées : {perception.get('annotations_total', 0)}"
        )
        lines.append(
            f"- Échecs de perception : {perception.get('failures', 0)}"
        )
        if perception.get("failures", 0):
            lines.append(
                "- Des notes `PERCEPTION_FAILED:*` sont présentes dans les "
                "records."
            )
        lines.append(
            "- Geometry fast-path : "
            f"{perception.get('geometry_fast_path', 0)} documents"
        )
        lines.append(
            "- Note `geometry_fast_path` : "
            + (
                "visible"
                if perception.get("geometry_fast_path", 0)
                else "aucune page éligible"
            )
        )
        for title, key in (
            ("Profils", "profiles"),
            ("Routes", "routes"),
            ("Familles métier", "families"),
            ("Rotations", "rotations_applied"),
            ("Annotations", "annotations_by_kind"),
        ):
            values = perception.get(key, {})
            lines.extend(["", f"### {title}", "", "| Valeur | Nombre |", "| --- | ---: |"])
            if values:
                for label, count in values.items():
                    lines.append(f"| {_md_cell(label)} | {count} |")
            else:
                lines.append("| <aucun> | 0 |")

    baseline = manifest.get("baseline") or {}
    if baseline:
        timing = baseline.get("timing", {})
        lines.extend(
            [
                "",
                "## Baseline",
                "",
                f"- Run : `{manifest.get('run_id')}`",
                f"- Commit : `{baseline.get('git_commit', '')}`",
                f"- Contrat : `sha256:{baseline.get('contract_sha256', '')}`",
                (
                    "- Ratio on/off : "
                    f"{timing.get('ratio')} "
                    f"(off={timing.get('off_ms')} ms, on={timing.get('on_ms')} ms)"
                ),
                f"- Signature : `{baseline.get('signature', '')}`",
            ]
        )

    for status, title in ((HITL, "Factures HITL"), (REJECTED, "Factures REJECTED")):
        lines.extend(["", f"## {title}", ""])
        matching = [
            record
            for record in records
            if record.get("decision", {}).get("status") == status
        ]
        if not matching:
            lines.append("Aucune.")
            continue
        for record in matching:
            rule = record.get("rule", {})
            details = "; ".join(rule.get("details") or []) or rule.get("reason")
            lines.append(
                f"- `{record['source']['name']}` — {details}"
            )

    lines.extend(
        [
            "",
            "## Index des factures",
            "",
            "| Fichier | Décision | Règle | Motif | Fournisseur |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for record in records:
        rule = record.get("rule", {})
        supplier = record.get("extraction", {}).get("fields", {}).get(
            "supplier_name"
        )
        lines.append(
            f"| {_md_cell(record['source']['name'])} "
            f"| {_md_cell(record['decision']['status'])} "
            f"| {_md_cell(rule.get('outcome'))} "
            f"| {_md_cell(rule.get('reason'))} "
            f"| {_md_cell(supplier)} |"
        )
    lines.append("")
    return "\n".join(lines)


def _build_manifest(
    *,
    run_id: str,
    config: Mapping[str, Any],
    corpus: Sequence[CorpusFile],
    supported_count: int,
    selected_count: int,
    rules_snapshot: Mapping[str, Any],
    deterministic: bool,
) -> dict[str, Any]:
    fingerprint = hashlib.sha256(
        "\n".join(
            f"{item.name}|{item.size_bytes}|{item.sha256}" for item in corpus
        ).encode("utf-8")
    ).hexdigest()
    return {
        "run_id": run_id,
        "started_at": _now(),
        "finished_at": None,
        "config": dict(config),
        "corpus": {
            "file_count": len(corpus),
            "supported_count": supported_count,
            "selected_count": selected_count,
            "fingerprint_sha256": fingerprint,
        },
        "rules_snapshot": dict(rules_snapshot),
        "environment": _environment(),
        "deterministic": deterministic,
        "resumed": False,
        "records_reused": 0,
        "records_written": 0,
        "record_count": 0,
        "run_valid": None,
        "aborted": False,
        "rules_changed_during_run": False,
    }


def _run_batch_audit_impl(
    input_dir: Path = DEFAULT_INPUT_DIR,
    *,
    rules_dir: Path = DEFAULT_RULES_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    vision: str = "none",
    pattern: str = "*",
    limit: int | None = None,
    resume: bool = False,
    run_id: str | None = None,
    fail_fast: bool = False,
    simulate_zero_vat: bool = False,
    vision_extractor: Any | None = None,
    max_cloud_escalations: int | None = None,
    document_pipeline: bool = True,
    baseline_out: Path | None = None,
    tests_passed: int = DEFAULT_BASELINE_TESTS_PASSED,
) -> BatchAuditResult:
    if vision not in VISION_MODES:
        raise BatchAuditError("INVALID_VISION_MODE", vision)
    input_dir = Path(input_dir)
    rules_dir = Path(rules_dir)
    output_dir = Path(output_dir)

    corpus = CorpusScanner(input_dir, pattern=pattern).scan()
    supported = [item for item in corpus if item.suffix in SUPPORTED_SUFFIXES]
    if not supported:
        raise BatchAuditError(
            "NO_SUPPORTED_FILES",
            f"aucun fichier supporté dans {input_dir}",
        )
    selected = corpus[:limit] if limit is not None else corpus
    if not selected:
        raise BatchAuditError("NO_FILES_SELECTED", "aucun fichier à auditer")

    rules_snapshot, rule_context = load_rule_context(rules_dir)
    budget_ratio = resolve_escalation_budget_ratio()
    if max_cloud_escalations is None:
        max_cloud_escalations = budget_max(budget_ratio, len(selected))
    max_cloud_escalations = max(0, int(max_cloud_escalations))
    escalation_budget = CloudEscalationBudget(max_cloud_escalations)
    extractor = (
        vision_extractor
        if vision_extractor is not None
        else build_vision_extractor(vision, escalation_budget)
    )
    deterministic = vision == "none" and extractor is None

    reset_perception_cache()
    if document_pipeline:
        if extractor is not None:
            pipeline_engine = LocalDocumentEngine()
        elif vision == "cloud":
            pipeline_engine = CloudDocumentEngine()
        elif vision == "hybrid":
            pipeline_engine = HybridDocumentEngine(
                LocalDocumentEngine(),
                CloudDocumentEngine(),
                budget=escalation_budget,
            )
        else:
            pipeline_engine = LocalDocumentEngine()
        set_default_engine(pipeline_engine)
    else:
        set_default_engine(None)

    run_dir, effective_run_id, existing = _prepare_run_dir(
        output_dir, run_id, resume
    )
    config = {
        "input_dir": str(input_dir),
        "rules_dir": str(rules_dir),
        "output_dir": str(output_dir),
        "vision": vision,
        "vision_extractor": type(extractor).__name__ if extractor else None,
        "document_pipeline": document_pipeline,
        "pattern": pattern,
        "limit": limit,
        "simulate_zero_vat": simulate_zero_vat,
        "fail_fast": fail_fast,
        "cloud_escalation_budget_ratio": budget_ratio,
        "max_cloud_escalations": max_cloud_escalations,
    }
    manifest = _build_manifest(
        run_id=effective_run_id,
        config=config,
        corpus=corpus,
        supported_count=len(supported),
        selected_count=len(selected),
        rules_snapshot=rules_snapshot,
        deterministic=deterministic,
    )
    manifest["resumed"] = bool(resume and existing)
    manifest["records_reused"] = len(existing)
    _write_json(run_dir / "manifest.json", manifest)

    records = list(existing)
    seen = {
        str(record.get("source", {}).get("sha256"))
        for record in existing
        if isinstance(record.get("source"), Mapping)
    }
    written = 0
    aborted = False
    for corpus_file in selected:
        if corpus_file.sha256 in seen:
            continue
        record = audit_invoice(
            corpus_file,
            run_id=effective_run_id,
            seq=len(records) + 1,
            rules_dir=rules_dir,
            rule_context=rule_context,
            vision_extractor=extractor,
            simulate_zero_vat=simulate_zero_vat,
        )
        payload = record.to_dict()
        _append_record(run_dir / "records.jsonl", payload)
        records.append(payload)
        seen.add(corpus_file.sha256)
        written += 1
        if fail_fast and record.status != "ok":
            aborted = True
            break

    rules_after = _scan_rule_files(rules_dir)
    rules_changed = [
        (entry["file"], entry["sha256"]) for entry in rules_after
    ] != [(entry["file"], entry["sha256"]) for entry in rules_snapshot["files"]]
    run_valid = (
        not aborted
        and not rules_changed
        and all(record.get("status") == "ok" for record in records)
    )
    summary = _summary_from_records(
        effective_run_id,
        run_dir,
        records,
        perception_enabled=document_pipeline,
    )
    guard = summary["cloud_escalations"]
    guard["budget"] = {
        "max": max_cloud_escalations,
        "used": escalation_budget.used,
        "exhausted": escalation_budget.exhausted,
        "external_extractor": vision_extractor is not None,
    }
    manifest["escalation_guard"] = guard
    manifest.update(
        {
            "finished_at": _now(),
            "records_written": written,
            "record_count": len(records),
            "run_valid": run_valid,
            "aborted": aborted,
            "rules_changed_during_run": rules_changed,
        }
    )
    if guard["exceeded"]:
        logger.warning(
            "Quota d'escalade cloud dépassé: %s/%s (%.1f%% > 5%%)",
            guard["count"],
            len(records),
            float(guard["ratio"]) * 100,
        )
    if baseline_out is not None:
        off_ms = _find_previous_off_ms(output_dir, run_dir)
        on_ms = int(summary.get("durations_ms", {}).get("total", 0))
        baseline_payload = _build_baseline(
            run_id=effective_run_id,
            decisions=summary.get("decisions", {}),
            rule_outcomes=summary.get("rule_outcomes", {}),
            perception=summary.get("perception", {}),
            off_ms=off_ms,
            on_ms=on_ms,
            tests_passed=tests_passed,
        )
        baseline_path = Path(baseline_out)
        _write_json(baseline_path, baseline_payload)
        manifest["baseline"] = {
            "path": str(baseline_path),
            "git_commit": baseline_payload["git_commit"],
            "signature": baseline_payload["signature"],
            "contract_sha256": baseline_payload["contract_sha256"],
            "timing": baseline_payload["timing"],
            "signed_at": baseline_payload["signed_at"],
        }
    _write_json(run_dir / "manifest.json", manifest)
    _write_json(run_dir / "summary.json", summary)
    (run_dir / "report.md").write_text(
        _render_report(manifest, summary, records), encoding="utf-8"
    )

    decisions = summary["decisions"]
    exit_code = 0 if decisions.get(VALIDATED, 0) == len(records) else 2
    if aborted:
        exit_code = 1
    return BatchAuditResult(
        run_id=effective_run_id,
        run_dir=run_dir,
        records=records,
        summary=summary,
        manifest=manifest,
        exit_code=exit_code,
    )


def _coerce_pipeline_flag(value: bool | str) -> bool:
    if isinstance(value, str):
        return is_pipeline_enabled({DEFAULT_PIPELINE_ENV: value})
    return bool(value)


def run_batch_audit(
    input_dir: Path = DEFAULT_INPUT_DIR,
    *,
    rules_dir: Path = DEFAULT_RULES_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    vision: str = "none",
    pattern: str = "*",
    limit: int | None = None,
    resume: bool = False,
    run_id: str | None = None,
    fail_fast: bool = False,
    simulate_zero_vat: bool = False,
    vision_extractor: Any | None = None,
    max_cloud_escalations: int | None = None,
    document_pipeline: bool | str = True,
    baseline_out: Path | None = None,
    tests_passed: int = DEFAULT_BASELINE_TESTS_PASSED,
) -> BatchAuditResult:
    """Run the batch audit with a scoped document-pipeline flag."""

    enabled = _coerce_pipeline_flag(document_pipeline)
    previous = os.environ.get(DEFAULT_PIPELINE_ENV)
    os.environ[DEFAULT_PIPELINE_ENV] = "on" if enabled else "off"
    try:
        return _run_batch_audit_impl(
            input_dir,
            rules_dir=rules_dir,
            output_dir=output_dir,
            vision=vision,
            pattern=pattern,
            limit=limit,
            resume=resume,
            run_id=run_id,
            fail_fast=fail_fast,
            simulate_zero_vat=simulate_zero_vat,
            vision_extractor=vision_extractor,
            max_cloud_escalations=max_cloud_escalations,
            document_pipeline=enabled,
            baseline_out=baseline_out,
            tests_passed=tests_passed,
        )
    finally:
        if previous is None:
            os.environ.pop(DEFAULT_PIPELINE_ENV, None)
        else:
            os.environ[DEFAULT_PIPELINE_ENV] = previous


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit de lot du corpus de factures DSH-Core"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help=f"corpus à auditer (défaut: {DEFAULT_INPUT_DIR.relative_to(REPO_ROOT)})",
    )
    parser.add_argument("--rules-dir", type=Path, default=DEFAULT_RULES_DIR)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"répertoire des rapports (défaut: {DEFAULT_OUTPUT_DIR.relative_to(REPO_ROOT)})",
    )
    parser.add_argument("--vision", choices=VISION_MODES, default="none")
    parser.add_argument(
        "--document-pipeline",
        choices=("on", "off"),
        default="on",
        help="active ou désactive la perception documentaire",
    )
    parser.add_argument("--pattern", default="*", help="filtre glob des fichiers")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--simulate-zero-vat", action="store_true")
    parser.add_argument(
        "--max-cloud-escalations",
        type=int,
        default=None,
        help="plafond dur d'escalades cloud (défaut: 5%% du corpus sélectionné)",
    )
    parser.add_argument(
        "--baseline-out",
        type=Path,
        default=None,
        help="?crit la signature de baseline JSON ? ce chemin",
    )
    parser.add_argument(
        "--tests-passed",
        type=int,
        default=DEFAULT_BASELINE_TESTS_PASSED,
        help="nombre de tests verts ? inscrire dans la baseline",
    )
    parser.add_argument("--json", action="store_true", help="sortie JSON machine")
    return parser.parse_args(argv)


def _print_report(result: BatchAuditResult) -> None:
    summary = result.summary
    decisions = summary["decisions"]
    print("=" * 72)
    print("AUDIT DE LOT DSH-CORE — CORPUS -> InvoiceSchema -> RÈGLES -> LANGGRAPH")
    print("=" * 72)
    print(f"Run        : {result.run_id}")
    print(f"Sortie     : {result.run_dir}")
    print(f"Factures   : {summary['record_count']}")
    print(
        "Décisions  : "
        f"VALIDATED={decisions.get(VALIDATED, 0)} "
        f"HITL={decisions.get(HITL, 0)} "
        f"REJECTED={decisions.get(REJECTED, 0)}"
    )
    blockers = summary.get("blocking_codes", [])
    if blockers:
        print(
            "Blocages   : "
            + ", ".join(
                f"{entry['code']}={entry['count']}" for entry in blockers[:8]
            )
        )
    budget = summary.get("cloud_escalations", {}).get("budget", {})
    if budget:
        print(
            "Budget     : "
            f"{budget.get('used', 0)}/{budget.get('max')} escalades "
            f"(épuisé: {'oui' if budget.get('exhausted') else 'non'})"
        )
    print(f"Rapport    : {result.run_dir / 'report.md'}")
    print("=" * 72)


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = _parse_args(argv)
    input_dir = args.input if args.input.is_absolute() else REPO_ROOT / args.input
    rules_dir = (
        args.rules_dir if args.rules_dir.is_absolute() else REPO_ROOT / args.rules_dir
    )
    output_dir = (
        args.output if args.output.is_absolute() else REPO_ROOT / args.output
    )
    try:
        result = run_batch_audit(
            input_dir,
            rules_dir=rules_dir,
            output_dir=output_dir,
            vision=args.vision,
            pattern=args.pattern,
            limit=args.limit,
            resume=args.resume,
            run_id=args.run_id,
            fail_fast=args.fail_fast,
            simulate_zero_vat=args.simulate_zero_vat,
            max_cloud_escalations=args.max_cloud_escalations,
            document_pipeline=args.document_pipeline,
            baseline_out=(
                args.baseline_out
                if args.baseline_out is None
                or args.baseline_out.is_absolute()
                else REPO_ROOT / args.baseline_out
            ),
            tests_passed=args.tests_passed,
        )
    except (BatchAuditError, RuleRegistryError, OSError) as exc:
        print(f"ECHEC AUDIT: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                result.summary, indent=2, ensure_ascii=False, default=str
            )
        )
    else:
        _print_report(result)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AuditRecord",
    "BatchAuditError",
    "BatchAuditResult",
    "CorpusFile",
    "CorpusScanner",
    "DEFAULT_INPUT_DIR",
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_RULES_DIR",
    "HITL",
    "REJECTED",
    "VALIDATED",
    "audit_invoice",
    "build_vision_extractor",
    "classify_decision",
    "load_rule_context",
    "main",
    "rule_outcome_for",
    "run_batch_audit",
]
