"""Pure French VAT / SIREN / SIRET consistency rules, independent of role."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

_SEPARATORS = re.compile(r"[ .\-/_]")
_FRENCH_VAT = re.compile(r"^FR[0-9]{11}$")
_SIREN = re.compile(r"^[0-9]{9}$")
_SIRET = re.compile(r"^[0-9]{14}$")


class FrenchVatAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    raw_value: str | None
    normalized_value: str | None
    is_french_format: bool
    extracted_siren: str | None
    provided_key: str | None
    expected_key: str | None
    key_valid: bool | None
    error_code: str | None


class IdentityConsistencyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    status: Literal[
        "CONSISTENT", "IDENTITY_CONFLICT", "INSUFFICIENT_EVIDENCE", "INVALID_INPUT"
    ]
    vat_analysis: FrenchVatAnalysis
    normalized_explicit_siren: str | None
    normalized_siret: str | None
    siren_from_siret: str | None
    conflict_codes: list[str]
    evidence_codes: list[str]


def _strip(value: str) -> str:
    return _SEPARATORS.sub("", value)


def analyze_french_vat_number(value: str | None) -> FrenchVatAnalysis:
    if value is None or not _strip(value):
        return FrenchVatAnalysis(
            raw_value=value,
            normalized_value=None,
            is_french_format=False,
            extracted_siren=None,
            provided_key=None,
            expected_key=None,
            key_valid=None,
            error_code="VAT_VALUE_MISSING",
        )
    normalized = _strip(value).upper()
    if not normalized.startswith("FR"):
        return FrenchVatAnalysis(
            raw_value=value,
            normalized_value=normalized,
            is_french_format=False,
            extracted_siren=None,
            provided_key=None,
            expected_key=None,
            key_valid=None,
            error_code="NOT_FRENCH_VAT",
        )
    if not _FRENCH_VAT.match(normalized):
        return FrenchVatAnalysis(
            raw_value=value,
            normalized_value=normalized,
            is_french_format=False,
            extracted_siren=None,
            provided_key=None,
            expected_key=None,
            key_valid=False,
            error_code="INVALID_FRENCH_VAT_FORMAT",
        )
    provided_key = normalized[2:4]
    siren = normalized[-9:]
    expected_key = str((12 + 3 * (int(siren) % 97)) % 97).zfill(2)
    valid = expected_key == provided_key
    return FrenchVatAnalysis(
        raw_value=value,
        normalized_value=normalized,
        is_french_format=True,
        extracted_siren=siren,
        provided_key=provided_key,
        expected_key=expected_key,
        key_valid=valid,
        error_code=None if valid else "INVALID_FRENCH_VAT_KEY",
    )


def check_french_identity_consistency(
    vat_number: str | None,
    explicit_siren: str | None,
    siret: str | None,
) -> IdentityConsistencyResult:
    vat = analyze_french_vat_number(vat_number)
    invalid: list[str] = []
    conflicts: list[str] = []
    evidence: list[str] = []

    if vat.error_code in ("INVALID_FRENCH_VAT_FORMAT", "INVALID_FRENCH_VAT_KEY"):
        invalid.append(vat.error_code)

    siren: str | None = None
    if explicit_siren is not None and _strip(explicit_siren):
        candidate = _strip(explicit_siren)
        if _SIREN.match(candidate):
            siren = candidate
        else:
            invalid.append("INVALID_SIREN_FORMAT")

    norm_siret: str | None = None
    siren_from_siret: str | None = None
    if siret is not None and _strip(siret):
        candidate = _strip(siret)
        if _SIRET.match(candidate):
            norm_siret = candidate
            siren_from_siret = candidate[:9]
        else:
            invalid.append("INVALID_SIRET_FORMAT")

    sources: dict[str, str] = {}
    if vat.is_french_format and vat.key_valid and vat.extracted_siren:
        sources["VAT"] = vat.extracted_siren
    if siren:
        sources["EXPLICIT_SIREN"] = siren
    if siren_from_siret:
        sources["SIRET"] = siren_from_siret

    pairs = [("VAT", "EXPLICIT_SIREN"), ("VAT", "SIRET"), ("EXPLICIT_SIREN", "SIRET")]
    for a, b in pairs:
        if a in sources and b in sources:
            if sources[a] == sources[b]:
                evidence.append(f"{a}_MATCHES_{b}")
            else:
                conflicts.append(f"{a}_MISMATCH_{b}")

    if invalid:
        status = "INVALID_INPUT"
    elif conflicts:
        status = "IDENTITY_CONFLICT"
    elif len(sources) >= 2:
        status = "CONSISTENT"
    else:
        status = "INSUFFICIENT_EVIDENCE"

    return IdentityConsistencyResult(
        status=status,
        vat_analysis=vat,
        normalized_explicit_siren=siren,
        normalized_siret=norm_siret,
        siren_from_siret=siren_from_siret,
        conflict_codes=invalid + conflicts,
        evidence_codes=evidence,
    )
