"""Exhaustive offline tests for services.document_router (ingestion routing)."""

from __future__ import annotations

import io
import socket
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from services.document_router import DocumentRouter, reset_route_cache

BANK_CSV = (
    "Date;Libelle;Débit;Crédit;Solde\n"
    "01/10/2026;VIR CLIENT;;1200,00;5200,00\n"
    "02/10/2026;PRELEVEMENT EDF;85,40;;5114,60\n"
)
PURCHASE_CSV = (
    "Date;Fournisseur;Libelle;HT;TVA;TTC\n"
    "01/10/2026;ACME;Fournitures;100,00;20,00;120,00\n"
    "02/10/2026;BETA;Services;50,00;10,00;60,00\n"
)
OFX = (
    "OFXHEADER:100\nDATA:OFXSGML\n<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS>"
    "<BANKTRANLIST><STMTTRN><TRNAMT>-10.00</TRNAMT></STMTTRN>"
    "</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>\n"
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    reset_route_cache()

    def _no_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    yield
    reset_route_cache()


@pytest.fixture
def router() -> DocumentRouter:
    return DocumentRouter()


def _write(tmp_path: Path, name: str, data: bytes | str) -> Path:
    path = tmp_path / name
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)
    return path


def _native_pdf(tmp_path: Path, name: str = "native.pdf") -> Path:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    lines = [
        "FACTURE N° FA-2026-001",
        "Fournisseur: ACME SAS",
        "SIRET: 12345678900012",
        "Total HT: 100.00 EUR",
        "TVA 20%: 20.00 EUR",
        "Total TTC: 120.00 EUR",
    ]
    for index, line in enumerate(lines):
        page.insert_text((50, 80 + index * 22), line, fontsize=11)
    path = tmp_path / name
    document.save(path)
    document.close()
    return path


def _png_bytes(size: tuple[int, int] = (300, 400), fmt: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format=fmt)
    return buffer.getvalue()


def _scanned_pdf(tmp_path: Path, name: str = "scan.pdf") -> Path:
    document = pymupdf.open()
    page = document.new_page(width=300, height=400)
    page.insert_image(pymupdf.Rect(20, 20, 280, 380), stream=_png_bytes())
    path = tmp_path / name
    document.save(path)
    document.close()
    return path


# --------------------------------------------------------------- extensions


@pytest.mark.parametrize("suffix", [".ofx", ".qfx", ".OFX", ".QFX"])
def test_bank_extensions_route_to_spreadsheet(router, tmp_path, suffix):
    verdict = router.route(_write(tmp_path, f"releve{suffix}", OFX))
    assert verdict.decision.route == "spreadsheet"
    assert verdict.profile.business_family == "bank_statement"
    assert verdict.profile.profile_type == "bank_statement"
    assert verdict.plan.tools == ["pandas"]
    assert verdict.decision.fallback is False
    assert verdict.decision.confidence == 1.0


@pytest.mark.parametrize("suffix", [".csv", ".CSV"])
def test_csv_routes_to_spreadsheet(router, tmp_path, suffix):
    verdict = router.route(_write(tmp_path, f"data{suffix}", PURCHASE_CSV))
    assert verdict.decision.route == "spreadsheet"
    assert verdict.plan.tools == ["pandas"]


def test_native_pdf_routes_to_native_text(router, tmp_path):
    verdict = router.route(_native_pdf(tmp_path))
    assert verdict.decision.route == "native_text"
    assert "pymupdf_text" in verdict.plan.tools
    assert verdict.profile.has_text_layer is True
    assert verdict.profile.page_count == 1


@pytest.mark.parametrize(
    "name,fmt", [("photo.png", "PNG"), ("photo.jpg", "JPEG"), ("photo.jpeg", "JPEG")]
)
def test_images_route_to_local_ocr(router, tmp_path, name, fmt):
    verdict = router.route(_write(tmp_path, name, _png_bytes(fmt=fmt)))
    assert verdict.decision.route == "local_ocr"
    assert verdict.plan.tools == ["paddleocr", "vision_text"]
    assert verdict.decision.fallback is False


@pytest.mark.parametrize("suffix", [".xlsx", ".xls"])
def test_excel_extensions_never_go_to_pdf_producers(router, tmp_path, suffix):
    # Content is irrelevant here: extension drives the spreadsheet route.
    verdict = router.route(_write(tmp_path, f"book{suffix}", PURCHASE_CSV))
    assert verdict.decision.route == "spreadsheet"
    assert "paddleocr" not in verdict.plan.tools
    assert "docling" not in verdict.plan.tools


# --------------------------------------------------------- ambiguous CSV


def test_bank_csv_debit_credit_is_bank_statement(router, tmp_path):
    verdict = router.route(_write(tmp_path, "bank.csv", BANK_CSV))
    assert verdict.profile.business_family == "bank_statement"
    assert verdict.decision.route == "spreadsheet"
    assert any(r.startswith("bank_markers:") for r in verdict.decision.reasons)


def test_purchase_journal_csv_is_ledger_not_bank(router, tmp_path):
    verdict = router.route(_write(tmp_path, "achats.csv", PURCHASE_CSV))
    assert verdict.profile.business_family == "ledger"
    assert verdict.decision.route == "spreadsheet"
    assert not any(r.startswith("bank_markers:") for r in verdict.decision.reasons)


def test_bank_and_purchase_csv_share_producer_but_not_family(router, tmp_path):
    bank = router.route(_write(tmp_path, "a.csv", BANK_CSV))
    ledger = router.route(_write(tmp_path, "b.csv", PURCHASE_CSV))
    assert bank.plan.tools == ledger.plan.tools == ["pandas"]
    assert bank.profile.business_family != ledger.profile.business_family


def test_csv_with_only_one_bank_word_stays_ledger(router, tmp_path):
    csv = "Date;Libelle;Débit;Montant\n01/10/2026;Achat;10,00;10,00\n"
    verdict = router.route(_write(tmp_path, "single.csv", csv))
    assert verdict.profile.business_family == "ledger"


def test_csv_bank_markers_with_bom_and_comma_separator(router, tmp_path):
    csv = "\ufeffdate,label,debit,credit,solde\n01/10/2026,X,1,,10\n"
    verdict = router.route(_write(tmp_path, "bom.csv", csv))
    assert verdict.profile.business_family == "bank_statement"


def test_single_column_csv_falls_back_to_extension_ledger(router, tmp_path):
    verdict = router.route(_write(tmp_path, "one.csv", "a\n1\n2\n"))
    assert verdict.decision.route == "spreadsheet"
    assert verdict.profile.business_family == "ledger"
    assert "spreadsheet_extension:csv" in verdict.decision.reasons


# ------------------------------------------------------ mis-named files


def test_csv_renamed_xlsx_still_spreadsheet_route(router, tmp_path):
    verdict = router.route(_write(tmp_path, "achats.xlsx", PURCHASE_CSV))
    assert verdict.decision.route == "spreadsheet"
    assert verdict.profile.extension == ".xlsx"


def test_pdf_renamed_txt_is_never_misrouted_to_spreadsheet(router, tmp_path):
    source = _native_pdf(tmp_path)
    renamed = _write(tmp_path, "facture.txt", source.read_bytes())
    verdict = router.route(renamed)
    assert verdict.decision.route != "spreadsheet"
    assert verdict.profile.profile_type != "spreadsheet"


def test_txt_extension_not_treated_as_spreadsheet(router, tmp_path):
    verdict = router.route(_write(tmp_path, "notes.txt", "hello world"))
    assert verdict.decision.route != "spreadsheet"


def test_pdf_content_with_csv_extension_does_not_crash(router, tmp_path):
    renamed = _write(tmp_path, "fake.csv", _native_pdf(tmp_path).read_bytes())
    verdict = router.route(renamed)
    assert verdict.decision.route in {"spreadsheet", "native_text", "manual_review"}


def test_png_renamed_pdf_is_handled_without_exception(router, tmp_path):
    verdict = router.route(_write(tmp_path, "img.pdf", _png_bytes()))
    assert verdict.decision.route in {
        "local_ocr",
        "unsupported",
        "manual_review",
        "native_text",
    }


# -------------------------------------------- empty / corrupt / no suffix


@pytest.mark.parametrize("name", ["empty.pdf", "empty.png", "empty.csv", "empty.ofx"])
def test_zero_byte_files_never_raise(router, tmp_path, name):
    verdict = router.route(_write(tmp_path, name, b""))
    assert verdict.decision.route
    assert verdict.plan.tasks


def test_zero_byte_pdf_is_unsupported_with_fallback(router, tmp_path):
    verdict = router.route(_write(tmp_path, "empty.pdf", b""))
    assert verdict.decision.route == "unsupported"
    assert verdict.decision.fallback is True
    assert "route:unsupported" in verdict.decision.reasons


def test_zero_byte_ofx_still_bank_by_extension(router, tmp_path):
    verdict = router.route(_write(tmp_path, "empty.ofx", b""))
    assert verdict.decision.route == "spreadsheet"


def test_corrupted_pdf_is_unsupported(router, tmp_path):
    path = _write(tmp_path, "broken.pdf", b"%PDF-1.7\n\x00\xff garbage not a pdf")
    verdict = router.route(path)
    assert verdict.decision.route in {"unsupported", "manual_review"}
    assert verdict.decision.fallback is True


def test_truncated_pdf_does_not_raise(router, tmp_path):
    data = _native_pdf(tmp_path).read_bytes()
    verdict = router.route(_write(tmp_path, "cut.pdf", data[: len(data) // 3]))
    assert verdict.decision.route


def test_corrupted_png_does_not_raise(router, tmp_path):
    path = _write(tmp_path, "bad.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)
    verdict = router.route(path)
    assert verdict.decision.route


def test_binary_garbage_csv_does_not_raise(router, tmp_path):
    verdict = router.route(_write(tmp_path, "bin.csv", bytes(range(256)) * 4))
    assert verdict.decision.route == "spreadsheet"


def test_file_without_extension_is_not_spreadsheet(router, tmp_path):
    verdict = router.route(_write(tmp_path, "README", "some text"))
    assert verdict.decision.route != "spreadsheet"
    assert verdict.profile.extension == ""


def test_pdf_without_extension_does_not_raise(router, tmp_path):
    data = _native_pdf(tmp_path).read_bytes()
    verdict = router.route(_write(tmp_path, "document", data))
    assert verdict.decision.route
    assert verdict.profile.extension == ""


def test_unknown_extension_does_not_raise(router, tmp_path):
    verdict = router.route(_write(tmp_path, "file.xyz", b"abc"))
    assert verdict.decision.route


def test_missing_file_is_unsupported_or_manual_review(router, tmp_path):
    verdict = router.route(tmp_path / "absent.pdf")
    assert verdict.decision.route in {"unsupported", "manual_review"}
    assert verdict.decision.fallback is True


def test_directory_input_is_not_routed_to_a_producer(router, tmp_path):
    verdict = router.route(tmp_path)
    assert verdict.decision.route in {"unsupported", "manual_review"}


@pytest.mark.parametrize("bad", [None, 123, object()])
def test_invalid_input_type_yields_manual_review(router, bad):
    verdict = router.route(bad)
    assert verdict.decision.route == "manual_review"
    assert verdict.decision.fallback is True
    assert verdict.decision.confidence == 0.0
    assert verdict.decision.reasons[0].startswith("ROUTER_FAILED:")
    assert verdict.geometry is None


def test_failure_verdict_for_invalid_type_code(router):
    verdict = router.route(None)
    assert verdict.decision.reasons == ["ROUTER_FAILED:INVALID_INPUT"]
    assert verdict.plan.tools == ["pymupdf_text"]


def test_profiler_exception_is_contained(tmp_path):
    class Boom:
        def profile(self, path):
            raise RuntimeError("boom")

    verdict = DocumentRouter(profiler=Boom()).route(
        _write(tmp_path, "x.csv", PURCHASE_CSV)
    )
    assert verdict.decision.route == "spreadsheet"


# ------------------------------------------- native vs scanned PDF / image


def test_native_vector_pdf_is_not_ocr(router, tmp_path):
    verdict = router.route(_native_pdf(tmp_path))
    assert verdict.decision.route == "native_text"
    assert "paddleocr" not in verdict.plan.tools


def test_scanned_pdf_routes_to_local_ocr(router, tmp_path):
    verdict = router.route(_scanned_pdf(tmp_path))
    assert verdict.decision.route == "local_ocr"
    assert verdict.plan.tools == ["paddleocr", "vision_text"]
    assert verdict.profile.has_text_layer is False


def test_native_and_scanned_use_different_producers(router, tmp_path):
    native = router.route(_native_pdf(tmp_path))
    scan = router.route(_scanned_pdf(tmp_path))
    assert native.decision.route != scan.decision.route
    assert set(native.plan.tools).isdisjoint(scan.plan.tools)


def test_scanned_pdf_without_scorer_abstains_orientation(router, tmp_path):
    verdict = router.route(_scanned_pdf(tmp_path))
    assert "orientation:abstain:no_native_text" in verdict.decision.reasons


def test_scanned_pdf_uses_injected_scorer_offline(tmp_path):
    calls = []

    def scorer(image, angle):
        calls.append(angle)
        return 1.0 if angle == 0 else 0.0

    verdict = DocumentRouter(ocr_scorer=scorer).route(_scanned_pdf(tmp_path))
    assert verdict.decision.route == "local_ocr"


def test_blank_pdf_is_not_routed_to_spreadsheet(router, tmp_path):
    document = pymupdf.open()
    document.new_page()
    path = tmp_path / "blank.pdf"
    document.save(path)
    document.close()
    verdict = router.route(path)
    assert verdict.decision.route != "spreadsheet"


def test_native_pdf_with_two_tables_routes_to_docling(router, tmp_path):
    document = pymupdf.open()
    page = document.new_page(width=500, height=500)
    for table_index in range(2):
        y0 = 30 + table_index * 200
        for row in range(4):
            page.draw_line((30, y0 + row * 50), (470, y0 + row * 50))
        for column in range(4):
            page.draw_line((30 + column * 110, y0), (30 + column * 110, y0 + 150))
        for row in range(3):
            for column in range(3):
                page.insert_text(
                    (40 + column * 110, y0 + 30 + row * 50),
                    f"cell {row}{column}",
                    fontsize=10,
                )
    path = tmp_path / "tables.pdf"
    document.save(path)
    document.close()
    verdict = router.route(path)
    assert verdict.decision.route == "docling"
    assert verdict.plan.tools == ["find_tables", "docling", "pymupdf_text"]


# --------------------------------------------------- contract / determinism


def test_plan_tools_are_allowed_and_match_decision(router, tmp_path):
    from services.document_router import ALLOWED_TOOLS

    for path in (
        _native_pdf(tmp_path),
        _scanned_pdf(tmp_path),
        _write(tmp_path, "b.csv", BANK_CSV),
        _write(tmp_path, "e.pdf", b""),
    ):
        verdict = router.route(path)
        assert set(verdict.plan.tools) <= ALLOWED_TOOLS
        assert verdict.decision.tools == verdict.plan.tools


def test_fallback_flag_matches_route(router, tmp_path):
    ok = router.route(_write(tmp_path, "b.csv", BANK_CSV))
    bad = router.route(_write(tmp_path, "e.pdf", b""))
    assert ok.decision.fallback is False
    assert bad.decision.fallback is True


def test_confidence_is_bounded(router, tmp_path):
    for path in (
        _native_pdf(tmp_path),
        _scanned_pdf(tmp_path),
        _write(tmp_path, "b.csv", BANK_CSV),
        _write(tmp_path, "e.pdf", b""),
    ):
        assert 0.0 <= router.route(path).decision.confidence <= 1.0


def test_routing_is_deterministic_and_cached(router, tmp_path):
    path = _write(tmp_path, "b.csv", BANK_CSV)
    first = router.route(path)
    second = router.route(path)
    assert first == second
    assert first is not second


def test_cache_returns_isolated_copies(router, tmp_path):
    path = _write(tmp_path, "b.csv", BANK_CSV)
    first = router.route(path)
    first.decision.reasons.append("tampered")
    assert "tampered" not in router.route(path).decision.reasons


def test_source_sha256_matches_content(router, tmp_path):
    import hashlib

    data = BANK_CSV.encode("utf-8")
    path = _write(tmp_path, "b.csv", data)
    assert router.route(path).plan.source_sha256 == hashlib.sha256(data).hexdigest()


def test_string_and_path_inputs_are_equivalent(router, tmp_path):
    path = _write(tmp_path, "b.csv", BANK_CSV)
    assert router.route(str(path)) == router.route(path)


def test_router_does_not_modify_source(router, tmp_path):
    path = _write(tmp_path, "b.csv", BANK_CSV)
    before = path.read_bytes()
    router.route(path)
    assert path.read_bytes() == before
