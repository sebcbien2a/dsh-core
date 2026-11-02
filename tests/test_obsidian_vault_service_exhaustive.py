"""P15.1 - Exhaustive tests of the Obsidian vault bridge (offline).

Covers: tree initialisation and ``_system/schema.yaml``, vault writer (facts,
entities, decisions, active context), the human review layer (human source,
lock, divergence -> ``preserved`` with byte-stable notes), the vault reader
token budgets (``max_hot_context`` / ``max_warm_context``), semantic H2/H3
chunking and ``rag_sync`` idempotence with a mock Qdrant client.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from schemas.obsidian_vault_schema import (
    ACTIVE_CONTEXT_FILE,
    DEFAULT_VAULT_ROOT,
    FACTS_DIR,
    RAG_MANIFEST_FILE,
    SCHEMA_FILE,
    VAULT_ROOT_ENV,
    VAULT_TREE,
    Chunk,
    NoteSource,
    NoteType,
    Relation,
    SearchHit,
    VaultConstraints,
    VaultSchemaModel,
    default_vault_schema,
    estimate_tokens,
    truncate_to_tokens,
)
from services.obsidian_vault_service import (
    DEFAULT_COLLECTION,
    OUTCOME_CREATED,
    OUTCOME_PRESERVED,
    OUTCOME_UNCHANGED,
    OUTCOME_UPDATED,
    PROTECTED_DIVERGED,
    PROTECTED_HUMAN,
    PROTECTED_LOCKED,
    PROTECTED_NO_HASH,
    PROTECTED_STATUS,
    PROTECTED_UNMANAGED,
    VaultError,
    VaultReader,
    VaultWriter,
    atomic_write,
    chunk_by_heading,
    chunk_id,
    derive_decision_id,
    ensure_contained,
    ensure_vault_tree,
    hash_embed,
    hash_file,
    hash_text,
    load_constraints,
    normalize_body,
    parse_note,
    payload_hash,
    rag_sync,
    read_decisions,
    render_note,
    render_schema_yaml,
    resolve_vault_root,
    validate_segment,
)

FIXED_NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
LATER_NOW = datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc)


def err(call, *args, **kwargs) -> VaultError:
    with pytest.raises(VaultError) as info:
        call(*args, **kwargs)
    return info.value


def writer(tmp_path, clock=None) -> VaultWriter:
    return VaultWriter(tmp_path, now=clock or (lambda: FIXED_NOW))


def read(tmp_path: Path, relative: str) -> str:
    return (tmp_path / relative).read_text(encoding="utf-8")


def body_of(tmp_path: Path, relative: str) -> str:
    parsed = parse_note(read(tmp_path, relative))
    assert parsed is not None
    return parsed[1]


def frontmatter_of(tmp_path: Path, relative: str) -> dict:
    parsed = parse_note(read(tmp_path, relative))
    assert parsed is not None
    return parsed[0]


class MockVectorStore:
    """Mockable Qdrant-like client recording every call."""

    def __init__(self) -> None:
        self.upserts: list[tuple[str, list]] = []
        self.deletes: list[tuple[str, list[str]]] = []

    def upsert(self, collection, points):
        self.upserts.append((collection, list(points)))

    def delete(self, collection, ids):
        self.deletes.append((collection, list(ids)))

    @property
    def upserted_points(self):
        return [point for _, points in self.upserts for point in points]

    @property
    def deleted_ids(self):
        return [chunk for _, ids in self.deletes for chunk in ids]


class MockVectorSearch:
    """Mockable vector search client for the vault reader."""

    def __init__(self, hits=()) -> None:
        self.hits = list(hits)
        self.calls: list[tuple[str, int]] = []

    def search(self, query, limit=5):
        self.calls.append((query, limit))
        return self.hits[:limit]


# -- schema & root resolution -------------------------------------------------


def test_vault_root_env_constant():
    assert VAULT_ROOT_ENV == "OBSIDIAN_VAULT_PATH"


def test_default_vault_root_is_workspace_vault():
    assert DEFAULT_VAULT_ROOT == Path("workspace") / "vault"


def test_vault_tree_eight_directories():
    assert VAULT_TREE == (
        "_system",
        "01-contexte",
        "02-projets",
        "03-rag",
        "04-graph/entities",
        "05-memoire/facts",
        "05-memoire/events",
        "06-journal",
    )


def test_resolve_root_explicit_wins(tmp_path, monkeypatch):
    monkeypatch.setenv(VAULT_ROOT_ENV, str(tmp_path / "env"))
    assert resolve_vault_root(tmp_path / "explicit") == tmp_path / "explicit"


def test_resolve_root_uses_env_variable(tmp_path, monkeypatch):
    monkeypatch.setenv(VAULT_ROOT_ENV, str(tmp_path / "from_env"))
    assert resolve_vault_root() == tmp_path / "from_env"


def test_resolve_root_default_when_env_absent(monkeypatch):
    monkeypatch.delenv(VAULT_ROOT_ENV, raising=False)
    assert resolve_vault_root() == DEFAULT_VAULT_ROOT


def test_resolve_root_rejects_nul():
    assert err(resolve_vault_root, "bad\x00root").code == "INVALID_ROOT"


def test_resolve_root_blank_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv(VAULT_ROOT_ENV, "   ")
    assert resolve_vault_root() == DEFAULT_VAULT_ROOT


def test_ensure_contained_accepts_child(tmp_path):
    assert ensure_contained(tmp_path, tmp_path / "a" / "b").is_absolute()


def test_ensure_contained_rejects_escape():
    base = Path("workspace") / "vault"
    assert err(ensure_contained, base, base / ".." / "etc").code == "PATH_TRAVERSAL"


def test_validate_segment_accepts_names():
    assert validate_segment("SMTRT") == "SMTRT"
    assert validate_segment("fact-01_x.y-z") == "fact-01_x.y-z"


@pytest.mark.parametrize("bad", ["", " ", "..", ".hidden", "a/b", "a\\b", 12, None])
def test_validate_segment_rejects_unsafe(bad):
    assert err(validate_segment, bad).code == "INVALID_SEGMENT"


def test_validate_segment_custom_code():
    assert err(validate_segment, "a/b", "INVALID_ENTITY").code == "INVALID_ENTITY"


# -- schema.yaml model --------------------------------------------------------


def test_note_types_are_the_four_required():
    schema = default_vault_schema()
    assert set(schema.note_types) == {"fact", "decision", "entity", "rag-chunk"}


def test_note_type_enum_values():
    assert [t.value for t in NoteType] == ["fact", "decision", "entity", "rag-chunk"]


def test_constraints_defaults():
    constraints = default_vault_schema().constraints
    assert constraints.max_hot_context == 2000
    assert constraints.max_warm_context == 4000
    assert constraints.one_fact_one_file is True
    assert constraints.wikilinks_required is True


def test_schema_yaml_render_contains_types_routing_constraints():
    text = render_schema_yaml()
    for token in ("fact", "decision", "entity", "rag-chunk"):
        assert f"{token}:" in text
    assert "routing:" in text
    assert "max_hot_context: 2000" in text
    assert "max_warm_context: 4000" in text
    assert "one_fact_one_file: true" in text
    assert "wikilinks_required: true" in text


def test_schema_yaml_round_trip():
    schema = VaultSchemaModel.from_yaml(render_schema_yaml())
    assert schema.to_yaml() == render_schema_yaml()


def test_schema_yaml_is_deterministic():
    assert render_schema_yaml() == render_schema_yaml()


def test_schema_rejects_missing_note_type():
    with pytest.raises(Exception):
        VaultSchemaModel.model_validate(
            {
                "note_types": {"fact": {"path_template": "x"}},
                "routing": {"fact": "x", "decision": "y", "entity": "z", "rag-chunk": "w", "context": "c"},
            }
        )


def test_schema_rejects_missing_context_route():
    with pytest.raises(Exception):
        VaultSchemaModel.model_validate(
            {
                "note_types": {
                    "fact": {"path_template": "x"},
                    "decision": {"path_template": "x"},
                    "entity": {"path_template": "x"},
                    "rag-chunk": {"path_template": "x"},
                },
                "routing": {
                    "fact": "x",
                    "decision": "y",
                    "entity": "z",
                    "rag-chunk": "w",
                },
            }
        )


def test_constraints_reject_non_positive_budget():
    with pytest.raises(Exception):
        VaultConstraints(max_hot_context=0)


def test_constraints_reject_unknown_field():
    with pytest.raises(Exception):
        VaultConstraints(extra_budget=10)


# -- note rendering & hashing -------------------------------------------------


def test_render_parse_round_trip():
    frontmatter = {"id": "x", "type": "fact", "locked": False, "n": 3}
    text = render_note(frontmatter, "# [[A]]\n")
    parsed = parse_note(text)
    assert parsed is not None
    loaded, body = parsed
    assert loaded["id"] == "x"
    assert loaded["locked"] is False
    assert loaded["n"] == 3
    assert body == "# [[A]]\n"


def test_parse_note_rejects_missing_frontmatter():
    assert parse_note("# Just markdown\n") is None


def test_parse_note_rejects_unterminated_frontmatter():
    assert parse_note("---\nid: x\n") is None


def test_parse_note_rejects_non_mapping_frontmatter():
    assert parse_note("---\n- a\n- b\n---\nbody\n") is None


def test_payload_hash_stable_across_yaml_round_trip():
    frontmatter = {
        "id": "x",
        "created_at": "2026-11-02T12:00:00+00:00",
        "updated_at": "2026-11-02T12:00:00+00:00",
        "lst": ["[[A]]", "[[B]]"],
    }
    text = render_note(frontmatter, "# [[A]]\n")
    loaded, body = parse_note(text)
    assert payload_hash(frontmatter, "# [[A]]\n") == payload_hash(loaded, body)


def test_payload_hash_ignores_updated_at_and_content_hash():
    base = {"id": "x", "created_at": "c", "updated_at": "a", "content_hash": "h"}
    other = {"id": "x", "created_at": "c", "updated_at": "b", "content_hash": "h2"}
    assert payload_hash(base, "body\n") == payload_hash(other, "body\n")


def test_payload_hash_changes_with_body():
    frontmatter = {"id": "x"}
    assert payload_hash(frontmatter, "a\n") != payload_hash(frontmatter, "b\n")


def test_normalize_body_trims_and_keeps_one_newline():
    assert normalize_body("\n\nfoo\n\n\n") == "foo\n"
    assert normalize_body("foo") == "foo\n"


def test_hash_text_and_hash_file(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("hello", encoding="utf-8")
    assert hash_file(target) == hash_text("hello")


def test_hash_embed_is_deterministic_64_dims():
    first = hash_embed("abc")
    second = hash_embed("abc")
    assert first == second
    assert len(first) == 64
    assert first != hash_embed("abd")
    assert err(hash_embed, "abc", 32).code == "INVALID_EMBED_DIM"


def test_atomic_write_creates_and_replaces(tmp_path):
    target = tmp_path / "sub" / "note.md"
    atomic_write(target, "one\n")
    atomic_write(target, "two\n")
    assert target.read_text(encoding="utf-8") == "two\n"
    assert not (tmp_path / "sub" / "note.md.tmp-vault").exists()


# -- ensure_vault_tree --------------------------------------------------------


def test_ensure_tree_creates_all_directories(tmp_path):
    tree = ensure_vault_tree(tmp_path)
    for name in VAULT_TREE:
        assert (tmp_path / name).is_dir()
        assert tree.dir(name) == (tmp_path / name).resolve()


def test_ensure_tree_writes_schema_yaml(tmp_path):
    ensure_vault_tree(tmp_path)
    schema_path = tmp_path / SCHEMA_FILE
    assert schema_path.exists()
    schema = VaultSchemaModel.from_yaml(schema_path.read_text(encoding="utf-8"))
    assert schema.constraints.max_hot_context == 2000
    assert schema.constraints.max_warm_context == 4000
    assert schema.constraints.one_fact_one_file is True
    assert schema.constraints.wikilinks_required is True


def test_ensure_tree_schema_is_absent_written_once(tmp_path):
    ensure_vault_tree(tmp_path)
    schema_path = tmp_path / SCHEMA_FILE
    schema_path.write_text("# human schema\n", encoding="utf-8")
    ensure_vault_tree(tmp_path)
    assert schema_path.read_text(encoding="utf-8") == "# human schema\n"


def test_ensure_tree_is_idempotent(tmp_path):
    first = ensure_vault_tree(tmp_path)
    second = ensure_vault_tree(tmp_path)
    assert first.root == second.root
    assert first.schema_path == second.schema_path


def test_ensure_tree_respects_env_root(tmp_path, monkeypatch):
    monkeypatch.setenv(VAULT_ROOT_ENV, str(tmp_path / "vault_env"))
    tree = ensure_vault_tree()
    assert tree.root == (tmp_path / "vault_env").resolve()
    assert (tmp_path / "vault_env" / "_system" / "schema.yaml").exists()


def test_ensure_tree_rejects_file_on_directory_path(tmp_path):
    (tmp_path / "03-rag").write_text("block", encoding="utf-8")
    assert err(ensure_vault_tree, tmp_path).code == "NOT_A_DIRECTORY"


def test_ensure_tree_rejects_root_standing_on_file(tmp_path):
    target = tmp_path / "not_a_dir"
    target.write_text("x", encoding="utf-8")
    assert err(ensure_vault_tree, target).code == "NOT_A_DIRECTORY"


def test_load_constraints_defaults_without_schema(tmp_path):
    constraints = load_constraints(tmp_path)
    assert constraints == default_vault_schema().constraints


def test_load_constraints_reads_written_schema(tmp_path):
    ensure_vault_tree(tmp_path)
    constraints = load_constraints(tmp_path)
    assert constraints.max_hot_context == 2000
    assert constraints.max_warm_context == 4000


def test_load_constraints_rejects_corrupt_schema(tmp_path):
    ensure_vault_tree(tmp_path)
    (tmp_path / SCHEMA_FILE).write_text("::: not yaml", encoding="utf-8")
    assert err(load_constraints, tmp_path).code == "INVALID_SCHEMA"


# -- vault_writer: facts ------------------------------------------------------


def test_write_fact_creates_expected_path(tmp_path):
    result = writer(tmp_path).write_fact("SMTRT", "tva_intracom", "FR123")
    assert result.status == OUTCOME_CREATED
    assert result.note_type == "fact"
    assert result.path == tmp_path / FACTS_DIR / "SMTRT" / "tva_intracom.md"
    assert result.path.exists()


def test_write_fact_frontmatter_is_complete(tmp_path):
    writer(tmp_path).write_fact("SMTRT", "tva", "FR123")
    fm = frontmatter_of(tmp_path, f"{FACTS_DIR}/SMTRT/tva.md")
    for field in (
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
    ):
        assert field in fm
    assert fm["type"] == "fact"
    assert fm["entity"] == "SMTRT"
    assert fm["predicate"] == "tva"
    assert fm["value"] == "FR123"
    assert fm["source"] == "mem0"
    assert fm["status"] == "generated"
    assert fm["locked"] is False


def test_write_fact_body_contains_wikilinks(tmp_path):
    writer(tmp_path).write_fact("SMTRT", "tva", "FR123", project="P15", links=("EDF",))
    body = body_of(tmp_path, f"{FACTS_DIR}/SMTRT/tva.md")
    assert "[[SMTRT]]" in body
    assert "[[EDF]]" in body
    assert "[[02-projets/P15/decisions|P15]]" in body


def test_write_fact_records_content_hash(tmp_path):
    writer(tmp_path).write_fact("SMTRT", "tva", "FR123")
    fm = frontmatter_of(tmp_path, f"{FACTS_DIR}/SMTRT/tva.md")
    body = body_of(tmp_path, f"{FACTS_DIR}/SMTRT/tva.md")
    assert fm["content_hash"] == payload_hash(fm, body)


def test_one_fact_one_file_rewrites_same_path(tmp_path):
    w = writer(tmp_path)
    w.write_fact("SMTRT", "tva", "FR123")
    result = w.write_fact("SMTRT", "tva", "FR456")
    assert result.status == OUTCOME_UPDATED
    facts_dir = tmp_path / FACTS_DIR / "SMTRT"
    assert [p.name for p in facts_dir.glob("*.md")] == ["tva.md"]
    assert frontmatter_of(tmp_path, f"{FACTS_DIR}/SMTRT/tva.md")["value"] == "FR456"


def test_write_fact_is_idempotent_with_frozen_clock(tmp_path):
    w = writer(tmp_path)
    w.write_fact("SMTRT", "tva", "FR123")
    result = w.write_fact("SMTRT", "tva", "FR123")
    assert result.status == OUTCOME_UNCHANGED


def test_write_fact_idempotent_leaves_bytes_stable(tmp_path):
    w = writer(tmp_path)
    w.write_fact("SMTRT", "tva", "FR123")
    before = read(tmp_path, f"{FACTS_DIR}/SMTRT/tva.md")
    w.write_fact("SMTRT", "tva", "FR123")
    assert read(tmp_path, f"{FACTS_DIR}/SMTRT/tva.md") == before


def test_write_fact_update_keeps_created_at(tmp_path):
    clock = {"now": FIXED_NOW}
    w = VaultWriter(tmp_path, now=lambda: clock["now"])
    w.write_fact("SMTRT", "tva", "FR123")
    created_at = frontmatter_of(tmp_path, f"{FACTS_DIR}/SMTRT/tva.md")["created_at"]
    clock["now"] = LATER_NOW
    w.write_fact("SMTRT", "tva", "FR456")
    fm = frontmatter_of(tmp_path, f"{FACTS_DIR}/SMTRT/tva.md")
    assert fm["created_at"] == created_at
    assert fm["updated_at"] == LATER_NOW.isoformat()


def test_write_fact_source_can_be_neo4j(tmp_path):
    writer(tmp_path).write_fact("SMTRT", "siren", "123", source=NoteSource.NEO4J)
    fm = frontmatter_of(tmp_path, f"{FACTS_DIR}/SMTRT/siren.md")
    assert fm["source"] == "neo4j"


def test_write_fact_rejects_unsafe_entity(tmp_path):
    assert err(writer(tmp_path).write_fact, "../EVIL", "tva", "x").code == "INVALID_ENTITY"


def test_write_fact_rejects_unsafe_predicate(tmp_path):
    assert err(writer(tmp_path).write_fact, "SMTRT", "a/b", "x").code == "INVALID_PREDICATE"


def test_write_fact_rejects_unsafe_project(tmp_path):
    assert err(
        writer(tmp_path).write_fact, "SMTRT", "tva", "x", project="../P"
    ).code == "INVALID_PROJECT"


def test_write_fact_rejects_unsafe_link(tmp_path):
    assert err(
        writer(tmp_path).write_fact, "SMTRT", "tva", "x", links=("a/b",)
    ).code == "INVALID_LINK"


def test_write_fact_empty_value_allowed(tmp_path):
    result = writer(tmp_path).write_fact("SMTRT", "tva", "")
    assert result.status == OUTCOME_CREATED


# -- vault_writer: human review layer ----------------------------------------


def test_human_source_note_is_preserved(tmp_path):
    w = writer(tmp_path)
    w.write_fact("SMTRT", "tva", "FR123")
    path = tmp_path / FACTS_DIR / "SMTRT" / "tva.md"
    text = read(tmp_path, f"{FACTS_DIR}/SMTRT/tva.md").replace(
        "source: mem0", "source: human"
    )
    path.write_text(text, encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    result = w.write_fact("SMTRT", "tva", "FR999")
    assert result.status == OUTCOME_PRESERVED
    assert result.detail == PROTECTED_HUMAN
    assert path.read_text(encoding="utf-8") == before


def test_locked_note_is_preserved(tmp_path):
    w = writer(tmp_path)
    w.write_fact("SMTRT", "tva", "FR123")
    path = tmp_path / FACTS_DIR / "SMTRT" / "tva.md"
    text = path.read_text(encoding="utf-8").replace("locked: false", "locked: true")
    path.write_text(text, encoding="utf-8")
    result = w.write_fact("SMTRT", "tva", "FR999")
    assert result.status == OUTCOME_PRESERVED
    assert result.detail == PROTECTED_LOCKED


def test_preserved_status_note_is_preserved(tmp_path):
    w = writer(tmp_path)
    w.write_fact("SMTRT", "tva", "FR123")
    path = tmp_path / FACTS_DIR / "SMTRT" / "tva.md"
    text = path.read_text(encoding="utf-8").replace(
        "status: generated", "status: preserved"
    )
    path.write_text(text, encoding="utf-8")
    result = w.write_fact("SMTRT", "tva", "FR999")
    assert result.status == OUTCOME_PRESERVED
    assert result.detail == PROTECTED_STATUS


def test_diverged_body_is_preserved(tmp_path):
    w = writer(tmp_path)
    w.write_fact("SMTRT", "tva", "FR123")
    path = tmp_path / FACTS_DIR / "SMTRT" / "tva.md"
    text = path.read_text(encoding="utf-8").replace("FR123", "HUMAN-EDIT")
    path.write_text(text, encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    result = w.write_fact("SMTRT", "tva", "FR999")
    assert result.status == OUTCOME_PRESERVED
    assert result.detail == PROTECTED_DIVERGED
    assert path.read_text(encoding="utf-8") == before


def test_diverged_frontmatter_is_preserved(tmp_path):
    w = writer(tmp_path)
    w.write_fact("SMTRT", "tva", "FR123")
    path = tmp_path / FACTS_DIR / "SMTRT" / "tva.md"
    text = path.read_text(encoding="utf-8").replace("entity: SMTRT", "entity: EDITED")
    path.write_text(text, encoding="utf-8")
    result = w.write_fact("SMTRT", "tva", "FR999")
    assert result.status == OUTCOME_PRESERVED
    assert result.detail == PROTECTED_DIVERGED


def test_note_without_content_hash_is_preserved(tmp_path):
    w = writer(tmp_path)
    w.write_fact("SMTRT", "tva", "FR123")
    path = tmp_path / FACTS_DIR / "SMTRT" / "tva.md"
    text = path.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines(keepends=True) if not ln.startswith("content_hash:")]
    path.write_text("".join(lines), encoding="utf-8")
    result = w.write_fact("SMTRT", "tva", "FR999")
    assert result.status == OUTCOME_PRESERVED
    assert result.detail == PROTECTED_NO_HASH


def test_unmanaged_note_without_frontmatter_is_preserved(tmp_path):
    w = writer(tmp_path)
    path = tmp_path / FACTS_DIR / "SMTRT" / "tva.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Human note, no frontmatter\n", encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    result = w.write_fact("SMTRT", "tva", "FR999")
    assert result.status == OUTCOME_PRESERVED
    assert result.detail == PROTECTED_UNMANAGED
    assert path.read_text(encoding="utf-8") == before


def test_preserved_fact_stays_byte_identical_across_attempts(tmp_path):
    w = writer(tmp_path)
    w.write_fact("SMTRT", "tva", "FR123")
    path = tmp_path / FACTS_DIR / "SMTRT" / "tva.md"
    text = path.read_text(encoding="utf-8").replace("locked: false", "locked: true")
    path.write_text(text, encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    for _ in range(3):
        assert w.write_fact("SMTRT", "tva", "FR999").status == OUTCOME_PRESERVED
    assert path.read_text(encoding="utf-8") == before


# -- vault_writer: entities ---------------------------------------------------


def test_update_entity_creates_note_with_relations(tmp_path):
    result = writer(tmp_path).update_entity(
        "SMTRT", [("fournit", "EDF"), ("possede", "COMPTE_512")]
    )
    assert result.status == OUTCOME_CREATED
    assert result.note_type == "entity"
    body = body_of(tmp_path, "04-graph/entities/SMTRT.md")
    assert "[[SMTRT]]" in body
    assert "- `fournit` → [[EDF]]" in body
    assert "- `possede` → [[COMPTE_512]]" in body


def test_update_entity_never_duplicates_relations(tmp_path):
    w = writer(tmp_path)
    w.update_entity("SMTRT", [("fournit", "EDF")])
    w.update_entity("SMTRT", [("fournit", "EDF"), ("fournit", "EDF")])
    body = body_of(tmp_path, "04-graph/entities/SMTRT.md")
    assert body.count("- `fournit` → [[EDF]]") == 1


def test_update_entity_merges_and_keeps_first_seen_order(tmp_path):
    w = writer(tmp_path)
    w.update_entity("SMTRT", [("a", "X"), ("b", "Y")])
    result = w.update_entity("SMTRT", [("b", "Y"), ("c", "Z")])
    assert result.status == OUTCOME_UPDATED
    fm = frontmatter_of(tmp_path, "04-graph/entities/SMTRT.md")
    pairs = [(r["predicate"], r["target"]) for r in fm["relations"]]
    assert pairs == [("a", "X"), ("b", "Y"), ("c", "Z")]


def test_update_entity_is_idempotent(tmp_path):
    w = writer(tmp_path)
    w.update_entity("SMTRT", [("fournit", "EDF")])
    result = w.update_entity("SMTRT", [("fournit", "EDF")])
    assert result.status == OUTCOME_UNCHANGED


def test_update_entity_accepts_relation_models(tmp_path):
    result = writer(tmp_path).update_entity(
        "SMTRT", [Relation(predicate="fournit", target="EDF")]
    )
    assert result.status == OUTCOME_CREATED
    fm = frontmatter_of(tmp_path, "04-graph/entities/SMTRT.md")
    assert fm["relations"] == [{"predicate": "fournit", "target": "EDF"}]


def test_update_entity_without_relations_shows_placeholder(tmp_path):
    writer(tmp_path).update_entity("SMTRT")
    body = body_of(tmp_path, "04-graph/entities/SMTRT.md")
    assert "_(aucune relation)_" in body


def test_update_entity_summary_carries_over(tmp_path):
    w = writer(tmp_path)
    w.update_entity("SMTRT", [], summary="Fournisseur de peintures")
    w.update_entity("SMTRT", [("a", "X")])
    fm = frontmatter_of(tmp_path, "04-graph/entities/SMTRT.md")
    assert fm["summary"] == "Fournisseur de peintures"


def test_update_entity_summary_can_be_replaced(tmp_path):
    w = writer(tmp_path)
    w.update_entity("SMTRT", [], summary="Ancien")
    result = w.update_entity("SMTRT", [], summary="Nouveau")
    assert result.status == OUTCOME_UPDATED
    fm = frontmatter_of(tmp_path, "04-graph/entities/SMTRT.md")
    assert fm["summary"] == "Nouveau"


def test_update_entity_rejects_unsafe_entity(tmp_path):
    assert err(writer(tmp_path).update_entity, "../E", []).code == "INVALID_ENTITY"


def test_update_entity_rejects_unsafe_relation(tmp_path):
    assert err(
        writer(tmp_path).update_entity, "SMTRT", [("a/b", "X")]
    ).code == "INVALID_PREDICATE"
    assert err(
        writer(tmp_path).update_entity, "SMTRT", [("a", "x/y")]
    ).code == "INVALID_TARGET"
    assert err(writer(tmp_path).update_entity, "SMTRT", ["bad"]).code == (
        "INVALID_RELATION"
    )


def test_update_entity_human_locked_is_preserved(tmp_path):
    w = writer(tmp_path)
    w.update_entity("SMTRT", [("fournit", "EDF")])
    path = tmp_path / "04-graph/entities/SMTRT.md"
    text = path.read_text(encoding="utf-8").replace("locked: false", "locked: true")
    path.write_text(text, encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    result = w.update_entity("SMTRT", [("nouveau", "X")])
    assert result.status == OUTCOME_PRESERVED
    assert result.detail == PROTECTED_LOCKED
    assert path.read_text(encoding="utf-8") == before


def test_update_entity_diverged_is_preserved(tmp_path):
    w = writer(tmp_path)
    w.update_entity("SMTRT", [("fournit", "EDF")])
    path = tmp_path / "04-graph/entities/SMTRT.md"
    text = path.read_text(encoding="utf-8").replace("SMTRT", "HACKED", 1)
    path.write_text(text, encoding="utf-8")
    result = w.update_entity("SMTRT", [("nouveau", "X")])
    assert result.status == OUTCOME_PRESERVED
    assert result.detail == PROTECTED_DIVERGED


def test_update_entity_preserves_existing_relations_of_protected_note(tmp_path):
    w = writer(tmp_path)
    w.update_entity("SMTRT", [("fournit", "EDF")])
    path = tmp_path / "04-graph/entities/SMTRT.md"
    text = path.read_text(encoding="utf-8").replace("source: neo4j", "source: human")
    path.write_text(text, encoding="utf-8")
    result = w.update_entity("SMTRT", [("autre", "X")])
    assert result.status == OUTCOME_PRESERVED
    assert "autre" not in path.read_text(encoding="utf-8")


# -- vault_writer: decisions & active context ---------------------------------


def test_record_decision_creates_log_and_active_context(tmp_path):
    result = writer(tmp_path).record_decision(
        "P15", "Gel du contrat perception", next_action="Implémenter P15.2"
    )
    assert result.decisions.status == OUTCOME_CREATED
    assert result.active.status == OUTCOME_CREATED
    assert (tmp_path / "02-projets/P15/decisions.md").exists()
    assert (tmp_path / ACTIVE_CONTEXT_FILE).exists()


def test_decision_id_is_deterministic():
    first = derive_decision_id("P15", "texte")
    assert first == derive_decision_id("P15", "texte")
    assert first != derive_decision_id("P15", "autre")
    assert first != derive_decision_id("P16", "texte")
    assert first.startswith("D-")


def test_record_decision_id_is_hash_derived(tmp_path):
    result = writer(tmp_path).record_decision("P15", "Gel du contrat")
    assert result.decision_id == derive_decision_id("P15", "Gel du contrat")


def test_record_decision_is_idempotent(tmp_path):
    w = writer(tmp_path)
    w.record_decision("P15", "Gel du contrat")
    result = w.record_decision("P15", "Gel du contrat")
    assert result.decisions.status == OUTCOME_UNCHANGED
    body = body_of(tmp_path, "02-projets/P15/decisions.md")
    assert body.count("Gel du contrat") == 1


def test_record_decision_appends_to_log(tmp_path):
    w = writer(tmp_path)
    w.record_decision("P15", "Première décision")
    w.record_decision("P15", "Seconde décision")
    body = body_of(tmp_path, "02-projets/P15/decisions.md")
    assert "Première décision" in body
    assert "Seconde décision" in body


def test_record_decision_log_body_has_wikilinks(tmp_path):
    writer(tmp_path).record_decision("P15", "Gel du contrat")
    body = body_of(tmp_path, "02-projets/P15/decisions.md")
    assert "[[01-contexte/active|Contexte actif]]" in body


def test_record_decision_rejects_empty_text(tmp_path):
    assert err(writer(tmp_path).record_decision, "P15", "   ").code == "EMPTY_DECISION"


def test_record_decision_rejects_unsafe_project(tmp_path):
    assert err(writer(tmp_path).record_decision, "../P", "x").code == "INVALID_PROJECT"


def test_active_context_shows_project_and_next_action(tmp_path):
    writer(tmp_path).record_decision("P15", "Gel", next_action="P15.2")
    body = body_of(tmp_path, ACTIVE_CONTEXT_FILE)
    assert "[[02-projets/P15/decisions|P15]]" in body
    assert "P15.2" in body
    fm = frontmatter_of(tmp_path, ACTIVE_CONTEXT_FILE)
    assert fm["project"] == "P15"
    assert fm["next_action"] == "P15.2"


def test_active_context_keeps_last_five_decisions_newest_first(tmp_path):
    w = writer(tmp_path)
    for index in range(7):
        w.record_decision("P15", f"Décision {index}")
    body = body_of(tmp_path, ACTIVE_CONTEXT_FILE)
    assert "Décision 6" in body
    assert "Décision 2" in body
    assert "Décision 1" not in body
    assert "Décision 0" not in body
    assert body.index("Décision 6") < body.index("Décision 5")


def test_active_context_next_action_updated(tmp_path):
    w = writer(tmp_path)
    w.record_decision("P15", "Gel", next_action="Ancienne")
    w.record_decision("P15", "Suite", next_action="Nouvelle")
    fm = frontmatter_of(tmp_path, ACTIVE_CONTEXT_FILE)
    assert fm["next_action"] == "Nouvelle"


def test_active_context_next_action_carries_over(tmp_path):
    w = writer(tmp_path)
    w.record_decision("P15", "Gel", next_action="Persistante")
    w.record_decision("P15", "Suite")
    fm = frontmatter_of(tmp_path, ACTIVE_CONTEXT_FILE)
    assert fm["next_action"] == "Persistante"


def test_update_active_context_direct(tmp_path):
    w = writer(tmp_path)
    result = w.update_active_context("P15", next_action="Directe")
    assert result.status == OUTCOME_CREATED
    assert result.note_type == "context"
    fm = frontmatter_of(tmp_path, ACTIVE_CONTEXT_FILE)
    assert fm["next_action"] == "Directe"


def test_update_active_context_without_decisions_shows_placeholder(tmp_path):
    writer(tmp_path).update_active_context("P15")
    body = body_of(tmp_path, ACTIVE_CONTEXT_FILE)
    assert "_(aucune décision)_" in body


def test_active_context_is_idempotent(tmp_path):
    w = writer(tmp_path)
    w.record_decision("P15", "Gel", next_action="Action")
    result = w.record_decision("P15", "Gel", next_action="Action")
    assert result.active.status == OUTCOME_UNCHANGED


def test_active_context_human_locked_is_preserved(tmp_path):
    w = writer(tmp_path)
    w.record_decision("P15", "Gel")
    path = tmp_path / ACTIVE_CONTEXT_FILE
    text = path.read_text(encoding="utf-8").replace("locked: false", "locked: true")
    path.write_text(text, encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    result = w.record_decision("P15", "Nouvelle décision")
    assert result.active.status == OUTCOME_PRESERVED
    assert result.active.detail == PROTECTED_LOCKED
    assert path.read_text(encoding="utf-8") == before


def test_active_context_diverged_is_preserved(tmp_path):
    w = writer(tmp_path)
    w.record_decision("P15", "Gel")
    path = tmp_path / ACTIVE_CONTEXT_FILE
    text = path.read_text(encoding="utf-8").replace("Contexte actif", "MODIFIÉ")
    path.write_text(text, encoding="utf-8")
    result = w.record_decision("P15", "Nouvelle décision")
    assert result.active.status == OUTCOME_PRESERVED
    assert result.active.detail == PROTECTED_DIVERGED


def test_protected_decision_log_blocks_append(tmp_path):
    w = writer(tmp_path)
    w.record_decision("P15", "Gel")
    path = tmp_path / "02-projets/P15/decisions.md"
    text = path.read_text(encoding="utf-8").replace("locked: false", "locked: true")
    path.write_text(text, encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    result = w.record_decision("P15", "Nouvelle décision")
    assert result.decisions.status == OUTCOME_PRESERVED
    assert result.decisions.detail == PROTECTED_LOCKED
    assert path.read_text(encoding="utf-8") == before
    assert "Nouvelle décision" not in path.read_text(encoding="utf-8")


def test_human_decision_log_is_preserved(tmp_path):
    w = writer(tmp_path)
    w.record_decision("P15", "Gel")
    path = tmp_path / "02-projets/P15/decisions.md"
    text = path.read_text(encoding="utf-8").replace("source: agent", "source: human")
    path.write_text(text, encoding="utf-8")
    result = w.record_decision("P15", "Nouvelle décision")
    assert result.decisions.status == OUTCOME_PRESERVED
    assert result.decisions.detail == PROTECTED_HUMAN


def test_read_decisions_parses_entries(tmp_path):
    w = writer(tmp_path)
    w.record_decision("P15", "Première")
    w.record_decision("P15", "Seconde")
    entries = read_decisions(tmp_path, "P15")
    assert len(entries) == 2
    assert entries[0].excerpt == "Première"
    assert entries[1].excerpt == "Seconde"
    assert entries[0].decision_id.startswith("D-")


def test_read_decisions_missing_log_returns_empty(tmp_path):
    assert read_decisions(tmp_path, "INCONNU") == []


def test_read_decisions_extracts_excerpts(tmp_path):
    w = writer(tmp_path)
    w.record_decision("P15", "Résumé court")
    entry = read_decisions(tmp_path, "P15")[0]
    assert entry.excerpt == "Résumé court"


# -- vault_reader -------------------------------------------------------------


def test_reader_hot_layer_reads_active_context(tmp_path):
    w = writer(tmp_path)
    w.record_decision("P15", "Gel", next_action="P15.2")
    bundle = VaultReader(tmp_path).read_context("P15")
    assert "Gel" in bundle.hot
    assert "P15.2" in bundle.hot
    assert bundle.priority == ("hot", "warm", "rag")


def test_reader_warm_layer_reads_project_notes(tmp_path):
    w = writer(tmp_path)
    w.record_decision("P15", "Gel")
    (tmp_path / "02-projets/P15/specs.md").write_text(
        "---\nid: specs\n---\n\nContenu specs\n", encoding="utf-8"
    )
    bundle = VaultReader(tmp_path).read_context("P15")
    assert "Contenu specs" in bundle.warm
    assert "specs.md" in bundle.warm


def test_reader_warm_layer_ignores_other_projects(tmp_path):
    w = writer(tmp_path)
    w.record_decision("P15", "Gel")
    (tmp_path / "02-projets/AUTRE").mkdir(parents=True, exist_ok=True)
    (tmp_path / "02-projets/AUTRE/secret.md").write_text("SECRET", encoding="utf-8")
    bundle = VaultReader(tmp_path).read_context("P15")
    assert "SECRET" not in bundle.warm


def test_reader_respects_hot_token_budget(tmp_path):
    constraints = VaultConstraints(max_hot_context=50, max_warm_context=100)
    active = tmp_path / ACTIVE_CONTEXT_FILE
    active.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(active, "---\nid: x\n---\n\n" + "ligne chaude\n" * 500)
    bundle = VaultReader(tmp_path, constraints=constraints).read_context("P15")
    assert bundle.hot_tokens <= 50
    assert bundle.hot_truncated is True


def test_reader_respects_warm_token_budget(tmp_path):
    constraints = VaultConstraints(max_hot_context=50, max_warm_context=60)
    project = tmp_path / "02-projets/P15"
    project.mkdir(parents=True, exist_ok=True)
    (project / "big.md").write_text("x\n" * 2000, encoding="utf-8")
    bundle = VaultReader(tmp_path, constraints=constraints).read_context("P15")
    assert bundle.warm_tokens <= 60
    assert bundle.warm_truncated is True


def test_reader_no_truncation_under_budget(tmp_path):
    w = writer(tmp_path)
    w.record_decision("P15", "Gel")
    bundle = VaultReader(tmp_path).read_context("P15")
    assert bundle.hot_truncated is False
    assert bundle.warm_truncated is False
    assert bundle.hot_tokens == estimate_tokens(bundle.hot)
    assert bundle.warm_tokens == estimate_tokens(bundle.warm)


def test_reader_uses_schema_budgets(tmp_path):
    ensure_vault_tree(tmp_path)
    schema_path = tmp_path / SCHEMA_FILE
    text = schema_path.read_text(encoding="utf-8")
    schema_path.write_text(
        text.replace("max_hot_context: 2000", "max_hot_context: 25"), encoding="utf-8"
    )
    active = tmp_path / ACTIVE_CONTEXT_FILE
    atomic_write(active, "---\nid: x\n---\n\n" + "mot\n" * 200)
    bundle = VaultReader(tmp_path).read_context("P15")
    assert bundle.hot_tokens <= 25
    assert bundle.hot_truncated is True


def test_reader_rag_layer_uses_vector_client(tmp_path):
    client = MockVectorSearch(
        [SearchHit(chunk_id="c1", source="doc.md", heading="S1", text="extrait", score=0.9)]
    )
    bundle = VaultReader(tmp_path).read_context(
        "P15", query="tva", vector_client=client, rag_limit=3
    )
    assert client.calls == [("tva", 3)]
    assert len(bundle.rag) == 1
    assert bundle.rag[0].text == "extrait"
    assert bundle.rag[0].score == 0.9


def test_reader_rag_layer_accepts_dict_hits(tmp_path):
    client = MockVectorSearch(
        [{"chunk_id": "c1", "source": "d.md", "heading": "H", "text": "t", "score": 0.5}]
    )
    bundle = VaultReader(tmp_path).read_context("P15", query="q", vector_client=client)
    assert bundle.rag[0].chunk_id == "c1"


def test_reader_rag_layer_empty_without_client(tmp_path):
    bundle = VaultReader(tmp_path).read_context("P15", query="tva")
    assert bundle.rag == ()


def test_reader_rag_layer_empty_without_query(tmp_path):
    client = MockVectorSearch([SearchHit(chunk_id="c1")])
    bundle = VaultReader(tmp_path).read_context("P15", vector_client=client)
    assert bundle.rag == ()
    assert client.calls == []


def test_reader_render_orders_priority_sections(tmp_path):
    client = MockVectorSearch([SearchHit(chunk_id="c1", text="RAG")])
    w = writer(tmp_path)
    w.record_decision("P15", "Gel")
    bundle = VaultReader(tmp_path).read_context("P15", query="q", vector_client=client)
    rendered = bundle.render()
    assert rendered.index("## HOT") < rendered.index("## WARM") < rendered.index("## RAG")
    assert "RAG" in rendered


def test_reader_handles_missing_project_directory(tmp_path):
    bundle = VaultReader(tmp_path).read_context("INCONNU")
    assert bundle.warm == ""
    assert bundle.warm_tokens == 0


def test_reader_handles_missing_vault(tmp_path):
    bundle = VaultReader(tmp_path).read_context("P15")
    assert bundle.hot == ""
    assert bundle.warm == ""


def test_reader_rejects_unsafe_project(tmp_path):
    assert err(VaultReader(tmp_path).read_context, "../P").code == "INVALID_PROJECT"


def test_reader_is_read_only(tmp_path):
    w = writer(tmp_path)
    w.record_decision("P15", "Gel")
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    VaultReader(tmp_path).read_context("P15", query="x", vector_client=MockVectorSearch())
    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    assert before == after


def test_reader_read_note_returns_body_or_none(tmp_path):
    w = writer(tmp_path)
    w.write_fact("SMTRT", "tva", "FR123")
    reader = VaultReader(tmp_path)
    body = reader.read_note(f"{FACTS_DIR}/SMTRT/tva.md")
    assert body is not None and "[[SMTRT]]" in body
    assert reader.read_note(f"{FACTS_DIR}/SMTRT/inconnu.md") is None


# -- token helpers ------------------------------------------------------------


def test_estimate_tokens_scales_with_length():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 4) == 1
    assert estimate_tokens("a" * 5) == 2
    assert estimate_tokens("a" * 8) == 2
    assert estimate_tokens("a" * 8000) == 2000


def test_truncate_to_tokens_under_budget_untouched():
    text = "court\n"
    kept, truncated = truncate_to_tokens(text, 100)
    assert kept == text
    assert truncated is False


def test_truncate_to_tokens_respects_budget():
    text = "mot\n" * 5000
    kept, truncated = truncate_to_tokens(text, 2000)
    assert truncated is True
    assert estimate_tokens(kept) <= 2000


def test_truncate_to_tokens_cuts_on_line_boundary():
    text = "aaaa\n" * 10000
    kept, _ = truncate_to_tokens(text, 10)
    assert kept.endswith("aaaa")


def test_truncate_to_tokens_rejects_zero_budget():
    with pytest.raises(ValueError):
        truncate_to_tokens("x", 0)


# -- chunk_by_heading ---------------------------------------------------------


def test_chunk_empty_text_returns_no_chunk():
    assert chunk_by_heading("") == []
    assert chunk_by_heading("   \n\n  ") == []


def test_chunk_without_heading_is_preamble(tmp_path):
    chunks = chunk_by_heading("intro text\nmore\n")
    assert len(chunks) == 1
    assert chunks[0].heading == ""
    assert chunks[0].heading_level == 0
    assert chunks[0].index == 0


def test_chunk_splits_on_h2_and_h3():
    text = "intro\n\n## Section A\ncontent A\n### Sub B\ncontent B\n## Section C\ncontent C\n"
    chunks = chunk_by_heading(text)
    assert [c.heading for c in chunks] == ["", "Section A", "Sub B", "Section C"]
    assert [c.heading_level for c in chunks] == [0, 2, 3, 2]


def test_chunk_h4_is_not_a_split_point():
    text = "## A\n#### Deep\ncontent\n"
    chunks = chunk_by_heading(text)
    assert len(chunks) == 1
    assert chunks[0].heading == "A"


def test_chunk_indices_are_sequential():
    text = "## A\na\n## B\nb\n## C\nc\n"
    assert [c.index for c in chunk_by_heading(text)] == [0, 1, 2]


def test_chunk_sha256_matches_text():
    chunk = chunk_by_heading("## A\ncontenu\n")[0]
    assert chunk.sha256 == hash_text(chunk.text)


def test_chunk_respects_max_chars():
    text = "## A\n" + "x" * 5000
    chunks = chunk_by_heading(text, max_chars=2000, overlap=0.15)
    assert all(len(c.text) <= 2000 for c in chunks)
    assert len(chunks) >= 3


def test_chunk_overlap_applies_between_windows():
    text = "## A\n" + "abcdefghij" * 500
    chunks = chunk_by_heading(text, max_chars=100, overlap=0.15)
    assert len(chunks) > 1
    for previous, current in zip(chunks, chunks[1:]):
        overlap_chars = int(100 * 0.15)
        assert previous.text[-overlap_chars:] == current.text[:overlap_chars]


def test_chunk_long_section_without_heading_also_split():
    chunks = chunk_by_heading("y" * 4500, max_chars=2000, overlap=0.15)
    assert len(chunks) == 3
    assert all(len(c.text) <= 2000 for c in chunks)


def test_chunk_is_deterministic():
    text = "## A\n" + "z" * 3000 + "\n## B\nsuite\n"
    first = chunk_by_heading(text)
    second = chunk_by_heading(text)
    assert first == second


def test_chunk_default_params():
    text = "## A\n" + "x" * 3000
    chunks = chunk_by_heading(text)
    assert all(len(c.text) <= 2000 for c in chunks)
    assert len(chunks) == 2


def test_chunk_rejects_invalid_params():
    assert err(chunk_by_heading, "x", max_chars=0).code == "INVALID_CHUNK_PARAMS"
    assert err(chunk_by_heading, "x", max_chars=-5).code == "INVALID_CHUNK_PARAMS"
    assert err(chunk_by_heading, "x", overlap=-0.1).code == "INVALID_CHUNK_PARAMS"
    assert err(chunk_by_heading, "x", overlap=1.0).code == "INVALID_CHUNK_PARAMS"
    assert err(chunk_by_heading, "x", overlap=2).code == "INVALID_CHUNK_PARAMS"


def test_chunk_allows_zero_overlap():
    text = "## A\n" + "x" * 2995
    chunks = chunk_by_heading(text, max_chars=1000, overlap=0.0)
    assert len(chunks) == 3
    assert all(len(c.text) == 1000 for c in chunks)


def test_chunk_heading_kept_in_each_window():
    chunks = chunk_by_heading("## Titre\n" + "x" * 3000, max_chars=1000, overlap=0.0)
    assert all(c.heading == "Titre" for c in chunks)
    assert chunks[0].text.startswith("## Titre")


def test_chunk_id_is_deterministic_and_content_bound():
    chunk = chunk_by_heading("## A\ncontenu\n")[0]
    assert chunk_id("doc.md", chunk) == chunk_id("doc.md", chunk)
    other = Chunk.build(heading="A", heading_level=2, index=0, text="contenu\n")
    assert chunk_id("doc.md", chunk) != chunk_id("other.md", other)


# -- rag_sync with mock Qdrant ------------------------------------------------


def write_rag_doc(tmp_path, name="doc.md", text="## S1\ncontenu\n"):
    rag_dir = tmp_path / "03-rag"
    rag_dir.mkdir(parents=True, exist_ok=True)
    target = rag_dir / name
    target.write_text(text, encoding="utf-8")
    return target


def test_rag_sync_upserts_chunks(tmp_path):
    ensure_vault_tree(tmp_path)
    write_rag_doc(tmp_path, text="# T\n\n## S1\n" + "a" * 3000 + "\n## S2\nb\n")
    client = MockVectorStore()
    report = rag_sync(tmp_path, client=client)
    assert report.files_scanned == 1
    assert report.files_synced == 1
    assert report.files_skipped == 0
    assert report.chunks_upserted >= 3
    assert len(client.upserts) == 1
    assert client.upserts[0][0] == DEFAULT_COLLECTION
    assert len(client.upserted_points) == report.chunks_upserted


def test_rag_sync_point_payload_fields(tmp_path):
    ensure_vault_tree(tmp_path)
    write_rag_doc(tmp_path, text="## S1\ntexte\n")
    client = MockVectorStore()
    rag_sync(tmp_path, client=client)
    point = client.upserted_points[0]
    payload = point.payload
    assert payload["source"] == "doc.md"
    assert payload["heading"] == "S1"
    assert payload["heading_level"] == 2
    assert payload["index"] == 0
    assert payload["text"] == "## S1\ntexte\n"
    assert payload["sha256"] == hash_text("## S1\ntexte\n")
    assert len(point.vector) == 64


def test_rag_sync_is_idempotent_by_hash(tmp_path):
    ensure_vault_tree(tmp_path)
    write_rag_doc(tmp_path)
    client = MockVectorStore()
    rag_sync(tmp_path, client=client)
    report = rag_sync(tmp_path, client=client)
    assert report.files_synced == 0
    assert report.files_skipped == 1
    assert report.chunks_upserted == 0
    assert len(client.upserts) == 1  # only the first pass


def test_rag_sync_rereads_modified_file(tmp_path):
    ensure_vault_tree(tmp_path)
    target = write_rag_doc(tmp_path, text="## S1\nancien\n")
    client = MockVectorStore()
    rag_sync(tmp_path, client=client)
    target.write_text("## S1\nnouveau contenu\n", encoding="utf-8")
    report = rag_sync(tmp_path, client=client)
    assert report.files_synced == 1
    assert report.chunks_upserted == 1
    assert len(client.upserts) == 2


def test_rag_sync_deletes_stale_chunks_of_modified_file(tmp_path):
    ensure_vault_tree(tmp_path)
    target = write_rag_doc(tmp_path, text="## S1\n" + "a" * 3000)
    client = MockVectorStore()
    rag_sync(tmp_path, client=client)
    first_ids = [point.id for point in client.upserted_points]
    assert len(first_ids) == 2
    target.write_text("## S1\nseul\n", encoding="utf-8")
    rag_sync(tmp_path, client=client)
    assert len(client.deletes) == 1
    deleted = client.deleted_ids
    assert all(chunk in first_ids for chunk in deleted)
    assert len(deleted) == len(first_ids)


def test_rag_sync_removes_deleted_file_points(tmp_path):
    ensure_vault_tree(tmp_path)
    target = write_rag_doc(tmp_path, text="## S1\ncontenu\n")
    client = MockVectorStore()
    rag_sync(tmp_path, client=client)
    target.unlink()
    report = rag_sync(tmp_path, client=client)
    assert report.files_removed == 1
    assert report.files_scanned == 0
    assert len(client.deletes) == 1
    assert client.deleted_ids


def test_rag_sync_writes_manifest(tmp_path):
    ensure_vault_tree(tmp_path)
    write_rag_doc(tmp_path)
    rag_sync(tmp_path, client=MockVectorStore())
    manifest = tmp_path / RAG_MANIFEST_FILE
    assert manifest.exists()
    text = manifest.read_text(encoding="utf-8")
    assert "doc.md" in text
    assert "sha256" in text
    assert "chunk_ids" in text


def test_rag_sync_manifest_hash_matches_file(tmp_path):
    ensure_vault_tree(tmp_path)
    target = write_rag_doc(tmp_path)
    rag_sync(tmp_path, client=MockVectorStore())
    text = (tmp_path / RAG_MANIFEST_FILE).read_text(encoding="utf-8")
    assert hash_file(target) in text


def test_rag_sync_scans_subdirectories(tmp_path):
    ensure_vault_tree(tmp_path)
    nested = tmp_path / "03-rag" / "dossier"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "note.md").write_text("## S\ncontenu\n", encoding="utf-8")
    client = MockVectorStore()
    report = rag_sync(tmp_path, client=client)
    assert report.files_scanned == 1
    assert client.upserted_points[0].payload["source"] == "dossier/note.md"


def test_rag_sync_handles_empty_rag_directory(tmp_path):
    ensure_vault_tree(tmp_path)
    client = MockVectorStore()
    report = rag_sync(tmp_path, client=client)
    assert report.files_scanned == 0
    assert report.chunks_upserted == 0
    assert client.upserts == []


def test_rag_sync_accepts_custom_collection(tmp_path):
    ensure_vault_tree(tmp_path)
    write_rag_doc(tmp_path)
    client = MockVectorStore()
    rag_sync(tmp_path, client=client, collection="ma_collection")
    assert client.upserts[0][0] == "ma_collection"


def test_rag_sync_accepts_injected_embedder(tmp_path):
    ensure_vault_tree(tmp_path)
    write_rag_doc(tmp_path)
    calls = []

    def embedder(text):
        calls.append(text)
        return [0.5] * 8

    client = MockVectorStore()
    rag_sync(tmp_path, client=client, embedder=embedder)
    assert calls == ["## S1\ncontenu\n"]
    assert client.upserted_points[0].vector == [0.5] * 8


def test_rag_sync_custom_chunking_params(tmp_path):
    ensure_vault_tree(tmp_path)
    write_rag_doc(tmp_path, text="## S1\n" + "x" * 3000)
    client = MockVectorStore()
    report = rag_sync(tmp_path, client=client, max_chars=500, overlap=0.1)
    assert report.chunks_upserted > 3
    assert all(len(point.payload["text"]) <= 500 for point in client.upserted_points)


def test_rag_sync_rejects_missing_client(tmp_path):
    assert err(rag_sync, tmp_path, client=None).code == "MISSING_CLIENT"


def test_rag_sync_rejects_empty_collection(tmp_path):
    assert err(
        rag_sync, tmp_path, client=MockVectorStore(), collection="  "
    ).code == "INVALID_COLLECTION"


def test_rag_sync_respects_env_root(tmp_path, monkeypatch):
    monkeypatch.setenv(VAULT_ROOT_ENV, str(tmp_path / "vault_env"))
    ensure_vault_tree()
    write_rag_doc(tmp_path / "vault_env")
    client = MockVectorStore()
    report = rag_sync(client=client)
    assert report.files_synced == 1


def test_rag_sync_is_deterministic_on_chunk_ids(tmp_path):
    ensure_vault_tree(tmp_path)
    write_rag_doc(tmp_path, text="## S1\ncontenu\n")
    first = MockVectorStore()
    rag_sync(tmp_path, client=first)
    (tmp_path / RAG_MANIFEST_FILE).unlink()
    second = MockVectorStore()
    rag_sync(tmp_path, client=second)
    assert [p.id for p in first.upserted_points] == [p.id for p in second.upserted_points]
