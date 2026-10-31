"""Strict, versioned contract for invoice vectors stored in Qdrant."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONTRACT_VERSION = "1.0.1"

_TENANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_SOURCE_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class InvoiceVectorPayloadV1(BaseModel):
    """Payload ``contract_version=1.0.1`` for the ``invoices`` collection."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1.0.1"]
    tenant_id: str
    source_sha256: str
    doc_type: str
    invoice_id: str
    point_id: str | None = None
    company_id: str
    supplier_id: str | None = None
    invoice_number: str | None = None
    party_name: str | None = None
    supplier_name: str | None = None
    customer_name: str | None = None
    issue_date: str | None = None
    net_amount: str | None = None
    tax_amount: str | None = None
    gross_amount: str | None = None
    currency: str
    direction: str
    document_type: str
    validation_status: str
    category: str | None = None
    rule: dict[str, Any] = Field(default_factory=dict)
    text: str

    @field_validator("tenant_id")
    @classmethod
    def validate_tenant_id(cls, value: str) -> str:
        if not _TENANT_ID_PATTERN.fullmatch(value):
            raise ValueError(
                "tenant_id must match [A-Za-z0-9_.:-]{1,64}"
            )
        return value

    @field_validator("source_sha256")
    @classmethod
    def validate_source_sha256(cls, value: str) -> str:
        if not _SOURCE_SHA256_PATTERN.fullmatch(value):
            raise ValueError("source_sha256 must be 64 lowercase hex characters")
        return value

    @field_validator("doc_type", "invoice_id", "company_id", "currency", "text")
    @classmethod
    def validate_non_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("string values must not be empty or whitespace")
        return value

    @field_validator(
        "point_id",
        "supplier_id",
        "invoice_number",
        "party_name",
        "supplier_name",
        "customer_name",
        "category",
    )
    @classmethod
    def validate_optional_non_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("string values must not be empty or whitespace")
        return value


__all__ = ["CONTRACT_VERSION", "InvoiceVectorPayloadV1"]
