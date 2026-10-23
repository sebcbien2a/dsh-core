"""Central model selection without loading dotenv files or contacting providers."""

import os
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional, Union


class ModelRole(str, Enum):
    FAST = "fast"
    BASE = "base"
    VISION = "vision"
    EXTRACTION = "extraction"
    EMBEDDING = "embedding"
    PREPARATION = "preparation"
    EXPERT = "expert"
    CODING = "coding"
    REVIEW = "review"


class ExecutionMode(str, Enum):
    LOCAL = "local"
    HYBRID = "hybrid"
    CLOUD = "cloud"


class Provider(str, Enum):
    OLLAMA = "ollama"
    OPENROUTER = "openrouter"


@dataclass(frozen=True)
class ConfigIssue:
    variable: str
    role: Optional[ModelRole]
    mode: Optional[ExecutionMode]
    message: str


RoleInput = Union[ModelRole, str]
ModeInput = Union[ExecutionMode, str]

_ROLE_VARIABLES = {
    ExecutionMode.LOCAL: {
        ModelRole.FAST: "LOCAL_FAST_MODEL",
        ModelRole.BASE: "LOCAL_BASE_MODEL",
        ModelRole.VISION: "LOCAL_VISION_MODEL",
        ModelRole.EXTRACTION: "LOCAL_EXTRACTION_MODEL",
        ModelRole.EMBEDDING: "LOCAL_EMBEDDING_MODEL",
        ModelRole.PREPARATION: "LOCAL_BASE_MODEL",
        ModelRole.EXPERT: "LOCAL_BASE_MODEL",
        ModelRole.CODING: "LOCAL_BASE_MODEL",
        ModelRole.REVIEW: "LOCAL_BASE_MODEL",
    },
    ExecutionMode.HYBRID: {
        ModelRole.FAST: "LOCAL_FAST_MODEL",
        ModelRole.BASE: "LOCAL_BASE_MODEL",
        ModelRole.VISION: "LOCAL_VISION_MODEL",
        ModelRole.EXTRACTION: "LOCAL_EXTRACTION_MODEL",
        ModelRole.EMBEDDING: "LOCAL_EMBEDDING_MODEL",
        ModelRole.PREPARATION: "CLOUD_PREPARATION_MODEL",
        ModelRole.EXPERT: "CLOUD_EXPERT_MODEL",
        ModelRole.CODING: "CLOUD_CODING_MODEL",
        ModelRole.REVIEW: "CLOUD_REVIEW_MODEL",
    },
    ExecutionMode.CLOUD: {
        ModelRole.VISION: "CLOUD_VISION_MODEL",
        ModelRole.PREPARATION: "CLOUD_PREPARATION_MODEL",
        ModelRole.EXPERT: "CLOUD_EXPERT_MODEL",
        ModelRole.CODING: "CLOUD_CODING_MODEL",
        ModelRole.REVIEW: "CLOUD_REVIEW_MODEL",
    },
}

_CLOUD_VARIABLES = frozenset(
    variable
    for variables in _ROLE_VARIABLES.values()
    for variable in variables.values()
    if variable.startswith("CLOUD_")
)


def _parse_role(role: RoleInput) -> ModelRole:
    try:
        return role if isinstance(role, ModelRole) else ModelRole(role)
    except (TypeError, ValueError):
        raise ValueError(f"Unknown model role: {role!r}") from None


def _parse_mode(mode: ModeInput) -> ExecutionMode:
    try:
        return mode if isinstance(mode, ExecutionMode) else ExecutionMode(mode)
    except (TypeError, ValueError):
        raise ValueError(f"Unknown execution mode: {mode!r}") from None


def _environment(environ: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _selected_mode(
    mode: Optional[ModeInput], environ: Mapping[str, str]
) -> ExecutionMode:
    if mode is not None:
        return _parse_mode(mode)
    configured_mode = environ.get("AI_MODE")
    if configured_mode is None or not configured_mode.strip():
        raise ValueError("AI_MODE is missing or empty")
    return _parse_mode(configured_mode)


def _variable_for(role: ModelRole, mode: ExecutionMode) -> str:
    variable = _ROLE_VARIABLES[mode].get(role)
    if variable is None:
        raise ValueError(f"Role {role.value!r} is not supported in mode {mode.value!r}")
    return variable


def resolve_model(
    role: RoleInput,
    mode: Optional[ModeInput] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> str:
    """Return a configured model identifier, never a credential."""
    role_value = _parse_role(role)
    env = _environment(environ)
    mode_value = _selected_mode(mode, env)
    variable = _variable_for(role_value, mode_value)
    model = env.get(variable)
    if model is None or not model.strip():
        raise ValueError(
            f"Missing or empty {variable} for role {role_value.value!r} "
            f"in mode {mode_value.value!r}"
        )
    return model.strip()


def resolve_provider(role: RoleInput, mode: Optional[ModeInput] = None) -> Provider:
    """Return the provider assigned by the role/mode table."""
    role_value = _parse_role(role)
    mode_value = _selected_mode(mode, os.environ)
    variable = _variable_for(role_value, mode_value)
    return Provider.OPENROUTER if variable.startswith("CLOUD_") else Provider.OLLAMA


def validate_model_config(
    mode: Optional[ModeInput] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> list[ConfigIssue]:
    """Report missing configuration without exposing environment values."""
    env = _environment(environ)
    if mode is None:
        configured_mode = env.get("AI_MODE")
        if configured_mode is None or not configured_mode.strip():
            return [
                ConfigIssue(
                    variable="AI_MODE",
                    role=None,
                    mode=None,
                    message="AI_MODE is missing or empty",
                )
            ]
        try:
            mode_value = _parse_mode(configured_mode)
        except ValueError:
            return [
                ConfigIssue(
                    variable="AI_MODE",
                    role=None,
                    mode=None,
                    message="AI_MODE contains an unknown execution mode",
                )
            ]
    else:
        mode_value = _parse_mode(mode)

    issues = []
    role_variables = _ROLE_VARIABLES[mode_value]
    checked_variables = set()
    for role_value in ModelRole:
        variable = role_variables.get(role_value)
        if variable is None:
            issues.append(
                ConfigIssue(
                    variable="",
                    role=role_value,
                    mode=mode_value,
                    message=f"Role {role_value.value!r} is unsupported in this mode",
                )
            )
        elif variable not in checked_variables and (
            variable not in env or not env[variable].strip()
        ):
            issues.append(
                ConfigIssue(
                    variable=variable,
                    role=role_value,
                    mode=mode_value,
                    message=f"{variable} is missing or empty",
                )
            )
        if variable is not None:
            checked_variables.add(variable)

    if any(variable.startswith("CLOUD_") for variable in role_variables.values()):
        if not env.get("OPENROUTER_API_KEY", "").strip():
            issues.append(
                ConfigIssue(
                    variable="OPENROUTER_API_KEY",
                    role=None,
                    mode=mode_value,
                    message="OPENROUTER_API_KEY is missing or empty",
                )
            )
    return issues
