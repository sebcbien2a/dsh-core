"""Declarative PDF / PLV redesign from ``instructions.md`` (P15.2).

Workflow 2 of the P15.2 engine. The service extracts the source text and block
structure from ``original.pdf`` with PyMuPDF, applies the declarative deletions,
replacements and additions in instruction order, builds a clean HTML/CSS
presentation (palette, columns, optional ``assets/`` logo) and renders
``output/resultat.pdf``.

Rendering is injectable. By default the service tries WeasyPrint first and falls
back cleanly to the native ReportLab renderer, then to a native PyMuPDF renderer
when WeasyPrint is unavailable or broken (for example a Windows GTK issue). No
renderer import is made at module import time, so the workflow stays fully
offline and import-safe.
"""

from __future__ import annotations

import html as html_lib
import inspect
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
from xml.sax.saxutils import escape as xml_escape

from schemas.declarative_task_schema import (
    DEFAULT_PALETTE,
    PDF_HTML_OUTPUT,
    PDF_OUTPUT,
    ContentModification,
    DeclarativeTask,
    DeclarativeTaskError,
    ModificationOperation,
    TaskFolderLayout,
    ensure_contained,
    load_task,
    normalize_label,
)

SAFE_COLOR_RE = re.compile(r"^(#[0-9A-Fa-f]{3,8}|[A-Za-z]{3,20}|rgba?\([0-9.,%\s]+\))$")


class PdfWorkflowError(DeclarativeTaskError):
    """Invalid PDF redesign operation; ``code`` is stable for tests and logs."""


@dataclass(frozen=True)
class ExtractedPdf:
    """Text and block structure extracted from the source PDF."""

    pages: tuple[str, ...]
    blocks: tuple[tuple[str, ...], ...]
    text: str
    page_count: int
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Presentation:
    """Structured presentation plus its rendered HTML template."""

    title: str
    source_text: str
    paragraphs: tuple[str, ...]
    html: str
    palette: Mapping[str, str]
    columns: int
    logo_path: Path | None
    paper_format: str
    base_url: str


@dataclass(frozen=True)
class PdfRunResult:
    """Outcome of one declarative PDF redesign."""

    output_path: Path
    html_path: Path
    log_path: Path
    renderer: str
    page_count: int
    modification_counts: Mapping[str, int]
    warnings: tuple[str, ...] = ()
    date: str = ""

    @property
    def output(self) -> Path:
        return self.output_path

    @property
    def log(self) -> Path:
        return self.log_path

    @property
    def html(self) -> Path:
        return self.html_path


class PdfRenderer(Protocol):
    """Injectable renderer contract."""

    name: str

    def render(self, presentation: Presentation, output_path: Path) -> None: ...


def _renderer_uses_html(callable_object: Any) -> bool | None:
    try:
        parameters = list(inspect.signature(callable_object).parameters.values())
    except (TypeError, ValueError):
        return None
    if not parameters:
        return None
    first = parameters[0]
    name = first.name.casefold()
    if "html" in name:
        return True
    if "presentation" in name:
        return False
    annotation = first.annotation
    if annotation is str:
        return True
    if isinstance(annotation, str) and annotation.casefold() in {"str", "html"}:
        return True
    return None


class _CallableRenderer:
    """Adapter accepting a callable renderer (HTML or presentation input)."""

    name = "callable"
    _html_only = True

    def __init__(self, function: Any) -> None:
        self._function = function
        self._uses_html = _renderer_uses_html(function)

    def render(self, presentation: Presentation, output_path: Path) -> None:
        uses_html = True if self._uses_html is None else self._uses_html
        try:
            value = presentation.html if uses_html else presentation
            self._function(value, output_path)
        except TypeError:
            value = presentation if uses_html else presentation.html
            self._function(value, output_path)


class _HtmlObjectRenderer:
    """Route an injected ``render(html, path)`` object to the HTML template."""

    _html_only = True

    def __init__(self, renderer: Any) -> None:
        self._renderer = renderer
        self.name = getattr(renderer, "name", "injected")

    def render(self, presentation: Presentation, output_path: Path) -> None:
        self._renderer.render(presentation.html, output_path)


def extract_pdf(path: str | os.PathLike[str]) -> ExtractedPdf:
    """Extract plain text and text blocks from a PDF with PyMuPDF."""
    try:
        import pymupdf
    except ImportError as exc:
        raise PdfWorkflowError("PYMUPDF_UNAVAILABLE", str(exc)) from exc
    try:
        document = pymupdf.open(str(path))
    except Exception as exc:
        raise PdfWorkflowError("PDF_UNREADABLE", str(path)) from exc
    try:
        pages: list[str] = []
        all_blocks: list[tuple[str, ...]] = []
        for page in document:
            pages.append(page.get_text("text"))
            blocks: list[str] = []
            for block in page.get_text("blocks"):
                if len(block) >= 5:
                    text = str(block[4]).strip()
                    if text:
                        blocks.append(text)
            all_blocks.append(tuple(blocks))
        metadata = {
            str(key): str(value)
            for key, value in (getattr(document, "metadata", None) or {}).items()
            if value is not None
        }
        text = "\n\n".join(pages)
        return ExtractedPdf(
            pages=tuple(pages),
            blocks=tuple(all_blocks),
            text=text,
            page_count=len(document),
            metadata=metadata,
        )
    finally:
        close = getattr(document, "close", None)
        if callable(close):
            close()


def apply_content_modifications(
    text: str, modifications: Sequence[ContentModification]
) -> tuple[str, dict[str, int], tuple[str, ...]]:
    """Apply declarative operations sequentially and deterministically."""
    result = text
    counts = {
        ModificationOperation.SUPPRIMER.value: 0,
        ModificationOperation.REMPLACER.value: 0,
        ModificationOperation.AJOUTER.value: 0,
    }
    warnings: list[str] = []
    for modification in modifications:
        operation = modification.operation
        if operation is ModificationOperation.SUPPRIMER:
            target = modification.target
            if target and target in result:
                occurrences = result.count(target)
                result = result.replace(target, "")
                counts[ModificationOperation.SUPPRIMER.value] += occurrences
            else:
                warnings.append(f"cible_introuvable:supprimer:{target}")
        elif operation is ModificationOperation.REMPLACER:
            target = modification.target
            if target and target in result:
                occurrences = result.count(target)
                result = result.replace(target, modification.replacement)
                counts[ModificationOperation.REMPLACER.value] += occurrences
            else:
                warnings.append(f"cible_introuvable:remplacer:{target}")
        elif operation is ModificationOperation.AJOUTER:
            content = modification.replacement
            if not content:
                continue
            position = modification.position or "end"
            anchor = modification.anchor
            inserted = False
            if position in {"after", "before"} and anchor:
                index = result.find(anchor)
                if index >= 0:
                    if position == "after":
                        index += len(anchor)
                    result = result[:index] + "\n\n" + content + "\n\n" + result[index:]
                    inserted = True
                else:
                    warnings.append(f"ancre_introuvable:ajouter:{anchor}")
            elif position == "start":
                result = content + "\n\n" + result
                inserted = True
            if not inserted:
                result = result.rstrip() + "\n\n" + content
            counts[ModificationOperation.AJOUTER.value] += 1

    result = re.sub(r"[ \t]+\n", "\n", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip(), counts, tuple(warnings)


def _safe_color(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    return text if SAFE_COLOR_RE.fullmatch(text) else fallback


def _split_paragraphs(text: str) -> tuple[str, ...]:
    if not text.strip():
        return ()
    paragraphs = re.split(r"\n\s*\n", text.strip())
    return tuple(paragraph.strip() for paragraph in paragraphs if paragraph.strip())


def build_presentation_html(
    *,
    title: str,
    paragraphs: Sequence[str],
    palette: Mapping[str, str],
    columns: int,
    logo_path: Path | None,
    paper_format: str = "A4",
) -> str:
    """Build the deterministic HTML/CSS presentation template."""
    primary = _safe_color(palette.get("primaire"), DEFAULT_PALETTE["primaire"])
    secondary = _safe_color(palette.get("secondaire"), DEFAULT_PALETTE["secondaire"])
    accent = _safe_color(palette.get("accent"), DEFAULT_PALETTE["accent"])
    background = _safe_color(palette.get("fond"), DEFAULT_PALETTE["fond"])
    text_color = _safe_color(palette.get("texte"), DEFAULT_PALETTE["texte"])
    column_count = max(1, min(4, int(columns or 1)))
    paper = re.sub(r"[^A-Za-z0-9]", "", paper_format or "A4") or "A4"
    logo_html = ""
    if logo_path is not None:
        logo_html = (
            f'<img class="plv-logo" src="{html_lib.escape(logo_path.as_uri(), quote=True)}" '
            f'alt="logo" />'
        )
    title_html = (
        f'<h1 class="plv-title">{html_lib.escape(title)}</h1>' if title else ""
    )
    header_html = (
        f'<header class="plv-header">{logo_html}{title_html}</header>'
        if logo_html or title_html
        else ""
    )
    body_parts: list[str] = []
    for paragraph in paragraphs:
        escaped = html_lib.escape(paragraph).replace("\n", "<br/>")
        body_parts.append(f"<p>{escaped}</p>")
    body_html = "\n".join(body_parts) or '<p class="plv-empty"></p>'
    css = f"""
    @page {{
        size: {paper};
        margin: 18mm 16mm 18mm 16mm;
        @bottom-center {{
            content: counter(page) " / " counter(pages);
            font-family: Helvetica, Arial, sans-serif;
            font-size: 8pt;
            color: {secondary};
        }}
    }}
    * {{ box-sizing: border-box; }}
    html, body {{
        margin: 0;
        padding: 0;
        background: {background};
        color: {text_color};
        font-family: Helvetica, Arial, sans-serif;
        font-size: 11pt;
        line-height: 1.45;
    }}
    .plv-header {{
        display: flex;
        align-items: center;
        gap: 10mm;
        padding-bottom: 5mm;
        margin-bottom: 7mm;
        border-bottom: 3px solid {accent};
    }}
    .plv-logo {{
        max-height: 22mm;
        max-width: 45mm;
        object-fit: contain;
    }}
    .plv-title {{
        margin: 0;
        color: {primary};
        font-size: 24pt;
        line-height: 1.1;
        letter-spacing: 0.2pt;
    }}
    .plv-content {{
        column-count: {column_count};
        column-gap: 9mm;
        column-rule: 1px solid {secondary};
    }}
    .plv-content p {{
        margin: 0 0 4mm 0;
        text-align: justify;
        orphans: 3;
        widows: 3;
        break-inside: avoid;
    }}
    .plv-content p:last-child {{ margin-bottom: 0; }}
    .plv-empty {{ min-height: 1mm; }}
    """
    return (
        "<!DOCTYPE html>\n"
        '<html lang="fr">\n<head>\n<meta charset="utf-8" />\n'
        f"<title>{html_lib.escape(title)}</title>\n"
        f"<style>{css}</style>\n</head>\n<body>\n"
        f"{header_html}\n"
        f'<main class="plv-content">\n{body_html}\n</main>\n'
        "</body>\n</html>\n"
    )


def _rl_color(value: object, fallback: str) -> Any:
    from reportlab.lib.colors import HexColor

    text = _safe_color(value, fallback)
    try:
        return HexColor(text)
    except Exception:
        return HexColor(fallback)


def _rl_markup(text: str) -> str:
    return xml_escape(text).replace("\n", "<br/>")


class WeasyPrintRenderer:
    """WeasyPrint renderer (lazy import, optional system GTK dependency)."""

    name = "weasyprint"

    def render(self, presentation: Presentation, output_path: Path) -> None:
        from weasyprint import HTML

        HTML(string=presentation.html, base_url=presentation.base_url).write_pdf(
            str(output_path)
        )


class ReportLabRenderer:
    """Native ReportLab renderer: multi-column frames, logo and palette."""

    name = "reportlab"

    def render(self, presentation: Presentation, output_path: Path) -> None:
        from reportlab.lib.enums import TA_JUSTIFY
        from reportlab.lib.pagesizes import A4, LETTER
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib.utils import ImageReader
        from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer

        pagesize = LETTER if presentation.paper_format.upper() == "LETTER" else A4
        margin = 16 * mm
        width, height = pagesize
        primary = _rl_color(presentation.palette.get("primaire"), DEFAULT_PALETTE["primaire"])
        secondary = _rl_color(
            presentation.palette.get("secondaire"), DEFAULT_PALETTE["secondaire"]
        )
        accent = _rl_color(presentation.palette.get("accent"), DEFAULT_PALETTE["accent"])
        text_color = _rl_color(presentation.palette.get("texte"), DEFAULT_PALETTE["texte"])
        columns = max(1, min(4, int(presentation.columns or 1)))
        has_header = bool(presentation.title or presentation.logo_path)
        header_height = 52 if has_header else 10
        content_width = width - 2 * margin
        content_height = height - 2 * margin - header_height
        gap = 6 * mm
        column_width = (content_width - gap * (columns - 1)) / columns
        frames = [
            Frame(
                margin + index * (column_width + gap),
                margin,
                column_width,
                max(40, content_height),
                leftPadding=0,
                rightPadding=0,
                topPadding=0,
                bottomPadding=0,
                id=f"plv-column-{index + 1}",
            )
            for index in range(columns)
        ]

        title_style = ParagraphStyle(
            "plv-title",
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            textColor=primary,
        )
        body_style = ParagraphStyle(
            "plv-body",
            fontName="Helvetica",
            fontSize=10.5,
            leading=14.5,
            textColor=text_color,
            alignment=TA_JUSTIFY,
        )

        def draw_header(canvas: Any, doc: Any) -> None:
            canvas.saveState()
            top = height - margin
            logo_width = 0
            if presentation.logo_path is not None:
                try:
                    image = ImageReader(str(presentation.logo_path))
                    image_width, image_height = image.getSize()
                    max_width, max_height = 24 * mm, 16 * mm
                    scale = min(max_width / image_width, max_height / image_height, 1.0)
                    logo_width = image_width * scale
                    logo_height = image_height * scale
                    canvas.drawImage(
                        image,
                        margin,
                        top - logo_height,
                        width=logo_width,
                        height=logo_height,
                        preserveAspectRatio=True,
                        mask="auto",
                    )
                    logo_width += 5 * mm
                except Exception:
                    logo_width = 0
            if presentation.title:
                paragraph = Paragraph(_rl_markup(presentation.title), title_style)
                available = max(40, content_width - logo_width)
                _, paragraph_height = paragraph.wrap(available, header_height)
                paragraph.drawOn(
                    canvas,
                    margin + logo_width,
                    top - paragraph_height - 4 * mm,
                )
            canvas.setStrokeColor(accent)
            canvas.setLineWidth(2)
            canvas.line(
                margin,
                margin + max(40, content_height) + 4 * mm,
                width - margin,
                margin + max(40, content_height) + 4 * mm,
            )
            canvas.setFillColor(secondary)
            canvas.setFont("Helvetica", 8)
            canvas.drawCentredString(width / 2, margin / 2, f"{doc.page}")
            canvas.restoreState()

        document = BaseDocTemplate(
            str(output_path),
            pagesize=pagesize,
            leftMargin=margin,
            rightMargin=margin,
            topMargin=margin,
            bottomMargin=margin,
            title=presentation.title or "DSH-Core",
            author="DSH-Core",
        )
        document.addPageTemplates(
            [PageTemplate(id="plv", frames=frames, onPage=draw_header)]
        )
        story: list[Any] = []
        for paragraph_text in presentation.paragraphs:
            story.append(Paragraph(_rl_markup(paragraph_text), body_style))
            story.append(Spacer(1, 4 * mm))
        if not story:
            story.append(Spacer(1, 1))
        document.build(story)


class PyMuPdfRenderer:
    """Native PyMuPDF fallback renderer (no external system dependency)."""

    name = "pymupdf"

    def render(self, presentation: Presentation, output_path: Path) -> None:
        import pymupdf

        if hasattr(pymupdf, "Story") and hasattr(pymupdf, "DocumentWriter"):
            try:
                story = pymupdf.Story(html=presentation.html)
                writer = pymupdf.DocumentWriter(str(output_path))
                paper = presentation.paper_format.lower() if presentation.paper_format else "a4"
                if paper not in {"a4", "letter"}:
                    paper = "a4"
                mediabox = pymupdf.paper_rect(paper)
                margin = 45
                where = mediabox + (margin, margin, -margin, -margin)
                pages = 0
                more = True
                while more:
                    device = writer.begin_page(mediabox)
                    more, _ = story.place(where)
                    story.draw(device)
                    writer.end_page()
                    pages += 1
                writer.close()
                if pages:
                    return
            except Exception:
                pass
        self._simple_text(presentation, output_path)

    def _simple_text(self, presentation: Presentation, output_path: Path) -> None:
        import pymupdf

        document = pymupdf.open()
        width, height = 595, 842
        margin = 45
        page = document.new_page(width=width, height=height)
        y = margin
        if presentation.title:
            page.insert_text(
                (margin, y + 18),
                presentation.title,
                fontsize=18,
                fontname="helv",
            )
            y += 40
        for paragraph in presentation.paragraphs:
            text = paragraph.strip()
            if not text:
                continue
            remaining = text
            while remaining:
                rect = pymupdf.Rect(margin, y, width - margin, height - margin)
                if rect.height < 30:
                    page = document.new_page(width=width, height=height)
                    y = margin
                    rect = pymupdf.Rect(margin, y, width - margin, height - margin)
                result = page.insert_textbox(
                    rect,
                    remaining,
                    fontsize=10.5,
                    fontname="helv",
                    lineheight=1.35,
                )
                if result >= 0:
                    y = height - margin - result + 8
                    remaining = ""
                else:
                    words = remaining.split()
                    if len(words) <= 1:
                        remaining = ""
                        break
                    cut = max(1, int(len(words) * 0.7))
                    chunk = " ".join(words[:cut])
                    page.insert_textbox(
                        rect,
                        chunk,
                        fontsize=10.5,
                        fontname="helv",
                        lineheight=1.35,
                    )
                    remaining = " ".join(words[cut:])
                    page = document.new_page(width=width, height=height)
                    y = margin
        data = document.tobytes(garbage=3, deflate=True)
        document.close()
        output_path.write_bytes(data)


def renderer_candidates(preference: str | None = "auto") -> tuple[PdfRenderer, ...]:
    """Return the renderer fallback chain for a preference."""
    key = normalize_label(preference or "auto")
    native = (ReportLabRenderer(), PyMuPdfRenderer())
    weasy = WeasyPrintRenderer()
    if key in {"reportlab", "report lab"}:
        return (ReportLabRenderer(), PyMuPdfRenderer(), weasy)
    if key in {"pymupdf", "fitz", "mupdf"}:
        return (PyMuPdfRenderer(), ReportLabRenderer(), weasy)
    return (weasy, *native)


def _today(clock: Callable[[], datetime | date] | None) -> str:
    value = clock() if clock is not None else datetime.now()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    try:
        temp.write_text(content, encoding="utf-8", newline="\n")
        os.replace(temp, path)
    except OSError as exc:
        raise PdfWorkflowError("WRITE_FAILED", str(path)) from exc
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)


def _append_audit_log(path: Path, section: str) -> None:
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise PdfWorkflowError("LOG_READ_FAILED", str(path)) from exc
        content = existing.rstrip() + "\n\n---\n\n" + section
    else:
        content = section
    _atomic_write_text(path, content)


def _log_section(
    *,
    day: str,
    folder: TaskFolderLayout,
    source_path: Path,
    output_path: Path,
    html_path: Path,
    renderer_name: str,
    page_count: int,
    counts: Mapping[str, int],
    warnings: Sequence[str],
) -> str:
    lines = [
        f"# Journal d'audit — refonte PDF — {day}",
        "",
        f"- Dossier projet : {folder.root}",
        f"- PDF source : {source_path}",
        f"- PDF de sortie : {output_path}",
        f"- Gabarit HTML : {html_path}",
        f"- Renderer : {renderer_name}",
        f"- Pages source : {page_count}",
        "",
        "## Modifications appliquées",
        "",
        f"- Suppressions : {counts.get(ModificationOperation.SUPPRIMER.value, 0)}",
        f"- Remplacements : {counts.get(ModificationOperation.REMPLACER.value, 0)}",
        f"- Ajouts : {counts.get(ModificationOperation.AJOUTER.value, 0)}",
        "",
    ]
    if warnings:
        lines.extend(["## Avertissements", ""])
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")
    return "\n".join(lines)


class DeclarativePdfService:
    """Apply the declarative PDF / PLV redesign workflow to one project folder."""

    def __init__(
        self,
        task: DeclarativeTask | str | os.PathLike[str],
        *,
        renderer: str | PdfRenderer | Callable[[str, Path], None] | None = None,
        clock: Callable[[], datetime | date] | None = None,
    ) -> None:
        self._task = task if isinstance(task, DeclarativeTask) else load_task(task)
        if (
            renderer is not None
            and not isinstance(renderer, str)
            and not hasattr(renderer, "render")
            and callable(renderer)
        ):
            renderer = _CallableRenderer(renderer)
        elif (
            renderer is not None
            and not isinstance(renderer, str)
            and hasattr(renderer, "render")
            and _renderer_uses_html(renderer.render) is True
        ):
            renderer = _HtmlObjectRenderer(renderer)
        self._renderer = renderer
        self._clock = clock

    @property
    def task(self) -> DeclarativeTask:
        return self._task

    @property
    def folder(self) -> TaskFolderLayout:
        return self._task.folder

    @property
    def brief(self):
        return self._task.brief

    def _resolve_logo(self, configured: str | None) -> tuple[Path | None, tuple[str, ...]]:
        warnings: list[str] = []
        assets = self.folder.assets
        if configured:
            raw = Path(configured)
            if raw.is_absolute():
                resolved = ensure_contained(assets, raw)
                if resolved.is_file():
                    return resolved, ()
                warnings.append(f"logo_introuvable:{configured}")
                return None, tuple(warnings)
            parts = raw.parts
            if parts and parts[0].casefold() == "assets":
                raw = Path(*parts[1:])
            candidate = ensure_contained(assets, assets / raw)
            if candidate.is_file():
                return candidate, ()
            warnings.append(f"logo_introuvable:{configured}")
            return None, tuple(warnings)
        default = ensure_contained(assets, assets / "logo.png")
        if default.is_file():
            return default, ()
        if assets.is_dir():
            for pattern in ("logo.*", "*.png", "*.jpg", "*.jpeg", "*.svg"):
                matches = sorted(
                    candidate
                    for candidate in assets.glob(pattern)
                    if candidate.is_file()
                )
                if matches:
                    return ensure_contained(assets, matches[0]), ()
        return None, ()

    def build_presentation(
        self,
    ) -> tuple[Presentation, dict[str, int], tuple[str, ...], ExtractedPdf]:
        if self.brief.workflow.value != "PDF_PRESENTATION":
            raise PdfWorkflowError("WRONG_WORKFLOW", self.brief.workflow.value)
        source_path = self.folder.original_pdf
        if not source_path.is_file():
            raise PdfWorkflowError("ORIGINAL_NOT_FOUND", str(source_path))
        ensure_contained(self.folder.root, source_path)
        extraction = extract_pdf(source_path)
        content, counts, warnings = apply_content_modifications(
            extraction.text, self.brief.modifications
        )
        logo_path, logo_warnings = self._resolve_logo(self.brief.constraints.logo)
        warnings = tuple(dict.fromkeys((*warnings, *logo_warnings, *self.brief.warnings)))
        title = (self.brief.constraints.title or "").strip()
        if not title:
            candidate = self.brief.target.strip()
            if candidate and not re.search(r"[./\\]", candidate) and len(candidate) <= 120:
                title = candidate
        paragraphs = _split_paragraphs(content)
        html = build_presentation_html(
            title=title,
            paragraphs=paragraphs,
            palette=self.brief.constraints.palette,
            columns=self.brief.constraints.columns,
            logo_path=logo_path,
            paper_format=self.brief.constraints.paper_format,
        )
        presentation = Presentation(
            title=title,
            source_text=content,
            paragraphs=paragraphs,
            html=html,
            palette=dict(self.brief.constraints.palette),
            columns=self.brief.constraints.columns,
            logo_path=logo_path,
            paper_format=self.brief.constraints.paper_format,
            base_url=str(self.folder.root),
        )
        return presentation, counts, warnings, extraction

    def run(self) -> PdfRunResult:
        presentation, counts, warnings, extraction = self.build_presentation()
        folder = self.folder
        day = _today(self._clock)
        output_name = self.brief.constraints.output_name or Path(PDF_OUTPUT).name
        output_path = ensure_contained(folder.output, folder.output / output_name)
        html_name = str(Path(output_name).with_suffix(".html"))
        html_path = ensure_contained(folder.output, folder.output / html_name)
        _atomic_write_text(html_path, presentation.html)

        warnings_list = list(warnings)
        renderer_used = ""
        temp_output = output_path.with_name(f".{output_path.name}.tmp")
        injected = self._renderer if not isinstance(self._renderer, str) else None
        candidates: Sequence[PdfRenderer]
        if injected is not None:
            candidates = (injected,)
        else:
            candidates = renderer_candidates(self._renderer or "auto")

        last_error: Exception | None = None
        for renderer in candidates:
            try:
                try:
                    renderer.render(presentation, temp_output)
                except TypeError:
                    if getattr(renderer, "_html_only", False):
                        raise
                    if temp_output.exists():
                        temp_output.unlink(missing_ok=True)
                    renderer.render(presentation.html, temp_output)
                if not temp_output.is_file() or temp_output.stat().st_size == 0:
                    raise PdfWorkflowError("RENDER_FAILED", f"{renderer.name}: empty output")
                os.replace(temp_output, output_path)
                renderer_used = renderer.name
                break
            except Exception as exc:
                last_error = exc
                warnings_list.append(
                    f"renderer_echec:{getattr(renderer, 'name', type(renderer).__name__)}:{type(exc).__name__}"
                )
                if temp_output.exists():
                    temp_output.unlink(missing_ok=True)
        if not renderer_used:
            raise PdfWorkflowError("RENDER_FAILED", str(last_error))

        log_path = ensure_contained(folder.root, folder.log / f"{day}.md")
        section = _log_section(
            day=day,
            folder=folder,
            source_path=folder.original_pdf,
            output_path=output_path,
            html_path=html_path,
            renderer_name=renderer_used,
            page_count=extraction.page_count,
            counts=counts,
            warnings=warnings_list,
        )
        _append_audit_log(log_path, section)

        return PdfRunResult(
            output_path=output_path,
            html_path=html_path,
            log_path=log_path,
            renderer=renderer_used,
            page_count=extraction.page_count,
            modification_counts=counts,
            warnings=tuple(dict.fromkeys(warnings_list)),
            date=day,
        )

    def render(self) -> PdfRunResult:
        """Alias of :meth:`run` for the PDF redesign workflow."""
        return self.run()


def run_pdf_workflow(
    task: DeclarativeTask | str | os.PathLike[str],
    *,
    renderer: str | PdfRenderer | None = None,
    clock: Callable[[], datetime | date] | None = None,
) -> PdfRunResult:
    """Convenience entry point for the declarative PDF workflow."""
    return DeclarativePdfService(task, renderer=renderer, clock=clock).run()


PdfService = DeclarativePdfService
redesign_pdf = run_pdf_workflow
