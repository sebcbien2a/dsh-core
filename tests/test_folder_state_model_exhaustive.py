"""P14.1 - Exhaustive tests of the folder state model and tree service (offline)."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from schemas.folder_state_schema import (
    ALLOWED_TRANSITIONS,
    DocumentCategory,
    DocumentSource,
    DocumentStateRecord,
    DocumentStatus,
    FolderState,
    MissingFieldEnum,
    is_allowed_transition,
)
from services.folder_tree_service import (
    CATEGORISED_STATES,
    DEFAULT_FLOW_ROOT,
    FLOW_ROOT_ENV,
    REJET_MANUEL_DIR,
    FolderTree,
    FolderTreeError,
    ensure_contained,
    ensure_tree,
    resolve_flow_root,
    validate_tenant_id,
)

FROZEN_RAISES = ValidationError

SHA = "ab" * 32
NOW = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


def err(call, *args, **kwargs) -> FolderTreeError:
    with pytest.raises(FolderTreeError) as info:
        call(*args, **kwargs)
    return info.value


def record(**overrides) -> DocumentStateRecord:
    payload = {
        "sha256": SHA,
        "nom_fichier": "facture.pdf",
        "state": FolderState.A_COMPLETER,
        "category": DocumentCategory.DIVERS_A_CLASSER,
        "champs_manquants": [MissingFieldEnum.DATE_PIECE],
        "source": DocumentSource.GMAIL_ATTACHMENT,
        "horodatage": NOW,
    }
    payload.update(overrides)
    return DocumentStateRecord(**payload)


# -- enums --------------------------------------------------------------------


def test_folder_state_values_are_the_six_folder_names():
    assert [(s.name, s.value) for s in FolderState] == [
        ("INBOX", "00_INBOX"),
        ("EN_COURS", "01_EN_COURS"),
        ("A_COMPLETER", "02_A_COMPLETER"),
        ("VALIDE_PRET_FEC", "03_VALIDE_PRET_FEC"),
        ("EXPORT_COMPTA", "04_EXPORT_COMPTA"),
        ("ARCHIVE_ERREUR", "05_ARCHIVE_ERREUR"),
    ]


def test_folder_state_is_ordered_00_to_05():
    assert [s.value for s in FolderState] == sorted(s.value for s in FolderState)


def test_document_category_values():
    assert [c.value for c in DocumentCategory] == [
        "ACHATS_MARCHANDISES",
        "FRAIS_GENERAUX",
        "TRANSPORTS_LOGISTIQUE",
        "IMMOBILISATIONS",
        "BANQUE_FINANCE",
        "FISCAL_SOCIAL",
        "VENTES_CLIENTS",
        "DIVERS_A_CLASSER",
    ]


def test_missing_field_enum_is_the_closed_decision_2_list():
    assert [f.value for f in MissingFieldEnum] == [
        "date_piece",
        "numero_piece",
        "nom_fournisseur",
        "montant_ht",
        "montant_tva",
        "montant_ttc",
        "categorie",
        "taux_tva",
    ]


def test_document_status_values_include_rejet_e():
    assert sorted(s.value for s in DocumentStatus) == ["EN_ATTENTE", "REJETE", "VALIDE"]


def test_document_source_values():
    assert sorted(s.value for s in DocumentSource) == [
        "GMAIL_ATTACHMENT",
        "MANUAL",
        "ONEDRIVE_DROP",
    ]


# -- state machine ------------------------------------------------------------


def test_allowed_transitions_match_the_spec():
    pairs = {
        (src.value, dst.value)
        for src, targets in ALLOWED_TRANSITIONS.items()
        for dst in targets
    }
    assert pairs == {
        ("00_INBOX", "01_EN_COURS"),
        ("00_INBOX", "05_ARCHIVE_ERREUR"),
        ("01_EN_COURS", "03_VALIDE_PRET_FEC"),
        ("01_EN_COURS", "02_A_COMPLETER"),
        ("01_EN_COURS", "05_ARCHIVE_ERREUR"),
        ("02_A_COMPLETER", "03_VALIDE_PRET_FEC"),
        ("02_A_COMPLETER", "05_ARCHIVE_ERREUR"),
        ("03_VALIDE_PRET_FEC", "04_EXPORT_COMPTA"),
        ("03_VALIDE_PRET_FEC", "05_ARCHIVE_ERREUR"),
    }


def test_transition_matrix_is_complete():
    allowed = {
        (src.value, dst.value)
        for src, targets in ALLOWED_TRANSITIONS.items()
        for dst in targets
    }
    for src in FolderState:
        for dst in FolderState:
            assert is_allowed_transition(src, dst) is (
                (src.value, dst.value) in allowed
            )


def test_terminal_states_have_no_exit():
    assert ALLOWED_TRANSITIONS[FolderState.EXPORT_COMPTA] == frozenset()
    assert ALLOWED_TRANSITIONS[FolderState.ARCHIVE_ERREUR] == frozenset()


def test_transition_lookup_rejects_unknown_values():
    with pytest.raises(ValueError):
        is_allowed_transition("99_X", FolderState.INBOX)


# -- DocumentStateRecord ------------------------------------------------------


def test_record_defaults():
    rec = record()
    assert rec.statut is DocumentStatus.EN_ATTENTE
    assert rec.categorie_suggeree is DocumentCategory.DIVERS_A_CLASSER
    assert rec.champs_manquants == [MissingFieldEnum.DATE_PIECE]


def test_record_accepts_string_values():
    rec = DocumentStateRecord(
        sha256=SHA,
        nom_fichier="f.pdf",
        state="02_A_COMPLETER",
        category="ACHATS_MARCHANDISES",
        champs_manquants=["montant_ht", "montant_tva"],
        categorie_suggeree="BANQUE_FINANCE",
        statut="VALIDE",
        source="ONEDRIVE_DROP",
        horodatage=NOW,
    )
    assert rec.state is FolderState.A_COMPLETER
    assert rec.champs_manquants == [
        MissingFieldEnum.MONTANT_HT,
        MissingFieldEnum.MONTANT_TVA,
    ]
    assert rec.statut is DocumentStatus.VALIDE
    assert rec.source is DocumentSource.ONEDRIVE_DROP


def test_record_empty_champs_manquants_is_allowed():
    assert record(champs_manquants=[]).champs_manquants == []


def test_sha256_is_normalised_to_lowercase_hex():
    assert record(sha256="AB" * 32).sha256 == SHA


@pytest.mark.parametrize(
    "bad", ["", "a", "g" * 64, "ab" * 31, "ab" * 33, "ab" * 32 + "z", " " * 64]
)
def test_sha256_rejects_bad_shape(bad):
    with pytest.raises(ValidationError):
        record(sha256=bad)


@pytest.mark.parametrize("bad", ["", "   ", "a/b.pdf", "a\\b.pdf", "a\x00b.pdf"])
def test_nom_fichier_rejects_blank_or_path(bad):
    with pytest.raises(ValidationError):
        record(nom_fichier=bad)


def test_nom_fichier_is_stripped():
    assert record(nom_fichier="  facture.pdf  ").nom_fichier == "facture.pdf"


def test_champs_manquants_dedupe_preserving_order():
    rec = record(
        champs_manquants=[
            MissingFieldEnum.MONTANT_TTC,
            MissingFieldEnum.DATE_PIECE,
            MissingFieldEnum.MONTANT_TTC,
            MissingFieldEnum.DATE_PIECE,
        ]
    )
    assert rec.champs_manquants == [
        MissingFieldEnum.MONTANT_TTC,
        MissingFieldEnum.DATE_PIECE,
    ]


def test_champs_manquants_rejects_unknown_value():
    with pytest.raises(ValidationError):
        record(champs_manquants=["date"])


@pytest.mark.parametrize(
    "field, value",
    [("state", "99_X"), ("category", "AUTRE"), ("statut", "OUI"), ("source", "FAX")],
)
def test_enum_fields_reject_unknown_values(field, value):
    with pytest.raises(ValidationError):
        record(**{field: value})


@pytest.mark.parametrize(
    "field",
    ["sha256", "nom_fichier", "state", "category", "source", "horodatage"],
)
def test_required_fields_may_not_be_omitted(field):
    payload = {
        "sha256": SHA,
        "nom_fichier": "f.pdf",
        "state": FolderState.INBOX,
        "category": DocumentCategory.DIVERS_A_CLASSER,
        "source": DocumentSource.MANUAL,
        "horodatage": NOW,
    }
    del payload[field]
    with pytest.raises(ValidationError):
        DocumentStateRecord(**payload)


def test_record_rejects_extra_fields():
    with pytest.raises(ValidationError):
        record(inconnu="x")


def test_record_round_trips_through_json_dump():
    rec = record()
    assert DocumentStateRecord.model_validate(rec.model_dump(mode="json")) == rec


def test_record_is_frozen():
    rec = record()
    with pytest.raises(FROZEN_RAISES):
        rec.nom_fichier = "autre.pdf"


# -- root resolution ----------------------------------------------------------


def test_default_flow_root_is_workspace_document_flow():
    assert DEFAULT_FLOW_ROOT == Path("workspace") / "document_flow"


def test_explicit_root_wins_over_env(monkeypatch, tmp_path):
    monkeypatch.setenv(FLOW_ROOT_ENV, str(tmp_path / "env"))
    assert resolve_flow_root(tmp_path / "explicit") == tmp_path / "explicit"


def test_env_root_is_used_without_explicit_root(monkeypatch, tmp_path):
    monkeypatch.setenv(FLOW_ROOT_ENV, str(tmp_path / "env"))
    assert resolve_flow_root() == tmp_path / "env"


def test_blank_env_root_falls_back_to_default(monkeypatch):
    monkeypatch.setenv(FLOW_ROOT_ENV, "   ")
    assert resolve_flow_root() == DEFAULT_FLOW_ROOT


def test_missing_env_root_falls_back_to_default(monkeypatch):
    monkeypatch.delenv(FLOW_ROOT_ENV, raising=False)
    assert resolve_flow_root() == DEFAULT_FLOW_ROOT


def test_root_rejects_empty_and_nul():
    assert err(resolve_flow_root, "").code == "INVALID_ROOT"
    assert err(resolve_flow_root, "a\x00b").code == "INVALID_ROOT"


# -- tenant validation --------------------------------------------------------


@pytest.mark.parametrize(
    "tenant", ["CLIENT_01", "tenant-1", "a.b", "a:b", "0", "x" * 64]
)
def test_valid_tenants_are_accepted(tenant):
    assert validate_tenant_id(tenant) == tenant


@pytest.mark.parametrize(
    "tenant",
    ["", " ", ".", "..", "...", "a/b", "a\\b", "a b", "x" * 65, "a\x00b", None, 123],
)
def test_invalid_tenants_are_rejected(tenant):
    assert err(validate_tenant_id, tenant).code == "INVALID_TENANT_ID"


# -- containment --------------------------------------------------------------


def test_ensure_contained_accepts_inside_paths(tmp_path):
    inside = tmp_path / "a" / "b"
    assert ensure_contained(tmp_path, inside) == inside.resolve()


def test_ensure_contained_accepts_the_base_itself(tmp_path):
    assert ensure_contained(tmp_path, tmp_path) == tmp_path.resolve()


def test_ensure_contained_rejects_escape_attempts(tmp_path):
    for candidate in (tmp_path / ".." / "elsewhere", tmp_path.parent / "x"):
        assert err(ensure_contained, tmp_path, candidate).code == "PATH_TRAVERSAL"


# -- ensure_tree --------------------------------------------------------------


def test_ensure_tree_creates_the_six_state_dirs(tmp_path):
    tree = ensure_tree("CLIENT_01", tmp_path)
    assert isinstance(tree, FolderTree)
    base = (tmp_path / "CLIENT_01").resolve()
    assert tree.root == tmp_path.resolve()
    assert tree.tenant_id == "CLIENT_01"
    assert set(tree.state_dirs) == set(FolderState)
    for state in FolderState:
        path = tree.state_dir(state)
        assert path == base / state.value
        assert path.is_dir()


def test_state_dir_names_on_disk(tmp_path):
    ensure_tree("T", tmp_path)
    assert sorted(p.name for p in (tmp_path / "T").iterdir()) == sorted(
        s.value for s in FolderState
    )


def test_ensure_tree_creates_eight_categories_under_03_and_04(tmp_path):
    tree = ensure_tree("T", tmp_path)
    for state in (FolderState.VALIDE_PRET_FEC, FolderState.EXPORT_COMPTA):
        for category in DocumentCategory:
            assert tree.category_dir(state, category).is_dir()


def test_ensure_tree_creates_no_category_dirs_outside_03_and_04(tmp_path):
    tree = ensure_tree("T", tmp_path)
    for state in (
        FolderState.INBOX,
        FolderState.EN_COURS,
        FolderState.A_COMPLETER,
        FolderState.ARCHIVE_ERREUR,
    ):
        for category in DocumentCategory:
            assert not (tree.state_dir(state) / category.value).exists()


def test_ensure_tree_creates_rejet_manuel_under_05(tmp_path):
    tree = ensure_tree("T", tmp_path)
    assert tree.rejet_manuel_dir() == (
        tree.state_dir(FolderState.ARCHIVE_ERREUR) / REJET_MANUEL_DIR
    )
    assert tree.rejet_manuel_dir().is_dir()


def test_ensure_tree_is_idempotent(tmp_path):
    first = ensure_tree("T", tmp_path)
    second = ensure_tree("T", tmp_path)
    assert first.state_dirs == second.state_dirs


def test_ensure_tree_isolates_tenants(tmp_path):
    tree_a = ensure_tree("TENANT_A", tmp_path)
    marker = tree_a.state_dir(FolderState.INBOX) / "doc.pdf"
    marker.write_text("x", encoding="utf-8")
    tree_b = ensure_tree("TENANT_B", tmp_path)
    assert tree_a.state_dirs[FolderState.INBOX] != tree_b.state_dirs[FolderState.INBOX]
    assert marker.exists()
    assert list((tmp_path / "TENANT_B").rglob("doc.pdf")) == []


def test_ensure_tree_uses_env_root(monkeypatch, tmp_path):
    env_root = tmp_path / "OneDrive" / "flow"
    monkeypatch.setenv(FLOW_ROOT_ENV, str(env_root))
    tree = ensure_tree("T")
    assert tree.root == env_root.resolve()
    assert (env_root / "T" / "00_INBOX").is_dir()


def test_ensure_tree_accepts_relative_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tree = ensure_tree("T", "rel_root")
    assert tree.root == (tmp_path / "rel_root").resolve()
    assert (tmp_path / "rel_root" / "T" / "00_INBOX").is_dir()


def test_ensure_tree_rejects_file_blocking_a_state_dir(tmp_path):
    (tmp_path / "T").mkdir(parents=True)
    (tmp_path / "T" / "01_EN_COURS").write_text("bloque", encoding="utf-8")
    assert err(ensure_tree, "T", tmp_path).code == "NOT_A_DIRECTORY"


def test_ensure_tree_rejects_file_blocking_a_category_dir(tmp_path):
    target = tmp_path / "T" / "03_VALIDE_PRET_FEC" / "ACHATS_MARCHANDISES"
    target.mkdir(parents=True)
    target.rmdir()
    target.write_text("bloque", encoding="utf-8")
    assert err(ensure_tree, "T", tmp_path).code == "NOT_A_DIRECTORY"


def test_ensure_tree_rejects_invalid_tenant_without_side_effects(tmp_path):
    assert err(ensure_tree, "..", tmp_path).code == "INVALID_TENANT_ID"
    assert list(tmp_path.iterdir()) == []


# -- FolderTree accessors -----------------------------------------------------


def test_categorised_states_constant():
    assert CATEGORISED_STATES == frozenset(
        {FolderState.VALIDE_PRET_FEC, FolderState.EXPORT_COMPTA}
    )


@pytest.mark.parametrize(
    "state",
    [
        FolderState.INBOX,
        FolderState.EN_COURS,
        FolderState.A_COMPLETER,
        FolderState.ARCHIVE_ERREUR,
    ],
)
def test_category_dir_rejects_non_categorised_states(tmp_path, state):
    tree = ensure_tree("T", tmp_path)
    assert (
        err(tree.category_dir, state, DocumentCategory.DIVERS_A_CLASSER).code
        == "INVALID_CATEGORY_STATE"
    )


def test_category_dir_rejects_unknown_values(tmp_path):
    tree = ensure_tree("T", tmp_path)
    with pytest.raises(ValueError):
        tree.category_dir("99_X", DocumentCategory.DIVERS_A_CLASSER)
    with pytest.raises(ValueError):
        tree.category_dir(FolderState.VALIDE_PRET_FEC, "AUTRE")


def test_state_dir_rejects_unknown_state(tmp_path):
    tree = ensure_tree("T", tmp_path)
    with pytest.raises(ValueError):
        tree.state_dir("99_X")


def test_folder_tree_is_frozen(tmp_path):
    tree = ensure_tree("T", tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        tree.tenant_id = "AUTRE"
