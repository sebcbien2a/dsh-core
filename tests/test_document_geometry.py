"""Tests for the document geometry helpers."""

import pymupdf
import pytest
from PIL import Image

import services.document_geometry as geometry
from services.document_geometry import (
    auto_rotate_page,
    cleanup_image,
    normalise_geometry,
    rotation_candidates,
)


def test_rotation_candidates_returns_four_orientations():
    image = Image.new("RGB", (40, 20), "white")
    candidates = rotation_candidates(image)
    assert [candidate.orientation_degrees for candidate in candidates] == [
        0,
        90,
        180,
        270,
    ]
    assert [candidate.image.size for candidate in candidates] == [
        (40, 20),
        (20, 40),
        (40, 20),
        (20, 40),
    ]


def test_auto_rotate_selects_best_angle_with_injected_scorer():
    document = pymupdf.open()
    page = document.new_page(width=200, height=100)

    def scorer(image, angle):
        return {0: 0.0, 90: 5.0, 180: 0.0, 270: 1.0}[angle]

    result = auto_rotate_page(page, scorer=scorer)
    document.close()

    assert result.angle == 90
    assert result.method == "injected_scorer"
    assert result.confidence > 0.5


def test_auto_rotate_on_synthetic_rotated_pdf_text_layer():
    document = pymupdf.open()
    page = document.new_page(width=300, height=200)
    page.insert_text(
        (50, 100),
        "INVOICE 2026 TOTAL 123.45",
        fontsize=18,
        rotate=90,
    )

    result = auto_rotate_page(page)
    document.close()

    assert result.angle == 90
    assert result.confidence > 0
    assert result.method == "native_text"


def test_cleanup_is_noop_without_opencv(monkeypatch):
    original_import = geometry.importlib.import_module

    def unavailable(name):
        if name == "cv2":
            raise ImportError("cv2 unavailable")
        return original_import(name)

    monkeypatch.setattr(geometry.importlib, "import_module", unavailable)
    image = Image.new("RGB", (16, 16), "white")
    cleaned, notes = cleanup_image(image)

    assert cleaned is image
    assert notes == ["opencv_unavailable"]


def test_cleanup_applies_deskew_with_opencv():
    cv2 = pytest.importorskip("cv2")
    numpy = pytest.importorskip("numpy")

    array = numpy.full((200, 300, 3), 255, dtype=numpy.uint8)
    cv2.rectangle(array, (80, 90), (220, 110), (0, 0, 0), -1)
    matrix = cv2.getRotationMatrix2D((150, 100), 10, 1.0)
    rotated = cv2.warpAffine(
        array, matrix, (300, 200), borderValue=(255, 255, 255)
    )

    cleaned, notes = cleanup_image(Image.fromarray(rotated), deskew=True)

    assert isinstance(cleaned, Image.Image)
    assert cleaned.size == (300, 200)
    assert "deskew_applied" in notes


def test_geometry_failure_returns_fallback_report(tmp_path):
    report = normalise_geometry(tmp_path / "missing.pdf")

    assert report.fallback is True
    assert report.error_code == "FILE_NOT_FOUND"
    assert report.notes == ["GEOMETRY_FAILED:FILE_NOT_FOUND"]
