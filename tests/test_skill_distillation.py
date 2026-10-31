"""Tests for teacher anchor filtering and the template skill store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from schemas.document_perception import (
    BBox,
    Block,
    DocumentPerception,
    PagePerception,
    TaskSpec,
    VisualAnnotation,
)
from schemas.profile_schema import DocumentProfile
from schemas.supplier_identity_schema import SupplierLayoutRule
from services.document_engine import CloudDocumentEngine, LocalDocumentEngine
from services.template_skill_store import (
    TemplateAnchor,
    TemplateSkill,
    TemplateSkillApplier,
    TemplateSkillStore,
    filter_teacher_anchors,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "workspace" / "skills"
CORPUS = REPO_ROOT / "workspace" / "invoices" / "input"


def _profile() -> DocumentProfile:
    return DocumentProfile(
        extension=".pdf",
        has_text_layer=True,
        word_count=100,
        has_tables=False,
        has_images=False,
        is_scanned=False,
        profile_type="native_pdf",
        page_count=1,
    )


def _block(
    block_id: str,
    text: str,
    bbox: BBox,
    page_index: int = 0,
    reading_order: int = 0,
) -> Block:
    return Block(
        block_id=block_id,
        kind="text",
        page_index=page_index,
        bbox=bbox,
        text=text,
        reading_order=reading_order,
    )


def _perception(
    *,
    blocks: list[Block] | None = None,
    annotations: list[VisualAnnotation] | None = None,
    raw_text: str | None = None,
    width: float = 600.0,
    height: float = 800.0,
) -> DocumentPerception:
    blocks = blocks or []
    page = PagePerception(
        page_index=0,
        width=width,
        height=height,
        blocks=blocks,
        annotations=annotations or [],
    )
    text = raw_text if raw_text is not None else "\n".join(
        block.text for block in blocks if block.text
    )
    return DocumentPerception(
        source_name="template.pdf",
        source_sha256="a" * 64,
        profile=_profile(),
        pages=[page],
        raw_text=text,
        tools_used=["pymupdf_text"],
    )


def _annotation(
    kind: str,
    target: str | None,
    bbox: BBox,
    confidence: float = 0.9,
    page_index: int = 0,
    anchor_block_id: str | None = None,
) -> VisualAnnotation:
    return VisualAnnotation(
        kind=kind,
        page_index=page_index,
        bbox=bbox,
        confidence=confidence,
        anchor_block_id=anchor_block_id,
        raw_text_target=target,
    )


def _skill(
    name: str = "invoice_test",
    *,
    supplier_id: str | None = "SUP_0002",
    anchors: list[TemplateAnchor] | None = None,
    labels: list[str] | None = None,
) -> TemplateSkill:
    return TemplateSkill(
        skill_name=name,
        supplier_id=supplier_id,
        lifecycle={"confidence": 0.9, "usage_count": 0, "status": "candidate"},
        extraction_rules={
            "regex_numero_facture": r"\b(AB/53)\b",
            "trusted_labels": {"ttc": "NET A PAYER"},
        },
        layout_anchors=anchors or [],
        visual_signature={
            "label_fingerprint": labels or [],
            "aspect_ratio": 0.75,
            "page_count": 1,
        },
        created_from={"documents": ["test.pdf"], "teacher_model": "test"},
    )


def _store(tmp_path: Path, repository=None) -> TemplateSkillStore:
    return TemplateSkillStore(tmp_path / "skills", repository=repository)


def test_filter_drops_annotations_without_target():
    annotations = [
        _annotation(
            "ink",
            None,
            BBox(x0=i, y0=0, x1=i + 1, y1=1),
        )
        for i in range(100)
    ]
    anchors, audit = filter_teacher_anchors(
        _perception(annotations=annotations)
    )

    assert anchors == []
    assert audit["raw_annotations_seen"] == 100
    assert audit["dropped_no_target"] == 100


def test_filter_keeps_qualifying_shapes():
    annotations = [
        _annotation("circle", "TOTAL H.T.", BBox(x0=0, y0=0, x1=10, y1=10)),
        _annotation("square", "MT TVA", BBox(x0=20, y0=0, x1=30, y1=10)),
        _annotation(
            "highlight", "NET A PAYER", BBox(x0=40, y0=0, x1=50, y1=10)
        ),
    ]
    anchors, audit = filter_teacher_anchors(
        _perception(annotations=annotations)
    )

    assert len(anchors) == 3
    assert audit["kept"] == 3


def test_filter_drops_ink_and_unknown_kinds():
    annotations = [
        _annotation("ink", "ink target", BBox(x0=0, y0=0, x1=10, y1=10)),
        _annotation(
            "unknown", "unknown target", BBox(x0=20, y0=0, x1=30, y1=10)
        ),
    ]
    anchors, audit = filter_teacher_anchors(
        _perception(annotations=annotations)
    )

    assert anchors == []
    assert audit["dropped_kind"] == 2


def test_filter_drops_low_confidence():
    annotations = [
        _annotation(
            "circle",
            "TOTAL H.T.",
            BBox(x0=0, y0=0, x1=10, y1=10),
            confidence=0.3,
        )
    ]
    anchors, audit = filter_teacher_anchors(
        _perception(annotations=annotations)
    )

    assert anchors == []
    assert audit["dropped_confidence"] == 1


def test_filter_dedupes_overlapping_anchors():
    bbox = BBox(x0=0, y0=0, x1=100, y1=20)
    annotations = [
        _annotation("circle", "TOTAL H.T.", bbox, confidence=0.6),
        _annotation("circle", "TOTAL H.T.", bbox, confidence=0.9),
    ]
    anchors, audit = filter_teacher_anchors(
        _perception(annotations=annotations)
    )

    assert len(anchors) == 1
    assert audit["deduped"] == 1
    assert anchors[0]["confidence"] == pytest.approx(0.9)


def test_filter_caps_anchor_count():
    annotations = [
        _annotation(
            "square",
            f"label {index}",
            BBox(x0=index * 12, y0=0, x1=index * 12 + 10, y1=10),
        )
        for index in range(20)
    ]
    anchors, audit = filter_teacher_anchors(
        _perception(annotations=annotations)
    )

    assert len(anchors) == 12
    assert audit["kept"] == 12


def test_filter_maps_field_keys_from_lexicon():
    labels = [
        ("TOTAL H.T.", "ht"),
        ("MT TVA", "tva"),
        ("NET A PAYER", "ttc"),
        ("Facture N", "numero_facture"),
        ("Fournisseur", "fournisseur"),
    ]
    annotations = [
        _annotation(
            "square",
            label,
            BBox(x0=index * 12, y0=0, x1=index * 12 + 10, y1=10),
        )
        for index, (label, _) in enumerate(labels)
    ]
    anchors, _ = filter_teacher_anchors(
        _perception(annotations=annotations)
    )

    assert [anchor["field_key"] for anchor in anchors] == [
        expected for _, expected in labels
    ]


def test_teacher_distill_uses_filtered_anchors():
    perception = _perception(
        annotations=[
            _annotation(
                "square", "TOTAL H.T.", BBox(x0=0, y0=0, x1=10, y1=10)
            ),
            _annotation(
                "ink", "noise", BBox(x0=20, y0=0, x1=30, y1=10)
            ),
        ]
    )
    engine = CloudDocumentEngine(model="google/gemini-3.8-flash")

    skill = engine.teacher_distill(perception, supplier_id="SUP_0002")

    assert skill["layout_anchors"]
    assert len(skill["layout_anchors"]) == 1
    assert skill["created_from"]["raw_annotations_seen"] == 2
    assert skill["created_from"]["filtered_annotations"] == 1
    assert "visual_signature" in skill


def test_template_skill_save_load_round_trip(tmp_path):
    store = _store(tmp_path)
    skill = _skill(
        anchors=[
            TemplateAnchor(
                field_key="ht",
                anchor_text="TOTAL H.T.",
                relative_position="right_of",
                confidence=0.9,
            )
        ]
    )

    path = store.save(skill)
    loaded = store.load(skill.skill_name)

    assert path.is_file()
    assert loaded == skill


def test_find_by_supplier_uses_repository_aliases(tmp_path):
    supplier_path = tmp_path / "suppliers.json"
    supplier_path.write_text(
        json.dumps(
            {
                "metadata": {"version": "2.0", "next_supplier_id": 100},
                "suppliers": {
                    "12345678900012": {
                        "supplier_id": "SUP_0099",
                        "raison_sociale": "ACME SAS",
                        "siret": "12345678900012",
                        "tva_intra": "FR12345678901",
                        "aliases": ["ACME"],
                        "status": "ACTIVE",
                        "skills": [],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    from services.supplier_repository import SupplierRepository

    repository = SupplierRepository(str(supplier_path))
    store = _store(tmp_path, repository=repository)
    skill = _skill(supplier_id="SUP_0099")
    store.save(skill)

    assert store.find_by_supplier(supplier_id="SUP_0099") == skill
    assert store.find_by_supplier(name="ACME") == skill
    assert store.find_by_supplier(siret="12345678900012") == skill


def test_find_by_signature_matches_page_labels(tmp_path):
    store = _store(tmp_path)
    skill = _skill(labels=["TOTAL H.T.", "MT TVA", "NET A PAYER"])
    store.save(skill)
    perception = _perception(
        blocks=[
            _block(
                "b1",
                "TOTAL H.T.\nMT TVA\nNET A PAYER",
                BBox(x0=0, y0=0, x1=200, y1=60),
            )
        ]
    )

    assert store.find_by_signature(perception) == skill


def test_inject_supplier_layout_rules_is_candidate_only(tmp_path):
    store = _store(tmp_path)
    skill = _skill(
        anchors=[
            TemplateAnchor(
                field_key="ht",
                anchor_text="TOTAL H.T.",
                relative_position="right_of",
                confidence=0.9,
            )
        ]
    )

    rules = store.inject_supplier_layout_rules(skill)

    assert len(rules) == 1
    assert isinstance(rules[0], SupplierLayoutRule)
    assert rules[0].active is False
    assert rules[0].human_confirmed is False


def test_applier_extracts_right_of_values(tmp_path):
    blocks = [
        _block("l-ht", "TOTAL H.T.", BBox(x0=10, y0=10, x1=60, y1=20), reading_order=0),
        _block("v-ht", "100,00", BBox(x0=70, y0=10, x1=120, y1=20), reading_order=1),
        _block("l-tva", "MT TVA", BBox(x0=10, y0=30, x1=60, y1=40), reading_order=2),
        _block("v-tva", "20,00", BBox(x0=70, y0=30, x1=120, y1=40), reading_order=3),
        _block("l-ttc", "NET A PAYER", BBox(x0=10, y0=50, x1=60, y1=60), reading_order=4),
        _block("v-ttc", "120,00", BBox(x0=70, y0=50, x1=120, y1=60), reading_order=5),
    ]
    anchors = [
        TemplateAnchor(field_key="ht", anchor_text="TOTAL H.T.", relative_position="right_of", confidence=0.9),
        TemplateAnchor(field_key="tva", anchor_text="MT TVA", relative_position="right_of", confidence=0.9),
        TemplateAnchor(field_key="ttc", anchor_text="NET A PAYER", relative_position="right_of", confidence=0.9),
    ]
    store = _store(tmp_path)
    skill = _skill(anchors=anchors, supplier_id=None).model_copy(
        update={"extraction_rules": {}}
    )
    perception = _perception(blocks=blocks)

    result = TemplateSkillApplier(store).extract(perception, skill)

    assert result["fields"] == {"ht": "100,00", "tva": "20,00", "ttc": "120,00"}
    assert result["coverage"] == 1.0
    assert result["source"] == "template_skill"


def test_local_engine_structure_bypasses_llm_with_template(tmp_path):
    blocks = [
        _block("l-ht", "TOTAL H.T.", BBox(x0=0, y0=0, x1=50, y1=10)),
        _block("v-ht", "100,00", BBox(x0=60, y0=0, x1=100, y1=10)),
        _block("l-tva", "MT TVA", BBox(x0=0, y0=20, x1=50, y1=30)),
        _block("v-tva", "20,00", BBox(x0=60, y0=20, x1=100, y1=30)),
        _block("l-ttc", "NET A PAYER", BBox(x0=0, y0=40, x1=50, y1=50)),
        _block("v-ttc", "120,00", BBox(x0=60, y0=40, x1=100, y1=50)),
    ]
    anchors = [
        TemplateAnchor(field_key="ht", anchor_text="TOTAL H.T.", relative_position="right_of", confidence=0.9),
        TemplateAnchor(field_key="tva", anchor_text="MT TVA", relative_position="right_of", confidence=0.9),
        TemplateAnchor(field_key="ttc", anchor_text="NET A PAYER", relative_position="right_of", confidence=0.9),
    ]
    store = _store(tmp_path)
    store.save(
        _skill(
            anchors=anchors,
            labels=["TOTAL H.T.", "MT TVA", "NET A PAYER"],
        )
    )
    calls: list[tuple[str, TaskSpec]] = []

    def structured(text: str, task: TaskSpec) -> dict[str, object]:
        calls.append((text, task))
        return {"fallback": True}

    engine = LocalDocumentEngine(skill_store=store, structured=structured)
    perception = _perception(blocks=blocks)
    task = TaskSpec(task_id="t1", kind="text", tool="pymupdf_text")

    result = engine.structure(
        perception.raw_text, task, perception=perception
    )
    fallback = engine.structure(
        "unknown document",
        task,
        perception=_perception(blocks=[_block("x", "unknown", BBox(x0=0, y0=0, x1=10, y1=10))]),
    )

    assert calls == [("unknown document", task)]
    assert result["source"] == "template_skill"
    assert result["fields"]["ttc"] == "120,00"
    assert fallback == {"fallback": True}


def test_real_case_skills_apply_without_llm():
    store = TemplateSkillStore()
    applier = TemplateSkillApplier(store)
    smtrt = store.load("invoice_smtrt_sas")
    edf = store.load("invoice_edf")
    crisol = store.load("invoice_crisol_color")

    assert smtrt is not None and smtrt.version == "2.0.0"
    assert edf is not None and edf.layout_anchors
    assert crisol is not None and crisol.layout_anchors

    control = CORPUS / "facture_paintcar.pdf"
    if control.is_file():
        perception = LocalDocumentEngine().perceive(control)
        result = applier.extract(perception, crisol)
        assert result["coverage"] >= 0.6
        assert result["fields"]["ttc"] == "-83,43"
        assert result["fields"]["numero_facture"] == "AB/53"
        assert result["source"] == "template_skill"

    edf_control = CORPUS / "ABONNEMENT.pdf"
    if edf_control.is_file():
        perception = LocalDocumentEngine().perceive(edf_control)
        result = applier.extract(perception, edf)
        assert result["fields"]["fournisseur"] == "EDF"
        assert "TTC" in result["fields"]["ttc"].upper()

    edf_number_doc = CORPUS / "Votre document edf.pdf"
    if edf_number_doc.is_file():
        perception = LocalDocumentEngine().perceive(edf_number_doc)
        result = applier.extract(perception, edf)
        assert result["fields"]["numero_facture"] == "6 032 506 183"

    smtrt_docs = [
        path for path in CORPUS.glob("*") if "SMTRT" in path.name.upper()
    ]
    if smtrt_docs:
        perception = LocalDocumentEngine().perceive(smtrt_docs[0])
        result = applier.extract(perception, smtrt)
        assert result["source"] == "template_skill"
