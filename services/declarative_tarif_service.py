"""Non-destructive supplier price merge into ``tarifs-existants.xlsx`` (P15.2).

Workflow 1 of the declarative ``instructions.md`` engine. The source workbook is
never modified: the merged result is written atomically to
``output/tarifs-mis-a-jour.xlsx`` and an audit journal is appended to
``log/{YYYY-MM-DD}.md``.

Absolute non-deletion guarantee: every pre-existing data row is kept. A row
whose article code is absent from the supplier files is written back with
``statut = "inchangé"``; rows are never removed, sorted or compacted. Matching
is done on the article ``code`` (case-insensitive, whitespace-insensitive):

- new code -> appended with ``statut = "nouveau"`` and an added date;
- known code with a different price -> ``statut = "modifié"`` and the previous
  main price stored in ``ancien_prix``;
- known code with the same price -> ``statut = "inchangé"``.

Prices are completed in both directions with standard half-up rounding to two
decimals (configurable through the instruction price rules):

- ``prix_kg = round(prix_conditionnement / poids, 2)``;
- ``prix_conditionnement = round(prix_kg * poids, 2)``;
- both missing -> ``alerte = "prix manquant"``.

Excel formatting is repaired deterministically: merges inside data rows are
removed (a merge fully contained in the header row may stay), thin borders are
applied only to non-empty data cells, the header gets a double bottom border,
article codes are forced to text and a ``Résumé`` sheet is inserted first.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from schemas.declarative_task_schema import (
    ALERT_MISSING_PRICE,
    STATUS_NEW,
    STATUS_UNCHANGED,
    STATUS_UPDATED,
    TARIFS_OUTPUT,
    DeclarativeTask,
    DeclarativeTaskError,
    TaskFolderLayout,
    ensure_contained,
    load_task,
    normalize_label,
)

SUMMARY_SHEET = "Résumé"
DEFAULT_SHEET = 0
HEADER_SCAN_LIMIT = 15
PRICE_TOLERANCE = 1e-9

CANONICAL_COLUMNS: tuple[str, ...] = (
    "code",
    "designation",
    "weight",
    "packaging_price",
    "kg_price",
    "ancien_prix",
    "statut",
    "alerte",
    "date_ajout",
)

_HEADER_ALIASES: Mapping[str, tuple[str, ...]] = {
    "code": (
        "code",
        "code article",
        "article",
        "reference",
        "ref",
        "sku",
        "code produit",
        "code produit article",
        "code interne",
        "code fournisseur",
    ),
    "designation": (
        "designation",
        "libelle",
        "produit",
        "nom",
        "nom article",
        "description",
        "article designation",
        "designation article",
        "nom produit",
        "nom du produit",
    ),
    "weight": (
        "poids",
        "poids kg",
        "poids_kg",
        "weight",
        "kg",
        "poids unitaire",
        "poids net",
        "poids du conditionnement",
    ),
    "packaging_price": (
        "prix_conditionnement",
        "prix conditionnement",
        "prix cond",
        "prix_cond",
        "prix cdt",
        "prix carton",
        "prix du conditionnement",
        "prix conditionne",
        "conditionnement prix",
        "packaging price",
        "prix colis",
        "prix unite",
        "prix",
        "prix unitaire",
        "prix ht",
        "prix ttc",
        "prix de vente",
        "prix public",
    ),
    "kg_price": (
        "prix_kg",
        "prix kg",
        "prix au kg",
        "prix kilo",
        "prix au kilo",
        "kg price",
        "price kg",
        "prix par kg",
    ),
    "ancien_prix": (
        "ancien_prix",
        "ancien prix",
        "old price",
        "prix precedent",
        "prix précédent",
        "ancien prix conditionnement",
    ),
    "statut": ("statut", "status", "etat", "state"),
    "alerte": ("alerte", "alert", "avertissement", "warning", "anomalie"),
    "date_ajout": (
        "date_ajout",
        "date ajout",
        "date d ajout",
        "date d'ajout",
        "added date",
        "date creation",
        "date de creation",
    ),
}

_CANONICAL_BY_ALIAS: dict[str, str] = {}
for _canonical, _aliases in _HEADER_ALIASES.items():
    for _alias in _aliases:
        _CANONICAL_BY_ALIAS[normalize_label(_alias)] = _canonical

_SUPPLIER_HEADER_ALIASES: Mapping[str, tuple[str, ...]] = {
    "code": ("code", "code article", "article", "reference", "ref", "sku"),
    "designation": (
        "designation",
        "libelle",
        "produit",
        "nom",
        "nom produit",
        "description",
    ),
    "weight": ("poids", "poids kg", "poids_kg", "weight", "kg"),
    "packaging_price": (
        "prix_conditionnement",
        "prix conditionnement",
        "prix cond",
        "prix cdt",
        "prix carton",
        "packaging price",
        "prix",
        "prix unitaire",
    ),
    "kg_price": ("prix_kg", "prix kg", "prix au kg", "prix kilo", "kg price"),
}

_SUPPLIER_CANONICAL_BY_ALIAS: dict[str, str] = {}
for _canonical, _aliases in _SUPPLIER_HEADER_ALIASES.items():
    for _alias in _aliases:
        _SUPPLIER_CANONICAL_BY_ALIAS[normalize_label(_alias)] = _canonical

_SKIP_CODES = {
    "code",
    "total",
    "totaux",
    "page",
    "tarif",
    "tarifs",
    "article",
    "reference",
    "designation",
    "sous total",
    "sous-total",
}
_CODE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/+-]{0,63}\Z")
_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


class TarifWorkflowError(DeclarativeTaskError):
    """Invalid tariff merge operation; ``code`` is stable for tests and logs."""


@dataclass(frozen=True)
class SupplierPrice:
    """One article price extracted from a supplier PDF."""

    code: str
    designation: str = ""
    weight: float | None = None
    packaging_price: float | None = None
    kg_price: float | None = None
    alert: str = ""
    source: str = ""


@dataclass(frozen=True)
class TarifRowResult:
    """Audit view of one output row."""

    code: str
    designation: str = ""
    statut: str = STATUS_UNCHANGED
    ancien_prix: float | None = None
    packaging_price: float | None = None
    kg_price: float | None = None
    weight: float | None = None
    alert: str = ""
    date_ajout: str = ""
    row_number: int = 0


@dataclass(frozen=True)
class TarifRunResult:
    """Outcome of one non-destructive tariff merge."""

    output_path: Path
    log_path: Path
    rows: tuple[TarifRowResult, ...]
    counts: Mapping[str, int]
    warnings: tuple[str, ...] = ()
    supplier_files: tuple[str, ...] = ()
    date: str = ""

    @property
    def output(self) -> Path:
        return self.output_path

    @property
    def log(self) -> Path:
        return self.log_path


@dataclass
class _ExistingRow:
    row_number: int
    code: str
    designation: str
    weight: float | None
    packaging_price: float | None
    kg_price: float | None
    ancien_prix: float | None
    statut: str
    alert: str
    date_ajout: str


def round_price(value: float, decimals: int = 2) -> float:
    """Standard financial rounding (half-up) to ``decimals`` places."""
    if value is None or not math.isfinite(float(value)):
        raise TarifWorkflowError("INVALID_PRICE", repr(value))
    quantum = Decimal(1).scaleb(-int(decimals))
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def _to_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).strip()
    if not text or text.startswith("="):
        return None
    text = (
        text.replace("\u00a0", "")
        .replace("\u202f", "")
        .replace(" ", "")
        .replace("€", "")
        .replace("%", "")
    )
    text = re.sub(r"(?i)\beur\b", "", text)
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    match = _NUMBER_RE.search(text)
    if match is None:
        return None
    try:
        number = float(match.group().replace(",", "."))
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def normalize_article_code(value: object) -> str:
    """Canonical display code (leading zeroes preserved, floats unwrapped)."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value).strip()
    text = str(value).strip()
    if re.fullmatch(r"-?\d+\.0+", text):
        return text.split(".", 1)[0]
    return text


def article_key(value: object) -> str:
    return normalize_article_code(value).casefold()


def complete_prices(
    code: object,
    designation: object = "",
    weight: object = None,
    packaging_price: object = None,
    kg_price: object = None,
    *,
    rules: Any | None = None,
    source: str = "",
    alert: str = "",
) -> SupplierPrice:
    """Apply the bidirectional price completion rules (conditioning <-> kg)."""
    decimals = int(getattr(rules, "decimals", 2) or 2)
    missing_alert = str(getattr(rules, "missing_alert", ALERT_MISSING_PRICE) or ALERT_MISSING_PRICE)
    normalized_code = normalize_article_code(code)
    if not normalized_code:
        raise TarifWorkflowError("INVALID_ARTICLE_CODE", repr(code))
    weight_value = _to_float(weight)
    packaging_value = _to_float(packaging_price)
    kg_value = _to_float(kg_price)
    current_alert = alert.strip()

    if packaging_value is None and kg_value is None:
        current_alert = current_alert or missing_alert
    elif packaging_value is None:
        if kg_value is not None and weight_value is not None and weight_value > 0:
            packaging_value = round_price(kg_value * weight_value, decimals)
        else:
            current_alert = current_alert or missing_alert
    elif kg_value is None:
        if packaging_value is not None and weight_value is not None and weight_value > 0:
            kg_value = round_price(packaging_value / weight_value, decimals)
        else:
            current_alert = current_alert or missing_alert

    return SupplierPrice(
        code=normalized_code,
        designation=str(designation or "").strip(),
        weight=weight_value,
        packaging_price=round_price(packaging_value, decimals)
        if packaging_value is not None
        else None,
        kg_price=round_price(kg_value, decimals) if kg_value is not None else None,
        alert=current_alert,
        source=source,
    )


def _split_cells(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    if "|" in text:
        return [cell.strip() for cell in text.split("|")]
    if "\t" in text:
        return [cell.strip() for cell in text.split("\t")]
    if ";" in text:
        return [cell.strip() for cell in text.split(";")]
    if re.search(r"\s{2,}", text):
        return [cell.strip() for cell in re.split(r"\s{2,}", text)]
    return [text.strip()]


def _supplier_header_map(cells: Sequence[str]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for index, cell in enumerate(cells):
        canonical = _SUPPLIER_CANONICAL_BY_ALIAS.get(normalize_label(cell))
        if canonical and canonical not in mapping:
            mapping[index] = canonical
    return mapping


def _is_supplier_header(cells: Sequence[str]) -> bool:
    mapping = _supplier_header_map(cells)
    canonical = set(mapping.values())
    return "code" in canonical and bool(canonical & {"weight", "packaging_price", "kg_price"})


def _line_key_value(cells: Sequence[str]) -> dict[str, str] | None:
    values: dict[str, str] = {}
    for cell in cells:
        key, value = (None, cell)
        match = re.match(r"^([A-Za-zÀ-ÿ0-9 _./()'-]+?)\s*[=:]\s*(.+)$", cell)
        if match:
            key, value = match.group(1), match.group(2)
        canonical = _SUPPLIER_CANONICAL_BY_ALIAS.get(normalize_label(key)) if key else None
        if canonical is None:
            if not values:
                return None
            continue
        values[canonical] = value.strip()
    return values or None


def _default_supplier_map(cell_count: int) -> dict[int, str]:
    if cell_count >= 5:
        return {
            0: "code",
            1: "designation",
            2: "weight",
            3: "packaging_price",
            4: "kg_price",
        }
    if cell_count == 4:
        return {0: "code", 1: "designation", 2: "weight", 3: "packaging_price"}
    if cell_count == 3:
        return {0: "code", 1: "designation", 2: "packaging_price"}
    return {0: "code"}


_NUMERIC_TOKEN_RE = re.compile(
    r"-?\d+(?:[.,]\d+)?\s*(?:kg|kgs|g|€|eur|euros?)?\Z", re.IGNORECASE
)


def _token_fallback(line: str, rules: Any, source: str) -> SupplierPrice | None:
    tokens = line.split()
    if len(tokens) < 3:
        return None
    code = tokens[0]
    if _CODE_RE.fullmatch(code) is None or normalize_label(code) in _SKIP_CODES:
        return None
    numeric: list[str] = []
    designation_tokens: list[str] = []
    for token in reversed(tokens[1:]):
        if _NUMERIC_TOKEN_RE.fullmatch(token):
            numeric.append(token)
        else:
            designation_tokens = tokens[1 : len(tokens) - len(numeric)]
            break
    if not numeric:
        return None
    numeric.reverse()
    if len(numeric) >= 3:
        weight, packaging_price, kg_price = numeric[-3:]
    elif len(numeric) == 2:
        weight, packaging_price = numeric
        kg_price = None
    else:
        weight = None
        packaging_price = numeric[0]
        kg_price = None
    return complete_prices(
        code=code,
        designation=" ".join(designation_tokens),
        weight=weight,
        packaging_price=packaging_price,
        kg_price=kg_price,
        rules=rules,
        source=source,
    )


def _record_from_mapping(
    values: Mapping[str, object], rules: Any, source: str
) -> SupplierPrice | None:
    code = normalize_article_code(values.get("code"))
    if not code:
        return None
    if normalize_label(code) in _SKIP_CODES or _CODE_RE.fullmatch(code) is None:
        return None
    return complete_prices(
        code=code,
        designation=values.get("designation", ""),
        weight=values.get("weight"),
        packaging_price=values.get("packaging_price"),
        kg_price=values.get("kg_price"),
        rules=rules,
        source=source,
    )


def extract_pdf_text(path: str | os.PathLike[str]) -> str:
    """Extract plain text from a PDF with PyMuPDF (lazy import)."""
    try:
        import pymupdf
    except ImportError as exc:
        raise TarifWorkflowError("PYMUPDF_UNAVAILABLE", str(exc)) from exc
    try:
        document = pymupdf.open(str(path))
    except Exception as exc:
        raise TarifWorkflowError("PDF_UNREADABLE", str(path)) from exc
    try:
        return "\n".join(page.get_text("text") for page in document)
    finally:
        close = getattr(document, "close", None)
        if callable(close):
            close()


def parse_supplier_text(
    text: str, *, source: str = "", rules: Any | None = None
) -> list[SupplierPrice]:
    """Parse deterministic supplier price lines from extracted PDF text."""
    records: list[SupplierPrice] = []
    header_mapping: dict[int, str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        explicit_separator = any(separator in line for separator in ("|", "\t", ";"))
        if not explicit_separator:
            record = _token_fallback(line, rules, source)
            if record is not None:
                records.append(record)
                continue
        cells = _split_cells(line)
        if _is_supplier_header(cells):
            header_mapping = _supplier_header_map(cells)
            continue
        key_values = _line_key_value(cells)
        if key_values is not None:
            record = _record_from_mapping(key_values, rules, source)
            if record is not None:
                records.append(record)
            continue
        mapping = header_mapping or _default_supplier_map(len(cells))
        values: dict[str, object] = {}
        for index, canonical in mapping.items():
            if index < len(cells):
                values[canonical] = cells[index]
        record = _record_from_mapping(values, rules, source)
        if record is not None:
            records.append(record)
    return records


def _same_number(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return abs(left - right) <= PRICE_TOLERANCE


def _merge_duplicate_records(
    records: Iterable[SupplierPrice], warnings: list[str], rules: Any | None = None
) -> tuple[dict[str, SupplierPrice], tuple[str, ...]]:
    merged: dict[str, SupplierPrice] = {}
    order: list[str] = []
    for record in records:
        key = article_key(record.code)
        if key not in merged:
            merged[key] = record
            order.append(key)
            continue
        current = merged[key]
        conflicts = [
            name
            for name, old, new in (
                ("poids", current.weight, record.weight),
                ("prix_conditionnement", current.packaging_price, record.packaging_price),
                ("prix_kg", current.kg_price, record.kg_price),
            )
            if old is not None and new is not None and not _same_number(old, new)
        ]
        if conflicts:
            warnings.append(
                f"doublon_conflit:{record.code}:{current.source}:{record.source}:{','.join(conflicts)}"
            )
        designation = current.designation or record.designation
        completed = complete_prices(
            code=current.code,
            designation=designation,
            weight=current.weight if current.weight is not None else record.weight,
            packaging_price=current.packaging_price
            if current.packaging_price is not None
            else record.packaging_price,
            kg_price=current.kg_price if current.kg_price is not None else record.kg_price,
            rules=rules,
            source=current.source,
        )
        merged[key] = completed
    return {key: merged[key] for key in order}, tuple(order)


def extract_supplier_prices(
    paths: Iterable[str | os.PathLike[str]], *, rules: Any | None = None
) -> tuple[tuple[SupplierPrice, ...], tuple[str, ...]]:
    """Extract every supplier PDF (sorted order) and merge duplicate codes."""
    records: list[SupplierPrice] = []
    warnings: list[str] = []
    for path in sorted(Path(item) for item in paths):
        try:
            text = extract_pdf_text(path)
        except TarifWorkflowError as exc:
            warnings.append(f"fournisseur_illisible:{path.name}:{exc.code}")
            continue
        parsed = parse_supplier_text(text, source=path.name, rules=rules)
        if not parsed:
            warnings.append(f"aucune_ligne_fournisseur:{path.name}")
        records.extend(parsed)
    merged, order = _merge_duplicate_records(records, warnings, rules)
    return tuple(merged[key] for key in order), tuple(warnings)


def _canonical_header(value: object) -> str | None:
    return _CANONICAL_BY_ALIAS.get(normalize_label(value))


def _header_label(canonical: str, rules: Any, date_column: str = "date_ajout") -> str:
    if canonical == "packaging_price":
        return str(getattr(rules, "packaging_column", "prix_conditionnement"))
    if canonical == "weight":
        return str(getattr(rules, "weight_column", "poids"))
    if canonical == "kg_price":
        return str(getattr(rules, "kg_column", "prix_kg"))
    if canonical == "date_ajout":
        return date_column or "date_ajout"
    return {
        "code": "code",
        "designation": "designation",
        "ancien_prix": "ancien_prix",
        "statut": "statut",
        "alerte": "alerte",
    }[canonical]


def _header_map(ws: Any, row: int, rules: Any, date_column: str) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for column in range(1, ws.max_column + 1):
        canonical = _canonical_header(ws.cell(row=row, column=column).value)
        if canonical is None:
            header = normalize_label(ws.cell(row=row, column=column).value)
            for candidate in CANONICAL_COLUMNS:
                if header and header == normalize_label(_header_label(candidate, rules, date_column)):
                    canonical = candidate
                    break
        if canonical is not None and canonical not in mapping:
            mapping[canonical] = column
    return mapping


def _detect_header_row(
    ws: Any, rules: Any, date_column: str, requested: int | None = None
) -> tuple[int, dict[str, int]]:
    if requested is not None:
        mapping = _header_map(ws, requested, rules, date_column)
        if "code" not in mapping:
            raise TarifWorkflowError("MISSING_HEADER", f"row {requested}")
        return requested, mapping
    best_row = 0
    best_map: dict[str, int] = {}
    limit = min(max(ws.max_row, 1), HEADER_SCAN_LIMIT)
    for row in range(1, limit + 1):
        mapping = _header_map(ws, row, rules, date_column)
        if "code" not in mapping:
            continue
        if not (mapping.keys() & {"designation", "weight", "packaging_price", "kg_price"}):
            continue
        if len(mapping) > len(best_map):
            best_row, best_map = row, mapping
    if best_row == 0:
        raise TarifWorkflowError("MISSING_HEADER", "no article code header found")
    return best_row, best_map


def _ensure_columns(
    ws: Any, header_row: int, mapping: dict[str, int], rules: Any, date_column: str
) -> dict[str, int]:
    result = dict(mapping)
    next_column = ws.max_column + 1
    for canonical in CANONICAL_COLUMNS:
        if canonical in result:
            continue
        cell = ws.cell(row=header_row, column=next_column)
        cell.value = _header_label(canonical, rules, date_column)
        result[canonical] = next_column
        next_column += 1
    return result


def _unmerge_data_rows(ws: Any, header_row: int) -> int:
    unmerged = 0
    for merged in list(ws.merged_cells.ranges):
        if merged.min_row == merged.max_row == header_row:
            continue
        ws.unmerge_cells(str(merged))
        unmerged += 1
    return unmerged


def _cell_value(ws: Any, row: int, column: int) -> object:
    return ws.cell(row=row, column=column).value


def _read_existing_rows(
    ws: Any, header_row: int, mapping: Mapping[str, int]
) -> list[_ExistingRow]:
    rows: list[_ExistingRow] = []
    for row_number in range(header_row + 1, ws.max_row + 1):
        values = [
            ws.cell(row=row_number, column=column).value
            for column in range(1, ws.max_column + 1)
        ]
        if not any(value is not None and str(value).strip() != "" for value in values):
            continue

        def text(name: str) -> str:
            value = _cell_value(ws, row_number, mapping[name])
            return "" if value is None else str(value).strip()

        rows.append(
            _ExistingRow(
                row_number=row_number,
                code=normalize_article_code(text("code")),
                designation=text("designation"),
                weight=_to_float(text("weight")),
                packaging_price=_to_float(text("packaging_price")),
                kg_price=_to_float(text("kg_price")),
                ancien_prix=_to_float(text("ancien_prix")),
                statut=text("statut"),
                alert=text("alerte"),
                date_ajout=text("date_ajout"),
            )
        )
    return rows


def _alert_for_values(
    existing_alert: str, packaging_price: float | None, kg_price: float | None, missing_alert: str
) -> str:
    if packaging_price is None and kg_price is None:
        return missing_alert
    if existing_alert in {"", missing_alert}:
        return ""
    return existing_alert


def _price_changed(
    existing: _ExistingRow, supplier: SupplierPrice
) -> bool:
    existing_prices = [
        value for value in (existing.packaging_price, existing.kg_price) if value is not None
    ]
    supplier_prices = [
        value for value in (supplier.packaging_price, supplier.kg_price) if value is not None
    ]
    if not existing_prices and supplier_prices:
        return True
    comparisons = (
        (existing.packaging_price, supplier.packaging_price),
        (existing.kg_price, supplier.kg_price),
    )
    for old, new in comparisons:
        if old is not None and new is not None and not _same_number(old, new):
            return True
    return False


def _format_data_cells(
    ws: Any, header_row: int, mapping: Mapping[str, int], rules: Any
) -> None:
    from openpyxl.styles import Border, Side

    thin = Side(style="thin")
    thin_border = Border(left=thin, right=thin, top=thin, bottom=thin)
    none_border = Border()
    decimals = int(getattr(rules, "decimals", 2) or 2)
    number_format = "0." + ("0" * decimals) if decimals else "0"

    double = Side(style="double")
    for column in range(1, ws.max_column + 1):
        cell = ws.cell(row=header_row, column=column)
        existing = cell.border
        cell.border = Border(
            left=existing.left,
            right=existing.right,
            top=existing.top,
            bottom=double,
        )

    code_column = mapping["code"]
    numeric_columns = {
        mapping["weight"],
        mapping["packaging_price"],
        mapping["kg_price"],
        mapping["ancien_prix"],
    }
    for row_number in range(header_row + 1, ws.max_row + 1):
        for column in range(1, ws.max_column + 1):
            cell = ws.cell(row=row_number, column=column)
            value = cell.value
            empty = value is None or (isinstance(value, str) and value.strip() == "")
            cell.border = none_border if empty else thin_border
            if column == code_column:
                if not empty:
                    cell.value = normalize_article_code(value)
                    cell.number_format = "@"
            elif column in numeric_columns and value is not None and not empty:
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    cell.number_format = number_format


def _write_row(
    ws: Any, row_number: int, mapping: Mapping[str, int], values: Mapping[str, object]
) -> None:
    for canonical, value in values.items():
        if canonical in mapping:
            ws.cell(row=row_number, column=mapping[canonical]).value = value


def _write_summary(
    wb: Any,
    *,
    day: str,
    source_name: str,
    output_name: str,
    counts: Mapping[str, int],
    rows: Sequence[TarifRowResult],
    supplier_files: Sequence[str],
    warnings: Sequence[str],
    rules: Any,
) -> None:
    from openpyxl.styles import Font

    if SUMMARY_SHEET in wb.sheetnames:
        del wb[SUMMARY_SHEET]
    summary = wb.create_sheet(SUMMARY_SHEET, 0)
    summary["A1"] = "Résumé de la fusion des tarifs"
    summary["A1"].font = Font(bold=True, size=14)
    summary["A3"] = "Date"
    summary["B3"] = day
    summary["A4"] = "Classeur source"
    summary["B4"] = source_name
    summary["A5"] = "Classeur de sortie"
    summary["B5"] = output_name
    summary["A7"] = "Statut"
    summary["B7"] = "Nombre"
    summary["A7"].font = Font(bold=True)
    summary["B7"].font = Font(bold=True)
    summary["A8"] = STATUS_NEW
    summary["B8"] = counts.get(STATUS_NEW, 0)
    summary["A9"] = STATUS_UPDATED
    summary["B9"] = counts.get(STATUS_UPDATED, 0)
    summary["A10"] = STATUS_UNCHANGED
    summary["B10"] = counts.get(STATUS_UNCHANGED, 0)
    summary["A11"] = "Total lignes"
    summary["B11"] = counts.get("total", 0)
    summary["A13"] = "Alertes"
    summary["B13"] = counts.get("alertes", 0)
    summary["A15"] = "Fournisseurs"
    summary["B15"] = ", ".join(supplier_files)
    summary["A17"] = "Arrondi (décimales)"
    summary["B17"] = int(getattr(rules, "decimals", 2) or 2)
    if warnings:
        summary["A19"] = "Avertissements"
        summary["B19"] = "; ".join(warnings)
    summary.column_dimensions["A"].width = 28
    summary.column_dimensions["B"].width = 60
    summary.freeze_panes = "A2"


def _escape_markdown(value: object) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def _log_section(
    *,
    day: str,
    folder: TaskFolderLayout,
    output_path: Path,
    rows: Sequence[TarifRowResult],
    counts: Mapping[str, int],
    warnings: Sequence[str],
    supplier_files: Sequence[str],
) -> str:
    lines = [
        f"# Journal d'audit — fusion tarifs — {day}",
        "",
        f"- Dossier projet : {folder.root}",
        f"- Classeur source : {folder.tarifs_existants.name}",
        f"- Classeur de sortie : {output_path}",
        f"- Fichiers fournisseurs : {', '.join(supplier_files) or '(aucun)'}",
        "",
        "## Totaux",
        "",
        f"- Lignes existantes conservées : {counts.get('existantes', 0)}",
        f"- Nouveaux articles : {counts.get(STATUS_NEW, 0)}",
        f"- Articles modifiés : {counts.get(STATUS_UPDATED, 0)}",
        f"- Articles inchangés : {counts.get(STATUS_UNCHANGED, 0)}",
        f"- Total lignes en sortie : {counts.get('total', 0)}",
        f"- Alertes prix : {counts.get('alertes', 0)}",
        "- Garantie : aucune ligne existante n'a été supprimée.",
        "",
    ]
    if warnings:
        lines.extend(["## Avertissements", ""])
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")
    lines.extend(
        [
            "## Détail des lignes",
            "",
            "| Ligne | Code | Désignation | Statut | Ancien prix | Prix conditionnement | Prix kg | Poids | Alerte | Date ajout |",
            "|---:|---|---|---|---:|---:|---:|---:|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            "| {row} | {code} | {designation} | {statut} | {ancien} | {packaging} | {kg} | {weight} | {alert} | {date} |".format(
                row=row.row_number,
                code=_escape_markdown(row.code),
                designation=_escape_markdown(row.designation),
                statut=_escape_markdown(row.statut),
                ancien=_escape_markdown("" if row.ancien_prix is None else f"{row.ancien_prix:.2f}"),
                packaging=_escape_markdown(
                    "" if row.packaging_price is None else f"{row.packaging_price:.2f}"
                ),
                kg=_escape_markdown("" if row.kg_price is None else f"{row.kg_price:.2f}"),
                weight=_escape_markdown("" if row.weight is None else f"{row.weight:.2f}"),
                alert=_escape_markdown(row.alert),
                date=_escape_markdown(row.date_ajout),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    try:
        temp.write_text(content, encoding="utf-8", newline="\n")
        os.replace(temp, path)
    except OSError as exc:
        raise TarifWorkflowError("WRITE_FAILED", str(path)) from exc
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)


def _append_audit_log(path: Path, section: str) -> None:
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise TarifWorkflowError("LOG_READ_FAILED", str(path)) from exc
        content = existing.rstrip() + "\n\n---\n\n" + section
    else:
        content = section
    _atomic_write_text(path, content)


def _atomic_save_workbook(wb: Any, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = output_path.with_name(f".{output_path.name}.tmp.xlsx")
    try:
        wb.save(temp)
        wb.close()
        os.replace(temp, output_path)
    except OSError as exc:
        raise TarifWorkflowError("WRITE_FAILED", str(output_path)) from exc
    finally:
        close = getattr(wb, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        if temp.exists():
            temp.unlink(missing_ok=True)


def _today(clock: Callable[[], datetime | date] | None) -> str:
    value = clock() if clock is not None else datetime.now()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _counts(rows: Sequence[TarifRowResult], existing_count: int) -> dict[str, int]:
    counts = {
        STATUS_NEW: 0,
        STATUS_UPDATED: 0,
        STATUS_UNCHANGED: 0,
        "total": len(rows),
        "existantes": existing_count,
        "alertes": 0,
    }
    for row in rows:
        counts[row.statut] = counts.get(row.statut, 0) + 1
        if row.alert:
            counts["alertes"] += 1
    return counts


class DeclarativeTarifService:
    """Apply the non-destructive Excel tariff workflow to one project folder."""

    def __init__(
        self,
        task: DeclarativeTask | str | os.PathLike[str],
        *,
        clock: Callable[[], datetime | date] | None = None,
    ) -> None:
        self._task = task if isinstance(task, DeclarativeTask) else load_task(task)
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

    def run(self) -> TarifRunResult:
        if self.brief.workflow.value != "TARIFS_EXCEL":
            raise TarifWorkflowError("WRONG_WORKFLOW", self.brief.workflow.value)
        folder = self.folder
        source_path = folder.tarifs_existants
        if not source_path.is_file():
            raise TarifWorkflowError("TARIFS_NOT_FOUND", str(source_path))
        ensure_contained(folder.root, source_path)
        output_path = ensure_contained(folder.root, folder.output / Path(TARIFS_OUTPUT).name)
        day = _today(self._clock)
        rules = self.brief.price_rules
        merge_rules = self.brief.merge_rules
        warnings: list[str] = list(self.brief.warnings)

        wb = self._load_workbook(source_path)
        try:
            sheet_name = merge_rules.sheet or wb.active.title
            if sheet_name not in wb.sheetnames:
                raise TarifWorkflowError("SHEET_NOT_FOUND", sheet_name)
            ws = wb[sheet_name]
            header_row, mapping = _detect_header_row(
                ws, rules, merge_rules.date_column, merge_rules.header_row
            )
            mapping = _ensure_columns(ws, header_row, mapping, rules, merge_rules.date_column)
            unmerged = _unmerge_data_rows(ws, header_row)
            existing_rows = _read_existing_rows(ws, header_row, mapping)
        except Exception:
            wb.close()
            raise

        supplier_files = folder.supplier_pdfs()
        suppliers, supplier_warnings = extract_supplier_prices(supplier_files, rules=rules)
        warnings.extend(supplier_warnings)
        supplier_index = {article_key(record.code): record for record in suppliers}
        matched: set[str] = set()
        results: list[TarifRowResult] = []

        try:
            for existing in existing_rows:
                key = article_key(existing.code)
                supplier = supplier_index.get(key) if key else None
                statut = STATUS_UNCHANGED
                ancien_prix = existing.ancien_prix
                packaging_price = existing.packaging_price
                kg_price = existing.kg_price
                weight = existing.weight
                designation = existing.designation
                date_ajout = existing.date_ajout
                alert = _alert_for_values(
                    existing.alert, packaging_price, kg_price, rules.missing_alert
                )

                if supplier is not None:
                    matched.add(key)
                    changed = _price_changed(existing, supplier)
                    if changed:
                        statut = STATUS_UPDATED
                        old_main = (
                            existing.packaging_price
                            if existing.packaging_price is not None
                            else existing.kg_price
                        )
                        if old_main is not None:
                            ancien_prix = round_price(old_main, rules.decimals)
                    packaging_price = (
                        supplier.packaging_price
                        if supplier.packaging_price is not None
                        else existing.packaging_price
                    )
                    kg_price = (
                        supplier.kg_price if supplier.kg_price is not None else existing.kg_price
                    )
                    weight = supplier.weight if supplier.weight is not None else existing.weight
                    designation = existing.designation or supplier.designation
                    alert = _alert_for_values(
                        existing.alert, packaging_price, kg_price, rules.missing_alert
                    )
                    if supplier.alert and not alert:
                        alert = supplier.alert

                values = {
                    "designation": designation,
                    "weight": weight,
                    "packaging_price": packaging_price,
                    "kg_price": kg_price,
                    "ancien_prix": ancien_prix,
                    "statut": statut,
                    "alerte": alert,
                    "date_ajout": date_ajout,
                }
                _write_row(ws, existing.row_number, mapping, values)
                results.append(
                    TarifRowResult(
                        code=existing.code,
                        designation=designation,
                        statut=statut,
                        ancien_prix=ancien_prix,
                        packaging_price=packaging_price,
                        kg_price=kg_price,
                        weight=weight,
                        alert=alert,
                        date_ajout=date_ajout,
                        row_number=existing.row_number,
                    )
                )

            new_row_number = ws.max_row + 1
            for record in suppliers:
                key = article_key(record.code)
                if key in matched:
                    continue
                values = {
                    "code": normalize_article_code(record.code),
                    "designation": record.designation,
                    "weight": record.weight,
                    "packaging_price": record.packaging_price,
                    "kg_price": record.kg_price,
                    "ancien_prix": None,
                    "statut": STATUS_NEW,
                    "alerte": record.alert,
                    "date_ajout": day,
                }
                _write_row(ws, new_row_number, mapping, values)
                results.append(
                    TarifRowResult(
                        code=normalize_article_code(record.code),
                        designation=record.designation,
                        statut=STATUS_NEW,
                        ancien_prix=None,
                        packaging_price=record.packaging_price,
                        kg_price=record.kg_price,
                        weight=record.weight,
                        alert=record.alert,
                        date_ajout=day,
                        row_number=new_row_number,
                    )
                )
                new_row_number += 1

            _format_data_cells(ws, header_row, mapping, rules)
            counts = _counts(results, len(existing_rows))
            if unmerged:
                warnings.append(f"cellules_fusionnees_defusionnees:{unmerged}")
            _write_summary(
                wb,
                day=day,
                source_name=source_path.name,
                output_name=output_path.name,
                counts=counts,
                rows=results,
                supplier_files=tuple(path.name for path in supplier_files),
                warnings=warnings,
                rules=rules,
            )
            _atomic_save_workbook(wb, output_path)
        except Exception:
            try:
                wb.close()
            except Exception:
                pass
            raise

        log_path = ensure_contained(folder.root, folder.log / f"{day}.md")
        section = _log_section(
            day=day,
            folder=folder,
            output_path=output_path,
            rows=results,
            counts=counts,
            warnings=warnings,
            supplier_files=tuple(path.name for path in supplier_files),
        )
        _append_audit_log(log_path, section)

        return TarifRunResult(
            output_path=output_path,
            log_path=log_path,
            rows=tuple(results),
            counts=counts,
            warnings=tuple(dict.fromkeys(warnings)),
            supplier_files=tuple(path.name for path in supplier_files),
            date=day,
        )

    def merge(self) -> TarifRunResult:
        """Alias of :meth:`run` for the non-destructive merge workflow."""
        return self.run()

    def _load_workbook(self, path: Path) -> Any:
        from openpyxl import load_workbook

        try:
            return load_workbook(path)
        except Exception as exc:
            raise TarifWorkflowError("WORKBOOK_INVALID", str(path)) from exc


def run_tarif_workflow(
    task: DeclarativeTask | str | os.PathLike[str],
    *,
    clock: Callable[[], datetime | date] | None = None,
) -> TarifRunResult:
    """Convenience entry point for the declarative tariff workflow."""
    return DeclarativeTarifService(task, clock=clock).run()


TarifService = DeclarativeTarifService
merge_tarifs = run_tarif_workflow
