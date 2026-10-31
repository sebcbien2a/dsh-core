"""Translate and structure a supplier catalog PDF into Obsidian Markdown.

The script extracts the PDF text layer with PyMuPDF, chunks pages under a
character budget, asks OpenRouter to translate/structure each chunk while
preserving references, codes, units and prices, then writes an index note and
part notes into the Obsidian supplier folder.

Dry-run mode never calls OpenRouter and never writes files; it prints the
planned notes and a preview so chunking/formatting can be checked safely.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Sequence

import requests

DEFAULT_SUPPLIER = "KARLUX"
DEFAULT_TARGET = Path(r"A:\Obsidian\perso\Fournisseurs")
DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
DEFAULT_API_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MAX_CHARS = 12_000
DEFAULT_TIMEOUT = 120.0


class CatalogError(RuntimeError):
    """Raised when extraction, translation or formatting fails."""


@dataclass(frozen=True)
class CatalogChunk:
    number: int
    pages: tuple[int, ...]
    text: str

    @property
    def page_range(self) -> str:
        return format_page_range(self.pages)

    @property
    def char_count(self) -> int:
        return len(self.text)


@dataclass(frozen=True)
class CatalogNote:
    path: Path
    content: str


SYSTEM_PROMPT = """Tu es un traducteur technique et un éditeur documentaire \
expert pour DSH-Core.

Ta mission : traduire en français un extrait de catalogue fournisseur et le \
structurer en Markdown Obsidian propre.

Règles absolues :
- Conserve EXACTEMENT les références produit, codes, SKU, EAN, dimensions, \
unités, quantités, prix, devises, remises et noms propres.
- Ne modifie, n'arrondis et n'invente aucun chiffre.
- Conserve les marqueurs de page `<!-- page N -->`.
- Utilise des titres `##` et `###`, des listes et des tableaux Markdown.
- N'ajoute pas de frontmatter YAML ni de bloc de code autour de la réponse.
- Si une information est illisible, écris `[illisible]` sans l'inventer.
"""


def safe_supplier_slug(supplier: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", supplier.strip()).strip("-")
    return (slug or "FOURNISSEUR").upper()


def safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "-", value).strip(" .")
    return cleaned or "SANS-NOM"


def format_page_range(pages: Sequence[int]) -> str:
    if not pages:
        return ""
    ordered = sorted(set(pages))
    if ordered == list(range(ordered[0], ordered[-1] + 1)):
        return (
            str(ordered[0])
            if len(ordered) == 1
            else f"{ordered[0]}–{ordered[-1]}"
        )
    return ", ".join(str(page) for page in ordered)


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def build_frontmatter(
    *,
    supplier: str,
    pdf_name: str,
    generated_date: date,
    pages_total: int,
    part_count: int,
    note_type: str = "catalogue_fournisseur",
    part_number: int | None = None,
    page_range: str | None = None,
) -> str:
    slug = safe_supplier_slug(supplier).lower()
    tags = ["fournisseur", f"fournisseur/{slug}", "catalogue"]
    lines = [
        "---",
        f"nom: {_yaml_string(supplier.strip().upper())}",
        f"fournisseur: {_yaml_string(supplier.strip().upper())}",
        f"type: {_yaml_string(note_type)}",
        f"source_pdf: {_yaml_string(pdf_name)}",
        f"date: {_yaml_string(generated_date.isoformat())}",
        "tags:",
        *[f"  - {_yaml_string(tag)}" for tag in tags],
        f"pages_total: {pages_total}",
        f"parties: {part_count}",
    ]
    if part_number is not None:
        lines.append(f"partie: {part_number}")
    if page_range is not None:
        lines.append(f"pages: {_yaml_string(page_range)}")
    lines.extend(["generated_by: \"DSH-Core\"", "---", ""])
    return "\n".join(lines)


def normalise_markdown(text: str) -> str:
    content = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z0-9_-]*\s*\n", "", content)
        content = re.sub(r"\n```\s*$", "", content)
    content = re.sub(r"(?m)^#\s+", "## ", content)
    content = re.sub(r"\n{3,}", "\n\n", content).strip()
    return content or "_(aucun contenu)_"


def build_index_note(
    *,
    supplier: str,
    pdf_name: str,
    generated_date: date,
    pages_total: int,
    chunks: Sequence[CatalogChunk],
) -> CatalogNote:
    slug = safe_supplier_slug(supplier)
    supplier_display = supplier.strip().upper()
    lines = [
        build_frontmatter(
            supplier=supplier,
            pdf_name=pdf_name,
            generated_date=generated_date,
            pages_total=pages_total,
            part_count=len(chunks),
        ).rstrip(),
        "",
        f"# {supplier_display} — Catalogue fournisseur",
        "",
        "> [!info] Source",
        f"> - PDF : `{pdf_name}`",
        f"> - Pages : {pages_total}",
        f"> - Parties : {len(chunks)}",
        f"> - Généré le : {generated_date.isoformat()}",
        "",
        "## Sommaire",
    ]
    for chunk in chunks:
        part_name = f"{slug} - Catalogue - Partie {chunk.number:02d}"
        lines.append(
            f"- [[{part_name}|Partie {chunk.number} — pages {chunk.page_range}]]"
        )
    lines.extend(
        [
            "",
            "## Navigation",
            f"- [[{slug} - Catalogue - Partie 01|Commencer la lecture]]",
            "",
        ]
    )
    return CatalogNote(
        path=Path(f"{slug} - Catalogue.md"),
        content="\n".join(lines),
    )


def build_part_note(
    *,
    supplier: str,
    pdf_name: str,
    generated_date: date,
    pages_total: int,
    chunk: CatalogChunk,
    part_count: int,
    body: str,
) -> CatalogNote:
    slug = safe_supplier_slug(supplier)
    supplier_display = supplier.strip().upper()
    content = "\n".join(
        [
            build_frontmatter(
                supplier=supplier,
                pdf_name=pdf_name,
                generated_date=generated_date,
                pages_total=pages_total,
                part_count=part_count,
                note_type="catalogue_fournisseur_partie",
                part_number=chunk.number,
                page_range=chunk.page_range,
            ).rstrip(),
            "",
            f"# {supplier_display} — Catalogue — Partie {chunk.number:02d}",
            "",
            f"[[{slug} - Catalogue|← Retour au sommaire]]",
            "",
            f"> Pages : {chunk.page_range}",
            "",
            normalise_markdown(body),
            "",
        ]
    )
    return CatalogNote(
        path=Path(f"{slug} - Catalogue - Partie {chunk.number:02d}.md"),
        content=content,
    )


def extract_pdf_pages(pdf_path: Path) -> list[str]:
    if not pdf_path.is_file():
        raise CatalogError(f"PDF introuvable : {pdf_path}")
    try:
        import pymupdf
    except ImportError as exc:
        raise CatalogError("PyMuPDF est requis pour lire le catalogue") from exc
    try:
        with pymupdf.open(pdf_path) as document:
            pages = [page.get_text("text", sort=True) for page in document]
    except Exception as exc:
        raise CatalogError(f"Lecture PDF impossible : {exc}") from exc
    if not any(page.strip() for page in pages):
        raise CatalogError(
            "PDF sans couche texte : un OCR est requis avant traduction"
        )
    return pages


def _split_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut <= 0:
            cut = remaining.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        parts.append(remaining)
    return parts


def chunk_pages(
    pages: Sequence[str], *, max_chars: int = DEFAULT_MAX_CHARS
) -> list[CatalogChunk]:
    if max_chars < 200:
        raise CatalogError("max_chars doit être supérieur ou égal à 200")
    chunks: list[CatalogChunk] = []
    current_pages: list[int] = []
    current_parts: list[str] = []
    current_size = 0
    number = 1

    def flush() -> None:
        nonlocal number, current_pages, current_parts, current_size
        if not current_parts:
            return
        chunks.append(
            CatalogChunk(
                number=number,
                pages=tuple(current_pages),
                text="\n\n".join(current_parts).strip(),
            )
        )
        number += 1
        current_pages = []
        current_parts = []
        current_size = 0

    for page_number, page_text in enumerate(pages, start=1):
        text = page_text.strip()
        if not text:
            continue
        marker = f"<!-- page {page_number} -->"
        suite_marker = f"<!-- page {page_number} (suite) -->"
        available = max_chars - max(len(marker), len(suite_marker)) - 1
        if available <= 0:
            raise CatalogError("max_chars est trop petit pour les marqueurs de page")
        pieces = _split_text(text, available)
        for piece_index, piece in enumerate(pieces):
            piece_marker = (
                marker
                if piece_index == 0
                else f"<!-- page {page_number} (suite) -->"
            )
            block = f"{piece_marker}\n{piece}"
            if current_parts and current_size + 2 + len(block) > max_chars:
                flush()
            current_parts.append(block)
            if page_number not in current_pages:
                current_pages.append(page_number)
            current_size += (2 if len(current_parts) > 1 else 0) + len(block)
    flush()
    if not chunks:
        raise CatalogError("Aucun contenu texte exploitable dans le PDF")
    return chunks


def resolve_target_dir(target: Path, supplier: str) -> Path:
    slug = safe_supplier_slug(supplier)
    if target.name.strip().upper() == slug:
        return target
    return target / slug


def build_user_prompt(chunk: CatalogChunk, supplier: str) -> str:
    return f"""Fournisseur : {supplier.strip().upper()}
Pages source : {chunk.page_range}

Traduis le texte ci-dessous en français et structure-le en Markdown Obsidian.
Conserve les références, codes, dimensions, unités, prix, devises et marqueurs
de page. Utilise des tableaux Markdown pour les grilles tarifaires.

Texte source :
{chunk.text}
"""


class OpenRouterTranslator:
    """OpenRouter chat-completions client for catalog translation."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        api_base: str = DEFAULT_API_BASE,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = 3,
    ) -> None:
        if not api_key.strip():
            raise CatalogError("OPENROUTER_API_KEY est manquante")
        self.api_key = api_key.strip()
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    def translate(self, chunk: CatalogChunk, supplier: str) -> str:
        payload = {
            "model": self.model,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(chunk, supplier)},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://dsh-core.local",
            "X-Title": "DSH-Core Supplier Catalog",
        }
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = requests.post(
                    f"{self.api_base}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise CatalogError(
                        f"OpenRouter HTTP {response.status_code}"
                    )
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                if isinstance(content, list):
                    content = "".join(
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict)
                    )
                if not isinstance(content, str) or not content.strip():
                    raise CatalogError("Réponse OpenRouter vide")
                return content
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(min(2 ** attempt, 8))
        raise CatalogError(
            f"Traduction OpenRouter échouée après {self.max_retries} essais : "
            f"{last_error}"
        )


def build_notes(
    *,
    supplier: str,
    pdf_name: str,
    generated_date: date,
    pages_total: int,
    chunks: Sequence[CatalogChunk],
    bodies: Sequence[str],
) -> list[CatalogNote]:
    notes = [
        build_index_note(
            supplier=supplier,
            pdf_name=pdf_name,
            generated_date=generated_date,
            pages_total=pages_total,
            chunks=chunks,
        )
    ]
    notes.extend(
        build_part_note(
            supplier=supplier,
            pdf_name=pdf_name,
            generated_date=generated_date,
            pages_total=pages_total,
            chunk=chunk,
            part_count=len(chunks),
            body=body,
        )
        for chunk, body in zip(chunks, bodies)
    )
    return notes


def write_notes(target_dir: Path, notes: Sequence[CatalogNote]) -> list[Path]:
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for note in notes:
        path = target_dir / safe_filename(note.path.name)
        path.write_text(note.content, encoding="utf-8", newline="\n")
        written.append(path)
    return written


def run(
    *,
    pdf_path: Path,
    supplier: str,
    target: Path,
    dry_run: bool,
    max_chars: int,
    model: str,
    api_key: str | None,
    api_base: str,
    timeout: float,
    generated_date: date,
) -> list[Path]:
    pages = extract_pdf_pages(pdf_path)
    chunks = chunk_pages(pages, max_chars=max_chars)
    target_dir = resolve_target_dir(target, supplier)

    if dry_run:
        bodies = [
            f"_(dry-run : traduction non exécutée)_\n\n{chunk.text}"
            for chunk in chunks
        ]
    else:
        key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        print(f"[INFO] Modèle OpenRouter : {model}")
        print(f"[INFO] Parties à traduire : {len(chunks)}")
        translator = OpenRouterTranslator(
            api_key=key,
            model=model,
            api_base=api_base,
            timeout=timeout,
        )
        bodies = []
        for chunk in chunks:
            print(
                f"[TRADUCTION] Partie {chunk.number}/{len(chunks)} "
                f"(pages {chunk.page_range}, {chunk.char_count} caractères)"
            )
            bodies.append(translator.translate(chunk, supplier))

    notes = build_notes(
        supplier=supplier,
        pdf_name=pdf_path.name,
        generated_date=generated_date,
        pages_total=len(pages),
        chunks=chunks,
        bodies=bodies,
    )

    if dry_run:
        print("[DRY-RUN] Aucun fichier écrit.")
        print(f"[DRY-RUN] Dossier cible : {target_dir}")
        for note in notes:
            print(f"  - {note.path.name} ({len(note.content)} caractères)")
        preview = notes[0].content[:1200]
        print("\n--- Aperçu de l'index ---\n")
        print(preview)
        return []

    written = write_notes(target_dir, notes)
    print(f"[OK] {len(written)} note(s) écrite(s) dans {target_dir}")
    for path in written:
        print(f"  - {path.name}")
    return written


def _parse_date(value: str | None) -> date:
    if not value:
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CatalogError("--date doit respecter le format YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Traduire un catalogue fournisseur PDF vers Obsidian"
    )
    parser.add_argument("pdf", type=Path, help="chemin du catalogue PDF")
    parser.add_argument("--supplier", default=DEFAULT_SUPPLIER)
    parser.add_argument(
        "--target",
        type=Path,
        default=DEFAULT_TARGET,
        help="dossier Obsidian (le sous-dossier fournisseur est ajouté si absent)",
    )
    parser.add_argument("--model", default=os.getenv("CATALOG_MODEL", DEFAULT_MODEL))
    parser.add_argument("--api-key", default=None, help="défaut : OPENROUTER_API_KEY")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--date", default=None, help="date du frontmatter (YYYY-MM-DD)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="extrait et formate sans appeler OpenRouter ni écrire",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        run(
            pdf_path=args.pdf,
            supplier=args.supplier,
            target=args.target,
            dry_run=args.dry_run,
            max_chars=args.max_chars,
            model=args.model,
            api_key=args.api_key,
            api_base=args.api_base,
            timeout=args.timeout,
            generated_date=_parse_date(args.date),
        )
    except CatalogError as exc:
        print(f"ERREUR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
