"""Fast-path geometry tests for the frozen v1.0.0 contract."""

from __future__ import annotations

import io

import pymupdf
import pytest
from PIL import Image

import services.document_geometry as geometry
from services.document_geometry import (
    normalise_geometry,
    reset_raster_cache,
)


def _rich_text_pdf(tmp_path, *, rotate: int = 0, rotation: int = 0):
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    for row in range(10):
        words = " ".join(f"mot{row}_{column}" for column in range(6))
        page.insert_text((50, 80 + row * 22), words, fontsize=10, rotate=rotate)
    if rotation:
        page.set_rotation(rotation)
    path = tmp_path / f"rich-{rotate}-{rotation}.pdf"
    document.save(path)
    document.close()
    return path


def _scan_pdf(tmp_path):
    buffer = io.BytesIO()
    Image.new("RGB", (400, 600), "white").save(buffer, format="PNG")
    document = pymupdf.open()
    page = document.new_page(width=400, height=600)
    page.insert_image(pymupdf.Rect(20, 20, 380, 580), stream=buffer.getvalue())
    path = tmp_path / "scan.pdf"
    document.save(path)
    document.close()
    return path


def _image_heavy_text_pdf(tmp_path):
    buffer = io.BytesIO()
    Image.new("RGB", (300, 300), "white").save(buffer, format="PNG")
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=buffer.getvalue())
    for row in range(10):
        words = " ".join(f"mot{row}_{column}" for column in range(6))
        page.insert_text((50, 80 + row * 22), words, fontsize=10)
    path = tmp_path / "image-heavy.pdf"
    document.save(path)
    document.close()
    return path


def test_fast_path_skips_rasterization_for_native_text(
    tmp_path, monkeypatch
):
    source = _rich_text_pdf(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("rasterize_page must not be called on fast path")

    monkeypatch.setattr(geometry, "rasterize_page", forbidden)
    report = normalise_geometry(source)

    assert report.fallback is False
    assert report.rotation_degrees == 0
    assert report.rotation_method == "fast_path_native_text"
    assert "geometry_fast_path" in report.notes
    assert report.pages[0]["fast_path"] is True
    assert report.raster_bytes == 0


def test_scan_uses_full_geometry_path(tmp_path):
    report = normalise_geometry(_scan_pdf(tmp_path))

    assert report.fallback is False
    assert "geometry_fast_path" not in report.notes
    assert report.raster_bytes > 0
    assert report.rotation_method != "fast_path_native_text"


def test_non_horizontal_text_uses_full_path(tmp_path):
    report = normalise_geometry(_rich_text_pdf(tmp_path, rotate=90))

    assert report.fallback is False
    assert "geometry_fast_path" not in report.notes
    assert report.rotation_method != "fast_path_native_text"


def test_image_coverage_above_threshold_uses_full_path(tmp_path):
    report = normalise_geometry(_image_heavy_text_pdf(tmp_path))

    assert report.fallback is False
    assert "geometry_fast_path" not in report.notes
    assert report.raster_bytes > 0


def test_non_zero_page_rotation_uses_full_path(tmp_path):
    report = normalise_geometry(_rich_text_pdf(tmp_path, rotation=90))

    assert report.fallback is False
    assert "geometry_fast_path" not in report.notes


def test_fast_path_evaluation_error_returns_fallback(
    tmp_path, monkeypatch
):
    def broken(page):
        raise RuntimeError("fast path evaluation failed")

    monkeypatch.setattr(geometry, "_fast_path_eligible", broken)
    report = normalise_geometry(_rich_text_pdf(tmp_path))

    assert report.fallback is True
    assert report.notes
    assert report.notes[0].startswith("GEOMETRY_FAILED:")


def test_raster_cache_reuses_second_render(tmp_path, monkeypatch):
    reset_raster_cache()
    calls = {"count": 0}
    original = geometry.rasterize_page

    def spy(page, *, dpi=170):
        calls["count"] += 1
        return original(page, dpi=dpi)

    monkeypatch.setattr(geometry, "rasterize_page", spy)
    source = _scan_pdf(tmp_path)

    normalise_geometry(source, mode="full")
    normalise_geometry(source, mode="full")

    assert calls["count"] == 1


def test_light_mode_never_rasterizes(tmp_path, monkeypatch):
    calls = {"count": 0}
    original = geometry.rasterize_page

    def spy(page, *, dpi=170):
        calls["count"] += 1
        return original(page, dpi=dpi)

    monkeypatch.setattr(geometry, "rasterize_page", spy)
    report = normalise_geometry(_scan_pdf(tmp_path), mode="light")

    assert calls["count"] == 0
    assert report.raster_bytes == 0
    assert "geometry_light_mode" in report.notes


def test_deskew_is_skipped_without_consumer(tmp_path):
    report = normalise_geometry(_image_heavy_text_pdf(tmp_path))

    assert report.fallback is False
    assert "geometry_fast_path" not in report.notes
    assert "deskew_skipped_no_consumer" in report.notes
    assert report.deskew_applied is False
