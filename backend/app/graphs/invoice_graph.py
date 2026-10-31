"""LangGraph orchestration for the official invoice workflow.

The graph executes the real contract chain:

1. triage of the source file;
2. production through ``PdfInvoiceProducer`` (native PDF, scanned PDF/vision);
3. structural validation of the produced payload with ``InvoiceSchema``;
4. business evaluation of ``RULE-TVA-ZERO-001`` via ``RuleRegistryService``;
5. conditional routing: commit/persistence or HITL inbox.
"""

import logging
import os
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from schemas.invoice_schema import InvoiceSchema
from services.invoice_producer_protocol import (
    IInvoiceProducer,
    InvoiceExtractionError,
    InvoiceProducerError,
    PdfInvoiceProducer,
    VisionExtractor,
    extract_document_text,
    validate_produced_invoice,
)
from services.document_engine import get_shared_router
from services.document_geometry import source_sha256 as compute_source_sha256
from services.document_pipeline import is_pipeline_enabled, perceive_document
from services.persistence_manager import DEFAULT_COMPANY_ID, PersistenceManager
from services.supplier_memory_service import enrich_invoice
from services.supplier_repository import SupplierRepository
from services.tva_zero_rule_evaluator import (
    DEFAULT_RULES_DIR,
    RuleDecision,
    evaluate_tva_zero_rule,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DSH-Core.InvoiceGraph")

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}
PDF_SUFFIXES = {".pdf"}
_INVOICE_ROUTER = get_shared_router()


class InvoiceState(TypedDict, total=False):
    """State exchanged between the orchestration nodes."""

    filename: str
    file_path: str
    file_type: Literal["image", "pdf", "unknown"]

    raw_text: str
    extracted_data: dict[str, Any]
    producer_result: dict[str, Any] | None
    perception: dict[str, Any] | None
    routing: dict[str, Any] | None
    invoice: InvoiceSchema | None
    rule_decision: RuleDecision | None

    validation_passed: bool
    hitl_required: bool
    confidence_score: float
    is_complex: bool

    commit_status: str
    qdrant_payload: Any
    persistence_result: dict[str, Any] | None
    skill_file_path: str
    error_message: str

    supplier_memory: dict[str, Any] | None

    rules_dir: str
    simulate_zero_vat: bool

    messages: Annotated[list, add_messages]


def triage_node(state: InvoiceState) -> InvoiceState:
    """Classify the source file before production."""

    filename = state.get("filename") or Path(state.get("file_path", "")).name
    suffix = Path(filename).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        file_type: Literal["image", "pdf", "unknown"] = "image"
    elif suffix in PDF_SUFFIXES:
        file_type = "pdf"
    else:
        file_type = "unknown"

    routing: dict[str, Any] | None = None
    if is_pipeline_enabled() and file_type in {"pdf", "image"}:
        try:
            verdict = _INVOICE_ROUTER.route(Path(state.get("file_path", "")))
            routing = {
                "route": verdict.decision.route,
                "profile_type": verdict.profile.profile_type,
                "business_family": verdict.profile.business_family,
                "reasons": list(verdict.decision.reasons),
                "tools": list(verdict.decision.tools),
                "confidence": verdict.decision.confidence,
            }
        except Exception as exc:
            routing = {
                "route": "manual_review",
                "reasons": [f"ROUTING_FAILED:{type(exc).__name__}"],
            }

    logger.info("[TRIAGE] %s -> %s", filename, file_type)
    return {
        "filename": filename,
        "file_type": file_type,
        "raw_text": "",
        "extracted_data": {},
        "producer_result": None,
        "perception": None,
        "routing": routing,
        "invoice": None,
        "rule_decision": None,
        "validation_passed": False,
        "hitl_required": False,
        "commit_status": "",
        "persistence_result": None,
        "error_message": "",
    }


def production_node(
    state: InvoiceState, config: RunnableConfig | None = None
) -> InvoiceState:
    """Produce a canonical invoice through the unified producer protocol."""

    source = Path(state.get("file_path", ""))
    extractor = (config or {}).get("configurable", {}).get("vision_extractor")
    producer: IInvoiceProducer = PdfInvoiceProducer(
        source,
        vision_extractor=extractor,
        simulate_zero_vat=bool(state.get("simulate_zero_vat")),
    )
    try:
        pipeline_enabled = is_pipeline_enabled()
    except Exception:
        pipeline_enabled = False
    perception = perceive_document(source) if pipeline_enabled else None
    perception_payload = (
        perception.model_dump(mode="json")
        if perception is not None
        else None
    )
    routing = state.get("routing")

    try:
        raw_text = extract_document_text(source)
    except Exception:
        raw_text = ""

    try:
        invoice = producer.produce()
    except InvoiceProducerError as exc:
        error = exc
    except Exception as exc:
        error = InvoiceProducerError(
            "EXTRACTION_FAILED", str(exc), source=source
        )
    else:
        error = None

    if error is not None:
        logger.warning("[PRODUCTION] %s", error)
        return {
            "raw_text": raw_text,
            "extracted_data": {},
            "producer_result": getattr(producer, "last_payload", None),
            "perception": perception_payload,
            "routing": routing,
            "invoice": None,
            "validation_passed": False,
            "hitl_required": True,
            "confidence_score": 0.0,
            "is_complex": True,
            "error_message": str(error),
            "messages": [("system", f"Production ǸchouǸe: {error.code}")],
        }

    payload = getattr(producer, "last_payload", None) or invoice.model_dump(
        mode="json"
    )
    logger.info("[PRODUCTION] InvoiceSchema produit pour %s", state.get("filename"))
    return {
        "raw_text": raw_text,
        "extracted_data": payload,
        "producer_result": payload,
        "perception": perception_payload,
        "routing": routing,
        "invoice": invoice,
        "confidence_score": invoice.confidence_score or 1.0,
        "is_complex": False,
        "error_message": "",
        "messages": [("system", f"InvoiceSchema produit pour {state.get('filename')}")],
    }


_AUTO_SUPPLIER_REPOSITORY = object()
_DEFAULT_SUPPLIER_REPOSITORY: SupplierRepository | None = None
_SUPPLIER_REPOSITORY_READY = False


def _default_supplier_repository() -> SupplierRepository | None:
    global _DEFAULT_SUPPLIER_REPOSITORY, _SUPPLIER_REPOSITORY_READY
    if not _SUPPLIER_REPOSITORY_READY:
        _SUPPLIER_REPOSITORY_READY = True
        try:
            _DEFAULT_SUPPLIER_REPOSITORY = SupplierRepository()
        except Exception as exc:  # pragma: no cover - defensive boundary
            logger.warning("[SUPPLIER] repository unavailable: %s", exc)
            _DEFAULT_SUPPLIER_REPOSITORY = None
    return _DEFAULT_SUPPLIER_REPOSITORY


def supplier_enrichment_node(
    state: InvoiceState, config: RunnableConfig | None = None
) -> InvoiceState:
    """Backfill certified supplier fields before schema validation."""

    invoice = state.get("invoice")
    if not isinstance(invoice, InvoiceSchema):
        return {
            "supplier_memory": {
                "matched": False,
                "reason": "INVOICE_NOT_PRODUCED",
            }
        }

    configurable = (config or {}).get("configurable", {})
    if "supplier_repository" in configurable:
        repository = configurable["supplier_repository"]
    else:
        repository = _default_supplier_repository()
    if repository is None:
        return {
            "supplier_memory": {
                "matched": False,
                "reason": "REPOSITORY_UNAVAILABLE",
            }
        }

    try:
        enriched, memory = enrich_invoice(invoice, repository)
    except Exception as exc:
        logger.warning("[SUPPLIER] enrichment failed: %s", exc)
        return {
            "supplier_memory": {
                "matched": False,
                "reason": "ENRICHMENT_FAILED",
                "error": str(exc),
            }
        }

    payload = enriched.model_dump(mode="json")
    logger.info(
        "[SUPPLIER] %s matched=%s by=%s",
        state.get("filename"),
        memory.get("matched"),
        memory.get("matched_by"),
    )
    if memory.get("matched"):
        summary = (
            f"match {memory.get('matched_by')} ({memory.get('legal_name')})"
        )
    else:
        summary = "aucun match"
    return {
        "invoice": enriched,
        "extracted_data": payload,
        "producer_result": payload,
        "supplier_memory": memory,
        "messages": [("system", f"Mémoire fournisseur: {summary}")],
    }


def validation_node(state: InvoiceState) -> InvoiceState:
    """Enforce the canonical ``InvoiceSchema`` contract on the payload."""

    invoice = state.get("invoice")
    payload = state.get("producer_result")
    if not isinstance(invoice, InvoiceSchema) or payload is None:
        message = state.get("error_message") or "INVOICE_NOT_PRODUCED"
        logger.warning("[VALIDATION] %s", message)
        return {
            "validation_passed": False,
            "hitl_required": True,
            "is_complex": True,
            "error_message": message,
            "messages": [("system", f"Validation structurelle échouée: {message}")],
        }

    try:
        validated = validate_produced_invoice(payload, source=state.get("file_path"))
    except InvoiceProducerError as exc:
        logger.warning("[VALIDATION] %s", exc)
        return {
            "invoice": None,
            "validation_passed": False,
            "hitl_required": True,
            "is_complex": True,
            "error_message": str(exc),
            "messages": [("system", f"Validation structurelle échouée: {exc.code}")],
        }

    logger.info("[VALIDATION] InvoiceSchema valide")
    return {
        "invoice": validated,
        "extracted_data": payload,
        "validation_passed": True,
        "hitl_required": False,
        "confidence_score": validated.confidence_score or 1.0,
        "is_complex": False,
        "error_message": "",
        "messages": [("system", "InvoiceSchema validé")],
    }


def rule_evaluation_node(state: InvoiceState) -> InvoiceState:
    """Evaluate the approved zero-VAT disambiguation rule."""

    invoice = state.get("invoice")
    if not isinstance(invoice, InvoiceSchema):
        decision = RuleDecision(
            status="HITL",
            reason="INVOICE_NOT_VALIDATED",
            details=("aucun InvoiceSchema valide à évaluer",),
        )
        return {
            "rule_decision": decision,
            "hitl_required": True,
            "is_complex": True,
            "messages": [("system", "Évaluation métier impossible: facture non validée")],
        }

    rules_dir = Path(state.get("rules_dir") or DEFAULT_RULES_DIR)
    decision = evaluate_tva_zero_rule(
        invoice,
        rules_dir,
        source_text=state.get("raw_text", ""),
    )
    logger.info(
        "[RULE] %s v%s -> %s/%s",
        decision.rule_id,
        decision.rule_version,
        decision.status,
        decision.reason,
    )
    return {
        "rule_decision": decision,
        "hitl_required": decision.hitl_required,
        "is_complex": decision.hitl_required,
        "messages": [
            (
                "system",
                f"Règle {decision.rule_id}: {decision.status}/{decision.reason}",
            )
        ],
    }


_AUTO_PERSISTENCE = object()
_DEFAULT_PERSISTENCE_MANAGER: PersistenceManager | None = None


def _default_persistence_manager() -> PersistenceManager | None:
    global _DEFAULT_PERSISTENCE_MANAGER
    if _DEFAULT_PERSISTENCE_MANAGER is None:
        try:
            _DEFAULT_PERSISTENCE_MANAGER = PersistenceManager.from_env()
        except Exception as exc:  # pragma: no cover - defensive boundary
            logger.warning("[COMMIT] Persistence manager unavailable: %s", exc)
            _DEFAULT_PERSISTENCE_MANAGER = None
    return _DEFAULT_PERSISTENCE_MANAGER


def commit_node(
    state: InvoiceState, config: RunnableConfig | None = None
) -> InvoiceState:
    """Persist the validated invoice to Neo4j and Qdrant."""

    invoice = state.get("invoice")
    decision = state.get("rule_decision")
    configurable = (config or {}).get("configurable", {})
    if "persistence_manager" in configurable:
        manager = configurable["persistence_manager"]
    else:
        manager = _default_persistence_manager()

    persistence_payload: dict[str, Any] | None = None
    if invoice is not None and manager is not None:
        source_path = state.get("file_path") or state.get("filename")
        perception = state.get("perception")
        source_sha256 = (
            perception.get("source_sha256")
            if isinstance(perception, dict)
            else None
        )
        if not source_sha256 and source_path:
            source_sha256 = compute_source_sha256(source_path) or None
        tenant_id = (
            invoice.company_id
            or os.getenv("DSH_TENANT_ID")
            or DEFAULT_COMPANY_ID
        )
        try:
            persistence_payload = manager.persist_invoice(
                invoice,
                rule_decision=decision,
                source_file=source_path,
                source_sha256=source_sha256,
                tenant_id=tenant_id,
            ).to_dict()
        except Exception as exc:
            persistence_payload = {
                "invoice_id": None,
                "graph_status": "FAILED",
                "vector_status": "FAILED",
                "point_id": None,
                "embedding_source": None,
                "errors": [str(exc)],
            }
        logger.info(
            "[COMMIT] persistence graph=%s vector=%s",
            persistence_payload.get("graph_status"),
            persistence_payload.get("vector_status"),
        )

    payload = {
        "status": "COMMITTED",
        "invoice": invoice.model_dump(mode="json") if invoice else None,
        "rule": {
            "rule_id": decision.rule_id if decision else None,
            "rule_version": decision.rule_version if decision else None,
            "status": decision.status if decision else None,
            "vat_regime": decision.vat_regime if decision else None,
            "include_in_vat_balance": (
                decision.include_in_vat_balance if decision else None
            ),
        },
        "persistence": persistence_payload,
    }
    logger.info("[COMMIT] %s", state.get("filename"))
    return {
        "commit_status": "COMMITTED",
        "hitl_required": False,
        "qdrant_payload": payload,
        "persistence_result": persistence_payload,
        "messages": [
            (
                "system",
                f"Facture {state.get('filename')} validée et commitée "
                f"({decision.vat_regime if decision else 'régime standard'}).",
            )
        ],
    }


def hitl_inbox_node(state: InvoiceState) -> InvoiceState:
    """Route the invoice to the human-in-the-loop inbox."""

    decision = state.get("rule_decision")
    reason = decision.reason if decision else "UNKNOWN"
    logger.warning("[HITL] %s -> %s", state.get("filename"), reason)
    return {
        "hitl_required": True,
        "commit_status": "PENDING_HITL",
        "error_message": state.get("error_message") or reason,
        "messages": [
            (
                "system",
                f"Facture {state.get('filename')} routée en HITL ({reason}).",
            )
        ],
    }


def route_after_triage(state: InvoiceState) -> Literal["production", "__end__"]:
    if state.get("file_type") in {"pdf", "image"}:
        return "production"
    return "__end__"


def route_after_rule_evaluation(state: InvoiceState) -> Literal["commit", "hitl_inbox"]:
    if not state.get("validation_passed") or state.get("hitl_required"):
        return "hitl_inbox"
    decision = state.get("rule_decision")
    if decision is None or decision.hitl_required:
        return "hitl_inbox"
    return "commit"


def build_invoice_graph():
    """Assemble and compile the official invoice LangGraph workflow."""

    workflow = StateGraph(InvoiceState)

    workflow.add_node("triage", triage_node)
    workflow.add_node("production", production_node)
    workflow.add_node("supplier_enrichment", supplier_enrichment_node)
    workflow.add_node("validation", validation_node)
    workflow.add_node("rule_evaluation", rule_evaluation_node)
    workflow.add_node("commit", commit_node)
    workflow.add_node("hitl_inbox", hitl_inbox_node)

    workflow.set_entry_point("triage")
    workflow.add_conditional_edges(
        "triage",
        route_after_triage,
        {"production": "production", "__end__": END},
    )
    workflow.add_edge("production", "supplier_enrichment")
    workflow.add_edge("supplier_enrichment", "validation")
    workflow.add_edge("validation", "rule_evaluation")
    workflow.add_conditional_edges(
        "rule_evaluation",
        route_after_rule_evaluation,
        {"commit": "commit", "hitl_inbox": "hitl_inbox"},
    )
    workflow.add_edge("commit", END)
    workflow.add_edge("hitl_inbox", END)

    return workflow.compile(checkpointer=MemorySaver())


def run_invoice_graph(
    file_path: str | Path,
    *,
    rules_dir: str | Path | None = None,
    vision_extractor: VisionExtractor | None = None,
    simulate_zero_vat: bool = False,
    persistence_manager: Any = _AUTO_PERSISTENCE,
    supplier_repository: Any = _AUTO_SUPPLIER_REPOSITORY,
    thread_id: str = "invoice-graph",
) -> InvoiceState:
    """Convenience entry point invoking the graph for a single file."""

    source = Path(file_path)
    state: InvoiceState = {
        "filename": source.name,
        "file_path": str(source),
        "file_type": "unknown",
        "raw_text": "",
        "extracted_data": {},
        "producer_result": None,
        "perception": None,
        "routing": None,
        "invoice": None,
        "rule_decision": None,
        "validation_passed": False,
        "hitl_required": False,
        "confidence_score": 0.0,
        "is_complex": False,
        "commit_status": "",
        "qdrant_payload": None,
        "persistence_result": None,
        "skill_file_path": "",
        "error_message": "",
        "supplier_memory": None,
        "rules_dir": str(rules_dir or DEFAULT_RULES_DIR),
        "simulate_zero_vat": simulate_zero_vat,
        "messages": [],
    }
    app = build_invoice_graph()
    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        "vision_extractor": vision_extractor,
    }
    if persistence_manager is not _AUTO_PERSISTENCE:
        configurable["persistence_manager"] = persistence_manager
    if supplier_repository is not _AUTO_SUPPLIER_REPOSITORY:
        configurable["supplier_repository"] = supplier_repository
    return app.invoke(state, {"configurable": configurable})


if __name__ == "__main__":
    corpus = Path(__file__).resolve().parents[3] / "workspace" / "invoices" / "input"
    result = run_invoice_graph(corpus / "facture_emaux.pdf", thread_id="demo")
    print("\n--- RÉSULTAT FINAL ---")
    print(f"Type fichier   : {result['file_type']}")
    print(f"Validation     : {result['validation_passed']}")
    print(f"Statut commit  : {result['commit_status']}")
    print(f"HITL requis    : {result['hitl_required']}")
    if result.get("rule_decision"):
        print(
            "Règle          : "
            f"{result['rule_decision'].rule_id} "
            f"{result['rule_decision'].status}/{result['rule_decision'].reason}"
        )
