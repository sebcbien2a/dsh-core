"""Tests for the supplier catalog translation/formating pipeline."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
import yaml

from scripts.translate_supplier_catalog import (
    CatalogChunk,
    build_frontmatter,
    build_notes,
    chunk_pages,
    extract_pdf_pages,
    main,
    normalise_markdown,
    resolve_target_dir,
)


def _chunks() -> list[CatalogChunk]:
    return [
        CatalogChunk(number=1, pages=(1, 2), text="<!-- page 1 -->\nALPHA"),
        CatalogChunk(number=2, pages=(3,), text="<!-- page 3 -->\nBETA"),
    ]


def test_chunk_pages_respects_budget_and_keeps_page_markers() -> None:
    pages = ["A" * 300, "B" * 300, "", "C" * 50]

    chunks = chunk_pages(pages, max_chars=400)

    assert chunks
    assert all(len(chunk.text) <= 400 for chunk in chunks)
    combined = "\n".join(chunk.text for chunk in chunks)
    assert "<!-- page 1 -->" in combined
    assert "<!-- page 2 -->" in combined
    assert "<!-- page 4 -->" in combined
    pages_seen = {page for chunk in chunks for page in chunk.pages}
    assert pages_seen == {1, 2, 4}
    assert [chunk.number for chunk in chunks] == list(range(1, len(chunks) + 1))


def test_chunk_pages_splits_oversized_page() -> None:
    chunks = chunk_pages(["X" * 1000], max_chars=300)

    assert len(chunks) > 1
    assert all(len(chunk.text) <= 300 for chunk in chunks)
    assert all(chunk.pages == (1,) for chunk in chunks)
    assert any("(suite)" in chunk.text for chunk in chunks)


def test_frontmatter_is_valid_yaml() -> None:
    frontmatter = build_frontmatter(
        supplier="KARLUX",
        pdf_name="karlux.pdf",
        generated_date=date(2026, 10, 29),
        pages_total=42,
        part_count=3,
    )

    match = re.match(r"\A---\n(.*?)\n---\n", frontmatter, re.DOTALL)
    assert match is not None
    data = yaml.safe_load(match.group(1))
    assert data["nom"] == "KARLUX"
    assert data["date"] == "2026-10-29"
    assert "fournisseur/karlux" in data["tags"]
    assert data["pages_total"] == 42


def test_notes_contain_obsidian_internal_links() -> None:
    notes = build_notes(
        supplier="KARLUX",
        pdf_name="karlux.pdf",
        generated_date=date(2026, 10, 29),
        pages_total=3,
        chunks=_chunks(),
        bodies=["## Produit\n\nREF-100 — 12,50 EUR", "## Produit\n\nREF-200"],
    )

    assert [note.path.name for note in notes] == [
        "KARLUX - Catalogue.md",
        "KARLUX - Catalogue - Partie 01.md",
        "KARLUX - Catalogue - Partie 02.md",
    ]
    index = notes[0].content
    part = notes[1].content
    assert "[[KARLUX - Catalogue - Partie 01|Partie 1 — pages 1–2]]" in index
    assert "[[KARLUX - Catalogue - Partie 02|Partie 2 — pages 3]]" in index
    assert "[[KARLUX - Catalogue|← Retour au sommaire]]" in part
    assert "REF-100 — 12,50 EUR" in part


def test_normalise_markdown_demotes_h1_and_strips_fence() -> None:
    raw = "```markdown\n# Titre\n\n\n\nTexte\n```"

    assert normalise_markdown(raw) == "## Titre\n\nTexte"


def test_resolve_target_dir() -> None:
    vault = Path("A:/Obsidian/perso/Fournisseurs")
    assert resolve_target_dir(vault, "KARLUX") == vault / "KARLUX"
    assert resolve_target_dir(vault / "KARLUX", "karlux") == vault / "KARLUX"


def _make_pdf(path: Path) -> None:
    pymupdf = pytest.importorskip("pymupdf")
    document = pymupdf.open()
    first = document.new_page()
    first.insert_text((72, 72), "KARLUX WIDGET REF-100 12.50 EUR")
    second = document.new_page()
    second.insert_text((72, 72), "KARLUX WIDGET REF-200 19.90 EUR")
    document.save(path)
    document.close()


def test_extract_pdf_pages(tmp_path: Path) -> None:
    pdf = tmp_path / "karlux.pdf"
    _make_pdf(pdf)

    pages = extract_pdf_pages(pdf)

    assert len(pages) == 2
    assert "KARLUX" in pages[0]
    assert "REF-200" in pages[1]


def test_cli_dry_run_writes_nothing(tmp_path: Path, capsys) -> None:
    pdf = tmp_path / "karlux.pdf"
    _make_pdf(pdf)
    target = tmp_path / "obsidian"

    code = main(
        [
            str(pdf),
            "--supplier",
            "KARLUX",
            "--target",
            str(target),
            "--dry-run",
            "--max-chars",
            "300",
        ]
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "[DRY-RUN]" in output
    assert "KARLUX - Catalogue.md" in output
    assert not (target / "KARLUX").exists()


def test_run_writes_notes_with_mocked_openrouter(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import scripts.translate_supplier_catalog as module

    pdf = tmp_path / "karlux.pdf"
    _make_pdf(pdf)
    target = tmp_path / "obsidian"

    def fake_translate(self, chunk, supplier):
        return f"## Traduction\n\nREF conservée — {chunk.page_range}"

    monkeypatch.setattr(module.OpenRouterTranslator, "translate", fake_translate)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    code = main(
        [
            str(pdf),
            "--supplier",
            "KARLUX",
            "--target",
            str(target),
            "--max-chars",
            "300",
        ]
    )

    assert code == 0
    destination = target / "KARLUX"
    notes = sorted(destination.glob("*.md"))
    assert notes
    assert any(note.name == "KARLUX - Catalogue.md" for note in notes)
    index = (destination / "KARLUX - Catalogue.md").read_text(encoding="utf-8")
    assert "nom: \"KARLUX\"" in index
    assert "[[KARLUX - Catalogue - Partie 01" in index
    part = next(note for note in notes if "Partie 01" in note.name)
    assert "REF conservée" in part.read_text(encoding="utf-8")


def test_missing_api_key_fails_without_writing(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "karlux.pdf"
    _make_pdf(pdf)
    target = tmp_path / "obsidian"
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    code = main([str(pdf), "--supplier", "KARLUX", "--target", str(target)])

    assert code == 1
    assert not (target / "KARLUX").exists()
