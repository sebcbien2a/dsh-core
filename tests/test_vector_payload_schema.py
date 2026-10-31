"""Contract tests for the versioned invoice vector payload."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.vector_payload_schema import InvoiceVectorPayloadV1

SHA = "a" * 64


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "contract_version": "1.0.1",
        "tenant_id": "TENANT-ALPHA",
        "source_sha256": SHA,
        "doc_type": "INVOICE",
        "invoice_id": "INV-1",
        "company_id": "COMPANY-1",
        "supplier_id": "SUP-1",
        "invoice_number": "F-2026-001",
        "currency": "EUR",
        "direction": "PURCHASE",
        "document_type": "INVOICE",
        "validation_status": "VALIDATED",
        "rule": {"rule_id": "RULE-TVA-ZERO-001"},
        "text": "Facture F-2026-001",
    }
    payload.update(overrides)
    return payload


def test_valid_payload_roundtrip() -> None:
    model = InvoiceVectorPayloadV1.model_validate(_payload())

    assert model.contract_version == "1.0.1"
    assert model.tenant_id == "TENANT-ALPHA"
    assert model.source_sha256 == SHA
    assert model.doc_type == "INVOICE"


@pytest.mark.parametrize(
    "tenant_id",
    ["", "   ", "bad tenant", "tenant/slash", "x" * 65, "téé"],
)
def test_rejects_invalid_tenant_id(tenant_id: str) -> None:
    with pytest.raises(ValidationError):
        InvoiceVectorPayloadV1.model_validate(_payload(tenant_id=tenant_id))


def test_rejects_missing_tenant_id() -> None:
    payload = _payload()
    payload.pop("tenant_id")

    with pytest.raises(ValidationError):
        InvoiceVectorPayloadV1.model_validate(payload)


@pytest.mark.parametrize(
    "source_sha256",
    ["", "A" * 64, "a" * 63, "g" * 64, "not-a-sha"],
)
def test_rejects_invalid_source_sha256(source_sha256: str) -> None:
    with pytest.raises(ValidationError):
        InvoiceVectorPayloadV1.model_validate(
            _payload(source_sha256=source_sha256)
        )


def test_rejects_missing_source_sha256() -> None:
    payload = _payload()
    payload.pop("source_sha256")

    with pytest.raises(ValidationError):
        InvoiceVectorPayloadV1.model_validate(payload)


@pytest.mark.parametrize("contract_version", ["1.0.0", "2.0.0", "1.0.1-rc1"])
def test_rejects_wrong_contract_version(contract_version: str) -> None:
    with pytest.raises(ValidationError):
        InvoiceVectorPayloadV1.model_validate(
            _payload(contract_version=contract_version)
        )


def test_rejects_missing_contract_version() -> None:
    payload = _payload()
    payload.pop("contract_version")

    with pytest.raises(ValidationError):
        InvoiceVectorPayloadV1.model_validate(payload)


def test_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        InvoiceVectorPayloadV1.model_validate(
            _payload(legacy_company_id="LEGACY")
        )


@pytest.mark.parametrize("doc_type", ["", "   "])
def test_rejects_blank_doc_type(doc_type: str) -> None:
    with pytest.raises(ValidationError):
        InvoiceVectorPayloadV1.model_validate(_payload(doc_type=doc_type))
