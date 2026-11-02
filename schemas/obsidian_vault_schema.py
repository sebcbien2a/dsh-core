"""Pydantic contracts for the Obsidian vault bridge (P15.1).

The vault is a human-readable Markdown mirror of the DSH memory: atomic facts
(Mem0), graph entities (Neo4j, persistence contract v1.0.2), decision logs and
RAG source documents, synchronised through a OneDrive folder. This module is
the pure contract layer: it defines the tree layout, the ``_system/schema.yaml``
model (note types, routing rules, constraints), the note/chunk models and the
deterministic token budget helpers. No I/O is performed here — physical tree
creation lives in ``services/obsidian_vault_service.py``.

Non-negotiables carried by these contracts (spec P15.1):

- vault root is configurable through ``OBSIDIAN_VAULT_PATH`` (default
  ``workspace/vault``);
- ``one_fact_one_file``: exactly one atomic fact per file
  (``05-memoire/facts/{entity}/{predicate}.md``);
- ``wikilinks_required``: every generated note body carries Obsidian
  ``[[wikilinks]]``;
- context budgets are token budgets: ``max_hot_context: 2000`` for
  ``01-contexte/active.md`` and ``max_warm_context: 4000`` for the per-project
  notes;
- a note whose frontmatter says ``source: human`` or ``locked: true``, or whose
  body diverged from the recorded ``content_hash``, is human-owned and is never
  overwritten (write outcome ``preserved``).
"""

from __future__ import annotations

import hashlib
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

# -- vault root & tree layout -------------------------------------------------

VAULT_ROOT_ENV = "OBSIDIAN_VAULT_PATH"
DEFAULT_VAULT_ROOT = Path("workspace") / "vault"

SYSTEM_DIR = "_system"
CONTEXT_DIR = "01-contexte"
PROJECTS_DIR = "02-projets"
RAG_DIR = "03-rag"
GRAPH_DIR = "04-graph"
MEMORY_DIR = "05-memoire"
JOURNAL_DIR = "06-journal"

ENTITIES_DIR = f"{GRAPH_DIR}/entities"
FACTS_DIR = f"{MEMORY_DIR}/facts"
EVENTS_DIR = f"{MEMORY_DIR}/events"

#: Normative tree created inside the vault root (spec P15.1).
VAULT_TREE: tuple[str, ...] = (
    SYSTEM_DIR,
    CONTEXT_DIR,
    PROJECTS_DIR,
    RAG_DIR,
    ENTITIES_DIR,
    FACTS_DIR,
    EVENTS_DIR,
    JOURNAL_DIR,
)

SCHEMA_FILE = f"{SYSTEM_DIR}/schema.yaml"
RAG_MANIFEST_FILE = f"{SYSTEM_DIR}/rag_sync_index.yaml"
ACTIVE_CONTEXT_FILE = f"{CONTEXT_DIR}/active.md"

# -- note types & routing ----------------------------------------------------


class NoteType(str, Enum):
    """The four note types modelled by ``_system/schema.yaml``."""

    FACT = "fact"
    DECISION = "decision"
    ENTITY = "entity"
    RAG_CHUNK = "rag-chunk"


#: Routing rules: note type (plus the ``context`` system note) -> path template.
ROUTING_RULES: Mapping[str, str] = {
    NoteType.FACT.value: "05-memoire/facts/{entity}/{predicate}.md",
    NoteType.DECISION.value: "02-projets/{project}/decisions.md",
    NoteType.ENTITY.value: "04-graph/entities/{entity}.md",
    NoteType.RAG_CHUNK.value: "03-rag/",
    "context": "01-contexte/active.md",
}


class NoteSource(str, Enum):
    """Provenance recorded in note frontmatter (human wins over machine)."""

    AGENT = "agent"
    MEM0 = "mem0"
    NEO4J = "neo4j"
    QDRANT = "qdrant"
    HUMAN = "human"


class NoteStatus(str, Enum):
    GENERATED = "generated"
    PRESERVED = "preserved"


#: Write outcomes (same vocabulary as the P14.2 workbook upsert).
OUTCOME_CREATED = "created"
OUTCOME_UPDATED = "updated"
OUTCOME_UNCHANGED = "unchanged"
OUTCOME_PRESERVED = "preserved"

# -- schema.yaml model -------------------------------------------------------


class NoteTypeDef(BaseModel):
    """One entry of the ``note_types`` section of ``_system/schema.yaml``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    description: str = ""
    path_template: str
    required_fields: tuple[str, ...] = ()


class VaultConstraints(BaseModel):
    """Constraints section of ``_system/schema.yaml`` (spec P15.1)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_hot_context: int = Field(default=2000, ge=1)
    max_warm_context: int = Field(default=4000, ge=1)
    one_fact_one_file: bool = True
    wikilinks_required: bool = True


class VaultSchemaModel(BaseModel):
    """Full model of ``_system/schema.yaml``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = "1.0.0"
    note_types: dict[str, NoteTypeDef]
    routing: dict[str, str]
    constraints: VaultConstraints = Field(default_factory=VaultConstraints)

    @field_validator("note_types")
    @classmethod
    def exact_note_types(cls, value: dict[str, NoteTypeDef]) -> dict[str, NoteTypeDef]:
        expected = {t.value for t in NoteType}
        if set(value) != expected:
            raise ValueError(f"note_types keys must be exactly {sorted(expected)}")
        return value

    @field_validator("routing")
    @classmethod
    def routing_must_cover_all_types(cls, value: dict[str, str]) -> dict[str, str]:
        missing = {t.value for t in NoteType} - set(value)
        if missing:
            raise ValueError(f"routing misses note types {sorted(missing)}")
        if not any(k == "context" for k in value):
            raise ValueError("routing must map the 'context' system note")
        return value

    def to_yaml_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "note_types": {
                name: {
                    "description": def_.description,
                    "path_template": def_.path_template,
                    "required_fields": list(def_.required_fields),
                }
                for name, def_ in self.note_types.items()
            },
            "routing": dict(self.routing),
            "constraints": {
                "max_hot_context": self.constraints.max_hot_context,
                "max_warm_context": self.constraints.max_warm_context,
                "one_fact_one_file": self.constraints.one_fact_one_file,
                "wikilinks_required": self.constraints.wikilinks_required,
            },
        }

    def to_yaml(self) -> str:
        return yaml.safe_dump(
            self.to_yaml_dict(), sort_keys=False, allow_unicode=True, default_flow_style=False
        )

    @classmethod
    def from_yaml(cls, text: str) -> "VaultSchemaModel":
        data = yaml.safe_load(text)
        return cls.model_validate(data)


_FACT_FIELDS = (
    "id",
    "type",
    "entity",
    "predicate",
    "value",
    "source",
    "status",
    "locked",
    "created_at",
    "updated_at",
    "content_hash",
)
_DECISION_FIELDS = (
    "id",
    "type",
    "project",
    "source",
    "status",
    "locked",
    "created_at",
    "updated_at",
    "content_hash",
)
_ENTITY_FIELDS = (
    "id",
    "type",
    "entity",
    "relations",
    "source",
    "status",
    "locked",
    "created_at",
    "updated_at",
    "content_hash",
)


def default_vault_schema() -> VaultSchemaModel:
    """Canonical ``_system/schema.yaml`` content (deterministic)."""
    return VaultSchemaModel(
        version="1.0.0",
        note_types={
            NoteType.FACT.value: NoteTypeDef(
                description="Fait atomique (Mem0) — un fait par fichier.",
                path_template=ROUTING_RULES[NoteType.FACT.value],
                required_fields=_FACT_FIELDS,
            ),
            NoteType.DECISION.value: NoteTypeDef(
                description="Journal de décisions d'un projet.",
                path_template=ROUTING_RULES[NoteType.DECISION.value],
                required_fields=_DECISION_FIELDS,
            ),
            NoteType.ENTITY.value: NoteTypeDef(
                description="Entité du graphe métier (Neo4j) avec ses relations.",
                path_template=ROUTING_RULES[NoteType.ENTITY.value],
                required_fields=_ENTITY_FIELDS,
            ),
            NoteType.RAG_CHUNK.value: NoteTypeDef(
                description="Chunk RAG dérivé de 03-rag/ par titres H2/H3.",
                path_template=ROUTING_RULES[NoteType.RAG_CHUNK.value],
                required_fields=("heading", "heading_level", "index", "text", "sha256"),
            ),
        },
        routing=dict(ROUTING_RULES),
        constraints=VaultConstraints(),
    )


# -- note / chunk models -----------------------------------------------------


class Relation(BaseModel):
    """One directed relation of an entity note (``predicate`` -> ``target``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    predicate: str
    target: str


class Chunk(BaseModel):
    """One semantic chunk produced by :func:`chunk_by_heading`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    heading: str
    heading_level: int
    index: int
    text: str
    sha256: str

    @classmethod
    def build(cls, *, heading: str, heading_level: int, index: int, text: str) -> "Chunk":
        return cls(
            heading=heading,
            heading_level=heading_level,
            index=index,
            text=text,
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )


class SearchHit(BaseModel):
    """One vector search result returned by the injectable RAG client."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str
    source: str = ""
    heading: str = ""
    text: str = ""
    score: float = 0.0


class VectorPoint(BaseModel):
    """Point upserted into the injectable vector store (Qdrant-like)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    vector: list[float]
    payload: dict[str, Any] = Field(default_factory=dict)


class ContextBundle(BaseModel):
    """Prioritised context assembled by the vault reader (hot -> warm -> rag)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project: str
    hot: str = ""
    warm: str = ""
    rag: tuple[SearchHit, ...] = ()
    hot_tokens: int = 0
    warm_tokens: int = 0
    hot_truncated: bool = False
    warm_truncated: bool = False
    priority: tuple[str, str, str] = ("hot", "warm", "rag")

    def render(self) -> str:
        """Render the bundle with explicit priority sections."""
        parts: list[str] = []
        parts.append("## HOT\n\n" + (self.hot or "_(vide)_"))
        parts.append("## WARM\n\n" + (self.warm or "_(vide)_"))
        rag_lines = "\n\n".join(
            f"### [{hit.score:.3f}] {hit.source} — {hit.heading}\n\n{hit.text}"
            for hit in self.rag
        )
        parts.append("## RAG\n\n" + (rag_lines or "_(vide)_"))
        return "\n\n---\n\n".join(parts)


# -- deterministic token budget helpers --------------------------------------

#: Deterministic offline token approximation: 1 token ~= 4 characters.
TOKEN_CHARS = 4


def estimate_tokens(text: str) -> int:
    """Return a deterministic token estimate (ceil of ``len(text) / 4``)."""
    if not text:
        return 0
    return (len(text) + TOKEN_CHARS - 1) // TOKEN_CHARS


def truncate_to_tokens(text: str, max_tokens: int) -> tuple[str, bool]:
    """Truncate ``text`` to ``max_tokens`` at a line boundary.

    Returns the kept text and a ``truncated`` flag. The kept text always
    satisfies ``estimate_tokens(kept) <= max_tokens``.
    """
    if max_tokens < 1:
        raise ValueError("max_tokens must be >= 1")
    if estimate_tokens(text) <= max_tokens:
        return text, False
    limit = max_tokens * TOKEN_CHARS
    cut = text[:limit]
    newline = cut.rfind("\n")
    if newline > 0:
        cut = cut[:newline]
    if not cut:
        cut = text[:limit]
    return cut, True
