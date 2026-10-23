"""Metadata-only orientation baseline and in-memory rotation utilities."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from PIL import Image

ORIENTATIONS = (0, 90, 180, 270)
CONFIDENCES = ("HIGH", "MEDIUM", "LOW", "ABSTAIN")
REQUIRED_FIELDS = {
    "file_name", "relative_path", "media_type", "page_count",
    "first_page_width", "first_page_height", "pdf_declared_rotation",
    "has_native_text", "sha256", "expected_orientation_degrees",
    "source_group", "notes",
}


@dataclass(frozen=True)
class OrientationCandidate:
    orientation_degrees: int
    image: Image.Image


@dataclass(frozen=True)
class OrientationPrediction:
    predicted_orientation: int | None
    confidence: str
    reason: str


@dataclass(frozen=True)
class OrientationBenchmarkRecord:
    file_name: str
    expected_orientation: int
    predicted_orientation: int | None
    is_correct: bool | None
    confidence: str
    reason: str


@dataclass(frozen=True)
class OrientationBenchmarkSummary:
    total: int
    successes: int
    errors: int
    abstentions: int
    accuracy: float | None
    records: tuple[OrientationBenchmarkRecord, ...]


def load_manifest(path: str | Path) -> list[dict[str, Any]]:
    try:
        entries = json.loads(Path(path).read_text(encoding="utf-8"))["entries"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("Invalid orientation manifest") from exc
    if not isinstance(entries, list) or len(entries) != 10:
        raise ValueError("Orientation manifest must contain exactly 10 entries")
    names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != REQUIRED_FIELDS:
            raise ValueError("Invalid orientation manifest entry fields")
        relpath = Path(entry["relative_path"])
        if relpath.is_absolute() or ".." in relpath.parts:
            raise ValueError("Manifest paths must be relative and stay in the corpus")
        if entry["file_name"] in names:
            raise ValueError("Manifest file names must be unique")
        names.add(entry["file_name"])
        if entry["expected_orientation_degrees"] not in ORIENTATIONS:
            raise ValueError("Invalid expected orientation")
        rotation = entry["pdf_declared_rotation"]
        if rotation is not None and rotation not in ORIENTATIONS:
            raise ValueError("Invalid declared PDF rotation")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", entry["sha256"]):
            raise ValueError("Invalid SHA-256 hash")
    return entries


def verify_source(path: str | Path, expected_sha256: str) -> bool:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise ValueError("Invalid expected SHA-256 hash")
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest().lower() != expected_sha256.lower():
        raise ValueError("Source SHA-256 mismatch")
    return True


def generate_rotation_candidates(image: Image.Image) -> tuple[OrientationCandidate, ...]:
    return tuple(
        OrientationCandidate(angle, image.rotate(angle, expand=True))
        for angle in ORIENTATIONS
    )


def render_pdf_first_page(path: str | Path, dpi: int = 170) -> Image.Image:
    if type(dpi) is not int or dpi <= 0:
        raise ValueError("dpi must be a positive integer")
    try:
        pdfium = importlib.import_module("pypdfium2")
    except ImportError as exc:
        raise RuntimeError("PDF_RENDERER_UNAVAILABLE") from exc
    with ExitStack() as stack:
        document = pdfium.PdfDocument(str(path))
        stack.callback(document.close)
        if len(document) == 0:
            raise ValueError("PDF_HAS_NO_PAGES")
        page = document[0]
        stack.callback(page.close)
        bitmap = page.render(scale=dpi / 72)
        stack.callback(bitmap.close)
        return bitmap.to_pil().copy()


def score_metadata_baseline(entry: dict[str, Any]) -> OrientationPrediction:
    rotation = entry.get("pdf_declared_rotation")
    if entry.get("media_type") == "application/pdf" and rotation in ORIENTATIONS:
        if rotation:
            return OrientationPrediction(
                rotation, "MEDIUM", "declared_pdf_rotation_only_pixels_not_inspected"
            )
        return OrientationPrediction(
            None, "ABSTAIN", "zero_pdf_rotation_does_not_establish_visual_orientation"
        )
    return OrientationPrediction(
        None, "ABSTAIN", "metadata_geometry_cannot_establish_visual_orientation"
    )


def evaluate_prediction(expected: int, predicted: int | None) -> bool | None:
    if expected not in ORIENTATIONS or (
        predicted is not None and predicted not in ORIENTATIONS
    ):
        raise ValueError("Invalid orientation")
    return None if predicted is None else expected == predicted


def build_summary(records: list[OrientationBenchmarkRecord]) -> OrientationBenchmarkSummary:
    successes = sum(record.is_correct is True for record in records)
    errors = sum(record.is_correct is False for record in records)
    abstentions = sum(record.is_correct is None for record in records)
    decided = successes + errors
    return OrientationBenchmarkSummary(
        len(records), successes, errors, abstentions,
        successes / decided if decided else None, tuple(records),
    )


def write_report(path: str | Path, summary: OrientationBenchmarkSummary) -> None:
    payload = {
        "variant": "A_metadata_geometry_only",
        "pixel_inspection": False,
        "ocr_or_model_invoked": False,
        "summary": {
            "total": summary.total, "successes": summary.successes,
            "errors": summary.errors, "abstentions": summary.abstentions,
            "accuracy": summary.accuracy,
        },
        "records": [asdict(record) for record in summary.records],
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run_metadata_baseline(manifest: Path, corpus_root: Path, report: Path) -> OrientationBenchmarkSummary:
    entries = load_manifest(manifest)
    sources = []
    for entry in entries:
        relative = Path(entry["relative_path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Manifest paths must stay in the corpus")
        source = corpus_root / relative
        verify_source(source, entry["sha256"])
        sources.append(source)
    records = []
    for entry in entries:
        prediction = score_metadata_baseline(entry)
        records.append(OrientationBenchmarkRecord(
            entry["file_name"], entry["expected_orientation_degrees"],
            prediction.predicted_orientation,
            evaluate_prediction(entry["expected_orientation_degrees"],
                                prediction.predicted_orientation),
            prediction.confidence, prediction.reason,
        ))
    summary = build_summary(records)
    write_report(report, summary)
    return summary


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=root / "tests/fixtures/orientation_corpus_manifest.json")
    parser.add_argument("--corpus-root", type=Path, default=root)
    parser.add_argument("--output", type=Path, default=root / "benchmarks/results/orientation_metadata_baseline.json")
    args = parser.parse_args()
    summary = run_metadata_baseline(args.manifest, args.corpus_root, args.output)
    print(f"Metadata baseline complete: {summary.total} entries, {summary.abstentions} abstentions")


if __name__ == "__main__":
    main()
