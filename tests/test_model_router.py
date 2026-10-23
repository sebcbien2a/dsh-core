import pytest

from model_router import (
    ExecutionMode,
    ModelRole,
    Provider,
    resolve_model,
    resolve_provider,
    validate_model_config,
)


LOCAL_VARIABLES = {
    ModelRole.FAST: "LOCAL_FAST_MODEL",
    ModelRole.BASE: "LOCAL_BASE_MODEL",
    ModelRole.VISION: "LOCAL_VISION_MODEL",
    ModelRole.EXTRACTION: "LOCAL_EXTRACTION_MODEL",
    ModelRole.EMBEDDING: "LOCAL_EMBEDDING_MODEL",
    ModelRole.PREPARATION: "LOCAL_BASE_MODEL",
    ModelRole.EXPERT: "LOCAL_BASE_MODEL",
    ModelRole.CODING: "LOCAL_BASE_MODEL",
    ModelRole.REVIEW: "LOCAL_BASE_MODEL",
}
HYBRID_VARIABLES = {
    **LOCAL_VARIABLES,
    ModelRole.PREPARATION: "CLOUD_PREPARATION_MODEL",
    ModelRole.EXPERT: "CLOUD_EXPERT_MODEL",
    ModelRole.CODING: "CLOUD_CODING_MODEL",
    ModelRole.REVIEW: "CLOUD_REVIEW_MODEL",
}
CLOUD_VARIABLES = {
    ModelRole.PREPARATION: "CLOUD_PREPARATION_MODEL",
    ModelRole.EXPERT: "CLOUD_EXPERT_MODEL",
    ModelRole.VISION: "CLOUD_VISION_MODEL",
    ModelRole.CODING: "CLOUD_CODING_MODEL",
    ModelRole.REVIEW: "CLOUD_REVIEW_MODEL",
}


def config_for(variables):
    return {variable: f"model-for-{variable}" for variable in set(variables.values())}


@pytest.mark.parametrize("role", list(ModelRole))
def test_resolves_every_role_in_local_mode(role):
    environ = config_for(LOCAL_VARIABLES)
    assert resolve_model(role, "local", environ) == (
        f"model-for-{LOCAL_VARIABLES[role]}"
    )


@pytest.mark.parametrize("role", list(ModelRole))
def test_resolves_every_role_in_hybrid_mode(role):
    environ = config_for(HYBRID_VARIABLES)
    assert resolve_model(role, "hybrid", environ) == (
        f"model-for-{HYBRID_VARIABLES[role]}"
    )


@pytest.mark.parametrize("role", list(CLOUD_VARIABLES))
def test_resolves_supported_roles_in_cloud_mode(role):
    environ = config_for(CLOUD_VARIABLES)
    assert resolve_model(role, "cloud", environ) == (
        f"model-for-{CLOUD_VARIABLES[role]}"
    )


@pytest.mark.parametrize(
    "role",
    [
        ModelRole.FAST,
        ModelRole.BASE,
        ModelRole.EXTRACTION,
        ModelRole.EMBEDDING,
    ],
)
def test_cloud_mode_rejects_unsupported_roles(role):
    with pytest.raises(ValueError, match="not supported"):
        resolve_model(role, "cloud", {})


def test_ai_mode_is_used_by_default():
    environ = {"AI_MODE": "local", "LOCAL_FAST_MODEL": "configured-local"}
    assert resolve_model("fast", environ=environ) == "configured-local"


def test_explicit_mode_overrides_ai_mode():
    environ = {"AI_MODE": "local", "CLOUD_EXPERT_MODEL": "configured-cloud"}
    assert resolve_model("expert", "hybrid", environ) == "configured-cloud"


def test_string_role_and_mode_are_accepted():
    assert resolve_model(
        "vision",
        "local",
        {"LOCAL_VISION_MODEL": "vision-model"},
    ) == "vision-model"


@pytest.mark.parametrize("role", ["unknown", None])
def test_unknown_role_is_rejected(role):
    with pytest.raises(ValueError, match="Unknown model role"):
        resolve_model(role, "local", {})


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError, match="Unknown execution mode"):
        resolve_model("fast", "unknown", {})


@pytest.mark.parametrize("value", [None, ""])
def test_missing_or_empty_model_variable_is_rejected(value):
    environ = {} if value is None else {"LOCAL_FAST_MODEL": value}
    with pytest.raises(ValueError, match="LOCAL_FAST_MODEL"):
        resolve_model("fast", "local", environ)


def test_providers_follow_role_mode_table():
    assert resolve_provider(ModelRole.FAST, ExecutionMode.HYBRID) is Provider.OLLAMA
    assert resolve_provider(ModelRole.EXPERT, ExecutionMode.HYBRID) is Provider.OPENROUTER
    assert resolve_provider(ModelRole.VISION, ExecutionMode.CLOUD) is Provider.OPENROUTER


def test_provider_rejects_unsupported_role_and_unknown_mode():
    with pytest.raises(ValueError, match="not supported"):
        resolve_provider("fast", "cloud")
    with pytest.raises(ValueError, match="Unknown execution mode"):
        resolve_provider("fast", "invalid")


def test_config_issues_never_expose_key_value():
    secret = "not-for-output-123456789"
    issues = validate_model_config(
        "hybrid",
        {"OPENROUTER_API_KEY": secret},
    )
    assert issues
    assert all(secret not in issue.message for issue in issues)
    assert all(secret not in repr(issue) for issue in issues)


def test_missing_key_issue_never_exposes_other_configuration_values():
    secret = "do-not-leak-this-model-value"
    environ = config_for(HYBRID_VARIABLES)
    environ["LOCAL_FAST_MODEL"] = secret
    environ["OPENROUTER_API_KEY"] = ""
    issues = validate_model_config("hybrid", environ)
    key_issues = [
        issue for issue in issues if issue.variable == "OPENROUTER_API_KEY"
    ]
    assert len(key_issues) == 1
    assert secret not in key_issues[0].message


def test_supplied_environment_mapping_isolated_from_process_environment(monkeypatch):
    monkeypatch.setenv("AI_MODE", "cloud")
    monkeypatch.setenv("LOCAL_FAST_MODEL", "process-environment-model")
    assert resolve_model(
        "fast",
        "local",
        {"LOCAL_FAST_MODEL": "mapping-model"},
    ) == "mapping-model"
    issues = validate_model_config("local", {"LOCAL_FAST_MODEL": "mapping-model"})
    assert any(issue.variable == "LOCAL_BASE_MODEL" for issue in issues)


def test_local_validation_does_not_require_openrouter_key():
    issues = validate_model_config("local", config_for(LOCAL_VARIABLES))
    assert not any(issue.variable == "OPENROUTER_API_KEY" for issue in issues)


def test_hybrid_validation_reports_missing_key():
    environ = config_for(HYBRID_VARIABLES)
    issues = validate_model_config("hybrid", environ)
    assert [issue.variable for issue in issues if issue.variable == "OPENROUTER_API_KEY"]


def test_hybrid_validation_accepts_complete_configuration():
    environ = config_for(HYBRID_VARIABLES)
    environ["OPENROUTER_API_KEY"] = "configured"
    assert validate_model_config("hybrid", environ) == []


def test_cloud_validation_accepts_complete_configuration_without_unsupported_roles():
    environ = config_for(CLOUD_VARIABLES)
    environ["OPENROUTER_API_KEY"] = "configured"
    assert validate_model_config("cloud", environ) == []


def test_cloud_validation_reports_missing_vision_model():
    environ = config_for(CLOUD_VARIABLES)
    environ.pop("CLOUD_VISION_MODEL")
    environ["OPENROUTER_API_KEY"] = "configured"
    issues = validate_model_config("cloud", environ)
    assert [issue.variable for issue in issues] == ["CLOUD_VISION_MODEL"]


def test_cloud_validation_does_not_report_unsupported_roles():
    environ = config_for(CLOUD_VARIABLES)
    environ["OPENROUTER_API_KEY"] = "configured"
    issues = validate_model_config("cloud", environ)
    unsupported = {
        ModelRole.FAST,
        ModelRole.BASE,
        ModelRole.EXTRACTION,
        ModelRole.EMBEDDING,
    }
    assert not any(issue.role in unsupported for issue in issues)


@pytest.mark.parametrize(
    "environ",
    [{}, {"AI_MODE": ""}, {"AI_MODE": "invalid"}],
)
def test_validation_reports_missing_or_unknown_default_mode(environ):
    issues = validate_model_config(environ=environ)
    assert len(issues) == 1
    assert issues[0].variable == "AI_MODE"


def test_validation_rejects_unknown_explicit_mode():
    with pytest.raises(ValueError, match="Unknown execution mode"):
        validate_model_config("invalid", {})
