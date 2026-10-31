"""Template Skill Store and deterministic template extraction (Phase 7).

The store persists candidate skills produced by teacher distillation. Candidate
layout rules are never activated automatically: ``SupplierLayoutRule`` instances
are always created with ``active=False`` and ``human_confirmed=False``.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemas.document_perception import BBox, Block, DocumentPerception
from schemas.supplier_identity_schema import SupplierLayoutRule
from services.supplier_repository import SupplierRepository

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SKILLS_ROOT = REPO_ROOT / "workspace" / "skills"
TEACHER_MODEL_DEFAULT = "MiMo-V2.6-Pro"
ALLOWED_ANCHOR_KINDS = frozenset(
    {"circle", "ellipse", "square", "highlight", "strike", "arrow"}
)
MAX_LAYOUT_ANCHORS = 12


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TemplateAnchor(_StrictModel):
    """Qualified label anchor extracted from visual annotations."""

    field_key: Literal[
        "ht", "tva", "ttc", "numero_facture", "fournisseur", "date", "autre"
    ]
    anchor_text: str
    relative_position: Literal[
        "right_of", "below", "left_of", "above", "inside"
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    page_index: int = Field(default=0, ge=0)


class TemplateSkill(BaseModel):
    """Persisted candidate template skill."""

    model_config = ConfigDict(extra="allow")

    skill_name: str
    supplier_id: str | None = None
    domain: Literal["invoice_processing"] = "invoice_processing"
    version: str = "1.0.0"
    tenant: str = "GLOBAL"
    lifecycle: dict[str, Any] = Field(default_factory=dict)
    extraction_rules: dict[str, Any] = Field(default_factory=dict)
    layout_anchors: list[TemplateAnchor] = Field(default_factory=list)
    visual_signature: dict[str, Any] = Field(default_factory=dict)
    created_from: dict[str, Any] = Field(default_factory=dict)
    overrides: dict[str, Any] = Field(default_factory=dict)

    @field_validator("layout_anchors")
    @classmethod
    def limit_anchors(
        cls, anchors: list[TemplateAnchor]
    ) -> list[TemplateAnchor]:
        if len(anchors) > MAX_LAYOUT_ANCHORS:
            raise ValueError("a template skill accepts at most 12 layout anchors")
        return anchors


def normalise_label(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _field_key_for_label(label: str) -> str:
    text = normalise_label(label)
    if re.search(
        r"\btotal\s*h\s*t\b|\bmontant\s*h\s*t\b|\bnet\s*h\s*t\b|"
        r"base\s*imponible",
        text,
    ):
        return "ht"
    if re.search(r"\btva\b|\bmt\s*tva\b|total\s*t\s*v\s*a", text):
        return "tva"
    if re.search(r"\bttc\b|net\s*a\s*payer|total\s*ttc", text):
        return "ttc"
    if re.search(
        r"facture\s*n|factura\s*n|\bn\s*facture|numero\s*facture|"
        r"\bn\s*°|invoice",
        text,
    ):
        return "numero_facture"
    if re.search(r"fournisseur|vendeur|supplier|edf|crisol", text):
        return "fournisseur"
    if re.search(r"\bdate\b|fecha", text):
        return "date"
    return "autre"


def _relative_position(
    annotation: Any, page_blocks: list[Block]
) -> Literal["right_of", "below", "left_of", "above", "inside"]:
    bbox = getattr(annotation, "bbox", None)
    anchor_id = getattr(annotation, "anchor_block_id", None)
    if bbox is None or not anchor_id:
        return "right_of"
    anchor = next(
        (block for block in page_blocks if block.block_id == anchor_id), None
    )
    if anchor is None:
        return "right_of"
    block_bbox = anchor.bbox
    center_x = (bbox.x0 + bbox.x1) / 2.0
    center_y = (bbox.y0 + bbox.y1) / 2.0
    if (
        block_bbox.x0 <= center_x <= block_bbox.x1
        and block_bbox.y0 <= center_y <= block_bbox.y1
    ):
        return "inside"
    block_center_x = (block_bbox.x0 + block_bbox.x1) / 2.0
    block_center_y = (block_bbox.y0 + block_bbox.y1) / 2.0
    if center_x > block_center_x:
        return "right_of"
    if center_x < block_center_x:
        return "left_of"
    if center_y > block_center_y:
        return "below"
    return "above"


def filter_teacher_anchors(
    perception: DocumentPerception,
    *,
    cap: int = 12,
    min_confidence: float = 0.5,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Qualify visual annotations into at most ``cap`` template anchors."""

    audit = {
        "raw_annotations_seen": 0,
        "dropped_no_target": 0,
        "dropped_kind": 0,
        "dropped_confidence": 0,
        "deduped": 0,
        "kept": 0,
    }
    candidates: list[tuple[float, int, int, Any, Any]] = []
    order = 0
    for page in perception.pages:
        for annotation in page.annotations:
            audit["raw_annotations_seen"] += 1
            order += 1
            target = (annotation.raw_text_target or "").strip()
            if not target:
                audit["dropped_no_target"] += 1
                continue
            if annotation.kind not in ALLOWED_ANCHOR_KINDS:
                audit["dropped_kind"] += 1
                continue
            if annotation.confidence < min_confidence:
                audit["dropped_confidence"] += 1
                continue
            candidates.append(
                (
                    annotation.confidence,
                    page.page_index,
                    order,
                    annotation,
                    page,
                )
            )

    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    kept: list[tuple[Any, Any]] = []
    for _, _, _, annotation, page in candidates:
        duplicate = False
        for previous, previous_page in kept:
            if previous_page.page_index != page.page_index:
                continue
            first = getattr(previous, "bbox", None)
            second = getattr(annotation, "bbox", None)
            if first is None or second is None:
                continue
            if first.overlap_ratio(second) > 0.7:
                duplicate = True
                break
        if duplicate:
            audit["deduped"] += 1
            continue
        kept.append((annotation, page))

    anchors: list[dict[str, Any]] = []
    for annotation, page in kept:
        if len(anchors) >= max(0, cap):
            break
        anchor_text = (annotation.raw_text_target or "").strip()[:200]
        anchors.append(
            {
                "field_key": _field_key_for_label(anchor_text),
                "anchor_text": anchor_text,
                "relative_position": _relative_position(
                    annotation, page.blocks
                ),
                "confidence": float(annotation.confidence),
                "page_index": page.page_index,
            }
        )
    audit["kept"] = len(anchors)
    return anchors, audit


def _supplier_uuid(supplier_id: str | None) -> UUID | None:
    if not supplier_id:
        return None
    try:
        return UUID(str(supplier_id))
    except (ValueError, AttributeError):
        return uuid5(NAMESPACE_URL, f"dsh-core:supplier:{supplier_id}")


class TemplateSkillStore:
    """Filesystem store for candidate template skills."""

    def __init__(
        self,
        root: Path = DEFAULT_SKILLS_ROOT,
        *,
        repository: SupplierRepository | None = None,
    ) -> None:
        self.root = Path(root)
        self.repository = repository

    def _repository(self) -> SupplierRepository:
        if self.repository is None:
            self.repository = SupplierRepository()
        return self.repository

    @staticmethod
    def _filename(skill_name: str) -> str:
        slug = re.sub(r"[^a-z0-9_]+", "_", skill_name.casefold()).strip("_")
        if not slug.startswith("invoice_"):
            slug = f"invoice_{slug}"
        return f"{slug}.json"

    def _path(self, skill_name: str) -> Path:
        return self.root / self._filename(skill_name)

    @staticmethod
    def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=".template-skill-",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_name = stream.name
                json.dump(payload, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, path)
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)

    @staticmethod
    def _read(path: Path) -> TemplateSkill | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        try:
            return TemplateSkill.model_validate(payload)
        except Exception:
            return None

    def save(self, skill: TemplateSkill) -> Path:
        path = self._path(skill.skill_name)
        self._atomic_write(path, skill.model_dump(mode="json"))
        return path

    def load(self, skill_name: str) -> TemplateSkill | None:
        return self._read(self._path(skill_name))

    def list_skills(self) -> list[TemplateSkill]:
        if not self.root.is_dir():
            return []
        skills = [
            skill
            for path in sorted(self.root.glob("*.json"))
            if (skill := self._read(path)) is not None
        ]
        return sorted(skills, key=lambda skill: skill.skill_name)

    def _skills_for_supplier(self, supplier_id: str | None) -> TemplateSkill | None:
        if not supplier_id:
            return None
        for skill in self.list_skills():
            if skill.supplier_id == supplier_id:
                return skill
        return None

    def find_by_supplier(
        self,
        *,
        supplier_id: str | None = None,
        siret: str | None = None,
        vat_number: str | None = None,
        name: str | None = None,
    ) -> TemplateSkill | None:
        skill = self._skills_for_supplier(supplier_id)
        if skill is not None:
            return skill
        try:
            repository = self._repository()
            supplier: dict[str, Any] | None = None
            if siret:
                supplier = repository.find_by_siret(siret)
            elif vat_number:
                supplier = repository.find_by_tva(vat_number)
            elif name:
                supplier = repository.find_by_name(name)
            if supplier is None:
                return None
            return self._skills_for_supplier(
                str(supplier.get("supplier_id") or "")
            )
        except Exception:
            return None

    @staticmethod
    def _skill_labels(skill: TemplateSkill) -> list[str]:
        labels: list[str] = []
        fingerprint = skill.visual_signature.get("label_fingerprint")
        if isinstance(fingerprint, list):
            labels.extend(str(label) for label in fingerprint)
        trusted = skill.extraction_rules.get("trusted_labels")
        if isinstance(trusted, dict):
            labels.extend(str(label) for label in trusted.values())
        labels.extend(anchor.anchor_text for anchor in skill.layout_anchors)
        normalised: list[str] = []
        seen: set[str] = set()
        for label in labels:
            value = normalise_label(label)
            if value and value not in seen:
                seen.add(value)
                normalised.append(value)
        return normalised

    @staticmethod
    def _page1_text(perception: DocumentPerception) -> str:
        parts: list[str] = []
        for page in perception.pages:
            if page.page_index == 0:
                parts.extend(block.text for block in page.blocks if block.text)
        if not parts:
            parts.append(perception.raw_text)
        return normalise_label(" ".join(parts))

    @staticmethod
    def _label_present(label: str, page_text: str) -> bool:
        if not label or not page_text:
            return False
        if label in page_text:
            return True
        lines = page_text.splitlines()
        if not lines:
            return False
        return max(
            (
                difflib.SequenceMatcher(None, label, line).ratio()
                for line in lines
            ),
            default=0.0,
        ) >= 0.8

    def find_by_signature(
        self, perception: DocumentPerception
    ) -> TemplateSkill | None:
        text = self._page1_text(perception)
        best: TemplateSkill | None = None
        best_ratio = 0.0
        for skill in self.list_skills():
            labels = self._skill_labels(skill)
            if not labels:
                continue
            present = sum(
                1 for label in labels if self._label_present(label, text)
            )
            ratio = present / len(labels)
            if ratio >= 0.6 and ratio > best_ratio:
                best = skill
                best_ratio = ratio
        return best

    def inject_supplier_layout_rules(
        self, skill: TemplateSkill
    ) -> list[SupplierLayoutRule]:
        supplier_uuid = _supplier_uuid(skill.supplier_id)
        if supplier_uuid is None:
            return []
        rules: list[SupplierLayoutRule] = []
        for anchor in skill.layout_anchors:
            rules.append(
                SupplierLayoutRule(
                    supplier_id=supplier_uuid,
                    field_key=anchor.field_key,
                    anchor_text=anchor.anchor_text,
                    relative_position=anchor.relative_position,
                    confidence=anchor.confidence,
                    version=1,
                    active=False,
                    human_confirmed=False,
                )
            )
        return rules

    def bind_to_supplier(self, skill: TemplateSkill) -> None:
        if not skill.supplier_id:
            return
        try:
            repository = self._repository()
            supplier = repository.find_by_id(skill.supplier_id)
            if supplier is None:
                return
            siret = supplier.get("siret")
            if siret:
                repository.attach_or_update_skill(
                    str(siret), skill.skill_name, increment_usage=False
                )
                return
            data = repository._load()
            for record in data.get("suppliers", {}).values():
                if str(record.get("supplier_id")) != str(skill.supplier_id):
                    continue
                skills = record.setdefault("skills", [])
                if not any(
                    entry.get("skill_name") == skill.skill_name
                    for entry in skills
                ):
                    now = datetime.now(timezone.utc).isoformat()
                    skills.append(
                        {
                            "skill_name": skill.skill_name,
                            "active": False,
                            "usage_count": 0,
                            "created_at": now,
                            "last_used": now,
                        }
                    )
                    record["updated_at"] = now
                repository._save(data)
                return
        except Exception:
            return

    def register_usage(self, skill_name: str) -> None:
        skill = self.load(skill_name)
        if skill is None:
            return
        lifecycle = dict(skill.lifecycle)
        try:
            usage = int(lifecycle.get("usage_count", 0) or 0)
        except (TypeError, ValueError):
            usage = 0
        lifecycle["usage_count"] = usage + 1
        lifecycle["last_used"] = datetime.now(timezone.utc).isoformat()
        self.save(skill.model_copy(update={"lifecycle": lifecycle}))


class TemplateSkillApplier:
    """Deterministic template-based extraction for known suppliers."""

    def __init__(self, store: TemplateSkillStore) -> None:
        self.store = store

    def match(
        self,
        perception: DocumentPerception,
        *,
        supplier_hint: dict[str, Any] | None = None,
    ) -> TemplateSkill | None:
        if supplier_hint:
            skill = self.store.find_by_supplier(
                supplier_id=supplier_hint.get("supplier_id"),
                siret=supplier_hint.get("siret"),
                vat_number=supplier_hint.get("vat_number"),
                name=supplier_hint.get("name"),
            )
            if skill is not None:
                return skill
        return self.store.find_by_signature(perception)

    @staticmethod
    def _is_value_line(line: str) -> bool:
        text = line.strip()
        if not text or len(text) > 80:
            return False
        if re.fullmatch(
            r"[-+]?\d[\d\s.,]*\s*(?:\u20ac|EUR|USD|CHF|TTC\*?)?",
            text,
            re.IGNORECASE,
        ):
            return True
        if (
            len(text) <= 40
            and re.search(r"\u20ac|EUR|USD|CHF", text, re.IGNORECASE)
            and re.search(r"\d", text)
        ):
            return True
        return False

    @classmethod
    def _value_from_label_block(
        cls, text: str, anchor_text: str
    ) -> str | None:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return None
        target = normalise_label(anchor_text)
        for index, line in enumerate(lines):
            normalised = normalise_label(line)
            if target not in normalised:
                continue
            if ":" in line:
                remainder = line.split(":", 1)[1].strip()
                if cls._is_value_line(remainder):
                    return remainder
            for offset in (1, -1, 2, -2, 3, -3):
                candidate_index = index + offset
                if 0 <= candidate_index < len(lines):
                    candidate = lines[candidate_index]
                    if cls._is_value_line(candidate):
                        return candidate
        for line in lines:
            if cls._is_value_line(line) and target not in normalise_label(line):
                return line
        return None

    @staticmethod
    def _orthogonal_overlap(
        first_start: float,
        first_end: float,
        second_start: float,
        second_end: float,
    ) -> float:
        overlap = max(
            0.0, min(first_end, second_end) - max(first_start, second_start)
        )
        denominator = min(first_end - first_start, second_end - second_start)
        if denominator <= 0:
            return 0.0
        return overlap / denominator

    @classmethod
    def _value_block_text(
        cls,
        label: Block,
        blocks: list[Block],
        relative_position: str,
    ) -> str | None:
        candidates: list[tuple[float, Block]] = []
        for block in blocks:
            if block.block_id == label.block_id or not block.text.strip():
                continue
            bbox = block.bbox
            label_bbox = label.bbox
            if relative_position == "right_of":
                if bbox.x0 < label_bbox.x1 - 2:
                    continue
                overlap = cls._orthogonal_overlap(
                    label_bbox.y0, label_bbox.y1, bbox.y0, bbox.y1
                )
                if overlap < 0.5:
                    continue
                distance = max(0.0, bbox.x0 - label_bbox.x1)
            elif relative_position == "left_of":
                if bbox.x1 > label_bbox.x0 + 2:
                    continue
                overlap = cls._orthogonal_overlap(
                    label_bbox.y0, label_bbox.y1, bbox.y0, bbox.y1
                )
                if overlap < 0.5:
                    continue
                distance = max(0.0, label_bbox.x0 - bbox.x1)
            elif relative_position == "below":
                if bbox.y0 < label_bbox.y1 - 2:
                    continue
                overlap = cls._orthogonal_overlap(
                    label_bbox.x0, label_bbox.x1, bbox.x0, bbox.x1
                )
                if overlap < 0.5:
                    continue
                distance = max(0.0, bbox.y0 - label_bbox.y1)
            elif relative_position == "above":
                if bbox.y1 > label_bbox.y0 + 2:
                    continue
                overlap = cls._orthogonal_overlap(
                    label_bbox.x0, label_bbox.x1, bbox.x0, bbox.x1
                )
                if overlap < 0.5:
                    continue
                distance = max(0.0, label_bbox.y0 - bbox.y1)
            else:
                continue
            candidates.append((distance, block))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1].text.strip()

    @staticmethod
    def _find_label_block(
        anchor: TemplateAnchor, blocks: list[Block]
    ) -> Block | None:
        target = normalise_label(anchor.anchor_text)
        best: Block | None = None
        best_score = 0.0
        for block in blocks:
            if block.page_index != anchor.page_index or not block.text:
                continue
            normalised = normalise_label(block.text)
            if target and target in normalised:
                return block
            score = difflib.SequenceMatcher(
                None, target, normalise_label(block.text[:120])
            ).ratio()
            if score > best_score:
                best = block
                best_score = score
        return best if best_score >= 0.7 else None

    def _supplier_name(self, skill: TemplateSkill) -> str | None:
        if not skill.supplier_id:
            return None
        try:
            supplier = self.store._repository().find_by_id(skill.supplier_id)
        except Exception:
            return None
        if not supplier:
            return None
        name = supplier.get("raison_sociale")
        return str(name) if name else None

    def extract(
        self, perception: DocumentPerception, skill: TemplateSkill
    ) -> dict[str, Any]:
        blocks_by_page: dict[int, list[Block]] = {}
        for page in perception.pages:
            blocks_by_page.setdefault(page.page_index, []).extend(page.blocks)
        fields: dict[str, Any] = {}
        for anchor in skill.layout_anchors:
            blocks = blocks_by_page.get(anchor.page_index, [])
            label = self._find_label_block(anchor, blocks)
            if label is None:
                continue
            value = self._value_from_label_block(label.text, anchor.anchor_text)
            if value is None and anchor.relative_position != "inside":
                value = self._value_block_text(
                    label, blocks, anchor.relative_position
                )
            if value is None:
                continue
            if anchor.field_key == "fournisseur":
                value = self._supplier_name(skill) or value
            fields.setdefault(anchor.field_key, value.strip())
        regex = skill.extraction_rules.get("regex_numero_facture")
        if isinstance(regex, str) and regex:
            try:
                match = re.search(regex, perception.raw_text)
            except re.error:
                match = None
            if match is not None:
                number = next(
                    (
                        group
                        for group in match.groups()
                        if group is not None
                    ),
                    match.group(0),
                )
                fields["numero_facture"] = str(number).strip()
        if "fournisseur" not in fields:
            supplier_name = self._supplier_name(skill)
            if supplier_name:
                fields["fournisseur"] = supplier_name
        expected = {anchor.field_key for anchor in skill.layout_anchors}
        if isinstance(regex, str) and regex:
            expected.add("numero_facture")
        present = {key for key in expected if fields.get(key)}
        coverage = len(present) / len(expected) if expected else 0.0
        return {
            "fields": fields,
            "skill_name": skill.skill_name,
            "coverage": round(coverage, 3),
            "source": "template_skill",
        }
