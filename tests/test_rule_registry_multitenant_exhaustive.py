"""Multi-tenant resolution tests for the rule registry (P10.3)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from services.rule_registry_service import RuleRegistryError, RuleRegistryService

NODE = "ValidateInvoice"


def _rule(rule_id: str, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "rule_id": rule_id,
        "version": "1.0.0",
        "status": "approved",
        "domain": "accounting",
        "applies_to_nodes": [NODE],
        "priority": 10,
        "conditions": [{"field": "supplier.country", "operator": "eq", "value": "FR"}],
        "deterministic_actions": [
            {"action": "set_vat_rate", "parameters": {"rate": 0.0}}
        ],
    }
    data.update(overrides)
    return data


def _write(directory: Path, data: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{data['rule_id']}.md"
    body = yaml.safe_dump(data, sort_keys=False).strip()
    path.write_text(f"---\n{body}\n---\n# Rule\n", encoding="utf-8")
    return path


def _touch(path: Path, delta: float = 10.0) -> None:
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + delta))


def _ids(rules: list[Any]) -> list[str]:
    return sorted(rule.rule_id for rule in rules)


@pytest.fixture
def base(tmp_path: Path) -> Path:
    _write(tmp_path / "common", _rule("COMMON-1"))
    _write(tmp_path / "common", _rule("COMMON-2", priority=20))
    _write(tmp_path / "tenants" / "CLIENT_01", _rule("C01-ONLY"))
    _write(tmp_path / "tenants" / "CLIENT_02", _rule("C02-ONLY"))
    return tmp_path


def test_tenant_loads_only_common_and_own(base: Path) -> None:
    service = RuleRegistryService()
    rules = service.load_tenant_rules(base, "CLIENT_01")
    assert _ids(rules) == ["C01-ONLY", "COMMON-1", "COMMON-2"]
    assert service.get_rule("C02-ONLY", tenant_id="CLIENT_01") is None


def test_other_tenant_rule_never_visible_or_applicable(base: Path) -> None:
    service = RuleRegistryService()
    service.load_tenant_rules(base, "CLIENT_01")
    service.load_tenant_rules(base, "CLIENT_02")
    c1 = _ids(service.get_applicable_rules(NODE, tenant_id="CLIENT_01"))
    c2 = _ids(service.get_applicable_rules(NODE, tenant_id="CLIENT_02"))
    assert "C02-ONLY" not in c1 and "C01-ONLY" in c1
    assert "C01-ONLY" not in c2 and "C02-ONLY" in c2
    assert service.get_rules_by_domain("accounting", "CLIENT_01") == (
        service.get_effective_rules("CLIENT_01")
    )


def test_override_is_local_to_one_tenant_among_twenty(tmp_path: Path) -> None:
    _write(tmp_path / "common", _rule("SHARED", priority=50))
    for i in range(1, 21):
        (tmp_path / "tenants" / f"CLIENT_{i:02d}").mkdir(parents=True)
    _write(tmp_path / "tenants" / "CLIENT_07", _rule("SHARED", priority=1))

    service = RuleRegistryService()
    for i in range(1, 21):
        service.load_tenant_rules(tmp_path, f"CLIENT_{i:02d}")

    for i in range(1, 21):
        tenant = f"CLIENT_{i:02d}"
        rule = service.get_rule("SHARED", tenant_id=tenant)
        assert rule is not None
        assert rule.priority == (1 if i == 7 else 50)
        assert len(service.get_effective_rules(tenant)) == 1


def test_override_does_not_raise_duplicate(base: Path) -> None:
    _write(base / "tenants" / "CLIENT_01", _rule("COMMON-1", priority=99))
    service = RuleRegistryService()
    service.load_tenant_rules(base, "CLIENT_01")
    assert service.get_rule("COMMON-1", "CLIENT_01").priority == 99  # type: ignore[union-attr]


def test_duplicate_within_tenant_still_rejected(base: Path) -> None:
    _write(base / "tenants" / "CLIENT_01", _rule("C01-ONLY"))
    (base / "tenants" / "CLIENT_01" / "sub").mkdir()
    _write(base / "tenants" / "CLIENT_01" / "sub", _rule("C01-ONLY"))
    with pytest.raises(RuleRegistryError) as exc:
        RuleRegistryService().load_tenant_rules(base, "CLIENT_01")
    assert exc.value.code == "DUPLICATE_RULE_ID"


def test_targeted_hot_reload_leaves_other_tenants_untouched(base: Path) -> None:
    service = RuleRegistryService()
    service.load_tenant_rules(base, "CLIENT_01")
    service.load_tenant_rules(base, "CLIENT_02")
    other_layer = service._tenant_layers["CLIENT_02"]
    common_layer = service._common_layer

    _write(base / "tenants" / "CLIENT_01", _rule("C01-NEW"))
    path = _write(base / "tenants" / "CLIENT_02", _rule("C02-ONLY", priority=77))
    _touch(path)

    assert service.reload_tenant_rules("CLIENT_01") == (1, 0, 0)
    assert service.get_rule("C01-NEW", "CLIENT_01") is not None
    # CLIENT_02 was modified on disk but not reloaded: memory unchanged.
    assert service.get_rule("C02-ONLY", "CLIENT_02").priority == 10  # type: ignore[union-attr]
    assert service._tenant_layers["CLIENT_02"] is other_layer
    assert service._common_layer is common_layer

    assert service.reload_tenant_rules("CLIENT_02") == (0, 1, 0)
    assert service.get_rule("C02-ONLY", "CLIENT_02").priority == 77  # type: ignore[union-attr]


def test_hot_reload_deleted_tenant_file(base: Path) -> None:
    service = RuleRegistryService()
    service.load_tenant_rules(base, "CLIENT_01")
    (base / "tenants" / "CLIENT_01" / "C01-ONLY.md").unlink()
    assert service.reload_tenant_rules("CLIENT_01") == (0, 0, 1)
    assert _ids(service.get_effective_rules("CLIENT_01")) == ["COMMON-1", "COMMON-2"]


def test_reload_common_affects_all_tenants(base: Path) -> None:
    service = RuleRegistryService()
    service.load_tenant_rules(base, "CLIENT_01")
    service.load_tenant_rules(base, "CLIENT_02")
    _write(base / "common", _rule("COMMON-3"))
    assert service.reload_common_rules() == (1, 0, 0)
    for tenant in ("CLIENT_01", "CLIENT_02"):
        assert service.get_rule("COMMON-3", tenant) is not None


def test_missing_tenant_directory_falls_back_to_common(base: Path) -> None:
    service = RuleRegistryService()
    rules = service.load_tenant_rules(base, "GHOST")
    assert _ids(rules) == ["COMMON-1", "COMMON-2"]
    assert service.reload_tenant_rules("GHOST") == (0, 0, 0)

    _write(base / "tenants" / "GHOST", _rule("GHOST-1"))
    assert service.reload_tenant_rules("GHOST") == (1, 0, 0)
    assert service.get_rule("GHOST-1", "GHOST") is not None


def test_missing_common_directory_is_tolerated(tmp_path: Path) -> None:
    _write(tmp_path / "tenants" / "CLIENT_01", _rule("C01-ONLY"))
    rules = RuleRegistryService().load_tenant_rules(tmp_path, "CLIENT_01")
    assert _ids(rules) == ["C01-ONLY"]


def test_missing_base_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(RuleRegistryError) as exc:
        RuleRegistryService().load_tenant_rules(tmp_path / "nope", "CLIENT_01")
    assert exc.value.code == "RULES_DIRECTORY_NOT_FOUND"


@pytest.mark.parametrize(
    "tenant_id",
    ["", ".", "..", "a/b", "a\\b", "../x", "a b", "x" * 65, "é", "a\n", "CLIENT*"],
)
def test_invalid_tenant_id_rejected(base: Path, tenant_id: str) -> None:
    service = RuleRegistryService()
    with pytest.raises(RuleRegistryError) as exc:
        service.load_tenant_rules(base, tenant_id)
    assert exc.value.code == "INVALID_TENANT_ID"
    with pytest.raises(RuleRegistryError):
        service.get_applicable_rules(NODE, tenant_id=tenant_id)
    with pytest.raises(RuleRegistryError):
        service.reload_tenant_rules(tenant_id)


@pytest.mark.parametrize("tenant_id", ["A", "a.b", "a:b-c_d", "x" * 64, "1.2"])
def test_valid_tenant_ids_accepted(tmp_path: Path, tenant_id: str) -> None:
    (tmp_path / "common").mkdir()
    assert RuleRegistryService().load_tenant_rules(tmp_path, tenant_id) == []


def test_unloaded_tenant_raises(base: Path) -> None:
    service = RuleRegistryService()
    service.load_tenant_rules(base, "CLIENT_01")
    with pytest.raises(RuleRegistryError) as exc:
        service.get_applicable_rules(NODE, tenant_id="CLIENT_02")
    assert exc.value.code == "TENANT_NOT_LOADED"


def test_unload_tenant(base: Path) -> None:
    service = RuleRegistryService()
    service.load_tenant_rules(base, "CLIENT_01")
    service.load_tenant_rules(base, "CLIENT_02")
    service.unload_tenant("CLIENT_01")
    assert service.loaded_tenants == frozenset({"CLIENT_02"})


def test_legacy_load_rules_unchanged(base: Path) -> None:
    service = RuleRegistryService()
    service.load_tenant_rules(base, "CLIENT_01")
    legacy = RuleRegistryService()
    legacy.load_rules(base / "common")
    assert _ids(legacy.get_applicable_rules(NODE)) == ["COMMON-1", "COMMON-2"]
