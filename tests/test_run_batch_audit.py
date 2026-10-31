"""Tests for the batch audit script corpus -> InvoiceSchema -> rules -> LangGraph."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

import pytest

from scripts.run_batch_audit import (
    DEFAULT_RULES_DIR,
    HITL,
    REJECTED,
    VALIDATED,
    BatchAuditError,
    CorpusScanner,
    classify_decision,
    main,
    rule_outcome_for,
    run_batch_audit,
)
from services.persistence_manager import PersistenceManager
from services.tva_zero_rule_evaluator import RuleDecision

STANDARD_LINES = [
    "ACME SAS",
    "12 rue des Lilas",
    "75001 Paris",
    "SIRET: 123 456 789 00012",
    "Numero TVA: FR12345678901",
    "Facture N: F-2026-001",
    "Date: 14/09/2026",
    "Net HT 100,00 20,00 120,00",
]
INTRACOM_LINES = [
    "ACME GmbH",
    "Berliner Strasse 12",
    "10115 Berlin",
    "Numero TVA: DE123456789",
    "Facture N: D-2026-002",
    "Date: 15/09/2026",
    "Net HT 200,00 0,00 200,00",
]
AMBIGUOUS_LINES = [
    "FOURNISSEUR TEST",
    "Facture N: Z-2026-003",
    "Date: 16/09/2026",
    "Net HT 50,00 0,00 50,00",
]
SPARSE_LINES = [
    "ACME SAS",
    "Facture N: S-2026-004",
    "Date: 17/09/2026",
    "Net HT 75,00 15,00 90,00",
]


def _make_pdf(path: Path, lines: Sequence[str]) -> Path:
    pymupdf = pytest.importorskip("pymupdf")
    document = pymupdf.open()
    page = document.new_page()
    y = 72
    for line in lines:
        page.insert_text((72, y), line)
        y += 16
    document.save(str(path))
    document.close()
    return path


def _write_corpus(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _make_pdf(root / "01_standard.pdf", STANDARD_LINES)
    _make_pdf(root / "02_intracom.pdf", INTRACOM_LINES)
    _make_pdf(root / "03_ambiguous.pdf", AMBIGUOUS_LINES)
    _make_pdf(root / "04_sparse.pdf", SPARSE_LINES)
    (root / "05_unsupported.txt").write_text(
        "not an invoice", encoding="utf-8"
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_classify_decision_mapping() -> None:
    assert classify_decision({"commit_status": "COMMITTED"}) == VALIDATED
    assert classify_decision({"commit_status": "PENDING_HITL"}) == HITL
    assert classify_decision({"commit_status": ""}) == REJECTED
    assert classify_decision({}) == REJECTED


def test_rule_outcome_mapping() -> None:
    def decision(reason: str) -> RuleDecision:
        return RuleDecision(status="HITL", reason=reason)

    assert rule_outcome_for(None) == "NO_RULE_REACHED"
    assert rule_outcome_for(decision("RULE_NOT_TRIGGERED")) == "NOT_TRIGGERED"
    assert (
        rule_outcome_for(decision("ZERO_VAT_DISAMBIGUATED"))
        == "TRIGGERED_RESOLVED"
    )
    assert rule_outcome_for(decision("AMBIGUOUS_ZERO_VAT")) == "TRIGGERED_BLOCKED"
    assert rule_outcome_for(decision("RULE_NOT_APPROVED")) == "RULE_MISSING"
    assert (
        rule_outcome_for(decision("INVOICE_NOT_VALIDATED"))
        == "RULE_EVALUATION_SKIPPED"
    )


def test_corpus_scanner_orders_and_hashes(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "b.pdf").write_bytes(b"beta")
    (corpus / "a.pdf").write_bytes(b"alpha")
    (corpus / "c.txt").write_text("gamma", encoding="utf-8")

    scanned = CorpusScanner(corpus).scan()

    assert [item.name for item in scanned] == ["a.pdf", "b.pdf", "c.txt"]
    assert [item.suffix for item in scanned] == [".pdf", ".pdf", ".txt"]
    assert scanned[0].sha256 == hashlib.sha256(b"alpha").hexdigest()
    assert scanned[0].size_bytes == len(b"alpha")


def test_batch_audit_end_to_end_writes_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = tmp_path / "input"
    _write_corpus(corpus)

    def _forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("persistence must never be called during audit")

    monkeypatch.setattr(PersistenceManager, "persist_invoice", _forbidden)

    result = run_batch_audit(
        corpus,
        rules_dir=DEFAULT_RULES_DIR,
        output_dir=tmp_path / "audit",
    )

    assert result.exit_code == 2
    for name in ("manifest.json", "records.jsonl", "summary.json", "report.md"):
        assert (result.run_dir / name).is_file()
    records = _read_jsonl(result.run_dir / "records.jsonl")
    assert [record["seq"] for record in records] == [1, 2, 3, 4, 5]
    assert [record["source"]["name"] for record in records] == [
        "01_standard.pdf",
        "02_intracom.pdf",
        "03_ambiguous.pdf",
        "04_sparse.pdf",
        "05_unsupported.txt",
    ]
    by_name = {record["source"]["name"]: record for record in records}

    standard = by_name["01_standard.pdf"]
    assert standard["decision"]["status"] == VALIDATED
    assert standard["decision"]["graph_commit_status"] == "COMMITTED"
    assert standard["rule"]["rule_id"] == "RULE-TVA-ZERO-001"
    assert standard["rule"]["rule_version"] == "1.0.0"
    assert standard["rule"]["outcome"] == "NOT_TRIGGERED"
    assert standard["rule"]["reason"] == "RULE_NOT_TRIGGERED"
    assert standard["rule"]["conditions"][0]["field"] == "amount.tax_amount"
    assert standard["rule"]["conditions"][0]["actual"] == "20.00"
    assert standard["extraction"]["tool"] == "pymupdf_text_regex"
    assert standard["extraction"]["fields"]["supplier_name"] == "ACME SAS"
    assert standard["extraction"]["fields"]["siret"] == "12345678900012"
    assert standard["blocking_points"] == []

    intracom = by_name["02_intracom.pdf"]
    assert intracom["decision"]["status"] == VALIDATED
    assert intracom["rule"]["outcome"] == "TRIGGERED_RESOLVED"
    assert intracom["rule"]["vat_regime"] == "INTRACOM_AUTOLIQUIDATION"
    assert intracom["rule"]["include_in_vat_balance"] is True
    assert intracom["extraction"]["fields"]["country"] == "DE"

    ambiguous = by_name["03_ambiguous.pdf"]
    assert ambiguous["decision"]["status"] == HITL
    assert ambiguous["decision"]["graph_commit_status"] == "PENDING_HITL"
    assert ambiguous["rule"]["outcome"] == "TRIGGERED_BLOCKED"
    assert ambiguous["rule"]["reason"] == "AMBIGUOUS_ZERO_VAT"
    codes = {point["code"] for point in ambiguous["blocking_points"]}
    assert "AMBIGUOUS_ZERO_VAT" in codes
    assert "SUPPLIER_UNKNOWN" in codes

    sparse = by_name["04_sparse.pdf"]
    assert sparse["decision"]["status"] == VALIDATED
    sparse_codes = {point["code"] for point in sparse["blocking_points"]}
    assert {"MISSING_SIRET", "MISSING_VAT_NUMBER", "MISSING_COUNTRY"} <= (
        sparse_codes
    )

    unsupported = by_name["05_unsupported.txt"]
    assert unsupported["decision"]["status"] == REJECTED
    assert unsupported["rule"]["outcome"] == "NO_RULE_REACHED"
    assert unsupported["rule"]["rule_id"] is None
    assert [point["code"] for point in unsupported["blocking_points"]] == [
        "UNSUPPORTED_FILE_TYPE"
    ]

    manifest = json.loads(
        (result.run_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["deterministic"] is True
    assert manifest["run_valid"] is True
    assert manifest["corpus"]["file_count"] == 5
    assert manifest["corpus"]["supported_count"] == 4
    assert manifest["config"]["vision"] == "none"
    approved = manifest["rules_snapshot"]["approved"]
    assert any(
        entry["rule_id"] == "RULE-TVA-ZERO-001"
        and entry["version"] == "1.0.0"
        for entry in approved
    )
    assert manifest["environment"]["python"]

    summary = json.loads(
        (result.run_dir / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["decisions"] == {VALIDATED: 3, HITL: 1, REJECTED: 1}
    assert summary["rule_outcomes"]["TRIGGERED_BLOCKED"] == 1
    assert summary["rule_outcomes"]["NO_RULE_REACHED"] == 1
    assert summary["extraction_field_coverage"]["supplier_name"]["present"] == 3
    assert summary["extraction_field_coverage"]["siret"]["present"] == 1
    assert summary["top_suppliers"] == [
        {"name": "ACME SAS", "count": 2},
        {"name": "ACME GmbH", "count": 1},
    ]

    report = (result.run_dir / "report.md").read_text(encoding="utf-8")
    assert "# Audit de lot DSH-Core" in report
    assert "AMBIGUOUS_ZERO_VAT" in report
    assert "UNSUPPORTED_FILE_TYPE" in report
    assert "RULE-TVA-ZERO-001" in report


def test_batch_audit_resume_skips_processed_files(tmp_path: Path) -> None:
    corpus = tmp_path / "input"
    _write_corpus(corpus)
    output = tmp_path / "audit"

    first = run_batch_audit(corpus, output_dir=output)
    assert len(_read_jsonl(first.run_dir / "records.jsonl")) == 5

    resumed = run_batch_audit(
        corpus,
        output_dir=output,
        resume=True,
        run_id=first.run_id,
    )

    assert resumed.manifest["resumed"] is True
    assert resumed.manifest["records_reused"] == 5
    assert resumed.manifest["records_written"] == 0
    assert resumed.manifest["record_count"] == 5
    assert len(_read_jsonl(first.run_dir / "records.jsonl")) == 5
    assert resumed.summary["record_count"] == 5


def test_batch_audit_unapproved_rule_routes_to_hitl(tmp_path: Path) -> None:
    corpus = tmp_path / "input"
    corpus.mkdir()
    _make_pdf(corpus / "standard.pdf", STANDARD_LINES)

    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    source = (
        DEFAULT_RULES_DIR / "RULE_TVA_ZERO_DISAMBIGUATION_001.md"
    ).read_text(encoding="utf-8")
    (rules_dir / "RULE_TVA_ZERO_DISAMBIGUATION_001.md").write_text(
        source.replace('status: "approved"', 'status: "draft"'),
        encoding="utf-8",
    )

    result = run_batch_audit(
        corpus,
        rules_dir=rules_dir,
        output_dir=tmp_path / "audit",
    )

    record = result.records[0]
    assert record["decision"]["status"] == HITL
    assert record["rule"]["outcome"] == "RULE_MISSING"
    assert record["rule"]["reason"] == "RULE_NOT_APPROVED"
    assert record["rule"]["source_file"] == (
        "RULE_TVA_ZERO_DISAMBIGUATION_001.md"
    )
    codes = {point["code"] for point in record["blocking_points"]}
    assert "RULE_NOT_APPROVED" in codes

    manifest = json.loads(
        (result.run_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["rules_snapshot"]["approved"] == []
    assert manifest["rules_snapshot"]["ignored"] == [
        {
            "file": "RULE_TVA_ZERO_DISAMBIGUATION_001.md",
            "raw_status": "draft",
        }
    ]


def test_batch_audit_requires_supported_file(tmp_path: Path) -> None:
    corpus = tmp_path / "input"
    corpus.mkdir()
    (corpus / "notes.txt").write_text("no invoice", encoding="utf-8")

    with pytest.raises(BatchAuditError) as exc_info:
        run_batch_audit(corpus, output_dir=tmp_path / "audit")

    assert exc_info.value.code == "NO_SUPPORTED_FILES"


def test_main_json_output_and_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = tmp_path / "input"
    _write_corpus(corpus)

    exit_code = main(
        [
            "--input",
            str(corpus),
            "--output",
            str(tmp_path / "audit"),
            "--rules-dir",
            str(DEFAULT_RULES_DIR),
            "--json",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    payload = json.loads(captured.out)
    assert payload["decisions"] == {VALIDATED: 3, HITL: 1, REJECTED: 1}
    assert payload["record_count"] == 5
