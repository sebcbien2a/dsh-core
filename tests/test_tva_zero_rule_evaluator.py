"""Tests for ``services.tva_zero_rule_evaluator``.

The rule registry is replaced by an in-memory fake so that every branch of the
``RULE-TVA-ZERO-001`` evaluation can be exercised without reading rule files.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import pytest

from schemas.invoice_schema import InvoiceSchema
from schemas.rule_registry_schema import BusinessRuleModel
from services import tva_zero_rule_evaluator as module
from services.rule_registry_service import RuleRegistryService
from services.tva_zero_rule_evaluator import (
    TARGET_RULE_ID,
    RuleDecision,
    evaluate_tva_zero_rule,
)

ACTION_PARAMETERS: dict[str, Any] = {
    "case_intracom_ue": {
        "include_in_vat_balance": True,
        "accounting_treatment": "AUTOLIQUIDATION",
    },
    "case_franchise_base_fr": {
        "include_in_vat_balance": False,
        "accounting_treatment": "FRANCHISE",
    },
    "on_unknown_supplier_regime": "ROUTE_TO_HITL",
}


def make_rule(**overrides: Any) -> BusinessRuleModel:
    """Build an approved ``RULE-TVA-ZERO-001`` rule.

    Args:
        **overrides: Fields replacing the default rule values.

    Returns:
        A validated business rule.
    """
    data: dict[str, Any] = {
        "rule_id": TARGET_RULE_ID,
        "version": "1.0",
        "status": "approved",
        "domain": "accounting",
        "applies_to_nodes": ["ApplyTaxRules"],
        "priority": 10,
        "conditions": [
            {"field": "amount.tax_amount", "operator": "eq", "value": 0}
        ],
        "deterministic_actions": [
            {
                "action": "disambiguate_zero_vat",
                "parameters": ACTION_PARAMETERS,
            }
        ],
    }
    data.update(overrides)
    return BusinessRuleModel(**data)


class FakeRegistry:
    """In-memory stand-in for ``RuleRegistryService``.

    Class attributes configure the behaviour shared by every instance created
    by the evaluator.

    Attributes:
        rule: Rule returned by ``get_rule`` when its id matches.
        ignored: Ignored rules exposed through ``ignored_rules``.
        loaded_dir: Directory last passed to ``load_rules``.
        ignored_rules: Read-only view of the ignored rules of this instance.
    """

    rule: BusinessRuleModel | None = None
    ignored: dict[Path, object] = {}
    loaded_dir: Path | None = None

    def __init__(self) -> None:
        """Snapshot the configured ignored rules."""
        self.ignored_rules: Mapping[Path, object] = MappingProxyType(
            dict(type(self).ignored)
        )

    def load_rules(self, directory: Path) -> list[BusinessRuleModel]:
        """Record the directory and load nothing.

        Args:
            directory: Directory the evaluator asked to load.

        Returns:
            An empty list.
        """
        type(self).loaded_dir = directory
        return []

    def get_rule(self, rule_id: str) -> BusinessRuleModel | None:
        """Return the configured rule when ``rule_id`` matches.

        Args:
            rule_id: Identifier requested by the evaluator.

        Returns:
            The configured rule, or ``None``.
        """
        rule = type(self).rule
        return rule if rule is not None and rule.rule_id == rule_id else None


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> type[FakeRegistry]:
    """Install ``FakeRegistry`` with an approved default rule.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        The fake registry class, to tweak per test.
    """
    FakeRegistry.rule = make_rule()
    FakeRegistry.ignored = {}
    FakeRegistry.loaded_dir = None
    monkeypatch.setattr(module, "RuleRegistryService", FakeRegistry)
    return FakeRegistry


def make_invoice(
    *,
    tax: str | int | float | None = "0",
    vat_number: str | None = None,
    metadata: dict[str, Any] | None = None,
    supplier: bool = True,
    gross: str = "100",
) -> InvoiceSchema:
    """Build a minimal invoice.

    Args:
        tax: Tax amount, or ``None`` to omit it.
        vat_number: Supplier VAT number, if any.
        metadata: Invoice metadata.
        supplier: Whether a supplier is present.
        gross: Gross amount.

    Returns:
        A validated invoice.
    """
    amount: dict[str, Any] = {"gross_amount": gross}
    if tax is not None:
        amount["tax_amount"] = tax
    data: dict[str, Any] = {"amount": amount, "metadata": metadata or {}}
    if supplier:
        party: dict[str, Any] = {"name": "ACME"}
        if vat_number is not None:
            party["vat_number"] = vat_number
        data["supplier"] = party
    return InvoiceSchema(**data)


def condition_rule(field: str, operator: str, value: Any) -> BusinessRuleModel:
    """Build a rule holding a single condition.

    Args:
        field: Dotted invoice field path.
        operator: Condition operator.
        value: Expected value.

    Returns:
        A validated business rule.
    """
    return make_rule(
        conditions=[{"field": field, "operator": operator, "value": value}]
    )


# --- RuleDecision -----------------------------------------------------------


def test_rule_decision_hitl_required_property() -> None:
    """``hitl_required`` is true only for the HITL status."""
    assert RuleDecision(status="HITL", reason="x").hitl_required is True
    assert RuleDecision(status="VALIDATED", reason="x").hitl_required is False


def test_rule_decision_is_frozen_with_defaults() -> None:
    """Defaults are empty and the dataclass is immutable."""
    decision = RuleDecision(status="VALIDATED", reason="x")
    assert decision.details == ()
    assert decision.rule_id is None
    with pytest.raises(AttributeError):
        decision.status = "HITL"  # type: ignore[misc]


# --- Registry level failures ------------------------------------------------


def test_rule_missing_returns_hitl(registry: type[FakeRegistry]) -> None:
    """An absent rule yields ``RULE_NOT_APPROVED``."""
    registry.rule = None
    decision = evaluate_tva_zero_rule(make_invoice())
    assert decision.status == "HITL"
    assert decision.reason == "RULE_NOT_APPROVED"
    assert decision.rule_id == TARGET_RULE_ID
    assert "statut brut" not in decision.details[0]


def test_rule_missing_reports_raw_status_of_ignored_rule(
    registry: type[FakeRegistry], tmp_path: Path
) -> None:
    """The raw status of a matching ignored rule is reported."""
    registry.rule = None
    registry.ignored = {tmp_path / "RULE_TVA_ZERO_draft.md": "draft"}
    decision = evaluate_tva_zero_rule(make_invoice())
    assert decision.reason == "RULE_NOT_APPROVED"
    assert "statut brut: 'draft'" in decision.details[0]


def test_rule_missing_ignores_unrelated_ignored_rules(
    registry: type[FakeRegistry], tmp_path: Path
) -> None:
    """Ignored rules with another prefix are not reported."""
    registry.rule = None
    registry.ignored = {tmp_path / "OTHER_RULE_001.md": "draft"}
    decision = evaluate_tva_zero_rule(make_invoice())
    assert "statut brut" not in decision.details[0]


def test_rules_dir_is_forwarded_as_path(
    registry: type[FakeRegistry], tmp_path: Path
) -> None:
    """A string rules directory is converted to ``Path``."""
    evaluate_tva_zero_rule(make_invoice(), str(tmp_path))  # type: ignore[arg-type]
    assert registry.loaded_dir == tmp_path


def test_rule_not_applicable_to_node(registry: type[FakeRegistry]) -> None:
    """A rule not declared for ``ApplyTaxRules`` routes to HITL."""
    registry.rule = make_rule(applies_to_nodes=["OtherNode", "Another"])
    decision = evaluate_tva_zero_rule(make_invoice())
    assert decision.reason == "RULE_NOT_APPLICABLE_TO_NODE"
    assert decision.hitl_required
    assert "OtherNode, Another" in decision.details[0]
    assert decision.rule_version == "1.0"


def test_registry_load_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Errors raised while loading rules are not swallowed."""

    class Boom(FakeRegistry):
        """Registry whose loading always fails."""

        def load_rules(self, directory: Path) -> list[BusinessRuleModel]:
            """Raise an ``OSError``.

            Args:
                directory: Ignored.

            Raises:
                OSError: Always.
            """
            raise OSError("unreadable")

    monkeypatch.setattr(module, "RuleRegistryService", Boom)
    with pytest.raises(OSError):
        evaluate_tva_zero_rule(make_invoice())


def test_real_registry_with_empty_directory(tmp_path: Path) -> None:
    """The real registry on an empty directory finds no rule."""
    decision = evaluate_tva_zero_rule(make_invoice(), tmp_path)
    assert decision.reason == "RULE_NOT_APPROVED"
    assert decision.hitl_required


# --- Tax amount / conditions ------------------------------------------------


def test_tax_amount_unknown_is_hitl(registry: type[FakeRegistry]) -> None:
    """A missing tax amount yields ``TAX_AMOUNT_UNKNOWN``."""
    decision = evaluate_tva_zero_rule(make_invoice(tax=None))
    assert decision.reason == "TAX_AMOUNT_UNKNOWN"
    assert decision.hitl_required


def test_non_zero_tax_does_not_trigger(registry: type[FakeRegistry]) -> None:
    """A non-zero tax leaves the rule untriggered."""
    decision = evaluate_tva_zero_rule(make_invoice(tax="20", gross="120"))
    assert decision.status == "VALIDATED"
    assert decision.reason == "RULE_NOT_TRIGGERED"
    assert decision.vat_regime is None
    assert decision.include_in_vat_balance is None


@pytest.mark.parametrize("tax", ["0", "0.00", "0.0000", 0, 0.0])
def test_zero_tax_representations_trigger(
    registry: type[FakeRegistry], tax: str | int | float
) -> None:
    """Every representation of zero triggers the rule."""
    decision = evaluate_tva_zero_rule(make_invoice(tax=tax))
    assert decision.reason == "AMBIGUOUS_ZERO_VAT"


def test_rule_without_conditions_always_triggers(
    registry: type[FakeRegistry],
) -> None:
    """A rule without conditions matches any invoice."""
    registry.rule = make_rule(conditions=[])
    decision = evaluate_tva_zero_rule(make_invoice(tax="20", gross="120"))
    assert decision.reason == "AMBIGUOUS_ZERO_VAT"


def test_action_not_found(registry: type[FakeRegistry]) -> None:
    """A rule without actions yields ``ACTION_NOT_FOUND``."""
    registry.rule = make_rule(deterministic_actions=[])
    decision = evaluate_tva_zero_rule(make_invoice())
    assert decision.reason == "ACTION_NOT_FOUND"
    assert decision.hitl_required


def test_action_with_empty_parameters_is_not_found(
    registry: type[FakeRegistry],
) -> None:
    """An action with empty parameters counts as missing."""
    registry.rule = make_rule(
        deterministic_actions=[
            {"action": "disambiguate_zero_vat", "parameters": {}}
        ]
    )
    assert evaluate_tva_zero_rule(make_invoice()).reason == "ACTION_NOT_FOUND"


def test_other_action_names_are_ignored(registry: type[FakeRegistry]) -> None:
    """Actions with another name are not used."""
    registry.rule = make_rule(
        deterministic_actions=[
            {"action": "something_else", "parameters": {"a": 1}}
        ]
    )
    assert evaluate_tva_zero_rule(make_invoice()).reason == "ACTION_NOT_FOUND"


# --- Supplier regime resolution ---------------------------------------------


@pytest.mark.parametrize(
    "vat", ["DE123456789", "de 123 456 789", "ES X1234567Z", "IT12345678901"]
)
def test_eu_non_fr_vat_prefix_is_intracom(
    registry: type[FakeRegistry], vat: str
) -> None:
    """A non-French EU VAT prefix selects the intra-EU regime."""
    decision = evaluate_tva_zero_rule(make_invoice(vat_number=vat))
    assert decision.status == "VALIDATED"
    assert decision.reason == "ZERO_VAT_DISAMBIGUATED"
    assert decision.vat_regime == "INTRACOM_AUTOLIQUIDATION"
    assert decision.include_in_vat_balance is True
    assert decision.accounting_treatment == "AUTOLIQUIDATION"
    assert any("préfixe TVA UE" in d for d in decision.details)


def test_vat_number_is_normalised_in_details(
    registry: type[FakeRegistry],
) -> None:
    """The VAT number is reported without spaces, in upper case."""
    decision = evaluate_tva_zero_rule(make_invoice(vat_number="de 12 34"))
    assert "supplier.vat_number=DE1234" in decision.details


def test_fr_vat_prefix_alone_is_ambiguous(registry: type[FakeRegistry]) -> None:
    """A French VAT prefix alone does not settle the regime."""
    decision = evaluate_tva_zero_rule(make_invoice(vat_number="FR12345678901"))
    assert decision.reason == "AMBIGUOUS_ZERO_VAT"
    assert decision.hitl_required


def test_non_eu_alpha_prefix_alone_is_ambiguous(
    registry: type[FakeRegistry],
) -> None:
    """A non-EU alphabetic prefix is ambiguous."""
    decision = evaluate_tva_zero_rule(make_invoice(vat_number="US123456"))
    assert decision.reason == "AMBIGUOUS_ZERO_VAT"


def test_numeric_prefix_vat_is_ambiguous(registry: type[FakeRegistry]) -> None:
    """A purely numeric VAT number is ambiguous."""
    decision = evaluate_tva_zero_rule(make_invoice(vat_number="12345678"))
    assert decision.reason == "AMBIGUOUS_ZERO_VAT"


def test_single_char_vat_is_ambiguous(registry: type[FakeRegistry]) -> None:
    """A one-character VAT number is ambiguous."""
    decision = evaluate_tva_zero_rule(make_invoice(vat_number="D"))
    assert decision.reason == "AMBIGUOUS_ZERO_VAT"


def test_no_supplier_is_ambiguous_and_reports_absent(
    registry: type[FakeRegistry],
) -> None:
    """Without supplier the details flag missing VAT number and country."""
    decision = evaluate_tva_zero_rule(make_invoice(supplier=False))
    assert decision.reason == "AMBIGUOUS_ZERO_VAT"
    assert "supplier.vat_number=<absent>" in decision.details
    assert "supplier.country=<inconnu>" in decision.details


def test_supplier_without_vat_number_is_ambiguous(
    registry: type[FakeRegistry],
) -> None:
    """A supplier without VAT number is routed to HITL."""
    decision = evaluate_tva_zero_rule(make_invoice())
    assert decision.reason == "AMBIGUOUS_ZERO_VAT"
    assert "routage: ROUTE_TO_HITL" in decision.details


def test_foreign_country_metadata_is_intracom(
    registry: type[FakeRegistry],
) -> None:
    """A foreign supplier country selects the intra-EU regime."""
    decision = evaluate_tva_zero_rule(
        make_invoice(metadata={"supplier_country": "DE"})
    )
    assert decision.vat_regime == "INTRACOM_AUTOLIQUIDATION"
    assert any("pays fournisseur hors France" in d for d in decision.details)


def test_french_country_without_mention_is_ambiguous(
    registry: type[FakeRegistry],
) -> None:
    """A French supplier without franchise mention is ambiguous."""
    decision = evaluate_tva_zero_rule(
        make_invoice(metadata={"supplier_country": "France"})
    )
    assert decision.reason == "AMBIGUOUS_ZERO_VAT"


@pytest.mark.parametrize("country", ["", "   ", None])
def test_blank_country_is_unknown(
    registry: type[FakeRegistry], country: str | None
) -> None:
    """A blank country is treated as unknown."""
    decision = evaluate_tva_zero_rule(
        make_invoice(metadata={"supplier_country": country})
    )
    assert decision.reason == "AMBIGUOUS_ZERO_VAT"
    assert "supplier.country=<inconnu>" in decision.details


@pytest.mark.parametrize(
    "metadata,text",
    [
        ({"mention_293b": True}, ""),
        ({}, "TVA non applicable, art. 293 B du CGI"),
        ({}, "art 293b"),
        ({"micro_entreprise": True}, ""),
        ({}, "Micro-entreprise"),
        ({}, "micro entreprise"),
        ({}, "MICROENTREPRISE"),
    ],
)
def test_french_franchise_detection(
    registry: type[FakeRegistry], metadata: dict[str, Any], text: str
) -> None:
    """293 B and micro-entreprise mentions select the franchise regime."""
    invoice = make_invoice(metadata={"supplier_country": "FR", **metadata})
    decision = evaluate_tva_zero_rule(invoice, source_text=text)
    assert decision.status == "VALIDATED"
    assert decision.vat_regime == "EXONERE_FRANCHISE_293B"
    assert decision.include_in_vat_balance is False
    assert decision.accounting_treatment == "FRANCHISE"


def test_franchise_mention_requires_french_country(
    registry: type[FakeRegistry],
) -> None:
    """A franchise mention without a French country is ignored."""
    decision = evaluate_tva_zero_rule(
        make_invoice(), source_text="art. 293 B du CGI"
    )
    assert decision.reason == "AMBIGUOUS_ZERO_VAT"


def test_foreign_country_wins_over_franchise_mention(
    registry: type[FakeRegistry],
) -> None:
    """A foreign country takes precedence over a franchise mention."""
    decision = evaluate_tva_zero_rule(
        make_invoice(metadata={"supplier_country": "DE", "mention_293b": True})
    )
    assert decision.vat_regime == "INTRACOM_AUTOLIQUIDATION"


def test_vat_prefix_wins_over_french_country(
    registry: type[FakeRegistry],
) -> None:
    """A non-French EU VAT prefix takes precedence over country ``FR``."""
    decision = evaluate_tva_zero_rule(
        make_invoice(
            vat_number="BE0123456789", metadata={"supplier_country": "FR"}
        )
    )
    assert decision.vat_regime == "INTRACOM_AUTOLIQUIDATION"


def test_unrelated_source_text_does_not_trigger_franchise(
    registry: type[FakeRegistry],
) -> None:
    """Text merely containing ``293`` is not a franchise mention."""
    decision = evaluate_tva_zero_rule(
        make_invoice(metadata={"supplier_country": "FR"}),
        source_text="Facture n° 293 - bagage",
    )
    assert decision.reason == "AMBIGUOUS_ZERO_VAT"


def test_missing_case_parameters_fall_back_to_hitl(
    registry: type[FakeRegistry],
) -> None:
    """Missing case parameters route to HITL with the configured route."""
    registry.rule = make_rule(
        deterministic_actions=[
            {
                "action": "disambiguate_zero_vat",
                "parameters": {"on_unknown_supplier_regime": "CUSTOM_ROUTE"},
            }
        ]
    )
    decision = evaluate_tva_zero_rule(make_invoice(vat_number="DE123"))
    assert decision.reason == "AMBIGUOUS_ZERO_VAT"
    assert "routage: CUSTOM_ROUTE" in decision.details


def test_case_parameters_not_a_mapping_fall_back_to_hitl(
    registry: type[FakeRegistry],
) -> None:
    """Non-mapping case parameters route to HITL."""
    registry.rule = make_rule(
        deterministic_actions=[
            {
                "action": "disambiguate_zero_vat",
                "parameters": {"case_intracom_ue": "oops"},
            }
        ]
    )
    decision = evaluate_tva_zero_rule(make_invoice(vat_number="DE123"))
    assert decision.reason == "AMBIGUOUS_ZERO_VAT"
    assert "routage: ROUTE_TO_HITL" in decision.details


def test_case_parameters_missing_keys_default_safely(
    registry: type[FakeRegistry],
) -> None:
    """Missing keys inside case parameters yield safe defaults."""
    registry.rule = make_rule(
        deterministic_actions=[
            {
                "action": "disambiguate_zero_vat",
                "parameters": {"case_intracom_ue": {"unexpected": 1}},
            }
        ]
    )
    decision = evaluate_tva_zero_rule(make_invoice(vat_number="DE123"))
    assert decision.status == "VALIDATED"
    assert decision.include_in_vat_balance is False
    assert decision.accounting_treatment is None


# --- Certified regime (supplier memory) -------------------------------------


@pytest.mark.parametrize(
    "regime,include,treatment",
    [
        ("INTRACOM_AUTOLIQUIDATION", True, "AUTOLIQUIDATION"),
        ("EXONERE_FRANCHISE_293B", False, "FRANCHISE"),
        ("  intracom_autoliquidation ", True, "AUTOLIQUIDATION"),
    ],
)
def test_certified_regime_is_applied(
    registry: type[FakeRegistry], regime: str, include: bool, treatment: str
) -> None:
    """A certified regime is applied, normalised for case and spaces."""
    decision = evaluate_tva_zero_rule(
        make_invoice(metadata={"vat_regime": regime})
    )
    assert decision.status == "VALIDATED"
    assert decision.reason == "SUPPLIER_REGIME_APPLIED"
    assert decision.vat_regime == regime.strip().upper()
    assert decision.include_in_vat_balance is include
    assert decision.accounting_treatment == treatment
    assert "source: mémoire fournisseur" in decision.details


def test_certified_regime_overrides_heuristics(
    registry: type[FakeRegistry],
) -> None:
    """A certified regime wins over the VAT prefix heuristics."""
    decision = evaluate_tva_zero_rule(
        make_invoice(
            vat_number="DE123", metadata={"vat_regime": "EXONERE_FRANCHISE_293B"}
        )
    )
    assert decision.reason == "SUPPLIER_REGIME_APPLIED"
    assert decision.vat_regime == "EXONERE_FRANCHISE_293B"


@pytest.mark.parametrize("regime", ["UNKNOWN_REGIME", "", None, 0])
def test_unknown_certified_regime_falls_back_to_heuristics(
    registry: type[FakeRegistry], regime: str | int | None
) -> None:
    """An unknown certified regime falls back to the heuristics."""
    decision = evaluate_tva_zero_rule(
        make_invoice(vat_number="DE123", metadata={"vat_regime": regime})
    )
    assert decision.reason == "ZERO_VAT_DISAMBIGUATED"
    assert decision.vat_regime == "INTRACOM_AUTOLIQUIDATION"


def test_certified_regime_with_invalid_case_params_falls_back(
    registry: type[FakeRegistry],
) -> None:
    """Invalid parameters for the certified case lead to HITL."""
    registry.rule = make_rule(
        deterministic_actions=[
            {
                "action": "disambiguate_zero_vat",
                "parameters": {
                    "case_intracom_ue": "bad",
                    "case_franchise_base_fr": ACTION_PARAMETERS[
                        "case_franchise_base_fr"
                    ],
                },
            }
        ]
    )
    decision = evaluate_tva_zero_rule(
        make_invoice(metadata={"vat_regime": "INTRACOM_AUTOLIQUIDATION"})
    )
    assert decision.reason == "AMBIGUOUS_ZERO_VAT"


def test_certified_regime_not_reached_when_tax_non_zero(
    registry: type[FakeRegistry],
) -> None:
    """A certified regime is irrelevant when the rule is not triggered."""
    decision = evaluate_tva_zero_rule(
        make_invoice(
            tax="20",
            gross="120",
            metadata={"vat_regime": "INTRACOM_AUTOLIQUIDATION"},
        )
    )
    assert decision.reason == "RULE_NOT_TRIGGERED"


# --- _resolve_field ---------------------------------------------------------


def test_resolve_field_nested_and_missing() -> None:
    """Dotted paths resolve, and missing segments give ``None``."""
    invoice = make_invoice(tax="5", gross="105")
    # InvoiceAmount serialises decimals as strings, even in python mode
    assert module._resolve_field(invoice, "amount.tax_amount") == "5"
    assert module._resolve_field(invoice, "supplier.name") == "ACME"
    assert module._resolve_field(invoice, "amount.nope") is None
    assert module._resolve_field(invoice, "nope.deeper") is None
    assert module._resolve_field(invoice, "amount.tax_amount.deeper") is None
    assert module._resolve_field(invoice, "") is None


def test_resolve_field_supplier_country_falls_back_to_metadata() -> None:
    """``supplier.country`` falls back to ``metadata['supplier_country']``."""
    invoice = make_invoice(metadata={"supplier_country": "ES"})
    assert module._resolve_field(invoice, "supplier.country") == "ES"
    assert module._resolve_field(make_invoice(), "supplier.country") is None


# --- _as_decimal / _values_equal --------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1.5", Decimal("1.5")),
        (3, Decimal("3")),
        (True, None),
        (False, None),
        (None, None),
        ("abc", None),
        ("", None),
        ([], None),
    ],
)
def test_as_decimal(value: Any, expected: Decimal | None) -> None:
    """``_as_decimal`` converts numbers and rejects everything else."""
    assert module._as_decimal(value) == expected


def test_as_decimal_unwraps_enum() -> None:
    """Enum members are unwrapped before conversion."""
    invoice = make_invoice()
    assert module._as_decimal(invoice.validation_status) is None


def test_values_equal() -> None:
    """Numeric values compare as decimals, others by equality."""
    assert module._values_equal("0.0", 0)
    assert module._values_equal(Decimal("1"), "1.00")
    assert not module._values_equal(Decimal("1"), 2)
    assert module._values_equal("abc", "abc")
    assert not module._values_equal("abc", "abd")
    assert not module._values_equal(None, 0)
    assert module._values_equal(None, None)
    assert module._values_equal(True, True)


def test_values_equal_with_enum() -> None:
    """Enum members compare through their value."""
    invoice = make_invoice()
    status = invoice.validation_status
    assert module._values_equal(status, status.value)


# --- _condition_matches operators -------------------------------------------


@pytest.mark.parametrize(
    "field,operator,value,expected",
    [
        ("amount.tax_amount", "eq", 5, True),
        ("amount.tax_amount", "EQ", 5, True),
        ("amount.tax_amount", "  eq  ", 5, True),
        ("amount.tax_amount", "eq", 6, False),
        ("amount.tax_amount", "ne", 6, True),
        ("amount.tax_amount", "ne", 5, False),
        ("amount.missing", "ne", 5, False),
        ("amount.tax_amount", "gt", 4, True),
        ("amount.tax_amount", "gt", 5, False),
        ("amount.tax_amount", "gte", 5, True),
        ("amount.tax_amount", "lt", 6, True),
        ("amount.tax_amount", "lt", 5, False),
        ("amount.tax_amount", "lte", 5, True),
        ("amount.tax_amount", "gt", "abc", False),
        ("amount.missing", "gt", 1, False),
        ("amount.tax_amount", "in", [4, "5", 6], True),
        ("amount.tax_amount", "in", [4, 5, 6], False),
        ("supplier.name", "in", ["ACME", "X"], True),
        ("supplier.name", "in", ["X"], False),
        ("supplier.name", "starts_with", "AC", True),
        ("supplier.name", "starts_with", "XX", False),
        ("supplier.name", "contains", "CM", True),
        ("supplier.name", "contains", "ZZ", False),
        ("amount.tax_amount", "starts_with", "5", True),
        ("amount.tax_amount", "contains", "5", True),
        ("amount.net_amount", "contains", "5", False),
        ("supplier.name", "exists", None, True),
        ("supplier.siret", "exists", None, False),
        ("supplier.name", "unknown_op", "x", False),
        ("supplier.name", "in", 5, False),
    ],
)
def test_condition_operators(
    field: str, operator: str, value: Any, expected: bool
) -> None:
    """Each operator matches or rejects as documented."""
    invoice = make_invoice(tax="5", gross="105")
    result = module._condition_matches(
        invoice, condition_rule(field, operator, value)
    )
    assert result is expected


def test_in_operator_with_string_expected_is_substring_check() -> None:
    """A string ``expected`` makes ``in`` a substring check."""
    invoice = make_invoice(tax="5", gross="105")
    rule = condition_rule("supplier.name", "in", "xxACMExx")
    assert module._condition_matches(invoice, rule) is True


def test_all_conditions_must_match() -> None:
    """One failing condition rejects the whole rule."""
    invoice = make_invoice(tax="5", gross="105")
    rule = make_rule(
        conditions=[
            {"field": "amount.tax_amount", "operator": "eq", "value": 5},
            {"field": "supplier.name", "operator": "eq", "value": "OTHER"},
        ]
    )
    assert module._condition_matches(invoice, rule) is False


def test_condition_on_none_actual_with_eq_none() -> None:
    """``eq None`` matches an absent field."""
    invoice = make_invoice()
    rule = condition_rule("supplier.siret", "eq", None)
    assert module._condition_matches(invoice, rule) is True


# --- _raw_status_for_rule / _action_parameters ------------------------------


def test_raw_status_for_rule_prefix_matching(tmp_path: Path) -> None:
    """The raw status is found through the rule id prefix."""
    service = FakeRegistry()
    service.ignored_rules = {
        tmp_path / "RULE_TVA_ZERO_001.md": "draft",
        tmp_path / "RULE_OTHER_001.md": "x",
    }
    typed = cast(RuleRegistryService, service)
    assert module._raw_status_for_rule(typed, "RULE-TVA-ZERO-001") == "draft"
    assert module._raw_status_for_rule(typed, "RULE-NOPE-001") is None
    service.ignored_rules = {}
    assert module._raw_status_for_rule(typed, "RULE-TVA-ZERO-001") is None


def test_raw_status_for_short_rule_id() -> None:
    """A short rule id does not break the prefix computation."""
    typed = cast(RuleRegistryService, FakeRegistry())
    assert module._raw_status_for_rule(typed, "X") is None


def test_action_parameters_lookup() -> None:
    """Parameters are returned by action name, or empty when absent."""
    rule = make_rule()
    assert (
        module._action_parameters(rule, "disambiguate_zero_vat")
        == ACTION_PARAMETERS
    )
    assert module._action_parameters(rule, "missing") == {}


# --- Defaults ---------------------------------------------------------------


def test_default_rules_dir_points_to_accounting_rules() -> None:
    """The default rules directory is the absolute accounting rules folder."""
    assert module.DEFAULT_RULES_DIR.parts[-3:] == (
        "knowledge",
        "rules",
        "accounting",
    )
    assert module.DEFAULT_RULES_DIR.is_absolute()
