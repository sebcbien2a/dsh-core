import importlib
import sys
from contextlib import contextmanager
from types import ModuleType

import pytest
from fastapi.testclient import TestClient


def _stub_module(name: str, **attributes: object) -> ModuleType:
    module = ModuleType(name)
    for attribute, value in attributes.items():
        setattr(module, attribute, value)
    return module


@contextmanager
def _load_api(monkeypatch: pytest.MonkeyPatch):
    previous_api = sys.modules.pop("api", None)
    monkeypatch.setitem(
        sys.modules,
        "agents.research_agent",
        _stub_module("agents.research_agent", ResearchAgent=type("ResearchAgent", (), {})),
    )
    monkeypatch.setitem(
        sys.modules,
        "agents.coder_agent",
        _stub_module("agents.coder_agent", CoderAgent=type("CoderAgent", (), {})),
    )
    monkeypatch.setitem(sys.modules, "memory_manager", _stub_module("memory_manager"))
    monkeypatch.setitem(
        sys.modules,
        "orchestrator",
        _stub_module("orchestrator", dsh_graph=object()),
    )

    try:
        yield importlib.import_module("api")
    finally:
        sys.modules.pop("api", None)
        if previous_api is not None:
            sys.modules["api"] = previous_api


def _set_enabled(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv("DSH_ENABLE_CANONICAL_SKILLS_API", raising=False)
    else:
        monkeypatch.setenv("DSH_ENABLE_CANONICAL_SKILLS_API", value)


def test_canonical_routes_are_disabled_by_default(monkeypatch, tmp_path):
    _set_enabled(monkeypatch, None)
    monkeypatch.chdir(tmp_path)

    with _load_api(monkeypatch) as api:
        assert not (tmp_path / "skills" / "catalog").exists()
        response = TestClient(api.app).get(
            "/api/canonical-skills/2b192c51-8b23-4ad0-a15b-9be394c490fd/versions"
        )

    assert response.status_code == 404
    assert not (tmp_path / "skills" / "catalog").exists()


def test_canonical_routes_are_mounted_when_explicitly_enabled(monkeypatch, tmp_path):
    _set_enabled(monkeypatch, "true")
    root = tmp_path / "registry"
    monkeypatch.setenv("DSH_CANONICAL_SKILLS_ROOT", str(root))

    with _load_api(monkeypatch) as api:
        assert not root.exists()
        response = TestClient(api.app).get(
            "/api/canonical-skills/2b192c51-8b23-4ad0-a15b-9be394c490fd/versions"
        )

    assert response.status_code == 404
    assert not root.exists()


@pytest.mark.parametrize(
    "value",
    ["true", "TRUE", "1", "yes", "on", " true ", " YES ", " on "],
)
def test_true_environment_values_are_recognized(monkeypatch, tmp_path, value):
    _set_enabled(monkeypatch, value)
    monkeypatch.setenv("DSH_CANONICAL_SKILLS_ROOT", str(tmp_path / "registry"))

    with _load_api(monkeypatch) as api:
        assert api._env_flag_enabled("DSH_ENABLE_CANONICAL_SKILLS_API") is True
        response = TestClient(api.app).get(
            "/api/canonical-skills/2b192c51-8b23-4ad0-a15b-9be394c490fd/versions"
        )

    assert response.status_code == 404


@pytest.mark.parametrize(
    "value",
    ["false", "FALSE", "0", "no", "off", " false ", " NO ", " off "],
)
def test_false_environment_values_are_recognized(monkeypatch, value):
    _set_enabled(monkeypatch, value)

    with _load_api(monkeypatch) as api:
        assert api._env_flag_enabled("DSH_ENABLE_CANONICAL_SKILLS_API") is False
        assert "/api/canonical-skills/{skill_id}/versions" not in {
            route.path for route in api.app.routes
        }


def test_unknown_value_uses_disabled_default(monkeypatch):
    _set_enabled(monkeypatch, "sometimes")

    with _load_api(monkeypatch) as api:
        assert api._env_flag_enabled("DSH_ENABLE_CANONICAL_SKILLS_API") is False
        assert "/api/canonical-skills/{skill_id}/versions" not in {
            route.path for route in api.app.routes
        }


def test_legacy_routes_remain_registered(monkeypatch):
    _set_enabled(monkeypatch, None)

    with _load_api(monkeypatch) as api:
        paths = {route.path for route in api.app.routes}

    assert {
        "/",
        "/api/research",
        "/api/code",
        "/api/execute",
        "/api/autocode",
        "/api/router",
        "/api/scrape",
        "/api/skills/register",
        "/api/skills/find",
        "/api/skills",
        "/api/orchestrator",
    }.issubset(paths)
