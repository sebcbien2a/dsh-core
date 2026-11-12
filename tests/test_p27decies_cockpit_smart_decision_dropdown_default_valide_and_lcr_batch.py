"""P27-decies - cockpit ergonomics and uncapped multi-client LCR remises.

The director's four regression points are enforced here:

1. a direct cockpit validation with a blank/``—`` decision never blocks and
   defaults to ``VALIDE`` while persisting exercise, category and sub-category;
2. known-tier and ``ARDOISE_*`` rows only expose the four lifecycle answers,
   while an unknown bank tier gets the grouped general-expense / internal /
   known-tier / creation selector;
3. a multi-client ``REMISE ENCAISSEMENT`` is matched by complete monthly client
   bundles (goods invoice + 144 EUR fixed invoice ± credit note), with no cap
   at four pieces (up to 15) and no skipped older invoice;
4. the readable workbook carries the same enriched dropdowns and resolved
   lines are removed.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from schemas.accounting_choices import (
    DECISION_CREATION_CHOICES,
    DECISION_GROUPS,
    DECISION_INTERNAL_CHOICES,
    DECISION_SIMPLE_CHOICES,
    normalize_decision,
)
from schemas.folder_state_schema import DocumentCategory, DocumentStatus
from services.acompleter_readable_service import (
    ReadableRow,
    write_readable_workbook,
)
from services.apd_control_panel import dashboard_html
from services.bank_reconciliation_service import (
    LCR_TRANSFER_ONLY_CLIENTS,
    MAX_REMISE_ENCAISSEMENT_PIECES,
    MAX_SUBSET_SUM_PIECES,
)
from services.cockpit_decision_service import (
    ARDOISE_PROFILE,
    BANK_UNKNOWN_PROFILE,
    decision_menu_for,
)
from services.customer_invoice_reconciliation import (
    ReconciliableInvoice,
    find_partial_remittance_combination,
    find_remittance_combination,
)
from services.hermes_accounting_bridge import HermesAccountingBridge
from services.sqlite_accounting_store import SqliteAccountingStore
from services.tier_reference_service import TierReferenceService

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TENANT = "APD"
TIER_REFERENCE = (
    REPO_ROOT / "workspace" / "document_flow" / TENANT / "REFERENTIEL_TIERS_APD.xlsx"
)


def _store(tmp_path: Path) -> SqliteAccountingStore:
    return SqliteAccountingStore(tmp_path / "accounting.sqlite3")


@pytest.fixture(autouse=True)
def isolated_learning_memory(tmp_path, monkeypatch):
    """Never let a unit test write the director's production memory file."""
    monkeypatch.setenv(
        "DSH_ACCOUNTING_MEMORY_PATH", str(tmp_path / "memory.json")
    )


def _bridge(tmp_path: Path, store: SqliteAccountingStore) -> HermesAccountingBridge:
    return HermesAccountingBridge(
        TENANT,
        store=store,
        root=tmp_path,
        export_dir=tmp_path / "exports",
    )


def _invoice(
    number: str,
    customer: str,
    amount: str,
    issued: date,
    *,
    credit_note: bool = False,
) -> ReconciliableInvoice:
    return ReconciliableInvoice(
        line_id=f"line-{number}",
        number=number,
        customer=customer,
        amount=Decimal(amount),
        due_date=None,
        payment_method="LCR",
        status="EN_ATTENTE_REGLEMENT",
        invoice_date=issued,
        is_credit_note=credit_note,
    )


# -- 1. decision menus -------------------------------------------------------


def test_blank_or_dash_decision_defaults_to_valide():
    assert normalize_decision("") == "VALIDE"
    assert normalize_decision("—") == "VALIDE"
    assert normalize_decision("-") == "VALIDE"
    assert normalize_decision("deja payee 2025") == "DEJA_PAYEE_2025"
    assert normalize_decision("reporter mois suivant") == "REPORTER_MOIS_SUIVANT"
    assert normalize_decision("impayee") == "IMPAYEE"


def test_known_tier_and_ardoise_only_expose_the_four_lifecycle_answers():
    reference = TierReferenceService(TIER_REFERENCE)
    reference.bootstrap(write_missing=False)
    for filename, supplier in (
        ("ARDOISE_FA16464.tier", "CARROSSERIE_VINOLIENNE"),
        ("FACTURE_CALANDRE.pdf", "CALANDRE_18"),
        ("FACTURE_CHOLLET.pdf", "CHOLLET"),
        ("FACTURE_SPPV.pdf", "SPPV"),
        ("FACTURE_GP_AUTO.pdf", "GP_AUTO_36_DP2C"),
    ):
        menu = decision_menu_for(
            filename, supplier, "PIECE_PDF", reference
        )
        assert menu.profile in {"ARDOISE", "TIERS_CONNU"}
        assert menu.choices == DECISION_SIMPLE_CHOICES
        assert "CREER_NOUVEAU_FOURNISSEUR" not in menu.choices
        assert "CREER_NOUVEAU_CLIENT" not in menu.choices
        assert not any(choice.startswith("GROUPE:") for choice in menu.choices)
    ardoise = decision_menu_for(
        "ARDOISE_FA16464.tier", "CARROSSERIE_VINOLIENNE", "PIECE_PDF", reference
    )
    assert ardoise.profile == ARDOISE_PROFILE


def test_unknown_bank_tier_gets_groups_internal_known_tiers_and_creation():
    reference = TierReferenceService(TIER_REFERENCE)
    reference.bootstrap(write_missing=False)
    for filename in (
        "TIER__RESTO INCONNU.tier",
        "INSTRUMENT__OFX-20260107030626515796145051394520260107.tier",
    ):
        menu = decision_menu_for(
            filename, "TIERS_INCONNU", "BANQUE_OFX", reference
        )
        assert menu.profile == BANK_UNKNOWN_PROFILE
        for group in DECISION_GROUPS:
            assert group in menu.choices
        for internal in DECISION_INTERNAL_CHOICES:
            assert internal in menu.choices
        for tier in ("SPPV", "CALANDRE_18", "BOSCH_SIA", "CHOLLET"):
            assert tier in menu.choices
        for creation in DECISION_CREATION_CHOICES:
            assert creation in menu.choices


def test_cockpit_renders_a_decision_selector_without_legacy_alias_column():
    html = dashboard_html(pending_questions=[])
    assert 'data-role="decision"' in html or "selectOptions('decision'" in html
    assert "applyDecisionDefaults" in html
    assert "decisionTarget" in html
    assert 'data-role="tier_alias"' not in html


# -- 2. direct validation ----------------------------------------------------


def test_direct_validation_without_decision_persists_exercise_category_and_subcategory(
    tmp_path,
):
    store = _store(tmp_path)
    store.force_upsert_document_line(
        TENANT,
        "FA90001-line",
        {
            "operation_date": "2025-11-30",
            "label": "Facture FA90001",
            "counterparty": "CALANDRE_18",
            "invoice_id": "FA90001",
            "status": "EN_ATTENTE_REGLEMENT",
            "category": DocumentCategory.VENTES_CLIENTS.value,
            "amount_ttc": "123.45",
            "document_type": "CUSTOMER_INVOICE",
            "reconciliation_status": "EN_ATTENTE_REGLEMENT",
        },
    )
    bridge = _bridge(tmp_path, store)
    result = bridge.decide_document(
        "FA90001-line",
        "",
        arbitration={
            "category": DocumentCategory.VENTES_CLIENTS.value,
            "expense_subcategory": "RESTAURATION",
            "fiscal_year_target": "2025",
            "payment_source": "BANQUE_PRO",
        },
    )
    assert result["status"] == DocumentStatus.VALIDE.value
    line = store.get_line(TENANT, "FA90001-line")
    assert line is not None
    assert line["status"] == DocumentStatus.VALIDE.value
    assert line["reconciliation_status"] == "VALIDE"
    assert line["category"] == DocumentCategory.VENTES_CLIENTS.value
    assert line["expense_subcategory"] == "RESTAURATION"
    assert line["exercise_year"] == "2025"
    assert line["fiscal_year_target"] == "2025"


def test_ardoise_lifecycle_decisions_are_persisted(tmp_path):
    store = _store(tmp_path)
    for invoice_id in ("FA91001", "FA91002"):
        store.force_upsert_document_line(
            TENANT,
            f"{invoice_id}-line",
            {
                "operation_date": "2026-01-31",
                "label": f"Facture {invoice_id}",
                "counterparty": "CALANDRE_18",
                "invoice_id": invoice_id,
                "status": "EN_ATTENTE_REGLEMENT",
                "category": DocumentCategory.VENTES_CLIENTS.value,
                "amount_ttc": "100.00",
                "document_type": "CUSTOMER_INVOICE",
                "reconciliation_status": "EN_ATTENTE_REGLEMENT",
            },
        )
    bridge = _bridge(tmp_path, store)
    bridge.decide_document(
        "ARDOISE_FA91001.tier",
        "DEJA_PAYEE_2025",
        arbitration={
            "category": DocumentCategory.VENTES_CLIENTS.value,
            "fiscal_year_target": "2025",
            "payment_source": "BANQUE_PRO",
            "tier_alias_target": "DEJA_PAYEE_2025",
        },
    )
    line = store.get_line(TENANT, "FA91001-line")
    assert line["status"] == DocumentStatus.VALIDE.value
    assert line["reconciliation_status"] == "DEJA_PAYEE_2025"
    assert line["balance_due"] == "0.00"
    assert line["exercise_year"] == "2025"

    bridge.decide_document(
        "ARDOISE_FA91002.tier",
        "REPORTER_MOIS_SUIVANT",
        arbitration={
            "category": DocumentCategory.VENTES_CLIENTS.value,
            "fiscal_year_target": "2026",
            "payment_source": "BANQUE_PRO",
            "tier_alias_target": "REPORTER_MOIS_SUIVANT",
        },
    )
    line = store.get_line(TENANT, "FA91002-line")
    assert line["status"] == DocumentStatus.EN_ATTENTE.value
    assert line["report"] == "OUI"


def test_group_decision_assigns_generic_supplier_category_and_subcategory(tmp_path):
    store = _store(tmp_path)
    store.force_upsert_document_line(
        TENANT,
        "OFX-RESTO",
        {
            "operation_date": "2026-04-10",
            "label": "CB RESTO INCONNU FACT 100426",
            "counterparty": "RESTO INCONNU",
            "status": "EN_ATTENTE",
            "category": DocumentCategory.DIVERS_A_CLASSER.value,
            "debit_amount": "42.00",
            "document_type": "OFX",
        },
    )
    bridge = _bridge(tmp_path, store)
    result = bridge.decide_document(
        "OFX-RESTO",
        "GROUPE:RESTAURANTS_REPAS",
        arbitration={
            "fiscal_year_target": "2026",
            "payment_source": "BANQUE_PRO",
            "tier_alias_target": "GROUPE:RESTAURANTS_REPAS",
        },
    )
    assert result["status"] == DocumentStatus.VALIDE.value
    line = store.get_line(TENANT, "OFX-RESTO")
    assert line["counterparty"] == "RESTAURANTS_REPAS"
    assert line["category"] == DocumentCategory.FRAIS_GENERAUX.value
    assert line["expense_subcategory"] == "RESTAURATION"
    assert line["exercise_year"] == "2026"


# -- 3. multi-client LCR remises --------------------------------------------


def test_lcr_january_remise_90949_frees_sppv_fa16400_without_yucel():
    invoices = [
        _invoice("FA16400", "SPPV", "4922.06", date(2025, 11, 28)),
        _invoice("FA16402", "CHOLLET", "2596.99", date(2025, 11, 28)),
        _invoice("FA16403", "CHOLLET", "144.00", date(2025, 11, 28)),
        _invoice("FA16405", "CARS_18", "140.62", date(2025, 11, 30)),
        _invoice("FA16406", "CARROSSERIE_YUCEL", "2013.32", date(2025, 11, 30)),
        _invoice("FA16407", "GP_AUTO_36_DP2C", "1400.92", date(2025, 11, 30)),
        _invoice("FA16408", "PAINTCAR", "2738.05", date(2025, 11, 30)),
        _invoice("FA16409", "CALANDRE_18", "1084.94", date(2025, 11, 30)),
        _invoice("FA16410", "CALANDRE_18", "144.00", date(2025, 11, 30)),
        _invoice("FA16411", "SOLFA_CARBURANT", "123.10", date(2025, 11, 30)),
        _invoice("FA16412", "YDES_AUTO_ACCESSOIRES", "5610.53", date(2025, 11, 30)),
    ]
    selected = find_partial_remittance_combination(
        invoices,
        Decimal("18844.85"),
        payment_date=date(2026, 1, 7),
        maturity_date=date(2026, 1, 7),
    )
    assert selected is not None
    numbers = {item.number for item in selected}
    assert "FA16400" in numbers
    assert "FA16406" not in numbers, "Yucel is paid exclusively by transfer"
    assert "FA16412" not in numbers, "the missing 60.36 EUR avoir blocks Ydes"
    assert sum((item.amount for item in selected), Decimal("0.00")) == Decimal(
        "13294.68"
    )
    assert len(selected) == 9
    assert "CARROSSERIE_YUCEL" in LCR_TRANSFER_ONLY_CLIENTS


def test_lcr_february_remise_91374_keeps_monthly_pairs_together():
    invoices = [
        _invoice("FA16414", "CALANDRE_18", "2420.52", date(2025, 12, 31)),
        _invoice("FA16418", "CALANDRE_18", "144.00", date(2025, 12, 31)),
        _invoice("FA16415", "CARS_18", "140.62", date(2025, 12, 31)),
        _invoice("FA16416", "CHOLLET", "1468.22", date(2025, 12, 31)),
        _invoice("FA16417", "CHOLLET", "144.00", date(2025, 12, 31)),
        _invoice("FA16419", "GP_AUTO_NEVERS", "2623.93", date(2025, 12, 31)),
        _invoice("FA16420", "GP_AUTO_36_DP2C", "543.36", date(2025, 12, 31)),
    ]
    selected = find_remittance_combination(
        invoices,
        Decimal("7484.65"),
        payment_date=date(2026, 2, 7),
        maturity_date=date(2026, 2, 7),
    )
    assert selected is not None
    numbers = {item.number for item in selected}
    assert numbers == {
        "FA16414",
        "FA16418",
        "FA16415",
        "FA16416",
        "FA16417",
        "FA16419",
        "FA16420",
    }
    assert {"FA16414", "FA16418"} <= numbers
    assert {"FA16416", "FA16417"} <= numbers
    assert len(selected) == 7


def test_lcr_march_remise_91787_frees_all_known_monthly_bundles():
    invoices = [
        _invoice("FA16421", "CHOLLET", "4293.23", date(2026, 1, 30)),
        _invoice("FA16422", "CHOLLET", "144.00", date(2026, 1, 30)),
        _invoice("FA16423", "CARROSSERIE_YUCEL", "950.76", date(2026, 1, 31)),
        _invoice("FA16424", "CALANDRE_18", "2077.56", date(2026, 1, 31)),
        _invoice("FA16425", "CALANDRE_18", "144.00", date(2026, 1, 31)),
        _invoice("FA16426", "CARS_18", "281.23", date(2026, 1, 31)),
        _invoice("FA16427", "CHRONOPOST", "2228.72", date(2026, 1, 31)),
        _invoice("FA16428", "GP_AUTO_36_DP2C", "699.82", date(2026, 1, 31)),
        _invoice("FA16429", "CARROSSERIE_CHANTEREAU_STEPHANO", "103.67", date(2026, 1, 31)),
        _invoice("FA16431", "SOLFA_CARBURANT", "344.74", date(2026, 1, 31)),
        _invoice("FA16432", "GP_AUTO_03100", "558.98", date(2026, 1, 31)),
        _invoice("FA16433", "TECHNI_WHEEL", "1324.20", date(2026, 1, 31)),
    ]
    selected = find_partial_remittance_combination(
        invoices,
        Decimal("17267.23"),
        payment_date=date(2026, 3, 10),
        maturity_date=date(2026, 3, 10),
    )
    assert selected is not None
    numbers = {item.number for item in selected}
    assert "FA16423" not in numbers
    assert {"FA16424", "FA16425"} <= numbers
    assert {"FA16421", "FA16422"} <= numbers
    assert len(selected) == 11
    assert sum((item.amount for item in selected), Decimal("0.00")) == Decimal(
        "12200.15"
    )


def test_remittance_allows_fifteen_pieces_but_never_sixteen():
    assert MAX_SUBSET_SUM_PIECES == 4
    assert MAX_REMISE_ENCAISSEMENT_PIECES == 15
    fifteen = [
        _invoice(f"FA9{index:04d}", f"CLIENT_{index}", "1.00", date(2026, 3, 31))
        for index in range(15)
    ]
    selected = find_remittance_combination(
        fifteen,
        Decimal("15.00"),
        payment_date=date(2026, 4, 30),
        maturity_date=date(2026, 4, 30),
    )
    assert selected is not None and len(selected) == 15

    sixteen = fifteen + [
        _invoice("FA9999", "CLIENT_16", "1.00", date(2026, 3, 31))
    ]
    assert (
        find_remittance_combination(
            sixteen,
            Decimal("16.00"),
            payment_date=date(2026, 4, 30),
            maturity_date=date(2026, 4, 30),
        )
        is None
    )


def test_no_older_invoice_is_skipped_before_a_newer_one():
    invoices = [
        _invoice("FA-JAN", "CLIENT_INVARIANT", "10.00", date(2026, 1, 31)),
        _invoice("FA-FEB", "CLIENT_INVARIANT", "20.00", date(2026, 2, 28)),
    ]
    # Both months are inside the 30-days-end-of-month window, but the January
    # bundle must be collected before the February one can be used.
    assert (
        find_remittance_combination(
            invoices,
            Decimal("20.00"),
            payment_date=date(2026, 3, 31),
            maturity_date=date(2026, 3, 31),
        )
        is None
    )


# -- 4. readable workbook ----------------------------------------------------


def test_readable_workbook_has_contextual_decision_lists_and_drops_resolved_rows(
    tmp_path,
):
    path = tmp_path / "02_A_COMPLETER" / "_A_COMPLETER.xlsx"
    rows = [
        ReadableRow(
            sha256="a" * 64,
            filename="ARDOISE_FA16464.tier",
            supplier="CARROSSERIE_VINOLIENNE",
            document_number="FA16464",
            document_date=date(2026, 3, 31),
            amount_ht=None,
            amount_tva=None,
            amount_ttc=Decimal("82.63"),
            category=DocumentCategory.VENTES_CLIENTS,
            reason="Facture non encaissée",
            action="DEJA_PAYEE_2025 / REPORTER_MOIS_SUIVANT / IMPAYEE",
        ),
        ReadableRow(
            sha256="b" * 64,
            filename="TIER__XYZ INCONNU 999.tier",
            supplier="XYZ INCONNU 999",
            document_number="CB XYZ",
            document_date=date(2026, 4, 10),
            amount_ht=None,
            amount_tva=None,
            amount_ttc=Decimal("42.00"),
            category=DocumentCategory.DIVERS_A_CLASSER,
            reason="Tiers inconnu",
            action="Choisir un rattachement",
            extra={"payment_source": "BANQUE_PRO", "report": "NON"},
        ),
    ]
    written = write_readable_workbook(path, rows)
    workbook = load_workbook(written)
    try:
        sheet = workbook.active
        formulas = {
            str(validation.sqref): str(validation.formula1)
            for validation in sheet.data_validations.dataValidation
            if str(validation.sqref).startswith("M")
        }
        assert formulas
        assert "DECISION_SIMPLE" in formulas.values()
        assert "DECISION_BANQUE_INCONNUE" in formulas.values()
        lists = workbook["LISTES_DECISIONS"]
        simple = [
            lists.cell(row=index, column=1).value
            for index in range(1, len(DECISION_SIMPLE_CHOICES) + 1)
        ]
        assert tuple(simple) == DECISION_SIMPLE_CHOICES
        full = {
            lists.cell(row=index, column=2).value
            for index in range(1, lists.max_row + 1)
        }
        assert "GROUPE:RESTAURANTS_REPAS" in full
        assert "PAIEMENT_INTERNE_SOCIETE" in full
        assert "SPPV" in full
        assert "BOSCH_SIA" in full
        assert "CREER_NOUVEAU_FOURNISSEUR" in full
    finally:
        workbook.close()

    # A resolved line is not re-added by the next readable rebuild.
    rewritten = write_readable_workbook(path, [])
    workbook = load_workbook(rewritten)
    try:
        assert workbook.active.max_row == 1
    finally:
        workbook.close()