import json
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from agents.invoice_agent import InvoiceData as AgentInvoiceData
from schemas.invoice_schema import (
    InvoiceAmount,
    InvoiceData,
    InvoiceDirection,
    InvoiceDocumentType,
    InvoiceParty,
    InvoiceReference,
    InvoiceSchema,
    TypePiece,
    ValidationStatus,
)


DOCUMENT_ID = UUID("e11bd995-d041-43c8-8a12-1c5ef7bb71c4")
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def make_invoice(**overrides: object) -> InvoiceSchema:
    values: dict[str, object] = {
        "document_id": DOCUMENT_ID,
        "document_type": InvoiceDocumentType.INVOICE,
        "direction": InvoiceDirection.PURCHASE,
        "supplier": {"name": "Acme Ltd"},
        "reference": {
            "invoice_number": "INV-42",
            "issue_date": "2026-10-01",
            "due_date": "2026-10-31",
        },
        "amount": {
            "currency": "EUR",
            "net_amount": Decimal("100.00"),
            "tax_amount": Decimal("20.00"),
            "gross_amount": Decimal("120.00"),
        },
        "validation_status": ValidationStatus.PENDING,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return InvoiceSchema.model_validate(values)


def test_valid_purchase_invoice() -> None:
    invoice = make_invoice()

    assert invoice.direction is InvoiceDirection.PURCHASE
    assert invoice.supplier == InvoiceParty(name="Acme Ltd")


def test_valid_sale_invoice() -> None:
    invoice = make_invoice(
        direction=InvoiceDirection.SALE,
        supplier=None,
        customer={"name": "Customer Ltd"},
    )

    assert invoice.direction is InvoiceDirection.SALE
    assert invoice.customer == InvoiceParty(name="Customer Ltd")


def test_valid_credit_note_allows_positive_amounts() -> None:
    invoice = make_invoice(document_type=InvoiceDocumentType.CREDIT_NOTE)

    assert invoice.document_type is InvoiceDocumentType.CREDIT_NOTE
    assert invoice.amount.gross_amount == Decimal("120.00")


@pytest.mark.parametrize(
    "document_type",
    [InvoiceDocumentType.MEAL_RECEIPT, InvoiceDocumentType.FUEL_RECEIPT],
)
def test_valid_receipt_types(document_type: InvoiceDocumentType) -> None:
    invoice = make_invoice(document_type=document_type)

    assert invoice.document_type is document_type


def test_valid_siret_is_preserved() -> None:
    invoice = make_invoice(supplier={"name": "Acme Ltd", "siret": "12345678901234"})

    assert invoice.supplier.siret == "12345678901234"


def test_invalid_siret_is_rejected() -> None:
    with pytest.raises(ValidationError):
        InvoiceParty(name="Acme Ltd", siret="1234567890123X")


@pytest.mark.parametrize("currency", ["eur", "EU", "EURO", "ÉUR"])
def test_invalid_currency_is_rejected(currency: str) -> None:
    with pytest.raises(ValidationError):
        InvoiceAmount(currency=currency, gross_amount=Decimal("1"))


def test_negative_gross_amount_is_rejected() -> None:
    with pytest.raises(ValidationError):
        InvoiceAmount(gross_amount=Decimal("-0.01"))


@pytest.mark.parametrize("tolerance", [Decimal("0"), Decimal("-0.01")])
def test_non_positive_tolerance_is_rejected(tolerance: Decimal) -> None:
    with pytest.raises(ValidationError):
        InvoiceAmount(gross_amount=Decimal("1"), rounding_tolerance=tolerance)


def test_net_plus_tax_equal_gross_is_accepted() -> None:
    amount = InvoiceAmount(
        net_amount=Decimal("100"),
        tax_amount=Decimal("20"),
        gross_amount=Decimal("120"),
    )

    assert amount.gross_amount == Decimal("120")


def test_amount_difference_over_tolerance_is_rejected() -> None:
    with pytest.raises(ValidationError):
        InvoiceAmount(
            net_amount=Decimal("100"),
            tax_amount=Decimal("20"),
            gross_amount=Decimal("120.03"),
            rounding_tolerance=Decimal("0.02"),
        )


def test_amount_difference_within_tolerance_is_accepted() -> None:
    amount = InvoiceAmount(
        net_amount=Decimal("100"),
        tax_amount=Decimal("20"),
        gross_amount=Decimal("120.02"),
        rounding_tolerance=Decimal("0.02"),
    )

    assert amount.gross_amount == Decimal("120.02")


def test_due_date_before_issue_date_is_rejected() -> None:
    with pytest.raises(ValidationError):
        InvoiceReference(issue_date=date(2026, 10, 2), due_date=date(2026, 10, 1))


@pytest.mark.parametrize("score", [-0.01, 1.01])
def test_confidence_score_outside_range_is_rejected(score: float) -> None:
    with pytest.raises(ValidationError):
        make_invoice(confidence_score=score)


def test_purchase_without_supplier_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_invoice(supplier=None)


def test_sale_without_customer_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_invoice(direction=InvoiceDirection.SALE, supplier=None, customer=None)


def test_unknown_direction_allows_missing_parties() -> None:
    invoice = make_invoice(
        direction=InvoiceDirection.UNKNOWN,
        supplier=None,
        customer=None,
    )

    assert invoice.direction is InvoiceDirection.UNKNOWN
    assert invoice.supplier is None
    assert invoice.customer is None


def test_json_round_trip_preserves_invoice() -> None:
    original = make_invoice(metadata={"source": "ocr"})

    restored = InvoiceSchema.model_validate_json(original.model_dump_json())

    assert restored == original


def test_amounts_remain_decimal_without_float_arithmetic() -> None:
    amount = InvoiceAmount(
        net_amount=Decimal("0.10"),
        tax_amount=Decimal("0.20"),
        gross_amount=Decimal("0.30"),
    )
    restored = InvoiceAmount.model_validate_json(amount.model_dump_json())

    assert isinstance(restored.net_amount, Decimal)
    assert isinstance(restored.tax_amount, Decimal)
    assert isinstance(restored.gross_amount, Decimal)
    assert restored.net_amount == Decimal("0.10")


def test_default_lists_and_metadata_are_not_shared() -> None:
    first = make_invoice()
    second = make_invoice()
    first.extraction_warnings.append("warning")
    first.metadata["source"] = "test"

    assert second.extraction_warnings == []
    assert second.metadata == {}


def test_extra_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_invoice(unrecognized="value")


def test_existing_schema_invoice_data_conversion_uses_only_available_fields() -> None:
    legacy = InvoiceData(
        type_piece=TypePiece.AVOIR,
        fournisseur="Legacy Supplier",
        date_facture="2026-10-01",
        numero_facture="CN-10",
        montant_ht=100.0,
        montant_tva=20.0,
        montant_ttc=120.0,
        categorie="office",
        commentaires_controle=["legacy warning"],
    )

    converted = InvoiceSchema.from_invoice_data(
        legacy,
        document_id=DOCUMENT_ID,
        created_at=NOW,
        updated_at=NOW,
    )

    assert converted.document_type is InvoiceDocumentType.CREDIT_NOTE
    assert converted.direction is InvoiceDirection.UNKNOWN
    assert converted.supplier.name == "Legacy Supplier"
    assert converted.reference.invoice_number == "CN-10"
    assert converted.amount.net_amount == Decimal("100.0")
    assert converted.amount.tax_amount == Decimal("20.0")
    assert converted.amount.gross_amount == Decimal("120.0")
    assert converted.extraction_warnings == ["legacy warning"]


def test_agent_invoice_data_conversion_uses_only_available_fields() -> None:
    legacy = AgentInvoiceData(
        fournisseur="Agent Supplier",
        date_facture="2026-10-01",
        montant_ht=50.0,
        montant_tva=10.0,
        montant_ttc=60.0,
        numero_facture="AG-1",
        categorie="travel",
    )

    converted = InvoiceSchema.from_invoice_data(
        legacy,
        document_id=DOCUMENT_ID,
        created_at=NOW,
        updated_at=NOW,
    )

    assert converted.document_type is InvoiceDocumentType.UNKNOWN
    assert converted.direction is InvoiceDirection.UNKNOWN
    assert converted.supplier.name == "Agent Supplier"
    assert converted.reference.invoice_number == "AG-1"
    assert converted.category == "travel"
    assert converted.amount.gross_amount == Decimal("60.0")


def test_minimal_legacy_dict_does_not_invent_missing_values() -> None:
    converted = InvoiceSchema.from_legacy_dict(
        {
            "fournisseur": "Legacy Supplier",
            "date_facture": "2026-10-01",
            "montant_ttc": "12.34",
        },
        document_id=DOCUMENT_ID,
        created_at=NOW,
        updated_at=NOW,
    )

    assert converted.document_type is InvoiceDocumentType.UNKNOWN
    assert converted.direction is InvoiceDirection.UNKNOWN
    assert converted.validation_status is ValidationStatus.PENDING
    assert converted.supplier.name == "Legacy Supplier"
    assert converted.reference.issue_date == date(2026, 10, 1)
    assert converted.amount.net_amount is None
    assert converted.amount.tax_amount is None
    assert converted.amount.gross_amount == Decimal("12.34")


def test_updated_at_before_created_at_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_invoice(updated_at=datetime(2026, 10, 20, tzinfo=timezone.utc))


@pytest.fixture
def extraction_manager_payload() -> dict[str, object]:
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


@pytest.fixture
def invoice_agent_payload() -> dict[str, object]:
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


def test_extraction_manager_payload_is_preserved_and_dumpable(
    extraction_manager_payload: dict[str, object],
) -> None:
    invoice = InvoiceSchema.model_validate(extraction_manager_payload)
    dumped = invoice.model_dump()
    dumped_json = json.dumps(dumped)
    reread = InvoiceSchema.model_validate(json.loads(dumped_json))

    assert invoice.document_id is None
    assert invoice.created_at is None
    assert invoice.updated_at is None
    assert invoice.direction is InvoiceDirection.UNKNOWN
    assert invoice.supplier.name == "Extraction Supplier"
    assert invoice.customer.name == "Extraction Customer"
    assert invoice.reference.invoice_number == "EM-204"
    assert invoice.reference.issue_date == date(2026, 10, 1)
    assert invoice.lines[0].description == "Consulting"
    assert invoice.lines[0].quantite == Decimal("2.0")
    assert invoice.amount.net_amount == Decimal("100.0")
    assert invoice.amount.tax_amount == Decimal("20.0")
    assert invoice.amount.gross_amount == Decimal("120.0")
    assert reread == invoice
    assert set(dumped) >= {
        "supplier",
        "customer",
        "reference",
        "lines",
        "amount",
        "document_id",
        "created_at",
        "updated_at",
    }


def test_invoice_agent_payload_preserves_party_details_lines_and_totals(
    invoice_agent_payload: dict[str, object],
) -> None:
    invoice = InvoiceSchema.model_validate(invoice_agent_payload)

    assert invoice.document_id is None
    assert invoice.reference.issue_date == date(2026, 10, 2)
    assert invoice.reference.due_date == date(2026, 11, 1)
    assert invoice.reference.invoice_number == "IA-305"
    assert invoice.supplier.name == "Vision Supplier"
    assert invoice.supplier.vat_number == "FR12345678901"
    assert invoice.supplier.address == "1 Rue Exemple"
    assert invoice.customer.name == "Vision Customer"
    assert invoice.customer.address == "2 Rue Exemple"
    assert invoice.lines[0].description == "Materials"
    assert invoice.lines[0].total_ht == Decimal("120.0")
    assert invoice.amount.gross_amount == Decimal("144.0")
    assert invoice.model_dump()["amount"]["gross_amount"] == "144.0"


def test_actual_payload_amount_aliases_convert_to_canonical_decimals(
    extraction_manager_payload: dict[str, object],
) -> None:
    invoice = InvoiceSchema.model_validate(extraction_manager_payload)

    assert invoice.amount.net_amount == Decimal("100.0")
    assert invoice.amount.tax_amount == Decimal("20.0")
    assert invoice.amount.gross_amount == Decimal("120.0")
    assert isinstance(invoice.amount.net_amount, Decimal)
    assert isinstance(invoice.amount.tax_amount, Decimal)
    assert isinstance(invoice.amount.gross_amount, Decimal)


def test_actual_payload_unknown_business_field_is_still_rejected(
    extraction_manager_payload: dict[str, object],
) -> None:
    payload = dict(extraction_manager_payload)
    payload["unrecognized_business_value"] = "must not be ignored"

    with pytest.raises(ValidationError):
        InvoiceSchema.model_validate(payload)
