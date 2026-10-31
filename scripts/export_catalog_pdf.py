r"""Export the translated Obsidian supplier catalog parts to a single PDF.

The source is the folder produced by ``scripts/translate_supplier_catalog.py``:
``KARLUX - Catalogue - Partie NN.md`` notes. Markdown is rendered with the
``markdown`` package, styled for A4 print, then converted with WeasyPrint.

Usage:
    python scripts/export_catalog_pdf.py ^
      --source-dir "A:\Obsidian\perso\Fournisseurs\KARLUX" ^
      --output "C:\Users\peint\OneDrive\FOURNISSEURS\KARLUX_CATALOG_FR.pdf"
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Sequence

PART_PATTERN = re.compile(r"Partie\s+(\d+)", re.IGNORECASE)
FRONTMATTER_PATTERN = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)
WIKILINK_PATTERN = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
BACKLINK_PATTERN = re.compile(r"(?m)^\[\[[^\]]*Catalogue[^\]]*\]\]\s*$\n?")
PAGE_MARKER_PATTERN = re.compile(r"<!--\s*page\s+(\d+)(?:\s*\(suite\))?\s*-->")


class ExportError(RuntimeError):
    """Raised when the catalog cannot be exported."""


def _configure_windows_runtime() -> None:
    """Point WeasyPrint to an available GTK runtime and Windows fonts."""

    if os.name != "nt":
        return
    if not os.environ.get("WEASYPRINT_DLL_DIRECTORIES"):
        candidates = (
            Path(r"C:\Program Files\Xournal++\bin"),
            Path(r"C:\Program Files\GTK3-Runtime Win64\bin"),
            Path(r"C:\msys64\mingw64\bin"),
        )
        for candidate in candidates:
            if candidate.is_dir():
                os.environ["WEASYPRINT_DLL_DIRECTORIES"] = str(candidate)
                break
    if not os.environ.get("FONTCONFIG_FILE"):
        fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        if not fonts_dir.is_dir():
            return
        cache_dir = Path(tempfile.gettempdir()) / "dsh_core_fontcache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        config_path = Path(tempfile.gettempdir()) / "dsh_core_fonts.conf"
        if not config_path.is_file():
            config_path.write_text(
                '<?xml version="1.0"?>\n'
                '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
                "<fontconfig>\n"
                f"  <dir>{fonts_dir.as_posix()}</dir>\n"
                f"  <cachedir>{cache_dir.as_posix()}</cachedir>\n"
                "</fontconfig>\n",
                encoding="utf-8",
            )
        os.environ["FONTCONFIG_FILE"] = str(config_path)


def collect_parts(source_dir: Path, supplier: str = "KARLUX") -> list[Path]:
    if not source_dir.is_dir():
        raise ExportError(f"Dossier source introuvable : {source_dir}")
    pattern = f"{supplier.strip().upper()} - Catalogue - Partie *.md"
    parts = list(source_dir.glob(pattern))
    if not parts:
        parts = list(source_dir.glob("* - Catalogue - Partie *.md"))
    if not parts:
        raise ExportError(f"Aucune partie Markdown trouvée dans {source_dir}")

    def sort_key(path: Path) -> tuple[int, str]:
        match = PART_PATTERN.search(path.name)
        return (int(match.group(1)) if match else 10**9, path.name.casefold())

    return sorted(parts, key=sort_key)


def strip_frontmatter(text: str) -> str:
    return FRONTMATTER_PATTERN.sub("", text, count=1).lstrip()


def preprocess_obsidian(text: str, *, page_breaks: bool = True) -> str:
    content = strip_frontmatter(text)
    content = BACKLINK_PATTERN.sub("", content)

    def replace_wikilink(match: re.Match[str]) -> str:
        target, label = match.group(1), match.group(2)
        return (label or target).strip()

    content = WIKILINK_PATTERN.sub(replace_wikilink, content)
    if page_breaks:
        content = PAGE_MARKER_PATTERN.sub(
            '<div class="page-break"></div>', content
        )
    else:
        content = PAGE_MARKER_PATTERN.sub("", content)
    return content.strip()


def build_html(
    parts: Sequence[tuple[Path, str]],
    *,
    title: str,
    supplier: str,
    page_breaks: bool = True,
) -> str:
    try:
        import markdown
    except ImportError as exc:
        raise ExportError(
            "Le module 'markdown' est requis : pip install markdown weasyprint"
        ) from exc

    rendered: list[str] = []
    for path, raw in parts:
        body = preprocess_obsidian(raw, page_breaks=page_breaks)
        html_body = markdown.markdown(
            body,
            extensions=["tables", "fenced_code", "sane_lists", "attr_list"],
        )
        rendered.append(
            f'<section class="part" data-source="{path.name}">{html_body}</section>'
        )

    total_parts = len(parts)
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
@page {{
  size: A4;
  margin: 16mm 14mm 18mm 14mm;
  @bottom-center {{
    content: "{supplier} — page " counter(page);
    font-size: 9px;
    color: #666;
  }}
}}
body {{
  font-family: "DejaVu Sans", "Segoe UI", Arial, sans-serif;
  font-size: 10.5px;
  line-height: 1.38;
  color: #1a1a1a;
}}
.cover {{
  text-align: center;
  padding-top: 70mm;
  margin-bottom: 30mm;
}}
.cover h1 {{
  font-size: 30px;
  letter-spacing: 1px;
  margin-bottom: 8px;
}}
.cover p {{ color: #555; }}
.part {{ break-before: page; }}
.part:first-of-type {{ break-before: auto; }}
.page-break {{ break-before: page; }}
h1, h2, h3 {{ color: #1f3a5f; break-after: avoid; }}
h2 {{ font-size: 16px; border-bottom: 1px solid #ccd6e0; padding-bottom: 3px; }}
h3 {{ font-size: 13px; }}
table {{
  border-collapse: collapse;
  width: 100%;
  margin: 8px 0 14px;
  font-size: 9.5px;
}}
th, td {{
  border: 1px solid #c9c9c9;
  padding: 3px 5px;
  vertical-align: top;
}}
th {{ background: #eef2f6; }}
tr:nth-child(even) td {{ background: #fafbfc; }}
ul, ol {{ margin: 4px 0 10px 18px; }}
code {{ font-size: 9px; background: #f3f3f3; padding: 0 2px; }}
</style>
</head>
<body>
<section class="cover">
  <h1>{supplier} — Catalogue fournisseur</h1>
  <p>Traduction française structurée</p>
  <p>{total_parts} parties</p>
</section>
{''.join(rendered)}
</body>
</html>
"""


def export_pdf(
    *,
    source_dir: Path,
    output: Path,
    supplier: str = "KARLUX",
    title: str | None = None,
    page_breaks: bool = True,
) -> Path:
    parts = collect_parts(source_dir, supplier)
    payload = [
        (path, path.read_text(encoding="utf-8")) for path in parts
    ]
    html = build_html(
        payload,
        title=title or f"{supplier.strip().upper()} — Catalogue fournisseur",
        supplier=supplier.strip().upper(),
        page_breaks=page_breaks,
    )
    _configure_windows_runtime()
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as exc:
        raise ExportError(
            "WeasyPrint indisponible "
            f"({exc}). Installez 'markdown' et 'weasyprint', puis un runtime "
            "GTK3 (Xournal++ ou GTK3-Runtime Win64) si les DLL manquent."
        ) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html, base_url=str(source_dir)).write_pdf(str(output))
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Exporter les notes Obsidian d'un catalogue en PDF"
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path(r"A:\Obsidian\perso\Fournisseurs\KARLUX"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--supplier", default="KARLUX")
    parser.add_argument("--title", default=None)
    parser.add_argument(
        "--no-page-breaks",
        action="store_true",
        help="ne pas insérer de saut de page aux marqueurs de page source",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        output = export_pdf(
            source_dir=args.source_dir,
            output=args.output,
            supplier=args.supplier,
            title=args.title,
            page_breaks=not args.no_page_breaks,
        )
    except ExportError as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 1
    print(f"[OK] PDF créé : {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
