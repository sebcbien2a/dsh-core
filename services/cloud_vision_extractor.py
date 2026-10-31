"""Cloud Vision fallback and hybrid local/cloud vision strategy.

``CloudVisionExtractor`` calls OpenRouter with ``CLOUD_VISION_MODEL`` (or
``VISION_FAST``) and returns the same canonical payload as the local adapters.
``HybridVisionExtractor`` tries the local two-stage extractor first and
escalates to the strict Gemini supervisor only on schema-anchoring failures
(``INVALID_VISION_PAYLOAD`` / missing TTC / empty Ollama response). Other local
failures (timeout, OCR unavailable) stay local and are never sent to the cloud.
"""

from __future__ import annotations

import base64
import json
import math
import os
import threading
from pathlib import Path
from typing import Any, Callable, Mapping

import requests

from services.invoice_producer_protocol import validate_produced_invoice
from services.local_invoice_extractor import LocalInvoiceExtractor
from services.ollama_vision_extractor import (
    SYSTEM_PROMPT,
    OllamaVisionExtractor,
    document_images,
    strip_code_fences,
    to_canonical_payload,
)

DEFAULT_API_BASE = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT = 120.0
CLOUD_MODEL_ENV_VARS = ("CLOUD_VISION_MODEL", "VISION_FAST")
ESCALATION_REASON = "local_vision_failed_escalated_to_cloud"
SUPERVISOR_ESCALATION_MARKERS = (
    "INVALID_VISION_PAYLOAD",
    "INVALID_PRODUCER_PAYLOAD",
    "MONTANT TTC ABSENT",
    "RÉPONSE_OLLAMA_VIDE",
    "REPONSE_OLLAMA_VIDE",
    "LOCAL_INFERENCE_TIMEOUT",
)


def should_escalate_to_supervisor(error: BaseException | str) -> bool:
    """Return True only for strict schema-anchoring or empty-response failures."""

    text = str(error).upper()
    return any(marker in text for marker in SUPERVISOR_ESCALATION_MARKERS)


class CloudVisionExtractorError(RuntimeError):
    """Raised when the cloud vision provider is unavailable or invalid."""


def resolve_cloud_model(model: str | None) -> str:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    from model_router import validate_vision_model

    if model is not None and model.strip():
        return validate_vision_model(model)
    for variable in CLOUD_MODEL_ENV_VARS:
        value = os.getenv(variable)
        if value and value.strip():
            return validate_vision_model(value)
    raise CloudVisionExtractorError(
        "CLOUD_VISION_MODEL_NOT_CONFIGURED: définir CLOUD_VISION_MODEL ou VISION_FAST"
    )


def _response_json(response: Any) -> Mapping[str, Any]:
    if hasattr(response, "raise_for_status"):
        response.raise_for_status()
    data = response.json() if hasattr(response, "json") else response
    if not isinstance(data, Mapping):
        raise CloudVisionExtractorError("RÉPONSE_CLOUD_NON_JSON")
    return data


def _extract_cloud_content(data: Mapping[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise CloudVisionExtractorError("RÉPONSE_CLOUD_SANS_CHOICES")
    choice = choices[0]
    message = choice.get("message") if isinstance(choice, Mapping) else None
    if not isinstance(message, Mapping):
        raise CloudVisionExtractorError("RÉPONSE_CLOUD_SANS_MESSAGE")
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, Mapping)
        )
    if not isinstance(content, str) or not content.strip():
        raise CloudVisionExtractorError("RÉPONSE_CLOUD_VIDE")
    return content.strip()


def _image_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


class CloudVisionExtractor:
    """``VisionExtractor`` backed by OpenRouter cloud vision."""

    def __init__(
        self,
        model: str | None = None,
        *,
        api_key: str | None = None,
        api_base: str = DEFAULT_API_BASE,
        timeout: float = DEFAULT_TIMEOUT,
        post: Callable[..., Any] | None = None,
        dpi: int = 150,
    ) -> None:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        self.model = resolve_cloud_model(model)
        key = api_key if api_key is not None else os.getenv("OPENROUTER_API_KEY")
        if not key or not key.strip():
            raise CloudVisionExtractorError("OPENROUTER_API_KEY manquante")
        self.api_key = key.strip()
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self._post = post or requests.post
        self.dpi = dpi

    def __call__(self, source: Path) -> dict[str, Any]:
        path = Path(source)
        if not path.is_file():
            raise CloudVisionExtractorError(f"SOURCE_NOT_FOUND: {path}")
        images = document_images(path, dpi=self.dpi)
        mime = _image_mime(path)
        content: list[dict[str, Any]] = [
            {"type": "text", "text": "Analyse cette facture et retourne le JSON."}
        ]
        content.extend(
            {
                "type": "image_url",
                "image_url": {
                    "url": (
                        f"data:{mime};base64,"
                        f"{base64.b64encode(image).decode('ascii')}"
                    )
                },
            }
            for image in images
        )
        payload = {
            "model": self.model,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://dsh-core.local",
            "X-Title": "DSH-Core Cloud Vision",
        }
        try:
            response = self._post(
                f"{self.api_base}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            data = _response_json(response)
            content_text = _extract_cloud_content(data)
        except Exception as exc:
            if isinstance(exc, CloudVisionExtractorError):
                raise
            raise CloudVisionExtractorError(
                f"OPENROUTER_VISION_FAILED: {exc}"
            ) from exc

        try:
            raw = json.loads(strip_code_fences(content_text))
        except (json.JSONDecodeError, TypeError) as exc:
            raise CloudVisionExtractorError(
                f"INVALID_CLOUD_VISION_PAYLOAD: {exc}"
            ) from exc
        if not isinstance(raw, Mapping):
            raise CloudVisionExtractorError(
                "INVALID_CLOUD_VISION_PAYLOAD: objet JSON attendu"
            )
        canonical = to_canonical_payload(raw, model=self.model)
        metadata = canonical.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["extraction_tool"] = "openrouter_vision"
            metadata["vision_model"] = self.model
        validate_produced_invoice(canonical, source=path)
        return canonical


def budget_max(ratio: float, total: int) -> int:
    """Return the integer escalation budget for ``total`` documents."""

    return max(0, math.ceil(float(ratio) * max(0, int(total))))


class CloudEscalationBudget:
    """Hard budget shared by every cloud escalation of a batch audit."""

    def __init__(self, max_escalations: int | None = None) -> None:
        if max_escalations is not None and (
            isinstance(max_escalations, bool)
            or not isinstance(max_escalations, int)
        ):
            raise ValueError("BUDGET_MAX_MUST_BE_INT")
        self.max_escalations = max_escalations
        self.used = 0
        self._lock = threading.Lock()

    @property
    def exhausted(self) -> bool:
        return (
            self.max_escalations is not None
            and self.used >= self.max_escalations
        )

    def consume(self) -> bool:
        # check-and-increment must be atomic across threads
        with self._lock:
            if self.exhausted:
                return False
            self.used += 1
            return True


class HybridVisionExtractor:
    """Try local OCR + NuExtract3, then escalate to the strict Gemini supervisor."""

    def __init__(
        self,
        local: Any | None = None,
        cloud: Any | None = None,
        supervisor_model: str | None = None,
        budget: CloudEscalationBudget | None = None,
    ) -> None:
        self.local = local or LocalInvoiceExtractor()
        self.budget = budget
        if cloud is not None:
            self.cloud = cloud
        else:
            from model_router import resolve_multimodal_fallback_model

            self.cloud = CloudVisionExtractor(
                model=resolve_multimodal_fallback_model(supervisor_model)
            )

    def __call__(self, source: Path) -> dict[str, Any]:
        path = Path(source)
        try:
            payload = self.local(path)
            validate_produced_invoice(payload, source=path)
            return payload
        except Exception as local_exc:
            if not should_escalate_to_supervisor(local_exc):
                raise
            local_failure = str(local_exc)
            if self.budget is not None and not self.budget.consume():
                raise CloudVisionExtractorError(
                    f"ESCALATION_BUDGET_EXCEEDED: {local_failure}"
                ) from local_exc
            try:
                payload = self.cloud(path)
            except Exception as cloud_error:
                raise CloudVisionExtractorError(
                    "LOCAL_AND_CLOUD_VISION_FAILED: "
                    f"local={local_failure}; cloud={cloud_error}"
                ) from cloud_error

        metadata = payload.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            raise CloudVisionExtractorError(
                "INVALID_CLOUD_VISION_PAYLOAD: metadata doit être un dictionnaire"
            )
        metadata["fallback_reason"] = ESCALATION_REASON
        metadata["local_vision_error"] = local_failure
        metadata["escalation_cause"] = (
            "local_inference_timeout"
            if "LOCAL_INFERENCE_TIMEOUT" in local_failure.upper()
            else "schema_anchoring_failure"
        )
        supervisor_model = getattr(self.cloud, "model", None)
        if supervisor_model:
            metadata["supervisor_model"] = supervisor_model
        validate_produced_invoice(payload, source=path)
        return payload


__all__ = [
    "CLOUD_MODEL_ENV_VARS",
    "CloudEscalationBudget",
    "CloudVisionExtractor",
    "CloudVisionExtractorError",
    "ESCALATION_REASON",
    "HybridVisionExtractor",
    "SUPERVISOR_ESCALATION_MARKERS",
    "budget_max",
    "resolve_cloud_model",
    "should_escalate_to_supervisor",
]
