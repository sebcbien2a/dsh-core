from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from agents import invoice_agent
from schemas.invoice_schema import (
    InvoiceDirection,
    InvoiceDocumentType,
    InvoiceSchema,
    ValidationStatus,
)
from agents.invoice_agent import (
    InvoiceAgent,
    InvoiceAgentCanonicalValidationError,
    InvoiceData,
    TypePiece,
)


DOCUMENT_ID = UUID("b812ceef-85cb-4a36-83df-37f3bcdf77a4")
CREATED_AT = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
UPDATED_AT = datetime(2026, 10, 21, 12, 5, tzinfo=timezone.utc)


def make_invoice_data(**overrides: object) -> InvoiceData:
    payload: dict[str, object] = {
        "fournisseur": "Legacy Supplier",
        "date_facture": "2026-10-01",
        "numero_facture": "LEG-2026-01",
        "categorie": "office",
        "montant_ht": 100.0,
        "montant_tva": 20.0,
        "montant_ttc": 120.0,
        "devise": "EUR",
        "taux_tva_pourcentage": 20.0,
    }
    payload.update(overrides)
    return InvoiceData.model_validate(payload)


def make_agent() -> InvoiceAgent:
    return InvoiceAgent.__new__(InvoiceAgent)


def test_to_canonical_invoice_converts_legacy_invoice_data() -> None:
    invoice_data = make_invoice_data()

    invoice = make_agent().to_canonical_invoice(
        invoice_data,
        document_id=DOCUMENT_ID,
        created_at=CREATED_AT,
        updated_at=UPDATED_AT,
    )

    assert isinstance(invoice, InvoiceSchema)
    assert invoice.document_id == DOCUMENT_ID
    assert invoice.created_at == CREATED_AT
    assert invoice.updated_at == UPDATED_AT
    assert invoice.supplier.name == "Legacy Supplier"
    assert invoice.customer is None
    assert invoice.reference.invoice_number == "LEG-2026-01"
    assert invoice.reference.issue_date.isoformat() == "2026-10-01"
    assert invoice.category == "office"
    assert invoice.amount.net_amount == Decimal("100.0")
    assert invoice.amount.tax_amount == Decimal("20.0")
    assert invoice.amount.gross_amount == Decimal("120.0")
    assert invoice.amount.currency == "EUR"
    assert invoice.direction is InvoiceDirection.UNKNOWN


def test_legacy_review_status_and_warnings_are_preserved() -> None:
    invoice_data = make_invoice_data(
        statut_traitement="A_VERIFIER",
        commentaires_controle=["manual review required", "tax rate uncertain"],
    )

    invoice = make_agent().to_canonical_invoice(
        invoice_data,
        document_id=DOCUMENT_ID,
        created_at=CREATED_AT,
        updated_at=UPDATED_AT,
    )

    assert invoice.validation_status is ValidationStatus.NEEDS_REVIEW
    assert invoice.extraction_warnings == [
        "manual review required",
        "tax rate uncertain",
    ]


def test_credit_note_and_absent_fields_are_not_invented() -> None:
    invoice_data = make_invoice_data(
        type_piece=TypePiece.AVOIR,
        numero_facture=None,
        categorie=None,
    )

    invoice = make_agent().to_canonical_invoice(
        invoice_data,
        document_id=DOCUMENT_ID,
        created_at=CREATED_AT,
        updated_at=UPDATED_AT,
    )

    assert invoice.document_type is InvoiceDocumentType.CREDIT_NOTE
    assert invoice.direction is InvoiceDirection.UNKNOWN
    assert invoice.reference.invoice_number is None
    assert invoice.category is None
    assert invoice.customer is None
    assert invoice.document_id == DOCUMENT_ID


def test_validate_canonical_invoice_accepts_legacy_payload() -> None:
    payload = make_invoice_data().model_dump(mode="python")

    invoice = make_agent().validate_canonical_invoice(
        payload,
        document_id=DOCUMENT_ID,
        created_at=CREATED_AT,
        updated_at=UPDATED_AT,
    )

    assert isinstance(invoice, InvoiceSchema)
    assert invoice.amount.gross_amount == Decimal("120.0")


@pytest.mark.parametrize(
    "payload",
    [
        {"fournisseur": "Missing required totals"},
        {
            "fournisseur": "Supplier",
            "date_facture": "2026-10-01",
            "montant_ht": "not-a-number",
            "montant_tva": 20.0,
            "montant_ttc": 120.0,
        },
    ],
)
def test_invalid_legacy_payload_is_wrapped_with_validation_error_cause(
    payload: dict[str, object],
) -> None:
    with pytest.raises(InvoiceAgentCanonicalValidationError) as error:
        make_agent().validate_canonical_invoice(
            payload,
            document_id=DOCUMENT_ID,
            created_at=CREATED_AT,
            updated_at=UPDATED_AT,
        )

    assert isinstance(error.value.__cause__, ValidationError)


def test_historical_methods_keep_invoice_data_return_and_dict_envelope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    agent = make_agent()
    monkeypatch.setattr(
        agent,
        "structurer_document_en_json",
        lambda _path: make_invoice_data().model_dump(mode="python"),
    )
    monkeypatch.setattr(
        invoice_agent.ollama,
        "chat",
        lambda **_kwargs: pytest.fail("Ollama must not be called in this test"),
    )
    source_file = tmp_path / "invoice.png"
    source_file.write_bytes(b"test")

    legacy_result = agent.traiter_facture(source_file)
    wrapped_result = agent.process_invoice(str(source_file))

    assert isinstance(legacy_result, InvoiceData)
    assert wrapped_result["status"] == "success"
    assert wrapped_result["data"]["fournisseur"] == "Legacy Supplier"
    assert "type_piece" not in wrapped_result["data"]
    assert all(
        path.resolve().is_relative_to(tmp_path.resolve())
        for path in tmp_path.rglob("*")
    )


def test_canonical_conversion_does_not_call_ollama_or_write_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        invoice_agent.ollama,
        "chat",
        lambda **_kwargs: pytest.fail("Ollama must not be called during conversion"),
    )

    invoice = make_agent().to_canonical_invoice(
        make_invoice_data(),
        document_id=DOCUMENT_ID,
        created_at=CREATED_AT,
        updated_at=UPDATED_AT,
    )

    assert isinstance(invoice, InvoiceSchema)
    assert list(tmp_path.iterdir()) == []


def test_canonical_adapter_validation_error_is_preserved_as_cause() -> None:
    invalid_legacy_data = make_invoice_data(fournisseur=" ")

    with pytest.raises(InvoiceAgentCanonicalValidationError) as error:
        make_agent().to_canonical_invoice(
            invalid_legacy_data,
            document_id=DOCUMENT_ID,
            created_at=CREATED_AT,
            updated_at=UPDATED_AT,
        )

    assert isinstance(error.value.__cause__, ValidationError)
