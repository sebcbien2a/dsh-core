"""Extensible supplier identity contract, independent from the invoice pipeline.

Nothing here computes amounts, deduces identifiers (SIREN from SIRET/VAT) or
merges suppliers by name. Identifier types and custom fields are open strings
and dictionary keys, so a new country or field needs no change to this module.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_COUNTRY = re.compile(r"^[A-Z]{2}$")
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    if not value.strip():
        raise ValueError("string values must not be empty or whitespace")
    return value.strip()


def _iso2(value: str | None) -> str | None:
    value = _clean(value)
    if value is None:
        return None
    value = value.upper()
    if not _COUNTRY.match(value):
        raise ValueError("country must be an ISO 3166-1 alpha-2 code")
    return value


def _sha256(value: str | None) -> str | None:
    if value is not None and not _SHA256.match(value):
        raise ValueError("source_sha256 must be 64 lowercase hexadecimal characters")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FieldStatus(str, Enum):
    KNOWN = "known"
    ABSENT = "absent"
    UNREADABLE = "unreadable"
    UNVERIFIED = "unverified"


class FieldValue(_Strict):
    """Raw, normalized and corrected values are kept strictly separate."""

    raw: str | None = None
    normalized: str | None = None
    corrected: str | None = None
    status: FieldStatus = FieldStatus.UNVERIFIED
    human_confirmed: bool = False
    source_sha256: str | None = None
    justification: str | None = None

    _check_sha = field_validator("source_sha256")(_sha256)

    @model_validator(mode="after")
    def corrected_needs_raw_or_justification(self) -> "FieldValue":
        if self.corrected is not None:
            justified = self.human_confirmed and bool((self.justification or "").strip())
            if self.raw is None and not justified:
                raise ValueError(
                    "corrected value requires a raw value or a human-confirmed justification"
                )
        return self


class SupplierIdentifier(_Strict):
    identifier_type: str
    scope: str = "other"
    country: str | None = None
    local_label: str | None = None
    raw_value: str | None = None
    normalized_value: str | None = None
    corrected_value: str | None = None
    validation_status: str = "unverified"
    is_primary: bool = False
    human_confirmed: bool = False
    justification: str | None = None
    confirmed_at: datetime | None = None

    @field_validator("identifier_type")
    @classmethod
    def normalize_type(cls, value: str) -> str:
        return _clean(value).upper()  # type: ignore[union-attr]

    @field_validator("scope")
    @classmethod
    def normalize_scope(cls, value: str) -> str:
        return _clean(value).lower()  # type: ignore[union-attr]

    _check_country = field_validator("country")(lambda cls, v: _iso2(v))

    @field_validator(
        "local_label", "raw_value", "normalized_value", "corrected_value",
        "validation_status", "justification",
    )
    @classmethod
    def non_blank(cls, value: str | None) -> str | None:
        return _clean(value)

    @model_validator(mode="after")
    def validate_correction_and_primary(self) -> "SupplierIdentifier":
        if self.corrected_value is not None and self.raw_value is None:
            if not (
                self.human_confirmed
                and self.justification
                and self.confirmed_at is not None
            ):
                raise ValueError(
                    "corrected_value without raw_value requires human confirmation, "
                    "justification and confirmed_at"
                )
        if self.is_primary and not self.normalized_value:
            raise ValueError("a primary identifier requires normalized_value")
        return self

    @property
    def key(self) -> tuple[str, str | None]:
        return self.identifier_type, self.country


class SupplierAddress(_Strict):
    address_type: str = "registered"
    lines: list[str] = Field(default_factory=list)
    postal_code: str | None = None
    city: str | None = None
    region: str | None = None
    country: str | None = None
    human_confirmed: bool = False

    _check_country = field_validator("country")(lambda cls, v: _iso2(v))

    @field_validator("address_type")
    @classmethod
    def non_blank_type(cls, value: str) -> str:
        return _clean(value)  # type: ignore[return-value]


class PaymentProfile(_Strict):
    issue_date: date | None = None
    due_date: date | None = None
    payment_terms_label: str | None = None
    delay_days: int | None = Field(default=None, ge=0)
    calculation_rule: str | None = None
    payment_method: str | None = None
    human_confirmed: bool = False

    @model_validator(mode="after")
    def due_not_before_issue(self) -> "PaymentProfile":
        if self.issue_date and self.due_date and self.due_date < self.issue_date:
            raise ValueError("due_date must not be before issue_date")
        return self


class CustomFieldDefinition(_Strict):
    key: str
    label: str
    value_type: str = "string"
    required: bool = False
    country_scope: list[str] | None = None
    validation_pattern: str | None = None
    active: bool = True

    @field_validator("key")
    @classmethod
    def valid_key(cls, value: str) -> str:
        if not _KEY.match(value):
            raise ValueError("key must be lowercase snake_case")
        return value

    @field_validator("country_scope")
    @classmethod
    def valid_scope(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else [_iso2(item) for item in value]  # type: ignore[misc]

    @field_validator("validation_pattern")
    @classmethod
    def compilable_pattern(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError("validation_pattern is not a valid regular expression") from exc
        return value


class SupplierLayoutRule(_Strict):
    supplier_id: UUID
    field_key: str
    anchor_text: str
    relative_position: str
    source_sha256: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    human_confirmed: bool = False
    corrected_value: str | None = None
    confirmed_at: datetime | None = None
    version: int = Field(default=1, ge=1)
    active: bool = False

    _check_sha = field_validator("source_sha256")(_sha256)

    @model_validator(mode="after")
    def active_requires_human_confirmation(self) -> "SupplierLayoutRule":
        if self.active and not (
            self.human_confirmed
            and self.source_sha256 is not None
            and self.confirmed_at is not None
        ):
            raise ValueError(
                "an active layout rule requires human confirmation, valid source_sha256 "
                "and confirmed_at"
            )
        return self


class SupplierIdentity(_Strict):
    supplier_id: UUID = Field(default_factory=uuid4)
    legal_name: str
    printed_names: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    country: str | None = None
    identifiers: list[SupplierIdentifier] = Field(default_factory=list)
    addresses: list[SupplierAddress] = Field(default_factory=list)
    payment_profiles: list[PaymentProfile] = Field(default_factory=list)
    custom_fields: dict[str, FieldValue] = Field(default_factory=dict)
    layout_rules: list[SupplierLayoutRule] = Field(default_factory=list)
    active: bool = True

    _check_country = field_validator("country")(lambda cls, v: _iso2(v))

    @field_validator("legal_name")
    @classmethod
    def non_blank_name(cls, value: str) -> str:
        return _clean(value)  # type: ignore[return-value]

    @field_validator("printed_names", "aliases")
    @classmethod
    def clean_names(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = _clean(value)
            if text is not None and text.casefold() not in seen:
                seen.add(text.casefold())
                cleaned.append(text)
        return cleaned

    @field_validator("custom_fields")
    @classmethod
    def valid_custom_keys(cls, value: dict[str, FieldValue]) -> dict[str, FieldValue]:
        for key in value:
            if not _KEY.match(key):
                raise ValueError("custom field keys must be lowercase snake_case")
        return value

    @model_validator(mode="after")
    def check_consistency(self) -> "SupplierIdentity":
        primaries: set[tuple[str, str | None]] = set()
        for identifier in self.identifiers:
            if identifier.is_primary:
                if identifier.key in primaries:
                    raise ValueError("two primary identifiers share the same type and country")
                primaries.add(identifier.key)
        for rule in self.layout_rules:
            if rule.supplier_id != self.supplier_id:
                raise ValueError("layout rule belongs to another supplier_id")
        return self

    def all_names(self) -> list[str]:
        return [self.legal_name, *self.printed_names, *self.aliases]


def shared_identifiers(a: SupplierIdentity, b: SupplierIdentity) -> set[tuple[str, str | None, str]]:
    """Human-confirmed identifiers (type, country, normalized value) shared by both."""

    def keys(identity: SupplierIdentity) -> set[tuple[str, str | None, str]]:
        return {
            (item.identifier_type, item.country, item.normalized_value)
            for item in identity.identifiers
            if item.normalized_value is not None and item.human_confirmed
        }

    return keys(a) & keys(b)


def can_merge(a: SupplierIdentity, b: SupplierIdentity) -> bool:
    """Same internal id, or a shared identifier; never name similarity alone."""
    return a.supplier_id == b.supplier_id or bool(shared_identifiers(a, b))
