"""Spreadsheet producer (.csv / .xlsx / .xls) implementing ``InvoiceProducer``.

The producer reads a purchase journal or an invoice grid, detects the key
columns with a flexible alias mapping (supplier, date, net, tax, gross,
reference), and projects each data row onto the canonical ``InvoiceSchema``
contract. ``produce`` returns one selected row, ``produce_all`` /
``produce_batch`` return the whole journal and ``produce_summary`` returns an
aggregated statement row.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import StringIO
from pathlib import Path
from typing import Any, Mapping, Sequence

from schemas.invoice_schema import InvoiceSchema
from services.invoice_producer_protocol import (
    InvoiceProducer,
    InvoiceProducerError,
    validate_produced_invoice,
)

CSV_SUFFIXES = {".csv", ".txt"}
EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}
_XLS_SUFFIXES = {".xls"}

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "supplier": (
        "fournisseur",
        "supplier",
        "vendor",
        "vendeur",
        "nom_fournisseur",
        "raison_sociale",
        "tiers",
    ),
    "date": (
        "date",
        "date_facture",
        "invoice_date",
        "date_piece",
        "date_emission",
        "date_operation",
    ),
    "net": (
        "net",
        "ht",
        "montant_ht",
        "total_ht",
        "net_amount",
        "base_ht",
        "montant_hors_taxe",
        "hors_taxe",
    ),
    "tax": (
        "tva",
        "montant_tva",
        "tax",
        "tax_amount",
        "tva_amount",
        "montant_taxe",
    ),
    "gross": (
        "ttc",
        "montant_ttc",
        "total_ttc",
        "gross",
        "gross_amount",
        "total",
    ),
    "reference": (
        "reference",
        "ref",
        "numero_facture",
        "facture",
        "invoice_number",
        "num_piece",
        "piece",
        "numero_piece",
        "n_facture",
    ),
    "currency": ("devise", "currency"),
    "siret": ("siret", "siret_fournisseur"),
    "vat_number": ("tva_intra", "vat_number", "numero_tva", "num_tva"),
    "direction": ("sens", "direction"),
    "description": ("description", "libelle", "designation", "objet"),
}
_ALL_ALIASES = frozenset(
    alias for aliases in _FIELD_ALIASES.values() for alias in aliases
)
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y%m%d",
    "%Y/%m/%d",
)
_PURCHASE_WORDS = {"ACHAT", "PURCHASE", "DEBIT", "D", "FOURNISSEUR"}
_SALE_WORDS = {"VENTE", "SALE", "CREDIT", "C", "CLIENT"}


@dataclass(frozen=True)
class SpreadsheetRow:
    """Normalized purchase-journal row."""

    row_index: int
    supplier: str | None
    invoice_date: date | None
    gross: Decimal
    currency: str
    net: Decimal | None = None
    tax: Decimal | None = None
    reference: str | None = None
    siret: str | None = None
    vat_number: str | None = None
    direction: str = "PURCHASE"
    description: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    raw: Mapping[str, str] = field(default_factory=dict)


def _decode_bytes(raw: bytes) -> str:
    """Decode UTF-8 (BOM tolerated), falling back to Windows-1252 then Latin-1."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


_TOTAL_LABEL = re.compile(r"^\s*(sous[\s\-]*)?(total|totaux)\b", re.IGNORECASE)
_EXCEL_EPOCH = date(1899, 12, 30)


def _excel_serial_to_date(text: str) -> date | None:
    if not re.fullmatch(r"\d{4,6}(\.\d+)?", text):
        return None
    days = int(float(text))
    if not 1 <= days <= 2958465:
        return None
    return _EXCEL_EPOCH + timedelta(days=days)


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return normalized.encode("ascii", "ignore").decode("ascii")


def _normalise_column(value: str) -> str:
    cleaned = _strip_accents(str(value)).strip().lower()
    cleaned = cleaned.replace("°", "").replace("º", "")
    cleaned = re.sub(r"[\s\-]+", "_", cleaned)
    return cleaned.strip("_")


def _parse_decimal(value: Any) -> Decimal:
    text = _strip_accents(str(value)).strip()
    text = text.replace("\u00a0", "").replace(" ", "")
    text = text.replace("€", "").replace("EUR", "").strip()
    if not text:
        raise InvoiceProducerError("INVALID_SPREADSHEET_ROW", "montant vide")
    negative = text.startswith("-")
    text = text[1:] if text[:1] in "+-" else text
    if text[:1] in "+-":
        raise InvoiceProducerError(
            "INVALID_SPREADSHEET_ROW", f"montant illisible: {value!r}"
        )
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    elif "." in text:
        head, _, tail = text.rpartition(".")
        if len(tail) == 3 and head:
            text = text.replace(".", "")
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise InvoiceProducerError(
            "INVALID_SPREADSHEET_ROW", f"montant illisible: {value!r}"
        ) from exc
    if not amount.is_finite():
        raise InvoiceProducerError(
            "INVALID_SPREADSHEET_ROW", f"montant illisible: {value!r}"
        )
    return -amount if negative else amount


def _parse_optional_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    serial = _excel_serial_to_date(text)
    if serial is not None:
        return serial
    candidate = text[:10] if re.match(r"\d{4}-\d{2}-\d{2}", text) else text
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    digits = re.match(r"(\d{8})", text)
    if digits:
        try:
            return datetime.strptime(digits.group(1), "%Y%m%d").date()
        except ValueError:
            pass
    raise InvoiceProducerError(
        "INVALID_SPREADSHEET_ROW", f"date illisible: {value!r}"
    )


def _normalise_currency(value: Any, default: str) -> str:
    text = _strip_accents(str(value or default)).strip().upper()
    if len(text) != 3 or not text.isalpha():
        raise InvoiceProducerError(
            "INVALID_SPREADSHEET_ROW", f"devise invalide: {value!r}"
        )
    return text


def _normalise_direction(value: Any, default: str) -> str:
    text = _strip_accents(str(value or "")).strip().upper()
    if text in _SALE_WORDS:
        return "SALE"
    if text in _PURCHASE_WORDS:
        return "PURCHASE"
    return default


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value != value:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


class SpreadsheetInvoiceProducer:
    """Produce canonical invoices from a spreadsheet purchase journal."""

    producer_name = "spreadsheet_journal"

    def __init__(
        self,
        source: str | Path,
        *,
        sheet_name: str | int = 0,
        header_row: int | None = None,
        header_scan_rows: int = 10,
        column_map: Mapping[str, str] | None = None,
        supplier_override: str | None = None,
        default_direction: str = "PURCHASE",
        currency: str = "EUR",
    ) -> None:
        self.source = Path(source)
        self.sheet_name = 0 if sheet_name is None else sheet_name
        self.header_row = header_row
        self.header_scan_rows = header_scan_rows
        self.column_map = dict(column_map or {})
        self.supplier_override = supplier_override
        direction = default_direction.strip().upper()
        if direction not in {"PURCHASE", "SALE"}:
            raise ValueError("default_direction must be PURCHASE or SALE")
        self.default_direction = direction
        self.currency = currency
        self._rows: list[SpreadsheetRow] | None = None
        self._columns: dict[str, str | None] | None = None

    def produce(self, row_index: int = 0) -> InvoiceSchema:
        rows = self.parse_rows()
        if not rows:
            raise InvoiceProducerError(
                "NO_SPREADSHEET_ROWS",
                "aucune ligne exploitable dans le tableur",
                source=self.source,
            )
        if not 0 <= row_index < len(rows):
            raise InvoiceProducerError(
                "ROW_INDEX_OUT_OF_RANGE",
                f"index {row_index} hors plage (0..{len(rows) - 1})",
                source=self.source,
            )
        return self._to_invoice(rows[row_index])

    def produce_all(self) -> list[InvoiceSchema]:
        rows = self.parse_rows()
        if not rows:
            raise InvoiceProducerError(
                "NO_SPREADSHEET_ROWS",
                "aucune ligne exploitable dans le tableur",
                source=self.source,
            )
        return [self._to_invoice(row) for row in rows]

    def produce_batch(self) -> list[InvoiceSchema]:
        return self.produce_all()

    def produce_summary(self) -> InvoiceSchema:
        rows = self.parse_rows()
        if not rows:
            raise InvoiceProducerError(
                "NO_SPREADSHEET_ROWS",
                "aucune ligne exploitable dans le tableur",
                source=self.source,
            )
        gross_total = sum((row.gross for row in rows), start=Decimal("0"))
        net_total = (
            sum((row.net for row in rows if row.net is not None), start=Decimal("0"))
            if all(row.net is not None for row in rows)
            else None
        )
        tax_total = (
            sum((row.tax for row in rows if row.tax is not None), start=Decimal("0"))
            if all(row.tax is not None for row in rows)
            else None
        )
        payload: dict[str, Any] = {
            "document_type": "UNKNOWN",
            "direction": "PURCHASE",
            "supplier": {"name": "JOURNAL_ACHATS"},
            "amount": {
                "currency": self.currency,
                "gross_amount": str(gross_total),
            },
            "validation_status": "PENDING",
            "metadata": {
                "source_kind": "spreadsheet_summary",
                "source_file": self.source.name,
                "producer": self.producer_name,
                "row_count": len(rows),
                "gross_total": str(gross_total),
                "net_total": str(net_total) if net_total is not None else None,
                "tax_total": str(tax_total) if tax_total is not None else None,
            },
        }
        return validate_produced_invoice(payload, source=self.source)

    def parse_rows(self) -> list[SpreadsheetRow]:
        if self._rows is not None:
            return list(self._rows)
        if not self.source.is_file():
            raise InvoiceProducerError(
                "SOURCE_NOT_FOUND",
                "le fichier source est introuvable",
                source=self.source,
            )
        suffix = self.source.suffix.lower()
        if suffix in CSV_SUFFIXES:
            grid = self._read_csv_grid()
            sheet_label = "csv"
        elif suffix in EXCEL_SUFFIXES:
            sheet_label, grid = self._read_excel_grid()
        else:
            raise InvoiceProducerError(
                "UNSUPPORTED_SPREADSHEET_FORMAT",
                f"format non supporté: {suffix or '<inconnu>'}",
                source=self.source,
            )
        if not grid:
            raise InvoiceProducerError(
                "NO_SPREADSHEET_ROWS",
                "le tableur est vide",
                source=self.source,
            )
        header_index = self._detect_header_row(grid)
        columns = self._build_columns(grid[header_index])
        self._columns = self._resolve_columns(columns)
        rows: list[SpreadsheetRow] = []
        row_offset = 0
        for raw_row in grid[header_index + 1 :]:
            row = self._build_row(
                raw_row,
                columns,
                row_index=row_offset,
                sheet_label=sheet_label,
            )
            if row is not None:
                rows.append(row)
            row_offset += 1
        self._rows = rows
        return list(rows)

    def _read_csv_grid(self) -> list[list[str]]:
        try:
            import pandas as pd

            read_csv = getattr(pd, "read_csv", None)
            if read_csv is None:
                raise AttributeError("pandas.read_csv unavailable")
            frame = read_csv(
                StringIO(_decode_bytes(self.source.read_bytes())),
                sep=None,
                engine="python",
                header=None,
                dtype=str,
                keep_default_na=False,
            )
            return [
                [_cell_text(value) for value in row]
                for row in frame.itertuples(index=False, name=None)
            ]
        except (ImportError, AttributeError, ValueError, OSError, csv.Error):
            text = _decode_bytes(self.source.read_bytes())
            if not text.strip():
                return []
            try:
                delimiter = csv.Sniffer().sniff(
                    text[:4096], delimiters=",;\t|"
                ).delimiter
            except csv.Error:
                delimiter = ";" if text.count(";") > text.count(",") else ","
            return [
                [_cell_text(value) for value in row]
                for row in csv.reader(StringIO(text, newline=""), delimiter=delimiter)
            ]

    def _read_excel_grid(self) -> tuple[str, list[list[str]]]:
        suffix = self.source.suffix.lower()
        try:
            import pandas as pd

            read_excel = getattr(pd, "read_excel", None)
            if read_excel is None:
                raise AttributeError("pandas.read_excel unavailable")
            frame = read_excel(
                self.source,
                sheet_name=self.sheet_name,
                header=None,
                dtype=str,
            )
            frame = frame.fillna("")
            return (
                str(self.sheet_name),
                [
                    [_cell_text(value) for value in row]
                    for row in frame.itertuples(index=False, name=None)
                ],
            )
        except (ImportError, AttributeError):
            if suffix in _XLS_SUFFIXES:
                raise InvoiceProducerError(
                    "MISSING_SPREADSHEET_ENGINE",
                    "lecture .xls impossible sans pandas/xlrd",
                    source=self.source,
                ) from None
            return self._read_xlsx_with_openpyxl()
        except (ValueError, IndexError, KeyError) as exc:
            if "orksheet" in str(exc) or isinstance(exc, IndexError):
                raise InvoiceProducerError(
                    "INVALID_SPREADSHEET_SHEET",
                    f"onglet introuvable: {self.sheet_name!r}",
                    source=self.source,
                ) from exc
            raise InvoiceProducerError(
                "CORRUPT_SPREADSHEET",
                "classeur illisible ou corrompu",
                source=self.source,
            ) from exc
        except Exception as exc:
            if isinstance(exc, InvoiceProducerError):
                raise
            raise InvoiceProducerError(
                "CORRUPT_SPREADSHEET",
                "classeur illisible ou corrompu",
                source=self.source,
            ) from exc

    def _read_xlsx_with_openpyxl(self) -> tuple[str, list[list[str]]]:
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise InvoiceProducerError(
                "MISSING_SPREADSHEET_ENGINE",
                "openpyxl indisponible pour lire le classeur",
                source=self.source,
            ) from exc
        try:
            workbook = load_workbook(self.source, read_only=True, data_only=True)
        except Exception as exc:
            raise InvoiceProducerError(
                "CORRUPT_SPREADSHEET",
                "classeur illisible ou corrompu",
                source=self.source,
            ) from exc
        try:
            if isinstance(self.sheet_name, str):
                sheet = workbook[self.sheet_name]
            else:
                sheet = workbook.worksheets[int(self.sheet_name)]
            grid = [
                [_cell_text(value) for value in row]
                for row in sheet.iter_rows(values_only=True)
            ]
            return sheet.title, grid
        finally:
            workbook.close()

    def _detect_header_row(self, grid: Sequence[Sequence[str]]) -> int:
        if self.header_row is not None:
            if not 0 <= self.header_row < len(grid):
                raise InvoiceProducerError(
                    "INVALID_SPREADSHEET_HEADER",
                    f"header_row {self.header_row} hors plage",
                    source=self.source,
                )
            return self.header_row
        best_index = 0
        best_score = 0
        aliases = set(_ALL_ALIASES)
        for value in self.column_map.values():
            aliases.add(_normalise_column(value))
        for index, row in enumerate(grid[: self.header_scan_rows]):
            score = sum(
                1
                for value in row
                if _normalise_column(value) in aliases and str(value).strip()
            )
            if score > best_score:
                best_index = index
                best_score = score
        if best_score < 2:
            raise InvoiceProducerError(
                "INVALID_SPREADSHEET_HEADER",
                "aucune ligne d'en-tête reconnue (fournisseur/date/montants)",
                source=self.source,
            )
        return best_index

    @staticmethod
    def _build_columns(header: Sequence[str]) -> list[str]:
        columns: list[str] = []
        seen: dict[str, int] = {}
        for index, value in enumerate(header):
            name = str(value).strip() or f"column_{index + 1}"
            if name in seen:
                seen[name] += 1
                name = f"{name}_{seen[name]}"
            else:
                seen[name] = 1
            columns.append(name)
        return columns

    def _resolve_columns(self, columns: Sequence[str]) -> dict[str, str | None]:
        resolved: dict[str, str | None] = {}
        normalised = {_normalise_column(column): column for column in columns}
        for field_name, aliases in _FIELD_ALIASES.items():
            override = self.column_map.get(field_name)
            if override is not None:
                resolved[field_name] = normalised.get(_normalise_column(override))
                continue
            resolved[field_name] = next(
                (normalised[alias] for alias in aliases if alias in normalised),
                None,
            )
        if (
            resolved["net"] is None
            and resolved["tax"] is None
            and resolved["gross"] is None
        ):
            raise InvoiceProducerError(
                "INVALID_SPREADSHEET_HEADER",
                "aucune colonne de montant (HT/TVA/TTC) reconnue",
                source=self.source,
            )
        return resolved

    def _build_row(
        self,
        raw_row: Sequence[str],
        columns: Sequence[str],
        *,
        row_index: int,
        sheet_label: str,
    ) -> SpreadsheetRow | None:
        raw = {
            column: _cell_text(raw_row[position])
            if position < len(raw_row)
            else ""
            for position, column in enumerate(columns)
        }
        if not any(value.strip() for value in raw.values()):
            return None
        assert self._columns is not None
        columns_map = self._columns
        if _TOTAL_LABEL.match(raw.get(columns_map["supplier"] or "", "")):
            return None
        warnings: list[str] = []
        try:
            supplier = (
                raw.get(columns_map["supplier"] or "", "").strip()
                or self.supplier_override
            )
            if not supplier:
                supplier = "FOURNISSEUR_INCONNU"
                warnings.append("supplier_missing")
            invoice_date = _parse_optional_date(
                raw.get(columns_map["date"] or "", "")
            )
            if invoice_date is None:
                warnings.append("date_missing")
            net = self._optional_amount(raw, columns_map["net"], row_index)
            tax = self._optional_amount(raw, columns_map["tax"], row_index)
            gross = self._optional_amount(raw, columns_map["gross"], row_index)
            if gross is None:
                if net is not None and tax is not None:
                    gross = net + tax
                elif net is not None:
                    gross = net
                else:
                    raise InvoiceProducerError(
                        "INVALID_SPREADSHEET_ROW",
                        f"ligne {row_index}: aucun montant HT/TVA/TTC",
                    )
            if net is None and tax is not None:
                net = gross - tax
            if (
                tax is None
                and net is not None
                and columns_map["gross"] is not None
            ):
                tax = gross - net
            for label, amount in (("net", net), ("tax", tax), ("gross", gross)):
                if amount is not None and amount < 0:
                    raise InvoiceProducerError(
                        "INVALID_SPREADSHEET_ROW",
                        f"ligne {row_index}: montant {label} négatif",
                    )
            currency = _normalise_currency(
                raw.get(columns_map["currency"] or "", ""), self.currency
            )
            reference = (
                raw.get(columns_map["reference"] or "", "").strip() or None
            )
            siret = re.sub(
                r"\s+", "", raw.get(columns_map["siret"] or "", "")
            )
            if siret and not re.fullmatch(r"\d{14}", siret):
                warnings.append("siret_ignored")
                siret = ""
            vat_number = (
                re.sub(r"\s+", "", raw.get(columns_map["vat_number"] or "", "")).upper()
                or None
            )
            direction = _normalise_direction(
                raw.get(columns_map["direction"] or "", ""), self.default_direction
            )
            description = (
                raw.get(columns_map["description"] or "", "").strip() or None
            )
        except InvoiceProducerError as exc:
            if exc.code == "INVALID_SPREADSHEET_ROW" and "ligne " not in str(exc):
                raise InvoiceProducerError(
                    exc.code, f"ligne {row_index}: {exc}", source=self.source
                ) from exc
            raise
        return SpreadsheetRow(
            row_index=row_index,
            supplier=supplier or None,
            invoice_date=invoice_date,
            net=net,
            tax=tax,
            gross=gross,
            currency=currency,
            reference=reference,
            siret=siret or None,
            vat_number=vat_number,
            direction=direction,
            description=description,
            warnings=tuple(warnings),
            raw=raw,
        )

    @staticmethod
    def _optional_amount(
        raw: Mapping[str, str], column: str | None, row_index: int
    ) -> Decimal | None:
        if column is None:
            return None
        value = raw.get(column, "").strip()
        if not value:
            return None
        try:
            return _parse_decimal(value)
        except InvoiceProducerError as exc:
            raise InvoiceProducerError(
                "INVALID_SPREADSHEET_ROW",
                f"ligne {row_index}: {exc}",
            ) from exc

    def _to_invoice(self, row: SpreadsheetRow) -> InvoiceSchema:
        is_sale = row.direction == "SALE"
        party: dict[str, Any] = {"name": row.supplier or "TIERS_INCONNU"}
        if row.siret:
            party["siret"] = row.siret
        if row.vat_number:
            party["vat_number"] = row.vat_number
        amount: dict[str, Any] = {
            "currency": row.currency,
            "gross_amount": str(row.gross),
        }
        if row.net is not None:
            amount["net_amount"] = str(row.net)
        if row.tax is not None:
            amount["tax_amount"] = str(row.tax)
        payload: dict[str, Any] = {
            "document_type": "INVOICE" if row.reference else "UNKNOWN",
            "direction": row.direction,
            "customer" if is_sale else "supplier": party,
            "reference": {
                "invoice_number": row.reference,
                "issue_date": (
                    row.invoice_date.isoformat() if row.invoice_date else None
                ),
            },
            "amount": amount,
            "validation_status": "PENDING",
            "metadata": {
                "source_kind": "spreadsheet_row",
                "source_file": self.source.name,
                "producer": self.producer_name,
                "row_index": row.row_index,
                "description": row.description,
                "warnings": list(row.warnings),
                "raw_row": dict(row.raw),
            },
        }
        try:
            return validate_produced_invoice(payload, source=self.source)
        except InvoiceProducerError as exc:
            raise InvoiceProducerError(
                "INVALID_SPREADSHEET_ROW",
                f"ligne {row.row_index}: {exc}",
                source=self.source,
            ) from exc


__all__ = [
    "SpreadsheetInvoiceProducer",
    "SpreadsheetRow",
    "InvoiceProducer",
]
