import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from benchmarks.extractor_benchmark_runner import (
    DEFAULT_MONETARY_TOLERANCE,
    ExtractionOutput,
    ExtractorBenchmarkRunner,
    InvoiceSchemaAdapter,
    OCRCase,
    calculate_metrics,
    compare_fields,
    load_ocr_dataset,
)
from schemas.invoice_schema import InvoiceSchema
from services.extraction_manager import InvoiceExtractionValidationError


def make_case(
    *,
    text: str = "OCR invoice text",
    truth: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
) -> OCRCase:
    return OCRCase(
        document_id="synthetic-1",
        ocr_text=text,
        ground_truth=truth or {},
        metadata=metadata or {},
    )


def make_truth() -> dict[str, object]:
    return {
        "fournisseur": "ACME Société",
        "date": "2026-10-24",
        "ht": "100.00",
        "tva": "20.00",
        "ttc": "120.00",
        "siret": "12345678901234",
    }


def make_prediction() -> dict[str, object]:
    return {
        "fournisseur": "  acme   société ",
        "date_emission": "2026-10-24",
        "total_ht": "100.01",
        "montant_tva": "20.02",
        "total_ttc": "120.03",
        "siret": "123 456 789 01234",
    }


def test_loads_fixed_ocr_dataset_from_synthetic_json(tmp_path: Path) -> None:
    dataset_path = tmp_path / "synthetic.json"
    dataset_path.write_text(
        json.dumps(
            {
                "dataset_version": "synthetic-v1",
                "cases": [
                    {
                        "id": "invoice-1",
                        "ocr_text": "frozen OCR",
                        "ground_truth": {"fournisseur": "ACME"},
                        "metadata": {"source": "fixture"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    version, cases = load_ocr_dataset(dataset_path)

    assert version == "synthetic-v1"
    assert cases == [
        OCRCase("invoice-1", "frozen OCR", {"fournisseur": "ACME"}, {"source": "fixture"})
    ]


def test_normalizes_supplier_whitespace_and_case() -> None:
    result = compare_fields(
        {"fournisseur": "Acme   Société"},
        {"fournisseur": "  ACME Société  "},
    )

    assert result["fournisseur"]["status"] == "correct"
    assert result["fournisseur"]["normalized_prediction"] == "acme société"


def test_normalizes_supported_date_formats_to_iso() -> None:
    iso = compare_fields({"date": "2026-10-24"}, {"date": "2026-10-24"})
    french = compare_fields({"date": "2026-10-24"}, {"date": "24/10/2026"})

    assert iso["date"]["status"] == "correct"
    assert french["date"]["normalized_prediction"] == "2026-10-24"
    assert french["date"]["status"] == "correct"


@pytest.mark.parametrize("bad_date", ["2026-02-30", "31/04/2026", "10/24/2026"])
def test_impossible_date_is_invalid_not_equal_by_text(bad_date: str) -> None:
    as_prediction = compare_fields({"date": "2026-10-24"}, {"date": bad_date})
    as_truth = compare_fields({"date": bad_date}, {"date": bad_date})

    assert as_prediction["date"]["status"] == "invalid_prediction"
    assert as_prediction["date"]["normalized_prediction"] is None
    assert as_truth["date"]["status"] == "ground_truth_invalid"


@pytest.mark.parametrize("ambiguous", ["24-10-2026", "2026/10/24", "24.10.2026", "10/24/26"])
def test_unexpected_date_format_is_rejected(ambiguous: str) -> None:
    result = compare_fields({"date": "2026-10-24"}, {"date": ambiguous})

    assert result["date"]["status"] == "invalid_prediction"


@pytest.mark.parametrize(
    "raw",
    [
        "1 234,56 €",
        "1\u00a0234,56 €",
        "1\u202f234,56",
        "1.234,56",
        "1234.56",
        "1,234.56",
        1234.56,
        Decimal("1234.56"),
    ],
)
def test_normalizes_french_and_plain_money_formats(raw: object) -> None:
    result = compare_fields({"ht": "1234.56"}, {"ht": raw})

    assert result["ht"]["status"] == "correct"
    assert result["ht"]["normalized_prediction"] == "1234.56"


@pytest.mark.parametrize("raw", ["abc", "12abc", "1,2,3", "NaN", "Infinity", True, ["1"]])
def test_rejects_non_numeric_money_values(raw: object) -> None:
    result = compare_fields({"ht": "10.00"}, {"ht": raw})

    assert result["ht"]["status"] == "invalid_prediction"
    assert result["ht"]["normalized_prediction"] is None


def test_compares_decimal_amounts_with_two_cent_tolerance() -> None:
    result = compare_fields(
        {"ht": Decimal("10.00"), "tva": Decimal("2.00"), "ttc": Decimal("12.00")},
        {"ht": Decimal("10.02"), "tva": Decimal("2.03"), "ttc": Decimal("12.00")},
    )

    assert DEFAULT_MONETARY_TOLERANCE == Decimal("0.02")
    assert result["ht"]["status"] == "correct"
    assert result["ht"]["difference"] == "0.02"
    assert result["tva"]["status"] == "incorrect"
    assert result["tva"]["difference"] == "0.03"
    assert result["ttc"]["status"] == "correct"


def test_normalizes_siret_separators_and_rejects_invalid_length() -> None:
    valid = compare_fields(
        {"siret": "12345678901234"},
        {"siret": "123 456 789-01234"},
    )
    invalid = compare_fields({"siret": "12345678901234"}, {"siret": "123456789"})

    assert valid["siret"]["status"] == "correct"
    assert valid["siret"]["normalized_prediction"] == "12345678901234"
    assert invalid["siret"]["status"] == "invalid_prediction"
    assert invalid["siret"]["normalized_prediction"] is None


def test_marks_missing_ground_truth_separately() -> None:
    result = compare_fields({}, {"fournisseur": "ACME"})

    assert result["fournisseur"]["status"] == "ground_truth_missing"


def test_marks_missing_prediction_as_incorrect_coverage() -> None:
    result = compare_fields({"fournisseur": "ACME"}, {})
    metrics = calculate_metrics([{"fields": result}])

    assert result["fournisseur"]["status"] == "missing_prediction"
    assert metrics["fields"]["fournisseur"] == {
        "known": 1,
        "predicted": 0,
        "correct": 0,
        "coverage": 0.0,
        "accuracy": 0.0,
    }


def test_empty_ocr_is_marked_and_excluded_from_field_metrics() -> None:
    runner = ExtractorBenchmarkRunner()
    case = make_case(text="", truth={"fournisseur": "ACME"})
    document = runner.evaluate_output(
        "NuExtract3", case, ExtractionOutput(raw_payload=None)
    )
    metrics = calculate_metrics([document])

    assert document["input_status"] == "input_missing"
    assert document["fields"]["fournisseur"]["status"] == "input_missing"
    assert metrics["fields"]["fournisseur"]["known"] == 0
    assert metrics["extraction_error_rate"] is None


def test_calculates_field_coverage_and_accuracy() -> None:
    correct = compare_fields({"fournisseur": "ACME"}, {"fournisseur": "ACME"})
    missing = compare_fields({"fournisseur": "ACME"}, {})
    unknown = compare_fields({}, {})

    metrics = calculate_metrics(
        [{"fields": correct}, {"fields": missing}, {"fields": unknown}]
    )

    assert metrics["fields"]["fournisseur"] == {
        "known": 2,
        "predicted": 1,
        "correct": 1,
        "coverage": 0.5,
        "accuracy": 0.5,
    }


def test_calculates_canonical_validation_rate_without_model_calls() -> None:
    def validator(payload: dict[str, object]) -> None:
        if payload.get("supplier", {}).get("name") != "ACME":
            raise InvoiceExtractionValidationError("synthetic validation failure")

    runner = ExtractorBenchmarkRunner(invoice_validator=validator)
    valid = runner.evaluate_output(
        "NuExtract3",
        make_case(),
        ExtractionOutput(raw_payload={"fournisseur": "ACME"}, call_status="success"),
    )
    invalid = runner.evaluate_output(
        "NuExtract3",
        make_case(),
        ExtractionOutput(raw_payload={"fournisseur": "Other"}, call_status="success"),
    )

    assert valid["validation_status"] == "valid"
    assert invalid["validation_status"] == "validation_failed"
    assert calculate_metrics([valid, invalid])["canonical_validation_rate"] == 0.5


def test_calculates_extraction_error_rate_for_attempted_documents() -> None:
    documents = [
        {"input_status": "ready", "call_status": "success", "parse_status": "success", "fields": compare_fields({}, {})},
        {"input_status": "ready", "call_status": "error", "parse_status": "not_attempted", "fields": compare_fields({}, {})},
        {"input_status": "ready", "call_status": "success", "parse_status": "error", "fields": compare_fields({}, {})},
        {"input_status": "input_missing", "call_status": "not_called", "parse_status": "not_attempted", "fields": compare_fields({}, {})},
    ]

    assert calculate_metrics(documents)["extraction_error_rate"] == pytest.approx(2 / 3)


def test_generates_json_report_without_model_calls_or_sensitive_paths(tmp_path: Path) -> None:
    private_path = r"C:\private\invoice.pdf"
    secret = "sk-live-synthetic-secret"
    case = make_case(metadata={"path": private_path, "api_key": secret})
    output = ExtractionOutput(
        raw_payload={"fournisseur": "ACME", "siret": "12345678901234"},
        call_status="success",
        parse_status="success",
    )
    runner = ExtractorBenchmarkRunner()
    document = runner.evaluate_output("NuExtract3", case, output)
    report = runner.build_report("synthetic-v1", {"NuExtract3": [document]})
    report_path = tmp_path / "report.json"
    runner.write_report(report, report_path)
    serialized = report_path.read_text(encoding="utf-8")

    assert json.loads(serialized)["settings"]["ocr_shared"] is True
    assert private_path not in serialized
    assert secret not in serialized
    assert not re.search(r"[A-Za-z]:\\", serialized)
    assert "sk-live-" not in serialized


def test_adapter_builds_canonical_nuextract_payload_with_invoice_party_siret() -> None:
    adapter = InvoiceSchemaAdapter()
    payload = {
        "fournisseur": "ACME",
        "numero_facture": "F-1",
        "date_facture": "2026-10-24",
        "montant_ht": 100,
        "montant_tva": 0,
        "montant_ttc": 120,
        "devise": "EUR",
        "siret": "12345678901234",
    }

    assert adapter.comparable_fields("NuExtract3", payload) == {
        "fournisseur": "ACME",
        "date": "2026-10-24",
        "ht": 100,
        "tva": 0,
        "ttc": 120,
        "siret": "12345678901234",
    }
    assert adapter.invoice_payload("NuExtract3", payload) == {
        "supplier": {"name": "ACME", "siret": "12345678901234"},
        "reference": {"invoice_number": "F-1", "issue_date": "2026-10-24"},
        "amount": {
            "net_amount": 100,
            "tax_amount": 0,
            "gross_amount": 120,
            "currency": "EUR",
        },
    }


def test_siret_uses_confirmed_supplier_path_and_configurable_paths() -> None:
    canonical = {
        "supplier": {"name": "ACME"},
        "amount": {"net_amount": 1, "tax_amount": 0, "gross_amount": 1},
    }
    envelope = {"status": "success", "data": canonical, "meta": {"id": "12345678901234"}}
    default = InvoiceSchemaAdapter()
    configured = InvoiceSchemaAdapter(siret_paths=[("meta", "id")])

    assert default.comparable_fields("ExtractionManager", envelope)["siret"] is None
    assert configured.comparable_fields("ExtractionManager", envelope)["siret"] == "12345678901234"
    with_siret = {"data": {**canonical, "siret": "12345678901234"}}
    assert default.invoice_payload("ExtractionManager", with_siret)["supplier"]["siret"] == "12345678901234"
    assert default.comparable_fields("ExtractionManager", with_siret)["siret"] == "12345678901234"


def test_nuextract_payload_validates_against_real_invoice_schema() -> None:
    raw_payload = {
        "fournisseur": "Synthetic Supplier",
        "numero_facture": "SYNTH-001",
        "date_facture": "2026-10-24",
        "montant_ht": "100.00",
        "montant_tva": "20.00",
        "montant_ttc": "120.00",
        "devise": "EUR",
        "siret": "12345678901234",
    }

    canonical = InvoiceSchemaAdapter.invoice_payload("NuExtract3", raw_payload)
    invoice = InvoiceSchema.model_validate(canonical)

    assert invoice.supplier is not None
    assert invoice.supplier.name == "Synthetic Supplier"
    assert invoice.supplier.siret == "12345678901234"
    assert invoice.reference.invoice_number == "SYNTH-001"
    assert invoice.reference.issue_date.isoformat() == "2026-10-24"
    assert invoice.amount.net_amount == Decimal("100.00")
    assert invoice.amount.tax_amount == Decimal("20.00")
    assert invoice.amount.gross_amount == Decimal("120.00")
    assert "siret" not in canonical
    assert "siret" in canonical["supplier"]


def test_real_invoice_schema_rejects_invalid_supplier_siret() -> None:
    canonical = InvoiceSchemaAdapter.invoice_payload(
        "NuExtract3",
        {
            "fournisseur": "Synthetic Supplier",
            "montant_ttc": "120.00",
            "siret": "1234567890123",
        },
    )

    with pytest.raises(ValidationError, match="siret must contain exactly 14 digits"):
        InvoiceSchema.model_validate(canonical)


def _doc(truth: dict[str, object], prediction: dict[str, object]) -> dict[str, object]:
    return {"fields": compare_fields(truth, prediction)}


def test_six_field_count_and_eligible_ratio() -> None:
    truth = make_truth()
    perfect = {
        "fournisseur": "ACME Société",
        "date": "24/10/2026",
        "ht": "100,00",
        "tva": "20,00",
        "ttc": "120,00",
        "siret": "123 456 789 01234",
    }
    wrong = {**perfect, "ttc": "999"}
    incomplete_truth = {k: v for k, v in truth.items() if k != "siret"}

    metrics = calculate_metrics(
        [
            _doc(truth, perfect),
            _doc(truth, wrong),
            _doc(incomplete_truth, perfect),
            _doc({**truth, "date": "2026-02-30"}, perfect),
        ]
    )

    assert metrics["documents_with_all_fields_correct"] == 1
    assert metrics["eligible_documents_with_all_ground_truth_fields"] == 2
    assert metrics["complete_six_field_accuracy"] == 0.5


def test_six_field_ratio_is_none_without_eligible_documents() -> None:
    metrics = calculate_metrics([_doc({}, {})])

    assert metrics["eligible_documents_with_all_ground_truth_fields"] == 0
    assert metrics["complete_six_field_accuracy"] is None


class FakeExtractor:
    def __init__(self, name: str, payload=None, error: Exception | None = None) -> None:
        self.name = name
        self.payload = payload
        self.error = error
        self.calls: list[str] = []

    def extract(self, case: OCRCase) -> ExtractionOutput:
        self.calls.append(case.document_id)
        if self.error:
            raise self.error
        return ExtractionOutput(self.payload, call_status="success", parse_status="success")


def test_run_evaluates_two_fake_extractors_and_skips_empty_ocr() -> None:
    truth = make_truth()
    cases = [
        OCRCase("with-text", "frozen OCR", truth, {}),
        OCRCase("empty", "   ", truth, {}),
    ]
    manager = FakeExtractor(
        "ExtractionManager",
        {
            "data": {
                "supplier": {"name": "ACME Société"},
                "reference": {"issue_date": "2026-10-24"},
                "amount": {"net_amount": "100", "tax_amount": "20", "gross_amount": "120"},
                "siret": "12345678901234",
            }
        },
    )
    nuextract = FakeExtractor("NuExtract3", error=RuntimeError(r"boom at C:\secret\dir\x.txt sk-abc123"))

    report = ExtractorBenchmarkRunner().run(cases, [manager, nuextract], "synthetic-v1")

    assert manager.calls == ["with-text"]
    assert nuextract.calls == ["with-text"]
    em_docs = report["extractors"]["ExtractionManager"]["documents"]
    nu_docs = report["extractors"]["NuExtract3"]["documents"]
    assert [d["document_id"] for d in em_docs] == ["with-text", "empty"]
    assert em_docs[0]["fields"]["ttc"]["status"] == "correct"
    assert em_docs[1]["input_status"] == "input_missing"
    assert em_docs[1]["call_status"] == "not_called"
    assert nu_docs[0]["call_status"] == "error"
    assert report["extractors"]["ExtractionManager"]["metrics"]["complete_six_field_accuracy"] == 1.0
    assert report["extractors"]["NuExtract3"]["metrics"]["extraction_error_rate"] == 1.0


def test_run_report_contains_no_secret_or_absolute_path() -> None:
    private_path = r"C:\private\invoice.pdf"
    failing = FakeExtractor(
        "NuExtract3", error=RuntimeError(f"cannot read {private_path} key=sk-live-123")
    )
    leaking = FakeExtractor("ExtractionManager", payload={"fournisseur": "ACME"})
    leaking_output = ExtractionOutput(
        None, call_status="error", error=f"failed {private_path} /home/u/x.txt sk-live-123"
    )
    runner = ExtractorBenchmarkRunner()
    document = runner.evaluate_output(
        "ExtractionManager", make_case(metadata={"path": private_path}), leaking_output
    )
    report = runner.run([make_case(metadata={"api_key": "sk-live-123"})], [failing, leaking])
    report["extractors"]["Direct"] = {"documents": [document]}
    serialized = json.dumps(report)

    assert "sk-live" not in serialized
    assert "private" not in serialized
    assert "/home/u" not in serialized
    assert not re.search(r"[A-Za-z]:\\", serialized)
