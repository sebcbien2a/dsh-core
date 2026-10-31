"""Behavioural and contract tests for the Phase 2 document router."""

from __future__ import annotations

import io
from pathlib import Path
from typing import get_args

import pymupdf
import pytest
from PIL import Image

from schemas.document_perception import RouteKind, TaskKind
from services.document_router import (
    ALLOWED_TOOLS,
    DocumentRouter,
    reset_route_cache,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_INVOICE = REPO_ROOT / "workspace" / "invoices" / "input" / "facture_emaux.pdf"


def _write_lines(page: pymupdf.Page, lines: list[str], step: float = 22) -> None:
    for index, line in enumerate(lines):
        page.insert_text((50, 80 + index * step), line, fontsize=11)


def _make_invoice(tmp_path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    _write_lines(
        page,
        [
            "FACTURE N° FA-2026-001",
            "Fournisseur: ACME SAS",
            "SIRET: 12345678900012",
            "Date: 30/10/2026",
            "Designation: Prestation de service",
            "Total HT: 100.00 EUR",
            "TVA 20%: 20.00 EUR",
            "Total TTC: 120.00 EUR",
            "Merci de votre confiance",
        ],
    )
    path = tmp_path / "invoice.pdf"
    document.save(path)
    document.close()
    return path


def _make_scan(tmp_path: Path) -> Path:
    buffer = io.BytesIO()
    Image.new("RGB", (300, 400), "white").save(buffer, format="PNG")
    document = pymupdf.open()
    page = document.new_page(width=300, height=400)
    page.insert_image(pymupdf.Rect(20, 20, 280, 380), stream=buffer.getvalue())
    path = tmp_path / "scan.pdf"
    document.save(path)
    document.close()
    return path


def _make_dense_tables(tmp_path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page(width=500, height=500)
    for table_index in range(2):
        x0 = 30
        y0 = 30 + table_index * 200
        x1 = 470
        y1 = y0 + 150
        for row in range(4):
            page.draw_line((x0, y0 + row * 50), (x1, y0 + row * 50))
        for column in range(4):
            page.draw_line(
                (x0 + column * 110, y0), (x0 + column * 110, y1)
            )
        for row in range(3):
            for column in range(3):
                page.insert_text(
                    (x0 + column * 110 + 6, y0 + row * 50 + 22),
                    f"data{row}{column}",
                    fontsize=9,
                )
    path = tmp_path / "tables.pdf"
    document.save(path)
    document.close()
    return path


def _make_receipt(tmp_path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page(width=200, height=500)
    _write_lines(
        page,
        [
            "TICKET DE CAISSE",
            "TOTAL 12,50 EUR",
            "TVA 2,08 EUR",
            "Merci de votre visite",
            "Bonne journee",
            "A bientot",
        ],
    )
    path = tmp_path / "receipt.pdf"
    document.save(path)
    document.close()
    return path


def _make_annotated(tmp_path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    _write_lines(
        page,
        [
            "COMPTE RENDU DE REUNION",
            "Ce document contient des annotations",
            "Validation effectuee le 30 octobre 2026",
            "Signature du responsable",
            "Prochaine etape le 15 novembre 2026",
        ],
    )
    page.add_rect_annot(pymupdf.Rect(50, 100, 220, 160))
    path = tmp_path / "annotated.pdf"
    document.save(path)
    document.close()
    return path


def _make_ledger_csv(tmp_path: Path) -> Path:
    path = tmp_path / "ledger.csv"
    path.write_text(
        "date;libelle;montant;compte\n"
        "2026-01-01;Achat bureau;120.00;401\n"
        "2026-01-02;Fournitures;80.00;401\n",
        encoding="utf-8",
    )
    return path


def _make_bank_csv(tmp_path: Path) -> Path:
    path = tmp_path / "bank.csv"
    path.write_text(
        "date;libelle;debit;credit;solde;iban\n"
        "2026-01-01;Virement;100,00;;1000,00;"
        "FR7612345678901234567890123\n"
        "2026-01-02;Remise cheque;;200,00;1200,00;"
        "FR7612345678901234567890123\n",
        encoding="utf-8",
    )
    return path


def _make_ofx(tmp_path: Path) -> Path:
    path = tmp_path / "statement.ofx"
    path.write_text(
        "OFXHEADER:100\n"
        "DATA:OFXSGML\n\n"
        "<OFX><BANKMSGSRSV1><STMTTRNRS><TRNUID>1</TRNUID>"
        "</STMTTRNRS></BANKMSGSRSV1></OFX>\n",
        encoding="utf-8",
    )
    return path


def _make_invalid_files(tmp_path: Path) -> tuple[Path, Path]:
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"%PDF-1.4 broken document")
    unknown = tmp_path / "mystery.xyz"
    unknown.write_text("unknown payload", encoding="utf-8")
    return corrupt, unknown


@pytest.fixture
def documents(tmp_path: Path) -> dict[str, Path]:
    corrupt, unknown = _make_invalid_files(tmp_path)
    return {
        "invoice": _make_invoice(tmp_path),
        "scan": _make_scan(tmp_path),
        "tables": _make_dense_tables(tmp_path),
        "ledger": _make_ledger_csv(tmp_path),
        "bank": _make_bank_csv(tmp_path),
        "ofx": _make_ofx(tmp_path),
        "receipt": _make_receipt(tmp_path),
        "annotated": _make_annotated(tmp_path),
        "corrupt": corrupt,
        "unknown": unknown,
    }


def test_native_invoice_routes_to_native_text(documents):
    verdict = DocumentRouter().route(documents["invoice"])

    assert verdict.profile.profile_type == "native_pdf"
    assert verdict.profile.business_family == "invoice"
    assert verdict.decision.route == "native_text"
    assert [(task.kind, task.tool) for task in verdict.plan.tasks] == [
        ("text", "pymupdf_text")
    ]
    assert verdict.decision.confidence > 0
    assert any(reason.startswith("text_layer:") for reason in verdict.decision.reasons)


def test_scanned_pdf_abstains_without_scorer(documents):
    verdict = DocumentRouter().route(documents["scan"])

    assert verdict.profile.profile_type == "scanned_pdf"
    assert verdict.profile.business_family in {"unknown", "invoice"}
    assert verdict.decision.route == "local_ocr"
    assert verdict.profile.orientation_degrees == 0
    assert "orientation:abstain:no_native_text" in verdict.decision.reasons
    assert [task.tool for task in verdict.plan.tasks] == [
        "paddleocr",
        "vision_text",
    ]


def test_scan_uses_injected_ocr_scorer_for_orientation(documents):
    def scorer(image, angle):
        return {0: 0.0, 90: 10.0, 180: 0.0, 270: 0.0}[angle]

    verdict = DocumentRouter(ocr_scorer=scorer).route(documents["scan"])

    assert verdict.profile.orientation_degrees == 90
    assert verdict.decision.route == "local_ocr"
    assert any("rotation:90cw" in reason for reason in verdict.decision.reasons)
    assert any("injected_scorer" in reason for reason in verdict.decision.reasons)
    assert verdict.geometry is not None
    assert verdict.geometry.rotation_degrees == 90


def test_dense_tables_route_to_docling(documents):
    verdict = DocumentRouter().route(documents["tables"])

    assert verdict.profile.profile_type == "complex_pdf"
    assert verdict.profile.business_family == "complex_table"
    assert verdict.decision.route == "docling"
    assert [(task.kind, task.tool) for task in verdict.plan.tasks] == [
        ("table", "find_tables"),
        ("table", "docling"),
        ("text", "pymupdf_text"),
    ]
    assert any(reason.startswith("tables:") for reason in verdict.decision.reasons)


def test_csv_ledger_routes_to_spreadsheet(documents):
    verdict = DocumentRouter().route(documents["ledger"])

    assert verdict.profile.profile_type == "spreadsheet"
    assert verdict.profile.business_family == "ledger"
    assert verdict.decision.route == "spreadsheet"
    assert [(task.kind, task.tool) for task in verdict.plan.tasks] == [
        ("table", "pandas")
    ]


def test_xlsx_ledger_routes_to_spreadsheet(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["date", "libelle", "montant"])
    sheet.append(["2026-01-01", "Achat bureau", 120.0])
    path = tmp_path / "ledger.xlsx"
    workbook.save(path)

    verdict = DocumentRouter().route(path)

    assert verdict.profile.profile_type == "spreadsheet"
    assert verdict.profile.business_family == "ledger"
    assert verdict.decision.route == "spreadsheet"
    assert [task.tool for task in verdict.plan.tasks] == ["pandas"]


def test_bank_csv_takes_priority_over_ledger(documents):
    verdict = DocumentRouter().route(documents["bank"])

    assert verdict.profile.business_family == "bank_statement"
    assert verdict.decision.route == "spreadsheet"
    assert any(
        reason.startswith("bank_statement_score:")
        for reason in verdict.decision.reasons
    )
    assert any(
        reason.startswith("ledger_score:") for reason in verdict.decision.reasons
    )


def test_ofx_minimal_routes_to_spreadsheet(documents):
    verdict = DocumentRouter().route(documents["ofx"])

    assert verdict.profile.profile_type == "bank_statement"
    assert verdict.profile.business_family == "bank_statement"
    assert verdict.decision.route == "spreadsheet"
    assert any(
        reason.startswith("bank_suffix:") for reason in verdict.decision.reasons
    )


def test_thermal_receipt_is_detected(documents):
    verdict = DocumentRouter().route(documents["receipt"])

    assert verdict.profile.profile_type in {"native_pdf", "scanned_pdf"}
    assert verdict.profile.business_family == "receipt"
    assert verdict.decision.route in {"native_text", "local_ocr"}
    assert any(
        reason.startswith("receipt_score:") for reason in verdict.decision.reasons
    )


def test_annotated_pdf_plan_includes_annotation_task(documents):
    verdict = DocumentRouter().route(documents["annotated"])

    assert verdict.profile.profile_type == "native_pdf"
    assert verdict.profile.business_family == "annotated"
    assert verdict.decision.route == "native_text"
    assert [(task.kind, task.tool) for task in verdict.plan.tasks] == [
        ("text", "pymupdf_text"),
        ("annotation", "pymupdf_text"),
    ]
    assert any(
        reason.startswith("annotations:") for reason in verdict.decision.reasons
    )


@pytest.mark.parametrize("key", ["corrupt", "unknown"])
def test_invalid_inputs_never_raise(documents, key):
    verdict = DocumentRouter().route(documents[key])

    assert verdict.decision.route in {"unsupported", "manual_review"}
    assert verdict.decision.reasons
    assert verdict.plan.tasks


@pytest.mark.skipif(
    not REAL_INVOICE.exists(), reason="real invoice fixture is absent"
)
def test_real_invoice_smoke():
    verdict = DocumentRouter().route(REAL_INVOICE)

    assert verdict.profile.business_family == "invoice"
    assert verdict.decision.route == "native_text"
    assert verdict.profile.profile_type in {"native_pdf", "complex_pdf"}
    assert "pymupdf_text" in {task.tool for task in verdict.plan.tasks}


def test_plan_tools_are_within_allowed_extractor_names(documents):
    router = DocumentRouter()
    for path in documents.values():
        verdict = router.route(path)
        assert {task.tool for task in verdict.plan.tasks} <= ALLOWED_TOOLS
        assert set(verdict.decision.tools) <= ALLOWED_TOOLS


def test_decision_route_and_task_kinds_use_delivered_literals(documents):
    route_kinds = set(get_args(RouteKind))
    task_kinds = set(get_args(TaskKind))
    router = DocumentRouter()
    for path in documents.values():
        verdict = router.route(path)
        assert verdict.decision.route in route_kinds
        for task in verdict.plan.tasks:
            assert task.kind in task_kinds


def test_router_never_raises_on_any_input(documents, tmp_path):
    inputs = list(documents.values()) + [
        tmp_path / "missing.pdf",
        tmp_path,
        None,
    ]
    router = DocumentRouter()
    route_kinds = set(get_args(RouteKind))
    for path in inputs:
        verdict = router.route(path)
        assert verdict.decision.route in route_kinds


def test_reasons_trace_all_signals(documents):
    router = DocumentRouter()
    invoice = router.route(documents["invoice"])
    bank = router.route(documents["bank"])
    annotated = router.route(documents["annotated"])
    tables = router.route(documents["tables"])

    assert any(reason.startswith("text_layer:") for reason in invoice.decision.reasons)
    assert any(reason.startswith("invoice_score:") for reason in invoice.decision.reasons)
    assert any(
        reason.startswith("bank_statement_score:") for reason in bank.decision.reasons
    )
    assert any(reason.startswith("ledger_score:") for reason in bank.decision.reasons)
    assert any(
        reason.startswith("annotations:") for reason in annotated.decision.reasons
    )
    assert any(
        reason.startswith("annotated_score:") for reason in annotated.decision.reasons
    )
    assert any(reason.startswith("tables:") for reason in tables.decision.reasons)
    assert any(
        reason.startswith("complex_table_score:")
        for reason in tables.decision.reasons
    )


def _strong_invoice_pdf(tmp_path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    lines = ["ACME SAS", "Facture N: F-2026-001", "Date: 14/09/2026"]
    lines.extend(f"Ligne de detail numero {index} sans ambiguite" for index in range(20))
    lines.append("Total HT 100,00 TVA 20,00 TTC 120,00")
    for index, line in enumerate(lines):
        page.insert_text((50, 80 + (index % 30) * 20), line, fontsize=10)
    path = tmp_path / "strong-invoice.pdf"
    document.save(path)
    document.close()
    return path


def _multipage_table_pdf(tmp_path: Path) -> Path:
    document = pymupdf.open()
    for page_number in range(4):
        page = document.new_page(width=400, height=400)
        for row in range(3):
            page.draw_line((40, 100 + row * 50), (360, 100 + row * 50))
        for column in range(3):
            page.draw_line(
                (40 + column * 160, 100), (40 + column * 160, 200)
            )
        page.insert_text((45, 130), f"cell {page_number}", fontsize=10)
    path = tmp_path / "multipage-tables.pdf"
    document.save(path)
    document.close()
    return path


def test_route_cache_computes_once(documents, monkeypatch):
    reset_route_cache()
    calls = {"count": 0}
    original = pymupdf.Page.find_tables

    def spy(page):
        calls["count"] += 1
        return original(page)

    monkeypatch.setattr(pymupdf.Page, "find_tables", spy)
    router = DocumentRouter()
    router.route(documents["tables"])
    first = calls["count"]
    assert first > 0
    router.route(documents["tables"])
    assert calls["count"] == first


def test_reset_route_cache_forces_new_computation(documents, monkeypatch):
    reset_route_cache()
    calls = {"count": 0}
    original = pymupdf.Page.find_tables

    def spy(page):
        calls["count"] += 1
        return original(page)

    monkeypatch.setattr(pymupdf.Page, "find_tables", spy)
    router = DocumentRouter()
    router.route(documents["tables"])
    first = calls["count"]
    reset_route_cache()
    router.route(documents["tables"])
    assert calls["count"] > first


def test_strong_invoice_short_circuits_find_tables(tmp_path, monkeypatch):
    reset_route_cache()
    calls = {"count": 0}
    original = pymupdf.Page.find_tables

    def spy(page):
        calls["count"] += 1
        return original(page)

    monkeypatch.setattr(pymupdf.Page, "find_tables", spy)
    DocumentRouter().route(_strong_invoice_pdf(tmp_path))

    assert calls["count"] == 0


def test_table_probe_is_bounded_to_two_pages(tmp_path, monkeypatch):
    reset_route_cache()
    probed: list[int] = []
    original = pymupdf.Page.find_tables

    def spy(page):
        probed.append(page.number)
        return original(page)

    monkeypatch.setattr(pymupdf.Page, "find_tables", spy)
    DocumentRouter().route(_multipage_table_pdf(tmp_path))

    assert probed
    assert max(probed) <= 1
