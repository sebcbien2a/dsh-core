"""Business evaluation of the approved zero-VAT disambiguation rule.

``RuleRegistryService`` supplies the approved rule; this module evaluates its
typed conditions against an ``InvoiceSchema`` and resolves the deterministic
action parameters for ``RULE-TVA-ZERO-001``. It is shared by the pilot script
and the LangGraph orchestration layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from schemas.invoice_schema import InvoiceSchema
from schemas.rule_registry_schema import BusinessRuleModel
from services.invoice_producer_protocol import (
    EU_VAT_PREFIXES,
    normalise_country,
    normalise_vat,
)
from services.rule_registry_service import RuleRegistryService

TARGET_RULE_ID = "RULE-TVA-ZERO-001"
TARGET_RULE_NODE = "ApplyTaxRules"
DEFAULT_RULES_DIR = (
    Path(__file__).resolve().parents[1] / "knowledge" / "rules" / "accounting"
)
_CERTIFIED_REGIME_CASES = {
    "INTRACOM_AUTOLIQUIDATION": "case_intracom_ue",
    "EXONERE_FRANCHISE_293B": "case_franchise_base_fr",
}


@dataclass(frozen=True)
class RuleDecision:
    status: str
    reason: str
    rule_id: str | None = None
    rule_version: str | None = None
    vat_regime: str | None = None
    include_in_vat_balance: bool | None = None
    accounting_treatment: str | None = None
    details: tuple[str, ...] = field(default_factory=tuple)

    @property
    def hitl_required(self) -> bool:
        return self.status == "HITL"


def _resolve_field(invoice: InvoiceSchema, field_path: str) -> Any:
    data: Any = invoice.model_dump(mode="python")
    current: Any = data
    for part in field_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            current = None
            break
        current = current[part]
    if current is None and field_path == "supplier.country":
        return invoice.metadata.get("supplier_country")
    return current


def _as_decimal(value: Any) -> Decimal | None:
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _values_equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, Enum):
        actual = actual.value
    actual_decimal = _as_decimal(actual)
    expected_decimal = _as_decimal(expected)
    if actual_decimal is not None and expected_decimal is not None:
        return actual_decimal == expected_decimal
    return actual == expected


def _condition_matches(invoice: InvoiceSchema, rule: BusinessRuleModel) -> bool:
    for condition in rule.conditions:
        actual = _resolve_field(invoice, condition.field)
        operator = condition.operator.strip().lower()
        expected = condition.value
        if operator == "eq":
            matched = _values_equal(actual, expected)
        elif operator == "ne":
            matched = actual is not None and not _values_equal(actual, expected)
        elif operator in {"gt", "gte", "lt", "lte"}:
            actual_decimal = _as_decimal(actual)
            target = _as_decimal(expected)
            matched = (
                actual_decimal is not None
                and target is not None
                and {
                    "gt": actual_decimal > target,
                    "gte": actual_decimal >= target,
                    "lt": actual_decimal < target,
                    "lte": actual_decimal <= target,
                }[operator]
            )
        elif operator == "in" and isinstance(expected, Sequence):
            matched = actual in expected
        elif operator == "starts_with":
            matched = isinstance(actual, str) and actual.startswith(str(expected))
        elif operator == "contains":
            matched = isinstance(actual, str) and str(expected) in actual
        elif operator == "exists":
            matched = actual is not None
        else:
            matched = False
        if not matched:
            return False
    return True


def _raw_status_for_rule(service: RuleRegistryService, rule_id: str) -> object | None:
    prefix = "_".join(rule_id.split("-")[:3])
    for path, status in service.ignored_rules.items():
        if path.stem.startswith(prefix):
            return status
    return None


def _supplier_regime(
    invoice: InvoiceSchema, source_text: str
) -> tuple[str, str, tuple[str, ...]]:
    supplier = invoice.supplier
    vat_number = (
        normalise_vat(supplier.vat_number)
        if supplier and supplier.vat_number
        else ""
    )
    country = normalise_country(invoice.metadata.get("supplier_country"))
    prefix = vat_number[:2] if len(vat_number) >= 2 and vat_number[:2].isalpha() else ""
    details = [
        f"supplier.vat_number={vat_number or '<absent>'}",
        f"supplier.country={country or '<inconnu>'}",
    ]

    if prefix and prefix in EU_VAT_PREFIXES and prefix != "FR":
        details.append(f"préfixe TVA UE non-FR détecté: {prefix}")
        return "case_intracom_ue", "INTRACOM_AUTOLIQUIDATION", tuple(details)
    if country is not None and country != "FR":
        details.append(f"pays fournisseur hors France: {country}")
        return "case_intracom_ue", "INTRACOM_AUTOLIQUIDATION", tuple(details)

    mention_293b = bool(
        invoice.metadata.get("mention_293b")
        or re.search(r"293\s*B", source_text, re.IGNORECASE)
    )
    micro = bool(
        invoice.metadata.get("micro_entreprise")
        or re.search(r"micro[-\s]?entreprise", source_text, re.IGNORECASE)
    )
    if country == "FR" and (mention_293b or micro):
        details.append(f"mention 293B={mention_293b}, micro-entreprise={micro}")
        return "case_franchise_base_fr", "EXONERE_FRANCHISE_293B", tuple(details)

    return "on_unknown_supplier_regime", "", tuple(details)


def _action_parameters(
    rule: BusinessRuleModel, action_name: str
) -> Mapping[str, Any]:
    for action in rule.deterministic_actions:
        if action.action == action_name:
            return action.parameters
    return {}


def evaluate_tva_zero_rule(
    invoice: InvoiceSchema,
    rules_dir: Path = DEFAULT_RULES_DIR,
    *,
    source_text: str = "",
) -> RuleDecision:
    """Evaluate ``RULE-TVA-ZERO-001`` for a validated invoice."""

    service = RuleRegistryService()
    service.load_rules(Path(rules_dir))
    rule = service.get_rule(TARGET_RULE_ID)

    if rule is None:
        raw_status = _raw_status_for_rule(service, TARGET_RULE_ID)
        suffix = f" (statut brut: {raw_status!r})" if raw_status is not None else ""
        return RuleDecision(
            status="HITL",
            reason="RULE_NOT_APPROVED",
            rule_id=TARGET_RULE_ID,
            details=(f"règle absente du registre approuvé{suffix}",),
        )

    if TARGET_RULE_NODE not in rule.applies_to_nodes:
        return RuleDecision(
            status="HITL",
            reason="RULE_NOT_APPLICABLE_TO_NODE",
            rule_id=rule.rule_id,
            rule_version=rule.version,
            details=(f"nœuds déclarés: {', '.join(rule.applies_to_nodes)}",),
        )

    if invoice.amount.tax_amount is None:
        return RuleDecision(
            status="HITL",
            reason="TAX_AMOUNT_UNKNOWN",
            rule_id=rule.rule_id,
            rule_version=rule.version,
            details=(
                "amount.tax_amount absent: régime TVA indéterminé",
                "renseigner la TVA ou valider humainement",
            ),
        )

    if not _condition_matches(invoice, rule):
        return RuleDecision(
            status="VALIDATED",
            reason="RULE_NOT_TRIGGERED",
            rule_id=rule.rule_id,
            rule_version=rule.version,
            details=(
                f"amount.tax_amount={invoice.amount.tax_amount} (condition: == 0)",
                "TVA standard: aucune désambiguïsation requise",
            ),
        )

    parameters = _action_parameters(rule, "disambiguate_zero_vat")
    if not parameters:
        return RuleDecision(
            status="HITL",
            reason="ACTION_NOT_FOUND",
            rule_id=rule.rule_id,
            rule_version=rule.version,
            details=("action disambiguate_zero_vat absente du frontmatter",),
        )

    certified_regime = str(invoice.metadata.get("vat_regime") or "").strip().upper()
    certified_case = _CERTIFIED_REGIME_CASES.get(certified_regime)
    if certified_case is not None:
        certified_parameters = parameters.get(certified_case)
        if isinstance(certified_parameters, Mapping):
            return RuleDecision(
                status="VALIDATED",
                reason="SUPPLIER_REGIME_APPLIED",
                rule_id=rule.rule_id,
                rule_version=rule.version,
                vat_regime=certified_regime,
                include_in_vat_balance=bool(
                    certified_parameters.get("include_in_vat_balance")
                ),
                accounting_treatment=certified_parameters.get(
                    "accounting_treatment"
                ),
                details=(
                    f"régime TVA certifié: {certified_regime}",
                    "source: mémoire fournisseur",
                ),
            )

    case_key, vat_regime, details = _supplier_regime(invoice, source_text)
    case_parameters = parameters.get(case_key)

    if case_key == "on_unknown_supplier_regime" or not isinstance(
        case_parameters, Mapping
    ):
        return RuleDecision(
            status="HITL",
            reason="AMBIGUOUS_ZERO_VAT",
            rule_id=rule.rule_id,
            rule_version=rule.version,
            details=tuple(details)
            + (
                f"routage: {parameters.get('on_unknown_supplier_regime', 'ROUTE_TO_HITL')}",
                "persister le régime TVA fournisseur après validation humaine",
            ),
        )

    return RuleDecision(
        status="VALIDATED",
        reason="ZERO_VAT_DISAMBIGUATED",
        rule_id=rule.rule_id,
        rule_version=rule.version,
        vat_regime=vat_regime,
        include_in_vat_balance=bool(case_parameters.get("include_in_vat_balance")),
        accounting_treatment=case_parameters.get("accounting_treatment"),
        details=tuple(details),
    )


__all__ = [
    "DEFAULT_RULES_DIR",
    "RuleDecision",
    "TARGET_RULE_ID",
    "TARGET_RULE_NODE",
    "evaluate_tva_zero_rule",
]
