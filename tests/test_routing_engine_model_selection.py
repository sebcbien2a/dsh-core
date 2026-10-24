from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys

import pytest

for _module_name in ("pandas", "pymupdf", "ollama"):
    _stub = sys.modules.setdefault(_module_name, ModuleType(_module_name))
    if _module_name == "ollama" and not hasattr(_stub, "chat"):
        def _ollama_unavailable(*args: object, **kwargs: object) -> None:
            raise AssertionError("Ollama must not be called by model-free tests")

        _stub.chat = _ollama_unavailable

from services import routing_engine
from services.routing_engine import RoutingEngine


def _profile(profile_type: str) -> SimpleNamespace:
    return SimpleNamespace(
        profile_type=profile_type,
        extension=".png",
        word_count=0,
        has_tables=False,
        has_images=True,
        is_scanned=True,
    )


def test_explicit_vision_model_is_returned_without_loading_it() -> None:
    engine = RoutingEngine(vision_model="synthetic-vision-model")

    assert engine.vision_model == "synthetic-vision-model"


@pytest.mark.parametrize("retired_model", ["qwen2.5vl:7b", "qwen3.5:2b"])
def test_routing_engine_rejects_retired_injected_models(retired_model: str) -> None:
    with pytest.raises(ValueError, match="VISION_MODEL_NOT_CONFIGURED"):
        RoutingEngine(vision_model=retired_model)


def test_routing_engine_uses_central_local_vision_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VISION_FAST", raising=False)
    monkeypatch.setenv("LOCAL_VISION_MODEL", "synthetic-vision-model")
    engine = RoutingEngine()
    monkeypatch.setattr(engine, "_extract_vision", lambda path, model: "synthetic OCR text")

    result = engine.extract(Path("synthetic-scan.png"), _profile("image"))

    assert engine.vision_model is None
    assert result.extraction_tool == "Local Vision (synthetic-vision-model)"
    assert result.raw_text == "synthetic OCR text"


def test_scanned_image_uses_vision_route_without_calling_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = RoutingEngine(vision_model="synthetic-vision-model")
    monkeypatch.setattr(engine, "_extract_vision", lambda path, model: "synthetic OCR text")

    def fail_if_called(*args: object, **kwargs: object) -> None:
        pytest.fail("Ollama must not be called by the mocked routing test")

    monkeypatch.setattr(routing_engine.ollama, "chat", fail_if_called, raising=False)
    result = engine.extract(Path("synthetic-scan.png"), _profile("image"))

    assert result.extraction_tool == "Local Vision (synthetic-vision-model)"
    assert result.raw_text == "synthetic OCR text"
    assert "autorotate_applied" in result.processing_notes


def test_scan_without_vision_model_fails_before_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOCAL_VISION_MODEL", raising=False)
    engine = RoutingEngine()

    def fail_if_called(*args: object, **kwargs: object) -> None:
        pytest.fail("Ollama must not be called before model configuration validation")

    monkeypatch.setattr(routing_engine.ollama, "chat", fail_if_called, raising=False)
    with pytest.raises(ValueError, match="VISION_MODEL_NOT_CONFIGURED"):
        engine.extract(Path("synthetic-scan.png"), _profile("image"))


@pytest.mark.parametrize("profile_type", ["handwritten", "handwriting", "manuscrit"])
def test_handwritten_profiles_remain_unsupported(profile_type: str) -> None:
    engine = RoutingEngine(vision_model="synthetic-vision-model")

    with pytest.raises(ValueError, match="HANDWRITING_PATH_MISSING"):
        engine.extract(Path("synthetic-handwritten.png"), _profile(profile_type))
