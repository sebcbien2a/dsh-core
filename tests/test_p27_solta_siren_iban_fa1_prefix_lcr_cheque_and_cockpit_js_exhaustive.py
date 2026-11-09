"""P27 - SOLTA SIREN/IBAN, FA1* prefix, LCR/cheque, OCR noise and cockpit JS."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

from schemas.folder_state_schema import DocumentCategory
from services.accounting_memory_learning_service import (
    AccountingMemoryLearningService,
)
from services.accounting_spreadsheet_export_service import (
    ClientLedgerExporter,
    _client_synthetic_payments,
    _normalize_payment_reference,
)
from services.apd_control_panel import dashboard_html, load_pending_questions
from services.apd_four_stage_pipeline import ApdFourStagePipeline
from services.onedrive_accounting_bridge_service import (
    EXPERT_DIR_NAME,
    PILOTAGE_DIR_NAME,
    SHARED_DIR_NAME,
    OneDriveAccountingBridge,
    resolve_onedrive_root,
)
from services.payment_instrument_reconciliation_service import (
    reconcile_payment_instruments,
)
from services.sqlite_accounting_store import SqliteAccountingStore
from services.tier_reference_service import (
    COLUMNS,
    INVALID_TIER_NOISE,
    TierReferenceService,
    is_client_invoice_reference,
    is_invalid_tier_noise,
    is_payment_instrument_label,
)

TENANT = "APD"


def make_store(tmp_path: Path) -> SqliteAccountingStore:
    return SqliteAccountingStore(tmp_path / "accounting.sqlite3")


def install_temp_tiers(tmp_path: Path, store: SqliteAccountingStore) -> TierReferenceService:
    memory = AccountingMemoryLearningService(tmp_path / "rules.json")
    tiers = TierReferenceService(
        tmp_path / "REFERENTIEL_TIERS_APD.xlsx",
        store=store,
        memory=memory,
        tenant_id=TENANT,
    )
    tiers.bootstrap()
    return tiers


def add_invoice(
    store: SqliteAccountingStore,
    invoice_id: str,
    *,
    day: str,
    ttc: str,
    counterparty: str,
    category: DocumentCategory = DocumentCategory.VENTES_CLIENTS,
    **extra: object,
) -> None:
    line = {
        "operation_date": day,
        "label": f"Facture {invoice_id} {counterparty}",
        "counterparty": counterparty,
        "invoice_id": invoice_id,
        "category": category.value,
        "document_type": "CUSTOMER_INVOICE"
        if category is DocumentCategory.VENTES_CLIENTS
        else "FACTURE",
        "amount_ht": str((Decimal(ttc) / Decimal("1.2")).quantize(Decimal("0.01"))),
        "amount_tva": str(
            (
                Decimal(ttc)
                - (Decimal(ttc) / Decimal("1.2")).quantize(Decimal("0.01"))
            ).quantize(Decimal("0.01"))
        ),
        "amount_ttc": ttc,
        "status": "VALIDE",
    }
    line.update(extra)
    store.upsert_document_line(TENANT, f"SALE-{invoice_id}", line)


def add_bank(
    store: SqliteAccountingStore,
    line_id: str,
    *,
    day: str,
    label: str,
    debit: str | None = None,
    credit: str | None = None,
    counterparty: str | None = None,
) -> None:
    store.add_line(
        TENANT,
        line_id,
        {
            "operation_date": day,
            "label": label,
            "counterparty": counterparty or label,
            "debit_amount": debit,
            "credit_amount": credit,
            "category": DocumentCategory.BANQUE_FINANCE.value,
            "document_type": "OFX",
            "status": "PENDING",
        },
    )


# -- 1. cockpit web ---------------------------------------------------------


def test_cockpit_p27_safe_json_apply_button_and_event_listeners():
    html = dashboard_html(pending_questions=[])
    assert "P27" in html
    assert "Appliquer mes modifications Excel" in html
    assert "/api/apply-excel" in html
    assert "window.INITIAL_PENDING" in html
    assert "addEventListener('click', () => showPending())" in html
    assert "addEventListener('click', () => showStatus())" in html
    assert "addEventListener('click', () => showAll())" in html
    assert "Exécution en cours..." in html
    # The inline JSON must never contain a raw closing script tag.
    start = html.index("window.INITIAL_PENDING = ")
    end = html.index(";</script>", start)
    payload = html[start + len("window.INITIAL_PENDING = ") : end]
    assert "</script>" not in payload
    json.loads(payload)


def test_cockpit_loads_pending_questions_without_click(tmp_path):
    store = make_store(tmp_path)
    tree = tmp_path / "APD"
    (tree / "02_A_COMPLETER").mkdir(parents=True, exist_ok=True)
    from services.acompleter_workbook_service import AcompleterWorkbook

    workbook = AcompleterWorkbook(tree)
    workbook.ensure_workbook()
    workbook.upsert(
        "b" * 64,
        "FACTURE_P27.pdf",
        [],
        DocumentCategory.FRAIS_GENERAUX,
    )
    questions = load_pending_questions(tenant_id=TENANT, root=tmp_path)
    assert any(q.get("filename") == "FACTURE_P27.pdf" for q in questions)
    html = dashboard_html(pending_questions=questions)
    assert "FACTURE_P27.pdf" in html


# -- 2. SIREN / SIRET / IBAN and new tiers ---------------------------------


def test_solta_siren_siret_iban_and_alias_matching(tmp_path):
    store = make_store(tmp_path)
    tiers = install_temp_tiers(tmp_path, store)
    assert tuple(cell.value for cell in load_workbook(tiers.path).active[1]) == COLUMNS
    solta = tiers.resolve("SOLTA_LR_COLOR")
    assert solta is not None
    assert solta.role == "FOURNISSEUR"
    assert solta.category == "ACHATS_MARCHANDISES"
    assert solta.siren == "327274155"
    assert solta.siret == "32727415500057"
    assert "FR7610096182390008817130172" in solta.iban
    for alias in (
        "SOLTA",
        "SOLTA GROUPE",
        "SOLTA SAS",
        "LR COLOR",
        "LRCOLOR3",
        "VITACOLOR",
        "VITAMARINE",
        "RESIHOM",
    ):
        assert tiers.canonical_name(alias) == "SOLTA_LR_COLOR"
    assert tiers.canonical_name("327274155") == "SOLTA_LR_COLOR"
    assert tiers.canonical_name("32727415500057") == "SOLTA_LR_COLOR"
    assert (
        tiers.canonical_name("FR76 1009 6182 3900 0881 7130 172")
        == "SOLTA_LR_COLOR"
    )
    assert tiers.canonical_name("COLOR STORE FRANCE") == "COLOR_STORE_FRANCE"
    assert tiers.canonical_name("COLOR STORE") == "COLOR_STORE_FRANCE"
    assert tiers.canonical_name("LE MARMARA") == "RESTAURANTS_REPAS"
    assert tiers.canonical_name("LE RELAI DE TRE") == "RESTAURANTS_REPAS"
    assert tiers.canonical_name("COMPAGNIE EIFFA") == "PEAGES_AUTOROUTES"
    assert tiers.canonical_name("EIFFAGE") == "PEAGES_AUTOROUTES"
    assert tiers.canonical_name("CHOLLET") == "CHOLLET"
    assert tiers.role_for("CHOLLET") == "CLIENT"


def test_solta_invoice_2601616_is_ingested(tmp_path):
    store = make_store(tmp_path)
    store.force_upsert_document_line(
        TENANT,
        "PURCHASE-2601616",
        {
            "operation_date": "2026-11-05",
            "label": "Facture 2601616 SOLTA / LR COLOR",
            "counterparty": "SOLTA_LR_COLOR",
            "invoice_id": "2601616",
            "category": DocumentCategory.ACHATS_MARCHANDISES.value,
            "document_type": "FACTURE",
            "amount_ht": "140.40",
            "amount_tva": "28.08",
            "amount_ttc": "168.48",
            "due_date": "2026-12-05",
            "status": "VALIDE",
        },
    )
    line = store.get_line(TENANT, "PURCHASE-2601616")
    assert line is not None
    assert line["amount_ht"] == "140.40"
    assert line["amount_tva"] == "28.08"
    assert line["amount_ttc"] == "168.48"
    assert line["due_date"] == "2026-12-05"


# -- 3. FA1* generalization -------------------------------------------------


def test_fa1_prefix_covers_fa15_to_fa19():
    for reference in ("FA15001", "FA16001", "FA17001", "FA18001", "FA19001"):
        assert is_client_invoice_reference(reference)
    assert is_client_invoice_reference("FA 17001")
    assert not is_client_invoice_reference("FA20001")
    assert not is_client_invoice_reference("ILA7CC61")


def test_synthetic_payment_uses_fa1_not_only_fa16(tmp_path):
    store = make_store(tmp_path)
    install_temp_tiers(tmp_path, store)
    add_invoice(
        store,
        "FA17001",
        day="2026-01-31",
        ttc="950.76",
        counterparty="Carrosserie Yucell",
        reconciliation_status="PAYEE_RAPPROCHEE",
        reconciliation_date="2026-07-03",
        reconciliation_reference="VIREMENT 20260703141545180335145058189420260703",
        reconciliation_amount="950.76",
        balance_due="0.00",
    )
    add_bank(
        store,
        "OFX-20260703141545180335145058189420260703",
        day="2026-07-03",
        label="VIR INST SARL CARROSSERIE YUCE",
        credit="950.76",
        counterparty="SARL CARROSSERIE YUCE",
    )
    path = ClientLedgerExporter(
        store,
        export_dir=tmp_path / "exports",
        strict_tiers=True,
        include_matched_pieces=True,
    ).export(TENANT)
    sheet = load_workbook(path)["CARROSSERIE_YUCEL"]
    rows = list(sheet.iter_rows(min_row=2, values_only=True))
    payments = [row for row in rows if row[1] == "PAIEMENT"]
    assert len(payments) == 1
    assert not any(row[3] == "Règlement lettré (registre)" for row in rows)


# -- 4. LCR / cheque handling ----------------------------------------------


def test_payment_instrument_labels_are_never_tiers():
    for label in (
        "LCR 806255 RLV 00000385",
        "RLV 00000385",
        "CHEQUE N 0000412",
        "CHQ 0000412",
        "REMISE ENCAISSEMENT N 91374",
    ):
        assert is_payment_instrument_label(label)
        assert is_invalid_tier_noise(label)
    assert not is_payment_instrument_label("CARROSSERIE YUCEL")


def test_lcr_matches_exact_invoice_and_assigns_real_tier(tmp_path):
    store = make_store(tmp_path)
    install_temp_tiers(tmp_path, store)
    add_invoice(
        store,
        "2601616",
        day="2026-11-05",
        ttc="168.48",
        counterparty="SOLTA_LR_COLOR",
        category=DocumentCategory.ACHATS_MARCHANDISES,
    )
    add_bank(
        store,
        "OFX-LCR-1",
        day="2026-11-10",
        label="LCR 806255 RLV 00000385",
        debit="168.48",
    )
    report = reconcile_payment_instruments(store, TENANT)
    assert len(report.matched) == 1
    match = report.matched[0]
    assert match.invoice_ids == ("2601616",)
    assert match.counterparty == "SOLTA_LR_COLOR"
    bank = store.get_line(TENANT, "OFX-LCR-1")
    assert bank is not None
    assert bank["counterparty"] == "SOLTA_LR_COLOR"
    assert bank["reconciliation_status"] == "PAYEE_RAPPROCHEE"
    invoice = store.get_line(TENANT, "SALE-2601616")
    assert invoice is not None
    assert invoice["reconciliation_status"] == "PAYEE_RAPPROCHEE"


def test_lcr_without_invoice_is_reported_with_real_date_and_amount(tmp_path):
    store = make_store(tmp_path)
    install_temp_tiers(tmp_path, store)
    add_bank(
        store,
        "OFX-CHEQUE-1",
        day="2026-01-27",
        label="CHEQUE N 0000399",
        debit="2644.69",
    )
    report = reconcile_payment_instruments(store, TENANT)
    assert not report.matched
    assert len(report.unmatched) == 1
    unmatched = report.unmatched[0]
    assert unmatched.operation_date == "2026-01-27"
    assert unmatched.label == "CHEQUE N 0000399"
    assert unmatched.amount == Decimal("2644.69")


def test_lcr_never_combines_two_positive_invoices(tmp_path):
    store = make_store(tmp_path)
    install_temp_tiers(tmp_path, store)
    add_invoice(
        store,
        "P-1",
        day="2026-05-01",
        ttc="100.00",
        counterparty="PAINTCAR",
        category=DocumentCategory.ACHATS_MARCHANDISES,
    )
    add_invoice(
        store,
        "P-2",
        day="2026-05-01",
        ttc="50.00",
        counterparty="PAINTCAR",
        category=DocumentCategory.ACHATS_MARCHANDISES,
    )
    add_invoice(
        store,
        "P-3",
        day="2026-05-01",
        ttc="30.00",
        counterparty="QREFINISH",
        category=DocumentCategory.ACHATS_MARCHANDISES,
    )
    add_bank(
        store,
        "OFX-LCR-2",
        day="2026-05-02",
        label="LCR 754189 RLV 00000376",
        debit="150.00",
    )
    report = reconcile_payment_instruments(store, TENANT)
    assert not report.matched
    assert len(report.unmatched) == 1


def test_lcr_matches_one_invoice_minus_one_credit_note(tmp_path):
    store = make_store(tmp_path)
    install_temp_tiers(tmp_path, store)
    add_invoice(
        store,
        "P-1",
        day="2026-05-01",
        ttc="200.00",
        counterparty="PAINTCAR",
        category=DocumentCategory.ACHATS_MARCHANDISES,
    )
    add_invoice(
        store,
        "A-1",
        day="2026-05-01",
        ttc="-50.00",
        counterparty="PAINTCAR",
        category=DocumentCategory.ACHATS_MARCHANDISES,
        document_type="AVOIR",
    )
    add_bank(
        store,
        "OFX-LCR-3",
        day="2026-05-02",
        label="LCR 754190 RLV 00000377",
        debit="150.00",
    )
    report = reconcile_payment_instruments(store, TENANT)
    assert len(report.matched) == 1
    assert set(report.matched[0].invoice_ids) == {"P-1", "A-1"}
    assert report.matched[0].counterparty == "PAINTCAR"


# -- 5. OCR noise and mandatory date/TTC -----------------------------------


def test_invalid_tier_noise_is_filtered():
    for word in INVALID_TIER_NOISE:
        assert is_invalid_tier_noise(word)
    assert is_invalid_tier_noise("LCR 806255 RLV 00000385")
    assert is_invalid_tier_noise("CHEQUE N 0000412")
    assert not is_invalid_tier_noise("CARROSSERIE YUCEL")


def test_unknown_tier_row_has_date_amount_and_clean_reason(tmp_path):
    store = make_store(tmp_path)
    install_temp_tiers(tmp_path, store)
    add_bank(
        store,
        "OFX-UNKNOWN-1",
        day="2026-03-15",
        label="VIR SEPA NOUVEAU FOURNISSEUR",
        debit="123.45",
        counterparty="NOUVEAU FOURNISSEUR",
    )
    pipeline = ApdFourStagePipeline(
        TENANT,
        store=store,
        root=tmp_path,
        export_dir=tmp_path / "exports",
        year=2026,
        enable_tier_workflow=True,
    )
    rows = pipeline._readable_rows()
    unknown = next(
        row for row in rows if row.filename == "TIER__NOUVEAU FOURNISSEUR.tier"
    )
    assert unknown.document_date is not None
    assert unknown.amount_ttc == Decimal("123.45")
    assert "référentiel verrouillé" in unknown.reason
    assert "r?f?rentiel" not in unknown.reason


# -- 6. alias target inherits category -------------------------------------


def test_alias_target_inherits_role_category_and_subcategory(tmp_path):
    store = make_store(tmp_path)
    tiers = install_temp_tiers(tmp_path, store)
    add_bank(
        store,
        "OFX-ALIAS-1",
        day="2026-03-15",
        label="VIR SEPA LE MARMARA",
        debit="42.00",
        counterparty="LE MARMARA",
    )
    tiers.learn_alias("LE MARMARA", "RESTAURANTS_REPAS")
    pipeline = ApdFourStagePipeline(
        TENANT,
        store=store,
        root=tmp_path,
        export_dir=tmp_path / "exports",
        year=2026,
        enable_tier_workflow=True,
    )
    pipeline._apply_tier_reference()
    line = store.get_line(TENANT, "OFX-ALIAS-1")
    assert line is not None
    assert line["category"] == "FRAIS_GENERAUX"
    assert line["expense_subcategory"] == "RESTAURATION"

# -- 7. OneDrive shared accounting bridge ----------------------------------


def test_onedrive_root_detection_prefers_explicit_dir(tmp_path):
    onedrive = tmp_path / "OneDrive"
    onedrive.mkdir()
    assert resolve_onedrive_root(onedrive) == onedrive
    assert resolve_onedrive_root(tmp_path / "missing") is None


def test_onedrive_pull_newer_director_files_and_push_expert(tmp_path):
    onedrive = tmp_path / "OneDrive"
    shared = onedrive / SHARED_DIR_NAME
    pilotage = shared / PILOTAGE_DIR_NAME
    pilotage.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    tenant_dir = workspace / "document_flow" / TENANT
    readable_dir = tenant_dir / "02_A_COMPLETER"
    readable_dir.mkdir(parents=True)
    export_dir = tenant_dir / "04_EXPORT_COMPTA" / TENANT
    export_dir.mkdir(parents=True)
    local_readable = readable_dir / "_A_COMPLETER.xlsx"
    local_readable.write_bytes(b"local-old")
    remote_readable = pilotage / "_A_COMPLETER.xlsx"
    remote_readable.write_bytes(b"onedrive-new")
    import os
    os.utime(remote_readable, (local_readable.stat().st_atime + 100, local_readable.stat().st_mtime + 100))
    local_tier = tenant_dir / "REFERENTIEL_TIERS_APD.xlsx"
    local_tier.write_bytes(b"tier-local")
    remote_tier = pilotage / "REFERENTIEL_TIERS_APD.xlsx"
    remote_tier.write_bytes(b"tier-onedrive")
    os.utime(remote_tier, (local_tier.stat().st_atime + 100, local_tier.stat().st_mtime + 100))
    workbook = export_dir / "GRAND_LIVRE_CLIENTS_DETAILLES.xlsx"
    workbook.write_bytes(b"xlsx")
    piece = export_dir / "PIECES_JOINTES" / "VENTES" / "FA1.pdf"
    piece.parent.mkdir(parents=True)
    piece.write_bytes(b"%PDF-1.4")
    bridge = OneDriveAccountingBridge(
        TENANT,
        root=workspace / "document_flow",
        onedrive_dir=onedrive,
        export_dir=export_dir,
        enabled=True,
    )
    pulled = bridge.pull_director_files()
    assert local_readable.read_bytes() == b"onedrive-new"
    assert local_tier.read_bytes() == b"tier-onedrive"
    assert len(pulled) >= 2
    pushed_director = bridge.push_director_files()
    assert (pilotage / "_A_COMPLETER.xlsx").is_file()
    assert len(pushed_director) == 2
    pushed_expert = bridge.push_expert_workbooks()
    expert = shared / EXPERT_DIR_NAME
    assert (expert / "GRAND_LIVRE_CLIENTS_DETAILLES.xlsx").is_file()
    assert (expert / "PIECES_JOINTES" / "VENTES" / "FA1.pdf").is_file()
    assert pushed_expert
