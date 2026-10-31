"""Tests for certified supplier memory: resolution, backfill and anti-corruption."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from backend.app.graphs.invoice_graph import run_invoice_graph
from schemas.invoice_schema import InvoiceSchema
from services.invoice_producer_protocol import normalise_country
from services.supplier_memory_service import enrich_invoice, resolve_supplier
from services.supplier_repository import SupplierRepository
from services.tva_zero_rule_evaluator import (
    DEFAULT_RULES_DIR,
    evaluate_tva_zero_rule,
)


def _invoice(
    *,
    name: str = "Transports SARL",
    siret: str | None = None,
    vat_number: str | None = None,
    country: str | None = None,
    tax_amount: str | None = "0.00",
) -> InvoiceSchema:
    supplier: dict[str, Any] = {"name": name}
    if siret is not None:
        supplier["siret"] = siret
    if vat_number is not None:
        supplier["vat_number"] = vat_number
    payload = {
        "document_type": "INVOICE",
        "direction": "PURCHASE",
        "supplier": supplier,
        "reference": {"invoice_number": "F-2026-001", "issue_date": "2026-09-30"},
        "amount": {
            "currency": "EUR",
            "net_amount": "100.00",
            "tax_amount": tax_amount,
            "gross_amount": "100.00",
        },
        "metadata": {},
    }
    if country is not None:
        payload["metadata"]["supplier_country"] = country
    return InvoiceSchema.model_validate(payload)


def _repository(
    tmp_path: Path, *entries: Mapping[str, Any]
) -> SupplierRepository:
    suppliers = {
        str(entry.get("siret") or entry.get("tva_intra")): dict(entry)
        for entry in entries
    }
    path = tmp_path / "suppliers.json"
    path.write_text(
        json.dumps(
            {
                "metadata": {
                    "version": "2.0",
                    "next_supplier_id": len(suppliers) + 1,
                },
                "suppliers": suppliers,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return SupplierRepository(str(path))


def _supplier(
    *,
    siret: str | None = "12345678900012",
    name: str = "CRISOL COLOR, S.L.",
    vat_number: str | None = "ESB17443482",
    country: str | None = "ES",
    vat_regime: str | None = "INTRACOM_AUTOLIQUIDATION",
    aliases: list[str] | None = None,
    supplier_id: str = "SUP_0001",
) -> dict[str, Any]:
    return {
        "supplier_id": supplier_id,
        "raison_sociale": name,
        "siret": siret,
        "tva_intra": vat_number,
        "country": country,
        "vat_regime": vat_regime,
        "aliases": aliases or [],
        "verification": {"status": "VERIFIED", "human_validations": 3},
        "status": "ACTIVE",
        "skills": [],
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("FRANCE", "FR"),
        ("France", "FR"),
        ("fr", "FR"),
        ("España", "ES"),
        ("SPAIN", "ES"),
        ("Allemagne", "DE"),
        ("DE", "DE"),
        ("13", None),
        ("", None),
        (None, None),
    ],
)
def test_normalise_country_iso2(raw: Any, expected: str | None) -> None:
    assert normalise_country(raw) == expected


def test_tax_amount_none_is_not_treated_as_zero_vat() -> None:
    invoice = _invoice(tax_amount=None)

    decision = evaluate_tva_zero_rule(invoice, DEFAULT_RULES_DIR)

    assert decision.status == "HITL"
    assert decision.reason == "TAX_AMOUNT_UNKNOWN"


def test_france_label_does_not_trigger_intracom_routing() -> None:
    invoice = _invoice(
        name="CHRONOPOST SAS",
        vat_number="FR67921781480",
        country="FRANCE",
    )

    decision = evaluate_tva_zero_rule(invoice, DEFAULT_RULES_DIR)

    assert decision.status == "HITL"
    assert decision.reason == "AMBIGUOUS_ZERO_VAT"


def test_spanish_label_triggers_intracom_routing() -> None:
    invoice = _invoice(
        name="CRISOL COLOR, S.L.",
        vat_number="ESB17443482",
        country="España",
    )

    decision = evaluate_tva_zero_rule(invoice, DEFAULT_RULES_DIR)

    assert decision.status == "VALIDATED"
    assert decision.reason == "ZERO_VAT_DISAMBIGUATED"
    assert decision.vat_regime == "INTRACOM_AUTOLIQUIDATION"


def test_resolution_by_siren(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        _supplier(
            siret="40138136300019",
            name="Les Emaux de l'Atlantique",
            vat_number="FR69401381363",
            country="FR",
            vat_regime=None,
        ),
    )
    invoice = _invoice(
        name="Les Emaux de l'Atlantique",
        siret="40138136300019",
    )

    match, attempts = resolve_supplier(repository, invoice)

    assert match is not None
    assert match.matched_by == "siren"
    assert attempts[0]["strategy"] == "siren"
    assert attempts[0]["matched"] is True


def test_resolution_by_vat_number(tmp_path: Path) -> None:
    repository = _repository(tmp_path, _supplier(vat_number="ESB17443482"))
    invoice = _invoice(
        name="Fournisseur espagnol",
        vat_number="ESB17443482",
    )

    match, attempts = resolve_supplier(repository, invoice)

    assert match is not None
    assert match.matched_by == "vat"
    assert attempts[0]["strategy"] == "vat"


def test_resolution_by_fuzzy_name_and_alias(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        _supplier(
            siret="99999999900012",
            name="ASSISTANCE PEINTURES DIFFUSION",
            vat_number=None,
            country="FR",
            vat_regime=None,
            aliases=["APD"],
        ),
    )
    invoice = _invoice(name="assistance peinture diffusion")

    match, attempts = resolve_supplier(repository, invoice)

    assert match is not None
    assert match.matched_by == "name"
    assert match.raison_sociale == "ASSISTANCE PEINTURES DIFFUSION"
    assert attempts[0]["strategy"] == "name"


def test_backfill_certified_identity_and_regime(tmp_path: Path) -> None:
    repository = _repository(tmp_path, _supplier())
    invoice = _invoice(name="CRISOL COLOR, S.L.")

    enriched, memory = enrich_invoice(invoice, repository)

    assert memory["matched"] is True
    assert memory["matched_by"] == "name"
    assert enriched.supplier is not None
    assert enriched.supplier.siret == "12345678900012"
    assert enriched.supplier.vat_number == "ESB17443482"
    assert enriched.metadata["supplier_country"] == "ES"
    assert enriched.metadata["vat_regime"] == "INTRACOM_AUTOLIQUIDATION"
    assert {"siret", "vat_number", "country", "vat_regime"} <= set(
        memory["backfilled"]
    )
    assert memory["conflicts"] == []

    decision = evaluate_tva_zero_rule(enriched, DEFAULT_RULES_DIR)

    assert decision.status == "VALIDATED"
    assert decision.reason == "SUPPLIER_REGIME_APPLIED"
    assert decision.vat_regime == "INTRACOM_AUTOLIQUIDATION"
    assert decision.include_in_vat_balance is True


def test_anti_corruption_keeps_certified_vat(tmp_path: Path) -> None:
    repository = _repository(tmp_path, _supplier())
    invoice = _invoice(
        name="CRISOL COLOR, S.L.",
        vat_number="FR11810908392",
        country="FR",
    )

    enriched, memory = enrich_invoice(invoice, repository)

    assert enriched.supplier is not None
    assert enriched.supplier.vat_number == "ESB17443482"
    assert enriched.metadata["supplier_country"] == "ES"
    assert memory["conflicts"]
    assert any("vat_number" in conflict for conflict in memory["conflicts"])
    assert any(
        warning.startswith("IDENTITY_CONFLICT:")
        for warning in enriched.extraction_warnings
    )

    decision = evaluate_tva_zero_rule(enriched, DEFAULT_RULES_DIR)

    assert decision.status == "VALIDATED"
    assert decision.vat_regime == "INTRACOM_AUTOLIQUIDATION"


def test_paintcar_alias_resolves_siretless_supplier(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        _supplier(
            siret=None,
            aliases=["Paintcar", "PAINTCAR"],
        ),
    )

    assert repository.find_by_tva("ESB17443482") is not None
    assert repository.find_by_name("Paintcar") is not None

    invoice = _invoice(name="Paintcar")
    match, attempts = resolve_supplier(repository, invoice)

    assert match is not None
    assert match.matched_by == "name"
    assert match.siret is None
    assert attempts[0]["strategy"] == "name"

    enriched, memory = enrich_invoice(invoice, repository)

    assert enriched.supplier is not None
    assert enriched.supplier.siret is None
    assert enriched.supplier.vat_number == "ESB17443482"
    assert enriched.metadata["supplier_country"] == "ES"
    assert enriched.metadata["vat_regime"] == "INTRACOM_AUTOLIQUIDATION"
    assert "siret" not in memory["backfilled"]
    assert {"vat_number", "country", "vat_regime"} <= set(memory["backfilled"])

    decision = evaluate_tva_zero_rule(enriched, DEFAULT_RULES_DIR)

    assert decision.status == "VALIDATED"
    assert decision.reason == "SUPPLIER_REGIME_APPLIED"
    assert decision.vat_regime == "INTRACOM_AUTOLIQUIDATION"


def test_graph_enriches_supplier_before_rule_evaluation(tmp_path: Path) -> None:
    pymupdf = pytest.importorskip("pymupdf")
    document = pymupdf.open()
    page = document.new_page()
    lines = [
        "CRISOL COLOR, S.L.",
        "12 rue des Lilas",
        "75001 Paris",
        "Facture N: P-2026-001",
        "Date: 14/09/2026",
        "Net HT 83,43 0,00 83,43",
    ]
    y = 72
    for line in lines:
        page.insert_text((72, y), line)
        y += 16
    pdf_path = tmp_path / "crisol.pdf"
    document.save(str(pdf_path))
    document.close()

    repository = _repository(tmp_path, _supplier())

    result = run_invoice_graph(
        pdf_path,
        persistence_manager=None,
        supplier_repository=repository,
        thread_id="supplier-memory",
    )

    assert result["supplier_memory"]["matched"] is True
    assert "invoice" in result and isinstance(result["invoice"], InvoiceSchema)
    assert result["invoice"].metadata["supplier_country"] == "ES"
    assert result["invoice"].supplier is not None
    assert result["invoice"].supplier.vat_number == "ESB17443482"
    assert result["rule_decision"].reason == "SUPPLIER_REGIME_APPLIED"
    assert result["rule_decision"].vat_regime == "INTRACOM_AUTOLIQUIDATION"
    assert result["commit_status"] == "COMMITTED"
