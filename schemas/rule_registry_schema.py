"""Pydantic contracts for approved Obsidian business rules.

Conditions and deterministic actions are represented as typed sub-models so the
future orchestration layer can evaluate them without introducing a dependency on
Neo4j, Qdrant or LangGraph in this module.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RuleStatus(str, Enum):
    DRAFT = "draft"
    PENDING_HITL = "pending_hitl"
    APPROVED = "approved"
    DEPRECATED = "deprecated"


class ConditionModel(BaseModel):
    """A single predicate evaluated against a node."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    operator: str
    value: Any

    @field_validator("field", "operator")
    @classmethod
    def non_blank_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty or whitespace")
        return value


class ActionModel(BaseModel):
    """A deterministic action applied when all conditions match."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("action")
    @classmethod
    def non_blank_action(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty or whitespace")
        return value


class BusinessRuleModel(BaseModel):
    """Validated YAML frontmatter of a single Obsidian rule."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    version: str
    status: RuleStatus
    domain: str
    applies_to_nodes: list[str]
    priority: int
    conditions: list[ConditionModel]
    deterministic_actions: list[ActionModel]

    @field_validator("rule_id", "version", "domain")
    @classmethod
    def non_blank_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty or whitespace")
        return value

    @field_validator("applies_to_nodes")
    @classmethod
    def clean_nodes(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            node = value.strip()
            if not node:
                raise ValueError("node names must not be empty or whitespace")
            if node not in seen:
                seen.add(node)
                cleaned.append(node)
        return cleaned
