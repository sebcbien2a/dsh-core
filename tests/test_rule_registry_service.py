"""Tests for the Markdown-backed rule registry service."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from schemas.rule_registry_schema import BusinessRuleModel, RuleStatus
from services.rule_registry_service import RuleRegistryError, RuleRegistryService

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LOGGER_NAME = "services.rule_registry_service"


def _base_rule(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "rule_id": "RULE-TEST-001",
        "version": "1.0.0",
        "status": "approved",
        "domain": "accounting",
        "applies_to_nodes": ["ValidateInvoice"],
        "priority": 10,
        "conditions": [
            {"field": "supplier.country", "operator": "eq", "value": "FR"}
        ],
        "deterministic_actions": [
            {"action": "set_vat_rate", "parameters": {"rate": 0.0}}
        ],
    }
    data.update(overrides)
    return data


def _write_rule(
    directory: Path, filename: str, data: dict[str, Any]
) -> Path:
    frontmatter = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).strip()
    path = directory / filename
    path.write_text(f"---\n{frontmatter}\n---\n# Rule\n", encoding="utf-8")
    return path


def _touch(path: Path, delta: float = 10.0) -> None:
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + delta))


def test_load_approved_rule(tmp_path: Path) -> None:
    _write_rule(tmp_path, "RULE-TEST-001.md", _base_rule())

    service = RuleRegistryService()
    rules = service.load_rules(tmp_path)

    assert len(rules) == 1
    rule = rules[0]
    assert isinstance(rule, BusinessRuleModel)
    assert rule.status == RuleStatus.APPROVED
    assert rule.rule_id == "RULE-TEST-001"
    assert rule.version == "1.0.0"
    assert rule.domain == "accounting"
    assert rule.applies_to_nodes == ["ValidateInvoice"]
    assert rule.priority == 10
    assert rule.conditions[0].field == "supplier.country"
    assert rule.deterministic_actions[0].action == "set_vat_rate"
    assert service.get_rule("RULE-TEST-001") == rule
    assert service.get_rule("INCONNU") is None


@pytest.mark.parametrize(
    "status",
    [
        "draft",
        "pending_hitl",
        "deprecated",
        "rejected",
        "pending",
        "",
        None,
        "Approved",
        "APPROVED",
    ],
)
def test_non_approved_rules_are_ignored(
    tmp_path: Path, status: object, caplog: pytest.LogCaptureFixture
) -> None:
    suffix = "none" if status is None else ("empty" if status == "" else str(status))
    rejected_id = f"RULE-REJECTED-{suffix}"
    rejected_path = _write_rule(
        tmp_path,
        f"{rejected_id}.md",
        _base_rule(rule_id=rejected_id, status=status),
    )
    _write_rule(tmp_path, "RULE-APPROVED.md", _base_rule(rule_id="RULE-APPROVED"))

    service = RuleRegistryService()
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        rules = service.load_rules(tmp_path)

    assert [rule.rule_id for rule in rules] == ["RULE-APPROVED"]
    assert service.get_rule(rejected_id) is None
    assert rejected_path in service.ignored_rules
    assert service.ignored_rules[rejected_path] == status

    warnings = [record for record in caplog.records if record.name == LOGGER_NAME]
    assert warnings
    assert any(
        rejected_id in record.getMessage() and str(rejected_path) in record.getMessage()
        for record in warnings
    )


def test_ignored_rules_view_is_read_only(tmp_path: Path) -> None:
    rejected_path = _write_rule(
        tmp_path, "draft.md", _base_rule(rule_id="RULE-DRAFT", status="draft")
    )

    service = RuleRegistryService()
    service.load_rules(tmp_path)

    view = service.ignored_rules
    assert rejected_path in view
    with pytest.raises(TypeError):
        view[rejected_path] = "approved"  # type: ignore[index]


def test_get_rules_by_domain_is_case_insensitive(tmp_path: Path) -> None:
    _write_rule(
        tmp_path,
        "accounting-low.md",
        _base_rule(rule_id="RULE-ACC-LOW", domain="Accounting", priority=50),
    )
    _write_rule(
        tmp_path,
        "accounting-high.md",
        _base_rule(rule_id="RULE-ACC-HIGH", domain="accounting", priority=5),
    )
    _write_rule(
        tmp_path,
        "legal.md",
        _base_rule(rule_id="RULE-LEGAL", domain="legal", priority=1),
    )

    service = RuleRegistryService()
    service.load_rules(tmp_path)

    accounting = service.get_rules_by_domain("ACCOUNTING")
    assert [rule.rule_id for rule in accounting] == ["RULE-ACC-HIGH", "RULE-ACC-LOW"]

    padded = service.get_rules_by_domain("  Accounting  ")
    assert [rule.rule_id for rule in padded] == ["RULE-ACC-HIGH", "RULE-ACC-LOW"]

    assert service.get_rules_by_domain("unknown") == []


def test_get_applicable_rules_orders_by_priority(tmp_path: Path) -> None:
    _write_rule(
        tmp_path,
        "rule-c.md",
        _base_rule(rule_id="RULE-C", priority=30),
    )
    _write_rule(
        tmp_path,
        "rule-b.md",
        _base_rule(rule_id="RULE-B", priority=10),
    )
    _write_rule(
        tmp_path,
        "rule-a.md",
        _base_rule(rule_id="RULE-A", priority=10),
    )
    _write_rule(
        tmp_path,
        "other-node.md",
        _base_rule(rule_id="RULE-OTHER", applies_to_nodes=["ApplyTaxRules"]),
    )

    service = RuleRegistryService()
    service.load_rules(tmp_path)

    applicable = service.get_applicable_rules("ValidateInvoice")
    assert [rule.rule_id for rule in applicable] == ["RULE-A", "RULE-B", "RULE-C"]
    assert service.get_applicable_rules("  ValidateInvoice  ") == applicable
    assert [rule.rule_id for rule in service.get_applicable_rules("UnknownNode")] == []


def test_invalid_approved_rule_is_rejected(tmp_path: Path) -> None:
    invalid = _base_rule()
    del invalid["priority"]
    _write_rule(tmp_path, "invalid.md", invalid)

    service = RuleRegistryService()
    with pytest.raises(RuleRegistryError) as exc_info:
        service.load_rules(tmp_path)

    assert exc_info.value.code == "INVALID_RULE"


def test_duplicate_rule_id_is_rejected(tmp_path: Path) -> None:
    _write_rule(tmp_path, "first.md", _base_rule(rule_id="RULE-DUPLICATE"))
    _write_rule(tmp_path, "second.md", _base_rule(rule_id="RULE-DUPLICATE"))

    service = RuleRegistryService()
    with pytest.raises(RuleRegistryError) as exc_info:
        service.load_rules(tmp_path)

    assert exc_info.value.code == "DUPLICATE_RULE_ID"


def test_invalid_yaml_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "broken.md").write_text(
        "---\nrule_id: [unclosed\n---\n# Broken\n", encoding="utf-8"
    )

    service = RuleRegistryService()
    with pytest.raises(RuleRegistryError) as exc_info:
        service.load_rules(tmp_path)

    assert exc_info.value.code == "INVALID_YAML"


def test_missing_directory_is_rejected(tmp_path: Path) -> None:
    service = RuleRegistryService()
    with pytest.raises(RuleRegistryError) as exc_info:
        service.load_rules(tmp_path / "missing")

    assert exc_info.value.code == "RULES_DIRECTORY_NOT_FOUND"


def test_markdown_without_frontmatter_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("# Just notes\n\nNo rule here.\n", encoding="utf-8")
    _write_rule(tmp_path, "RULE-TEST-001.md", _base_rule())

    service = RuleRegistryService()
    rules = service.load_rules(tmp_path)

    assert [rule.rule_id for rule in rules] == ["RULE-TEST-001"]


def test_reload_changed_rules_detects_added_modified_deleted(tmp_path: Path) -> None:
    a_path = _write_rule(tmp_path, "a.md", _base_rule(rule_id="RULE-A", priority=10))

    service = RuleRegistryService()
    service.load_rules(tmp_path)
    assert service.get_rule("RULE-A") is not None

    assert service.reload_changed_rules() == (0, 0, 0)

    b_path = _write_rule(tmp_path, "b.md", _base_rule(rule_id="RULE-B", priority=20))
    assert service.reload_changed_rules() == (1, 0, 0)
    assert service.get_rule("RULE-A") is not None
    assert service.get_rule("RULE-B") is not None

    _write_rule(
        tmp_path,
        "a.md",
        _base_rule(rule_id="RULE-A", priority=5, domain="legal"),
    )
    _touch(a_path)
    assert service.reload_changed_rules() == (0, 1, 0)
    reloaded = service.get_rule("RULE-A")
    assert reloaded is not None
    assert reloaded.priority == 5
    assert reloaded.domain == "legal"
    assert service.get_rule("RULE-B") is not None

    b_path.unlink()
    assert service.reload_changed_rules() == (0, 0, 1)
    assert service.get_rule("RULE-B") is None
    assert service.get_rule("RULE-A") is not None


def test_reload_without_initial_load_raises() -> None:
    service = RuleRegistryService()
    with pytest.raises(RuleRegistryError) as exc_info:
        service.reload_changed_rules()

    assert exc_info.value.code == "RULES_NOT_LOADED"


def test_reload_removes_rule_when_status_regresses_to_draft(tmp_path: Path) -> None:
    a_path = _write_rule(tmp_path, "a.md", _base_rule(rule_id="RULE-A"))

    service = RuleRegistryService()
    service.load_rules(tmp_path)
    assert service.get_rule("RULE-A") is not None

    _write_rule(tmp_path, "a.md", _base_rule(rule_id="RULE-A", status="draft"))
    _touch(a_path)

    assert service.reload_changed_rules() == (0, 1, 0)
    assert service.get_rule("RULE-A") is None
    assert a_path in service.ignored_rules
    assert service.ignored_rules[a_path] == "draft"


def test_reload_detects_new_rule_in_previously_non_rule_markdown(
    tmp_path: Path,
) -> None:
    notes = tmp_path / "notes.md"
    notes.write_text("# Just notes\n\nNo rule here.\n", encoding="utf-8")

    service = RuleRegistryService()
    service.load_rules(tmp_path)
    assert service.reload_changed_rules() == (0, 0, 0)

    _write_rule(tmp_path, "notes.md", _base_rule(rule_id="RULE-NOTES"))
    _touch(notes)

    assert service.reload_changed_rules() == (0, 1, 0)
    assert service.get_rule("RULE-NOTES") is not None


def test_reload_missing_directory_raises(tmp_path: Path) -> None:
    directory = tmp_path / "rules"
    directory.mkdir()
    a_path = _write_rule(directory, "a.md", _base_rule(rule_id="RULE-A"))

    service = RuleRegistryService()
    service.load_rules(directory)
    a_path.unlink()
    directory.rmdir()

    with pytest.raises(RuleRegistryError) as exc_info:
        service.reload_changed_rules()

    assert exc_info.value.code == "RULES_DIRECTORY_NOT_FOUND"


def test_reload_duplicate_rule_id_is_rejected_atomically(tmp_path: Path) -> None:
    _write_rule(tmp_path, "a.md", _base_rule(rule_id="RULE-A"))

    service = RuleRegistryService()
    service.load_rules(tmp_path)

    _write_rule(tmp_path, "b.md", _base_rule(rule_id="RULE-A"))
    with pytest.raises(RuleRegistryError) as exc_info:
        service.reload_changed_rules()

    assert exc_info.value.code == "DUPLICATE_RULE_ID"
    assert service.get_rule("RULE-A") is not None
    assert len(service.get_applicable_rules("ValidateInvoice")) == 1


def test_load_real_tva_zero_disambiguation_rule() -> None:
    rule_path = (
        REPOSITORY_ROOT
        / "knowledge"
        / "rules"
        / "accounting"
        / "RULE_TVA_ZERO_DISAMBIGUATION_001.md"
    )
    assert rule_path.is_file()

    service = RuleRegistryService()
    service.load_rules(rule_path.parent)

    rule = service.get_rule("RULE-TVA-ZERO-001")
    assert rule is not None
    assert rule.status == RuleStatus.APPROVED
    assert rule.version == "1.0.0"
    assert rule.domain == "accounting"
    assert rule.applies_to_nodes == [
        "ClassifyTax",
        "ApplyTaxRules",
        "ValidateInvoice",
    ]
    assert rule.priority == 10
    assert rule.conditions[0].field == "amount.tax_amount"
    assert rule.conditions[0].operator == "eq"
    assert rule.conditions[0].value == 0

    action = rule.deterministic_actions[0]
    assert action.action == "disambiguate_zero_vat"
    parameters = action.parameters
    assert parameters["case_intracom_ue"]["vat_regime"] == (
        "INTRACOM_AUTOLIQUIDATION"
    )
    assert parameters["case_intracom_ue"]["include_in_vat_balance"] is True
    assert parameters["case_franchise_base_fr"]["vat_regime"] == (
        "EXONERE_FRANCHISE_293B"
    )
    assert parameters["case_franchise_base_fr"]["include_in_vat_balance"] is False
    assert (
        parameters["on_unknown_supplier_regime"]
        == "ROUTE_TO_HITL_AND_PERSIST_SUPPLIER_REGIME"
    )
