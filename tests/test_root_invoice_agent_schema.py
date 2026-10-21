import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

import invoice_agent as root_invoice_agent
from schemas.invoice_schema import InvoiceSchema


@pytest.fixture
def invoice_payload() -> dict[str, object]:
    return {
        "numero_facture": "IA-305",
        "date_emission": "2026-10-02",
        "date_echeance": "2026-11-01",
        "fournisseur": {
            "nom": "Vision Supplier",
            "siret": "12345678901234",
            "numero_tva": "FR12345678901",
            "adresse": "1 Rue Exemple",
        },
        "client": {
            "nom": "Vision Customer",
            "siret": None,
            "adresse": "2 Rue Exemple",
        },
        "lignes": [
            {
                "description": "Materials",
                "quantite": 3.0,
                "prix_unitaire_ht": 40.0,
                "total_ht": 120.0,
                "taux_tva": 20.0,
            }
        ],
        "total_ht": 120.0,
        "montant_tva": 24.0,
        "total_ttc": 144.0,
        "devise": "EUR",
    }


@pytest.fixture
def agent(monkeypatch: pytest.MonkeyPatch) -> root_invoice_agent.InvoiceAgent:
    monkeypatch.setattr(root_invoice_agent, "VISION_MODEL", "test-vision")
    return root_invoice_agent.InvoiceAgent()


def test_validate_invoice_returns_canonical_model_and_preserves_payload(
    agent: root_invoice_agent.InvoiceAgent,
    invoice_payload: dict[str, object],
) -> None:
    invoice = agent.validate_invoice(invoice_payload)

    assert isinstance(invoice, InvoiceSchema)
    assert invoice.supplier.name == "Vision Supplier"
    assert invoice.supplier.siret == "12345678901234"
    assert invoice.supplier.vat_number == "FR12345678901"
    assert invoice.supplier.address == "1 Rue Exemple"
    assert invoice.customer.name == "Vision Customer"
    assert invoice.customer.address == "2 Rue Exemple"
    assert invoice.reference.invoice_number == "IA-305"
    assert invoice.reference.issue_date == date(2026, 10, 2)
    assert invoice.reference.due_date == date(2026, 11, 1)
    assert invoice.lines[0].description == "Materials"
    assert invoice.lines[0].total_ht == Decimal("120.0")
    assert invoice.amount.net_amount == Decimal("120.0")
    assert invoice.amount.tax_amount == Decimal("24.0")
    assert invoice.amount.gross_amount == Decimal("144.0")
    assert invoice.amount.currency == "EUR"
    assert invoice.document_id is None
    assert invoice.direction.value == "UNKNOWN"
    assert invoice.created_at is None
    assert invoice.updated_at is None


def test_json_mode_dump_is_serializable(agent, invoice_payload) -> None:
    invoice = agent.validate_invoice(invoice_payload)

    dumped = invoice.model_dump(mode="json")
    assert json.dumps(dumped)
    assert dumped["supplier"]["vat_number"] == "FR12345678901"
    assert dumped["amount"]["gross_amount"] == "144.0"


@pytest.mark.parametrize(
    "invalid_update",
    [
        {"unknown_business_field": "not accepted"},
        {"total_ttc": 145.0},
        {"lignes": [{"description": "incomplete"}]},
        {
            "fournisseur": {
                "nom": "Vision Supplier",
                "siret": "12345",
                "numero_tva": "FR12345678901",
                "adresse": "1 Rue Exemple",
            }
        },
    ],
)
def test_invalid_payload_raises_domain_error_with_validation_cause(
    agent: root_invoice_agent.InvoiceAgent,
    invoice_payload: dict[str, object],
    invalid_update: dict[str, object],
) -> None:
    payload = dict(invoice_payload)
    payload.update(invalid_update)

    with pytest.raises(root_invoice_agent.InvoiceAgentValidationError) as error:
        agent.validate_invoice(payload)

    assert isinstance(error.value.__cause__, ValidationError)


def test_historical_processing_path_uses_canonical_validation_and_json_dump(
    agent: root_invoice_agent.InvoiceAgent,
    invoice_payload: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_file = tmp_path / "input" / "invoice.json"
    source_file.parent.mkdir()
    source_file.write_text("local test input", encoding="utf-8")
    output_dir = tmp_path / "output_json"
    processed_dir = tmp_path / "processed"
    monkeypatch.setattr(root_invoice_agent, "OUTPUT_JSON_DIR", output_dir)
    monkeypatch.setattr(root_invoice_agent, "PROCESSED_DIR", processed_dir)
    monkeypatch.setattr(
        agent,
        "structurer_document_en_json",
        lambda _file_path: invoice_payload,
    )

    def forbid_external_call(*args: object, **kwargs: object) -> None:
        pytest.fail("Validation flow must not call a Vision or network service")

    monkeypatch.setattr(root_invoice_agent.ollama, "chat", forbid_external_call)
    monkeypatch.setattr(
        root_invoice_agent.shutil,
        "move",
        lambda source, destination: str(destination),
    )

    invoice = agent.traiter_facture(source_file)
    persisted = json.loads(
        (output_dir / "invoice.json").read_text(encoding="utf-8")
    )

    assert isinstance(invoice, InvoiceSchema)
    assert persisted["reference"]["invoice_number"] == "IA-305"
    assert persisted["amount"]["gross_amount"] == "144.0"
    assert all(
        path.resolve().is_relative_to(tmp_path.resolve())
        for path in tmp_path.rglob("*")
    )


def test_validation_does_not_create_files_or_call_external_services(
    agent: root_invoice_agent.InvoiceAgent,
    invoice_payload: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def forbid_external_call(*args: object, **kwargs: object) -> None:
        pytest.fail("Validation must not call a Vision or network service")

    monkeypatch.setattr(root_invoice_agent.ollama, "chat", forbid_external_call)

    invoice = agent.validate_invoice(invoice_payload)

    assert isinstance(invoice, InvoiceSchema)
    assert list(tmp_path.iterdir()) == []
