import pytest
from pydantic import ValidationError

from services.french_business_identity_rule import (
    FrenchVatAnalysis,
    analyze_french_vat_number,
    check_french_identity_consistency,
)

SIREN = "404833048"
KEY = str((12 + 3 * (int(SIREN) % 97)) % 97).zfill(2)
VAT = f"FR{KEY}{SIREN}"
SIRET = SIREN + "00012"


def test_valid_vat_with_separators_keeps_raw():
    raw = f" fr {KEY}.{SIREN[:3]}-{SIREN[3:]} "
    a = analyze_french_vat_number(raw)
    assert a.raw_value == raw
    assert a.normalized_value == VAT
    assert a.is_french_format and a.key_valid is True
    assert a.extracted_siren == SIREN
    assert a.provided_key == a.expected_key == KEY
    assert a.error_code is None


def test_expected_key_zero_padded():
    for n in range(100000000, 100000200):
        a = analyze_french_vat_number("FR00" + str(n))
        assert len(a.expected_key) == 2


def test_invalid_key():
    bad = "00" if KEY != "00" else "01"
    a = analyze_french_vat_number(f"FR{bad}{SIREN}")
    assert a.is_french_format and a.key_valid is False
    assert a.error_code == "INVALID_FRENCH_VAT_KEY"
    assert a.expected_key == KEY


@pytest.mark.parametrize("value", [None, "", "  ", " .-/_ "])
def test_missing(value):
    a = analyze_french_vat_number(value)
    assert a.error_code == "VAT_VALUE_MISSING"
    assert a.key_valid is None and not a.is_french_format
    assert a.extracted_siren is None


def test_foreign_vat_not_french():
    a = analyze_french_vat_number("DE123456789")
    assert a.error_code == "NOT_FRENCH_VAT"
    assert not a.is_french_format
    assert a.extracted_siren is None
    assert a.provided_key is None and a.expected_key is None
    assert a.key_valid is None


@pytest.mark.parametrize(
    "value", ["FR123", f"FR{KEY}{SIREN}0", "FRAB404833048", f"FR{KEY}40483304X"]
)
def test_invalid_french_format(value):
    a = analyze_french_vat_number(value)
    assert a.error_code == "INVALID_FRENCH_VAT_FORMAT"
    assert not a.is_french_format
    assert a.key_valid is False
    assert a.extracted_siren is None


def test_models_strict_and_frozen():
    a = analyze_french_vat_number(VAT)
    with pytest.raises(ValidationError):
        a.raw_value = "x"
    with pytest.raises(ValidationError):
        FrenchVatAnalysis(**{**a.model_dump(), "extra": 1})


def test_consistent_all_sources():
    r = check_french_identity_consistency(VAT, SIREN, SIRET)
    assert r.status == "CONSISTENT"
    assert r.siren_from_siret == SIREN
    assert r.normalized_siret == SIRET
    assert r.conflict_codes == []
    assert len(r.evidence_codes) == 3


def test_consistent_two_sources_with_separators():
    r = check_french_identity_consistency(None, "404 833 048", SIRET)
    assert r.status == "CONSISTENT"
    assert r.normalized_explicit_siren == SIREN


def test_vat_alone_insufficient():
    assert check_french_identity_consistency(VAT, None, None).status == "INSUFFICIENT_EVIDENCE"


def test_siret_alone_insufficient_no_vat_deduced():
    r = check_french_identity_consistency(None, None, SIRET)
    assert r.status == "INSUFFICIENT_EVIDENCE"
    assert r.vat_analysis.extracted_siren is None
    assert r.vat_analysis.normalized_value is None


def test_nothing_provided():
    assert check_french_identity_consistency(None, None, None).status == "INSUFFICIENT_EVIDENCE"


def test_conflict_vat_vs_siren():
    r = check_french_identity_consistency(VAT, "123456789", None)
    assert r.status == "IDENTITY_CONFLICT"
    assert "VAT_MISMATCH_EXPLICIT_SIREN" in r.conflict_codes


def test_conflict_siren_vs_siret():
    r = check_french_identity_consistency(None, "123456789", SIRET)
    assert r.status == "IDENTITY_CONFLICT"
    assert "EXPLICIT_SIREN_MISMATCH_SIRET" in r.conflict_codes


def test_conflict_vat_vs_siret():
    r = check_french_identity_consistency(VAT, None, "123456789" + "00012")
    assert r.status == "IDENTITY_CONFLICT"
    assert "VAT_MISMATCH_SIRET" in r.conflict_codes


def test_invalid_siren_and_siret_formats():
    r = check_french_identity_consistency(None, "12345", "123")
    assert r.status == "INVALID_INPUT"
    assert "INVALID_SIREN_FORMAT" in r.conflict_codes
    assert "INVALID_SIRET_FORMAT" in r.conflict_codes
    assert r.siren_from_siret is None


def test_invalid_french_vat_format_and_key():
    assert check_french_identity_consistency("FR12", SIREN, None).status == "INVALID_INPUT"
    bad = "00" if KEY != "00" else "01"
    r = check_french_identity_consistency(f"FR{bad}{SIREN}", SIREN, SIRET)
    assert r.status == "INVALID_INPUT"


def test_foreign_vat_ignored_not_invalid():
    r = check_french_identity_consistency("DE123456789", SIREN, SIRET)
    assert r.status == "CONSISTENT"
    assert r.vat_analysis.error_code == "NOT_FRENCH_VAT"


def test_foreign_vat_alone_insufficient():
    r = check_french_identity_consistency("DE123456789", None, None)
    assert r.status == "INSUFFICIENT_EVIDENCE"


def test_no_role_deduced_and_no_forbidden_imports():
    import services.french_business_identity_rule as m

    src = open(m.__file__, encoding="utf-8").read()
    for forbidden in ("neo4j", "requests", "urllib", "socket", "supplier_repository"):
        assert forbidden not in src
    assert "role" not in check_french_identity_consistency(VAT, None, None).model_dump()
