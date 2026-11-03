"""Obsidian vault bridge (P15.1): tree, writer, reader and RAG sync.

This service is the human-readable Markdown mirror of the DSH memory. It is
offline, deterministic and non-destructive:

- :func:`ensure_vault_tree` materialises the vault tree
  (``_system/``, ``01-contexte/``, ``02-projets/``, ``03-rag/``,
  ``04-graph/entities/``, ``05-memoire/facts/``, ``05-memoire/events/``,
  ``06-journal/``) and writes ``_system/schema.yaml`` only when absent.
- ``vault_writer`` (:class:`VaultWriter`) exports atomic facts
  (``05-memoire/facts/{entity}/{predicate}.md`` — one fact per file), graph
  entities (``04-graph/entities/{entity}.md``, relations merged without
  duplication), decision logs (``02-projets/{project}/decisions.md``) and the
  active context (``01-contexte/active.md``). Every note carries a complete
  YAML frontmatter and Obsidian ``[[wikilinks]]``.
- The human review layer is strict: a note whose frontmatter says
  ``source: human``, ``locked: true`` or ``status: preserved``, or whose content
  diverged from the generated version (``content_hash`` mismatch), is never
  overwritten — the write outcome is ``preserved`` and the file stays
  byte-identical.
- ``vault_reader`` (:class:`VaultReader`) assembles context by priority:
  ``hot`` (``01-contexte/active.md``, capped at ``max_hot_context`` tokens),
  ``warm`` (``02-projets/{project}/*.md``, capped at ``max_warm_context``), and
  ``rag`` through an injectable vector search interface.
- :func:`chunk_by_heading` splits ``03-rag/`` Markdown documents on H2/H3
  headings (``max_chars=2000``, ``overlap=0.15``) and :func:`rag_sync`
  upserts the chunks into an injectable Qdrant-like client with SHA-256
  idempotence (unchanged files are skipped entirely).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence
from uuid import NAMESPACE_URL, uuid5

import yaml

from schemas.obsidian_vault_schema import (
    ACTIVE_CONTEXT_FILE,
    COMPTA_DIR,
    CONTEXT_DIR,
    DEFAULT_VAULT_ROOT,
    ENTITIES_DIR,
    EVENTS_DIR,
    FACTS_DIR,
    PROJECTS_DIR,
    RAG_DIR,
    RAG_MANIFEST_FILE,
    SCHEMA_FILE,
    VAULT_ROOT_ENV,
    VAULT_TREE,
    Chunk,
    ContextBundle,
    NoteSource,
    NoteType,
    Relation,
    SearchHit,
    VectorPoint,
    VaultConstraints,
    VaultSchemaModel,
    default_vault_schema,
    estimate_tokens,
    truncate_to_tokens,
)

WIKILINK_RE = re.compile(r"\[\[[^\[\]]+\]\]")
DECISION_ENTRY_RE = re.compile(r"^## (D-[0-9a-f]{8}) — (\S+)$", re.MULTILINE)
_HEADING_RE = re.compile(r"^(#{2,3})\s+(.*)$")
_SEGMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")

OUTCOME_CREATED = "created"
OUTCOME_UPDATED = "updated"
OUTCOME_UNCHANGED = "unchanged"
OUTCOME_PRESERVED = "preserved"

PROTECTED_HUMAN = "human_source"
PROTECTED_LOCKED = "locked"
PROTECTED_STATUS = "preserved_status"
PROTECTED_DIVERGED = "content_diverged"
PROTECTED_UNMANAGED = "unmanaged_note"
PROTECTED_NO_HASH = "missing_content_hash"

DEFAULT_COLLECTION = "vault_rag"
DEFAULT_MAX_CHARS = 2000
DEFAULT_OVERLAP = 0.15


class VaultError(ValueError):
    """Invalid vault operation; ``code`` is stable for tests and logs."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


# -- protocols (injectable / mockable dependencies) ---------------------------


class VectorSearchClient(Protocol):
    """Injectable vector search interface used by the vault reader."""

    def search(self, query: str, limit: int = 5) -> Sequence[Any]: ...


class VectorStoreClient(Protocol):
    """Injectable Qdrant-like store used by :func:`rag_sync`."""

    def upsert(self, collection: str, points: Sequence[VectorPoint]) -> Any: ...

    def delete(self, collection: str, ids: Sequence[str]) -> Any: ...


# -- result models ------------------------------------------------------------


@dataclass(frozen=True)
class WriteResult:
    """Outcome of one vault write (P14.2 vocabulary: created/updated/...)."""

    path: Path
    status: str
    note_type: str
    detail: str = ""


@dataclass(frozen=True)
class DecisionRecordResult:
    """Outcome of :meth:`VaultWriter.record_decision` (log + active context)."""

    decision_id: str
    decisions: WriteResult
    active: WriteResult


@dataclass(frozen=True)
class DecisionEntry:
    """One decision entry parsed back from a project decision log."""

    decision_id: str
    timestamp: str
    excerpt: str


@dataclass(frozen=True)
class RagSyncReport:
    """Observable outcome of one :func:`rag_sync` pass."""

    files_scanned: int
    files_synced: int
    files_skipped: int
    files_removed: int
    chunks_upserted: int
    chunks_deleted: int
    manifest: Path
    traces: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ExistingNote:
    """Inspection of a note already present on disk."""

    exists: bool
    frontmatter: dict[str, Any]
    body: str
    protected: str | None = None


# -- generic helpers ----------------------------------------------------------


def resolve_vault_root(root: str | os.PathLike[str] | None = None) -> Path:
    """Explicit ``root`` wins, then ``OBSIDIAN_VAULT_PATH``, then the default."""
    if root is not None:
        raw = os.fspath(root)
    else:
        raw = os.environ.get(VAULT_ROOT_ENV, "").strip() or os.fspath(DEFAULT_VAULT_ROOT)
    if not raw or "\x00" in raw:
        raise VaultError("INVALID_ROOT", repr(raw))
    return Path(raw)


def ensure_contained(base: Path, candidate: Path) -> Path:
    """Return the resolved ``candidate`` or raise when it escapes ``base``."""
    resolved_base = Path(base).resolve()
    resolved = Path(candidate).resolve()
    if resolved_base != resolved and resolved_base not in resolved.parents:
        raise VaultError("PATH_TRAVERSAL", str(candidate))
    return resolved


def validate_segment(value: object, code: str = "INVALID_SEGMENT") -> str:
    """Accept one safe path segment (note name / entity / predicate / project)."""
    if not isinstance(value, str) or _SEGMENT_RE.fullmatch(value) is None:
        raise VaultError(code, repr(value))
    return value


def hash_text(text: str) -> str:
    """SHA-256 hex digest of ``text`` (UTF-8)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_file(path: Path) -> str:
    """SHA-256 hex digest of a file's bytes (aligned on P13.2 dedup)."""
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(1 << 16):
            digest.update(chunk)
    return digest.hexdigest()


def hash_embed(text: str, dim: int = 64) -> list[float]:
    """Deterministic offline placeholder embedding (sha-512 based).

    Real deployments inject an embedder through ``rag_sync(embedder=...)``;
    this helper keeps tests and offline runs deterministic and network-free.
    """
    if dim != 64:
        raise VaultError("INVALID_EMBED_DIM", str(dim))
    return [byte / 255.0 for byte in hashlib.sha512(text.encode("utf-8")).digest()]


def render_note(frontmatter: Mapping[str, Any], body: str) -> str:
    """Render a complete note: YAML frontmatter plus Markdown body."""
    dumped = yaml.safe_dump(
        dict(frontmatter), sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    return f"---\n{dumped}---\n\n{body}"


def parse_note(text: str) -> tuple[dict[str, Any], str] | None:
    """Split ``text`` into ``(frontmatter, body)``; None when unmanaged."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            frontmatter_text = "".join(lines[1:index])
            body = "".join(lines[index + 1 :])
            if body.startswith("\n"):
                body = body[1:]
            try:
                loaded = yaml.safe_load(frontmatter_text)
            except yaml.YAMLError:
                return None
            if not isinstance(loaded, dict):
                return None
            return _normalize_frontmatter(loaded), body
    return None


def _normalize_frontmatter(frontmatter: dict[str, Any]) -> dict[str, Any]:
    """Canonicalise YAML scalars so hashes and comparisons stay deterministic.

    PyYAML parses ISO timestamps as ``datetime`` objects; they are folded back
    to their ISO string form so ``content_hash`` computed at write time matches
    the hash recomputed from a re-read note.
    """
    normalized: dict[str, Any] = {}
    for key, value in frontmatter.items():
        normalized[str(key)] = _normalize_scalar(value)
    return normalized


def _normalize_scalar(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _normalize_scalar(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_scalar(item) for item in value]
    return value


def normalize_body(body: str) -> str:
    """Canonical body form: no surrounding blank lines, one trailing newline."""
    return body.strip("\n") + "\n"


def payload_hash(frontmatter: Mapping[str, Any], body: str) -> str:
    """Hash of the generated payload (frontmatter minus volatile keys + body).

    ``content_hash`` records this digest so any later human edit of the note
    (frontmatter or body) is detected as a divergence and protects the note.
    """
    stable = {
        key: value
        for key, value in frontmatter.items()
        if key not in ("content_hash", "updated_at")
    }
    canonical = json.dumps(
        stable, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")
    )
    return hash_text(canonical + "\n\n" + normalize_body(body))


def atomic_write(path: Path, text: str) -> None:
    """Write ``text`` atomically (temp file then ``os.replace``, OneDrive-safe)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp-vault")
    temp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temp, path)


# -- vault tree ---------------------------------------------------------------


@dataclass(frozen=True)
class VaultTree:
    """Resolved layout of one vault root."""

    root: Path
    dirs: Mapping[str, Path]
    schema_path: Path

    def dir(self, name: str) -> Path:
        return self.dirs[name]

    def active_file(self) -> Path:
        return self.root / ACTIVE_CONTEXT_FILE

    def fact_file(self, entity: str, predicate: str) -> Path:
        return self.root / FACTS_DIR / entity / f"{predicate}.md"

    def entity_file(self, entity: str) -> Path:
        return self.root / ENTITIES_DIR / f"{entity}.md"

    def decisions_file(self, project: str) -> Path:
        return self.root / PROJECTS_DIR / project / "decisions.md"

    def project_dir(self, project: str) -> Path:
        return self.root / PROJECTS_DIR / project

    def rag_dir(self) -> Path:
        return self.root / RAG_DIR

    def manifest_file(self) -> Path:
        return self.root / RAG_MANIFEST_FILE


def render_schema_yaml() -> str:
    """Deterministic ``_system/schema.yaml`` content."""
    return default_vault_schema().to_yaml()


def ensure_vault_tree(
    root: str | os.PathLike[str] | None = None,
) -> VaultTree:
    """Create (idempotently) the vault tree and write the schema if absent.

    ``_system/schema.yaml`` is written only when missing: an existing schema —
    even human-edited — is never overwritten (non-destructive rule).
    """
    base = resolve_vault_root(root)
    if base.exists() and not base.is_dir():
        raise VaultError("NOT_A_DIRECTORY", str(base))
    base.mkdir(parents=True, exist_ok=True)

    dirs: dict[str, Path] = {}
    for name in (*VAULT_TREE, COMPTA_DIR):
        path = ensure_contained(base, base / name)
        if path.exists() and not path.is_dir():
            raise VaultError("NOT_A_DIRECTORY", str(path))
        path.mkdir(parents=True, exist_ok=True)
        dirs[name] = path

    schema_path = ensure_contained(base, base / SCHEMA_FILE)
    if not schema_path.exists():
        atomic_write(schema_path, render_schema_yaml())

    return VaultTree(root=base.resolve(), dirs=dirs, schema_path=schema_path)


def load_constraints(root: str | os.PathLike[str] | None = None) -> VaultConstraints:
    """Read budget constraints from ``_system/schema.yaml`` (defaults if absent)."""
    base = resolve_vault_root(root)
    schema_path = base / SCHEMA_FILE
    if not schema_path.exists():
        return default_vault_schema().constraints
    text = schema_path.read_text(encoding="utf-8-sig")
    try:
        schema = VaultSchemaModel.from_yaml(text)
    except Exception as exc:  # noqa: BLE001 - normalised into a coded error
        raise VaultError("INVALID_SCHEMA", str(exc)) from exc
    return schema.constraints


# -- vault writer -------------------------------------------------------------


class VaultWriter:
    """Export facts, entities, decisions and active context as Markdown notes.

    ``now`` is injectable so tests are fully deterministic; the default clock
    is timezone-aware UTC. Every write goes through the human review layer:
    a protected note is never touched and yields ``status='preserved'``.
    """

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        now: Callable[[], datetime] | None = None,
        constraints: VaultConstraints | None = None,
    ) -> None:
        self._tree = ensure_vault_tree(root)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._constraints = constraints or load_constraints(self._tree.root)

    @property
    def tree(self) -> VaultTree:
        return self._tree

    def _iso_now(self) -> str:
        moment = self._now()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat()

    # -- inspection / commit -------------------------------------------------

    def _inspect(self, path: Path) -> _ExistingNote:
        if not path.exists():
            return _ExistingNote(exists=False, frontmatter={}, body="", protected=None)
        try:
            raw = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            raise VaultError("UNREADABLE_NOTE", str(path)) from exc
        parsed = parse_note(raw)
        if parsed is None:
            return _ExistingNote(
                exists=True, frontmatter={}, body=raw, protected=PROTECTED_UNMANAGED
            )
        frontmatter, body = parsed
        protected = _protection_reason(frontmatter, body)
        return _ExistingNote(
            exists=True, frontmatter=frontmatter, body=body, protected=protected
        )

    def _commit(
        self,
        path: Path,
        frontmatter: dict[str, Any],
        body: str,
        note_type: str,
        existing: _ExistingNote,
    ) -> WriteResult:
        if existing.protected is not None:
            return WriteResult(
                path=path,
                status=OUTCOME_PRESERVED,
                note_type=note_type,
                detail=existing.protected,
            )
        body = normalize_body(body)
        self._check_wikilinks(body)
        candidate = dict(frontmatter)
        candidate["created_at"] = existing.frontmatter.get("created_at") or self._iso_now()
        candidate["updated_at"] = self._iso_now()
        candidate["content_hash"] = payload_hash(candidate, body)
        if existing.exists and _same_note(existing.frontmatter, existing.body, candidate, body):
            return WriteResult(
                path=path, status=OUTCOME_UNCHANGED, note_type=note_type, detail=""
            )
        atomic_write(path, render_note(candidate, body))
        status = OUTCOME_UPDATED if existing.exists else OUTCOME_CREATED
        return WriteResult(path=path, status=status, note_type=note_type, detail="")

    def _check_wikilinks(self, body: str) -> None:
        if self._constraints.wikilinks_required and not WIKILINK_RE.search(body):
            raise VaultError("WIKILINK_REQUIRED", body[:80])

    # -- facts ---------------------------------------------------------------

    def write_fact(
        self,
        entity: str,
        predicate: str,
        value: object,
        *,
        source: str | NoteSource = NoteSource.MEM0,
        project: str | None = None,
        links: Iterable[str] = (),
    ) -> WriteResult:
        """Write one atomic fact in ``05-memoire/facts/{entity}/{predicate}.md``.

        ``one_fact_one_file``: the note path only depends on
        ``(entity, predicate)`` so rewrites always target the same file.
        """
        entity = validate_segment(entity, "INVALID_ENTITY")
        predicate = validate_segment(predicate, "INVALID_PREDICATE")
        source_value = _clean_source(source)
        project_value = validate_segment(project, "INVALID_PROJECT") if project else None
        wikilinks = [f"[[{entity}]]"]
        for link in links:
            target = validate_segment(link, "INVALID_LINK")
            wikilinks.append(f"[[{target}]]")
        if project_value:
            wikilinks.append(f"[[02-projets/{project_value}/decisions|{project_value}]]")

        path = self._tree.fact_file(entity, predicate)
        ensure_contained(self._tree.root, path)
        existing = self._inspect(path)

        body_lines = [f"# [[{entity}]] — {predicate}", ""]
        body_lines.append(f"- **Entité** : [[{entity}]]")
        body_lines.append(f"- **Prédicat** : `{predicate}`")
        body_lines.append(f"- **Valeur** : `{value}`")
        if project_value:
            body_lines.append(
                f"- **Projet** : [[02-projets/{project_value}/decisions|{project_value}]]"
            )
        if len(wikilinks) > 1 or project_value:
            rendered_links = ", ".join(wikilinks)
            body_lines.append(f"- **Liens** : {rendered_links}")
        body_lines.append("")
        body_lines.append(
            "Fait atomique — une note par fait (`one_fact_one_file`), "
            f"source : `{source_value}`."
        )

        frontmatter: dict[str, Any] = {
            "id": f"fact-{entity}-{predicate}",
            "type": NoteType.FACT.value,
            "entity": entity,
            "predicate": predicate,
            "value": str(value),
            "source": source_value,
            "status": "generated",
            "locked": False,
            "wikilinks": wikilinks,
        }
        if project_value:
            frontmatter["project"] = project_value
        return self._commit(
            path, frontmatter, "\n".join(body_lines) + "\n", NoteType.FACT.value, existing
        )

    # -- entities ------------------------------------------------------------

    def update_entity(
        self,
        entity: str,
        relations: Iterable[tuple[str, str] | Relation] = (),
        *,
        summary: str | None = None,
        source: str | NoteSource = NoteSource.NEO4J,
    ) -> WriteResult:
        """Update ``04-graph/entities/{entity}.md`` without duplicating relations.

        Existing relations recorded in the frontmatter are merged with the new
        ones on the ``(predicate, target)`` key; duplicates are dropped and the
        first-seen order is preserved.
        """
        entity = validate_segment(entity, "INVALID_ENTITY")
        source_value = _clean_source(source)
        path = self._tree.entity_file(entity)
        ensure_contained(self._tree.root, path)
        existing = self._inspect(path)

        merged: list[Relation] = []
        seen: set[tuple[str, str]] = set()
        for relation in _existing_relations(existing.frontmatter) + _normalize_relations(
            relations
        ):
            key = (relation.predicate, relation.target)
            if key in seen:
                continue
            seen.add(key)
            merged.append(relation)

        summary_text = summary if summary is not None else _existing_summary(existing)
        relation_lines = (
            [f"- `{relation.predicate}` → [[{relation.target}]]" for relation in merged]
            or ["- _(aucune relation)_"]
        )
        body = "\n".join(
            [
                f"# [[{entity}]]",
                "",
                summary_text or "Entité suivie dans le graphe métier.",
                "",
                "## Relations",
                "",
                *relation_lines,
            ]
        )

        frontmatter: dict[str, Any] = {
            "id": f"entity-{entity}",
            "type": NoteType.ENTITY.value,
            "entity": entity,
            "summary": summary_text or "",
            "relations": [relation.model_dump() for relation in merged],
            "source": source_value,
            "status": "generated",
            "locked": False,
            "wikilinks": [f"[[{entity}]]"]
            + [f"[[{relation.target}]]" for relation in merged],
        }
        return self._commit(
            path, frontmatter, body, NoteType.ENTITY.value, existing
        )

    # -- dated events --------------------------------------------------------

    def write_event(
        self,
        event_id: str,
        *,
        entity: str,
        summary: str,
        occurred_at: str | None = None,
        links: Iterable[str] = (),
        source: str | NoteSource = NoteSource.AGENT,
    ) -> WriteResult:
        """Write one dated event in ``05-memoire/events/{event_id}.md``.

        Events are the append-only memory of business milestones (invoice
        validation, uploads, reconciliation). The file name only depends on
        ``event_id`` so replaying the same event is idempotent, and the usual
        human review layer still protects manually edited notes.
        """
        event_id = validate_segment(event_id, "INVALID_EVENT_ID")
        entity = validate_segment(entity, "INVALID_ENTITY")
        source_value = _clean_source(source)
        text = (summary or "").strip()
        if not text:
            raise VaultError("EMPTY_EVENT")

        wikilinks = [f"[[{entity}]]"]
        for link in links:
            target = validate_segment(link, "INVALID_LINK")
            if f"[[{target}]]" not in wikilinks:
                wikilinks.append(f"[[{target}]]")

        path = self._tree.dir(EVENTS_DIR) / f"{event_id}.md"
        ensure_contained(self._tree.root, path)
        existing = self._inspect(path)

        body_lines = [
            f"# [[{entity}]] — Événement",
            "",
            f"- **Entité** : [[{entity}]]",
            f"- **Événement** : `{event_id}`",
            f"- **Date** : `{occurred_at or 'inconnue'}`",
            f"- **Résumé** : {text}",
        ]
        if len(wikilinks) > 1:
            body_lines.append(f"- **Liens** : {', '.join(wikilinks)}")
        body_lines.append("")
        body_lines.append(
            "Événement daté — note générée par le pont mémoire, "
            f"source : `{source_value}`."
        )

        frontmatter: dict[str, Any] = {
            "id": f"event-{event_id}",
            "type": "event",
            "entity": entity,
            "event": event_id,
            "summary": text,
            "occurred_at": occurred_at,
            "source": source_value,
            "status": "generated",
            "locked": False,
            "wikilinks": wikilinks,
        }
        return self._commit(
            path, frontmatter, "\n".join(body_lines) + "\n", "event", existing
        )

    # -- generated context notes ---------------------------------------------

    def write_context_note(
        self,
        name: str,
        *,
        body: str,
        wikilinks: Iterable[str] = (),
        source: str | NoteSource = NoteSource.AGENT,
    ) -> WriteResult:
        """Write/update a generated note in ``01-contexte/{name}.md``.

        Used by the accounting pipeline to refresh dashboard notes such as
        ``01-contexte/cockpit_compta.md``. The usual human review layer applies:
        a locked / human-owned / diverged note is returned as ``preserved`` and
        stays byte-identical. The body must already contain its Markdown
        heading (and at least one ``[[wikilink]]`` when the vault constraint
        requires it).
        """
        name = validate_segment(name, "INVALID_NOTE_NAME")
        source_value = _clean_source(source)
        text = (body or "").strip()
        if not text:
            raise VaultError("EMPTY_NOTE")

        links = tuple(
            dict.fromkeys(
                str(link).strip()
                for link in wikilinks
                if str(link).strip()
            )
        )
        path = self._tree.dir(CONTEXT_DIR) / f"{name}.md"
        ensure_contained(self._tree.root, path)
        existing = self._inspect(path)

        frontmatter: dict[str, Any] = {
            "id": f"context-{name}",
            "type": "context",
            "source": source_value,
            "status": "generated",
            "locked": False,
            "wikilinks": list(links),
        }
        return self._commit(
            path, frontmatter, text + "\n", "context", existing
        )

    def write_compta_note(
        self,
        name: str,
        *,
        body: str,
        wikilinks: Iterable[str] = (),
        source: str | NoteSource = NoteSource.AGENT,
    ) -> WriteResult:
        """Write/update the P17 accounting cockpit in ``07-compta/{name}.md``.

        The historical ``01-contexte/cockpit_compta.md`` alias is kept by the
        caller so existing Obsidian links never break. The human review layer
        is identical to :meth:`write_context_note`: a protected note is left
        byte-identical with ``status='preserved'``.
        """
        name = validate_segment(name, "INVALID_NOTE_NAME")
        source_value = _clean_source(source)
        text = (body or "").strip()
        if not text:
            raise VaultError("EMPTY_NOTE")

        links = tuple(
            dict.fromkeys(
                str(link).strip()
                for link in wikilinks
                if str(link).strip()
            )
        )
        path = self.tree.root / COMPTA_DIR / f"{name}.md"
        ensure_contained(self.tree.root, path)
        existing = self._inspect(path)

        frontmatter: dict[str, Any] = {
            "id": f"compta-{name}",
            "type": "context",
            "source": source_value,
            "status": "generated",
            "locked": False,
            "wikilinks": list(links),
        }
        return self._commit(
            path, frontmatter, text + "\n", "context", existing
        )

    # -- decisions & active context ------------------------------------------

    def record_decision(
        self,
        project: str,
        decision_text: str,
        *,
        next_action: str | None = None,
    ) -> DecisionRecordResult:
        """Append one decision to the project log then refresh the active context.

        The decision id is hash-derived from ``(project, decision_text)`` so
        recording the same decision twice is idempotent (``unchanged``). The
        two notes are protected independently: a human-owned decision log is
        left strictly untouched while the active context still follows its own
        review layer.
        """
        project = validate_segment(project, "INVALID_PROJECT")
        text = (decision_text or "").strip()
        if not text:
            raise VaultError("EMPTY_DECISION")
        decision_id = derive_decision_id(project, text)

        decisions_path = self._tree.decisions_file(project)
        ensure_contained(self._tree.root, decisions_path)
        existing = self._inspect(decisions_path)

        if existing.protected is not None:
            decisions_result = WriteResult(
                path=decisions_path,
                status=OUTCOME_PRESERVED,
                note_type=NoteType.DECISION.value,
                detail=existing.protected,
            )
        elif f"## {decision_id} — " in existing.body:
            decisions_result = WriteResult(
                path=decisions_path,
                status=OUTCOME_UNCHANGED,
                note_type=NoteType.DECISION.value,
                detail="",
            )
        else:
            base_body = (
                existing.body
                if existing.exists
                else "# Décisions — [[01-contexte/active|Contexte actif]]\n"
            )
            entry = f"\n## {decision_id} — {self._iso_now()}\n\n{text}\n"
            new_body = normalize_body(base_body + entry)
            frontmatter: dict[str, Any] = {
                "id": f"decisions-{project}",
                "type": NoteType.DECISION.value,
                "project": project,
                "source": NoteSource.AGENT.value,
                "status": "generated",
                "locked": False,
                "wikilinks": [f"[[01-contexte/active|Contexte actif]]"],
            }
            decisions_result = self._commit(
                decisions_path,
                frontmatter,
                new_body,
                NoteType.DECISION.value,
                existing,
            )

        active_result = self.update_active_context(project, next_action=next_action)
        return DecisionRecordResult(
            decision_id=decision_id,
            decisions=decisions_result,
            active=active_result,
        )

    def update_active_context(
        self,
        project: str,
        *,
        next_action: str | None = None,
    ) -> WriteResult:
        """Regenerate ``01-contexte/active.md`` from the project decision log.

        The note shows the current project, the next action and the last five
        decisions (newest first) parsed from ``02-projets/{project}/decisions.md``.
        """
        project = validate_segment(project, "INVALID_PROJECT")
        active_path = self._tree.active_file()
        ensure_contained(self._tree.root, active_path)
        existing = self._inspect(active_path)

        if next_action is None:
            next_action = str(existing.frontmatter.get("next_action") or "")
        next_action = next_action.strip()

        entries = read_decisions(self._tree.root, project)
        recent = list(reversed(entries))[:5]
        decision_lines = (
            [
                f"{index}. **{entry.decision_id}** ({entry.timestamp}) — {entry.excerpt}"
                for index, entry in enumerate(recent, start=1)
            ]
            or ["1. _(aucune décision)_"]
        )
        body = "\n".join(
            [
                "# Contexte actif",
                "",
                f"- **Projet en cours** : [[02-projets/{project}/decisions|{project}]]",
                f"- **Prochaine action** : {next_action or '_(à définir)_'}",
                "",
                "## 5 dernières décisions",
                "",
                *decision_lines,
            ]
        )
        frontmatter: dict[str, Any] = {
            "id": "context-active",
            "type": "context",
            "project": project,
            "next_action": next_action,
            "source": NoteSource.AGENT.value,
            "status": "generated",
            "locked": False,
            "wikilinks": [f"[[02-projets/{project}/decisions|{project}]]"],
        }
        return self._commit(active_path, frontmatter, body, "context", existing)


# -- vault reader -------------------------------------------------------------


class VaultReader:
    """Assemble prioritised context: hot -> warm -> rag.

    The reader is strictly read-only; budgets come from the vault constraints
    (``_system/schema.yaml``) or from the explicit ``constraints`` override.
    """

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        constraints: VaultConstraints | None = None,
    ) -> None:
        self._root = resolve_vault_root(root)
        self._constraints = constraints or load_constraints(self._root)

    @property
    def root(self) -> Path:
        return self._root

    def read_note(self, relative_path: str) -> str | None:
        """Return the body of one note, or None when the note is absent."""
        path = ensure_contained(self._root, self._root / relative_path)
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8-sig")
        parsed = parse_note(raw)
        return parsed[1] if parsed else raw

    def read_context(
        self,
        project: str,
        *,
        query: str | None = None,
        vector_client: VectorSearchClient | None = None,
        rag_limit: int = 5,
    ) -> ContextBundle:
        """Build the prioritised :class:`ContextBundle` for ``project``."""
        project = validate_segment(project, "INVALID_PROJECT")

        hot_raw = self.read_note(ACTIVE_CONTEXT_FILE) or ""
        hot, hot_truncated = truncate_to_tokens(
            hot_raw, self._constraints.max_hot_context
        )

        warm_sections: list[str] = []
        project_dir = ensure_contained(self._root, self._root / PROJECTS_DIR / project)
        if project_dir.is_dir():
            for note in sorted(project_dir.glob("*.md"), key=lambda p: p.name):
                body = self.read_note(
                    f"{PROJECTS_DIR}/{project}/{note.name}"
                ) or note.read_text(encoding="utf-8-sig")
                warm_sections.append(f"## {note.name}\n\n{body.strip()}")
        warm_raw = "\n\n".join(warm_sections)
        warm, warm_truncated = truncate_to_tokens(
            warm_raw, self._constraints.max_warm_context
        )

        rag: tuple[SearchHit, ...] = ()
        if query and vector_client is not None:
            rag = tuple(_coerce_hit(hit) for hit in vector_client.search(query, rag_limit))

        return ContextBundle(
            project=project,
            hot=hot,
            warm=warm,
            rag=rag,
            hot_tokens=estimate_tokens(hot),
            warm_tokens=estimate_tokens(warm),
            hot_truncated=hot_truncated,
            warm_truncated=warm_truncated,
        )


# -- decision log parsing -----------------------------------------------------


def derive_decision_id(project: str, decision_text: str) -> str:
    """Deterministic decision id derived from ``(project, decision_text)``."""
    return "D-" + uuid5(
        NAMESPACE_URL, f"decision:{project}:{decision_text}"
    ).hex[:8]


def read_decisions(root: str | os.PathLike[str] | None, project: str) -> list[DecisionEntry]:
    """Parse the decision entries of ``02-projets/{project}/decisions.md``."""
    base = resolve_vault_root(root)
    path = base / PROJECTS_DIR / project / "decisions.md"
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8-sig")
    parsed = parse_note(raw)
    body = parsed[1] if parsed else raw
    entries: list[DecisionEntry] = []
    matches = list(DECISION_ENTRY_RE.finditer(body))
    for position, match in enumerate(matches):
        start = match.end()
        end = matches[position + 1].start() if position + 1 < len(matches) else len(body)
        excerpt = next(
            (line.strip() for line in body[start:end].splitlines() if line.strip()),
            "",
        )
        entries.append(
            DecisionEntry(
                decision_id=match.group(1),
                timestamp=match.group(2),
                excerpt=excerpt[:80],
            )
        )
    return entries


# -- semantic chunking & RAG sync ---------------------------------------------


def chunk_by_heading(
    text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: float = DEFAULT_OVERLAP,
    source: str = "",
) -> list[Chunk]:
    """Split Markdown ``text`` into chunks at H2/H3 headings.

    A preamble before the first H2/H3 heading becomes its own chunk
    (``heading_level=0``). Sections longer than ``max_chars`` are cut into
    deterministic overlapping windows of ``int(max_chars * overlap)`` chars.
    """
    if not isinstance(max_chars, int) or max_chars < 1:
        raise VaultError("INVALID_CHUNK_PARAMS", f"max_chars={max_chars!r}")
    if (
        isinstance(overlap, bool)
        or not isinstance(overlap, (int, float))
        or not 0.0 <= float(overlap) < 1.0
    ):
        raise VaultError("INVALID_CHUNK_PARAMS", f"overlap={overlap!r}")

    if not text.strip():
        return []

    chunks: list[Chunk] = []
    index = 0
    for heading, level, section in _split_sections(text):
        overlap_chars = int(max_chars * float(overlap))
        step = max_chars - overlap_chars
        start = 0
        length = len(section)
        while start < length:
            piece = section[start : start + max_chars]
            chunks.append(
                Chunk.build(
                    heading=heading,
                    heading_level=level,
                    index=index,
                    text=piece,
                )
            )
            index += 1
            if start + max_chars >= length:
                break
            start += step
    return chunks


def rag_sync(
    root: str | os.PathLike[str] | None = None,
    *,
    client: VectorStoreClient,
    collection: str = DEFAULT_COLLECTION,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: float = DEFAULT_OVERLAP,
    embedder: Callable[[str], list[float]] | None = None,
) -> RagSyncReport:
    """Chunk ``03-rag/`` Markdown files and upsert them into ``client``.

    Idempotence is hash-based: a file whose SHA-256 is unchanged since the last
    pass is skipped entirely (no client call at all). Changed files are
    re-chunked; stale chunk ids are deleted first. The sync index lives in
    ``_system/rag_sync_index.yaml`` and is written atomically.
    """
    if client is None:
        raise VaultError("MISSING_CLIENT")
    if not isinstance(collection, str) or not collection.strip():
        raise VaultError("INVALID_COLLECTION", repr(collection))
    embed = embedder or hash_embed

    tree = ensure_vault_tree(root)
    rag_root = tree.rag_dir()
    manifest_path = tree.manifest_file()
    manifest = _load_manifest(manifest_path)

    files_scanned = 0
    files_synced = 0
    files_skipped = 0
    files_removed = 0
    chunks_upserted = 0
    chunks_deleted = 0
    traces: list[str] = []
    seen: set[str] = set()

    for path in sorted(rag_root.rglob("*.md"), key=lambda p: p.as_posix()):
        relative = path.relative_to(rag_root).as_posix()
        seen.add(relative)
        files_scanned += 1
        digest = hash_file(path)
        entry = manifest.get(relative)
        if entry and entry.get("sha256") == digest:
            files_skipped += 1
            traces.append(f"skipped:{relative}")
            continue

        text = path.read_text(encoding="utf-8-sig")
        chunks = chunk_by_heading(
            text, max_chars=max_chars, overlap=overlap, source=relative
        )
        points = [
            VectorPoint(
                id=chunk_id(relative, chunk),
                vector=embed(chunk.text),
                payload={
                    "source": relative,
                    "heading": chunk.heading,
                    "heading_level": chunk.heading_level,
                    "index": chunk.index,
                    "text": chunk.text,
                    "sha256": chunk.sha256,
                },
            )
            for chunk in chunks
        ]
        new_ids = [point.id for point in points]
        stale_ids = [
            chunk_id
            for chunk_id in (entry or {}).get("chunk_ids", [])
            if chunk_id not in new_ids
        ]
        if stale_ids:
            client.delete(collection, stale_ids)
            chunks_deleted += len(stale_ids)
            traces.append(f"deleted:{relative}:{len(stale_ids)}")
        if points:
            client.upsert(collection, points)
            chunks_upserted += len(points)
        manifest[relative] = {"sha256": digest, "chunk_ids": new_ids}
        files_synced += 1
        traces.append(f"synced:{relative}:{len(points)}")

    for relative in sorted(set(manifest) - seen):
        stale_ids = list(manifest[relative].get("chunk_ids", []))
        if stale_ids:
            client.delete(collection, stale_ids)
            chunks_deleted += len(stale_ids)
        del manifest[relative]
        files_removed += 1
        traces.append(f"removed:{relative}")

    if files_synced or files_removed:
        _save_manifest(manifest_path, manifest)

    return RagSyncReport(
        files_scanned=files_scanned,
        files_synced=files_synced,
        files_skipped=files_skipped,
        files_removed=files_removed,
        chunks_upserted=chunks_upserted,
        chunks_deleted=chunks_deleted,
        manifest=manifest_path,
        traces=tuple(traces),
    )


def chunk_id(source: str, chunk: Chunk) -> str:
    """Deterministic chunk identifier derived from source + content hash."""
    return str(
        uuid5(NAMESPACE_URL, f"rag:{source}:{chunk.index}:{chunk.sha256}")
    )


# -- internal helpers ---------------------------------------------------------


def _protection_reason(frontmatter: Mapping[str, Any], body: str) -> str | None:
    """Human review layer: return why the note must not be touched, if ever."""
    if str(frontmatter.get("source", "")).strip().lower() == NoteSource.HUMAN.value:
        return PROTECTED_HUMAN
    locked = frontmatter.get("locked")
    if locked is True or str(locked).strip().lower() == "true":
        return PROTECTED_LOCKED
    if str(frontmatter.get("status", "")).strip().lower() == "preserved":
        return PROTECTED_STATUS
    recorded = frontmatter.get("content_hash")
    if recorded is None:
        return PROTECTED_NO_HASH
    if not isinstance(recorded, str) or _HASH_RE.fullmatch(recorded) is None:
        return PROTECTED_NO_HASH
    if payload_hash(frontmatter, body) != recorded:
        return PROTECTED_DIVERGED
    return None


def _same_note(
    old_frontmatter: Mapping[str, Any],
    old_body: str,
    new_frontmatter: Mapping[str, Any],
    new_body: str,
) -> bool:
    """True when the rewrite is semantically identical (ignoring ``updated_at``)."""
    old_stable = {k: v for k, v in old_frontmatter.items() if k != "updated_at"}
    new_stable = {k: v for k, v in new_frontmatter.items() if k != "updated_at"}
    return old_stable == new_stable and normalize_body(old_body) == normalize_body(new_body)


def _clean_source(source: object) -> str:
    if isinstance(source, NoteSource):
        return source.value
    if isinstance(source, str):
        try:
            return NoteSource(source.strip().lower()).value
        except ValueError:
            return source.strip().lower()
    raise VaultError("INVALID_SOURCE", repr(source))


def _normalize_relations(
    relations: Iterable[tuple[str, str] | Relation],
) -> list[Relation]:
    normalized: list[Relation] = []
    for relation in relations:
        if isinstance(relation, Relation):
            model = relation
        elif isinstance(relation, (tuple, list)) and len(relation) == 2:
            model = Relation(predicate=str(relation[0]), target=str(relation[1]))
        else:
            raise VaultError("INVALID_RELATION", repr(relation))
        validate_segment(model.predicate, "INVALID_PREDICATE")
        validate_segment(model.target, "INVALID_TARGET")
        normalized.append(model)
    return normalized


def _existing_relations(frontmatter: Mapping[str, Any]) -> list[Relation]:
    relations: list[Relation] = []
    for raw in frontmatter.get("relations") or []:
        if isinstance(raw, Mapping) and "predicate" in raw and "target" in raw:
            relations.append(
                Relation(predicate=str(raw["predicate"]), target=str(raw["target"]))
            )
    return relations


def _existing_summary(existing: _ExistingNote) -> str:
    return str(existing.frontmatter.get("summary") or "")


def _coerce_hit(raw: Any) -> SearchHit:
    if isinstance(raw, SearchHit):
        return raw
    if isinstance(raw, Mapping):
        return SearchHit.model_validate(dict(raw))
    raise VaultError("INVALID_SEARCH_HIT", repr(raw))


def _split_sections(text: str) -> list[tuple[str, int, str]]:
    """Split ``text`` into ``(heading, level, section_text)`` at H2/H3 lines."""
    lines = text.splitlines(keepends=True)
    sections: list[tuple[str, int, str]] = []
    heading = ""
    level = 0
    buffer: list[str] = []
    for line in lines:
        match = _HEADING_RE.match(line.rstrip("\r\n"))
        if match:
            if buffer:
                sections.append((heading, level, "".join(buffer)))
            heading = match.group(2).strip()
            level = len(match.group(1))
            buffer = [line]
        else:
            buffer.append(line)
    if buffer:
        sections.append((heading, level, "".join(buffer)))
    return [(h, lv, s) for h, lv, s in sections if s.strip()]


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise VaultError("INVALID_MANIFEST", str(path))
    files = data.get("files")
    if not isinstance(files, dict):
        return {}
    return {str(key): dict(value) for key, value in files.items() if isinstance(value, dict)}


def _save_manifest(path: Path, files: Mapping[str, Any]) -> None:
    ordered = {key: files[key] for key in sorted(files)}
    text = yaml.safe_dump(
        {"version": 1, "files": ordered},
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    atomic_write(path, text)
