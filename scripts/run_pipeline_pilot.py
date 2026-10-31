"""Pilot integration script: corpus invoice -> InvoiceSchema -> RuleRegistryService.

The script performs a deterministic raw extraction (PyMuPDF text layer + regex
heuristics), validates the resulting payload against the existing
``InvoiceSchema`` contract, then evaluates the approved business rule
``RULE-TVA-ZERO-001`` through ``RuleRegistryService``.

No schema or service is modified: the pilot only consumes the validated
contracts already present in the repository.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from schemas.invoice_schema import InvoiceSchema
from services.invoice_producer_protocol import (
    InvoiceExtractionError as PilotExtractionError,
)
from services.invoice_producer_protocol import InvoiceProducerError
from services.invoice_producer_protocol import (
    extract_raw_payload,
    validate_produced_invoice as validate_payload,
)
from services.tva_zero_rule_evaluator import (
    RuleDecision,
    evaluate_tva_zero_rule,
)

DEFAULT_INVOICE = REPO_ROOT / "workspace" / "invoices" / "input" / "facture_emaux.pdf"
DEFAULT_RULES_DIR = REPO_ROOT / "knowledge" / "rules" / "accounting"
TARGET_RULE_ID = "RULE-TVA-ZERO-001"


@dataclass(frozen=True)
class PilotResult:
    invoice_path: Path
    extraction_tool: str
    notes: tuple[str, ...]
    payload: Mapping[str, Any]
    invoice: InvoiceSchema | None
    decision: RuleDecision

    @property
    def validated(self) -> bool:
        return self.decision.status == "VALIDATED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "invoice_path": str(self.invoice_path),
            "extraction_tool": self.extraction_tool,
            "notes": list(self.notes),
            "validated": self.validated,
            "decision": {
                "status": self.decision.status,
                "reason": self.decision.reason,
                "rule_id": self.decision.rule_id,
                "rule_version": self.decision.rule_version,
                "vat_regime": self.decision.vat_regime,
                "include_in_vat_balance": self.decision.include_in_vat_balance,
                "accounting_treatment": self.decision.accounting_treatment,
                "details": list(self.decision.details),
            },
        }


def run_pilot(
    invoice_path: Path,
    *,
    rules_dir: Path = DEFAULT_RULES_DIR,
    payload_path: Path | None = None,
    supplier_override: str | None = None,
    country_override: str | None = None,
    vat_override: str | None = None,
    simulate_zero_vat: bool = False,
) -> PilotResult:
    notes: list[str] = []
    source_text = ""

    if payload_path is not None:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        extraction_tool = "simulated_payload"
        payload.setdefault("metadata", {})
        payload["metadata"].setdefault("source_file", invoice_path.name)
        payload["metadata"].setdefault("extraction_tool", extraction_tool)
        notes.append(f"payload simulé chargé depuis {payload_path}")
    else:
        payload, source_text, extraction_notes = extract_raw_payload(
            invoice_path,
            supplier_override=supplier_override,
            country_override=country_override,
            vat_override=vat_override,
            simulate_zero_vat=simulate_zero_vat,
        )
        notes.extend(extraction_notes)
        extraction_tool = payload["metadata"]["extraction_tool"]
        if not source_text.strip():
            source_text = " ".join(
                str(value) for value in payload.get("metadata", {}).values()
            )

    try:
        invoice = validate_payload(payload)
    except InvoiceProducerError as exc:
        return PilotResult(
            invoice_path=invoice_path,
            extraction_tool=extraction_tool,
            notes=tuple(notes),
            payload=payload,
            invoice=None,
            decision=RuleDecision(
                status="HITL",
                reason="INVALID_INVOICE_PAYLOAD",
                details=(str(exc),),
            ),
        )

    decision = evaluate_tva_zero_rule(invoice, rules_dir, source_text=source_text)
    return PilotResult(
        invoice_path=invoice_path,
        extraction_tool=extraction_tool,
        notes=tuple(notes),
        payload=payload,
        invoice=invoice,
        decision=decision,
    )


def _print_report(result: PilotResult) -> None:
    print("=" * 72)
    print("PILOT DSH-CORE — FACTURE -> InvoiceSchema -> RuleRegistryService")
    print("=" * 72)
    print(f"Facture      : {result.invoice_path}")
    print(f"Extraction   : {result.extraction_tool}")
    for note in result.notes:
        print(f"Note         : {note}")

    print("\n[1] PAYLOAD BRUT")
    print(json.dumps(result.payload, indent=2, ensure_ascii=False, default=str))

    if result.invoice is None:
        print("\n[2] VALIDATION InvoiceSchema : ECHEC")
    else:
        amount = result.invoice.amount
        supplier = result.invoice.supplier
        print("\n[2] VALIDATION InvoiceSchema : OK")
        print(f"    fournisseur : {supplier.name if supplier else '<absent>'}")
        print(f"    siret       : {supplier.siret if supplier else '<absent>'}")
        print(f"    HT / TVA / TTC : {amount.net_amount} / {amount.tax_amount} / {amount.gross_amount}")
        print(f"    statut      : {result.invoice.validation_status.value}")

    decision = result.decision
    rule_label = f"{decision.rule_id} v{decision.rule_version}" if decision.rule_id else "RULE-TVA-ZERO-001"
    print(f"\n[3] REGLE METIER : {rule_label}")
    print(f"    verdict     : {decision.status}")
    print(f"    motif       : {decision.reason}")
    for detail in decision.details:
        print(f"    detail      : {detail}")
    if decision.vat_regime:
        print(f"    régime TVA  : {decision.vat_regime}")
        print(f"    balance TVA : {'OUI' if decision.include_in_vat_balance else 'NON'}")
        print(f"    traitement  : {decision.accounting_treatment}")

    print("\n[4] DECISION FINALE")
    if decision.status == "VALIDATED":
        print("    >>> FACTURE VALIDEE (aucun routage HITL requis)")
    else:
        print("    >>> ROUTAGE HITL (validation humaine requise)")
        print("    >>> persister le régime TVA fournisseur après décision")
    print("=" * 72)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pilote d'intégration corpus -> InvoiceSchema -> RuleRegistryService"
    )
    parser.add_argument(
        "--invoice",
        type=Path,
        default=DEFAULT_INVOICE,
        help=f"facture cible (défaut: {DEFAULT_INVOICE.relative_to(REPO_ROOT)})",
    )
    parser.add_argument("--rules-dir", type=Path, default=DEFAULT_RULES_DIR)
    parser.add_argument(
        "--payload",
        type=Path,
        default=None,
        help="payload d'extraction simulé (JSON) au lieu de PyMuPDF+regex",
    )
    parser.add_argument("--supplier", default=None, help="override du nom fournisseur")
    parser.add_argument("--country", default=None, help="override du pays fournisseur")
    parser.add_argument("--vat-number", default=None, help="override du n° TVA fournisseur")
    parser.add_argument(
        "--simulate-zero-vat",
        action="store_true",
        help="force tax_amount=0 pour démontrer la désambiguïsation",
    )
    parser.add_argument("--json", action="store_true", help="sortie JSON machine")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = _parse_args(argv)
    invoice_path = args.invoice if args.invoice.is_absolute() else REPO_ROOT / args.invoice
    rules_dir = args.rules_dir if args.rules_dir.is_absolute() else REPO_ROOT / args.rules_dir
    payload_path = args.payload
    if payload_path is not None and not payload_path.is_absolute():
        payload_path = REPO_ROOT / payload_path

    try:
        result = run_pilot(
            invoice_path,
            rules_dir=rules_dir,
            payload_path=payload_path,
            supplier_override=args.supplier,
            country_override=args.country,
            vat_override=args.vat_number,
            simulate_zero_vat=args.simulate_zero_vat,
        )
    except (PilotExtractionError, OSError, json.JSONDecodeError) as exc:
        print(f"ECHEC PILOTE: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))
    else:
        _print_report(result)
    return 0 if result.validated else 2


if __name__ == "__main__":
    raise SystemExit(main())
