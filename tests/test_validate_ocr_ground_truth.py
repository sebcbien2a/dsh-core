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
