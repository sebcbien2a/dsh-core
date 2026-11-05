"""Canonical protocol for every ``InvoiceSchema`` producer.

Any ingestion source (native PDF, scanned PDF/vision, OFX/MT940 statement,
CSV/Excel spreadsheet) must implement the ``IInvoiceProducer`` contract: a
``produce()`` method returning a validated ``InvoiceSchema``. Producer-specific
payload construction stays private; the canonical schema is the only output
that crosses the boundary.

The first concrete adapter, ``PdfInvoiceProducer``, covers producers 1 and 2:
native PDFs use a deterministic PyMuPDF text layer + regex extraction, while
scanned PDFs delegate to an injected vision extractor. Both paths share the
same ``InvoiceSchema`` validation barrier.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from pydantic import ValidationError

from schemas.invoice_schema import InvoiceSchema
from services.hotel_expense_parser import parse_hotel_evening_stage

REPO_ROOT = Path(__file__).resolve().parents[1]

MIN_TEXT_LAYER_CHARS = 20
AMOUNT_TOLERANCE = Decimal("0.02")

_AMOUNT_RE = re.compile(
    r"(?<!\d)(\d{1,3}(?:[ \u00a0\u202f.]\d{3})*|\d+)[,.](\d{2})(?!\d)"
)
_DATE_RE = re.compile(r"(?<!\d)(\d{2})[/.-](\d{2})[/.-](\d{2,4})(?!\d)")
_INVOICE_NUMBER_RE = re.compile(
    r"(?:FACTURE|Facture)\s*(?:N[°ºo\u00b0\uFFFD]?|NO|N°)?\s*:?\s*"
    r"([A-Z0-9][A-Z0-9._/-]*\d[A-Z0-9._/-]*)"
)
_VAT_RE = re.compile(
    r"(?:N[°ºo\u00b0\uFFFD]\s*TVA|Num[e\u00e9\uFFFD]ro\s*(?:de\s*)?TVA|"
    r"Identification\s*TVA|TVA\s*intra\w*)[^\n:]{0,15}:?\s*"
    r"([A-Z]{2}[\s\d]{8,16})",
    re.IGNORECASE,
)
_SIRET_RE = re.compile(r"SIRET\s*:?\s*(\d{3}\s?\d{3}\s?\d{3}\s?\d{5})", re.IGNORECASE)
_ADDRESS_RE = re.compile(
    r"^\d{1,4}\s+(?:Rue|Chemin|Avenue|Av\.|Bd|Boulevard|Route|Impasse|Place|"
    r"All[e\u00e9]e|Quai|Rond[- ]Point|Z[IE]|Zone)\b",
    re.IGNORECASE,
)
_LABEL_RE = re.compile(
    r"(?:Net\s*H\.?T\.?|Montant\s*H\.?T\.?|Motant\s*H\.?T\.?|"
    r"Sous-total[^\n]{0,30}H\.?T\.?|Base\s*H\.?T\.?)",
    re.IGNORECASE,
)
EU_VAT_PREFIXES = frozenset(
    {
        "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL", "ES", "FI", "FR",
        "GR", "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT",
        "RO", "SE", "SI", "SK", "XI",
    }
)

_COUNTRY_ALIASES = {
    "FRANCE": "FR",
    "FRA": "FR",
    "ESPAGNE": "ES",
    "SPAIN": "ES",
    "ESPANA": "ES",
    "ESP": "ES",
    "ALLEMAGNE": "DE",
    "GERMANY": "DE",
    "DEUTSCHLAND": "DE",
    "DEU": "DE",
    "ITALIE": "IT",
    "ITALY": "IT",
    "ITA": "IT",
    "BELGIQUE": "BE",
    "BELGIUM": "BE",
    "BEL": "BE",
    "PAYSBAS": "NL",
    "NETHERLANDS": "NL",
    "HOLLANDE": "NL",
    "NLD": "NL",
    "LUXEMBOURG": "LU",
    "LUX": "LU",
    "PORTUGAL": "PT",
    "PRT": "PT",
    "ROYAUMEUNI": "GB",
    "UNITEDKINGDOM": "GB",
    "UK": "GB",
    "GBR": "GB",
    "SUISSE": "CH",
    "SWITZERLAND": "CH",
    "CHE": "CH",
    "ETATSUNIS": "US",
    "UNITEDSTATES": "US",
    "USA": "US",
    "IRLANDE": "IE",
    "IRELAND": "IE",
    "IRL": "IE",
    "AUTRICHE": "AT",
    "AUSTRIA": "AT",
    "AUT": "AT",
    "POLOGNE": "PL",
    "POLAND": "PL",
    "POL": "PL",
}


class InvoiceExtractionError(RuntimeError):
    """Raised when raw source content cannot yield an invoice payload."""


class InvoiceProducerError(RuntimeError):
    """Raised when a producer violates the canonical output contract."""

    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        source: str | Path | None = None,
    ) -> None:
        detail = f"{code}: {message}" if message else code
        if source is not None:
            detail = f"{detail} [{source}]"
        super().__init__(detail)
        self.code = code
        self.source = source


class VisionExtractor(Protocol):
    """Callable turning a scanned document path into an invoice payload."""

    def __call__(self, source: Path) -> Mapping[str, Any]: ...


@runtime_checkable
class IInvoiceProducer(Protocol):
    """Common contract every ingestion source must satisfy.

    A producer owns its raw acquisition logic (PDF text, OCR/Vision, OFX,
    spreadsheet) and must return a fully validated ``InvoiceSchema``. The
    canonical schema is the only object allowed to cross this boundary.
    """

    def produce(self) -> InvoiceSchema: ...


InvoiceProducer = IInvoiceProducer


def normalise_text(text: str) -> str:
    for char in ("\u00a0", "\u202f", "\u2009", "\u2007"):
        text = text.replace(char, " ")
    return text


def normalise_vat(raw: str) -> str:
    return re.sub(r"\s+", "", raw).upper()


def normalise_country(value: Any) -> str | None:
    """Return a canonical ISO-3166 alpha-2 code when the value is recognisable."""

    if value is None:
        return None
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.strip().upper()
    if not text:
        return None
    compact = re.sub(r"[^A-Z0-9]", "", text)
    alias = _COUNTRY_ALIASES.get(compact)
    if alias is not None:
        return alias
    if re.fullmatch(r"[A-Z]{2}", text):
        return text
    return None


def _parse_amount(match: re.Match[str]) -> Decimal:
    whole = match.group(1).replace(" ", "").replace(".", "")
    token = f"{whole}.{match.group(2)}"
    try:
        return Decimal(token)
    except InvalidOperation as exc:
        raise InvoiceExtractionError(
            f"montant illisible: {match.group(0)!r}"
        ) from exc


def _amounts(text: str) -> list[Decimal]:
    return [_parse_amount(match) for match in _AMOUNT_RE.finditer(text)]


def _amount_triplet(window: str) -> tuple[Decimal, Decimal, Decimal] | None:
    values = _amounts(window)
    for index in range(len(values) - 2):
        net, tax, gross = values[index : index + 3]
        if tax >= 0 and abs(net + tax - gross) <= AMOUNT_TOLERANCE:
            return net, tax, gross
    return None


def _extract_totals(flat_text: str) -> tuple[Decimal, Decimal, Decimal]:
    for label in _LABEL_RE.finditer(flat_text):
        triplet = _amount_triplet(flat_text[label.end() : label.end() + 220])
        if triplet is not None:
            return triplet
    triplet = _amount_triplet(flat_text)
    if triplet is not None:
        return triplet
    raise InvoiceExtractionError(
        "aucun triplet cohérent HT + TVA = TTC trouvé dans la couche texte"
    )


def _extract_date(flat_text: str) -> date | None:
    preferred = re.search(
        r"(?:mission|Date\s*d)[^\d]{0,30}(\d{2})[/.-](\d{2})[/.-](\d{2,4})",
        flat_text,
        re.IGNORECASE,
    )
    match = preferred or _DATE_RE.search(flat_text)
    if match is None:
        return None
    day, month, year = (int(part) for part in match.groups())
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _extract_vat_numbers(flat_text: str) -> list[str]:
    seen: list[str] = []
    for match in _VAT_RE.finditer(flat_text):
        value = normalise_vat(match.group(1))
        if len(value) >= 10 and value not in seen:
            seen.append(value)
    return seen


def _select_supplier_vat(
    vat_numbers: Sequence[str], siret: str | None
) -> str | None:
    if siret is not None:
        siren = siret[:9]
        for vat in vat_numbers:
            if siren in vat:
                return vat
    for vat in vat_numbers:
        if vat[:2] == "FR":
            return vat
    return vat_numbers[0] if vat_numbers else None


def _extract_siret(flat_text: str) -> str | None:
    match = _SIRET_RE.search(flat_text)
    if match is None:
        return None
    return re.sub(r"\s+", "", match.group(1))


def _extract_supplier_name(lines: Sequence[str]) -> str | None:
    for index, line in enumerate(lines[1:], start=1):
        if _ADDRESS_RE.match(line.strip()):
            candidate = lines[index - 1].strip()
            if 3 <= len(candidate) <= 80:
                return candidate
    for line in lines:
        candidate = line.strip()
        if 3 <= len(candidate) <= 80 and re.search(
            r"\b(?:SAS|SARL|SASU|EURL|SA|SCI|SNC|GmbH|BV|NV|Ltd)\b",
            candidate,
            re.IGNORECASE,
        ):
            return candidate
    return None


def _load_supplier_registry() -> dict[str, str]:
    registry_path = REPO_ROOT / "workspace" / "suppliers" / "suppliers.json"
    if not registry_path.is_file():
        return {}
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    suppliers = data.get("suppliers", {})
    if not isinstance(suppliers, dict):
        return {}
    return {
        str(siret): str(entry.get("raison_sociale"))
        for siret, entry in suppliers.items()
        if isinstance(entry, dict) and entry.get("raison_sociale")
    }


def load_pymupdf() -> Any:
    """Import the real PyMuPDF module, even if a test stub is registered."""

    import importlib
    import sys

    try:
        pymupdf = importlib.import_module("pymupdf")
    except ImportError as exc:
        raise InvoiceExtractionError(
            "PyMuPDF indisponible pour la couche texte PDF"
        ) from exc
    if not hasattr(pymupdf, "open"):
        sys.modules.pop("pymupdf", None)
        try:
            pymupdf = importlib.import_module("pymupdf")
        except ImportError as exc:
            raise InvoiceExtractionError(
                "PyMuPDF indisponible pour la couche texte PDF"
            ) from exc
    if not hasattr(pymupdf, "open"):
        raise InvoiceExtractionError(
            "PyMuPDF indisponible pour la couche texte PDF"
        )
    tools = getattr(pymupdf, "TOOLS", None)
    if tools is not None and hasattr(tools, "mupdf_display_errors"):
        tools.mupdf_display_errors(False)
    return pymupdf


def extract_document_text(path: Path) -> str:
    """Return the raw text layer of a supported local document."""

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        pymupdf = load_pymupdf()
        with pymupdf.open(path) as document:
            return "\n".join(page.get_text() for page in document)
    if suffix in {".txt", ".md", ".csv"}:
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}:
        return ""
    raise InvoiceExtractionError(
        f"extraction brute non simulable pour le format {suffix or '<inconnu>'}"
    )


def build_invoice_payload(
    text: str,
    *,
    source_name: str,
    supplier_override: str | None = None,
    country_override: str | None = None,
    vat_override: str | None = None,
    simulate_zero_vat: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """Build a canonical invoice payload from a raw text layer."""

    flat = normalise_text(" ".join(text.split()))
    if len(flat.strip()) < MIN_TEXT_LAYER_CHARS:
        raise InvoiceExtractionError(
            "couche texte absente ou vide: OCR/Vision requis avant Factur-X"
        )

    notes: list[str] = []
    net, tax, gross = _extract_totals(flat)
    if simulate_zero_vat:
        tax = Decimal("0.00")
        gross = net
        notes.append("SIMULATION: tax_amount forcé à 0 et TTC aligné sur HT")

    siret = _extract_siret(flat)
    vat_numbers = _extract_vat_numbers(flat)
    supplier_vat = vat_override or _select_supplier_vat(vat_numbers, siret)
    if supplier_vat is not None:
        supplier_vat = normalise_vat(supplier_vat)

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    registry = _load_supplier_registry()
    supplier_name = supplier_override or (registry.get(siret) if siret else None)
    if supplier_name is None:
        supplier_name = _extract_supplier_name(lines)
    if supplier_name is None:
        supplier_name = "FOURNISSEUR_INCONNU"
        notes.append("nom fournisseur non extrait: override supplier_override requis")

    country = country_override
    if country is None and supplier_vat is not None:
        country = supplier_vat[:2] if supplier_vat[:2].isalpha() else None
    if country is None and (siret is not None or re.search(r"\bFR\b", flat)):
        country = "FR"
    country = normalise_country(country)

    mention_293b = bool(re.search(r"293\s*B", flat, re.IGNORECASE))
    micro_entreprise = bool(
        re.search(r"micro[-\s]?entreprise", flat, re.IGNORECASE)
    )

    issue_date = _extract_date(flat)
    number_match = _INVOICE_NUMBER_RE.search(flat)
    address = next((line for line in lines if _ADDRESS_RE.match(line)), None)
    hotel_breakdown = parse_hotel_evening_stage(text)

    metadata: dict[str, Any] = {
        "source_file": source_name,
        "extraction_tool": "pymupdf_text_regex",
        "raw_text_chars": len(flat),
        "supplier_country": country,
        "mention_293b": mention_293b,
        "micro_entreprise": micro_entreprise,
        "simulated_zero_vat": simulate_zero_vat,
    }
    if hotel_breakdown is not None:
        metadata.update(hotel_breakdown.as_metadata())

    payload: dict[str, Any] = {
        "document_type": "INVOICE",
        "direction": "PURCHASE",
        "supplier": {
            "name": supplier_name,
            "siret": siret,
            "vat_number": supplier_vat,
            "address": address,
        },
        "reference": {
            "invoice_number": number_match.group(1) if number_match else None,
            "issue_date": issue_date.isoformat() if issue_date else None,
        },
        "amount": {
            "currency": "EUR",
            "net_amount": str(net),
            "tax_amount": str(tax),
            "gross_amount": str(gross),
        },
        "metadata": metadata,
    }
    return payload, notes


def extract_raw_payload(
    invoice_path: Path,
    *,
    supplier_override: str | None = None,
    country_override: str | None = None,
    vat_override: str | None = None,
    simulate_zero_vat: bool = False,
) -> tuple[dict[str, Any], str, list[str]]:
    """Extract a raw payload and its source text from a local document."""

    text = extract_document_text(invoice_path)
    payload, notes = build_invoice_payload(
        text,
        source_name=invoice_path.name,
        supplier_override=supplier_override,
        country_override=country_override,
        vat_override=vat_override,
        simulate_zero_vat=simulate_zero_vat,
    )
    return payload, text, notes


def validate_produced_invoice(
    payload: Mapping[str, Any],
    *,
    source: str | Path | None = None,
) -> InvoiceSchema:
    """Validate a producer payload against the canonical ``InvoiceSchema``."""

    try:
        return InvoiceSchema.model_validate(dict(payload))
    except ValidationError as exc:
        raise InvoiceProducerError(
            "INVALID_PRODUCER_PAYLOAD",
            str(exc),
            source=source,
        ) from exc


def _apply_zero_vat_simulation(payload: dict[str, Any]) -> None:
    amount = payload.get("amount")
    if not isinstance(amount, dict):
        return
    net = amount.get("net_amount")
    amount["tax_amount"] = "0.00"
    if net is not None:
        amount["gross_amount"] = str(net)
    metadata = payload.setdefault("metadata", {})
    if isinstance(metadata, dict):
        metadata["simulated_zero_vat"] = True


class PdfInvoiceProducer:
    """Reference producer for native PDFs and scanned PDFs (vision).

    Producer 1 (native PDF): deterministic PyMuPDF text layer + regex.
    Producer 2 (scanned PDF): injected ``vision_extractor`` callable, then the
    same canonical validation barrier. Without a vision extractor, a scanned
    PDF fails explicitly with ``SCANNED_PDF_REQUIRES_VISION``.
    """

    producer_name = "pdf_native_or_vision"

    def __init__(
        self,
        source: str | Path,
        *,
        vision_extractor: VisionExtractor | None = None,
        supplier_override: str | None = None,
        country_override: str | None = None,
        vat_override: str | None = None,
        simulate_zero_vat: bool = False,
    ) -> None:
        self.source = Path(source)
        self.vision_extractor = vision_extractor
        self.supplier_override = supplier_override
        self.country_override = country_override
        self.vat_override = vat_override
        self.simulate_zero_vat = simulate_zero_vat
        self.last_payload: dict[str, Any] | None = None

    def produce(self) -> InvoiceSchema:
        if not self.source.is_file():
            raise InvoiceProducerError(
                "SOURCE_NOT_FOUND",
                "le fichier source est introuvable",
                source=self.source,
            )

        try:
            text = extract_document_text(self.source)
        except InvoiceExtractionError as exc:
            raise InvoiceProducerError(
                "EXTRACTION_FAILED", str(exc), source=self.source
            ) from exc

        flat = normalise_text(" ".join(text.split()))

        perception = None
        pipeline_active = False
        try:
            from services.document_pipeline import (
                is_pipeline_enabled,
                perceive_document,
            )

            if is_pipeline_enabled():
                perception = perceive_document(self.source)
                pipeline_active = True
        except Exception:
            perception = None
            pipeline_active = False

        if len(flat.strip()) < MIN_TEXT_LAYER_CHARS:
            payload = None
            perception_text = ""
            if perception is not None:
                try:
                    if perception.raw_text.strip():
                        perception_text = perception.raw_text
                    else:
                        perception_text = "\n".join(
                            block.text
                            for page in perception.pages
                            for block in sorted(
                                page.blocks,
                                key=lambda item: item.reading_order,
                            )
                            if block.text
                        )
                except Exception:
                    perception_text = ""
            perception_flat = normalise_text(
                " ".join(perception_text.split())
            )
            if (
                perception is not None
                and perception.total_text() >= MIN_TEXT_LAYER_CHARS
                and len(perception_flat.strip()) >= MIN_TEXT_LAYER_CHARS
            ):
                try:
                    payload, _ = build_invoice_payload(
                        perception_text,
                        source_name=self.source.name,
                        supplier_override=self.supplier_override,
                        country_override=self.country_override,
                        vat_override=self.vat_override,
                        simulate_zero_vat=self.simulate_zero_vat,
                    )
                    text = perception_text
                    flat = perception_flat
                except InvoiceExtractionError:
                    payload = None
            if payload is None:
                payload = self._produce_from_vision(reason="no_text_layer")
        else:
            try:
                payload, _ = build_invoice_payload(
                    text,
                    source_name=self.source.name,
                    supplier_override=self.supplier_override,
                    country_override=self.country_override,
                    vat_override=self.vat_override,
                    simulate_zero_vat=self.simulate_zero_vat,
                )
            except InvoiceExtractionError as exc:
                if self.vision_extractor is None:
                    raise InvoiceProducerError(
                        "EXTRACTION_FAILED", str(exc), source=self.source
                    ) from exc
                payload = self._produce_from_vision(
                    reason="native_text_extraction_failed"
                )

        metadata = payload.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["producer"] = self.producer_name
            metadata["producer_source"] = str(self.source)
            if pipeline_active and perception is not None:
                try:
                    from services.document_pipeline import (
                        perception_summary,
                    )

                    metadata.update(perception_summary(perception))
                except Exception:
                    pass
        self.last_payload = payload
        return validate_produced_invoice(payload, source=self.source)


    def _produce_from_vision(
        self, *, reason: str = "no_text_layer"
    ) -> dict[str, Any]:
        if self.vision_extractor is None:
            raise InvoiceProducerError(
                "SCANNED_PDF_REQUIRES_VISION",
                "couche texte absente: fournir un vision_extractor",
                source=self.source,
            )
        try:
            extracted = self.vision_extractor(self.source)
        except InvoiceProducerError:
            raise
        except Exception as exc:  # noqa: BLE001 - producer boundary
            raise InvoiceProducerError(
                "VISION_EXTRACTION_FAILED", str(exc), source=self.source
            ) from exc

        if not isinstance(extracted, Mapping):
            raise InvoiceProducerError(
                "INVALID_PRODUCER_PAYLOAD",
                "le vision_extractor doit retourner un mapping",
                source=self.source,
            )

        payload = dict(extracted)
        metadata = payload.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            raise InvoiceProducerError(
                "INVALID_PRODUCER_PAYLOAD",
                "metadata doit être un dictionnaire",
                source=self.source,
            )
        metadata.setdefault("source_file", self.source.name)
        metadata.setdefault("extraction_tool", "vision_extractor")
        metadata.setdefault("fallback_reason", reason)
        if "supplier_country" in metadata:
            metadata["supplier_country"] = normalise_country(
                metadata.get("supplier_country")
            )
        if self.simulate_zero_vat:
            _apply_zero_vat_simulation(payload)
        return payload


__all__ = [
    "EU_VAT_PREFIXES",
    "IInvoiceProducer",
    "InvoiceExtractionError",
    "InvoiceProducer",
    "InvoiceProducerError",
    "PdfInvoiceProducer",
    "VisionExtractor",
    "build_invoice_payload",
    "extract_document_text",
    "extract_raw_payload",
    "load_pymupdf",
    "normalise_country",
    "normalise_text",
    "normalise_vat",
    "validate_produced_invoice",
]
