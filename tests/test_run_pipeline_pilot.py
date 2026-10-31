"""Tests for the pilot pipeline script corpus -> InvoiceSchema -> RuleRegistry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_pipeline_pilot import (
    DEFAULT_INVOICE,
    DEFAULT_RULES_DIR,
    REPO_ROOT,
    evaluate_tva_zero_rule,
    extract_raw_payload,
    run_pilot,
    validate_payload,
)

EMAUX_INVOICE = REPO_ROOT / "workspace" / "invoices" / "input" / "facture_emaux.pdf"


def _zero_vat_invoice(
    *,
    country: str,
    vat_number: str | None,
    mention_293b: bool = False,
    micro_entreprise: bool = False,
):
    payload = {
        "document_type": "INVOICE",
        "direction": "PURCHASE",
        "supplier": {
            "name": f"Supplier {country}",
            "vat_number": vat_number,
        },
        "reference": {"issue_date": "2026-09-30"},
        "amount": {
            "currency": "EUR",
            "net_amount": "100.00",
            "tax_amount": "0.00",
            "gross_amount": "100.00",
        },
        "metadata": {
            "supplier_country": country,
            "mention_293b": mention_293b,
            "micro_entreprise": micro_entreprise,
        },
    }
    return validate_payload(payload)


def test_extract_emaux_payload() -> None:
    if not EMAUX_INVOICE.is_file():
        pytest.skip("corpus facture_emaux.pdf absent")
    payload, text, _ = extract_raw_payload(EMAUX_INVOICE)

    assert payload["supplier"]["name"] == "Les Emaux de l'Atlantique"
    assert payload["supplier"]["siret"] == "40138136300019"
    assert payload["supplier"]["vat_number"] == "FR69401381363"
    assert payload["reference"]["invoice_number"] == "263075"
    assert payload["reference"]["issue_date"] == "2026-09-30"
    assert payload["amount"] == {
        "currency": "EUR",
        "net_amount": "1757.42",
        "tax_amount": "351.48",
        "gross_amount": "2108.90",
    }
    invoice = validate_payload(payload)
    assert invoice.amount.tax_amount is not None
    assert text


def test_pilot_validates_invoice_with_standard_vat() -> None:
    if not EMAUX_INVOICE.is_file():
        pytest.skip("corpus facture_emaux.pdf absent")
    result = run_pilot(EMAUX_INVOICE)

    assert result.invoice is not None
    assert result.validated is True
    assert result.decision.reason == "RULE_NOT_TRIGGERED"
    assert result.decision.rule_id == "RULE-TVA-ZERO-001"


def test_zero_vat_intracom_is_autoliquidated() -> None:
    invoice = _zero_vat_invoice(country="DE", vat_number="DE123456789")
    decision = evaluate_tva_zero_rule(invoice, DEFAULT_RULES_DIR)

    assert decision.status == "VALIDATED"
    assert decision.reason == "ZERO_VAT_DISAMBIGUATED"
    assert decision.vat_regime == "INTRACOM_AUTOLIQUIDATION"
    assert decision.include_in_vat_balance is True


def test_zero_vat_franchise_293b_is_out_of_vat_balance() -> None:
    invoice = _zero_vat_invoice(
        country="FR",
        vat_number="FR12345678901",
        mention_293b=True,
    )
    decision = evaluate_tva_zero_rule(invoice, DEFAULT_RULES_DIR)

    assert decision.status == "VALIDATED"
    assert decision.reason == "ZERO_VAT_DISAMBIGUATED"
    assert decision.vat_regime == "EXONERE_FRANCHISE_293B"
    assert decision.include_in_vat_balance is False


def test_zero_vat_unknown_regime_routes_to_hitl() -> None:
    invoice = _zero_vat_invoice(country="FR", vat_number="FR12345678901")
    decision = evaluate_tva_zero_rule(invoice, DEFAULT_RULES_DIR)

    assert decision.status == "HITL"
    assert decision.reason == "AMBIGUOUS_ZERO_VAT"
    assert any(
        "PERSIST_SUPPLIER_REGIME" in detail for detail in decision.details
    )


def test_non_approved_rule_is_reported_as_hitl(tmp_path: Path) -> None:
    (tmp_path / "RULE_TVA_ZERO_DISAMBIGUATION_001.md").write_text(
        "---\nrule_id: RULE-TVA-ZERO-001\nstatus: draft\n---\n# Draft\n",
        encoding="utf-8",
    )
    invoice = _zero_vat_invoice(
        country="DE",
        vat_number="DE123456789",
    )

    decision = evaluate_tva_zero_rule(invoice, tmp_path)

    assert decision.status == "HITL"
    assert decision.reason == "RULE_NOT_APPROVED"
    assert any("draft" in detail for detail in decision.details)


def test_invalid_payload_routes_to_hitl(tmp_path: Path) -> None:
    payload = {
        "document_type": "INVOICE",
        "direction": "PURCHASE",
        "supplier": {"name": "Supplier"},
        "amount": {"currency": "EUR", "net_amount": "100.00", "tax_amount": "0.00"},
    }
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    result = run_pilot(DEFAULT_INVOICE, payload_path=payload_path)

    assert result.invoice is None
    assert result.validated is False
    assert result.decision.status == "HITL"
    assert result.decision.reason == "INVALID_INVOICE_PAYLOAD"
