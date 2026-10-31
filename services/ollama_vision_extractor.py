"""Concrete Ollama Vision extractor for the ``VisionExtractor`` contract.

The adapter mirrors the behaviour of ``InvoiceAgent.structurer_document_en_json``
(binary document sent to the configured vision model) but returns a canonical
``InvoiceSchema`` payload, including legacy field mapping and credit-note
normalization. It can be injected directly into ``PdfInvoiceProducer`` or
``run_invoice_graph``.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping

from schemas.invoice_schema import InvoiceSchema
from services.invoice_producer_protocol import (
    InvoiceProducerError,
    load_pymupdf,
    validate_produced_invoice,
)

DEFAULT_MODEL_ENV_VARS = ("VISION_MODEL", "LOCAL_VISION_MODEL")
SYSTEM_PROMPT = """Tu es un expert-comptable numérique.
À partir de l'image ou du PDF de facture fourni, extrais les données et
structure-les STRICTEMENT selon ce schéma JSON canonique :

{
  "document_type": "INVOICE | CREDIT_NOTE | RECEIPT | FUEL_RECEIPT | MEAL_RECEIPT | UNKNOWN",
  "direction": "PURCHASE | SALE",
  "supplier": {
    "name": "Raison sociale de l'émetteur",
    "siret": "14 chiffres ou null",
    "vat_number": "TVA intracommunautaire de l'émetteur ou null (jamais celle du client)"
  },
  "reference": {
    "invoice_number": "Numéro de facture",
    "issue_date": "YYYY-MM-DD"
  },
  "amount": {
    "currency": "EUR",
    "net_amount": 0.0,
    "tax_amount": 0.0,
    "gross_amount": 0.0
  },
  "category": "Catégorie ou null",
  "supplier_country": "Code ISO 2 lettres ou null"
}

RÈGLES CRITIQUES :
- Réponds UNIQUEMENT avec un objet JSON valide, sans texte avant ou après.
- Ne modifie aucun chiffre : recopie exactement les montants lus.
- HT + TVA doit être rigoureusement égal à TTC.
- Pour un avoir ou une facture rectificative, utilise "CREDIT_NOTE" et des
  montants positifs (valeur absolue).
- Si une donnée est absente, mets null ; n'invente rien.
"""


class OllamaVisionExtractorError(RuntimeError):
    """Raised when Ollama cannot be reached or returns unusable content."""


def _default_chat(**kwargs: Any) -> Any:
    import ollama

    return ollama.chat(**kwargs)


def _resolve_model(model: str | None) -> str:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    from model_router import (
        ExecutionMode,
        ModelRole,
        resolve_model,
        validate_vision_model,
    )

    if model is not None and model.strip():
        return validate_vision_model(model)
    for variable in DEFAULT_MODEL_ENV_VARS:
        value = os.getenv(variable)
        if value and value.strip():
            return validate_vision_model(value)
    try:
        return resolve_model(ModelRole.VISION, ExecutionMode.LOCAL)
    except ValueError as exc:
        raise OllamaVisionExtractorError(
            "VISION_MODEL_NOT_CONFIGURED: définir VISION_MODEL ou "
            "LOCAL_VISION_MODEL"
        ) from exc


def _extract_content(response: Any) -> str:
    if isinstance(response, Mapping):
        message = response.get("message")
    else:
        message = getattr(response, "message", None)
    if message is None:
        raise OllamaVisionExtractorError("RÉPONSE_OLLAMA_SANS_MESSAGE")
    if isinstance(message, Mapping):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)
    if isinstance(content, list):
        content = "".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, Mapping)
        )
    if not isinstance(content, str) or not content.strip():
        raise OllamaVisionExtractorError("RÉPONSE_OLLAMA_VIDE")
    return content.strip()


def strip_code_fences(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
    return text.strip()


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    text = text.replace("€", "").replace("EUR", "")
    if not text:
        return None
    if "," in text and "." in text:
        text = (
            text.replace(".", "").replace(",", ".")
            if text.rfind(",") > text.rfind(".")
            else text.replace(",", "")
        )
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _normalise_date(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _normalise_currency(value: Any) -> str:
    text = str(value or "EUR").strip().upper()
    if len(text) != 3 or not text.isalpha():
        raise InvoiceProducerError(
            "INVALID_VISION_PAYLOAD", f"devise invalide: {value!r}"
        )
    return text


def _normalise_document_type(value: Any, *, negative: bool) -> str:
    if negative:
        return "CREDIT_NOTE"
    text = str(value or "INVOICE").strip().upper().replace(" ", "_")
    mapping = {
        "FACTURE": "INVOICE",
        "AVOIR": "CREDIT_NOTE",
        "INVOICE": "INVOICE",
        "CREDIT_NOTE": "CREDIT_NOTE",
        "RECEIPT": "RECEIPT",
        "FUEL_RECEIPT": "FUEL_RECEIPT",
        "MEAL_RECEIPT": "MEAL_RECEIPT",
        "UNKNOWN": "UNKNOWN",
    }
    return mapping.get(text, "UNKNOWN")


def _normalise_direction(value: Any) -> str:
    text = str(value or "PURCHASE").strip().upper()
    if text in {"SALE", "VENTE", "CREDIT"}:
        return "SALE"
    return "PURCHASE"


def _normalise_amounts(
    net: Decimal | None, tax: Decimal | None, gross: Decimal | None
) -> tuple[Decimal | None, Decimal | None, Decimal, bool]:
    negative = any(
        value is not None and value < 0 for value in (net, tax, gross)
    )
    if net is not None:
        net = abs(net)
    if tax is not None:
        tax = abs(tax)
    if gross is not None:
        gross = abs(gross)
    if gross is None:
        if net is None:
            raise InvoiceProducerError(
                "INVALID_VISION_PAYLOAD", "montant TTC absent"
            )
        gross = net + (tax or Decimal("0"))
    if net is None and tax is not None:
        net = gross - tax
    if net is not None and tax is None:
        tax = gross - net
    if net is not None and tax is not None:
        if net < 0 or tax < 0 or gross < 0:
            raise InvoiceProducerError(
                "INVALID_VISION_PAYLOAD", "montants négatifs non normalisables"
            )
        if abs(net + tax - gross) > Decimal("0.02"):
            tax = gross - net
        if tax < 0:
            tax = Decimal("0")
            net = gross
    return net, tax, gross, negative


def to_canonical_payload(raw: Mapping[str, Any], *, model: str) -> dict[str, Any]:
    amount_source = raw.get("amount")
    amount_source = amount_source if isinstance(amount_source, Mapping) else {}
    net = _decimal(
        amount_source.get("net_amount", raw.get("montant_ht", raw.get("net")))
    )
    tax = _decimal(
        amount_source.get("tax_amount", raw.get("montant_tva", raw.get("tax")))
    )
    gross = _decimal(
        amount_source.get(
            "gross_amount", raw.get("montant_ttc", raw.get("gross", raw.get("ttc")))
        )
    )
    net, tax, gross, negative = _normalise_amounts(net, tax, gross)

    supplier_source = raw.get("supplier")
    supplier = supplier_source if isinstance(supplier_source, Mapping) else {}
    supplier_name = (
        supplier.get("name")
        or raw.get("fournisseur")
        or (supplier_source if isinstance(supplier_source, str) else None)
        or "FOURNISSEUR_INCONNU"
    )
    reference_source = raw.get("reference")
    reference = reference_source if isinstance(reference_source, Mapping) else {}
    invoice_number = reference.get("invoice_number") or raw.get(
        "numero_facture"
    )
    issue_date = _normalise_date(
        reference.get("issue_date") or raw.get("date_facture")
    )
    currency = _normalise_currency(
        amount_source.get("currency")
        or raw.get("devise")
        or raw.get("currency")
    )
    supplier_vat = (
        supplier.get("vat_number")
        or raw.get("vat_number")
        or raw.get("tva_intra")
        or raw.get("numero_tva")
    )
    supplier_country = (
        raw.get("supplier_country")
        or supplier.get("country")
        or (str(supplier_vat)[:2] if supplier_vat else None)
    )
    amount: dict[str, Any] = {
        "currency": currency,
        "gross_amount": str(gross),
    }
    if net is not None:
        amount["net_amount"] = str(net)
    if tax is not None:
        amount["tax_amount"] = str(tax)

    supplier_payload: dict[str, Any] = {"name": str(supplier_name).strip()}
    siret = str(supplier.get("siret") or raw.get("siret") or "").strip()
    if re.fullmatch(r"\d{14}", siret):
        supplier_payload["siret"] = siret
    if supplier_vat:
        vat_text = re.sub(r"\s+", "", str(supplier_vat)).upper()
        if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{8,14}", vat_text):
            supplier_payload["vat_number"] = vat_text

    payload: dict[str, Any] = {
        "document_type": _normalise_document_type(
            raw.get("document_type") or raw.get("type_piece"),
            negative=negative,
        ),
        "direction": _normalise_direction(raw.get("direction")),
        "supplier": supplier_payload,
        "reference": {
            "invoice_number": str(invoice_number) if invoice_number else None,
            "issue_date": issue_date,
        },
        "amount": amount,
        "category": raw.get("category") or raw.get("categorie"),
        "metadata": {
            "extraction_tool": "ollama_vision",
            "vision_model": model,
            "supplier_country": (
                str(supplier_country).upper() if supplier_country else None
            ),
            "vision_credit_note": negative,
        },
    }
    if payload["direction"] == "SALE":
        payload["customer"] = payload.pop("supplier")
    return payload


def document_images(path: Path, *, dpi: int = 150) -> list[bytes]:
    """Return PNG page images for a PDF, or raw bytes for an image."""

    path = Path(path)
    if not path.is_file():
        raise OllamaVisionExtractorError(f"SOURCE_NOT_FOUND: {path}")
    if path.suffix.lower() != ".pdf":
        return [path.read_bytes()]
    pymupdf = load_pymupdf()
    with pymupdf.open(path) as document:
        images = [
            page.get_pixmap(dpi=dpi).tobytes("png") for page in document
        ]
    if not images:
        raise OllamaVisionExtractorError("PDF sans page exploitable")
    return images


class OllamaVisionExtractor:
    """``VisionExtractor`` backed by a local Ollama vision model."""

    def __init__(
        self,
        model: str | None = None,
        *,
        chat: Callable[..., Any] | None = None,
        dpi: int = 150,
    ) -> None:
        self.model = _resolve_model(model)
        self._chat = chat or _default_chat
        self.dpi = dpi

    def __call__(self, source: Path) -> dict[str, Any]:
        path = Path(source)
        if not path.is_file():
            raise OllamaVisionExtractorError(f"SOURCE_NOT_FOUND: {path}")
        images = document_images(path, dpi=self.dpi)
        try:
            response = self._chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": "Analyse cette facture et retourne le JSON.",
                        "images": images,
                    },
                ],
                options={"temperature": 0.0},
                format="json",
            )
        except Exception as exc:
            raise OllamaVisionExtractorError(
                f"OLLAMA_VISION_FAILED: {exc}"
            ) from exc

        try:
            raw = json.loads(strip_code_fences(_extract_content(response)))
        except (json.JSONDecodeError, TypeError) as exc:
            raise InvoiceProducerError(
                "INVALID_VISION_PAYLOAD",
                f"JSON illisible: {exc}",
                source=path,
            ) from exc
        if not isinstance(raw, Mapping):
            raise InvoiceProducerError(
                "INVALID_VISION_PAYLOAD",
                "la réponse Vision doit être un objet JSON",
                source=path,
            )

        payload = to_canonical_payload(raw, model=self.model)
        validate_produced_invoice(payload, source=path)
        return payload


__all__ = [
    "DEFAULT_MODEL_ENV_VARS",
    "OllamaVisionExtractor",
    "OllamaVisionExtractorError",
    "SYSTEM_PROMPT",
    "document_images",
    "strip_code_fences",
    "to_canonical_payload",
]
