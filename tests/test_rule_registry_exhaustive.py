"""Exhaustive tests for the Markdown-backed rule registry (parsing, status, cache)."""

from __future__ import annotations

import datetime as dt
import logging
import os
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from schemas.rule_registry_schema import (
    ActionModel,
    BusinessRuleModel,
    ConditionModel,
    RuleStatus,
)
from services.rule_registry_service import RuleRegistryError, RuleRegistryService

LOGGER_NAME = "services.rule_registry_service"


def _rule(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "rule_id": "RULE-X-001",
        "version": "1.0.0",
        "status": "approved",
        "domain": "accounting",
        "applies_to_nodes": ["ValidateInvoice"],
        "priority": 10,
        "conditions": [{"field": "supplier.country", "operator": "eq", "value": "FR"}],
        "deterministic_actions": [{"action": "set_vat_rate", "parameters": {"rate": 0.0}}],
    }
    data.update(overrides)
    return data


def _md(data: dict[str, Any], body: str = "# Rule\n") -> str:
    fm = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{fm}\n---\n{body}"


def _write(directory: Path, name: str, data: dict[str, Any]) -> Path:
    path = directory / name
    path.write_text(_md(data), encoding="utf-8")
    return path


def _raw(directory: Path, name: str, content: str | bytes) -> Path:
    path = directory / name
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_bytes(content.encode("utf-8"))
    return path


def _bump(path: Path, delta: float = 10.0) -> None:
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + delta))


def _load_error(directory: Path) -> RuleRegistryError:
    with pytest.raises(RuleRegistryError) as exc_info:
        RuleRegistryService().load_rules(directory)
    return exc_info.value


MINIMAL_YAML = """\
rule_id: R1
version: "1"
status: approved
domain: d
applies_to_nodes: [N]
priority: 1
conditions: []
deterministic_actions: []
"""


# --------------------------------------------------------------------------
# Valid / incomplete / corrupt / no-frontmatter files
# --------------------------------------------------------------------------


class TestFileShapes:
    def test_valid_rule_with_body_content_after_frontmatter(self, tmp_path: Path) -> None:
        (tmp_path / "r.md").write_text(
            _md(_rule(), body="# Title\n\n---\nnot: frontmatter\n---\ntext\n"),
            encoding="utf-8",
        )
        rules = RuleRegistryService().load_rules(tmp_path)
        assert [r.rule_id for r in rules] == ["RULE-X-001"]

    def test_empty_directory_returns_empty_list(self, tmp_path: Path) -> None:
        service = RuleRegistryService()
        assert service.load_rules(tmp_path) == []
        assert dict(service.ignored_rules) == {}

    def test_non_markdown_files_are_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "r.txt").write_text(_md(_rule()), encoding="utf-8")
        (tmp_path / "r.yaml").write_text(_md(_rule()), encoding="utf-8")
        assert RuleRegistryService().load_rules(tmp_path) == []

    def test_nested_directories_are_scanned(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        _write(nested, "deep.md", _rule(rule_id="DEEP"))
        assert [r.rule_id for r in RuleRegistryService().load_rules(tmp_path)] == ["DEEP"]

    def test_directory_argument_accepts_str(self, tmp_path: Path) -> None:
        _write(tmp_path, "r.md", _rule())
        rules = RuleRegistryService().load_rules(str(tmp_path))  # type: ignore[arg-type]
        assert len(rules) == 1

    def test_file_path_instead_of_directory_is_rejected(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "r.md", _rule())
        with pytest.raises(RuleRegistryError) as exc:
            RuleRegistryService().load_rules(path)
        assert exc.value.code == "RULES_DIRECTORY_NOT_FOUND"

    @pytest.mark.parametrize(
        "content",
        ["", "   \n\n", "# Title only\n", "text\n---\nrule_id: x\n---\n"],
    )
    def test_files_without_frontmatter_are_silently_ignored(
        self, tmp_path: Path, content: str
    ) -> None:
        _raw(tmp_path, "n.md", content)
        service = RuleRegistryService()
        assert service.load_rules(tmp_path) == []
        assert dict(service.ignored_rules) == {}

    def test_leading_blank_lines_before_frontmatter_are_tolerated(
        self, tmp_path: Path
    ) -> None:
        _raw(tmp_path, "r.md", "\n\n  " + _md(_rule()).lstrip())
        assert len(RuleRegistryService().load_rules(tmp_path)) == 1

    def test_utf8_bom_is_tolerated(self, tmp_path: Path) -> None:
        _raw(tmp_path, "r.md", b"\xef\xbb\xbf" + _md(_rule()).encode("utf-8"))
        assert len(RuleRegistryService().load_rules(tmp_path)) == 1

    def test_crlf_line_endings(self, tmp_path: Path) -> None:
        _raw(tmp_path, "r.md", _md(_rule()).replace("\n", "\r\n"))
        assert len(RuleRegistryService().load_rules(tmp_path)) == 1

    def test_trailing_spaces_after_delimiters(self, tmp_path: Path) -> None:
        _raw(tmp_path, "r.md", f"---  \n{MINIMAL_YAML}---\t\n# body\n")
        assert [r.rule_id for r in RuleRegistryService().load_rules(tmp_path)] == ["R1"]

    def test_frontmatter_at_end_of_file_without_trailing_newline(
        self, tmp_path: Path
    ) -> None:
        _raw(tmp_path, "r.md", f"---\n{MINIMAL_YAML}---")
        assert len(RuleRegistryService().load_rules(tmp_path)) == 1

    def test_unterminated_frontmatter_is_invalid(self, tmp_path: Path) -> None:
        _raw(tmp_path, "r.md", f"---\n{MINIMAL_YAML}# no closing delimiter\n")
        err = _load_error(tmp_path)
        assert err.code == "INVALID_FRONTMATTER"
        assert err.path == tmp_path / "r.md"

    @pytest.mark.parametrize("content", ["---\n", "---", "---\n---\n", "---\n\n---\n"])
    def test_degenerate_frontmatter_never_loads_silently(
        self, tmp_path: Path, content: str
    ) -> None:
        _raw(tmp_path, "r.md", content)
        err = _load_error(tmp_path)
        assert err.code in {"INVALID_FRONTMATTER"}

    def test_frontmatter_must_be_a_mapping(self, tmp_path: Path) -> None:
        _raw(tmp_path, "r.md", "---\n- a\n- b\n---\n")
        assert _load_error(tmp_path).code == "INVALID_FRONTMATTER"

    def test_scalar_frontmatter_is_invalid(self, tmp_path: Path) -> None:
        _raw(tmp_path, "r.md", "---\njust text\n---\n")
        assert _load_error(tmp_path).code == "INVALID_FRONTMATTER"

    def test_dashes_prefix_not_a_delimiter(self, tmp_path: Path) -> None:
        _raw(tmp_path, "r.md", "----\nrule_id: x\n----\n")
        assert _load_error(tmp_path).code == "INVALID_FRONTMATTER"

    def test_corrupted_yaml(self, tmp_path: Path) -> None:
        _raw(tmp_path, "r.md", "---\nrule_id: [unclosed\nstatus: approved\n---\n")
        err = _load_error(tmp_path)
        assert err.code == "INVALID_YAML"
        assert "r.md" in str(err)

    def test_duplicate_yaml_keys_last_wins_or_fails_cleanly(self, tmp_path: Path) -> None:
        _raw(tmp_path, "r.md", f"---\n{MINIMAL_YAML}priority: 99\n---\n")
        rules = RuleRegistryService().load_rules(tmp_path)
        assert rules[0].priority == 99

    def test_binary_garbage_is_invalid_encoding(self, tmp_path: Path) -> None:
        _raw(tmp_path, "r.md", b"\xff\xfe\x00\x80\x81garbage")
        assert _load_error(tmp_path).code == "INVALID_ENCODING"

    def test_one_corrupt_file_aborts_whole_load(self, tmp_path: Path) -> None:
        _write(tmp_path, "good.md", _rule())
        _raw(tmp_path, "bad.md", "---\nx: [\n---\n")
        assert _load_error(tmp_path).code == "INVALID_YAML"

    def test_failed_load_does_not_replace_previous_state(self, tmp_path: Path) -> None:
        good = tmp_path / "good"
        good.mkdir()
        _write(good, "r.md", _rule())
        bad = tmp_path / "bad"
        bad.mkdir()
        _raw(bad, "r.md", "---\nx: [\n---\n")

        service = RuleRegistryService()
        service.load_rules(good)
        with pytest.raises(RuleRegistryError):
            service.load_rules(bad)
        assert service.get_rule("RULE-X-001") is not None

    @pytest.mark.parametrize(
        "missing",
        [
            "rule_id",
            "version",
            "status",
            "domain",
            "applies_to_nodes",
            "priority",
            "conditions",
            "deterministic_actions",
        ],
    )
    def test_incomplete_approved_rule_missing_field(
        self, tmp_path: Path, missing: str
    ) -> None:
        data = _rule()
        del data[missing]
        _write(tmp_path, "r.md", data)
        if missing == "status":
            service = RuleRegistryService()
            assert service.load_rules(tmp_path) == []
            assert service.ignored_rules[tmp_path / "r.md"] is None
        else:
            assert _load_error(tmp_path).code == "INVALID_RULE"

    def test_unknown_extra_field_is_rejected(self, tmp_path: Path) -> None:
        _write(tmp_path, "r.md", _rule(surprise=True))
        assert _load_error(tmp_path).code == "INVALID_RULE"

    @pytest.mark.parametrize(
        "overrides",
        [
            {"priority": "high"},
            {"priority": None},
            {"applies_to_nodes": "ValidateInvoice"},
            {"applies_to_nodes": [""]},
            {"applies_to_nodes": ["  "]},
            {"rule_id": "   "},
            {"version": ""},
            {"domain": " "},
            {"conditions": "nope"},
            {"deterministic_actions": {"action": "x"}},
            {"conditions": [{"field": "a", "operator": "eq"}]},
            {"conditions": [{"field": " ", "operator": "eq", "value": 1}]},
            {"conditions": [{"field": "a", "operator": " ", "value": 1}]},
            {"deterministic_actions": [{"action": " "}]},
            {"deterministic_actions": [{"action": "x", "parameters": [1]}]},
            {"deterministic_actions": [{"action": "x", "bogus": 1}]},
        ],
    )
    def test_invalid_field_values_are_rejected(
        self, tmp_path: Path, overrides: dict[str, Any]
    ) -> None:
        _write(tmp_path, "r.md", _rule(**overrides))
        assert _load_error(tmp_path).code == "INVALID_RULE"

    def test_unknown_status_value_is_ignored_not_validated(self, tmp_path: Path) -> None:
        _write(tmp_path, "r.md", {"rule_id": "Z", "status": "weird", "junk": 1})
        service = RuleRegistryService()
        assert service.load_rules(tmp_path) == []
        assert service.ignored_rules[tmp_path / "r.md"] == "weird"

    def test_error_carries_path_and_code(self, tmp_path: Path) -> None:
        _write(tmp_path, "r.md", _rule(priority="x"))
        err = _load_error(tmp_path)
        assert err.code == "INVALID_RULE"
        assert err.path == tmp_path / "r.md"
        assert isinstance(err, ValueError)
        assert str(err.path) in str(err)


# --------------------------------------------------------------------------
# Encodings, tabs, escapes
# --------------------------------------------------------------------------


class TestEncodingAndYamlQuirks:
    def test_utf8_accents_and_emoji(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "r.md",
            _rule(
                rule_id="RÈGLE-É-001",
                domain="comptabilité",
                applies_to_nodes=["Vérifier_Facture", "排序"],
                conditions=[{"field": "fournisseur.pays", "operator": "eq", "value": "Côte d'Ivoire 🇨🇮"}],
            ),
        )
        rule = RuleRegistryService().load_rules(tmp_path)[0]
        assert rule.rule_id == "RÈGLE-É-001"
        assert rule.conditions[0].value == "Côte d'Ivoire 🇨🇮"
        assert rule.applies_to_nodes == ["Vérifier_Facture", "排序"]

    @pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "latin-1", "cp1252"])
    def test_non_utf8_encodings_fail_cleanly(self, tmp_path: Path, encoding: str) -> None:
        text = _md(_rule(domain="comptabilité"))
        (tmp_path / "r.md").write_bytes(text.encode(encoding))
        err = _load_error(tmp_path)
        assert err.code in {"INVALID_ENCODING", "INVALID_FRONTMATTER", "INVALID_YAML"}

    def test_latin1_accent_byte_is_invalid_encoding(self, tmp_path: Path) -> None:
        _raw(tmp_path, "r.md", b"---\ndomain: caf\xe9\n---\n")
        assert _load_error(tmp_path).code == "INVALID_ENCODING"

    def test_latin1_non_rule_markdown_also_fails_loudly(self, tmp_path: Path) -> None:
        _raw(tmp_path, "notes.md", b"caf\xe9 notes")
        assert _load_error(tmp_path).code == "INVALID_ENCODING"

    def test_nul_byte_in_yaml_is_rejected(self, tmp_path: Path) -> None:
        _raw(tmp_path, "r.md", b"---\nrule_id: a\x00b\n---\n")
        assert _load_error(tmp_path).code == "INVALID_YAML"

    def test_tab_indentation_is_invalid_yaml(self, tmp_path: Path) -> None:
        content = (
            "---\nrule_id: R1\nversion: '1'\nstatus: approved\ndomain: d\n"
            "applies_to_nodes: [N]\npriority: 1\nconditions:\n"
            "\t- field: a\n\t  operator: eq\n\t  value: 1\n"
            "deterministic_actions: []\n---\n"
        )
        _raw(tmp_path, "r.md", content)
        assert _load_error(tmp_path).code == "INVALID_YAML"

    def test_tab_inside_quoted_scalar_is_preserved(self, tmp_path: Path) -> None:
        content = MINIMAL_YAML.replace(
            "conditions: []",
            'conditions:\n  - field: a\n    operator: eq\n    value: "x\\ty"',
        )
        _raw(tmp_path, "r.md", f"---\n{content}---\n")
        rule = RuleRegistryService().load_rules(tmp_path)[0]
        assert rule.conditions[0].value == "x\ty"

    def test_tab_between_key_and_value_is_invalid_yaml(self, tmp_path: Path) -> None:
        content = MINIMAL_YAML.replace("priority: 1", "priority:\t7")
        _raw(tmp_path, "r.md", f"---\n{content}---\n")
        assert _load_error(tmp_path).code == "INVALID_YAML"

    def test_tab_before_inline_comment_is_invalid_yaml(self, tmp_path: Path) -> None:
        content = MINIMAL_YAML.replace("priority: 1", "priority: 7\t# comment")
        _raw(tmp_path, "r.md", f"---\n{content}---\n")
        assert _load_error(tmp_path).code == "INVALID_YAML"

    def test_escape_sequences_in_double_quotes(self, tmp_path: Path) -> None:
        content = MINIMAL_YAML.replace(
            "conditions: []",
            'conditions:\n  - field: a\n    operator: regex\n    value: "^\\\\d+\\n\\u00e9\\"q\\""',
        )
        _raw(tmp_path, "r.md", f"---\n{content}---\n")
        rule = RuleRegistryService().load_rules(tmp_path)[0]
        assert rule.conditions[0].value == '^\\d+\né"q"'

    def test_single_quotes_do_not_interpret_escapes(self, tmp_path: Path) -> None:
        content = MINIMAL_YAML.replace(
            "conditions: []",
            "conditions:\n  - field: a\n    operator: regex\n    value: '\\d+\\n''x'''",
        )
        _raw(tmp_path, "r.md", f"---\n{content}---\n")
        rule = RuleRegistryService().load_rules(tmp_path)[0]
        assert rule.conditions[0].value == "\\d+\\n'x'"

    def test_invalid_escape_sequence_is_invalid_yaml(self, tmp_path: Path) -> None:
        content = MINIMAL_YAML.replace("rule_id: R1", 'rule_id: "bad\\qescape"')
        _raw(tmp_path, "r.md", f"---\n{content}---\n")
        assert _load_error(tmp_path).code == "INVALID_YAML"

    def test_block_scalars_and_colons(self, tmp_path: Path) -> None:
        content = MINIMAL_YAML.replace(
            "deterministic_actions: []",
            "deterministic_actions:\n  - action: notify\n    parameters:\n"
            "      message: |\n        line1: a\n        line2 # not a comment\n",
        )
        _raw(tmp_path, "r.md", f"---\n{content}---\n")
        rule = RuleRegistryService().load_rules(tmp_path)[0]
        assert rule.deterministic_actions[0].parameters["message"] == (
            "line1: a\nline2 # not a comment\n"
        )

    def test_yaml_comments_ignored(self, tmp_path: Path) -> None:
        _raw(tmp_path, "r.md", f"---\n# comment\n{MINIMAL_YAML}# end\n---\n")
        assert len(RuleRegistryService().load_rules(tmp_path)) == 1

    def test_yaml_anchors_and_aliases(self, tmp_path: Path) -> None:
        content = (
            "---\nrule_id: R1\nversion: '1'\nstatus: approved\ndomain: d\n"
            "applies_to_nodes: [N]\npriority: 1\n"
            "conditions:\n  - &c {field: a, operator: eq, value: 1}\n  - *c\n"
            "deterministic_actions: []\n---\n"
        )
        _raw(tmp_path, "r.md", content)
        rule = RuleRegistryService().load_rules(tmp_path)[0]
        assert rule.conditions[0] == rule.conditions[1]

    def test_unsafe_python_tag_is_rejected(self, tmp_path: Path) -> None:
        content = MINIMAL_YAML.replace(
            "domain: d", "domain: !!python/object/apply:os.system ['echo hi']"
        )
        _raw(tmp_path, "r.md", f"---\n{content}---\n")
        assert _load_error(tmp_path).code == "INVALID_YAML"

    def test_unquoted_version_float_is_rejected_by_schema(self, tmp_path: Path) -> None:
        # version: 1.0 parses as float; schema requires a string.
        content = MINIMAL_YAML.replace('version: "1"', "version: 1.0")
        _raw(tmp_path, "r.md", f"---\n{content}---\n")
        assert _load_error(tmp_path).code == "INVALID_RULE"

    def test_whitespace_is_stripped_from_text_fields(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "r.md",
            _rule(
                rule_id="  R-PAD  ",
                version=" 2 ",
                domain=" legal ",
                applies_to_nodes=[" A ", "A", "B"],
            ),
        )
        rule = RuleRegistryService().load_rules(tmp_path)[0]
        assert (rule.rule_id, rule.version, rule.domain) == ("R-PAD", "2", "legal")
        assert rule.applies_to_nodes == ["A", "B"]
        assert RuleRegistryService().get_rule("R-PAD") is None


# --------------------------------------------------------------------------
# Status filtering
# --------------------------------------------------------------------------


class TestStatusFiltering:
    def _all_statuses(self, tmp_path: Path) -> RuleRegistryService:
        for status in ("draft", "pending_hitl", "approved", "deprecated"):
            _write(tmp_path, f"{status}.md", _rule(rule_id=f"R-{status}", status=status))
        service = RuleRegistryService()
        service.load_rules(tmp_path)
        return service

    def test_only_approved_is_active(self, tmp_path: Path) -> None:
        service = self._all_statuses(tmp_path)
        assert service.get_rule("R-approved") is not None
        for status in ("draft", "pending_hitl", "deprecated"):
            assert service.get_rule(f"R-{status}") is None
        assert [r.rule_id for r in service.get_applicable_rules("ValidateInvoice")] == [
            "R-approved"
        ]
        assert [r.rule_id for r in service.get_rules_by_domain("accounting")] == [
            "R-approved"
        ]

    def test_ignored_rules_audit_records_raw_status(self, tmp_path: Path) -> None:
        service = self._all_statuses(tmp_path)
        recorded = {p.stem: s for p, s in service.ignored_rules.items()}
        assert recorded == {
            "draft": "draft",
            "pending_hitl": "pending_hitl",
            "deprecated": "deprecated",
        }

    def test_pending_approval_alias_is_not_a_known_status(self, tmp_path: Path) -> None:
        # The schema names the pending state "pending_hitl"; "pending_approval"
        # is not an enum member but is still safely ignored by the registry.
        assert "pending_approval" not in {s.value for s in RuleStatus}
        _write(tmp_path, "p.md", _rule(rule_id="P", status="pending_approval"))
        service = RuleRegistryService()
        assert service.load_rules(tmp_path) == []
        assert service.ignored_rules[tmp_path / "p.md"] == "pending_approval"

    def test_enum_values(self) -> None:
        assert {s.value for s in RuleStatus} == {
            "draft",
            "pending_hitl",
            "approved",
            "deprecated",
        }
        assert RuleStatus("approved") is RuleStatus.APPROVED
        with pytest.raises(ValueError):
            RuleStatus("pending_approval")

    @pytest.mark.parametrize("status", ["Approved", "APPROVED", " approved", "approved ", "approuvé"])
    def test_status_matching_is_exact(self, tmp_path: Path, status: str) -> None:
        _write(tmp_path, "r.md", _rule(status=status))
        service = RuleRegistryService()
        assert service.load_rules(tmp_path) == []
        assert service.ignored_rules[tmp_path / "r.md"] == status

    @pytest.mark.parametrize("status", [None, 1, True, ["approved"], {"a": 1}, 1.5])
    def test_non_string_status_is_ignored(self, tmp_path: Path, status: Any) -> None:
        _write(tmp_path, "r.md", _rule(status=status))
        service = RuleRegistryService()
        assert service.load_rules(tmp_path) == []
        assert (tmp_path / "r.md") in service.ignored_rules

    def test_non_approved_rule_is_not_schema_validated(self, tmp_path: Path) -> None:
        _write(tmp_path, "d.md", {"status": "draft"})
        service = RuleRegistryService()
        assert service.load_rules(tmp_path) == []
        assert service.ignored_rules[tmp_path / "d.md"] == "draft"

    def test_draft_with_broken_yaml_still_raises(self, tmp_path: Path) -> None:
        _raw(tmp_path, "d.md", "---\nstatus: draft\nx: [\n---\n")
        assert _load_error(tmp_path).code == "INVALID_YAML"

    def test_warning_logged_for_ignored_rule(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _write(tmp_path, "d.md", _rule(rule_id="R-DRAFT", status="draft"))
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            RuleRegistryService().load_rules(tmp_path)
        messages = [r.getMessage() for r in caplog.records if r.name == LOGGER_NAME]
        assert any("R-DRAFT" in m and "draft" in m for m in messages)

    def test_warning_falls_back_to_file_stem_when_rule_id_not_a_string(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _write(tmp_path, "stem-name.md", {"rule_id": 42, "status": "draft"})
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            RuleRegistryService().load_rules(tmp_path)
        assert any("stem-name" in r.getMessage() for r in caplog.records)

    def test_no_warning_for_approved(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _write(tmp_path, "r.md", _rule())
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            RuleRegistryService().load_rules(tmp_path)
        assert not [r for r in caplog.records if r.name == LOGGER_NAME]

    def test_duplicate_id_between_approved_and_draft_is_allowed(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path, "a.md", _rule(rule_id="DUP", status="approved"))
        _write(tmp_path, "b.md", _rule(rule_id="DUP", status="draft"))
        assert [r.rule_id for r in RuleRegistryService().load_rules(tmp_path)] == ["DUP"]

    def test_duplicate_approved_ids_across_subdirectories(self, tmp_path: Path) -> None:
        (tmp_path / "sub").mkdir()
        _write(tmp_path, "a.md", _rule(rule_id="DUP"))
        _write(tmp_path / "sub", "b.md", _rule(rule_id="DUP"))
        err = _load_error(tmp_path)
        assert err.code == "DUPLICATE_RULE_ID"
        assert err.path is not None

    def test_ignored_rules_replaced_on_each_load(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "d.md", _rule(status="draft"))
        service = RuleRegistryService()
        service.load_rules(tmp_path)
        assert path in service.ignored_rules
        path.unlink()
        service.load_rules(tmp_path)
        assert dict(service.ignored_rules) == {}

    def test_status_promotion_to_approved_on_reload(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "r.md", _rule(rule_id="R", status="pending_hitl"))
        service = RuleRegistryService()
        service.load_rules(tmp_path)
        assert service.get_rule("R") is None

        _write(tmp_path, "r.md", _rule(rule_id="R", status="approved"))
        _bump(path)
        assert service.reload_changed_rules() == (0, 1, 0)
        assert service.get_rule("R") is not None
        assert path not in service.ignored_rules

    def test_approved_rule_deprecated_on_reload(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "r.md", _rule(rule_id="R"))
        service = RuleRegistryService()
        service.load_rules(tmp_path)

        _write(tmp_path, "r.md", _rule(rule_id="R", status="deprecated"))
        _bump(path)
        service.reload_changed_rules()
        assert service.get_rule("R") is None
        assert service.get_applicable_rules("ValidateInvoice") == []
        assert service.ignored_rules[path] == "deprecated"


# --------------------------------------------------------------------------
# Complex conditions, unknown operators, invalid dates
# --------------------------------------------------------------------------


class TestConditions:
    def test_complex_nested_conditions_and_actions(self, tmp_path: Path) -> None:
        conditions = [
            {"field": "amount", "operator": "gte", "value": 1000.5},
            {"field": "supplier.country", "operator": "in", "value": ["FR", "BE", "DE"]},
            {"field": "meta", "operator": "matches", "value": {"a": {"b": [1, 2, {"c": None}]}}},
            {"field": "flag", "operator": "eq", "value": True},
            {"field": "missing", "operator": "is_null", "value": None},
        ]
        actions = [
            {"action": "set_vat_rate", "parameters": {"rate": 0.2, "tiers": [1, 2]}},
            {"action": "flag_for_review"},
        ]
        _write(tmp_path, "r.md", _rule(conditions=conditions, deterministic_actions=actions))
        rule = RuleRegistryService().load_rules(tmp_path)[0]
        assert len(rule.conditions) == 5
        assert rule.conditions[1].value == ["FR", "BE", "DE"]
        assert rule.conditions[2].value == {"a": {"b": [1, 2, {"c": None}]}}
        assert rule.conditions[3].value is True
        assert rule.conditions[4].value is None
        assert rule.deterministic_actions[1].parameters == {}

    def test_many_conditions(self, tmp_path: Path) -> None:
        conditions = [
            {"field": f"f{i}", "operator": "eq", "value": i} for i in range(300)
        ]
        _write(tmp_path, "r.md", _rule(conditions=conditions))
        rule = RuleRegistryService().load_rules(tmp_path)[0]
        assert [c.value for c in rule.conditions] == list(range(300))

    def test_empty_conditions_and_actions_are_allowed(self, tmp_path: Path) -> None:
        _write(tmp_path, "r.md", _rule(conditions=[], deterministic_actions=[]))
        rule = RuleRegistryService().load_rules(tmp_path)[0]
        assert rule.conditions == [] and rule.deterministic_actions == []

    @pytest.mark.parametrize(
        "operator", ["???", "UNKNOWN_OP", "between_dates", "EQ", "=", "~~", "drop table"]
    )
    def test_unknown_operators_are_accepted_by_schema(
        self, tmp_path: Path, operator: str
    ) -> None:
        # Operator vocabulary is not enforced at registry level (documented gap):
        # the evaluation layer is responsible for rejecting unsupported operators.
        _write(
            tmp_path,
            "r.md",
            _rule(conditions=[{"field": "a", "operator": operator, "value": 1}]),
        )
        rule = RuleRegistryService().load_rules(tmp_path)[0]
        assert rule.conditions[0].operator == operator

    def test_operator_is_stripped(self) -> None:
        cond = ConditionModel(field=" a ", operator=" eq ", value=1)
        assert (cond.field, cond.operator) == ("a", "eq")

    @pytest.mark.parametrize("operator", [1, None, ["eq"], {"x": 1}])
    def test_non_string_operator_rejected(self, tmp_path: Path, operator: Any) -> None:
        _write(
            tmp_path,
            "r.md",
            _rule(conditions=[{"field": "a", "operator": operator, "value": 1}]),
        )
        assert _load_error(tmp_path).code == "INVALID_RULE"

    def test_condition_extra_key_rejected(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "r.md",
            _rule(conditions=[{"field": "a", "operator": "eq", "value": 1, "negate": True}]),
        )
        assert _load_error(tmp_path).code == "INVALID_RULE"

    def test_condition_value_is_required_but_may_be_null(self, tmp_path: Path) -> None:
        _write(
            tmp_path, "r.md", _rule(conditions=[{"field": "a", "operator": "eq", "value": None}])
        )
        assert RuleRegistryService().load_rules(tmp_path)[0].conditions[0].value is None

    def test_unquoted_yaml_date_becomes_date_object(self, tmp_path: Path) -> None:
        content = MINIMAL_YAML.replace(
            "conditions: []",
            "conditions:\n  - field: invoice_date\n    operator: after\n    value: 2024-01-31",
        )
        _raw(tmp_path, "r.md", f"---\n{content}---\n")
        value = RuleRegistryService().load_rules(tmp_path)[0].conditions[0].value
        assert value == dt.date(2024, 1, 31)

    def test_unquoted_yaml_datetime_becomes_datetime_object(self, tmp_path: Path) -> None:
        content = MINIMAL_YAML.replace(
            "conditions: []",
            "conditions:\n  - field: d\n    operator: after\n    value: 2024-01-31T10:00:00Z",
        )
        _raw(tmp_path, "r.md", f"---\n{content}---\n")
        value = RuleRegistryService().load_rules(tmp_path)[0].conditions[0].value
        assert isinstance(value, dt.datetime)

    @pytest.mark.parametrize(
        "raw", ["2024-02-30", "2024-13-01", "31/01/2024", "not-a-date", "2024-1-1x"]
    )
    def test_invalid_dates_in_quoted_value_are_kept_as_strings(
        self, tmp_path: Path, raw: str
    ) -> None:
        # The registry does not validate date semantics inside condition values.
        _write(
            tmp_path,
            "r.md",
            _rule(conditions=[{"field": "d", "operator": "after", "value": raw}]),
        )
        assert RuleRegistryService().load_rules(tmp_path)[0].conditions[0].value == raw

    @pytest.mark.parametrize("raw", ["2024-13-45", "2024-02-30"])
    def test_unquoted_impossible_date_fails_cleanly(self, tmp_path: Path, raw: str) -> None:
        content = MINIMAL_YAML.replace(
            "conditions: []",
            f"conditions:\n  - field: d\n    operator: after\n    value: {raw}",
        )
        _raw(tmp_path, "r.md", f"---\n{content}---\n")
        err = _load_error(tmp_path)
        assert err.code == "INVALID_YAML"
        assert err.path == tmp_path / "r.md"

    def test_date_as_top_level_field_is_rejected_by_extra_forbid(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path, "r.md", _rule(effective_date="not-a-date"))
        assert _load_error(tmp_path).code == "INVALID_RULE"

    def test_model_is_frozen(self) -> None:
        rule = BusinessRuleModel.model_validate(_rule())
        with pytest.raises(ValidationError):
            rule.priority = 1  # type: ignore[misc]
        with pytest.raises(ValidationError):
            rule.conditions[0].operator = "ne"  # type: ignore[misc]

    def test_action_model_default_parameters_not_shared(self) -> None:
        a, b = ActionModel(action="x"), ActionModel(action="y")
        assert a.parameters is not b.parameters

    @pytest.mark.parametrize("priority", [-5, 0, 2**40])
    def test_priority_range_values(self, tmp_path: Path, priority: int) -> None:
        _write(tmp_path, "r.md", _rule(priority=priority))
        assert RuleRegistryService().load_rules(tmp_path)[0].priority == priority

    def test_priority_float_with_fraction_rejected(self, tmp_path: Path) -> None:
        _write(tmp_path, "r.md", _rule(priority=1.5))
        assert _load_error(tmp_path).code == "INVALID_RULE"


# --------------------------------------------------------------------------
# Queries and ordering
# --------------------------------------------------------------------------


class TestQueries:
    def test_ordering_by_priority_then_rule_id(self, tmp_path: Path) -> None:
        for rid, prio in [("B", 1), ("A", 1), ("C", 0), ("D", 5)]:
            _write(tmp_path, f"{rid}.md", _rule(rule_id=rid, priority=prio))
        service = RuleRegistryService()
        loaded = service.load_rules(tmp_path)
        assert [r.rule_id for r in loaded] == ["C", "A", "B", "D"]

    def test_queries_before_load_return_empty(self) -> None:
        service = RuleRegistryService()
        assert service.get_rule("x") is None
        assert service.get_rules_by_domain("x") == []
        assert service.get_applicable_rules("x") == []

    def test_applicable_rules_multi_node_and_case_sensitive(self, tmp_path: Path) -> None:
        _write(tmp_path, "r.md", _rule(applies_to_nodes=["A", "B"]))
        service = RuleRegistryService()
        service.load_rules(tmp_path)
        assert len(service.get_applicable_rules("A")) == 1
        assert len(service.get_applicable_rules("B")) == 1
        assert service.get_applicable_rules("a") == []
        assert service.get_applicable_rules("") == []

    def test_domain_casefold_unicode(self, tmp_path: Path) -> None:
        _write(tmp_path, "r.md", _rule(domain="Straße"))
        service = RuleRegistryService()
        service.load_rules(tmp_path)
        assert len(service.get_rules_by_domain("STRASSE")) == 1


# --------------------------------------------------------------------------
# Cache / hot reload
# --------------------------------------------------------------------------


class TestCacheAndHotReload:
    def test_reload_idempotent_without_changes(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", _rule(rule_id="A"))
        service = RuleRegistryService()
        service.load_rules(tmp_path)
        before = service.get_rule("A")
        assert service.reload_changed_rules() == (0, 0, 0)
        assert service.reload_changed_rules() == (0, 0, 0)
        assert service.get_rule("A") is before

    def test_unchanged_files_are_not_reparsed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write(tmp_path, "a.md", _rule(rule_id="A"))
        b = _write(tmp_path, "b.md", _rule(rule_id="B"))
        service = RuleRegistryService()
        service.load_rules(tmp_path)

        parsed: list[Path] = []
        original = RuleRegistryService._load_single

        def spy(self: RuleRegistryService, path: Path):  # type: ignore[no-untyped-def]
            parsed.append(path)
            return original(self, path)

        monkeypatch.setattr(RuleRegistryService, "_load_single", spy)
        _write(tmp_path, "b.md", _rule(rule_id="B", priority=1))
        _bump(b)
        assert service.reload_changed_rules() == (0, 1, 0)
        assert parsed == [b]

    def test_unchanged_rule_object_identity_preserved(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.md", _rule(rule_id="A"))
        b = _write(tmp_path, "b.md", _rule(rule_id="B"))
        service = RuleRegistryService()
        service.load_rules(tmp_path)
        a_before = service.get_rule("A")
        _write(tmp_path, "b.md", _rule(rule_id="B", priority=3))
        _bump(b)
        service.reload_changed_rules()
        assert service.get_rule("A") is a_before

    def test_noop_reload_when_directory_is_empty(self, tmp_path: Path) -> None:
        service = RuleRegistryService()
        service.load_rules(tmp_path)
        assert service.reload_changed_rules() == (0, 0, 0)

    def test_add_in_new_subdirectory(self, tmp_path: Path) -> None:
        service = RuleRegistryService()
        service.load_rules(tmp_path)
        (tmp_path / "new").mkdir()
        _write(tmp_path / "new", "n.md", _rule(rule_id="N"))
        assert service.reload_changed_rules() == (1, 0, 0)
        assert service.get_rule("N") is not None

    def test_rename_counts_as_add_and_delete(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "old.md", _rule(rule_id="R"))
        service = RuleRegistryService()
        service.load_rules(tmp_path)
        path.rename(tmp_path / "new.md")
        assert service.reload_changed_rules() == (1, 0, 1)
        assert service.get_rule("R") is not None

    def test_rule_id_change_in_same_file_drops_old_id(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "a.md", _rule(rule_id="OLD"))
        service = RuleRegistryService()
        service.load_rules(tmp_path)
        _write(tmp_path, "a.md", _rule(rule_id="NEW"))
        _bump(path)
        service.reload_changed_rules()
        assert service.get_rule("OLD") is None
        assert service.get_rule("NEW") is not None

    def test_swap_rule_ids_between_files(self, tmp_path: Path) -> None:
        a = _write(tmp_path, "a.md", _rule(rule_id="X"))
        b = _write(tmp_path, "b.md", _rule(rule_id="Y"))
        service = RuleRegistryService()
        service.load_rules(tmp_path)
        _write(tmp_path, "a.md", _rule(rule_id="Y"))
        _write(tmp_path, "b.md", _rule(rule_id="X"))
        _bump(a)
        _bump(b)
        assert service.reload_changed_rules() == (0, 2, 0)
        assert service.get_rule("X") is not None and service.get_rule("Y") is not None

    def test_reload_duplicate_is_atomic(self, tmp_path: Path) -> None:
        a = _write(tmp_path, "a.md", _rule(rule_id="A"))
        service = RuleRegistryService()
        service.load_rules(tmp_path)
        _write(tmp_path, "b.md", _rule(rule_id="A", priority=99))

        with pytest.raises(RuleRegistryError) as exc:
            service.reload_changed_rules()
        assert exc.value.code == "DUPLICATE_RULE_ID"
        assert service.get_rule("A").priority == 10  # type: ignore[union-attr]
        assert a.exists()

    def test_reload_corrupt_file_is_atomic_and_retried(self, tmp_path: Path) -> None:
        a = _write(tmp_path, "a.md", _rule(rule_id="A"))
        service = RuleRegistryService()
        service.load_rules(tmp_path)

        a.write_text("---\nx: [\n---\n", encoding="utf-8")
        _bump(a)
        with pytest.raises(RuleRegistryError) as exc:
            service.reload_changed_rules()
        assert exc.value.code == "INVALID_YAML"
        assert service.get_rule("A") is not None
        # Failure persists until the file is fixed (fingerprints not committed).
        with pytest.raises(RuleRegistryError):
            service.reload_changed_rules()

        _write(tmp_path, "a.md", _rule(rule_id="A", priority=2))
        _bump(a, 20.0)
        assert service.reload_changed_rules() == (0, 1, 0)
        assert service.get_rule("A").priority == 2  # type: ignore[union-attr]

    def test_reload_invalid_rule_keeps_previous_state(self, tmp_path: Path) -> None:
        a = _write(tmp_path, "a.md", _rule(rule_id="A"))
        service = RuleRegistryService()
        service.load_rules(tmp_path)
        _write(tmp_path, "a.md", _rule(rule_id="A", priority="bad"))
        _bump(a)
        with pytest.raises(RuleRegistryError) as exc:
            service.reload_changed_rules()
        assert exc.value.code == "INVALID_RULE"
        assert service.get_rule("A").priority == 10  # type: ignore[union-attr]

    def test_reload_after_directory_removed(self, tmp_path: Path) -> None:
        directory = tmp_path / "rules"
        directory.mkdir()
        service = RuleRegistryService()
        service.load_rules(directory)
        directory.rmdir()
        with pytest.raises(RuleRegistryError) as exc:
            service.reload_changed_rules()
        assert exc.value.code == "RULES_DIRECTORY_NOT_FOUND"

    def test_load_rules_switches_directory(self, tmp_path: Path) -> None:
        d1, d2 = tmp_path / "d1", tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        _write(d1, "a.md", _rule(rule_id="A"))
        _write(d2, "b.md", _rule(rule_id="B"))
        service = RuleRegistryService()
        service.load_rules(d1)
        service.load_rules(d2)
        assert service.get_rule("A") is None
        assert service.get_rule("B") is not None
        _write(d1, "c.md", _rule(rule_id="C"))
        assert service.reload_changed_rules() == (0, 0, 0)

    def test_load_rules_rebuilds_cache_from_scratch(self, tmp_path: Path) -> None:
        a = _write(tmp_path, "a.md", _rule(rule_id="A", priority=1))
        service = RuleRegistryService()
        service.load_rules(tmp_path)
        _write(tmp_path, "a.md", _rule(rule_id="A", priority=2))
        # Same mtime would not be detected by reload; a full load always is.
        os.utime(a, (a.stat().st_atime, a.stat().st_mtime))
        service.load_rules(tmp_path)
        assert service.get_rule("A").priority == 2  # type: ignore[union-attr]

    def test_reload_detects_modified_non_rule_file_becoming_corrupt(
        self, tmp_path: Path
    ) -> None:
        notes = _raw(tmp_path, "n.md", "# plain\n")
        service = RuleRegistryService()
        service.load_rules(tmp_path)
        _raw(tmp_path, "n.md", "---\nbroken: [\n---\n")
        _bump(notes)
        with pytest.raises(RuleRegistryError):
            service.reload_changed_rules()

    def test_reload_removes_ignored_entry_on_delete(self, tmp_path: Path) -> None:
        d = _write(tmp_path, "d.md", _rule(status="draft"))
        service = RuleRegistryService()
        service.load_rules(tmp_path)
        d.unlink()
        assert service.reload_changed_rules() == (0, 0, 1)
        assert dict(service.ignored_rules) == {}

    def test_reload_counts_files_without_frontmatter(self, tmp_path: Path) -> None:
        service = RuleRegistryService()
        service.load_rules(tmp_path)
        _raw(tmp_path, "n.md", "# nothing\n")
        assert service.reload_changed_rules() == (1, 0, 0)
        assert service.reload_changed_rules() == (0, 0, 0)

    def test_reload_ignores_non_markdown_changes(self, tmp_path: Path) -> None:
        service = RuleRegistryService()
        service.load_rules(tmp_path)
        (tmp_path / "x.txt").write_text("hi", encoding="utf-8")
        assert service.reload_changed_rules() == (0, 0, 0)

    def test_mtime_going_backwards_is_detected(self, tmp_path: Path) -> None:
        a = _write(tmp_path, "a.md", _rule(rule_id="A", priority=1))
        service = RuleRegistryService()
        service.load_rules(tmp_path)
        _write(tmp_path, "a.md", _rule(rule_id="A", priority=2))
        _bump(a, -1000.0)
        assert service.reload_changed_rules() == (0, 1, 0)
        assert service.get_rule("A").priority == 2  # type: ignore[union-attr]

    def test_same_mtime_modification_is_not_detected(self, tmp_path: Path) -> None:
        # Documented limitation: fingerprints rely on mtime only.
        a = _write(tmp_path, "a.md", _rule(rule_id="A", priority=1))
        stat = a.stat()
        service = RuleRegistryService()
        service.load_rules(tmp_path)
        _write(tmp_path, "a.md", _rule(rule_id="A", priority=2))
        os.utime(a, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert service.reload_changed_rules() == (0, 0, 0)
        assert service.get_rule("A").priority == 1  # type: ignore[union-attr]

    def test_ignored_rules_snapshot_is_decoupled(self, tmp_path: Path) -> None:
        d = _write(tmp_path, "d.md", _rule(status="draft"))
        service = RuleRegistryService()
        service.load_rules(tmp_path)
        snapshot = service.ignored_rules
        d.unlink()
        service.load_rules(tmp_path)
        assert d in snapshot
        assert d not in service.ignored_rules
