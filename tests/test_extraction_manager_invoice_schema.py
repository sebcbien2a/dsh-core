import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from schemas.invoice_schema import InvoiceSchema
from services.extraction_manager import (
    ExtractionManager,
    InvoiceExtractionValidationError,
)


@pytest.fixture
def extraction_payload() -> dict[str, object]:
    return {
        "numero_facture": "EM-204",
        "date_emission": "2026-10-01",
        "fournisseur": {"nom": "Extraction Supplier"},
        "client": {"nom": "Extraction Customer"},
        "lignes": [
            {
                "description": "Consulting",
                "quantite": 2.0,
                "prix_unitaire_ht": 50.0,
                "total_ht": 100.0,
                "taux_tva": 20.0,
            }
        ],
        "total_ht": 100.0,
        "montant_tva": 20.0,
        "total_ttc": 120.0,
        "devise": "EUR",
    }


def test_validate_invoice_returns_canonical_model_and_preserves_payload(
    extraction_payload: dict[str, object],
) -> None:
    manager = ExtractionManager()

    invoice = manager.validate_invoice(extraction_payload)

    assert isinstance(invoice, InvoiceSchema)
    assert invoice.supplier.name == "Extraction Supplier"
    assert invoice.customer.name == "Extraction Customer"
    assert invoice.reference.invoice_number == "EM-204"
    assert invoice.reference.issue_date.isoformat() == "2026-10-01"
    assert invoice.lines[0].description == "Consulting"
    assert invoice.amount.net_amount == Decimal("100.0")
    assert invoice.amount.tax_amount == Decimal("20.0")
    assert invoice.amount.gross_amount == Decimal("120.0")
    assert invoice.amount.currency == "EUR"
    assert invoice.document_id is None
    assert invoice.created_at is None
    assert invoice.updated_at is None


def test_historical_validation_interface_returns_json_serializable_dict(
    extraction_payload: dict[str, object],
) -> None:
    manager = ExtractionManager()

    result = manager._validate_data(extraction_payload)

    assert result["status"] == "success"
    assert result["data"]["supplier"]["name"] == "Extraction Supplier"
    assert json.dumps(result)
    assert result["data"]["amount"]["gross_amount"] == "120.0"


@pytest.mark.parametrize(
    "invalid_update",
    [
        {"unknown_business_field": "not accepted"},
        {"total_ttc": 121.0},
        {"lignes": [{"description": "missing required numeric fields"}]},
    ],
)
def test_invalid_payload_raises_domain_error_with_validation_cause(
    extraction_payload: dict[str, object],
    invalid_update: dict[str, object],
) -> None:
    payload = dict(extraction_payload)
    payload.update(invalid_update)
    manager = ExtractionManager()

    with pytest.raises(InvoiceExtractionValidationError) as error:
        manager.validate_invoice(payload)

    assert isinstance(error.value.__cause__, ValidationError)


def test_historical_interface_keeps_validation_failure_shape(
    extraction_payload: dict[str, object],
) -> None:
    payload = dict(extraction_payload)
    payload["total_ttc"] = 121.0

    result = ExtractionManager()._validate_data(payload)

    assert result["status"] == "validation_failed"
    assert result["extracted_data"] is payload
    assert result["errors"]


def test_validation_does_not_call_external_services_or_write_files(
    extraction_payload: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> None:
        pytest.fail("External model service must not be called during validation")

    monkeypatch.setattr("services.extraction_manager.ollama.chat", fail_if_called)

    result = ExtractionManager()._validate_data(extraction_payload)

    assert result["status"] == "success"
    assert list(tmp_path.iterdir()) == []
