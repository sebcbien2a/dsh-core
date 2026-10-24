"""Scaffold for a shared-OCR ExtractionManager vs NuExtract3 benchmark.

This module contains no model invocation and is not a runnable benchmark yet.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from pydantic import ValidationError

from services.extraction_manager import InvoiceExtractionValidationError

FIELDS = ("fournisseur", "date", "ht", "tva", "ttc", "siret")
MONEY_FIELDS = frozenset(("ht", "tva", "ttc"))
DEFAULT_MONETARY_TOLERANCE = Decimal("0.02")


@dataclass(frozen=True)
class OCRCase:
    document_id: str
    ocr_text: str
    ground_truth: Mapping[str, Any]
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class ExtractionOutput:
    raw_payload: Mapping[str, Any] | None
    call_status: str = "not_called"
    parse_status: str = "not_attempted"
    error: str | None = None


class ExtractorInterface(Protocol):
    name: str

    def extract(self, case: OCRCase) -> ExtractionOutput:
        """Future model boundary; implementations must be explicitly supplied."""


class ExtractionManagerInterface:
    """Model-free boundary for the existing ExtractionManager."""

    name = "ExtractionManager"

    def __init__(self, invoice_validator: Callable[[dict[str, Any]], Any]) -> None:
        self._invoice_validator = invoice_validator

    def extract(self, case: OCRCase) -> ExtractionOutput:
        raise NotImplementedError(
            "Connect a model-free fixture or an explicitly authorized adapter."
        )

    def validate_invoice(self, payload: dict[str, Any]) -> Any:
        return self._invoice_validator(payload)


class NuExtract3Interface:
    """Model-free NuExtract3 boundary; no Ollama or other client is created."""

    name = "NuExtract3"

    def extract(self, case: OCRCase) -> ExtractionOutput:
        raise NotImplementedError(
            "Connect a model-free fixture or an explicitly authorized adapter."
        )


class InvoiceSchemaAdapter:
    """Map known extractor payload shapes to comparable fields and validation."""

    DEFAULT_SIRET_PATHS: tuple[tuple[str, ...], ...] = (
        ("supplier", "siret"),
        ("siret",),
    )

    def __init__(self, siret_paths: Sequence[Sequence[str]] | None = None) -> None:
        paths = siret_paths if siret_paths else self.DEFAULT_SIRET_PATHS
        self.siret_paths = tuple(tuple(path) for path in paths)

    @staticmethod
    def _first(data: Mapping[str, Any], *keys: str) -> Any:
        for key in keys:
            if data.get(key) is not None:
                return data[key]
        return None

    @staticmethod
    def _supplier_name(value: Any) -> Any:
        if isinstance(value, Mapping):
            return value.get("nom") or value.get("name")
        return value

    def _siret(self, payload: Mapping[str, Any], data: Mapping[str, Any]) -> Any:
        for source in (data, payload):
            for path in self.siret_paths:
                value = self._value(source, *path)
                if value is not None:
                    return value
        return None

    @staticmethod
    def _value(payload: Mapping[str, Any], *path: str) -> Any:
        value: Any = payload
        for key in path:
            if not isinstance(value, Mapping) or key not in value:
                return None
            value = value[key]
        return value

    def comparable_fields(self, extractor: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        data = payload.get("data", payload)
        if not isinstance(data, Mapping):
            return {}
        if extractor == "ExtractionManager":
            fields = {
                "fournisseur": self._value(data, "supplier", "name"),
                "date": self._value(data, "reference", "issue_date"),
                "ht": self._value(data, "amount", "net_amount"),
                "tva": self._value(data, "amount", "tax_amount"),
                "ttc": self._value(data, "amount", "gross_amount"),
            }
        else:
            first = self._first
            fields = {
                "fournisseur": self._supplier_name(first(data, "fournisseur")),
                "date": first(data, "date_emission", "date_facture"),
                "ht": first(data, "total_ht", "montant_ht"),
                "tva": first(data, "montant_tva"),
                "ttc": first(data, "total_ttc", "montant_ttc"),
            }
        fields["siret"] = self._siret(payload, data)
        return fields

    @staticmethod
    def invoice_payload(extractor: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Build the canonical structure, placing SIRET under supplier when present."""
        data = payload.get("data", payload)
        if not isinstance(data, Mapping):
            return {}
        if extractor == "ExtractionManager":
            canonical = dict(data)
            siret = canonical.pop("siret", None)
            supplier = canonical.get("supplier")
            if siret is not None and isinstance(supplier, Mapping):
                canonical["supplier"] = {
                    **supplier,
                    "siret": supplier.get("siret", siret),
                }
            return canonical
        first = InvoiceSchemaAdapter._first
        canonical: dict[str, Any] = {}
        supplier = InvoiceSchemaAdapter._supplier_name(first(data, "fournisseur"))
        if supplier is not None:
            canonical_supplier = {"name": supplier}
            siret = first(data, "siret")
            if siret is None:
                siret = InvoiceSchemaAdapter._value(data, "supplier", "siret")
            if siret is not None:
                canonical_supplier["siret"] = siret
            canonical["supplier"] = canonical_supplier
        reference = {
            key: value
            for key, value in (
                ("invoice_number", first(data, "numero_facture")),
                ("issue_date", first(data, "date_emission", "date_facture")),
            )
            if value is not None
        }
        if reference:
            canonical["reference"] = reference
        amount = {
            key: value
            for key, value in (
                ("net_amount", first(data, "total_ht", "montant_ht")),
                ("tax_amount", first(data, "montant_tva")),
                ("gross_amount", first(data, "total_ttc", "montant_ttc")),
                ("currency", first(data, "devise")),
            )
            if value is not None
        }
        if amount:
            canonical["amount"] = amount
        return canonical

    @staticmethod
    def validate(
        payload: dict[str, Any],
        invoice_validator: Callable[[dict[str, Any]], Any],
    ) -> tuple[str, str | None]:
        try:
            invoice_validator(payload)
        except (InvoiceExtractionValidationError, ValidationError) as exc:
            return "validation_failed", _redact(str(exc))
        return "valid", None


def load_ocr_dataset(path: Path) -> tuple[str, list[OCRCase]]:
    """Load fixed OCR text and ground truth; never invokes OCR or a model."""
    dataset = json.loads(path.read_text(encoding="utf-8"))
    cases = [
        OCRCase(
            document_id=item["id"],
            ocr_text=item.get("ocr_text", ""),
            ground_truth=item.get("ground_truth", {}),
            metadata=item.get("metadata", {}),
        )
        for item in dataset["cases"]
    ]
    return dataset["dataset_version"], cases


_DATE_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DATE_FR = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
_MONEY_NOISE = re.compile(r"[\s\u00a0\u202f]+|€|EUR", re.IGNORECASE)
_MONEY_FORMAT = re.compile(r"^[+-]?\d[\d.,]*$")
_UNSCORED_TRUTH = ("ground_truth_missing", "ground_truth_invalid", "input_missing")
_REDACTIONS = (
    (re.compile(r"[A-Za-z]:[\\/][^\s'\"]*"), "<path>"),
    (re.compile(r"(?<![\w.:])/(?:[\w.-]+/)+[\w.-]*"), "<path>"),
    (re.compile(r"sk-[A-Za-z0-9_-]+"), "<secret>"),
)


def _redact(text: str | None) -> str | None:
    if text is None:
        return None
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _parse_date(value: Any) -> str | None:
    """Accept only YYYY-MM-DD and DD/MM/YYYY; impossible dates are invalid."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        return None
    text = value.strip()
    iso, french = _DATE_ISO.match(text), _DATE_FR.match(text)
    if iso:
        year, month, day = map(int, iso.groups())
    elif french:
        day, month, year = map(int, french.groups())
    else:
        return None
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _parse_money(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, (int, float)):
        number = Decimal(str(value))
    elif isinstance(value, str):
        text = _MONEY_NOISE.sub("", value)
        if not _MONEY_FORMAT.match(text):
            return None
        if "," in text and "." in text:
            decimal_sep = "," if text.rfind(",") > text.rfind(".") else "."
            thousands_sep = "." if decimal_sep == "," else ","
            if text.count(decimal_sep) > 1:
                return None
            text = text.replace(thousands_sep, "").replace(decimal_sep, ".")
        elif text.count(",") == 1:
            text = text.replace(",", ".")
        elif text.count(",") > 1 or text.count(".") > 1:
            return None
        try:
            number = Decimal(text)
        except InvalidOperation:
            return None
    else:
        return None
    return number if number.is_finite() else None


def _normalize(field: str, value: Any) -> Any:
    if value is None:
        return None
    if field == "fournisseur":
        return " ".join(str(value).split()).casefold() or None
    if field == "date":
        return _parse_date(value)
    if field in MONEY_FIELDS:
        return _parse_money(value)
    if field == "siret":
        digits = re.sub(r"\D", "", str(value))
        return digits if len(digits) == 14 else None
    return value


def compare_fields(
    truth: Mapping[str, Any],
    prediction: Mapping[str, Any],
    monetary_tolerance: Decimal = DEFAULT_MONETARY_TOLERANCE,
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for field in FIELDS:
        actual = truth.get(field)
        predicted = prediction.get(field)
        expected = _normalize(field, actual)
        normalized = _normalize(field, predicted)
        difference = (
            str(abs(expected - normalized))
            if field in MONEY_FIELDS and expected is not None and normalized is not None
            else None
        )
        if actual is None:
            status = "ground_truth_missing"
        elif expected is None:
            status = "ground_truth_invalid"
        elif predicted is None:
            status = "missing_prediction"
        elif normalized is None:
            status = "invalid_prediction"
        elif field in MONEY_FIELDS:
            status = (
                "correct"
                if abs(expected - normalized) <= monetary_tolerance
                else "incorrect"
            )
        else:
            status = "correct" if expected == normalized else "incorrect"
        results[field] = {
            "ground_truth": actual,
            "raw_prediction": predicted,
            "normalized_prediction": str(normalized) if isinstance(normalized, Decimal) else normalized,
            "difference": difference,
            "status": status,
        }
    return results


def calculate_metrics(
    documents: list[Mapping[str, Any]],
) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for field in FIELDS:
        statuses = [doc["fields"][field]["status"] for doc in documents]
        known = sum(status not in _UNSCORED_TRUTH for status in statuses)
        predicted = sum(
            status not in (*_UNSCORED_TRUTH, "missing_prediction", "invalid_prediction")
            for status in statuses
        )
        correct = statuses.count("correct")
        metrics[field] = {
            "known": known,
            "predicted": predicted,
            "correct": correct,
            "coverage": predicted / known if known else None,
            "accuracy": correct / known if known else None,
        }
    attempted_validation = sum(doc.get("validation_status") != "not_attempted" for doc in documents)
    valid = sum(doc.get("validation_status") == "valid" for doc in documents)
    attempted = [doc for doc in documents if doc.get("input_status") != "input_missing"]
    errors = sum(
        doc.get("call_status") == "error" or doc.get("parse_status") == "error"
        for doc in attempted
    )
    eligible = [
        doc
        for doc in documents
        if all(doc["fields"][f]["status"] not in _UNSCORED_TRUTH for f in FIELDS)
    ]
    eligible_correct = sum(
        all(doc["fields"][f]["status"] == "correct" for f in FIELDS) for doc in eligible
    )
    return {
        "fields": metrics,
        "canonical_validation_rate": valid / attempted_validation if attempted_validation else None,
        "extraction_error_rate": errors / len(attempted) if attempted else None,
        "documents_with_all_fields_correct": sum(
            all(doc["fields"][field]["status"] == "correct" for field in FIELDS)
            for doc in documents
        ),
        "eligible_documents_with_all_ground_truth_fields": len(eligible),
        "complete_six_field_accuracy": eligible_correct / len(eligible) if eligible else None,
    }


class ExtractorBenchmarkRunner:
    def __init__(
        self,
        monetary_tolerance: Decimal = DEFAULT_MONETARY_TOLERANCE,
        schema_adapter: InvoiceSchemaAdapter | None = None,
        invoice_validator: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.monetary_tolerance = monetary_tolerance
        self.schema_adapter = schema_adapter or InvoiceSchemaAdapter()
        self.invoice_validator = invoice_validator

    def evaluate_output(
        self,
        extractor: str,
        case: OCRCase,
        output: ExtractionOutput,
    ) -> dict[str, Any]:
        payload = output.raw_payload or {}
        predicted = self.schema_adapter.comparable_fields(extractor, payload)
        validation_status = "not_attempted"
        validation_error = None
        if payload and self.invoice_validator is not None:
            invoice_payload = self.schema_adapter.invoice_payload(extractor, payload)
            validation_status, validation_error = self.schema_adapter.validate(
                invoice_payload, self.invoice_validator
            )
        input_missing = not case.ocr_text.strip()
        fields = compare_fields(case.ground_truth, predicted, self.monetary_tolerance)
        if input_missing:
            for result in fields.values():
                result["status"] = "input_missing"
        return {
            "document_id": case.document_id,
            "input_status": "input_missing" if input_missing else "ready",
            "call_status": output.call_status,
            "parse_status": output.parse_status,
            "validation_status": validation_status,
            "validation_error": validation_error,
            "error": _redact(output.error),
            "fields": fields,
        }

    def run(
        self,
        cases: Sequence[OCRCase],
        extractors: Sequence[ExtractorInterface],
        dataset_version: str = "unversioned",
    ) -> dict[str, Any]:
        """Evaluate injected extractors on shared OCR cases; creates no client."""
        results: dict[str, list[dict[str, Any]]] = {e.name: [] for e in extractors}
        for case in cases:
            for extractor in extractors:
                if not case.ocr_text.strip():
                    output = ExtractionOutput(raw_payload=None)
                else:
                    try:
                        output = extractor.extract(case)
                    except Exception as exc:
                        output = ExtractionOutput(
                            raw_payload=None,
                            call_status="error",
                            error=type(exc).__name__,
                        )
                results[extractor.name].append(
                    self.evaluate_output(extractor.name, case, output)
                )
        return self.build_report(dataset_version, results)

    def build_report(
        self,
        dataset_version: str,
        results: Mapping[str, list[Mapping[str, Any]]],
    ) -> dict[str, Any]:
        return {
            "benchmark": "ExtractionManager vs NuExtract3",
            "dataset_version": dataset_version,
            "settings": {
                "monetary_tolerance_eur": str(self.monetary_tolerance),
                "ocr_shared": True,
                "postprocessing_enabled": False,
            },
            "extractors": {
                name: {"documents": docs, "metrics": calculate_metrics(docs)}
                for name, docs in results.items()
            },
        }

    @staticmethod
    def write_report(report: Mapping[str, Any], destination: Path) -> None:
        destination.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
