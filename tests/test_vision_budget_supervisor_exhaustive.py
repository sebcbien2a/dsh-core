"""Exhaustive budget / quota / error-path tests for the vision supervisor.

The code under test lives in ``services.cloud_vision_extractor``
(``CloudEscalationBudget``, ``budget_max``, ``HybridVisionExtractor``,
``CloudVisionExtractor``). There is no retry, circuit-breaker or time-window
logic in that module: the tests pin the actual contract (one network call per
attempt, failures wrapped, budget scoped to a batch instance). No real network
call is ever made: ``requests.post`` is replaced by a guard fixture.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import requests

import services.cloud_vision_extractor as cve
from services.cloud_vision_extractor import (
    ESCALATION_REASON,
    CloudEscalationBudget,
    CloudVisionExtractor,
    CloudVisionExtractorError,
    HybridVisionExtractor,
    budget_max,
    should_escalate_to_supervisor,
)

TRIGGER = "INVALID_VISION_PAYLOAD: montant TTC absent"


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("real network call attempted")

    monkeypatch.setattr(requests, "post", _forbidden)
    monkeypatch.setattr(requests, "get", _forbidden)
    monkeypatch.setattr(requests.Session, "request", _forbidden)


def _payload() -> dict[str, Any]:
    return {
        "document_type": "INVOICE",
        "direction": "PURCHASE",
        "supplier": {"name": "VISION SAS", "vat_number": "FR12345678901"},
        "reference": {
            "invoice_number": "V-2026-001",
            "issue_date": "2026-09-30",
        },
        "amount": {
            "currency": "EUR",
            "net_amount": "100.00",
            "tax_amount": "20.00",
            "gross_amount": "120.00",
        },
        "metadata": {
            "supplier_country": "FR",
            "extraction_tool": "vision_extractor",
        },
    }


class _Local:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0

    def __call__(self, source: Path) -> dict[str, Any]:
        self.calls += 1
        raise self.error


class _Cloud:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, source: Path) -> dict[str, Any]:
        with self._lock:
            self.calls += 1
        if self.error is not None:
            raise self.error
        return _payload()


def _hybrid(
    budget: CloudEscalationBudget | None,
    cloud: Any,
    error: str = TRIGGER,
) -> HybridVisionExtractor:
    return HybridVisionExtractor(
        local=_Local(RuntimeError(error)), cloud=cloud, budget=budget
    )


# --------------------------------------------------------------------------
# budget_max
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ratio", "total", "expected"),
    [
        (0.0, 100, 0),
        (0.1, 10, 1),
        (0.1, 11, 2),  # ceil
        (1.0, 7, 7),
        (0.5, 0, 0),
        (0.5, -5, 0),
        (-1.0, 10, 0),
        (2.0, 3, 6),
    ],
)
def test_budget_max(ratio: float, total: int, expected: int) -> None:
    assert budget_max(ratio, total) == expected


# --------------------------------------------------------------------------
# CloudEscalationBudget: strict quota
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [True, False, 1.5, "3", [1]])
def test_budget_rejects_non_int(bad: Any) -> None:
    with pytest.raises(ValueError, match="BUDGET_MAX_MUST_BE_INT"):
        CloudEscalationBudget(bad)


def test_budget_unlimited_never_exhausts() -> None:
    budget = CloudEscalationBudget(None)
    assert all(budget.consume() for _ in range(1000))
    assert not budget.exhausted
    assert budget.used == 1000


def test_budget_zero_is_exhausted_immediately() -> None:
    budget = CloudEscalationBudget(0)
    assert budget.exhausted
    assert budget.consume() is False
    assert budget.used == 0


def test_budget_strict_limit_not_exceeded() -> None:
    budget = CloudEscalationBudget(3)
    results = [budget.consume() for _ in range(6)]
    assert results == [True, True, True, False, False, False]
    assert budget.used == 3
    assert budget.exhausted


def test_budget_refused_consume_does_not_increment() -> None:
    budget = CloudEscalationBudget(1)
    budget.consume()
    for _ in range(10):
        budget.consume()
    assert budget.used == 1


def test_budget_negative_limit_blocks_everything() -> None:
    budget = CloudEscalationBudget(-1)
    assert budget.exhausted
    assert budget.consume() is False
    assert budget.used == 0


# --------------------------------------------------------------------------
# Hybrid: blocked cleanly without network / cloud call
# --------------------------------------------------------------------------


def test_exhausted_budget_blocks_without_cloud_call() -> None:
    cloud = _Cloud()
    hybrid = _hybrid(CloudEscalationBudget(0), cloud)

    with pytest.raises(CloudVisionExtractorError) as info:
        hybrid(Path("invoice.pdf"))

    assert "ESCALATION_BUDGET_EXCEEDED" in str(info.value)
    assert TRIGGER in str(info.value)
    assert cloud.calls == 0


def test_quota_exceeded_on_nth_document_only() -> None:
    cloud = _Cloud()
    budget = CloudEscalationBudget(2)
    hybrid = _hybrid(budget, cloud)

    assert hybrid(Path("a.pdf"))["metadata"]["fallback_reason"] == (
        ESCALATION_REASON
    )
    assert hybrid(Path("b.pdf"))
    with pytest.raises(CloudVisionExtractorError, match="BUDGET_EXCEEDED"):
        hybrid(Path("c.pdf"))
    assert cloud.calls == 2
    assert budget.used == 2


def test_budget_is_consumed_even_if_cloud_fails() -> None:
    cloud = _Cloud(RuntimeError("boom"))
    budget = CloudEscalationBudget(1)
    hybrid = _hybrid(budget, cloud)

    with pytest.raises(CloudVisionExtractorError, match="LOCAL_AND_CLOUD"):
        hybrid(Path("a.pdf"))
    assert budget.used == 1
    assert cloud.calls == 1

    with pytest.raises(CloudVisionExtractorError, match="BUDGET_EXCEEDED"):
        hybrid(Path("b.pdf"))
    assert cloud.calls == 1


def test_non_escalatable_error_does_not_consume_budget() -> None:
    cloud = _Cloud()
    budget = CloudEscalationBudget(1)
    hybrid = _hybrid(budget, cloud, error="ollama connection refused")

    with pytest.raises(RuntimeError, match="connection refused"):
        hybrid(Path("a.pdf"))
    assert budget.used == 0
    assert cloud.calls == 0


def test_no_budget_means_unlimited_escalations() -> None:
    cloud = _Cloud()
    hybrid = _hybrid(None, cloud)
    for _ in range(25):
        hybrid(Path("a.pdf"))
    assert cloud.calls == 25


def test_timeout_cause_metadata_and_budget() -> None:
    budget = CloudEscalationBudget(1)
    hybrid = _hybrid(
        budget, _Cloud(), error="LOCAL_INFERENCE_TIMEOUT: 90s dépassés"
    )
    meta = hybrid(Path("a.pdf"))["metadata"]
    assert meta["escalation_cause"] == "local_inference_timeout"
    assert budget.used == 1


def test_supervisor_model_recorded_from_cloud() -> None:
    cloud = _Cloud()
    cloud.model = "google/gemini-test"  # type: ignore[attr-defined]
    meta = _hybrid(None, cloud)(Path("a.pdf"))["metadata"]
    assert meta["supervisor_model"] == "google/gemini-test"


def test_cloud_returns_non_dict_metadata_is_rejected() -> None:
    def cloud(source: Path) -> dict[str, Any]:
        data = _payload()
        data["metadata"] = "bad"
        return data

    with pytest.raises(CloudVisionExtractorError, match="metadata"):
        _hybrid(None, cloud)(Path("a.pdf"))


@pytest.mark.parametrize(
    "text",
    [
        "invalid_vision_payload",
        "réponse_ollama_vide",
        "reponse_ollama_vide",
        "Local_Inference_Timeout",
    ],
)
def test_escalation_markers_case_insensitive(text: str) -> None:
    assert should_escalate_to_supervisor(text)


@pytest.mark.parametrize(
    "text", ["", "HTTP 429", "503 Service Unavailable", "read timed out"]
)
def test_http_errors_are_not_escalation_markers(text: str) -> None:
    assert not should_escalate_to_supervisor(text)
    assert not should_escalate_to_supervisor(RuntimeError(text))


# --------------------------------------------------------------------------
# Temporal windows: the budget has no clock; scope == instance lifetime.
# --------------------------------------------------------------------------


def test_budget_does_not_depend_on_wall_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = CloudEscalationBudget(1)
    assert budget.consume()
    # Simulate hour change, month change and DST: nothing resets implicitly.
    for moment in (
        datetime(2026, 3, 29, 1, 59, tzinfo=timezone.utc),
        datetime(2026, 3, 29, 2, 1, tzinfo=timezone.utc),
        datetime(2026, 10, 25, 0, 59, tzinfo=timezone.utc),
        datetime(2026, 10, 31, 23, 59, 59, tzinfo=timezone.utc),
        datetime(2026, 11, 1, 0, 0, 1, tzinfo=timezone.utc),
    ):
        monkeypatch.setattr("time.time", lambda m=moment: m.timestamp())
        assert budget.consume() is False
    assert budget.used == 1


def test_new_window_is_a_new_budget_instance() -> None:
    window_end = datetime(2026, 10, 31, 23, 59, 59, tzinfo=timezone.utc)
    next_window = window_end + timedelta(seconds=1)
    assert next_window.month == 11 and next_window.tzinfo is timezone.utc

    first = CloudEscalationBudget(1)
    assert first.consume() and not first.consume()
    second = CloudEscalationBudget(1)
    assert second.consume() and second.used == 1
    assert first.used == 1  # windows are independent


def test_budget_window_rollover_via_batch_size() -> None:
    assert budget_max(0.1, 50) == 5
    assert budget_max(0.1, 51) == 6


# --------------------------------------------------------------------------
# Concurrency
# --------------------------------------------------------------------------


def _run_threads(n: int, target: Any) -> None:
    barrier = threading.Barrier(n)

    def _wrapped() -> None:
        barrier.wait()
        target()

    threads = [threading.Thread(target=_wrapped) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads)


@pytest.mark.parametrize("limit", [1, 7, 50])
def test_concurrent_consume_never_exceeds_limit(limit: int) -> None:
    budget = CloudEscalationBudget(limit)
    granted: list[bool] = []
    lock = threading.Lock()

    def work() -> None:
        for _ in range(200):
            ok = budget.consume()
            with lock:
                granted.append(ok)

    _run_threads(16, work)

    assert sum(granted) == limit
    assert budget.used == limit
    assert len(granted) == 16 * 200


def test_concurrent_consume_unlimited_counts_exactly() -> None:
    budget = CloudEscalationBudget(None)

    def work() -> None:
        for _ in range(500):
            budget.consume()

    _run_threads(8, work)
    assert budget.used == 8 * 500


def test_concurrent_hybrid_calls_cloud_at_most_limit_times() -> None:
    cloud = _Cloud()
    budget = CloudEscalationBudget(5)
    hybrid = _hybrid(budget, cloud)
    outcomes: list[str] = []
    lock = threading.Lock()

    def work() -> None:
        for _ in range(10):
            try:
                hybrid(Path("a.pdf"))
                result = "ok"
            except CloudVisionExtractorError as exc:
                result = "blocked" if "BUDGET_EXCEEDED" in str(exc) else "err"
            with lock:
                outcomes.append(result)

    _run_threads(8, work)

    assert outcomes.count("ok") == 5
    assert outcomes.count("blocked") == 75
    assert outcomes.count("err") == 0
    assert cloud.calls == 5


def test_budget_consume_idempotent_when_exhausted_under_threads() -> None:
    budget = CloudEscalationBudget(0)
    _run_threads(8, lambda: [budget.consume() for _ in range(100)])
    assert budget.used == 0


# --------------------------------------------------------------------------
# CloudVisionExtractor: mocked HTTP errors
# --------------------------------------------------------------------------


class _Resp:
    def __init__(
        self, status: int = 200, body: Any = None, text: str = ""
    ) -> None:
        self.status_code = status
        self._body = body
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"{self.status_code} error", response=self  # type: ignore[arg-type]
            )

    def json(self) -> Any:
        if isinstance(self._body, BaseException):
            raise self._body
        return self._body


class _Post:
    """Scripted fake for ``requests.post`` recording every call."""

    def __init__(self, *steps: Any) -> None:
        self.steps = list(steps)
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        if isinstance(step, BaseException):
            raise step
        return step


@pytest.fixture
def source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "invoice.png"
    path.write_bytes(b"\x89PNG fake")
    monkeypatch.setattr(
        cve, "document_images", lambda p, dpi=150: [b"img-bytes"]
    )
    monkeypatch.setattr(
        cve, "resolve_cloud_model", lambda model: model or "test/model"
    )
    return path


def _extractor(post: _Post) -> CloudVisionExtractor:
    return CloudVisionExtractor(
        model="test/model", api_key="  sk-test  ", post=post, timeout=1.0
    )


def _ok_body(content: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}}]}


@pytest.mark.parametrize("status", [429, 503, 500, 502, 504])
def test_http_error_status_wrapped_no_retry(source: Path, status: int) -> None:
    post = _Post(_Resp(status))
    with pytest.raises(
        CloudVisionExtractorError, match="OPENROUTER_VISION_FAILED"
    ) as info:
        _extractor(post)(source)
    assert str(status) in str(info.value)
    assert len(post.calls) == 1  # no hidden retry storm
    assert isinstance(info.value.__cause__, requests.HTTPError)


@pytest.mark.parametrize(
    "exc",
    [
        requests.Timeout("read timed out"),
        requests.ConnectTimeout("connect timed out"),
        requests.ConnectionError("connection reset"),
        TimeoutError("socket timeout"),
        OSError("network down"),
    ],
)
def test_network_errors_wrapped(source: Path, exc: BaseException) -> None:
    post = _Post(exc)
    with pytest.raises(
        CloudVisionExtractorError, match="OPENROUTER_VISION_FAILED"
    ):
        _extractor(post)(source)
    assert len(post.calls) == 1


def test_timeout_is_forwarded_to_post(source: Path) -> None:
    post = _Post(requests.Timeout("x"))
    with pytest.raises(CloudVisionExtractorError):
        _extractor(post)(source)
    _, kwargs = post.calls[0]
    assert kwargs["timeout"] == 1.0
    assert "sk-test" in kwargs["headers"]["Authorization"]
    assert kwargs["json"]["temperature"] == 0.0


def test_api_key_is_stripped_and_url_normalised(source: Path) -> None:
    post = _Post(_Resp(503))
    ext = CloudVisionExtractor(
        model="m", api_key=" k ", api_base="https://x.test/v1///", post=post
    )
    assert ext.api_key == "k"
    with pytest.raises(CloudVisionExtractorError):
        ext(source)
    assert post.calls[0][0][0] == "https://x.test/v1/chat/completions"


@pytest.mark.parametrize("key", ["", "   "])
def test_missing_api_key_rejected(
    source: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(CloudVisionExtractorError, match="API_KEY"):
        CloudVisionExtractor(model="m", api_key=key, post=_Post(_Resp()))


def test_source_not_found_makes_no_call(tmp_path: Path) -> None:
    post = _Post(_Resp(200, _ok_body("{}")))
    ext = CloudVisionExtractor(model="m", api_key="k", post=post)
    with pytest.raises(CloudVisionExtractorError, match="SOURCE_NOT_FOUND"):
        ext(tmp_path / "missing.pdf")
    assert post.calls == []


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ([], "NON_JSON"),
        ({}, "SANS_CHOICES"),
        ({"choices": []}, "SANS_CHOICES"),
        ({"choices": ["x"]}, "SANS_MESSAGE"),
        ({"choices": [{"message": "x"}]}, "SANS_MESSAGE"),
        ({"choices": [{"message": {"content": "  "}}]}, "VIDE"),
        ({"choices": [{"message": {"content": None}}]}, "VIDE"),
        ({"choices": [{"message": {"content": [{"text": ""}]}}]}, "VIDE"),
    ],
)
def test_malformed_cloud_responses(source: Path, body: Any, match: str) -> None:
    post = _Post(_Resp(200, body))
    with pytest.raises(CloudVisionExtractorError, match=match):
        _extractor(post)(source)
    assert len(post.calls) == 1


def test_rate_limit_error_body_without_choices(source: Path) -> None:
    post = _Post(_Resp(200, {"error": {"code": 429, "message": "rate"}}))
    with pytest.raises(CloudVisionExtractorError, match="SANS_CHOICES"):
        _extractor(post)(source)


def test_invalid_json_response_wrapped(source: Path) -> None:
    post = _Post(_Resp(200, ValueError("not json")))
    with pytest.raises(
        CloudVisionExtractorError, match="OPENROUTER_VISION_FAILED"
    ):
        _extractor(post)(source)


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '"str"', "{"])
def test_invalid_payload_content(source: Path, content: str) -> None:
    post = _Post(_Resp(200, _ok_body(content)))
    with pytest.raises(
        CloudVisionExtractorError, match="INVALID_CLOUD_VISION_PAYLOAD"
    ):
        _extractor(post)(source)


def test_success_path_single_call(
    source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cve, "to_canonical_payload", lambda raw, model: dict(_payload())
    )
    monkeypatch.setattr(cve, "validate_produced_invoice", lambda p, source: p)
    content = [{"type": "text", "text": json.dumps({"a": 1})}]
    post = _Post(_Resp(200, {"choices": [{"message": {"content": content}}]}))

    result = _extractor(post)(source)

    assert len(post.calls) == 1
    assert result["metadata"]["extraction_tool"] == "openrouter_vision"
    assert result["metadata"]["vision_model"] == "test/model"


# --------------------------------------------------------------------------
# Full chain: Hybrid + CloudVisionExtractor with mocked 429 / 503 / timeout
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [_Resp(429), _Resp(503), requests.Timeout("timed out")],
)
def test_hybrid_chain_cloud_failure_keeps_context(
    source: Path, failure: Any
) -> None:
    post = _Post(failure)
    budget = CloudEscalationBudget(2)
    hybrid = _hybrid(budget, _extractor(post))

    with pytest.raises(CloudVisionExtractorError) as info:
        hybrid(source)

    message = str(info.value)
    assert "LOCAL_AND_CLOUD_VISION_FAILED" in message
    assert TRIGGER in message
    assert "OPENROUTER_VISION_FAILED" in message
    assert len(post.calls) == 1
    assert budget.used == 1


def test_hybrid_chain_budget_blocks_before_http(source: Path) -> None:
    post = _Post(_Resp(200, _ok_body("{}")))
    hybrid = _hybrid(CloudEscalationBudget(0), _extractor(post))

    with pytest.raises(CloudVisionExtractorError, match="BUDGET_EXCEEDED"):
        hybrid(source)
    assert post.calls == []


def test_hybrid_chain_repeated_503_never_exceeds_budget(source: Path) -> None:
    post = _Post(_Resp(503))
    hybrid = _hybrid(CloudEscalationBudget(3), _extractor(post))

    messages = []
    for _ in range(8):
        with pytest.raises(CloudVisionExtractorError) as info:
            hybrid(source)
        messages.append(str(info.value))

    assert len(post.calls) == 3
    assert sum("LOCAL_AND_CLOUD" in m for m in messages) == 3
    assert sum("BUDGET_EXCEEDED" in m for m in messages) == 5


def test_hybrid_chain_recovers_after_transient_failure(
    source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cve, "to_canonical_payload", lambda raw, model: dict(_payload())
    )
    monkeypatch.setattr(cve, "validate_produced_invoice", lambda p, source: p)
    post = _Post(_Resp(429), _Resp(200, _ok_body("{}")))
    hybrid = _hybrid(CloudEscalationBudget(2), _extractor(post))

    with pytest.raises(CloudVisionExtractorError):
        hybrid(source)
    result = hybrid(source)

    assert result["metadata"]["fallback_reason"] == ESCALATION_REASON
    assert len(post.calls) == 2


def test_concurrent_hybrid_chain_http_calls_bounded(source: Path) -> None:
    post = _Post(_Resp(503))
    hybrid = _hybrid(CloudEscalationBudget(4), _extractor(post))

    def work() -> None:
        for _ in range(5):
            with pytest.raises(CloudVisionExtractorError):
                hybrid(source)

    _run_threads(8, work)
    assert len(post.calls) == 4
