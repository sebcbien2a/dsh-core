"""Graph-level tests for the Phase 6 perception rebranching."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

import services.document_pipeline as pipeline
from backend.app.graphs.invoice_graph import run_invoice_graph
from schemas.invoice_schema import InvoiceSchema
from services.cloud_vision_extractor import (
    CloudEscalationBudget,
    budget_max,
)
from services.document_engine import LocalDocumentEngine


def _invoice_pdf(tmp_path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    lines = [
        "ACME SAS",
        "12 rue des Lilas",
        "75001 Paris",
        "SIRET: 123 456 789 00012",
        "Numero TVA: FR12345678901",
        "Facture N: F-2026-001",
        "Date: 14/09/2026",
        "Net HT 100,00 20,00 120,00",
    ]
    for index, line in enumerate(lines):
        page.insert_text((50, 80 + index * 20), line, fontsize=11)
    path = tmp_path / "invoice.pdf"
    document.save(path)
    document.close()
    return path


def _annotated_pdf(tmp_path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    lines = [
        "FACTURE N FA-2026-001",
        "ACME SAS",
        "12 rue des Lilas",
        "75001 Paris",
        "SIRET: 123 456 789 00012",
        "Net HT 100,00 20,00 120,00",
    ]
    for index, line in enumerate(lines):
        page.insert_text((50, 80 + index * 20), line, fontsize=11)
    page.add_rect_annot(pymupdf.Rect(45, 65, 300, 100))
    path = tmp_path / "annotated.pdf"
    document.save(path)
    document.close()
    return path


def _rotated_annotated_pdf(tmp_path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page(width=500, height=800)
    page.insert_text(
        (100, 700),
        "FACTURE N FA-2026-001 TOTAL HT 100,00 TVA 20,00 TTC 120,00 "
        "FOURNISSEUR ACME",
        fontsize=12,
        rotate=90,
    )
    page.add_ink_annot([[(80, 600), (110, 650), (80, 700)]])
    path = tmp_path / "rotated.pdf"
    document.save(path)
    document.close()
    return path


@pytest.fixture
def local_pipeline(monkeypatch):
    monkeypatch.setenv("DOCUMENT_PIPELINE", "on")
    monkeypatch.setenv("DOCUMENT_ENGINE", "local")
    pipeline.reset_default_engine()
    pipeline.reset_perception_cache()
    pipeline.set_default_engine(LocalDocumentEngine())
    yield
    pipeline.reset_default_engine()
    pipeline.reset_perception_cache()


def test_pipeline_on_native_pdf_fills_routing(local_pipeline, tmp_path):
    state = run_invoice_graph(
        _invoice_pdf(tmp_path),
        persistence_manager=None,
        thread_id="p6-routing",
    )

    routing = state.get("routing")
    assert isinstance(routing, dict)
    assert routing["route"] == "native_text"
    assert routing["business_family"] == "invoice"
    assert routing["profile_type"] == "native_pdf"


def test_pipeline_on_unknown_suffix_preserves_rejection(local_pipeline, tmp_path):
    source = tmp_path / "mystery.xyz"
    source.write_text("unsupported payload", encoding="utf-8")

    state = run_invoice_graph(
        source,
        persistence_manager=None,
        thread_id="p6-unknown",
    )

    assert state["file_type"] == "unknown"
    assert state["commit_status"] == ""
    assert state.get("routing") is None
    assert state.get("perception") is None


def test_pipeline_on_valid_pdf_stores_perception(local_pipeline, tmp_path):
    state = run_invoice_graph(
        _invoice_pdf(tmp_path),
        persistence_manager=None,
        thread_id="p6-perception",
    )

    perception = state.get("perception")
    assert isinstance(perception, dict)
    assert perception["pages"]
    assert perception["tools_used"]
    assert perception["raw_text"].strip()


def test_pipeline_off_disables_routing_and_perception(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCUMENT_PIPELINE", "off")

    state = run_invoice_graph(
        _invoice_pdf(tmp_path),
        persistence_manager=None,
        thread_id="p6-off",
    )

    assert state.get("routing") is None
    assert state.get("perception") is None
    invoice = state.get("invoice")
    assert isinstance(invoice, InvoiceSchema)
    assert not any(
        key.startswith("perception_") for key in invoice.metadata
    )


def test_corrupt_pdf_completes_with_perception_failure(local_pipeline, tmp_path):
    source = tmp_path / "corrupt.pdf"
    source.write_bytes(b"not a pdf at all")

    state = run_invoice_graph(
        source,
        persistence_manager=None,
        thread_id="p6-corrupt",
    )

    perception = state.get("perception")
    assert isinstance(perception, dict)
    notes = perception.get("processing_notes", [])
    assert any(str(note).startswith("PERCEPTION_FAILED:") for note in notes)


def test_rotated_annotated_pdf_uses_upright_frame(local_pipeline, tmp_path):
    state = run_invoice_graph(
        _rotated_annotated_pdf(tmp_path),
        persistence_manager=None,
        thread_id="p6-rotated",
    )

    perception = state.get("perception")
    assert isinstance(perception, dict)
    page = perception["pages"][0]
    assert page["rotation_applied"] == 90
    assert page["width"] == 800.0
    assert page["height"] == 500.0
    assert page["annotations"]
    assert page["blocks"]


def test_annotated_raw_text_has_no_duplicate_lines(local_pipeline, tmp_path):
    state = run_invoice_graph(
        _annotated_pdf(tmp_path),
        persistence_manager=None,
        thread_id="p6-duplicates",
    )

    perception = state.get("perception")
    assert isinstance(perception, dict)
    lines = [
        line for line in perception["raw_text"].splitlines() if line.strip()
    ]
    assert len(lines) == len(set(lines))
    blocks = [
        block
        for page in perception["pages"]
        for block in page["blocks"]
    ]
    keys = {
        (
            block["page_index"],
            block["kind"],
            tuple(block["bbox"].values()),
            block["text"],
        )
        for block in blocks
    }
    assert len(keys) == len(blocks)


def test_escalation_budget_requires_integer_max():
    with pytest.raises(ValueError, match="BUDGET_MAX_MUST_BE_INT"):
        CloudEscalationBudget(0.5)

    assert budget_max(0.05, 6) == 1
    assert budget_max(0.5, 6) == 3
    assert budget_max(0.05, 0) == 0
