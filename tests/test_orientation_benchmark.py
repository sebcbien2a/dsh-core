import json
import hashlib
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from benchmarks import orientation_benchmark as benchmark

MANIFEST = Path(__file__).parent / "fixtures" / "orientation_corpus_manifest.json"


@pytest.fixture
def synthetic_pdf(tmp_path):
    path = tmp_path / "synthetic.pdf"
    document = pymupdf.open()
    page = document.new_page(width=72, height=144)
    page.draw_rect(pymupdf.Rect(12, 24, 36, 60))
    document.save(path)
    document.close()
    return path


def test_loads_ten_manifest_entries():
    assert len(benchmark.load_manifest(MANIFEST)) == 10


def test_rejects_invalid_orientation(tmp_path):
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    data["entries"][0]["expected_orientation_degrees"] = 45
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="orientation"):
        benchmark.load_manifest(path)


def test_rejects_incorrect_source_hash(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"synthetic")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        benchmark.verify_source(source, "0" * 64)


def test_generates_all_rotations_without_changing_image():
    image = Image.new("RGB", (5, 3), "white")
    before = image.tobytes()
    candidates = benchmark.generate_rotation_candidates(image)
    assert [candidate.orientation_degrees for candidate in candidates] == [0, 90, 180, 270]
    assert [candidate.image.size for candidate in candidates] == [
        (5, 3), (3, 5), (5, 3), (3, 5)
    ]
    assert image.size == (5, 3) and image.tobytes() == before


def test_metadata_baseline_can_abstain():
    prediction = benchmark.score_metadata_baseline({
        "media_type": "application/pdf", "pdf_declared_rotation": 0,
        "first_page_width": 595, "first_page_height": 842,
    })
    assert prediction.predicted_orientation is None
    assert prediction.confidence == "ABSTAIN"


def test_summary_counts_success_error_and_abstention():
    records = [
        benchmark.OrientationBenchmarkRecord("a.pdf", 0, 0, True, "HIGH", "test"),
        benchmark.OrientationBenchmarkRecord("b.pdf", 90, 0, False, "LOW", "test"),
        benchmark.OrientationBenchmarkRecord("c.pdf", 180, None, None, "ABSTAIN", "test"),
    ]
    summary = benchmark.build_summary(records)
    assert (summary.successes, summary.errors, summary.abstentions) == (1, 1, 1)
    assert summary.accuracy == 0.5


def test_report_contains_no_absolute_paths_or_document_content(tmp_path):
    record = benchmark.OrientationBenchmarkRecord(
        "sample.pdf", 0, None, None, "ABSTAIN", "metadata_only"
    )
    output = tmp_path / "nested" / "report.json"
    benchmark.write_report(output, benchmark.build_summary([record]))
    text = output.read_text(encoding="utf-8")
    report = json.loads(text)
    assert str(tmp_path) not in text and "C:\\" not in text
    assert not ({"raw_text", "document_text", "supplier", "amount", "address"} &
                set(report["records"][0]))
    assert report["pixel_inspection"] is False


def test_pdf_renderer_unavailable_is_explicit(monkeypatch, tmp_path):
    original_import = benchmark.importlib.import_module

    def unavailable(name):
        if name == "pypdfium2":
            raise ImportError("unavailable")
        return original_import(name)

    monkeypatch.setattr(benchmark.importlib, "import_module", unavailable)
    with pytest.raises(RuntimeError, match="PDF_RENDERER_UNAVAILABLE"):
        benchmark.render_pdf_first_page(tmp_path / "unused.pdf")


@pytest.mark.parametrize(
    ("dpi", "expected_size"),
    [(72, (72, 144)), (144, (144, 288)), (170, (170, 340))],
)
def test_pdf_renders_in_memory_at_fixed_dpi(
    synthetic_pdf, dpi, expected_size, tmp_path
):
    source_hash = hashlib.sha256(synthetic_pdf.read_bytes()).hexdigest()
    image = benchmark.render_pdf_first_page(synthetic_pdf, dpi=dpi)
    assert abs(image.width - expected_size[0]) <= 1
    assert abs(image.height - expected_size[1]) <= 1
    assert list(tmp_path.iterdir()) == [synthetic_pdf]
    assert hashlib.sha256(synthetic_pdf.read_bytes()).hexdigest() == source_hash


def test_default_pdf_render_generates_four_in_memory_rotations(synthetic_pdf):
    image = benchmark.render_pdf_first_page(synthetic_pdf)
    before = image.tobytes()
    candidates = benchmark.generate_rotation_candidates(image)
    assert [candidate.orientation_degrees for candidate in candidates] == [
        0, 90, 180, 270
    ]
    assert abs(candidates[0].image.width - image.width) <= 1
    assert abs(candidates[0].image.height - image.height) <= 1
    for candidate in (candidates[1], candidates[3]):
        assert abs(candidate.image.width - image.height) <= 1
        assert abs(candidate.image.height - image.width) <= 1
    assert image.tobytes() == before


@pytest.mark.parametrize("dpi", [0, -1])
def test_pdf_render_rejects_non_positive_dpi(synthetic_pdf, dpi):
    with pytest.raises(ValueError, match="dpi must be a positive integer"):
        benchmark.render_pdf_first_page(synthetic_pdf, dpi=dpi)


def test_pdf_render_rejects_empty_document(monkeypatch, tmp_path):
    class EmptyDocument:
        closed = False

        def __len__(self):
            return 0

        def close(self):
            self.closed = True

    document = EmptyDocument()
    monkeypatch.setattr(
        benchmark.importlib,
        "import_module",
        lambda name: type("Pdfium", (), {"PdfDocument": lambda _path: document}),
    )
    with pytest.raises(ValueError, match="PDF_HAS_NO_PAGES"):
        benchmark.render_pdf_first_page(tmp_path / "empty.pdf")
    assert document.closed


def test_failed_source_validation_does_not_write_report(tmp_path):
    entries = benchmark.load_manifest(MANIFEST)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    source = corpus / entries[0]["relative_path"]
    source.parent.mkdir(parents=True)
    source.write_bytes(b"incorrect")
    report = tmp_path / "result.json"
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        benchmark.run_metadata_baseline(MANIFEST, corpus, report)
    assert not report.exists()
