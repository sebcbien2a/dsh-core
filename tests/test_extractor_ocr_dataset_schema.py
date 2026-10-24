import json
import re
from pathlib import Path
from typing import Any

import pytest

from benchmarks.extractor_benchmark_runner import FIELDS, _normalize, load_ocr_dataset

EXAMPLE_PATH = (
    Path(__file__).resolve().parents[1]
    / "benchmarks"
    / "data"
    / "ocr_baseline_texts.example.json"
)
EXPECTED_FIELDS = {"fournisseur", "date", "ht", "tva", "ttc", "siret"}
PREDICTION_KEYS = {"prediction", "predictions", "extractors", "model_output"}
SECRET_PATTERN = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{8,}|api[_-]?key|secret|password|token)",
    re.IGNORECASE,
)
WINDOWS_PATH_PATTERN = re.compile(r"[A-Za-z]:[\\/]")
POSIX_PATH_PATTERN = re.compile(r"(?<![\w:])/(?:[\w.-]+/)+[\w.-]*")


@pytest.fixture
def dataset() -> dict[str, Any]:
    return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


def _all_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join([*(str(key) for key in value), *(_all_text(v) for v in value.values())])
    if isinstance(value, list):
        return " ".join(_all_text(item) for item in value)
    return "" if value is None else str(value)


def test_example_is_valid_json_with_version_and_exactly_two_cases(
    dataset: dict[str, Any],
) -> None:
    assert dataset["dataset_version"]
    assert dataset["description"]
    assert dataset["fields"] == list(FIELDS)
    assert len(dataset["cases"]) == 2


def test_example_case_ids_are_unique_and_ocr_text_is_nonempty(
    dataset: dict[str, Any],
) -> None:
    cases = dataset["cases"]

    assert len({case["id"] for case in cases}) == len(cases)
    assert all(case["id"].startswith("synthetic-") for case in cases)
    assert all(case["ocr_text"].strip() for case in cases)


def test_every_case_has_six_ground_truth_fields_and_required_metadata(
    dataset: dict[str, Any],
) -> None:
    for case in dataset["cases"]:
        assert set(case["ground_truth"]) == EXPECTED_FIELDS
        assert set(case["metadata"]) == {
            "source_type",
            "language",
            "reviewed_by_human",
            "notes",
        }
        assert case["metadata"]["source_type"] == "synthetic_text"
        assert isinstance(case["metadata"]["reviewed_by_human"], bool)
        assert case["metadata"]["notes"]


def test_null_ground_truth_is_explicit_and_supported(dataset: dict[str, Any]) -> None:
    assert all(
        value is None or isinstance(value, str)
        for case in dataset["cases"]
        for value in case["ground_truth"].values()
    )
    assert any(
        value is None
        for case in dataset["cases"]
        for value in case["ground_truth"].values()
    )


def test_ground_truth_dates_follow_runner_formats(dataset: dict[str, Any]) -> None:
    for case in dataset["cases"]:
        value = case["ground_truth"]["date"]
        assert value is None or _normalize("date", value) is not None


def test_ground_truth_money_is_compatible_with_french_normalization(
    dataset: dict[str, Any],
) -> None:
    for case in dataset["cases"]:
        for field in ("ht", "tva", "ttc"):
            value = case["ground_truth"][field]
            assert value is None or _normalize(field, value) is not None


def test_siret_values_are_synthetic_fourteen_digit_strings_or_null(
    dataset: dict[str, Any],
) -> None:
    for case in dataset["cases"]:
        siret = case["ground_truth"]["siret"]
        assert siret is None or re.fullmatch(r"\d{14}", siret)


def test_example_contains_no_absolute_paths_secrets_or_predictions(
    dataset: dict[str, Any],
) -> None:
    serialized = json.dumps(dataset, ensure_ascii=False)

    assert not WINDOWS_PATH_PATTERN.search(serialized)
    assert not POSIX_PATH_PATTERN.search(serialized)
    assert not SECRET_PATTERN.search(serialized)
    assert not (PREDICTION_KEYS & {str(key).lower() for key in dataset})
    assert all(
        not (PREDICTION_KEYS & {str(key).lower() for key in case})
        for case in dataset["cases"]
    )


def test_example_loads_through_runner_dataset_loader() -> None:
    version, cases = load_ocr_dataset(EXAMPLE_PATH)

    assert version == "synthetic-example-v1"
    assert len(cases) == 2
    assert all(case.ocr_text for case in cases)
    assert all(set(case.ground_truth) == EXPECTED_FIELDS for case in cases)
