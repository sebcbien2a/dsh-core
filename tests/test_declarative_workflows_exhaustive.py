"""P15.2 - Exhaustive offline tests of the declarative folder workflows.

Covers the task-folder contracts and the tolerant ``instructions.md`` parser
(workflow detection, sections, merge/price/constraints rules, anti-traversal
paths), the absolute non-deletion guarantee of the Excel tariff merge
(``ancien_prix``, bidirectional conditioning <-> kg completion, merge repair,
formatting, atomic output and audit log) and the end-to-end PDF / PLV
redesign workflow (PyMuPDF extraction, declarative operations, HTML template,
renderer injection, WeasyPrint -> ReportLab fallback and native PyMuPDF).
"""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pymupdf
import pytest
from openpyxl import Workbook, load_workbook
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from schemas.declarative_task_schema import (
    ALERT_MISSING_PRICE,
    ASSETS_DIR,
    FOURNISSEURS_DIR,
    INSTRUCTIONS_FILE,
    LOG_DIR,
    ORIGINAL_PDF,
    OUTPUT_DIR,
    STATUS_NEW,
    STATUS_UNCHANGED,
    STATUS_UPDATED,
    TARIFS_EXISTANTS,
    WorkflowType,
    DeclarativeTaskError,
    InstructionSection,
    ModificationOperation,
    build_task_folder,
    ensure_contained,
    load_task,
    parse_instructions,
    parse_merge_rules,
    parse_price_rules,
)
from services.declarative_pdf_service import (
    DeclarativePdfService,
    PdfWorkflowError,
    PyMuPdfRenderer,
    ReportLabRenderer,
    WeasyPrintRenderer,
    apply_content_modifications,
    build_presentation_html,
    renderer_candidates,
)
from services.declarative_tarif_service import (
    DeclarativeTarifService,
    TarifWorkflowError,
    article_key,
    complete_prices,
    extract_supplier_prices,
    normalize_article_code,
    parse_supplier_text,
    round_price,
)
from services.instructions_task_engine import InstructionsTaskEngine, run_instructions_task

FIXED_NOW = datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc)
DAY = "2026-11-03"
LOGO_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Z0ZkAAAAASUVORK5CYII="
)


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_pdf(path: Path, lines, pagesize=A4) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = canvas.Canvas(str(path), pagesize=pagesize)
    y = pagesize[1] - 50
    for line in lines:
        document.drawString(40, y, str(line))
        y -= 18
    document.save()
    return path


def supplier_pdf(path: Path, rows, header=None) -> Path:
    header = header or [
        "code",
        "designation",
        "poids",
        "prix_conditionnement",
        "prix_kg",
    ]
    lines = [" | ".join(header)]
    for row in rows:
        lines.append(" | ".join("" if value is None else str(value) for value in row))
    return make_pdf(path, lines)


def create_tarif_workbook(path: Path, merges=()) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Tarifs"
    sheet.append(
        [
            "code",
            "designation",
            "poids",
            "prix_conditionnement",
            "prix_kg",
            "statut",
            "commentaire",
            "extra",
        ]
    )
    sheet.append(["ART001", "Peinture blanche", 5, 40, 8, None, None, None])
    sheet.append(["ART002", "Peinture noire", 2, 10, 5, None, None, None])
    sheet.append(["OLD001", "Ancien produit", 2, 20, 10, None, "à conserver", None])
    sheet.append(["12345", "Produit numérique", 3, 30, 10, None, None, None])
    sheet.append([None, "Sans code", 1, 5, 2, None, None, None])
    sheet.merge_cells("G1:H1")
    sheet["G1"] = "commentaire"
    for merge in merges:
        sheet.merge_cells(merge)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


def tarif_instructions(**overrides) -> str:
    workflow = overrides.get("workflow", "TARIFS_EXCEL")
    return f"""---
version: "1.0"
workflow: {workflow}
options:
  force: false
---
# Instructions

## Source / Fichiers
- {TARIFS_EXISTS_XLSX}
- fournisseurs/*.pdf

## Règles de fusion
- clé: code
- conserver_lignes_absentes: true
- ancien_prix: true
- date_ajout: true

## Règles de prix
- arrondi: 2
- devise: EUR

## Contraintes
- non_destructif: true
"""


TARIFS_EXISTS_XLSX = TARIFS_EXISTANTS


def prepare_tarif_project(tmp_path: Path, supplier_rows=None) -> Path:
    root = tmp_path / "tarifs-projet"
    root.mkdir(parents=True, exist_ok=True)
    create_tarif_workbook(root / TARIFS_EXISTANTS, merges=("G4:H4",))
    write_text(root / INSTRUCTIONS_FILE, tarif_instructions())
    (root / FOURNISSEURS_DIR).mkdir(parents=True, exist_ok=True)
    supplier_pdf(
        root / FOURNISSEURS_DIR / "fournisseur-a.pdf",
        supplier_rows
        if supplier_rows is not None
        else [
            ["ART001", "Peinture blanche premium", 5, 50, None],
            ["ART002", "Peinture noire", 2, 10, 5],
            ["ART003", "Nouveau produit", 1, 12.5, None],
            ["ART004", "Sans prix", 1, None, None],
            ["00999", "Code avec zéros", 2, 24, None],
            ["ART005", "Kg seulement", 4, None, 3],
        ],
    )
    return root


def pdf_instructions(renderer: str = "reportlab", logo: str = "assets/logo.png") -> str:
    return f"""---
version: "1.0"
workflow: PDF_PRESENTATION
options:
  titre: "Nouvelle PLV"
  colonnes: 2
  logo: {logo}
  palette:
    primaire: "#112233"
    secondaire: "#445566"
    accent: "#FF6600"
    fond: "#FFFFFF"
    texte: "#101010"
---
# Instructions

## Modifications de contenu
- supprimer: "Ancien slogan"
- remplacer: "Titre original" -> "Titre modernisé"
- ajouter: "Mention légale ajoutée"

## Contraintes
- renderer: {renderer}
- non_destructif: true
"""


def prepare_pdf_project(tmp_path: Path, instructions: str | None = None) -> Path:
    root = tmp_path / "pdf-projet"
    root.mkdir(parents=True, exist_ok=True)
    make_pdf(
        root / ORIGINAL_PDF,
        [
            "Titre original",
            "Ancien slogan",
            "Conserver ceci",
            "Prix 10 EUR",
        ],
    )
    (root / ASSETS_DIR).mkdir(parents=True, exist_ok=True)
    (root / ASSETS_DIR / "logo.png").write_bytes(LOGO_PNG)
    write_text(root / INSTRUCTIONS_FILE, instructions or pdf_instructions())
    return root


def sheet_rows(path: Path, sheet_name: str = "Tarifs") -> tuple[list[str], list[dict]]:
    workbook = load_workbook(path)
    sheet = workbook[sheet_name]
    headers = [
        sheet.cell(row=1, column=column).value
        for column in range(1, sheet.max_column + 1)
    ]
    rows = []
    for row_number in range(2, sheet.max_row + 1):
        values = {}
        for index, header in enumerate(headers, start=1):
            values[str(header)] = sheet.cell(row=row_number, column=index).value
        if any(value is not None and str(value).strip() != "" for value in values.values()):
            values["_row"] = row_number
            rows.append(values)
    return [str(header) for header in headers], rows


def row_index(path: Path) -> dict[str, int]:
    workbook = load_workbook(path)
    sheet = workbook["Tarifs"]
    headers = [
        str(sheet.cell(row=1, column=column).value or "")
        for column in range(1, sheet.max_column + 1)
    ]
    return {header: index + 1 for index, header in enumerate(headers)}


# -- path safety & folder layout ---------------------------------------------


def test_ensure_contained_accepts_nested_path(tmp_path):
    assert ensure_contained(tmp_path, tmp_path / OUTPUT_DIR / "x.pdf") == (
        tmp_path / OUTPUT_DIR / "x.pdf"
    ).resolve()


def test_ensure_contained_rejects_parent_traversal(tmp_path):
    with pytest.raises(DeclarativeTaskError) as info:
        ensure_contained(tmp_path, tmp_path / ".." / "evil.txt")
    assert info.value.code == "PATH_TRAVERSAL"


def test_ensure_contained_rejects_absolute_outside(tmp_path):
    with pytest.raises(DeclarativeTaskError) as info:
        ensure_contained(tmp_path, tmp_path.parent / "outside.txt")
    assert info.value.code == "PATH_TRAVERSAL"


def test_build_task_folder_layout_and_supplier_sort(tmp_path):
    root = tmp_path / "projet"
    (root / FOURNISSEURS_DIR).mkdir(parents=True)
    for name in ("b.pdf", "a.pdf", "ignore.txt"):
        (root / FOURNISSEURS_DIR / name).write_bytes(b"x")
    folder = build_task_folder(root)
    assert folder.instructions == (root / INSTRUCTIONS_FILE).resolve()
    assert folder.original_pdf == (root / ORIGINAL_PDF).resolve()
    assert folder.output == (root / OUTPUT_DIR).resolve()
    assert folder.log == (root / LOG_DIR).resolve()
    assert [path.name for path in folder.supplier_pdfs()] == ["a.pdf", "b.pdf"]
    assert FOURNISSEURS_DIR in folder.existing_inputs()


def test_build_task_folder_rejects_a_file(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(DeclarativeTaskError) as info:
        build_task_folder(target)
    assert info.value.code == "INVALID_TASK_FOLDER"


def test_load_task_missing_instructions(tmp_path):
    root = tmp_path / "vide"
    root.mkdir()
    with pytest.raises(DeclarativeTaskError) as info:
        load_task(root)
    assert info.value.code == "INSTRUCTIONS_NOT_FOUND"


def test_task_folder_model_rejects_outside_path(tmp_path):
    from schemas.declarative_task_schema import TaskFolderLayout

    root = tmp_path / "projet"
    root.mkdir()
    folder = build_task_folder(root)
    payload = folder.model_dump()
    payload["instructions"] = tmp_path / "outside.md"
    with pytest.raises(Exception) as info:
        TaskFolderLayout(**payload)
    assert "PATH_TRAVERSAL" in str(info.value) or "PATH_TRAVERSAL" in repr(info.value)


# -- instructions parser -----------------------------------------------------


def test_parse_sections_with_accents_and_slashes():
    brief = parse_instructions(
        """---
workflow: PDF_PRESENTATION
---
## Source/Fichiers
- original.pdf
## Modifications de contenu
- supprimer: "Ancien"
## RÈGLES DE FUSION
- clé: code
## Règles de prix
- arrondi: 2
## Contraintes
- non_destructif: true
"""
    )
    assert brief.step_texts(InstructionSection.SOURCE_FICHIERS) == ("original.pdf",)
    assert brief.step_texts(InstructionSection.MODIFICATIONS_CONTENU) == (
        'supprimer: "Ancien"',
    )
    assert brief.step_texts(InstructionSection.REGLES_FUSION) == ("clé: code",)
    assert brief.step_texts(InstructionSection.REGLES_PRIX) == ("arrondi: 2",)
    assert brief.constraints.non_destructif is True


def test_parse_numbered_section_headings():
    brief = parse_instructions(
        """---
workflow: TARIFS_EXCEL
---
## 1. Source / Fichiers
- tarifs-existants.xlsx
## 2) Règles de fusion
- clé: code
"""
    )
    assert brief.step_texts(InstructionSection.SOURCE_FICHIERS) == ("tarifs-existants.xlsx",)
    assert brief.merge_rules.match_key == "code"


def test_parse_target_frontmatter_key():
    brief = parse_instructions(
        """---
workflow: TARIFS_EXCEL
target: tarifs fournisseurs
---
## Contraintes
- force: false
"""
    )
    assert brief.workflow is WorkflowType.TARIFS_EXCEL
    assert brief.target == "tarifs fournisseurs"


def test_explicit_workflow_wins_over_text():
    brief = parse_instructions(
        """---
workflow: TARIFS_EXCEL
---
## Modifications de contenu
- supprimer: "original.pdf"
"""
    )
    assert brief.workflow is WorkflowType.TARIFS_EXCEL
    assert brief.workflow_source.startswith("explicit:")


def test_detect_pdf_workflow_from_content():
    brief = parse_instructions(
        """## Source / Fichiers
- original.pdf
## Modifications de contenu
- remplacer: "A" -> "B"
"""
    )
    assert brief.workflow is WorkflowType.PDF_PRESENTATION


def test_detect_tarif_workflow_from_present_files():
    brief = parse_instructions(
        "## Contraintes\n- non_destructif: true\n",
        present_files=(TARIFS_EXISTANTS,),
    )
    assert brief.workflow is WorkflowType.TARIFS_EXCEL
    assert brief.workflow_source == "keywords"


def test_invalid_explicit_workflow_is_rejected():
    with pytest.raises(DeclarativeTaskError) as info:
        parse_instructions("---\nworkflow: inconnu\n---\n## Contraintes\n- force: false\n")
    assert info.value.code == "INVALID_WORKFLOW"


def test_parse_frontmatter_options_and_palette():
    brief = parse_instructions(
        """---
version: "2.0"
workflow: PDF_PRESENTATION
options:
  titre: Catalogue
  colonnes: 2
  force: true
  logo: assets/logo.png
  palette:
    primary: "#112233"
    accent_color: "#FF6600"
---
## Contraintes
- non_destructif: true
"""
    )
    assert brief.version == "2.0"
    assert brief.constraints.title == "Catalogue"
    assert brief.constraints.columns == 2
    assert brief.constraints.force is True
    assert brief.constraints.logo == "assets/logo.png"
    assert brief.constraints.palette["primaire"] == "#112233"
    assert brief.constraints.palette["accent"] == "#FF6600"
    assert "options" not in brief.constraints.extra


def test_parse_merge_rules_defaults_and_flags():
    brief = parse_instructions(tarif_instructions())
    assert brief.merge_rules.match_key == "code"
    assert brief.merge_rules.preserve_missing is True
    assert brief.merge_rules.track_ancien_prix is True
    assert brief.merge_rules.added_date is True


def test_parse_merge_rules_forbids_line_deletion():
    text = """---
workflow: TARIFS_EXCEL
---
## Règles de fusion
- supprimer_lignes_absentes: true
"""
    with pytest.raises(DeclarativeTaskError) as info:
        parse_instructions(text)
    assert info.value.code == "NON_DESTRUCTIVE_VIOLATION"


def test_parse_merge_rules_forbids_preserve_false():
    text = """---
workflow: TARIFS_EXCEL
---
## Règles de fusion
- preserve_missing: false
"""
    with pytest.raises(DeclarativeTaskError) as info:
        parse_instructions(text)
    assert info.value.code == "NON_DESTRUCTIVE_VIOLATION"


def test_parse_constraints_forbids_non_destructif_false():
    text = """---
workflow: PDF_PRESENTATION
---
## Contraintes
- non_destructif: false
"""
    with pytest.raises(DeclarativeTaskError) as info:
        parse_instructions(text)
    assert info.value.code == "NON_DESTRUCTIVE_VIOLATION"


def test_parse_price_rules_custom_columns():
    rules, warnings = parse_price_rules(
        parse_instructions(
            """---
workflow: TARIFS_EXCEL
---
## Règles de prix
- arrondi: 3
- devise: chf
- colonne_poids: masse
- prix_kg = prix_conditionnement / poids
"""
        ).steps(InstructionSection.REGLES_PRIX)
    )
    assert rules.decimals == 3
    assert rules.currency == "CHF"
    assert rules.weight_column == "masse"
    assert any(warning.startswith("regle_prix_ignoree:") for warning in warnings)


def test_parse_content_modifications_delete_replace_add():
    brief = parse_instructions(
        """---
workflow: PDF_PRESENTATION
---
## Modifications de contenu
- supprimer: "Ancien slogan"
- remplacer: "Titre original" -> "Titre modernisé"
- ajouter: "Mention légale" après "Titre modernisé"
"""
    )
    assert [item.operation for item in brief.modifications] == [
        ModificationOperation.SUPPRIMER,
        ModificationOperation.REMPLACER,
        ModificationOperation.AJOUTER,
    ]
    assert brief.modifications[0].target == "Ancien slogan"
    assert brief.modifications[1].target == "Titre original"
    assert brief.modifications[1].replacement == "Titre modernisé"
    assert brief.modifications[2].replacement == "Mention légale"
    assert brief.modifications[2].position == "after"
    assert brief.modifications[2].anchor == "Titre modernisé"


def test_parse_unknown_modification_is_tolerated():
    brief = parse_instructions(
        """---
workflow: PDF_PRESENTATION
---
## Modifications de contenu
- faire quelque chose de non prévu
"""
    )
    assert brief.modifications == ()
    assert any(warning.startswith("modification_ignoree:") for warning in brief.warnings)
    with pytest.raises(DeclarativeTaskError) as info:
        parse_instructions(
            """---
workflow: PDF_PRESENTATION
---
## Modifications de contenu
- faire quelque chose de non prévu
""",
            strict=True,
        )
    assert info.value.code == "INVALID_MODIFICATION"


def test_parse_empty_instructions_is_rejected():
    with pytest.raises(DeclarativeTaskError) as info:
        parse_instructions("   \n")
    assert info.value.code == "EMPTY_INSTRUCTIONS"


def test_parse_invalid_frontmatter_is_rejected():
    with pytest.raises(DeclarativeTaskError) as info:
        parse_instructions("---\nworkflow: PDF_PRESENTATION\n## Contraintes\n- force: true\n")
    assert info.value.code == "INVALID_FRONTMATTER"


# -- price model helpers -----------------------------------------------------


def test_round_price_uses_standard_half_up():
    assert round_price(2.675, 2) == 2.68
    assert round_price(1.005, 2) == 1.01
    assert round_price(10 / 3, 2) == 3.33


def test_complete_prices_deduces_kg_from_conditioning():
    record = complete_prices("ART", weight=4, packaging_price=10)
    assert record.kg_price == 2.5
    assert record.alert == ""


def test_complete_prices_deduces_conditioning_from_kg():
    record = complete_prices("ART", weight=4, kg_price=2.5)
    assert record.packaging_price == 10.0
    assert record.alert == ""


def test_complete_prices_missing_both_prices_alerts():
    record = complete_prices("ART", weight=4)
    assert record.packaging_price is None
    assert record.kg_price is None
    assert record.alert == ALERT_MISSING_PRICE


def test_complete_prices_one_price_without_weight_alerts():
    record = complete_prices("ART", packaging_price=10)
    assert record.kg_price is None
    assert record.alert == ALERT_MISSING_PRICE


def test_normalize_article_code_preserves_text_and_leading_zeros():
    assert normalize_article_code(12345) == "12345"
    assert normalize_article_code(12345.0) == "12345"
    assert normalize_article_code("00999") == "00999"
    assert article_key(" ART001 ") == "art001"


def test_parse_supplier_text_pipe_header():
    records = parse_supplier_text(
        "code | designation | poids | prix_conditionnement | prix_kg\n"
        "ART001 | Peinture | 5 | 50 |\n"
        "ART002 | Sans prix | 2 | |\n",
        source="fournisseur.pdf",
    )
    assert len(records) == 2
    by_code = {record.code: record for record in records}
    assert by_code["ART001"].kg_price == 10.0
    assert by_code["ART002"].alert == ALERT_MISSING_PRICE


def test_parse_supplier_text_single_space_fallback():
    records = parse_supplier_text(
        "ART010 Peinture blanche 4 20 5\n",
        source="fournisseur.pdf",
    )
    assert len(records) == 1
    record = records[0]
    assert record.code == "ART010"
    assert record.weight == 4
    assert record.packaging_price == 20
    assert record.kg_price == 5
    assert record.designation == "Peinture blanche"
    spaced = parse_supplier_text(
        "ART011  Peinture  blanche  premium  4  20  5\n",
        source="fournisseur.pdf",
    )
    assert len(spaced) == 1
    assert spaced[0].code == "ART011"
    assert spaced[0].designation == "Peinture blanche premium"
    assert spaced[0].weight == 4
    assert spaced[0].packaging_price == 20
    assert spaced[0].kg_price == 5


def test_extract_supplier_prices_merges_complementary_duplicates(tmp_path):
    first = tmp_path / "a.pdf"
    second = tmp_path / "b.pdf"
    supplier_pdf(first, [["ART100", "Produit", 2, 10, None]])
    supplier_pdf(second, [["ART100", "Produit", 2, None, 5]])
    records, warnings = extract_supplier_prices(sorted([first, second]))
    assert len(records) == 1
    assert records[0].packaging_price == 10
    assert records[0].kg_price == 5
    assert warnings == ()


# -- tariff workflow: end-to-end non-destructive merge ------------------------


def _run_tarif(tmp_path: Path):
    root = prepare_tarif_project(tmp_path)
    source = root / TARIFS_EXISTANTS
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    result = DeclarativeTarifService(
        root, clock=lambda: FIXED_NOW
    ).run()
    after = hashlib.sha256(source.read_bytes()).hexdigest()
    return root, source, before, after, result


def test_tarif_source_workbook_is_never_modified(tmp_path):
    _, _, before, after, _ = _run_tarif(tmp_path)
    assert before == after


def test_tarif_output_audit_log_and_summary_sheet(tmp_path):
    root, _, _, _, result = _run_tarif(tmp_path)
    assert result.output_path == (root / OUTPUT_DIR / "tarifs-mis-a-jour.xlsx").resolve()
    assert result.log_path == (root / LOG_DIR / f"{DAY}.md").resolve()
    assert result.output_path.is_file()
    assert result.log_path.is_file()
    audit = result.log_path.read_text(encoding="utf-8")
    assert "Garantie : aucune ligne existante n'a été supprimée." in audit
    assert "ART001" in audit
    assert "modifié" in audit
    assert "prix manquant" in audit
    workbook = load_workbook(result.output_path, data_only=True)
    assert workbook.sheetnames[0] == "Résumé"
    assert "Tarifs" in workbook.sheetnames
    summary = workbook["Résumé"]
    assert summary["B3"].value == DAY
    assert summary["B8"].value == 4
    assert summary["B9"].value == 1
    assert summary["B10"].value == 4
    assert summary["B11"].value == 9
    assert summary["B13"].value == 1


def test_tarif_never_deletes_existing_rows(tmp_path):
    _, _, _, _, result = _run_tarif(tmp_path)
    _, rows = sheet_rows(result.output_path)
    codes = [str(row.get("code") or "") for row in rows]
    assert "OLD001" in codes
    assert "" in codes
    assert len(rows) == 9
    by_code = {str(row.get("code") or ""): row for row in rows}
    old = by_code["OLD001"]
    assert old["statut"] == STATUS_UNCHANGED
    assert old["designation"] == "Ancien produit"
    assert old["prix_conditionnement"] == 20
    blank = [row for row in rows if not row.get("code")][0]
    assert blank["designation"] == "Sans code"
    assert blank["statut"] == STATUS_UNCHANGED
    assert result.counts["existantes"] == 5
    assert result.counts["total"] == 9


def test_tarif_updates_modified_rows_and_keeps_ancien_prix(tmp_path):
    _, _, _, _, result = _run_tarif(tmp_path)
    _, rows = sheet_rows(result.output_path)
    by_code = {str(row.get("code") or ""): row for row in rows}
    updated = by_code["ART001"]
    assert updated["statut"] == STATUS_UPDATED
    assert updated["ancien_prix"] == 40
    assert updated["prix_conditionnement"] == 50
    assert updated["prix_kg"] == 10
    unchanged = by_code["ART002"]
    assert unchanged["statut"] == STATUS_UNCHANGED
    assert unchanged["prix_conditionnement"] == 10
    assert unchanged["prix_kg"] == 5
    assert by_code["12345"]["statut"] == STATUS_UNCHANGED


def test_tarif_adds_new_codes_with_date_and_alert(tmp_path):
    _, _, _, _, result = _run_tarif(tmp_path)
    _, rows = sheet_rows(result.output_path)
    by_code = {str(row.get("code") or ""): row for row in rows}
    assert by_code["ART003"]["statut"] == STATUS_NEW
    assert by_code["ART003"]["date_ajout"] == DAY
    assert by_code["ART003"]["prix_kg"] == 12.5
    assert by_code["ART004"]["statut"] == STATUS_NEW
    assert by_code["ART004"]["alerte"] == ALERT_MISSING_PRICE
    assert by_code["00999"]["statut"] == STATUS_NEW
    assert by_code["ART005"]["statut"] == STATUS_NEW
    assert by_code["ART005"]["prix_conditionnement"] == 12
    assert by_code["ART005"]["prix_kg"] == 3


def test_tarif_unmerges_data_rows_and_keeps_header_merge(tmp_path):
    _, _, _, _, result = _run_tarif(tmp_path)
    workbook = load_workbook(result.output_path)
    sheet = workbook["Tarifs"]
    ranges = [str(item) for item in sheet.merged_cells.ranges]
    assert "G1:H1" in ranges
    assert all("G4" not in item and "G5" not in item for item in ranges)
    assert all(
        workbook["Tarifs"].cell(row=item.min_row, column=item.min_col).row <= 1
        for item in sheet.merged_cells.ranges
    )
    _, rows = sheet_rows(result.output_path)
    old = [row for row in rows if row.get("code") == "OLD001"][0]
    assert old["commentaire"] == "à conserver"


def test_tarif_formatting_borders_and_text_codes(tmp_path):
    _, _, _, _, result = _run_tarif(tmp_path)
    workbook = load_workbook(result.output_path)
    sheet = workbook["Tarifs"]
    assert sheet["A1"].border.bottom.style == "double"
    index = row_index(result.output_path)
    code_column = index["code"]
    code_rows = {
        str(sheet.cell(row=row, column=code_column).value): row
        for row in range(2, sheet.max_row + 1)
        if sheet.cell(row=row, column=code_column).value is not None
    }
    for code in ("ART001", "12345", "00999"):
        row = code_rows[code]
        cell = sheet.cell(row=row, column=code_column)
        assert cell.number_format == "@"
        assert cell.data_type == "s"
    data_cell = sheet.cell(row=code_rows["ART001"], column=code_column)
    assert data_cell.border.left.style == "thin"
    empty_header_cell = sheet.cell(row=code_rows["ART002"], column=index["ancien_prix"])
    assert empty_header_cell.value is None
    assert empty_header_cell.border.left is None or empty_header_cell.border.left.style is None


def test_tarif_audit_log_appends_same_day_runs(tmp_path):
    root, _, _, _, first = _run_tarif(tmp_path)
    second = DeclarativeTarifService(root, clock=lambda: FIXED_NOW).run()
    content = second.log_path.read_text(encoding="utf-8")
    assert content.count("# Journal d'audit — fusion tarifs") == 2
    assert first.log_path == second.log_path


def test_tarif_wrong_workflow_is_rejected(tmp_path):
    root = tmp_path / "pdf"
    root.mkdir()
    write_text(root / INSTRUCTIONS_FILE, pdf_instructions())
    with pytest.raises(TarifWorkflowError) as info:
        DeclarativeTarifService(root).run()
    assert info.value.code == "WRONG_WORKFLOW"


def test_tarif_missing_source_workbook_is_rejected(tmp_path):
    root = tmp_path / "tarifs"
    root.mkdir()
    write_text(root / INSTRUCTIONS_FILE, tarif_instructions())
    with pytest.raises(TarifWorkflowError) as info:
        DeclarativeTarifService(root).run()
    assert info.value.code == "TARIFS_NOT_FOUND"


# -- PDF workflow ------------------------------------------------------------


def test_apply_content_modifications_order_anchor_and_counts():
    modifications = parse_instructions(
        """---
workflow: PDF_PRESENTATION
---
## Modifications de contenu
- remplacer: "A" -> "B"
- supprimer: "B"
- ajouter: "C" après "D"
"""
    ).modifications
    text, counts, warnings = apply_content_modifications("A D", modifications)
    assert text == "D\n\nC"
    assert counts["remplacer"] == 1
    assert counts["supprimer"] == 1
    assert counts["ajouter"] == 1
    assert warnings == ()


def test_parse_add_at_start_sets_position():
    brief = parse_instructions(
        """---
workflow: PDF_PRESENTATION
---
## Modifications de contenu
- ajouter au début: "En-tête"
"""
    )
    assert len(brief.modifications) == 1
    assert brief.modifications[0].operation is ModificationOperation.AJOUTER
    assert brief.modifications[0].replacement == "En-tête"
    assert brief.modifications[0].position == "start"


def test_apply_content_modifications_reports_missing_targets():
    modifications = parse_instructions(
        """---
workflow: PDF_PRESENTATION
---
## Modifications de contenu
- supprimer: "absent"
- ajouter: "C" après "ancre absente"
"""
    ).modifications
    text, counts, warnings = apply_content_modifications("D", modifications)
    assert text == "D\n\nC"
    assert counts["supprimer"] == 0
    assert counts["ajouter"] == 1
    assert any(warning.startswith("cible_introuvable:supprimer:") for warning in warnings)
    assert any(warning.startswith("ancre_introuvable:ajouter:") for warning in warnings)


def test_pdf_html_template_injects_palette_columns_and_logo(tmp_path):
    logo = tmp_path / "logo.png"
    logo.write_bytes(LOGO_PNG)
    html = build_presentation_html(
        title="Titre <test>",
        paragraphs=["Un <script>alert(1)</script>", "Deuxième paragraphe"],
        palette={
            "primaire": "#112233",
            "secondaire": "#445566",
            "accent": "#FF6600",
            "fond": "#FFFFFF",
            "texte": "#101010",
        },
        columns=2,
        logo_path=logo,
        paper_format="A4",
    )
    assert "column-count: 2" in html
    assert "#FF6600" in html
    assert "#112233" in html
    assert "logo.png" in html
    assert "Titre &lt;test&gt;" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>" not in html


def test_pdf_workflow_end_to_end_reportlab(tmp_path):
    root = prepare_pdf_project(tmp_path)
    result = DeclarativePdfService(
        root, renderer="reportlab", clock=lambda: FIXED_NOW
    ).run()
    assert result.renderer == "reportlab"
    assert result.output_path == (root / OUTPUT_DIR / "resultat.pdf").resolve()
    assert result.html_path == (root / OUTPUT_DIR / "resultat.html").resolve()
    assert result.log_path == (root / LOG_DIR / f"{DAY}.md").resolve()
    assert result.output_path.read_bytes().startswith(b"%PDF")
    assert result.output_path.stat().st_size > 500
    html = result.html_path.read_text(encoding="utf-8")
    assert "column-count: 2" in html
    assert "#FF6600" in html
    assert "logo.png" in html
    document = pymupdf.open(result.output_path)
    text = "\n".join(page.get_text("text") for page in document)
    document.close()
    assert "Titre modernisé" in text
    assert "Ancien slogan" not in text
    assert "Conserver ceci" in text
    assert "Mention légale ajoutée" in text
    assert result.modification_counts["supprimer"] == 1
    assert result.modification_counts["remplacer"] == 1
    assert result.modification_counts["ajouter"] == 1
    audit = result.log_path.read_text(encoding="utf-8")
    assert "Renderers :" not in audit
    assert "Renderer : reportlab" in audit
    assert "Suppressions : 1" in audit
    assert "Remplacements : 1" in audit
    assert "Ajouts : 1" in audit


class FakeRenderer:
    name = "fake"

    def __init__(self) -> None:
        self.presentations = []

    def render(self, presentation, output_path):
        self.presentations.append(presentation)
        output_path.write_bytes(b"%PDF-1.4\n%%EOF\n")


def test_pdf_injected_renderer_receives_template(tmp_path):
    root = prepare_pdf_project(tmp_path)
    renderer = FakeRenderer()
    result = DeclarativePdfService(
        root, renderer=renderer, clock=lambda: FIXED_NOW
    ).run()
    assert result.renderer == "fake"
    assert result.output_path.read_bytes().startswith(b"%PDF")
    assert len(renderer.presentations) == 1
    presentation = renderer.presentations[0]
    assert presentation.columns == 2
    assert presentation.palette["accent"] == "#FF6600"
    assert presentation.logo_path is not None
    assert "column-count: 2" in presentation.html
    assert "Titre modernisé" in presentation.source_text


def test_pdf_callable_renderer_injection(tmp_path):
    root = prepare_pdf_project(tmp_path)
    captured = []

    def renderer(html, output_path):
        captured.append(html)
        output_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    result = DeclarativePdfService(
        root, renderer=renderer, clock=lambda: FIXED_NOW
    ).run()
    assert result.renderer == "callable"
    assert result.output_path.read_bytes().startswith(b"%PDF")
    assert captured and "Titre modernisé" in captured[0]
    assert "#FF6600" in captured[0]


def test_pdf_weasyprint_failure_falls_back_to_reportlab(tmp_path, monkeypatch):
    root = prepare_pdf_project(tmp_path)

    def broken(self, presentation, output_path):
        raise RuntimeError("GTK unavailable")

    monkeypatch.setattr(WeasyPrintRenderer, "render", broken)
    result = DeclarativePdfService(
        root, renderer="auto", clock=lambda: FIXED_NOW
    ).run()
    assert result.renderer == "reportlab"
    assert result.output_path.read_bytes().startswith(b"%PDF")
    assert any(warning.startswith("renderer_echec:weasyprint:") for warning in result.warnings)


def test_pdf_native_pymupdf_renderer_produces_pdf(tmp_path):
    root = prepare_pdf_project(tmp_path, instructions=pdf_instructions(logo=""))
    presentation, _, _, _ = DeclarativePdfService(root).build_presentation()
    output = tmp_path / "native.pdf"
    PyMuPdfRenderer().render(presentation, output)
    assert output.read_bytes().startswith(b"%PDF")
    document = pymupdf.open(output)
    text = "\n".join(page.get_text("text") for page in document)
    document.close()
    assert "Titre modernisé" in text


def test_pdf_logo_outside_folder_is_rejected(tmp_path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(LOGO_PNG)
    root = prepare_pdf_project(tmp_path / "inside")
    write_text(
        root / INSTRUCTIONS_FILE,
        pdf_instructions(logo="../outside.png"),
    )
    with pytest.raises(DeclarativeTaskError) as info:
        DeclarativePdfService(root).build_presentation()
    assert info.value.code == "PATH_TRAVERSAL"


def test_pdf_missing_original_is_rejected(tmp_path):
    root = tmp_path / "pdf"
    root.mkdir()
    write_text(root / INSTRUCTIONS_FILE, pdf_instructions())
    with pytest.raises(PdfWorkflowError) as info:
        DeclarativePdfService(root).build_presentation()
    assert info.value.code == "ORIGINAL_NOT_FOUND"


def test_pdf_unknown_modification_is_reported_as_warning(tmp_path):
    instructions = """---
workflow: PDF_PRESENTATION
---
## Modifications de contenu
- faire quelque chose
"""
    root = prepare_pdf_project(tmp_path, instructions=instructions)
    renderer = FakeRenderer()
    result = DeclarativePdfService(root, renderer=renderer).run()
    assert any(warning.startswith("modification_ignoree:") for warning in result.warnings)


def test_renderer_candidates_preferences():
    assert renderer_candidates("auto")[0].name == "weasyprint"
    assert renderer_candidates("reportlab")[0].name == "reportlab"
    assert renderer_candidates("pymupdf")[0].name == "pymupdf"
    assert isinstance(ReportLabRenderer(), ReportLabRenderer)


# -- engine dispatch ---------------------------------------------------------


def test_engine_dispatches_tarif_workflow(tmp_path):
    root = prepare_tarif_project(tmp_path)
    result = run_instructions_task(root, clock=lambda: FIXED_NOW)
    assert result.output_path.name == "tarifs-mis-a-jour.xlsx"
    assert result.counts["total"] == 9


def test_engine_dispatches_pdf_workflow(tmp_path):
    root = prepare_pdf_project(tmp_path)
    renderer = FakeRenderer()
    engine = InstructionsTaskEngine(root, renderer=renderer, clock=lambda: FIXED_NOW)
    assert engine.workflow is WorkflowType.PDF_PRESENTATION
    result = engine.run()
    assert result.renderer == "fake"
    assert result.output_path.name == "resultat.pdf"
