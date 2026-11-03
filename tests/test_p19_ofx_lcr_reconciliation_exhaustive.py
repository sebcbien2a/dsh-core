"""P19 - Exhaustive offline coverage of Gmail fetch, OFX and LCR reconciliation.

The suite validates:

* the Caisse d'Epargne OFX SGML parser (comma amounts, ``FACT JJMMAA`` card
  purchase dates, ``FITID`` de-duplication and every payment channel);
* the ``Bordereau d'effets a l'encaissement`` parser and the exact 1-to-7
  reconciliation with the global ``REMISE ENCAISSEMENT N 94694`` OFX credit;
* the ``counterparty_payment_rules`` store API (manual rules, learning,
  filtering and prioritisation);
* the ``scripts/fetch_apd_emails.py --dry-run`` CLI and the APD label mirrors.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from schemas.payment_method_schema import (
    ExpectedPaymentMethod,
    PaymentMethodError,
    normalize_expected_payment_method,
    payment_method_matches,
)
from scripts.fetch_apd_emails import (
    MIN_ATTACHMENT_BYTES,
    ApdMailConfigError,
    build_dry_run_report,
    main as fetch_main,
    preview_attachment,
    read_env_file,
    resolve_mail_config,
)
from services.email_inbox_bridge import (
    APD_MAILBOX_PREFIX,
    EmailAttachment,
    EmailInboxBridge,
    EmailInboxBridgeError,
    EmailMessage,
    MAILBOX_STATES,
)
from services.lcr_remittance_parser import (
    LcrReconciliationError,
    LcrRemittanceError,
    allocate_lcr_remittance,
    parse_lcr_remittance,
    reconcile_lcr_with_ofx,
    reconcile_ofx_remittance,
)
from services.ofx_bank_statement_parser import (
    CATEGORY_BANQUE_FINANCE,
    CATEGORY_VENTES_CLIENTS,
    OfxStatementError,
    OfxTransaction,
    classify_ofx_line,
    classify_payment_method,
    decode_ofx,
    import_ofx_statement,
    ofx_line_id,
    parse_ofx_amount,
    parse_ofx_date,
    parse_ofx_statement,
)
from services.sqlite_accounting_store import AccountingStoreError, SqliteAccountingStore

TENANT = "APD"
FITID_REMISE = "CE-20261005-001"

OFX_TEXT = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:UTF-8
CHARSET:UTF-8
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<SIGNONMSGSRSV1>
<SONRS>
<STATUS>
<CODE>0
<SEVERITY>INFO
</STATUS>
<DTSERVER>20261103120000
<LANGUAGE>FRA
</SONRS>
</SIGNONMSGSRSV1>
<BANKMSGSRSV1>
<STMTTRNRS>
<TRNUID>1
<STATUS>
<CODE>0
<SEVERITY>INFO
</STATUS>
<STMTRS>
<CURDEF>EUR
<BANKACCTFROM>
<BANKID>14505
<BRANCHID>00002
<ACCTID>145050000208001409859
<ACCTTYPE>CHECKING
</BANKACCTFROM>
<BANKTRANLIST>
<DTSTART>20261001
<DTEND>20261031
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20261005120000+02:00
<TRNAMT>+9240,48
<FITID>CE-20261005-001
<NAME>REMISE ENCAISSEMENT N 94694
<MEMO>BORDEREAU LCR 7 EFFETS
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20261006120000
<TRNAMT>-4173,79
<FITID>CE-20261006-002
<NAME>LCR 123456 RLV 789
<MEMO>ECHEANCE 15/11/2026
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20261007120000
<TRNAMT>1500,00
<FITID>CE-20261007-003
<NAME>REMISE CHEQUES N 1201
<MEMO>REMISE DE 3 CHEQUES
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20261008120000
<TRNAMT>-85,30
<FITID>CE-20261008-004
<NAME>PRLV SEPA EDF
<MEMO>PRELEVEMENT MENSUEL
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20261009120000
<TRNAMT>500,00
<FITID>CE-20261009-005
<NAME>VIR SEPA DUPONT SAS
<MEMO>REGLEMENT FACTURE
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20261010120000
<TRNAMT>-200,00
<FITID>CE-20261010-006
<NAME>VIR INST MARTIN
<MEMO>FRAIS NOTAIRE
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20261011120000
<TRNAMT>-59,99
<FITID>CE-20261011-007
<NAME>CB CARREFOUR FACT 120126
<MEMO>CARTE 1234
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20261012120000
<TRNAMT>-120,00
<FITID>CE-20261012-008
<NAME>CHEQUE N 4567
<MEMO>FOURNISSEUR PEINTURE
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20261013120000
<TRNAMT>-12,50
<FITID>CE-20261013-009
<NAME>COMMISSION SUR EFFETS
<MEMO>FRAIS LCR
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20261014120000
<TRNAMT>-2,00
<FITID>CE-20261014-010
<NAME>COMMISSIONS
<MEMO>TENUE DE COMPTE
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20261015120000
<TRNAMT>-5,00
<FITID>CE-20261015-011
<NAME>*Abo CE
<MEMO>ABONNEMENT
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20261016120000
<TRNAMT>-3,00
<FITID>CE-20261016-012
<NAME>* FORFAIT
<MEMO>FORFAIT
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20261005120000+02:00
<TRNAMT>1,00
<FITID>CE-20261005-001
<NAME>DOUBLON DE FITID
<MEMO>A IGNORER
</STMTTRN>
</BANKTRANLIST>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>
"""

LCR_TEXT = """CAISSE D'EPARGNE
Bordereau d'effets a l'encaissement
Reference de transmission : 32000028917640
Compte : 145050000208001409859
Nombre d'operations : 7
Montant total au credit : 9 240,48 EUR

Tire | Rang | Domiciliation | Acceptation | Echeance | Montant au credit
CALANDRE 18 | 1 | LYON | ACC | 15/01/2026 | 144,00
CALANDRE 18 | 2 | LYON | ACC | 15/02/2026 | 669,07
CARROSSERIE VINOLIENNE | 3 | VINOLIENNE | ACC | 15/01/2026 | 225,60
GP AUTO 36 | 4 | DP2C | ACC | 15/01/2026 | 1 045,88
GP AUTO 58 | 5 | NEVERS | ACC | 15/01/2026 | 3 658,21
SAVOIE JANTES | 6 | CHAMBERY | ACC | 15/02/2026 | 2 000,08
3G AUTOS | 7 | LYON | ACC | 15/02/2026 | 1 497,64
"""

EXPECTED_AMOUNTS = [
    Decimal("144.00"),
    Decimal("669.07"),
    Decimal("225.60"),
    Decimal("1045.88"),
    Decimal("3658.21"),
    Decimal("2000.08"),
    Decimal("1497.64"),
]


class FakeMailClient:
    def __init__(self, messages=()):
        self.messages = list(messages)
        self.folders: list[str] = []
        self.moves: list[tuple[str, str, str]] = []
        self.listed: list[str] = []

    def ensure_folder(self, folder: str) -> None:
        if folder not in self.folders:
            self.folders.append(folder)

    def list_messages(self, folder: str) -> list[EmailMessage]:
        self.listed.append(folder)
        return list(self.messages)

    def move_message(
        self, message_id: str, source_folder: str, target_folder: str
    ) -> None:
        self.moves.append((message_id, source_folder, target_folder))


def make_store(tmp_path: Path) -> SqliteAccountingStore:
    return SqliteAccountingStore(tmp_path / "accounting.sqlite3")


def remittance_transaction() -> OfxTransaction:
    statement = parse_ofx_statement(OFX_TEXT)
    return next(
        item for item in statement.transactions if item.fitid == FITID_REMISE
    )


# -- OFX amounts and dates ----------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("-4173,79", "-4173.79"),
        ("+9240,48", "9240.48"),
        ("1 234,56", "1234.56"),
        ("1\u00a0234,56", "1234.56"),
        ("1.234,56", "1234.56"),
        ("12,5", "12.50"),
        ("12.50", "12.50"),
        ("-0,01", "-0.01"),
    ],
)
def test_ofx_amount_formats(raw, expected):
    assert parse_ofx_amount(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["", "abc", "12,345"])
def test_ofx_amount_rejects_invalid_values(raw):
    with pytest.raises(OfxStatementError):
        parse_ofx_amount(raw)


def test_ofx_date_accepts_timezone_and_compact_values():
    assert parse_ofx_date("20261005120000+02:00").isoformat() == "2026-10-05"
    assert parse_ofx_date("20261031").isoformat() == "2026-10-31"
    with pytest.raises(OfxStatementError) as exc:
        parse_ofx_date("31/10/2026")
    assert exc.value.code == "INVALID_DATE"


def test_decode_ofx_supports_utf8_and_cp1252():
    assert decode_ofx(OFX_TEXT.encode("utf-8")) == OFX_TEXT
    assert decode_ofx(OFX_TEXT.encode("utf-8-sig")) == OFX_TEXT
    cp1252 = OFX_TEXT.replace("DUPONT SAS", "CRÉDIT AGRICOLE").replace(
        "ENCODING:UTF-8", "ENCODING:1252"
    )
    parsed = parse_ofx_statement(cp1252.encode("cp1252"))
    assert any(
        "CRÉDIT AGRICOLE" in (item.counterparty or "")
        for item in parsed.transactions
    )
    assert decode_ofx(cp1252.encode("cp1252")).startswith("OFXHEADER:100")


# -- OFX classification -------------------------------------------------------


@pytest.mark.parametrize(
    "label, memo, method, category",
    [
        (
            "REMISE ENCAISSEMENT N 94694",
            "BORDEREAU LCR",
            "LCR_ENCAISSEMENT_LOT",
            CATEGORY_VENTES_CLIENTS,
        ),
        (
            "LCR 123456 RLV 789",
            "",
            "LCR_FOURNISSEUR_DEBIT",
            None,
        ),
        (
            "REMISE CHEQUES N 1201",
            "",
            "REMISE_CHEQUES",
            CATEGORY_VENTES_CLIENTS,
        ),
        ("PRLV SEPA EDF", "", "PRELEVEMENT_SEPA", None),
        ("VIR SEPA DUPONT SAS", "", "VIREMENT", None),
        ("VIR INST MARTIN", "", "VIREMENT", None),
        ("CB CARREFOUR FACT 120126", "", "CARTE_BANCAIRE", None),
        ("CHEQUE N 4567", "", "CHEQUE_EMIS", None),
        ("COMMISSION SUR EFFETS", "", "FRAIS_BANCAIRES", CATEGORY_BANQUE_FINANCE),
        ("COMMISSIONS", "", "FRAIS_BANCAIRES", CATEGORY_BANQUE_FINANCE),
        ("*Abo CE", "", "FRAIS_BANCAIRES", CATEGORY_BANQUE_FINANCE),
        ("* FORFAIT", "", "FRAIS_BANCAIRES", CATEGORY_BANQUE_FINANCE),
    ],
)
def test_classify_ofx_payment_channels(label, memo, method, category):
    classification = classify_ofx_line(label, memo)
    assert classification.payment_method == method
    assert classification.category == category
    assert classify_payment_method(label, memo) == method


def test_card_purchase_date_is_extracted_from_fact_jjmmaa():
    classification = classify_ofx_line("CB CARREFOUR FACT 120126")
    assert classification.payment_method == "CARTE_BANCAIRE"
    assert classification.purchase_date.isoformat() == "2026-01-12"
    assert classification.counterparty == "CARREFOUR"


def test_card_purchase_date_handles_accent_and_invalid_date():
    valid = classify_ofx_line("CB ÉPICERIE FACT 010226")
    assert valid.purchase_date.isoformat() == "2026-02-01"
    invalid = classify_ofx_line("CB EPICERIE FACT 311326")
    assert invalid.payment_method == "CARTE_BANCAIRE"
    assert invalid.purchase_date is None


# -- OFX statement parsing ----------------------------------------------------


def test_parse_caisse_depargne_sgml_statement_and_deduplicate_fitid():
    statement = parse_ofx_statement(OFX_TEXT)
    assert statement.account_id == "145050000208001409859"
    assert statement.bank_id == "14505"
    assert statement.branch_id == "00002"
    assert statement.currency == "EUR"
    assert statement.date_start.isoformat() == "2026-10-01"
    assert statement.date_end.isoformat() == "2026-10-31"
    assert len(statement.transactions) == 12
    assert statement.duplicates == 1
    assert len({item.fitid for item in statement.transactions}) == 12
    assert statement.transactions[0].amount == Decimal("9240.48")
    assert statement.transactions[1].amount == Decimal("-4173.79")
    assert statement.total_credit == Decimal("11240.48")
    assert statement.total_debit == Decimal("4661.58")


def test_every_ofx_transaction_has_a_method_and_consistent_movement():
    statement = parse_ofx_statement(OFX_TEXT)
    assert all(item.payment_method != "AUTRE" for item in statement.transactions)
    for item in statement.transactions:
        if item.amount > 0:
            assert item.credit_amount == item.amount and item.debit_amount is None
        else:
            assert item.debit_amount == -item.amount and item.credit_amount is None


def test_ofx_statement_rejects_missing_statement_and_unknown_format():
    with pytest.raises(OfxStatementError) as exc:
        parse_ofx_statement("OFXHEADER:100\n<OFX></OFX>")
    assert exc.value.code == "NO_TRANSACTIONS"
    with pytest.raises(OfxStatementError) as exc:
        parse_ofx_statement("not an ofx file")
    assert exc.value.code == "UNSUPPORTED_OFX"
    with pytest.raises(OfxStatementError):
        parse_ofx_statement(b"")


def test_ofx_line_id_is_stable_and_safe():
    assert ofx_line_id("CE-20261005-001") == "OFX-CE-20261005-001"
    assert ofx_line_id("A/B C") == "OFX-A_B_C"
    assert len(ofx_line_id("x" * 500)) <= 128


def test_import_ofx_statement_into_store_is_idempotent(tmp_path):
    store = make_store(tmp_path)
    first = import_ofx_statement(store, TENANT, OFX_TEXT)
    assert first.created == 12
    assert first.duplicates == 0
    second = import_ofx_statement(store, TENANT, parse_ofx_statement(OFX_TEXT))
    assert second.created == 0
    assert second.duplicates == 12
    lines = store.list_master_lines(TENANT)
    assert len(lines) == 12
    remise = next(line for line in lines if line["fitid"] == FITID_REMISE)
    assert remise["payment_method"] == "LCR_ENCAISSEMENT_LOT"
    assert remise["credit_amount"] == "9240.48"
    assert remise["payment_reference"] == "94694"
    assert remise["counterparty"] == "CLIENTS LCR"
    assert remise["category"] == CATEGORY_VENTES_CLIENTS
    card = next(line for line in lines if line["payment_method"] == "CARTE_BANCAIRE")
    assert card["purchase_date"] == "2026-01-12"
    fee = next(line for line in lines if line["payment_method"] == "FRAIS_BANCAIRES")
    assert fee["category"] == CATEGORY_BANQUE_FINANCE


# -- LCR remittance parser ----------------------------------------------------


def test_parse_lcr_bordereau_extracts_metadata_and_seven_lines():
    remittance = parse_lcr_remittance(LCR_TEXT, strict=True)
    assert remittance.transmission_reference == "32000028917640"
    assert remittance.account_number == "145050000208001409859"
    assert remittance.operation_count == 7
    assert remittance.total_amount == Decimal("9240.48")
    assert remittance.computed_total == Decimal("9240.48")
    assert remittance.currency == "EUR"
    assert remittance.line_count == 7
    assert remittance.balanced and remittance.count_matches
    assert [line.amount for line in remittance.lines] == EXPECTED_AMOUNTS
    assert [line.drawer for line in remittance.lines[:3]] == [
        "CALANDRE 18",
        "CALANDRE 18",
        "CARROSSERIE VINOLIENNE",
    ]
    assert remittance.lines[0].acceptance == "ACC"
    assert remittance.lines[0].due_date.isoformat() == "2026-01-15"
    assert remittance.by_drawer()["CALANDRE 18"] == Decimal("813.07")
    assert len(remittance.unique_drawers) == 6


def test_parse_lcr_whitespace_table_without_pipes():
    text = LCR_TEXT.replace(" | ", "   ").replace("| ", "  ").replace(" |", "  ")
    remittance = parse_lcr_remittance(text)
    assert remittance.line_count == 7
    assert remittance.computed_total == Decimal("9240.48")
    assert remittance.lines[3].drawer == "GP AUTO 36"
    assert remittance.lines[3].domiciliation == "DP2C"
    assert remittance.lines[3].amount == Decimal("1045.88")
    assert remittance.lines[3].due_date.isoformat() == "2026-01-15"


def test_parse_lcr_accepts_accented_labels_and_short_dates():
    text = LCR_TEXT.replace("Reference", "Référence").replace(
        "Echeance", "Échéance"
    ).replace("15/01/2026", "15/01/26")
    remittance = parse_lcr_remittance(text)
    assert remittance.transmission_reference == "32000028917640"
    assert remittance.lines[0].due_date.isoformat() == "2026-01-15"


def test_lcr_strict_validation_rejects_bad_total_and_count():
    with pytest.raises(LcrRemittanceError) as exc:
        parse_lcr_remittance(
            LCR_TEXT.replace("9 240,48 EUR", "9 240,49 EUR"), strict=True
        )
    assert exc.value.code == "TOTAL_MISMATCH"
    with pytest.raises(LcrRemittanceError) as exc:
        parse_lcr_remittance(
            LCR_TEXT.replace("Nombre d'operations : 7", "Nombre d'operations : 8"),
            strict=True,
        )
    assert exc.value.code == "OPERATION_COUNT_MISMATCH"


def test_parse_lcr_rejects_empty_and_line_less_documents():
    with pytest.raises(LcrRemittanceError) as exc:
        parse_lcr_remittance("")
    assert exc.value.code == "EMPTY_DOCUMENT"
    with pytest.raises(LcrRemittanceError) as exc:
        parse_lcr_remittance("Bordereau sans lignes exploitables")
    assert exc.value.code == "NO_LINES"


def test_parse_lcr_pdf_bytes_with_text_layer():
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(fitz.Rect(20, 20, 580, 800), LCR_TEXT, fontsize=7)
    remittance = parse_lcr_remittance(document.tobytes(), strict=True)
    assert remittance.line_count == 7
    assert remittance.computed_total == Decimal("9240.48")
    assert remittance.lines[3].drawer == "GP AUTO 36"
    assert remittance.lines[3].amount == Decimal("1045.88")


# -- 1-to-7 reconciliation ----------------------------------------------------


def test_reconcile_remise_encaissement_with_bordereau_is_exact():
    transaction = remittance_transaction()
    remittance = parse_lcr_remittance(LCR_TEXT, strict=True)
    report = reconcile_ofx_remittance(transaction, remittance)
    assert report.matched and report.balanced
    assert report.ofx_reference == "94694"
    assert report.ofx_amount == Decimal("9240.48")
    assert report.remittance_reference == "32000028917640"
    assert report.allocation_count == 7
    assert report.allocated_total == Decimal("9240.48")
    assert [allocation.amount for allocation in report.allocations] == EXPECTED_AMOUNTS
    assert report.by_drawer()["CALANDRE 18"] == Decimal("813.07")
    assert report.by_drawer()["GP AUTO 58"] == Decimal("3658.21")
    assert report.by_drawer()["3G AUTOS"] == Decimal("1497.64")
    assert reconcile_lcr_with_ofx(transaction, remittance) == report


def test_reconciliation_lines_are_exact_customer_credits():
    report = reconcile_ofx_remittance(
        remittance_transaction(), parse_lcr_remittance(LCR_TEXT)
    )
    lines = report.as_reconciliation_lines()
    assert len(lines) == 7
    assert sum(Decimal(line["credit_amount"]) for line in lines) == Decimal("9240.48")
    assert all(line["payment_method"] == "LCR_ENCAISSEMENT_LOT" for line in lines)
    assert all(line["remittance_reference"] == "32000028917640" for line in lines)
    assert lines[0]["line_id"] == f"{FITID_REMISE}:LCR:01"
    assert lines[0]["counterparty"] == "CALANDRE 18"
    assert lines[0]["due_date"] == "2026-01-15"
    assert lines[1]["line_id"] == f"{FITID_REMISE}:LCR:02"
    assert lines[1]["due_date"] == "2026-02-15"


def test_allocate_lcr_is_one_to_seven_without_parent_transaction():
    remittance = parse_lcr_remittance(LCR_TEXT)
    allocations = allocate_lcr_remittance(remittance, fitid="FIT-1")
    assert len(allocations) == 7
    assert {allocation.line_id for allocation in allocations} == {
        f"FIT-1:LCR:{rank:02d}" for rank in range(1, 8)
    }
    assert sum(allocation.amount for allocation in allocations) == Decimal("9240.48")


def test_reconciliation_rejects_wrong_method_and_amount():
    remittance = parse_lcr_remittance(LCR_TEXT)
    transaction = remittance_transaction()
    wrong_method = OfxTransaction(
        fitid="X",
        operation_date=transaction.operation_date,
        amount=transaction.amount,
        label=transaction.label,
        payment_method="VIREMENT",
    )
    with pytest.raises(LcrReconciliationError) as exc:
        reconcile_ofx_remittance(wrong_method, remittance)
    assert exc.value.code == "NOT_REMISE_ENCAISSEMENT"
    with pytest.raises(LcrReconciliationError) as exc:
        reconcile_ofx_remittance(
            {
                "fitid": "X",
                "label": "REMISE ENCAISSEMENT N 1",
                "payment_method": "LCR_ENCAISSEMENT_LOT",
                "credit_amount": "9000.00",
            },
            remittance,
        )
    assert exc.value.code == "AMOUNT_MISMATCH"


def test_reconciliation_rejects_unbalanced_bordereau():
    remittance = parse_lcr_remittance(
        LCR_TEXT.replace("9 240,48 EUR", "9 240,49 EUR")
    )
    with pytest.raises(LcrReconciliationError) as exc:
        reconcile_ofx_remittance(remittance_transaction(), remittance)
    assert exc.value.code == "REMITTANCE_TOTAL_MISMATCH"


# -- counterparty payment rules ----------------------------------------------


@pytest.mark.parametrize(
    "observed, expected",
    [
        ("LCR_ENCAISSEMENT_LOT", True),
        ("LCR_FOURNISSEUR_DEBIT", True),
        ("REMISE_CHEQUES", False),
        ("PRELEVEMENT_SEPA", False),
        (None, False),
    ],
)
def test_payment_method_matches_lcr_family(observed, expected):
    assert payment_method_matches("LCR", observed) is expected


def test_payment_method_matches_all_expected_families():
    assert payment_method_matches("VIREMENT", "VIREMENT")
    assert payment_method_matches("PRELEVEMENT", "PRELEVEMENT_SEPA")
    assert payment_method_matches("CB", "CARTE_BANCAIRE")
    assert payment_method_matches("CHEQUE", "CHEQUE_EMIS")
    assert payment_method_matches("CHEQUE", "REMISE_CHEQUES")
    assert payment_method_matches("LCR", "AUTRE") is False
    assert payment_method_matches("INCONNU", "VIREMENT") is False


def test_expected_payment_method_normalisation():
    assert normalize_expected_payment_method("lcr") == "LCR"
    assert normalize_expected_payment_method("Prélèvement SEPA") == "PRELEVEMENT"
    assert normalize_expected_payment_method("carte bancaire") == "CB"
    assert normalize_expected_payment_method(ExpectedPaymentMethod.CHEQUE) == "CHEQUE"
    with pytest.raises(PaymentMethodError):
        normalize_expected_payment_method("BITCOIN")


def test_store_set_get_list_and_delete_payment_rule(tmp_path):
    store = make_store(tmp_path)
    rule = store.set_counterparty_payment_rule(TENANT, "DUPONT SAS", "LCR")
    assert rule["counterparty"] == "DUPONT SAS"
    assert rule["expected_payment_method"] == "LCR"
    assert rule["source"] == "HUMAN"
    assert rule["sample_count"] == 1
    assert store.get_counterparty_payment_rule(TENANT, "dupont sas") == rule
    assert store.get_counterparty_payment_rule(TENANT, "inconnu") is None
    store.set_counterparty_payment_rule(
        TENANT, "DUPONT SAS", "VIREMENT", confidence="0.5"
    )
    rules = store.list_counterparty_payment_rules(TENANT)
    assert len(rules) == 1
    assert rules[0]["expected_payment_method"] == "VIREMENT"
    assert rules[0]["confidence"] == "0.5"
    assert store.delete_counterparty_payment_rule(TENANT, "DUPONT SAS") is True
    assert store.list_counterparty_payment_rules(TENANT) == []


def test_store_payment_rule_rejects_invalid_inputs(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(AccountingStoreError) as exc:
        store.set_counterparty_payment_rule(TENANT, "X", "BITCOIN")
    assert exc.value.code == "INVALID_PAYMENT_METHOD"
    with pytest.raises(AccountingStoreError) as exc:
        store.set_counterparty_payment_rule(TENANT, "X", "LCR", confidence="2")
    assert exc.value.code == "INVALID_CONFIDENCE"
    with pytest.raises(AccountingStoreError) as exc:
        store.set_counterparty_payment_rule(TENANT, "X", "LCR", source="ROBOT")
    assert exc.value.code == "INVALID_RULE_SOURCE"
    with pytest.raises(AccountingStoreError) as exc:
        store.set_counterparty_payment_rule(TENANT, "", "LCR")
    assert exc.value.code == "INVALID_COUNTERPARTY"


def _add_payment_line(
    store: SqliteAccountingStore,
    key: str,
    counterparty: str,
    method: str,
    amount: str = "100.00",
) -> None:
    store.add_line(
        TENANT,
        key,
        {
            "operation_date": "2026-10-05",
            "label": f"{method} {counterparty}",
            "credit_amount": amount,
            "counterparty": counterparty,
            "payment_method": method,
            "status": "PENDING",
        },
    )


def test_store_learns_dominant_payment_habit(tmp_path):
    store = make_store(tmp_path)
    _add_payment_line(store, "l1", "DUPONT SAS", "LCR_ENCAISSEMENT_LOT")
    _add_payment_line(store, "l2", "DUPONT SAS", "LCR_ENCAISSEMENT_LOT")
    _add_payment_line(store, "l3", "DUPONT SAS", "LCR_ENCAISSEMENT_LOT")
    _add_payment_line(store, "l4", "DUPONT SAS", "VIREMENT")
    _add_payment_line(store, "l5", "EDF", "PRELEVEMENT_SEPA")
    _add_payment_line(store, "l6", "EDF", "PRELEVEMENT_SEPA")
    learned = {rule["counterparty"]: rule for rule in store.learn_counterparty_payment_rules(TENANT)}
    assert learned["DUPONT SAS"]["expected_payment_method"] == "LCR"
    assert learned["DUPONT SAS"]["source"] == "LEARNED"
    assert learned["DUPONT SAS"]["sample_count"] == 4
    assert learned["DUPONT SAS"]["confidence"] == "0.7500"
    assert learned["EDF"]["expected_payment_method"] == "PRELEVEMENT"
    assert learned["EDF"]["confidence"] == "1.0000"


def test_store_learning_respects_human_rules(tmp_path):
    store = make_store(tmp_path)
    store.set_counterparty_payment_rule(TENANT, "DUPONT SAS", "VIREMENT")
    _add_payment_line(store, "l1", "DUPONT SAS", "LCR_ENCAISSEMENT_LOT")
    _add_payment_line(store, "l2", "DUPONT SAS", "LCR_ENCAISSEMENT_LOT")
    assert store.learn_counterparty_payment_rules(TENANT) == []
    preserved = store.get_counterparty_payment_rule(TENANT, "DUPONT SAS")
    assert preserved["expected_payment_method"] == "VIREMENT"
    assert preserved["source"] == "HUMAN"
    learned = store.learn_counterparty_payment_rules(TENANT, overwrite_human=True)
    assert learned[0]["expected_payment_method"] == "LCR"
    assert learned[0]["source"] == "LEARNED"


def test_store_learning_honours_min_occurrences(tmp_path):
    store = make_store(tmp_path)
    _add_payment_line(store, "l1", "RARE", "VIREMENT")
    assert store.learn_counterparty_payment_rules(TENANT, min_occurrences=2) == []
    assert store.list_counterparty_payment_rules(TENANT) == []
    learned = store.learn_counterparty_payment_rules(TENANT, min_occurrences=1)
    assert learned[0]["counterparty"] == "RARE"


def test_store_filter_and_prioritise_lines_by_payment_rule(tmp_path):
    store = make_store(tmp_path)
    store.set_counterparty_payment_rule(TENANT, "DUPONT SAS", "LCR")
    store.set_counterparty_payment_rule(TENANT, "EDF", "PRELEVEMENT")
    _add_payment_line(store, "match", "DUPONT SAS", "LCR_ENCAISSEMENT_LOT")
    _add_payment_line(store, "mismatch", "DUPONT SAS", "VIREMENT")
    _add_payment_line(store, "unknown", "CLIENT INCONNU", "VIREMENT")
    groups = store.filter_lines_by_payment_rule(TENANT)
    assert [line["line_id"] for line in groups["matched"]] == ["match"]
    assert groups["matched"][0]["expected_payment_method"] == "LCR"
    assert groups["matched"][0]["payment_rule_matches"] is True
    assert [line["line_id"] for line in groups["mismatched"]] == ["mismatch"]
    assert groups["mismatched"][0]["payment_rule_matches"] is False
    assert [line["line_id"] for line in groups["without_rule"]] == ["unknown"]
    assert groups["without_rule"][0]["expected_payment_method"] is None
    ordered = store.prioritize_lines_by_payment_rule(TENANT)
    assert [line["line_id"] for line in ordered] == ["match", "unknown", "mismatch"]
    assert [line["payment_rule_priority"] for line in ordered] == [0, 1, 2]


# -- Gmail bridge and CLI -----------------------------------------------------


def test_bridge_mirrors_apd_labels_and_ignores_small_attachments(tmp_path):
    big = b"%PDF-1.7\n" + b"x" * (MIN_ATTACHMENT_BYTES + 10)
    small = b"%PDF-1.7"
    client = FakeMailClient(
        [
            EmailMessage(
                "m1",
                subject="Facture",
                attachments=(
                    EmailAttachment("facture.pdf", big),
                    EmailAttachment("minuscule.pdf", small),
                    EmailAttachment("tableau.xlsx", b"x" * 6000),
                ),
            )
        ]
    )
    bridge = EmailInboxBridge(
        tenant_id=TENANT,
        root=tmp_path,
        client=client,
        process_attachments=False,
        mailbox_prefix=APD_MAILBOX_PREFIX,
        min_attachment_bytes=MIN_ATTACHMENT_BYTES,
    )
    report = bridge.fetch_and_stage_emails()
    assert report.mirrored_folders == tuple(
        f"APD/{state}" for state in MAILBOX_STATES
    )
    assert set(client.folders) == set(report.mirrored_folders)
    assert client.listed == ["APD/00_INBOX"]
    assert report.attachments_seen == 3
    assert report.staged == 1
    assert report.ignored == 2
    assert (tmp_path / TENANT / "00_INBOX" / "facture.pdf").is_file()
    assert report.records[0].mailbox_folder == "APD/00_INBOX"
    digest = report.records[0].sha256
    assert bridge.sync_email_folder_state(digest, "03_VALIDE_PRET_FEC") is True
    assert client.moves == [
        ("m1", "APD/00_INBOX", "APD/03_VALIDE_PRET_FEC")
    ]
    assert bridge.records[0].mailbox_folder == "APD/03_VALIDE_PRET_FEC"


def test_bridge_default_prefix_keeps_p17_behaviour(tmp_path):
    client = FakeMailClient(
        [EmailMessage("m1", attachments=(EmailAttachment("f.pdf", b"%PDF"),))]
    )
    bridge = EmailInboxBridge(
        tenant_id=TENANT,
        root=tmp_path,
        client=client,
        process_attachments=False,
    )
    report = bridge.fetch_and_stage_emails()
    assert report.mirrored_folders == MAILBOX_STATES
    assert report.records[0].mailbox_folder == "00_INBOX"


def test_bridge_rejects_invalid_prefix_and_threshold(tmp_path):
    with pytest.raises(EmailInboxBridgeError) as exc:
        EmailInboxBridge(tenant_id=TENANT, root=tmp_path, mailbox_prefix=123)
    assert exc.value.code == "INVALID_MAILBOX_PREFIX"
    with pytest.raises(EmailInboxBridgeError) as exc:
        EmailInboxBridge(
            tenant_id=TENANT, root=tmp_path, min_attachment_bytes=-1
        )
    assert exc.value.code == "INVALID_MIN_ATTACHMENT_BYTES"


def test_env_file_reader_supports_quotes_and_export(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# APD\n"
        "export APD_INVOICE_EMAIL='factures@example.com'\n"
        'GMAIL_APP_PASSWORD="abcd efgh"\n'
        "EMPTY=\n",
        encoding="utf-8",
    )
    values = read_env_file(path)
    assert values["APD_INVOICE_EMAIL"] == "factures@example.com"
    assert values["GMAIL_APP_PASSWORD"] == "abcd efgh"
    assert values["EMPTY"] == ""


def test_resolve_mail_config_uses_dotenv_then_environment(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "APD_INVOICE_EMAIL=facturesapd@gmail.com\n"
        "GMAIL_APP_PASSWORD=secret\n",
        encoding="utf-8",
    )
    config = resolve_mail_config(env_file=path, environ={})
    assert config.email == "facturesapd@gmail.com"
    assert config.password == "secret"
    assert config.host == "imap.gmail.com"
    assert config.port == 993
    assert config.mailbox_prefix == "APD/"
    assert config.inbox_folder == "APD/00_INBOX"
    overridden = resolve_mail_config(
        env_file=path,
        environ={
            "APD_INVOICE_EMAIL": "override@example.com",
            "DSH_MAIL_HOST": "mail.example.com",
            "DSH_MAIL_PORT": "1993",
        },
    )
    assert overridden.email == "override@example.com"
    assert overridden.host == "mail.example.com"
    assert overridden.port == 1993


def test_resolve_mail_config_reports_missing_values(tmp_path):
    path = tmp_path / ".env"
    path.write_text("APD_INVOICE_EMAIL=factures@example.com\n", encoding="utf-8")
    with pytest.raises(ApdMailConfigError) as exc:
        resolve_mail_config(env_file=path, environ={})
    assert exc.value.code == "GMAIL_APP_PASSWORD_MISSING"
    path.write_text("GMAIL_APP_PASSWORD=secret\n", encoding="utf-8")
    with pytest.raises(ApdMailConfigError) as exc:
        resolve_mail_config(env_file=path, environ={})
    assert exc.value.code == "APD_INVOICE_EMAIL_MISSING"


def test_preview_attachment_applies_size_and_type_rules():
    big = EmailAttachment("facture.pdf", b"x" * MIN_ATTACHMENT_BYTES)
    small = EmailAttachment("petit.pdf", b"x" * (MIN_ATTACHMENT_BYTES - 1))
    wrong = EmailAttachment("classeur.xlsx", b"x" * MIN_ATTACHMENT_BYTES)
    assert preview_attachment(big).eligible is True
    assert preview_attachment(small).reason == "TAILLE_INFERIEURE_A_5_KO"
    assert preview_attachment(wrong).reason == "TYPE_NON_DOCUMENTAIRE"


def test_fetch_cli_dry_run_never_mutates_the_mailbox(tmp_path, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APD_INVOICE_EMAIL=facturesapd@gmail.com\nGMAIL_APP_PASSWORD=secret\n",
        encoding="utf-8",
    )
    client = FakeMailClient(
        [
            EmailMessage(
                "m1",
                subject="Lot APD",
                attachments=(
                    EmailAttachment("facture.pdf", b"x" * MIN_ATTACHMENT_BYTES),
                    EmailAttachment("petit.pdf", b"x"),
                    EmailAttachment("notes.xlsx", b"x" * MIN_ATTACHMENT_BYTES),
                ),
            ),
            EmailMessage("m2", subject="Sans piece"),
        ]
    )
    code = fetch_main(
        ["--dry-run", "--limit", "1", "--env-file", str(env_file)],
        client=client,
        environ={},
    )
    output = capsys.readouterr().out
    assert code == 0
    assert "DRY-RUN" in output
    assert "APD/00_INBOX" in output
    assert "TAILLE_INFERIEURE_A_5_KO" in output
    assert "TYPE_NON_DOCUMENTAIRE" in output
    assert client.folders == []
    assert client.moves == []
    assert client.listed == ["APD/00_INBOX"]


def test_fetch_cli_dry_run_reports_missing_credentials(tmp_path, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    code = fetch_main(
        ["--dry-run", "--env-file", str(env_file)],
        client=FakeMailClient(),
        environ={},
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "APD_INVOICE_EMAIL_MISSING" in captured.err


def test_build_dry_run_report_counts_are_consistent():
    messages = [
        EmailMessage(
            "m1",
            attachments=(
                EmailAttachment("ok.pdf", b"x" * MIN_ATTACHMENT_BYTES),
                EmailAttachment("small.pdf", b"x"),
                EmailAttachment("bad.xlsx", b"x"),
            ),
        )
    ]
    report = build_dry_run_report(messages, folder="APD/00_INBOX")
    assert report.messages_scanned == 1
    assert report.attachments_seen == 3
    assert report.eligible == 1
    assert report.ignored_size == 1
    assert report.ignored_type == 1
    assert report.ignored == 2
