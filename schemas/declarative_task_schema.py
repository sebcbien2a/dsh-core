"""Declarative task-folder contracts and ``instructions.md`` parser (P15.2).

A project folder is self-describing: an ``instructions.md`` file lists the
declarative operations to apply to the folder inputs. Two workflows are
supported:

- ``PDF_PRESENTATION``: redesign a PDF / PLV from ``original.pdf`` using
  declarative deletions, replacements and additions;
- ``TARIFS_EXCEL``: non-destructively merge supplier prices into
  ``tarifs-existants.xlsx``.

The parser is deliberately tolerant: accents, heading aliases (French and
English), bullet or numbered steps and YAML frontmatter are all accepted.
Unknown step lines become warnings instead of hard failures, while any attempt
to opt out of the non-destructive guarantee is rejected with
``NON_DESTRUCTIVE_VIOLATION``.

This module performs no workflow I/O beyond reading ``instructions.md`` through
:func:`load_task`. Every path is checked with :func:`ensure_contained` so no
instruction can escape the project folder.
"""

from __future__ import annotations

import re
import unicodedata
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

INSTRUCTIONS_FILE = "instructions.md"
ORIGINAL_PDF = "original.pdf"
ORIGINAL_ANNOTE_PDF = "original-annote.pdf"
TEMPLATE_REFERENCE_PDF = "template-reference.pdf"
TARIFS_EXISTANTS = "tarifs-existants.xlsx"
FOURNISSEURS_DIR = "fournisseurs"
ASSETS_DIR = "assets"
OUTPUT_DIR = "output"
LOG_DIR = "log"
TARIFS_OUTPUT = f"{OUTPUT_DIR}/tarifs-mis-a-jour.xlsx"
PDF_OUTPUT = f"{OUTPUT_DIR}/resultat.pdf"
PDF_HTML_OUTPUT = f"{OUTPUT_DIR}/resultat.html"

TASK_TREE: tuple[str, ...] = (
    FOURNISSEURS_DIR,
    ASSETS_DIR,
    OUTPUT_DIR,
    LOG_DIR,
)

INPUT_FILES: tuple[str, ...] = (
    ORIGINAL_PDF,
    ORIGINAL_ANNOTE_PDF,
    TEMPLATE_REFERENCE_PDF,
    TARIFS_EXISTANTS,
)

DEFAULT_PALETTE: Mapping[str, str] = {
    "primaire": "#1A1F2C",
    "secondaire": "#2B2D42",
    "accent": "#E63946",
    "fond": "#FFFFFF",
    "texte": "#1A1F2C",
}

STATUS_NEW = "nouveau"
STATUS_UPDATED = "modifié"
STATUS_UNCHANGED = "inchangé"
ALERT_MISSING_PRICE = "prix manquant"
STATUT_NOUVEAU = STATUS_NEW
STATUT_MODIFIE = STATUS_UPDATED
STATUT_INCHANGE = STATUS_UNCHANGED


class DeclarativeTaskError(ValueError):
    """Invalid declarative task; ``code`` is stable for tests and logs."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


class WorkflowType(str, Enum):
    """The two declarative workflows supported by P15.2."""

    PDF_PRESENTATION = "PDF_PRESENTATION"
    TARIFS_EXCEL = "TARIFS_EXCEL"


class InstructionSection(str, Enum):
    """Canonical section identifiers accepted by the parser."""

    SOURCE_FICHIERS = "source_fichiers"
    MODIFICATIONS_CONTENU = "modifications_contenu"
    REGLES_FUSION = "regles_fusion"
    REGLES_PRIX = "regles_prix"
    CONTRAINTES = "contraintes"


SECTION_LABELS: Mapping[str, str] = {
    InstructionSection.SOURCE_FICHIERS.value: "Source / Fichiers",
    InstructionSection.MODIFICATIONS_CONTENU.value: "Modifications de contenu",
    InstructionSection.REGLES_FUSION.value: "Règles de fusion",
    InstructionSection.REGLES_PRIX.value: "Règles de prix",
    InstructionSection.CONTRAINTES.value: "Contraintes",
}

SECTION_ALIASES: Mapping[str, tuple[str, ...]] = {
    InstructionSection.SOURCE_FICHIERS.value: (
        "source fichiers",
        "sources fichiers",
        "source et fichiers",
        "source",
        "sources",
        "fichiers",
        "fichiers source",
        "fichiers d entree",
        "entree",
        "entrees",
        "inputs",
        "documents source",
        "source files",
    ),
    InstructionSection.MODIFICATIONS_CONTENU.value: (
        "modifications de contenu",
        "modification de contenu",
        "modifications du contenu",
        "modifications contenu",
        "modification contenu",
        "modifications",
        "contenu",
        "content modifications",
        "content changes",
        "changes",
    ),
    InstructionSection.REGLES_FUSION.value: (
        "regles de fusion",
        "regle de fusion",
        "regles fusion",
        "regles de fusions",
        "fusion",
        "regles de rapprochement",
        "merge rules",
        "fusion rules",
    ),
    InstructionSection.REGLES_PRIX.value: (
        "regles de prix",
        "regles des prix",
        "regles prix",
        "regle de prix",
        "prix",
        "tarification",
        "regles tarifaires",
        "price rules",
        "pricing rules",
    ),
    InstructionSection.CONTRAINTES.value: (
        "contraintes",
        "contrainte",
        "contraintes techniques",
        "limites",
        "options",
        "constraints",
        "settings",
    ),
}


class ModificationOperation(str, Enum):
    """Declarative content operation (French stable values + English aliases)."""

    SUPPRIMER = "supprimer"
    DELETE = "supprimer"
    REMPLACER = "remplacer"
    REPLACE = "remplacer"
    AJOUTER = "ajouter"
    ADD = "ajouter"


class ContentModification(BaseModel):
    """One deterministic PDF content operation parsed from ``instructions.md``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: ModificationOperation
    target: str = ""
    replacement: str = ""
    position: str = ""
    anchor: str = ""
    step_index: int = 0
    raw: str = ""

    @property
    def action(self) -> str:
        return self.operation.value


class TaskStep(BaseModel):
    """One bullet / numbered step of an instruction section."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int
    text: str
    key: str | None = None
    value: str | None = None


class MergeRules(BaseModel):
    """Non-destructive Excel merge rules (``preserve_missing`` is mandatory)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    match_key: str = "code"
    preserve_missing: bool = True
    track_ancien_prix: bool = True
    added_date: bool = True
    header_row: int | None = Field(default=None, ge=1)
    sheet: str | None = None
    date_column: str = "date_ajout"
    extra: dict[str, str] = Field(default_factory=dict)

    @field_validator("match_key")
    @classmethod
    def only_article_code(cls, value: str) -> str:
        key = normalize_key(value)
        if key not in {"code", "code_article", "article", "reference"}:
            raise ValueError("match_key must be the article code")
        return "code"

    @field_validator("preserve_missing")
    @classmethod
    def never_delete_missing_rows(cls, value: bool) -> bool:
        if not value:
            raise ValueError("preserve_missing is mandatory")
        return True


class PriceRules(BaseModel):
    """Bidirectional price completion rules (conditioning <-> kg)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    packaging_column: str = "prix_conditionnement"
    weight_column: str = "poids"
    kg_column: str = "prix_kg"
    decimals: int = Field(default=2, ge=0, le=6)
    currency: str = "EUR"
    missing_alert: str = ALERT_MISSING_PRICE


class TaskConstraints(BaseModel):
    """Execution constraints shared by both workflows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    force: bool = False
    non_destructif: bool = True
    renderer: str = "auto"
    paper_format: str = "A4"
    columns: int = Field(default=1, ge=1, le=4)
    palette: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_PALETTE))
    logo: str | None = None
    title: str | None = None
    output_name: str | None = None
    extra: dict[str, str] = Field(default_factory=dict)

    @field_validator("non_destructif")
    @classmethod
    def non_destructive_mandatory(cls, value: bool) -> bool:
        if not value:
            raise ValueError("non_destructif is mandatory")
        return True

    @field_validator("renderer")
    @classmethod
    def known_renderer(cls, value: str) -> str:
        return normalize_renderer(value)


class TaskFolderLayout(BaseModel):
    """Resolved paths of one declarative project folder (no I/O)."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    root: Path
    instructions: Path
    original_pdf: Path
    original_annote_pdf: Path
    template_reference_pdf: Path
    tarifs_existants: Path
    fournisseurs: Path
    assets: Path
    output: Path
    log: Path

    @model_validator(mode="after")
    def all_paths_are_contained(self) -> "TaskFolderLayout":
        for name in (
            "instructions",
            "original_pdf",
            "original_annote_pdf",
            "template_reference_pdf",
            "tarifs_existants",
            "fournisseurs",
            "assets",
            "output",
            "log",
        ):
            ensure_contained(self.root, getattr(self, name))
        return self

    @property
    def instructions_path(self) -> Path:
        return self.instructions

    @property
    def fournisseurs_dir(self) -> Path:
        return self.fournisseurs

    @property
    def assets_dir(self) -> Path:
        return self.assets

    @property
    def output_dir(self) -> Path:
        return self.output

    @property
    def log_dir(self) -> Path:
        return self.log

    def path(self, *parts: str) -> Path:
        """Resolve a path inside the folder (traversal-safe)."""
        return ensure_contained(self.root, self.root.joinpath(*parts))

    def output_path(self, relative: str) -> Path:
        return ensure_contained(self.output, self.output / relative)

    def log_path(self, relative: str) -> Path:
        return ensure_contained(self.log, self.log / relative)

    def existing_inputs(self) -> tuple[str, ...]:
        names: list[str] = []
        for name in INPUT_FILES:
            if (self.root / name).is_file():
                names.append(name)
        if self.fournisseurs.is_dir():
            names.append(FOURNISSEURS_DIR)
        return tuple(names)

    def supplier_pdfs(self) -> tuple[Path, ...]:
        if not self.fournisseurs.is_dir():
            return ()
        return tuple(sorted(path for path in self.fournisseurs.glob("*.pdf") if path.is_file()))


class ParsedInstructions(BaseModel):
    """Deterministic in-memory view of one ``instructions.md`` file."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    version: str = "1.0"
    target: str = ""
    workflow: WorkflowType
    workflow_source: str = "default"
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    sections: dict[str, tuple[TaskStep, ...]] = Field(default_factory=dict)
    extra_sections: dict[str, tuple[TaskStep, ...]] = Field(default_factory=dict)
    modifications: tuple[ContentModification, ...] = ()
    merge_rules: MergeRules = Field(default_factory=MergeRules)
    price_rules: PriceRules = Field(default_factory=PriceRules)
    constraints: TaskConstraints = Field(default_factory=TaskConstraints)
    warnings: tuple[str, ...] = ()
    source_path: Path | None = None
    raw_text: str = Field(default="", exclude=True)

    def steps(self, section: InstructionSection | str) -> tuple[TaskStep, ...]:
        key = section.value if isinstance(section, InstructionSection) else str(section)
        return self.sections.get(key, ())

    def step_texts(self, section: InstructionSection | str) -> tuple[str, ...]:
        return tuple(step.text for step in self.steps(section))

    @property
    def is_tarifs(self) -> bool:
        return self.workflow is WorkflowType.TARIFS_EXCEL

    @property
    def is_pdf(self) -> bool:
        return self.workflow is WorkflowType.PDF_PRESENTATION

    @property
    def workflow_type(self) -> WorkflowType:
        return self.workflow

    @property
    def merge(self) -> MergeRules:
        return self.merge_rules

    @property
    def prices(self) -> PriceRules:
        return self.price_rules

    def section_steps(self, section: InstructionSection | str) -> tuple[TaskStep, ...]:
        return self.steps(section)


class DeclarativeTask(BaseModel):
    """A project folder bound to its parsed instructions."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    folder: TaskFolderLayout
    brief: ParsedInstructions

    @property
    def workflow(self) -> WorkflowType:
        return self.brief.workflow


# -- path safety -------------------------------------------------------------


def ensure_contained(base: str | Path, candidate: str | Path) -> Path:
    """Return the resolved ``candidate`` or raise when it escapes ``base``."""
    base_path = Path(base)
    candidate_path = Path(candidate)
    if "\x00" in str(base_path) or "\x00" in str(candidate_path):
        raise DeclarativeTaskError("INVALID_PATH", repr(candidate))
    try:
        root = base_path.resolve()
        resolved = candidate_path.resolve()
    except OSError as exc:
        raise DeclarativeTaskError("INVALID_PATH", repr(candidate)) from exc
    if root != resolved and root not in resolved.parents:
        raise DeclarativeTaskError("PATH_TRAVERSAL", str(candidate))
    return resolved


def build_task_folder(root: str | Path) -> TaskFolderLayout:
    """Resolve the canonical layout of a project folder (no directory creation)."""
    root_path = Path(root)
    try:
        resolved_root = root_path.resolve()
    except OSError as exc:
        raise DeclarativeTaskError("INVALID_TASK_FOLDER", repr(root)) from exc
    if not resolved_root.is_dir():
        raise DeclarativeTaskError("INVALID_TASK_FOLDER", str(root))

    def contained(name: str) -> Path:
        return ensure_contained(resolved_root, resolved_root / name)

    return TaskFolderLayout(
        root=resolved_root,
        instructions=contained(INSTRUCTIONS_FILE),
        original_pdf=contained(ORIGINAL_PDF),
        original_annote_pdf=contained(ORIGINAL_ANNOTE_PDF),
        template_reference_pdf=contained(TEMPLATE_REFERENCE_PDF),
        tarifs_existants=contained(TARIFS_EXISTANTS),
        fournisseurs=contained(FOURNISSEURS_DIR),
        assets=contained(ASSETS_DIR),
        output=contained(OUTPUT_DIR),
        log=contained(LOG_DIR),
    )


# -- text normalization ------------------------------------------------------


def _strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_label(value: object) -> str:
    """Lower-case, accent-free, punctuation-free label."""
    if value is None:
        return ""
    text = _strip_accents(str(value)).casefold()
    text = text.replace("&", " et ").replace("+", " et ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_key(value: object) -> str:
    return normalize_label(value).replace(" ", "_")


def normalize_renderer(value: object) -> str:
    key = normalize_label(value)
    if key in {"", "auto", "automatique", "automatic", "default"}:
        return "auto"
    if key in {"weasyprint", "weasy", "weasy print"}:
        return "weasyprint"
    if key in {"reportlab", "report lab"}:
        return "reportlab"
    if key in {"pymupdf", "fitz", "mupdf"}:
        return "pymupdf"
    return "auto"


def _to_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    key = normalize_label(value)
    if key in {"true", "vrai", "oui", "yes", "on", "1", "actif", "active"}:
        return True
    if key in {"false", "faux", "non", "no", "off", "0", "inactif", "inactive"}:
        return False
    return None


def _to_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    match = re.search(r"-?\d+", str(value))
    return int(match.group()) if match else default


def _scalar(value: object) -> Any:
    if isinstance(value, (bool, int, float)):
        return value
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1].strip()
    key = normalize_label(text)
    if key in {"true", "vrai", "oui", "yes", "on"}:
        return True
    if key in {"false", "faux", "non", "no", "off"}:
        return False
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+[.,]\d+", text):
        return float(text.replace(",", "."))
    return text


def _unquote(text: str) -> str:
    value = text.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"', "`"}:
        return value[1:-1].strip()
    return value


def _first_quoted(text: str) -> tuple[str | None, str]:
    match = re.search(r'"([^"]*)"|\'([^\']*)\'|`([^`]*)`', text)
    if match is None:
        return None, text
    quoted = next(group for group in match.groups() if group is not None)
    remainder = (text[: match.start()] + " " + text[match.end() :]).strip()
    return quoted.strip(), remainder


def split_key_value(text: str) -> tuple[str | None, str]:
    """Split ``key: value`` / ``key = value`` outside quotes and brackets."""
    quote: str | None = None
    depth = 0
    for index, char in enumerate(text):
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char in {":", "="} and depth == 0:
            key = text[:index].strip()
            if 0 < len(key) <= 64 and re.fullmatch(r"[A-Za-zÀ-ÿ0-9 _./()'-]+", key):
                return key, text[index + 1 :].strip()
    return None, text.strip()


def _color_or_default(value: object, default: str) -> str | None:
    text = str(value).strip()
    if re.fullmatch(r"#[0-9A-Fa-f]{3,8}", text):
        return text
    if re.fullmatch(r"[A-Za-z]{3,20}", text):
        return text
    if re.fullmatch(r"rgba?\([0-9.,%\s]+\)", text):
        return text
    return None


# -- instructions parsing ----------------------------------------------------

_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_LIST_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+(.*)$")
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    content = text.lstrip("\ufeff")
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, content
    for index in range(1, len(lines)):
        if lines[index].strip() in {"---", "..."}:
            try:
                data = yaml.safe_load("\n".join(lines[1:index])) or {}
            except yaml.YAMLError as exc:
                raise DeclarativeTaskError("INVALID_FRONTMATTER", str(exc)) from exc
            if not isinstance(data, Mapping):
                raise DeclarativeTaskError("INVALID_FRONTMATTER", "frontmatter must be a mapping")
            return dict(data), "\n".join(lines[index + 1 :])
    raise DeclarativeTaskError("INVALID_FRONTMATTER", "missing closing delimiter")


def _alias_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for section, aliases in SECTION_ALIASES.items():
        for alias in aliases:
            index[normalize_label(alias)] = section
    return index


_SECTION_BY_ALIAS = _alias_index()


def _parse_sections(body: str) -> tuple[dict[str, tuple[TaskStep, ...]], dict[str, tuple[TaskStep, ...]]]:
    collected: dict[str, list[TaskStep]] = {section.value: [] for section in InstructionSection}
    extra: dict[str, list[TaskStep]] = {}
    current: str | None = None
    current_level = 0
    extra_key: str | None = None
    step_index = 0
    in_fence = False

    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        heading = _HEADING_RE.match(line)
        if heading and not in_fence:
            level = len(heading.group(1))
            label = re.sub(r"[*_`]+", "", heading.group(2)).strip()
            label = re.sub(r"^\d+(?:[.)]|[\s-]+)\s*", "", label)
            section = _SECTION_BY_ALIAS.get(normalize_label(label))
            if section is not None:
                current = section
                current_level = level
                extra_key = None
            elif current is not None and level > current_level:
                continue
            else:
                current = None
                current_level = level
                extra_key = normalize_key(label) or "preamble"
                extra.setdefault(extra_key, [])
            continue

        if not line.strip():
            continue
        if current is None and extra_key is None:
            continue
        list_match = _LIST_RE.match(line)
        text = list_match.group(1).strip() if list_match else line.strip()
        text = re.sub(r"^\[[ xX]\]\s*", "", text)
        if not text or text.startswith("<!--") or text == "---":
            continue
        key_raw, value_raw = split_key_value(text)
        step = TaskStep(
            index=step_index,
            text=text,
            key=key_raw,
            value=value_raw if key_raw is not None else None,
        )
        step_index += 1
        target = collected[current] if current is not None else extra.setdefault(extra_key or "extra", [])
        target.append(step)

    return (
        {key: tuple(value) for key, value in collected.items()},
        {key: tuple(value) for key, value in extra.items() if value},
    )


def _replace_parts(value: str) -> tuple[str, str]:
    parts = re.split(r"\s*(?:->|=>|→)\s*", value, maxsplit=1)
    if len(parts) == 2:
        return _unquote(parts[0]), _unquote(parts[1])
    parts = re.split(r"\s+(?:par|by|avec)\s+", value, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        return _unquote(parts[0]), _unquote(parts[1])
    quoted, remainder = _first_quoted(value)
    if quoted is not None:
        return quoted, _unquote(remainder)
    return _unquote(value), ""


def _parse_replacement_pairs(value: str) -> tuple[str, str]:
    chunks = re.split(r"\s*[;,]\s*", value)
    old: str | None = None
    new: str | None = None
    for chunk in chunks:
        key, item = split_key_value(chunk)
        if key is None:
            continue
        normalized = normalize_key(key)
        if normalized in {"ancien", "old", "source", "avant", "de"}:
            old = _unquote(item)
        elif normalized in {"nouveau", "new", "cible", "apres", "replacement", "vers"}:
            new = _unquote(item)
    if old is not None and new is not None:
        return old, new
    return _replace_parts(value)


def _parse_add(value: str) -> tuple[str, str, str]:
    position = "end"
    anchor = ""
    remainder = value.strip()
    match = re.search(r"\s+(apres|après|after|avant|before)\s+(.+)$", remainder, re.IGNORECASE)
    if match:
        position = "before" if normalize_label(match.group(1)) in {"avant", "before"} else "after"
        anchor = _unquote(match.group(2))
        remainder = remainder[: match.start()].strip()
    start_match = re.match(
        r"^(?:au\s+)?(?:debut|début|commencement|start)\b[\s:]*",
        remainder,
        re.IGNORECASE,
    )
    if start_match:
        position = "start"
        remainder = remainder[start_match.end() :].strip()
    end_match = re.search(
        r"[\s:]*(?:a\s+la\s+fin|à\s+la\s+fin|fin|end)\s*$",
        remainder,
        re.IGNORECASE,
    )
    if end_match and end_match.start() > 0:
        position = "end"
        remainder = remainder[: end_match.start()].strip()
    quoted, rest = _first_quoted(remainder)
    content = quoted if quoted is not None else _unquote(rest if quoted is not None else remainder)
    return content, position, anchor


def parse_content_modifications(
    steps: Iterable[TaskStep], *, strict: bool = False
) -> tuple[tuple[ContentModification, ...], tuple[str, ...]]:
    """Parse declarative modification steps (tolerant unless ``strict``)."""
    modifications: list[ContentModification] = []
    warnings: list[str] = []
    for step in steps:
        text = step.text.strip()
        parsing_text = re.sub(
            r"^[*_`]{1,3}([^*_`]+?)[*_`]{1,3}\s*", r"\1 ", text
        ).strip()
        key_raw, value_raw = split_key_value(parsing_text)
        key = normalize_key(key_raw) if key_raw is not None else ""
        raw_value = value_raw if key_raw is not None else parsing_text
        rest = parsing_text
        operator = ""
        if key:
            if key in {"supprimer", "supprime", "suppression", "retirer", "retrait", "delete", "remove"}:
                operator = ModificationOperation.SUPPRIMER.value
            elif key in {"remplacer", "remplace", "remplacement", "modifier", "changer", "replace", "update"}:
                operator = ModificationOperation.REMPLACER.value
            elif key in {"ajouter", "ajout", "add", "inserer", "insert"}:
                operator = ModificationOperation.AJOUTER.value
        if not operator:
            match = re.match(
                r"^(supprimer|supprime|suppression|retirer|retrait|delete|remove|remplacer|remplace|remplacement|modifier|changer|replace|update|ajouter|ajout|add|inserer|insert)\b\s*(.*)$",
                parsing_text,
                re.IGNORECASE,
            )
            if match:
                verb = normalize_key(match.group(1))
                rest = match.group(2).strip()
                if verb in {
                    "supprimer",
                    "supprime",
                    "suppression",
                    "retirer",
                    "retrait",
                    "delete",
                    "remove",
                }:
                    operator = ModificationOperation.SUPPRIMER.value
                elif verb in {
                    "remplacer",
                    "remplace",
                    "remplacement",
                    "modifier",
                    "changer",
                    "replace",
                    "update",
                }:
                    operator = ModificationOperation.REMPLACER.value
                else:
                    operator = ModificationOperation.AJOUTER.value
                raw_value = re.sub(r"^\s*:\s*", "", rest)
        if not operator:
            message = f"modification_ignoree:{text}"
            if strict:
                raise DeclarativeTaskError("INVALID_MODIFICATION", text)
            warnings.append(message)
            continue

        candidate = str(raw_value).strip()
        candidate = re.sub(r"^\s*:\s*", "", candidate)
        if operator == ModificationOperation.SUPPRIMER.value:
            quoted, remainder = _first_quoted(candidate)
            target = quoted if quoted is not None else _unquote(remainder if quoted is not None else candidate)
            if not target:
                if strict:
                    raise DeclarativeTaskError("INVALID_MODIFICATION", text)
                warnings.append(f"modification_ignoree:{text}")
                continue
            modifications.append(
                ContentModification(
                    operation=ModificationOperation.SUPPRIMER,
                    target=target,
                    step_index=step.index,
                    raw=text,
                )
            )
        elif operator == ModificationOperation.REMPLACER.value:
            target, replacement = _parse_replacement_pairs(candidate)
            if not target:
                if strict:
                    raise DeclarativeTaskError("INVALID_MODIFICATION", text)
                warnings.append(f"modification_ignoree:{text}")
                continue
            modifications.append(
                ContentModification(
                    operation=ModificationOperation.REMPLACER,
                    target=target,
                    replacement=replacement,
                    step_index=step.index,
                    raw=text,
                )
            )
        else:
            ordered = re.match(
                r"^(apres|après|after|avant|before)\s+([\"'`])(.+?)\2\s*:\s*(.+)$",
                candidate,
                re.IGNORECASE,
            )
            if ordered:
                position = (
                    "before"
                    if normalize_label(ordered.group(1)) in {"avant", "before"}
                    else "after"
                )
                anchor = ordered.group(3)
                content = _unquote(ordered.group(4))
            else:
                content, position, anchor = _parse_add(candidate)
            key_hint = normalize_label(key_raw or "")
            if any(token in key_hint for token in ("debut", "commencement", "start")):
                position = "start"
            elif any(token in key_hint for token in ("fin", "end")):
                position = "end"
            if not content and position not in {"start", "end"}:
                if strict:
                    raise DeclarativeTaskError("INVALID_MODIFICATION", text)
                warnings.append(f"modification_ignoree:{text}")
                continue
            modifications.append(
                ContentModification(
                    operation=ModificationOperation.AJOUTER,
                    replacement=content,
                    position=position,
                    anchor=anchor,
                    step_index=step.index,
                    raw=text,
                )
            )
    return tuple(modifications), tuple(warnings)


def _option_items(frontmatter: Mapping[str, Any]) -> dict[str, Any]:
    items: dict[str, Any] = {}
    for key, value in frontmatter.items():
        normalized = normalize_key(key)
        if normalized in {
            "workflow",
            "workflow_type",
            "version",
            "cible",
            "moteur",
            "engine",
            "type",
            "target",
            "options",
        }:
            continue
        items[normalized] = value
    options = frontmatter.get("options")
    if isinstance(options, Mapping):
        for key, value in options.items():
            items[normalize_key(key)] = value
    elif options is not None:
        raise DeclarativeTaskError("INVALID_OPTIONS", repr(options))
    return items


def parse_merge_rules(
    steps: Iterable[TaskStep],
    options: Mapping[str, Any] | None = None,
) -> tuple[MergeRules, tuple[str, ...]]:
    """Parse merge rules while enforcing the absolute no-deletion guarantee."""
    values: dict[str, Any] = {}
    warnings: list[str] = []
    for key, value in (options or {}).items():
        normalized = normalize_key(key)
        if normalized in {
            "conserver_lignes_absentes",
            "preserver_lignes_absentes",
            "preserve_missing",
            "supprimer_lignes_absentes",
            "delete_missing",
            "match_key",
            "cle",
            "cle_fusion",
            "key",
            "ancien_prix",
            "track_ancien_prix",
            "date_ajout",
            "added_date",
            "header_row",
            "en_tete",
            "ligne_entete",
            "sheet",
            "feuille",
            "onglet",
            "date_column",
            "colonne_date",
        }:
            values[normalized] = value

    for step in steps:
        key_raw, value_raw = step.key, step.value
        if key_raw is None:
            normalized_text = normalize_label(step.text)
            negated = any(
                marker in normalized_text
                for marker in ("ne pas", "jamais", "aucune", "interdit", "sans supprimer", "conserver")
            )
            if "supprimer" in normalized_text and "absent" in normalized_text:
                if negated:
                    values["conserver_lignes_absentes"] = True
                else:
                    raise DeclarativeTaskError("NON_DESTRUCTIVE_VIOLATION", step.text)
            if "conserver" in normalized_text and "absent" in normalized_text:
                values["conserver_lignes_absentes"] = True
            if "par code" in normalized_text or "cle code" in normalized_text:
                values["cle"] = "code"
            if "ancien prix" in normalized_text:
                values["ancien_prix"] = True
            if "date d ajout" in normalized_text or "date ajout" in normalized_text:
                values["date_ajout"] = True
            continue
        key = normalize_key(key_raw)
        raw_value = value_raw or ""
        if (
            "supprimer" in key
            and "absent" in key
            and not any(marker in key for marker in ("pas", "sans", "conserver", "preserver"))
        ):
            if _to_bool(raw_value) is not False:
                raise DeclarativeTaskError("NON_DESTRUCTIVE_VIOLATION", step.text)
            warnings.append("suppression_lignes_refusee")
            continue
        if key in {
            "supprimer_lignes_absentes",
            "delete_missing",
            "supprimer_absents",
            "supprimer_lignes_presentes",
        }:
            if _to_bool(raw_value) is not False:
                raise DeclarativeTaskError("NON_DESTRUCTIVE_VIOLATION", step.text)
            warnings.append("suppression_lignes_refusee")
            continue
        if key in {
            "conserver_lignes_absentes",
            "preserver_lignes_absentes",
            "preserve_missing",
            "conserver_absents",
        }:
            if _to_bool(raw_value) is False:
                raise DeclarativeTaskError("NON_DESTRUCTIVE_VIOLATION", step.text)
            values[key] = True
            continue
        values[key] = _scalar(raw_value)

    if _to_bool(values.get("supprimer_lignes_absentes")) is True or _to_bool(
        values.get("delete_missing")
    ) is True:
        raise DeclarativeTaskError("NON_DESTRUCTIVE_VIOLATION", "supprimer_lignes_absentes")
    for key in (
        "conserver_lignes_absentes",
        "preserver_lignes_absentes",
        "preserve_missing",
    ):
        if key in values and _to_bool(values[key]) is False:
            raise DeclarativeTaskError("NON_DESTRUCTIVE_VIOLATION", key)

    match_key = values.get("cle") or values.get("cle_fusion") or values.get("match_key") or values.get("key")
    normalized_match = normalize_key(match_key) if match_key is not None else "code"
    if normalized_match not in {"code", "code_article", "article", "reference"}:
        raise DeclarativeTaskError("INVALID_MERGE_KEY", str(match_key))

    header_raw = values.get("header_row", values.get("en_tete", values.get("ligne_entete")))
    header_row = _to_int(header_raw, 0) if header_raw is not None else None
    if header_row is not None and header_row < 1:
        raise DeclarativeTaskError("INVALID_HEADER_ROW", str(header_raw))

    track = values.get("ancien_prix", values.get("track_ancien_prix", True))
    added = values.get("date_ajout", values.get("added_date", True))
    sheet = values.get("sheet", values.get("feuille", values.get("onglet")))
    date_column = values.get("date_column", values.get("colonne_date", "date_ajout"))
    known = {
        "cle",
        "cle_fusion",
        "match_key",
        "key",
        "conserver_lignes_absentes",
        "preserver_lignes_absentes",
        "preserve_missing",
        "supprimer_lignes_absentes",
        "delete_missing",
        "supprimer_absents",
        "ancien_prix",
        "track_ancien_prix",
        "date_ajout",
        "added_date",
        "header_row",
        "en_tete",
        "ligne_entete",
        "sheet",
        "feuille",
        "onglet",
        "date_column",
        "colonne_date",
    }
    extra = {key: str(value) for key, value in values.items() if key not in known}

    return (
        MergeRules(
            match_key="code",
            preserve_missing=True,
            track_ancien_prix=_to_bool(track) is not False,
            added_date=_to_bool(added) is not False,
            header_row=header_row,
            sheet=str(sheet) if sheet else None,
            date_column=str(date_column) or "date_ajout",
            extra=extra,
        ),
        tuple(warnings),
    )


def parse_price_rules(steps: Iterable[TaskStep]) -> tuple[PriceRules, tuple[str, ...]]:
    values: dict[str, Any] = {}
    warnings: list[str] = []
    aliases = {
        "arrondi": "decimals",
        "decimales": "decimals",
        "decimals": "decimals",
        "devise": "currency",
        "currency": "currency",
        "colonne_conditionnement": "packaging_column",
        "conditionnement": "packaging_column",
        "prix_conditionnement": "packaging_column",
        "colonne_poids": "weight_column",
        "poids": "weight_column",
        "colonne_prix_kg": "kg_column",
        "prix_kg": "kg_column",
        "alerte": "missing_alert",
        "alerte_prix_manquant": "missing_alert",
    }
    for step in steps:
        if step.key is None:
            continue
        key = aliases.get(normalize_key(step.key))
        if key is None:
            warnings.append(f"regle_prix_ignoree:{step.text}")
            continue
        raw = step.value or ""
        if key in {"packaging_column", "weight_column", "kg_column"} and not re.fullmatch(
            r"[A-Za-z0-9_ -]{1,64}", str(raw).strip()
        ):
            warnings.append(f"regle_prix_ignoree:{step.text}")
            continue
        values[key] = _scalar(raw)

    decimals = _to_int(values.get("decimals", 2), 2)
    if not 0 <= decimals <= 6:
        warnings.append("arrondi_invalide")
        decimals = 2
    return (
        PriceRules(
            packaging_column=str(values.get("packaging_column") or "prix_conditionnement"),
            weight_column=str(values.get("weight_column") or "poids"),
            kg_column=str(values.get("kg_column") or "prix_kg"),
            decimals=decimals,
            currency=str(values.get("currency") or "EUR").upper(),
            missing_alert=str(values.get("missing_alert") or ALERT_MISSING_PRICE),
        ),
        tuple(warnings),
    )


def _palette_key(value: object) -> str:
    key = normalize_key(value)
    return {
        "primary": "primaire",
        "primary_color": "primaire",
        "couleur_primaire": "primaire",
        "secondary": "secondaire",
        "secondary_color": "secondaire",
        "couleur_secondaire": "secondaire",
        "accent_color": "accent",
        "couleur_accent": "accent",
        "background": "fond",
        "background_color": "fond",
        "couleur_fond": "fond",
        "text": "texte",
        "text_color": "texte",
        "couleur_texte": "texte",
    }.get(key, key)


def parse_constraints(
    steps: Iterable[TaskStep],
    frontmatter: Mapping[str, Any] | None = None,
) -> tuple[TaskConstraints, tuple[str, ...]]:
    options = _option_items(frontmatter or {})
    palette = dict(DEFAULT_PALETTE)
    extra: dict[str, str] = {}
    warnings: list[str] = []
    force = False
    non_destructif = True
    renderer = "auto"
    paper_format = "A4"
    columns = 1
    logo: str | None = None
    title: str | None = None
    output_name: str | None = None

    palette_keys = {
        "primaire": ("palette_primaire", "couleur_primaire", "primary", "primary_color"),
        "secondaire": ("palette_secondaire", "couleur_secondaire", "secondary", "secondary_color"),
        "accent": ("palette_accent", "couleur_accent", "accent", "accent_color"),
        "fond": ("palette_fond", "couleur_fond", "background", "background_color"),
        "texte": ("palette_texte", "couleur_texte", "text", "text_color", "couleur_texte_principale"),
    }

    def apply_item(raw_key: object, raw_value: object) -> None:
        nonlocal force, non_destructif, renderer, paper_format, columns, logo, title, output_name
        key = normalize_key(raw_key)
        if key in {"force", "forcer"}:
            parsed = _to_bool(raw_value)
            if parsed is not None:
                force = parsed
            return
        if key in {"non_destructif", "non_destructive", "preserver_donnees"}:
            parsed = _to_bool(raw_value)
            if parsed is False:
                raise DeclarativeTaskError("NON_DESTRUCTIVE_VIOLATION", str(raw_key))
            non_destructif = True
            return
        if key in {"renderer", "moteur_rendu", "moteur_pdf", "pdf_renderer"}:
            normalized = normalize_renderer(raw_value)
            if normalize_label(raw_value) not in {
                "",
                "auto",
                "automatique",
                "automatic",
                "default",
                "weasyprint",
                "weasy",
                "weasy print",
                "reportlab",
                "report lab",
                "pymupdf",
                "fitz",
                "mupdf",
            }:
                warnings.append(f"renderer_inconnu:{raw_value}")
            renderer = normalized
            return
        if key in {"format", "paper_format", "page_format", "format_papier"}:
            paper_format = str(raw_value).strip().upper() or "A4"
            return
        if key in {"colonnes", "columns", "column_count", "nb_colonnes"}:
            columns = _to_int(raw_value, 1)
            if not 1 <= columns <= 4:
                warnings.append(f"colonnes_invalides:{raw_value}")
                columns = 1
            return
        if key in {"logo", "logo_path", "chemin_logo"}:
            logo = str(raw_value).strip() or None
            return
        if key in {"titre", "title", "titre_document"}:
            title = str(raw_value).strip() or None
            return
        if key in {"output", "output_name", "nom_sortie", "fichier_sortie"}:
            output_name = str(raw_value).strip() or None
            return
        if key == "palette":
            if isinstance(raw_value, Mapping):
                for palette_key, palette_value in raw_value.items():
                    normalized_palette_key = _palette_key(palette_key)
                    if normalized_palette_key in palette:
                        color = _color_or_default(palette_value, palette[normalized_palette_key])
                        if color is None:
                            warnings.append(f"couleur_invalide:{palette_value}")
                        else:
                            palette[normalized_palette_key] = color
                    else:
                        extra[f"palette_{normalize_key(palette_key)}"] = str(palette_value)
            else:
                for chunk in re.split(r"[;,]", str(raw_value)):
                    part_key, part_value = split_key_value(chunk)
                    if part_key is None:
                        continue
                    normalized_palette_key = _palette_key(part_key)
                    if normalized_palette_key in palette:
                        color = _color_or_default(part_value, palette[normalized_palette_key])
                        if color is not None:
                            palette[normalized_palette_key] = color
                        else:
                            warnings.append(f"couleur_invalide:{part_value}")
            return
        for palette_key, aliases in palette_keys.items():
            if key in aliases:
                color = _color_or_default(raw_value, palette[palette_key])
                if color is None:
                    warnings.append(f"couleur_invalide:{raw_value}")
                else:
                    palette[palette_key] = color
                return
        if key in {"max_pages", "marge", "margin", "font", "police", "footer", "pied_de_page"}:
            extra[key] = str(raw_value)
            return
        extra[key] = str(raw_value)

    for key, value in options.items():
        apply_item(key, value)
    for step in steps:
        if step.key is None:
            warning_key = normalize_label(step.text)
            if warning_key.startswith("force") and _to_bool(step.text.split(":", 1)[-1]) is True:
                force = True
            continue
        apply_item(step.key, step.value or "")

    return (
        TaskConstraints(
            force=force,
            non_destructif=non_destructif,
            renderer=renderer,
            paper_format=paper_format,
            columns=columns,
            palette=palette,
            logo=logo,
            title=title,
            output_name=output_name,
            extra=extra,
        ),
        tuple(warnings),
    )


_EXPLICIT_WORKFLOW_KEYS = (
    "workflow",
    "workflow_type",
    "type",
    "cible",
    "target",
    "workflow_target",
    "moteur",
    "engine",
    "tache",
)


def _workflow_from_explicit(value: object) -> WorkflowType | None:
    if value is None:
        return None
    key = normalize_label(value)
    if key in {"", "auto", "automatique", "automatic", "detection", "detect"}:
        return None
    if any(marker in key for marker in ("tarif", "excel", "fusion", "prix", "price", "spreadsheet")):
        return WorkflowType.TARIFS_EXCEL
    if any(
        marker in key
        for marker in ("pdf", "plv", "presentation", "refonte", "weasy", "mise en page", "affiche")
    ):
        return WorkflowType.PDF_PRESENTATION
    raise DeclarativeTaskError("INVALID_WORKFLOW", repr(value))


_EXCEL_MARKERS = (
    "tarifs existants xlsx",
    "tarifs-existants.xlsx",
    "tarifs existants",
    "fournisseurs",
    "regles de fusion",
    "regles de prix",
    "prix kg",
    "prix conditionnement",
    "openpyxl",
    "fusion",
    "excel",
    "xlsx",
)
_PDF_MARKERS = (
    "original pdf",
    "original-annote.pdf",
    "original annote",
    "template reference",
    "template-reference.pdf",
    "plv",
    "pdf presentation",
    "refonte",
    "weasyprint",
    "mise en page",
    "presentation",
    "affiche",
    "flyer",
)


def detect_workflow_type(
    text: str,
    frontmatter: Mapping[str, Any] | None = None,
    present_files: Iterable[str] = (),
) -> tuple[WorkflowType, str]:
    """Return ``(workflow, source)`` with deterministic precedence."""
    data = dict(frontmatter or {})
    for key in _EXPLICIT_WORKFLOW_KEYS:
        if key in data:
            explicit = _workflow_from_explicit(data[key])
            if explicit is not None:
                return explicit, f"explicit:{key}"
    options = data.get("options")
    if isinstance(options, Mapping):
        for key in _EXPLICIT_WORKFLOW_KEYS:
            if key in options:
                explicit = _workflow_from_explicit(options[key])
                if explicit is not None:
                    return explicit, f"explicit:options.{key}"

    normalized_text = normalize_label(text)
    names = {str(name).casefold() for name in present_files}
    excel_score = sum(normalized_text.count(normalize_label(marker)) for marker in _EXCEL_MARKERS)
    pdf_score = sum(normalized_text.count(normalize_label(marker)) for marker in _PDF_MARKERS)
    if TARIFS_EXISTANTS.casefold() in names:
        excel_score += 4
    if ORIGINAL_PDF.casefold() in names:
        pdf_score += 4
    if FOURNISSEURS_DIR.casefold() in names:
        excel_score += 2
    if ORIGINAL_ANNOTE_PDF.casefold() in names or TEMPLATE_REFERENCE_PDF.casefold() in names:
        pdf_score += 2

    if normalized_text.count(normalize_label(TARIFS_EXISTANTS)) > 0:
        excel_score += 4
    if normalized_text.count(normalize_label(ORIGINAL_PDF)) > 0:
        pdf_score += 4

    if excel_score == 0 and pdf_score == 0:
        return WorkflowType.PDF_PRESENTATION, "default"
    if excel_score >= pdf_score:
        return WorkflowType.TARIFS_EXCEL, "keywords"
    return WorkflowType.PDF_PRESENTATION, "keywords"


def parse_instructions(
    text: str,
    *,
    present_files: Iterable[str] = (),
    source_path: str | Path | None = None,
    strict: bool = False,
) -> ParsedInstructions:
    """Parse ``instructions.md`` deterministically and tolerantly."""
    if not isinstance(text, str):
        raise DeclarativeTaskError("INVALID_INSTRUCTIONS", repr(text))
    if not text.strip():
        raise DeclarativeTaskError("EMPTY_INSTRUCTIONS", "instructions.md is empty")
    frontmatter, body = _split_frontmatter(text)
    sections, extra_sections = _parse_sections(body)
    warnings: list[str] = []

    modifications, mod_warnings = parse_content_modifications(
        sections.get(InstructionSection.MODIFICATIONS_CONTENU.value, ()), strict=strict
    )
    merge_rules, merge_warnings = parse_merge_rules(
        sections.get(InstructionSection.REGLES_FUSION.value, ()), _option_items(frontmatter)
    )
    price_rules, price_warnings = parse_price_rules(
        sections.get(InstructionSection.REGLES_PRIX.value, ())
    )
    constraints, constraint_warnings = parse_constraints(
        sections.get(InstructionSection.CONTRAINTES.value, ()), frontmatter
    )
    workflow, workflow_source = detect_workflow_type(body, frontmatter, present_files)

    warnings.extend(mod_warnings)
    warnings.extend(merge_warnings)
    warnings.extend(price_warnings)
    warnings.extend(constraint_warnings)
    if workflow is WorkflowType.TARIFS_EXCEL and modifications:
        warnings.append("modifications_pdf_ignorees")
    if workflow is WorkflowType.PDF_PRESENTATION and (
        sections.get(InstructionSection.REGLES_FUSION.value)
        or sections.get(InstructionSection.REGLES_PRIX.value)
    ):
        warnings.append("regles_tarifs_ignorees")

    target = ""
    for key in ("titre", "title", "cible", "target"):
        if key in frontmatter and frontmatter[key] is not None:
            target = str(frontmatter[key])
            break

    version = str(frontmatter.get("version", "1.0"))
    resolved_source = Path(source_path) if source_path is not None else None
    return ParsedInstructions(
        version=version,
        target=target,
        workflow=workflow,
        workflow_source=workflow_source,
        frontmatter=frontmatter,
        sections=sections,
        extra_sections=extra_sections,
        modifications=modifications,
        merge_rules=merge_rules,
        price_rules=price_rules,
        constraints=constraints,
        warnings=tuple(dict.fromkeys(warnings)),
        source_path=resolved_source,
        raw_text=text,
    )


def ensure_task_folder(root: str | Path) -> TaskFolderLayout:
    """Create the canonical input/output folders and return the layout."""
    root_path = Path(root)
    try:
        root_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DeclarativeTaskError("INVALID_TASK_FOLDER", str(root)) from exc
    folder = build_task_folder(root_path)
    for directory in (folder.fournisseurs, folder.assets, folder.output, folder.log):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DeclarativeTaskError("INVALID_TASK_FOLDER", str(directory)) from exc
    return folder


def load_task(root: str | Path, *, strict: bool = False) -> DeclarativeTask:
    """Read ``<root>/instructions.md`` and bind it to the folder layout."""
    folder = build_task_folder(root)
    if not folder.instructions.is_file():
        raise DeclarativeTaskError("INSTRUCTIONS_NOT_FOUND", str(folder.instructions))
    try:
        text = folder.instructions.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise DeclarativeTaskError("INSTRUCTIONS_UNREADABLE", str(folder.instructions)) from exc
    brief = parse_instructions(
        text,
        present_files=folder.existing_inputs(),
        source_path=folder.instructions,
        strict=strict,
    )
    return DeclarativeTask(folder=folder, brief=brief)


def load_instructions(
    path: str | Path, *, strict: bool = False
) -> ParsedInstructions:
    """Load a parsed brief from a folder or a direct ``instructions.md`` path."""
    target = Path(path)
    if target.is_dir():
        return load_task(target, strict=strict).brief
    if not target.is_file():
        raise DeclarativeTaskError("INSTRUCTIONS_NOT_FOUND", str(target))
    try:
        text = target.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise DeclarativeTaskError("INSTRUCTIONS_UNREADABLE", str(target)) from exc
    present = tuple(item.name for item in target.parent.iterdir())
    return parse_instructions(
        text, present_files=present, source_path=target, strict=strict
    )


parse_instructions_file = load_instructions
discover_task_folder = build_task_folder


def detect_workflow(
    text: str,
    frontmatter: Mapping[str, Any] | None = None,
    present_files: Iterable[str] = (),
) -> WorkflowType:
    """Return only the detected workflow (see :func:`detect_workflow_type`)."""
    return detect_workflow_type(text, frontmatter, present_files)[0]
