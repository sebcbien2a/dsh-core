import json
import re
import socket
from datetime import datetime
from pathlib import Path

import pytest

from benchmarks import validate_ocr_ground_truth as tool

SECRET_OCR = "TEXTE OCR SYNTHETIQUE UNIQUE 987654"


def _case(case_id: str = "DOC-001") -> dict:
    return {
        "id": case_id,
        "ocr_text": SECRET_OCR,
        "ground_truth": {field: None for field in tool.FIELDS},
        "metadata": {"source_sha256": "0" * 64, "reviewed_by_human": False},
    }


def _write(tmp_path: Path, cases: list[dict] | None = None) -> Path:
    path = tmp_path / "dataset.json"
    dataset = {"dataset_version": "t", "fields": list(tool.FIELDS), "cases": cases or [_case("DOC-001"), _case("DOC-002")]}
    path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
    return path


def _full_decisions() -> dict:
    return {
        "fournisseur": (tool.KNOWN, "Societe Fictive"),
        "date": (tool.KNOWN, "15/10/2026"),
        "ht": (tool.KNOWN, "1 187,43 EUR"),
        "tva": (tool.ABSENT, None),
        "ttc": (tool.UNREADABLE, None),
        "siret": (tool.KNOWN, "11111111111111"),
    }


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def test_loads_temporary_synthetic_dataset(tmp_path):
    dataset = tool.load_dataset(_write(tmp_path))
    assert len(dataset["cases"]) == 2


def test_refuses_missing_non_json_and_invalid_json(tmp_path):
    with pytest.raises(tool.DatasetError):
        tool.load_dataset(tmp_path / "absent.json")
    other = tmp_path / "data.txt"
    other.write_text("{}", encoding="utf-8")
    with pytest.raises(tool.DatasetError):
        tool.load_dataset(other)
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(tool.DatasetError):
        tool.load_dataset(broken)


def test_refuses_case_without_six_fields(tmp_path):
    case = _case()
    del case["ground_truth"]["siret"]
    with pytest.raises(tool.DatasetError):
        tool.load_dataset(_write(tmp_path, [case]))


def test_three_states_are_accepted():
    assert tool.validate_field("fournisseur", tool.KNOWN, "ACME")[0]
    assert tool.validate_field("tva", tool.ABSENT, None)[0]
    assert tool.validate_field("siret", tool.UNREADABLE, "")[0]
    assert not tool.validate_field("tva", "", None)[0]


@pytest.mark.parametrize("value", ["2026-10-15", "15/10/2026"])
def test_valid_date_formats(value):
    assert tool.validate_field("date", tool.KNOWN, value)[0]


@pytest.mark.parametrize("value", ["15-10-2026", "31/02/2026", "2026/10/15", ""])
def test_invalid_dates_are_refused(value):
    assert not tool.validate_field("date", tool.KNOWN, value)[0]


@pytest.mark.parametrize("value", ["100,00", "1 234,56", "1.234,56", "12,00 EUR"])
def test_french_amounts_are_accepted(value):
    assert tool.validate_field("ht", tool.KNOWN, value)[0]


def test_invalid_amount_is_refused():
    assert not tool.validate_field("ttc", tool.KNOWN, "abc")[0]


def test_siret_requires_exactly_fourteen_digits():
    assert tool.validate_field("siret", tool.KNOWN, "11111111111111")[0]
    for bad in ("1111111111111", "111111111111111", "1111 1111 111111", "1111111111111A"):
        assert not tool.validate_field("siret", tool.KNOWN, bad)[0]


def test_empty_supplier_is_refused():
    assert not tool.validate_field("fournisseur", tool.KNOWN, "   ")[0]


def test_reviewed_flag_only_after_six_valid_decisions():
    case = _case()
    partial = _full_decisions()
    del partial["siret"]
    with pytest.raises(tool.DatasetError):
        tool.apply_decisions(case, partial)
    assert case["metadata"]["reviewed_by_human"] is False
    invalid = _full_decisions()
    invalid["siret"] = (tool.KNOWN, "123")
    with pytest.raises(tool.DatasetError):
        tool.apply_decisions(case, invalid)
    assert case["metadata"]["reviewed_by_human"] is False

    tool.apply_decisions(case, _full_decisions(), now=datetime(2026, 10, 25, 3, 0, 0))
    assert case["metadata"]["reviewed_by_human"] is True
    assert case["metadata"][tool.VALIDATED_AT_KEY] == "2026-10-25T03:00:00"
    assert case["ground_truth"]["ht"] == "1 187,43 EUR"
    assert case["ground_truth"]["tva"] is None
    assert case["metadata"][tool.STATUS_KEY]["tva"] == "absent"
    assert case["metadata"][tool.STATUS_KEY]["ttc"] == "unreadable"


def test_atomic_save_creates_backup_and_leaves_no_temp(tmp_path):
    path = _write(tmp_path)
    original = path.read_text(encoding="utf-8")
    dataset = tool.load_dataset(path)
    tool.apply_decisions(dataset["cases"][0], _full_decisions())
    tool.save_dataset_atomic(path, dataset)

    assert path.with_name(path.name + ".bak").read_text(encoding="utf-8") == original
    assert json.loads(path.read_text(encoding="utf-8"))["cases"][0]["metadata"]["reviewed_by_human"]
    assert not list(tmp_path.glob("*.tmp"))


def test_failed_write_keeps_original_file(tmp_path, monkeypatch):
    path = _write(tmp_path)
    original = path.read_text(encoding="utf-8")
    dataset = tool.load_dataset(path)

    def boom(*args, **kwargs):
        raise OSError("disk")

    monkeypatch.setattr(tool.os, "replace", boom)
    with pytest.raises(OSError):
        tool.save_dataset_atomic(path, dataset)
    assert path.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("*.tmp"))


def test_ocr_text_is_unchanged_and_no_absolute_path_added(tmp_path):
    path = _write(tmp_path)
    dataset = tool.load_dataset(path)
    tool.apply_decisions(dataset["cases"][0], _full_decisions())
    tool.save_dataset_atomic(path, dataset)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert all(case["ocr_text"] == SECRET_OCR for case in saved["cases"])
    assert not re.search(r"[A-Za-z]:[\\/]", path.read_text(encoding="utf-8"))
    assert str(tmp_path) not in path.read_text(encoding="utf-8")


def test_resume_after_interruption(tmp_path):
    path = _write(tmp_path, [_case("DOC-001"), _case("DOC-002"), _case("DOC-003")])
    dataset = tool.load_dataset(path)
    tool.apply_decisions(dataset["cases"][0], _full_decisions())
    tool.save_dataset_atomic(path, dataset)

    resumed = tool.load_dataset(path)
    assert tool.progress(resumed) == (1, 3)
    assert tool.first_unconfirmed_index(resumed) == 1
    assert tool.case_decisions(resumed["cases"][0])["tva"] == (tool.ABSENT, None)
    assert tool.case_decisions(resumed["cases"][1]) == {f: (None, None) for f in tool.FIELDS}


def test_cli_errors_and_functions_never_print_ocr_text(tmp_path, capsys):
    path = _write(tmp_path)
    dataset = tool.load_dataset(path)
    tool.apply_decisions(dataset["cases"][0], _full_decisions())
    tool.save_dataset_atomic(path, dataset)
    assert tool.main([str(tmp_path / "missing.json")]) == 2
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert tool.main([str(broken)]) == 2
    captured = capsys.readouterr()
    assert SECRET_OCR not in captured.out + captured.err
    assert "0" * 64 not in captured.out + captured.err


# --- source preview ---------------------------------------------------------

import hashlib
import io


def _make_pdf(path: Path) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=200, height=100)
    page.insert_text((20, 50), "synthetic")
    data = doc.tobytes()
    doc.close()
    path.write_bytes(data)
    return data


def _make_png(path: Path, size=(40, 20)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, "red").save(buffer, format="PNG")
    path.write_bytes(buffer.getvalue())
    return buffer.getvalue()


def _setup_sources(tmp_path: Path):
    (tmp_path / "src").mkdir()
    pdf = _make_pdf(tmp_path / "src" / "a.pdf")
    png = _make_png(tmp_path / "src" / "b.png")
    entries = [
        {"relative_path": "src/a.pdf", "media_type": "application/pdf",
         "sha256": hashlib.sha256(pdf).hexdigest(), "expected_orientation_degrees": 0},
        {"relative_path": "src/b.png", "media_type": "image/png",
         "sha256": hashlib.sha256(png).hexdigest(), "expected_orientation_degrees": 90},
    ]
    return entries


def test_resolves_source_by_sha256(tmp_path):
    entries = _setup_sources(tmp_path)
    data, entry = tool.resolve_source(entries[1]["sha256"], entries, tmp_path)
    assert entry["relative_path"] == "src/b.png" and data


def test_missing_or_wrong_hash_refused(tmp_path):
    entries = _setup_sources(tmp_path)
    with pytest.raises(tool.SourceError):
        tool.resolve_source("f" * 64, entries, tmp_path)
    with pytest.raises(tool.SourceError):
        tool.resolve_source(None, entries, tmp_path)
    (tmp_path / "src" / "b.png").write_bytes(b"altered")
    with pytest.raises(tool.SourceError):
        tool.resolve_source(entries[1]["sha256"], entries, tmp_path)


def test_path_escaping_repo_root_refused(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"x")
    entries = [{"relative_path": "../outside.bin", "sha256": hashlib.sha256(b"x").hexdigest()}]
    with pytest.raises(tool.SourceError):
        tool.resolve_source(entries[0]["sha256"], entries, root)


def test_pdf_rendered_in_memory_without_files(tmp_path):
    entries = _setup_sources(tmp_path)
    before = sorted(p.name for p in tmp_path.rglob("*"))
    data, entry = tool.resolve_source(entries[0]["sha256"], entries, tmp_path)
    image = tool.load_source_image(data, entry)
    assert image.width > 200 and image.height > 100
    assert tool.render_preview_png(image, 0.5)[:8] == b"\x89PNG\r\n\x1a\n"
    assert sorted(p.name for p in tmp_path.rglob("*")) == before


def test_image_loaded_and_preview_orientation_applied(tmp_path):
    entries = _setup_sources(tmp_path)
    data, entry = tool.resolve_source(entries[1]["sha256"], entries, tmp_path)
    image = tool.load_source_image(data, entry)
    assert image.size == (20, 40)  # 40x20 rotated by 90 degrees


def test_confirmation_blocked_without_verified_source():
    with pytest.raises(tool.SourceError):
        tool.require_verified_source(False)
    tool.require_verified_source(True)


def test_zoom_helpers_bounded():
    assert tool.clamp_zoom(100) == tool.MAX_ZOOM
    assert tool.clamp_zoom(0.0001) == tool.MIN_ZOOM
    assert tool.fit_zoom((1000, 500), (500, 500)) == 0.5


def test_source_path_never_written_to_dataset(tmp_path):
    entries = _setup_sources(tmp_path)
    case = _case()
    case["metadata"]["source_sha256"] = entries[0]["sha256"]
    path = _write(tmp_path, [case])
    dataset = tool.load_dataset(path)
    tool.resolve_source(entries[0]["sha256"], entries, tmp_path)
    tool.apply_decisions(dataset["cases"][0], _full_decisions())
    tool.save_dataset_atomic(path, dataset)
    saved = path.read_text(encoding="utf-8")
    assert "a.pdf" not in saved and "src/" not in saved and str(tmp_path) not in saved
    assert json.loads(saved)["cases"][0]["ocr_text"] == SECRET_OCR


def test_absent_siret_and_unreadable_accepted_vat_number_not_copied():
    decisions = _full_decisions()
    decisions["siret"] = (tool.ABSENT, "FR12345678901")
    decisions["ttc"] = (tool.UNREADABLE, "123,45")
    assert tool.validate_decisions(decisions) == {}
    case = _case()
    tool.apply_decisions(case, decisions)
    assert case["ground_truth"]["siret"] is None
    assert case["ground_truth"]["ttc"] is None
    assert case["metadata"][tool.STATUS_KEY]["siret"] == tool.ABSENT
    errors = tool.validate_decisions({**decisions, "siret": (tool.KNOWN, "FR12345678901")})
    assert "siret" in errors


def test_manifest_loading(tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"entries": [{"sha256": "a"}]}), encoding="utf-8")
    assert tool.load_manifest(manifest) == [{"sha256": "a"}]
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(tool.SourceError):
        tool.load_manifest(manifest)


def test_help_texts_present():
    assert "Numéro TVA ≠ SIRET" in tool.HELP_WARNINGS
    assert "UNREADABLE" in tool.HELP_STATES
    assert "numero_tva" not in tool.FIELDS
