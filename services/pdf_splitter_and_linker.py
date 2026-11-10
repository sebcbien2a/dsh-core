"""P21 - Physical multi-document PDF splitting and relative OneDrive links.

Grouped scans (``factures clients.pdf``, supplier batches) are physically cut
into one clean PDF per accounting piece under
``PIECES_JOINTES/<VENTES|ACHATS>/``. Every Excel workbook produced by the
accounting flow exposes a trailing ``Pièce PDF (Clic)`` column whose hyperlink
is *relative* to the workbook directory, so a shared OneDrive folder keeps
working on any computer (never an absolute ``A:\\...`` path).
"""

from __future__ import annotations

import os
import re
import shutil
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from services.folder_tree_service import ensure_contained

PIECES_DIR_NAME = "PIECES_JOINTES"
SALES_SUBDIR = "VENTES"
PURCHASES_SUBDIR = "ACHATS"
PIECE_LINK_HEADER = "Pièce PDF (Clic)"
DOCUMENT_TYPE_INVOICE = "FACTURE"
DOCUMENT_TYPE_CREDIT_NOTE = "AVOIR"

_CUSTOMER_NUMBER = re.compile(r"\b(FA\d{3,})\b", re.IGNORECASE)
_CUSTOMER_LABEL = re.compile(r"\bClient\s*[:\-]\s*([^\n\r]+)", re.IGNORECASE)
_SUPPLIER_HEADER = re.compile(
    r"\b(Facture|Avoir)\s+([A-Z0-9][A-Z0-9._/-]{2,})\s+Du\s+"
    r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
    re.IGNORECASE,
)
_SUPPLIER_LABEL = re.compile(
    r"\b(Facture|Avoir)\s*N[°o]?\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]{2,})",
    re.IGNORECASE,
)
_DATE_LABEL = re.compile(
    r"\bDate\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
    re.IGNORECASE,
)
_DOCUMENT_DATE = re.compile(
    r"\b(?:Du|Date de valeur|Date)\s*[:\-]?\s*"
    r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
    re.IGNORECASE,
)
_ISSUER_VAT = re.compile(
    r"N[°o]\s*TVA\s*intracom\s*:\s*(FR\s*[0-9A-Z]{2}(?:\s*\d){9})",
    re.IGNORECASE,
)
_CLIENT_NAME_STOPWORDS = (
    "total",
    "net ",
    "facture",
    "date",
    "client",
    "code",
    "base",
    "taux",
    "montant",
    "n°",
    "siret",
    "tva",
    "réserve",
    "penalit",
    "pénalit",
    "facture payable",
    "agence",
    "capital",
    "autre s.a.s",
)


class PdfSplitError(ValueError):
    """Invalid split request; ``code`` is stable for tests and logs."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class PdfPiece:
    """One physical document extracted from a grouped PDF."""

    document_number: str
    document_type: str
    start_page: int
    end_page: int
    party: str = ""
    document_date: date | None = None
    source_path: Path | None = None
    path: Path | None = None

    @property
    def page_count(self) -> int:
        return self.end_page - self.start_page + 1

    @property
    def filename(self) -> str:
        return self.path.name if self.path is not None else ""


@dataclass(frozen=True)
class PdfSplitReport:
    """Observable outcome of one physical split."""

    source: Path
    pieces: tuple[PdfPiece, ...]
    page_count: int
    pieces_dir: Path

    @property
    def written(self) -> int:
        return sum(1 for piece in self.pieces if piece.path is not None)


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in text if not unicodedata.combining(char))


def sanitize_component(value: Any, *, uppercase: bool = False, limit: int = 48) -> str:
    """Return a filesystem-safe, accent-free component."""
    text = _fold(value)
    if uppercase:
        text = text.upper()
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    return (text or "PIECE")[:limit].strip("_") or "PIECE"


def parse_date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(text[:10], pattern).date()
        except ValueError:
            continue
    return None


def _extract_date(text: str) -> date | None:
    for pattern in (_DATE_LABEL, _DOCUMENT_DATE):
        match = pattern.search(text)
        if match is not None:
            parsed = parse_date_value(match.group(1))
            if parsed is not None:
                return parsed
    return None


def _extract_customer(text: str) -> str:
    """Return the printed customer name, never the issuer VAT/SIRET block."""
    issuer = _ISSUER_VAT.search(text)
    if issuer is not None:
        for raw_line in text[issuer.end() :].splitlines():
            value = " ".join(raw_line.replace("\u00a0", " ").split())
            if not value:
                continue
            folded = _fold(value).casefold()
            if any(folded.startswith(stop) for stop in _CLIENT_NAME_STOPWORDS):
                continue
            if re.fullmatch(r"[\d\s.,]+", value):
                continue
            return value
    match = _CUSTOMER_LABEL.search(text)
    return match.group(1).strip() if match is not None else ""


def normalized_sales_filename(
    document_number: str,
    document_date: date | None,
    customer: str,
    *,
    suffix: str = ".pdf",
) -> str:
    """``FA16443_2026-02-28_GP_AUTO_36.pdf``."""
    number = sanitize_component(document_number, uppercase=True)
    party = sanitize_component(customer, uppercase=True)
    if document_date is None:
        return f"{number}_{party}{suffix}"
    return f"{number}_{document_date.isoformat()}_{party}{suffix}"


def normalized_purchase_filename(
    supplier: str,
    document_type: str,
    document_number: str,
    document_date: date | None,
    *,
    suffix: str = ".pdf",
) -> str:
    """``ROBERT_BOSCH_SIA_ABRASIVES_AVOIR_3956106092_2026-09-14.pdf``."""
    name = sanitize_component(supplier, uppercase=True)
    kind = sanitize_component(document_type, uppercase=True)
    number = sanitize_component(document_number, uppercase=True)
    if document_date is None:
        return f"{name}_{kind}_{number}{suffix}"
    return f"{name}_{kind}_{number}_{document_date.isoformat()}{suffix}"


def _pdf_pages(source: str | Path | bytes | bytearray) -> list[str]:
    try:
        import fitz

        if hasattr(fitz, "TOOLS"):
            fitz.TOOLS.mupdf_display_errors(False)
    except ImportError as exc:  # pragma: no cover - dependency is installed
        raise PdfSplitError("PDF_SUPPORT_UNAVAILABLE") from exc
    try:
        if isinstance(source, (bytes, bytearray)):
            document = fitz.open(stream=bytes(source), filetype="pdf")
        else:
            document = fitz.open(str(source))
    except Exception as exc:  # noqa: BLE001 - normalised error
        raise PdfSplitError("PDF_EXTRACTION_FAILED", str(exc)) from exc
    try:
        return [page.get_text("text") for page in document]
    finally:
        document.close()


def detect_customer_page_groups(
    pages: Sequence[str],
) -> list[tuple[str, int, int, str]]:
    """Return ``(invoice_number, start_page, end_page, text)`` groups."""
    groups: dict[str, list[int]] = {}
    order: list[str] = []
    current: str | None = None
    for page_index, text in enumerate(pages, start=1):
        match = _CUSTOMER_NUMBER.search(text)
        if match is not None:
            current = match.group(1).upper()
            if current not in groups:
                groups[current] = []
                order.append(current)
            groups[current].append(page_index)
        elif current is not None:
            groups[current].append(page_index)
    return [
        (
            number,
            groups[number][0],
            groups[number][-1],
            "\n".join(pages[index - 1] for index in groups[number]),
        )
        for number in order
    ]


def detect_supplier_page_groups(
    pages: Sequence[str],
) -> list[tuple[str, str, date | None, int, int, str]]:
    """Return ``(type, number, date, start, end, text)`` supplier groups."""
    groups: dict[tuple[str, str], list[int]] = {}
    order: list[tuple[str, str]] = []
    dates: dict[tuple[str, str], date | None] = {}
    current: tuple[str, str] | None = None
    for page_index, text in enumerate(pages, start=1):
        match = _SUPPLIER_HEADER.search(text)
        if match is None:
            match = _SUPPLIER_LABEL.search(text)
        if match is not None:
            kind = _fold(match.group(1)).upper()
            number = match.group(2).upper()
            current = (kind, number)
            if current not in groups:
                groups[current] = []
                order.append(current)
                dates[current] = (
                    parse_date_value(match.group(3))
                    if match.lastindex and match.lastindex >= 3
                    else None
                )
            groups[current].append(page_index)
        elif current is not None:
            groups[current].append(page_index)
    return [
        (
            kind,
            number,
            dates[(kind, number)],
            groups[(kind, number)][0],
            groups[(kind, number)][-1],
            "\n".join(
                pages[index - 1] for index in groups[(kind, number)]
            ),
        )
        for kind, number in order
    ]


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    index = 1
    while True:
        candidate = path.with_name(f"{path.stem}__{index}{path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _write_pages(
    source: Path,
    target: Path,
    *,
    start_page: int,
    end_page: int,
) -> Path:
    try:
        import fitz

        if hasattr(fitz, "TOOLS"):
            fitz.TOOLS.mupdf_display_errors(False)
    except ImportError as exc:  # pragma: no cover
        raise PdfSplitError("PDF_SUPPORT_UNAVAILABLE") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    unique = _unique_path(target)
    temporary = unique.with_name(unique.name + ".tmp-pdf")
    try:
        document = fitz.open(str(source))
    except Exception as exc:  # noqa: BLE001
        raise PdfSplitError("PDF_EXTRACTION_FAILED", str(exc)) from exc
    try:
        output = fitz.open()
        try:
            output.insert_pdf(
                document,
                from_page=max(0, start_page - 1),
                to_page=max(0, end_page - 1),
            )
            output.save(str(temporary))
        finally:
            output.close()
        os.replace(temporary, unique)
    except Exception as exc:  # noqa: BLE001
        temporary.unlink(missing_ok=True)
        raise PdfSplitError("PDF_WRITE_FAILED", str(exc)) from exc
    finally:
        document.close()
    return unique


def pieces_root_for(export_dir: str | os.PathLike[str]) -> Path:
    return Path(export_dir) / PIECES_DIR_NAME


def split_customer_invoices_pdf(
    source: str | os.PathLike[str],
    export_dir: str | os.PathLike[str],
    *,
    pieces_dir: str | os.PathLike[str] | None = None,
) -> PdfSplitReport:
    """Physically cut a grouped customer PDF into ``PIECES_JOINTES/VENTES``."""
    source_path = Path(source)
    pages = _pdf_pages(source_path)
    groups = detect_customer_page_groups(pages)
    if not groups:
        raise PdfSplitError("NO_CUSTOMER_INVOICE_IN_PDF", source_path.name)
    root = (
        Path(pieces_dir)
        if pieces_dir is not None
        else pieces_root_for(export_dir) / SALES_SUBDIR
    )
    root.mkdir(parents=True, exist_ok=True)
    pieces: list[PdfPiece] = []
    for number, start, end, text in groups:
        customer = _extract_customer(text)
        document_date = _extract_date(text)
        filename = normalized_sales_filename(number, document_date, customer)
        target = ensure_contained(root, root / filename)
        written = _write_pages(
            source_path, target, start_page=start, end_page=end
        )
        pieces.append(
            PdfPiece(
                document_number=number,
                document_type=DOCUMENT_TYPE_INVOICE,
                start_page=start,
                end_page=end,
                party=customer,
                document_date=document_date,
                source_path=source_path,
                path=written,
            )
        )
    return PdfSplitReport(
        source=source_path,
        pieces=tuple(pieces),
        page_count=len(pages),
        pieces_dir=root,
    )


def split_supplier_pdf(
    source: str | os.PathLike[str],
    export_dir: str | os.PathLike[str],
    supplier: str,
    *,
    pieces_dir: str | os.PathLike[str] | None = None,
) -> PdfSplitReport:
    """Physically cut a grouped supplier PDF into ``PIECES_JOINTES/ACHATS``."""
    source_path = Path(source)
    pages = _pdf_pages(source_path)
    groups = detect_supplier_page_groups(pages)
    if not groups:
        raise PdfSplitError("NO_SUPPLIER_DOCUMENT_IN_PDF", source_path.name)
    root = (
        Path(pieces_dir)
        if pieces_dir is not None
        else pieces_root_for(export_dir) / PURCHASES_SUBDIR
    )
    root.mkdir(parents=True, exist_ok=True)
    pieces: list[PdfPiece] = []
    for kind, number, document_date, start, end, _text in groups:
        document_type = (
            DOCUMENT_TYPE_CREDIT_NOTE
            if _fold(kind).upper() == DOCUMENT_TYPE_CREDIT_NOTE
            else DOCUMENT_TYPE_INVOICE
        )
        filename = normalized_purchase_filename(
            supplier, document_type, number, document_date
        )
        target = ensure_contained(root, root / filename)
        written = _write_pages(
            source_path, target, start_page=start, end_page=end
        )
        pieces.append(
            PdfPiece(
                document_number=number,
                document_type=document_type,
                start_page=start,
                end_page=end,
                party=supplier,
                document_date=document_date,
                source_path=source_path,
                path=written,
            )
        )
    return PdfSplitReport(
        source=source_path,
        pieces=tuple(pieces),
        page_count=len(pages),
        pieces_dir=root,
    )


def copy_unit_piece(
    source: str | os.PathLike[str],
    export_dir: str | os.PathLike[str],
    *,
    side: str,
    document_type: str,
    document_number: str,
    document_date: date | None,
    party: str,
    filename: str | None = None,
) -> Path:
    """Copy one already-unitary validated piece into ``PIECES_JOINTES``."""
    source_path = Path(source)
    if not source_path.is_file():
        raise PdfSplitError("PIECE_NOT_FOUND", str(source_path))
    selected = SALES_SUBDIR if side.upper() == SALES_SUBDIR else PURCHASES_SUBDIR
    root = pieces_root_for(export_dir) / selected
    root.mkdir(parents=True, exist_ok=True)
    if filename is None:
        if selected == SALES_SUBDIR:
            filename = normalized_sales_filename(
                document_number, document_date, party
            )
        else:
            filename = normalized_purchase_filename(
                party, document_type, document_number, document_date
            )
    target = ensure_contained(root, root / filename)
    if target.exists() and source_path.resolve() == target.resolve():
        return target
    unique = _unique_path(target)
    temporary = unique.with_name(unique.name + ".tmp-piece")
    shutil.copyfile(source_path, temporary)
    os.replace(temporary, unique)
    return unique


def relative_link(target: str | os.PathLike[str], workbook_path: str | os.PathLike[str]) -> str:
    """Return a POSIX relative link from ``workbook_path``; reject absolute."""
    target_path = Path(target)
    workbook = Path(workbook_path)
    if target_path.is_absolute():
        try:
            relative = os.path.relpath(target_path, workbook.parent)
        except ValueError as exc:
            raise PdfSplitError("RELATIVE_LINK_FAILED", str(target_path)) from exc
    else:
        relative = os.fspath(target_path)
    relative = os.fspath(Path(relative)).replace("\\", "/")
    if relative.startswith("/") or re.match(r"^[A-Za-z]:", relative):
        raise PdfSplitError("ABSOLUTE_LINK_FORBIDDEN", relative)
    return relative


def set_piece_link(
    cell: Any,
    target: str | os.PathLike[str],
    workbook_path: str | os.PathLike[str],
) -> str:
    """Write a relative hyperlink into one openpyxl cell."""
    from openpyxl.styles import Font

    link = relative_link(target, workbook_path)
    cell.value = link
    cell.hyperlink = link
    cell.font = Font(color="0563C1", underline="single")
    return link


def find_piece_for_document(
    export_dir: str | os.PathLike[str],
    document_number: str,
    *,
    side: str | None = None,
) -> Path | None:
    """Find an existing split piece from its document number."""
    needle = re.sub(r"[^A-Z0-9]", "", sanitize_component(document_number, uppercase=True))
    if not needle:
        return None
    roots: list[Path] = []
    base = pieces_root_for(export_dir)
    if side is not None:
        roots.append(base / (SALES_SUBDIR if side.upper() == SALES_SUBDIR else PURCHASES_SUBDIR))
    else:
        roots.extend((base / SALES_SUBDIR, base / PURCHASES_SUBDIR, base))
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.pdf")):
            if "SOURCE_OFX" in path.name.upper():
                # P27-nonies - a raw OFX statement is never an invoice PDF.
                continue
            candidate = re.sub(
                r"[^A-Z0-9]", "", sanitize_component(path.stem, uppercase=True)
            )
            if needle in candidate:
                return path
    return None


def ensure_piece_links(
    sheet: Any,
    workbook_path: str | os.PathLike[str],
    links: Iterable[str | os.PathLike[str] | None],
    *,
    header: str = PIECE_LINK_HEADER,
    start_row: int = 2,
) -> int:
    """Append the trailing ``Pièce PDF (Clic)`` column and write hyperlinks."""
    from openpyxl.styles import Alignment, Font, PatternFill

    column = sheet.max_column + 1
    header_cell = sheet.cell(row=1, column=column, value=header)
    header_cell.font = Font(bold=True, color="FFFFFF")
    header_cell.fill = PatternFill("solid", fgColor="1F4E78")
    header_cell.alignment = Alignment(horizontal="center", vertical="center")
    written = 0
    for offset, target in enumerate(links):
        if target is None:
            continue
        cell = sheet.cell(row=start_row + offset, column=column)
        set_piece_link(cell, target, workbook_path)
        written += 1
    return written


__all__ = [
    "DOCUMENT_TYPE_CREDIT_NOTE",
    "DOCUMENT_TYPE_INVOICE",
    "PIECES_DIR_NAME",
    "PIECE_LINK_HEADER",
    "PURCHASES_SUBDIR",
    "PdfPiece",
    "PdfSplitError",
    "PdfSplitReport",
    "SALES_SUBDIR",
    "copy_unit_piece",
    "detect_customer_page_groups",
    "detect_supplier_page_groups",
    "ensure_piece_links",
    "find_piece_for_document",
    "normalized_purchase_filename",
    "normalized_sales_filename",
    "parse_date_value",
    "pieces_root_for",
    "relative_link",
    "sanitize_component",
    "set_piece_link",
    "split_customer_invoices_pdf",
    "split_supplier_pdf",
]
