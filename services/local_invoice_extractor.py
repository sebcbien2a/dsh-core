"""Two-stage local invoice extractor: raw text acquisition then NuExtract3.

Stage 1 obtains raw text from the configured ``LOCAL_OCR_ENGINE`` (PaddleOCR),
from the frozen OCR baseline (``ocr_baseline_texts.json``) when the source hash
matches, or from the native document text layer.

Stage 2 sends that raw text to ``LOCAL_EXTRACTION_MODEL`` through Ollama and
normalises the answer into a payload validated by ``InvoiceSchema``.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, Mapping

from model_router import (
    ExecutionMode,
    ModelRole,
    resolve_model,
    validate_model_identifier,
)
from schemas.invoice_schema import InvoiceSchema
from services.invoice_producer_protocol import (
    MIN_TEXT_LAYER_CHARS,
    InvoiceExtractionError,
    InvoiceProducerError,
    extract_document_text,
    normalise_text,
    validate_produced_invoice,
)
from services.ollama_vision_extractor import (
    _extract_content,
    document_images,
    strip_code_fences,
    to_canonical_payload,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE_PATH = REPO_ROOT / "benchmarks" / "data" / "ocr_baseline_texts.json"
DEFAULT_OCR_ENGINE = "PaddleOCR"
DEFAULT_INFERENCE_TIMEOUT_SECONDS = 90.0
INFERENCE_TIMEOUT_ENV = "LOCAL_INFERENCE_TIMEOUT_SECONDS"
DEFAULT_MODEL_ENV_VARS = ("LOCAL_EXTRACTION_MODEL", "NUEXTRACT_MODEL")


def _resolve_inference_timeout(value: float | None) -> float | None:
    if value is None:
        raw = os.getenv(INFERENCE_TIMEOUT_ENV)
        if raw and raw.strip():
            try:
                value = float(raw)
            except ValueError:
                value = DEFAULT_INFERENCE_TIMEOUT_SECONDS
        else:
            value = DEFAULT_INFERENCE_TIMEOUT_SECONDS
    if value is None or value <= 0:
        return None
    return float(value)


DISABLED_OCR_ENGINES = frozenset({"", "none", "disabled", "off"})
EXTRACTION_TOOL = "local_text_extraction"

CONSTRAINT_AUTO = "auto"
CONSTRAINT_SCHEMA = "schema"
CONSTRAINT_JSON = "json"
CONSTRAINT_NONE = "none"
CONSTRAINT_LADDER = (CONSTRAINT_SCHEMA, CONSTRAINT_JSON, CONSTRAINT_NONE)
DEFAULT_CONSTRAINT_MODE = CONSTRAINT_AUTO
FORMAT_MODE_ENV = "LOCAL_EXTRACTION_FORMAT_MODE"

EXTRACTION_TEMPLATE: dict[str, Any] = {
    "document_type": "INVOICE",
    "direction": "PURCHASE",
    "supplier": {
        "name": "string",
        "siret": "string",
        "vat_number": "string",
    },
    "reference": {
        "invoice_number": "string",
        "issue_date": "YYYY-MM-DD",
    },
    "amount": {
        "currency": "EUR",
        "net_amount": "number",
        "tax_amount": "number",
        "gross_amount": "number",
    },
    "category": "string",
    "supplier_country": "string",
}


class LocalInvoiceExtractorError(RuntimeError):
    """Raised when local OCR or NuExtract3 cannot produce a payload."""


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


def _default_chat(**kwargs: Any) -> Any:
    import ollama

    return ollama.chat(**kwargs)


def resolve_extraction_model(model: str | None = None) -> str:
    _load_dotenv()
    if model is not None and model.strip():
        return validate_model_identifier(model)
    for variable in DEFAULT_MODEL_ENV_VARS:
        value = os.getenv(variable)
        if value and value.strip():
            return validate_model_identifier(value)
    try:
        return resolve_model(ModelRole.EXTRACTION, ExecutionMode.LOCAL)
    except ValueError as exc:
        raise LocalInvoiceExtractorError(
            "LOCAL_EXTRACTION_MODEL_NOT_CONFIGURED: définir "
            "LOCAL_EXTRACTION_MODEL"
        ) from exc


_INVOICE_JSON_SCHEMA: dict[str, Any] | None = None


def invoice_json_schema() -> dict[str, Any]:
    global _INVOICE_JSON_SCHEMA
    if _INVOICE_JSON_SCHEMA is None:
        _INVOICE_JSON_SCHEMA = InvoiceSchema.model_json_schema()
    return _INVOICE_JSON_SCHEMA


def normalise_constraint_mode(value: str | None) -> str:
    if value is None:
        return DEFAULT_CONSTRAINT_MODE
    mode = value.strip().casefold()
    if mode in {CONSTRAINT_AUTO, CONSTRAINT_SCHEMA, CONSTRAINT_JSON, CONSTRAINT_NONE}:
        return mode
    return DEFAULT_CONSTRAINT_MODE


def build_chat_format(mode: str) -> Any:
    normalised = normalise_constraint_mode(mode)
    if normalised == CONSTRAINT_NONE:
        return None
    if normalised == CONSTRAINT_JSON:
        return "json"
    return invoice_json_schema()


def _structured_payload_complete(raw: Mapping[str, Any]) -> bool:
    supplier = raw.get("supplier")
    supplier_name = (
        supplier.get("name") if isinstance(supplier, Mapping) else supplier
    )
    reference = raw.get("reference")
    has_reference = (
        isinstance(reference, Mapping)
        and bool(reference.get("invoice_number") or reference.get("issue_date"))
    ) or bool(raw.get("numero_facture") or raw.get("date_facture"))
    return bool(str(supplier_name or raw.get("fournisseur") or "").strip()) and bool(
        has_reference
    )


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        if "rec_texts" in value:
            texts = value["rec_texts"]
            if isinstance(texts, (list, tuple)):
                return "\n".join(
                    text for text in (str(item).strip() for item in texts) if text
                )
        for key in ("text", "rec_text", "transcription", "content", "res"):
            if key in value:
                return _coerce_text(value[key])
        return _coerce_text(list(value.values()))
    for attribute in ("to_dict", "json"):
        converter = getattr(value, attribute, None)
        if converter is None:
            continue
        if callable(converter):
            try:
                converter = converter()
            except Exception:
                continue
        if converter is not None and converter is not value:
            return _coerce_text(converter)
    if isinstance(value, (list, tuple)):
        if (
            len(value) == 2
            and isinstance(value[0], str)
            and isinstance(value[1], (int, float))
        ):
            return value[0].strip()
        if (
            len(value) == 2
            and isinstance(value[0], (list, tuple))
            and isinstance(value[1], (list, tuple))
            and len(value[1]) == 2
            and isinstance(value[1][0], str)
        ):
            return value[1][0].strip()
        if value and all(
            isinstance(point, (list, tuple))
            and len(point) == 2
            and all(isinstance(axis, (int, float)) for axis in point)
            for point in value
        ):
            return ""
        parts = (_coerce_text(item) for item in value)
        return "\n".join(part for part in parts if part)
    return str(value).strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_ocr_baseline_texts(path: str | Path) -> dict[str, str]:
    """Index frozen OCR texts by source SHA-256."""

    baseline_path = Path(path)
    if not baseline_path.is_file():
        return {}
    try:
        data = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    cases = data.get("cases") if isinstance(data, Mapping) else None
    if not isinstance(cases, list):
        return {}
    indexed: dict[str, str] = {}
    for case in cases:
        if not isinstance(case, Mapping):
            continue
        text = case.get("ocr_text")
        metadata = case.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        digest = metadata.get("source_sha256")
        if isinstance(text, str) and text.strip() and isinstance(digest, str):
            indexed[digest.casefold()] = text
    return indexed


def build_extraction_prompt(text: str) -> str:
    return (
        "<|input|>\n"
        "### Template:\n"
        f"{json.dumps(EXTRACTION_TEMPLATE, ensure_ascii=False)}\n"
        "### Text:\n"
        f"{text}\n"
        "<|output|>"
    )


class PaddleOCREngine:
    """Lazy PaddleOCR adapter returning plain text for a document."""

    name = "PaddleOCR"

    def __init__(self, *, lang: str = "fr", dpi: int = 170) -> None:
        self.lang = lang
        self.dpi = dpi
        self._engine: Any = None

    def _load(self) -> Any:
        if self._engine is not None:
            return self._engine
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise LocalInvoiceExtractorError("PADDLEOCR_UNAVAILABLE") from exc
        attempts = (
            {
                "lang": self.lang,
                "use_doc_orientation_classify": False,
                "use_doc_unwarping": False,
                "use_textline_orientation": False,
            },
            {"lang": self.lang, "use_angle_cls": False, "show_log": False},
            {"lang": self.lang},
        )
        last_error: Exception | None = None
        for kwargs in attempts:
            try:
                self._engine = PaddleOCR(**kwargs)
                return self._engine
            except (TypeError, ValueError) as exc:
                last_error = exc
        raise LocalInvoiceExtractorError(
            f"PADDLEOCR_INIT_FAILED: {last_error}"
        ) from last_error

    def _image_arrays(self, source: Path) -> list[Any]:
        import numpy
        from PIL import Image

        return [
            numpy.array(Image.open(io.BytesIO(raw)).convert("RGB"))
            for raw in document_images(source, dpi=self.dpi)
        ]

    def __call__(self, source: Path) -> str:
        engine = self._load()
        outputs: list[Any] = []
        for image in self._image_arrays(source):
            if hasattr(engine, "predict"):
                try:
                    outputs.append(engine.predict(image))
                    continue
                except TypeError:
                    pass
            outputs.append(engine.ocr(image, cls=True))
        return _coerce_text(outputs)


class LocalInvoiceExtractor:
    """``VisionExtractor`` backed by OCR/text layer then local NuExtract3."""

    def __init__(
        self,
        model: str | None = None,
        *,
        chat: Callable[..., Any] | None = None,
        ocr_engine: Callable[[Path], Any] | None = None,
        ocr_engine_name: str | None = None,
        baseline_path: str | Path | None = None,
        dpi: int = 170,
        min_text_chars: int = MIN_TEXT_LAYER_CHARS,
        format_mode: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        _load_dotenv()
        self.model = resolve_extraction_model(model)
        self._chat = chat or _default_chat
        self._ocr_engine = ocr_engine
        self.format_mode = normalise_constraint_mode(
            format_mode if format_mode is not None else os.getenv(FORMAT_MODE_ENV)
        )
        self._constraint_mode: str | None = (
            None
            if self.format_mode == CONSTRAINT_AUTO
            else self.format_mode
        )
        self.ocr_engine_name = (
            ocr_engine_name
            if ocr_engine_name is not None
            else os.getenv("LOCAL_OCR_ENGINE") or DEFAULT_OCR_ENGINE
        )
        self.baseline_path = (
            Path(baseline_path) if baseline_path is not None else DEFAULT_BASELINE_PATH
        )
        self.dpi = dpi
        self.min_text_chars = min_text_chars
        self.timeout_seconds = _resolve_inference_timeout(timeout_seconds)
        self._baseline: dict[str, str] | None = None
        self._paddle: PaddleOCREngine | None = None

    def _baseline_text(self, source: Path) -> str:
        if self._baseline is None:
            self._baseline = load_ocr_baseline_texts(self.baseline_path)
        try:
            digest = _sha256(source)
        except OSError:
            return ""
        return self._baseline.get(digest.casefold(), "")

    def _configured_ocr_engine(self) -> Callable[[Path], Any] | None:
        name = self.ocr_engine_name.strip()
        if name.casefold() in DISABLED_OCR_ENGINES:
            return None
        if "paddle" not in name.casefold():
            return None
        if self._paddle is None:
            self._paddle = PaddleOCREngine(dpi=self.dpi)
        return self._paddle

    def extract_text(self, source: str | Path) -> tuple[str, str]:
        """Stage 1: return raw text and the acquisition tool that produced it."""

        path = Path(source)
        if not path.is_file():
            raise InvoiceProducerError(
                "SOURCE_NOT_FOUND",
                "le fichier source est introuvable",
                source=path,
            )

        if self._ocr_engine is not None:
            return _coerce_text(self._ocr_engine(path)), "custom_ocr"

        ocr_error: str | None = None
        engine = self._configured_ocr_engine()
        if engine is not None:
            try:
                text = _coerce_text(engine(path))
            except Exception as exc:
                ocr_error = str(exc)
            else:
                if len(normalise_text(" ".join(text.split()))) >= self.min_text_chars:
                    return text, self.ocr_engine_name

        baseline_text = self._baseline_text(path)
        if baseline_text.strip():
            return baseline_text, "ocr_baseline_texts"

        try:
            layer_text = extract_document_text(path)
        except InvoiceExtractionError:
            layer_text = ""
        flat_layer = normalise_text(" ".join(layer_text.split()))
        if len(flat_layer.strip()) >= self.min_text_chars:
            return layer_text, "text_layer"

        detail = (
            f"LOCAL_OCR_TEXT_UNAVAILABLE: {self.ocr_engine_name}"
            if ocr_error is None
            else f"LOCAL_OCR_FAILED: {ocr_error}"
        )
        raise InvoiceProducerError("OCR_TEXT_UNAVAILABLE", detail, source=path)

    def _constraint_modes(
        self, format_mode: str | None = None
    ) -> tuple[str, ...]:
        mode = (
            self.format_mode
            if format_mode is None
            else normalise_constraint_mode(format_mode)
        )
        if mode != CONSTRAINT_AUTO:
            return (mode,)
        start = self._constraint_mode or CONSTRAINT_SCHEMA
        return CONSTRAINT_LADDER[CONSTRAINT_LADDER.index(start):]

    def extract_payload(
        self,
        text: str,
        *,
        source: Path,
        ocr_source: str,
        format_mode: str | None = None,
    ) -> dict[str, Any]:
        """Stage 2: constrain NuExtract3 and downshift on strict failure."""

        if not text.strip():
            raise LocalInvoiceExtractorError("OCR_TEXT_EMPTY")

        prompt = build_extraction_prompt(text)
        modes = self._constraint_modes(format_mode)
        attempts: list[str] = []
        fallback_reason: str | None = None

        for attempt_index, mode in enumerate(modes):
            try:
                payload = self._extract_payload_once(
                    prompt,
                    mode=mode,
                    source=source,
                    ocr_source=ocr_source,
                    text=text,
                    attempt_index=attempt_index,
                    fallback_reason=fallback_reason,
                )
            except Exception as exc:
                attempts.append(f"{mode}: {exc}")
                if "LOCAL_INFERENCE_TIMEOUT" in str(exc):
                    raise LocalInvoiceExtractorError(
                        f"OLLAMA_EXTRACTION_FAILED: {exc}"
                    ) from exc
                if attempt_index + 1 < len(modes):
                    fallback_reason = str(exc)
                    self._constraint_mode = modes[attempt_index + 1]
            else:
                self._constraint_mode = mode
                return payload

        raise LocalInvoiceExtractorError(
            "OLLAMA_EXTRACTION_FAILED: " + " | ".join(attempts)
        )

    def _call_chat(self, **kwargs: Any) -> Any:
        timeout = self.timeout_seconds
        if timeout is None:
            return self._chat(**kwargs)
        outcome: dict[str, Any] = {}

        def _target() -> None:
            try:
                outcome["value"] = self._chat(**kwargs)
            except BaseException as exc:  # noqa: BLE001 - forwarded to caller
                outcome["error"] = exc

        thread = threading.Thread(
            target=_target, name="dsh-local-inference", daemon=True
        )
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            raise LocalInvoiceExtractorError(
                f"LOCAL_INFERENCE_TIMEOUT: {timeout:g}s dépassés"
            )
        if "error" in outcome:
            raise outcome["error"]
        return outcome["value"]

    def _extract_payload_once(
        self,
        prompt: str,
        *,
        mode: str,
        source: Path,
        ocr_source: str,
        text: str,
        attempt_index: int,
        fallback_reason: str | None,
    ) -> dict[str, Any]:
        try:
            response = self._call_chat(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.0},
                format=build_chat_format(mode),
            )
        except Exception as exc:
            raise LocalInvoiceExtractorError(
                f"OLLAMA_EXTRACTION_FAILED: {exc}"
            ) from exc

        try:
            raw = json.loads(strip_code_fences(_extract_content(response)))
        except (json.JSONDecodeError, TypeError) as exc:
            raise InvoiceProducerError(
                "INVALID_EXTRACTION_PAYLOAD",
                f"JSON illisible: {exc}",
                source=source,
            ) from exc
        if not isinstance(raw, Mapping):
            raise InvoiceProducerError(
                "INVALID_EXTRACTION_PAYLOAD",
                "la réponse NuExtract3 doit être un objet JSON",
                source=source,
            )
        if mode == CONSTRAINT_SCHEMA and not _structured_payload_complete(raw):
            raise InvoiceProducerError(
                "INCOMPLETE_STRUCTURED_PAYLOAD",
                "le schéma strict a omis le fournisseur ou la référence",
                source=source,
            )

        payload = to_canonical_payload(raw, model=self.model)
        metadata = payload.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata.update(
                {
                    "extraction_tool": EXTRACTION_TOOL,
                    "extraction_model": self.model,
                    "ocr_engine": ocr_source,
                    "ocr_text_chars": len(text),
                    "constraint_mode": mode,
                    "constraint_retries": attempt_index,
                }
            )
            if fallback_reason is not None:
                metadata["constraint_fallback_reason"] = fallback_reason
        validate_produced_invoice(payload, source=source)
        return payload

    def __call__(self, source: str | Path) -> dict[str, Any]:
        path = Path(source)
        text, ocr_source = self.extract_text(path)
        return self.extract_payload(text, source=path, ocr_source=ocr_source)


LocalTextExtractionExtractor = LocalInvoiceExtractor
LocalTextInvoiceExtractor = LocalInvoiceExtractor

__all__ = [
    "CONSTRAINT_AUTO",
    "CONSTRAINT_JSON",
    "CONSTRAINT_LADDER",
    "CONSTRAINT_NONE",
    "CONSTRAINT_SCHEMA",
    "DEFAULT_BASELINE_PATH",
    "DEFAULT_CONSTRAINT_MODE",
    "DEFAULT_INFERENCE_TIMEOUT_SECONDS",
    "DEFAULT_MODEL_ENV_VARS",
    "DEFAULT_OCR_ENGINE",
    "EXTRACTION_TEMPLATE",
    "EXTRACTION_TOOL",
    "FORMAT_MODE_ENV",
    "INFERENCE_TIMEOUT_ENV",
    "LocalInvoiceExtractor",
    "LocalInvoiceExtractorError",
    "LocalTextExtractionExtractor",
    "LocalTextInvoiceExtractor",
    "PaddleOCREngine",
    "build_chat_format",
    "build_extraction_prompt",
    "invoice_json_schema",
    "load_ocr_baseline_texts",
    "normalise_constraint_mode",
    "resolve_extraction_model",
]
