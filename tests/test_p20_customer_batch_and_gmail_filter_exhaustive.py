"""P20 - Exhaustive offline coverage of Gmail filtering, Sage/PDF batches and
bank lettering.

The suite validates:

* the strict P20 Gmail eligibility rule (PDF >= 5 KiB + accounting keyword in
  subject, body or filename), with zero message deletion and off-topic messages
  left untouched in INBOX;
* automatic creation of the six APD mirror labels and the ``--folder INBOX``
  fallback;
* the 23-invoice Sage CSV import (``34 897,87 EUR HT``, ``6 979,59 EUR TVA``,
  ``41 877,46 EUR TTC``) coupled with the grouped multi-invoice PDF;
* the exact subset-sum lettering of remittances 91374, 91787 and 92174, the
  1-to-7 bordereau 94694, and the SCTI / Carrosserie Yucell transfers;
* the ``TABLEAU_VENTES_CLIENTS.xlsx`` payment status column.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from scripts.fetch_apd_emails import (
    build_dry_run_report,
    main as fetch_main,
)
from services.customer_invoice_batch_importer import (
    PAID_STATUS,
    PENDING_STATUS,
    SALES_WORKBOOK_HEADERS,
    CustomerInvoice,
    import_customer_invoice_batch,
    import_customer_invoices,
    merge_customer_invoices,
    parse_multi_invoice_pdf,
    parse_sage_customer_invoices,
    export_customer_sales_workbook,
)
from services.customer_invoice_reconciliation import (
    REMISE_CHEQUES,
    counterparty_names_match,
    find_exact_invoice_combination,
    load_pending_invoices,
    reconcile_customer_invoices,
)
from services.email_inbox_bridge import (
    APD_ELIGIBILITY_KEYWORDS,
    APD_MAILBOX_PREFIX,
    ELIGIBLE_REASON,
    EmailAttachment,
    EmailInboxBridge,
    EmailMessage,
    MAILBOX_STATES,
    MIN_APD_PDF_BYTES,
    REASON_NO_KEYWORD,
    REASON_NO_PDF,
    REASON_PDF_TOO_SMALL,
    evaluate_apd_email_eligibility,
)
from services.ofx_bank_statement_parser import import_ofx_statement
from services.sqlite_accounting_store import SqliteAccountingStore

TENANT = "APD"
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

CSV_HEADER = (
    "N° pièce;Statut;Date Pièce;Client;Société;Total HT;Total TVA;"
    "Total TTC;Solde Dû;Date Echéance"
)


class FakeMailClient:
    """Deterministic in-memory mailbox (never touches a real server)."""

    def __init__(self, messages: dict[str, list[EmailMessage]] | None = None):
        self.messages = {key: list(value) for key, value in (messages or {}).items()}
        self.folders: list[str] = []
        self.listed: list[str] = []
        self.moves: list[tuple[str, str, str]] = []
        self.deleted: list[str] = []

    def ensure_folder(self, folder: str) -> None:
        if folder not in self.folders:
            self.folders.append(folder)

    def list_messages(self, folder: str) -> list[EmailMessage]:
        self.listed.append(folder)
        return list(self.messages.get(folder, []))

    def move_message(
        self, message_id: str, source_folder: str, target_folder: str
    ) -> None:
        self.moves.append((message_id, source_folder, target_folder))

    def delete_message(self, message_id: str) -> None:
        self.deleted.append(message_id)


def make_store(tmp_path: Path) -> SqliteAccountingStore:
    return SqliteAccountingStore(tmp_path / "accounting.sqlite3")


def big_pdf(size: int = MIN_APD_PDF_BYTES + 100) -> bytes:
    prefix = b"%PDF-1.7\n"
    return prefix + b"x" * max(0, size - len(prefix))


def make_pdf_attachment(name: str = "facture.pdf", size: int | None = None):
    return EmailAttachment(
        name,
        big_pdf(size if size is not None else MIN_APD_PDF_BYTES + 100),
        "application/pdf",
    )


def mail(
    message_id: str,
    *,
    subject: str = "",
    body: str = "",
    attachments: tuple[EmailAttachment, ...] = (),
) -> EmailMessage:
    return EmailMessage(
        message_id=message_id,
        subject=subject,
        body=body,
        attachments=attachments,
    )


def invoice_row(
    number: str,
    *,
    status: str = "À relancer",
    date_piece: str = "02/01/2026",
    customer: str = "CLIENT TEST",
    company: str = "APD",
    ht: str = "327,22",
    tva: str = "65,44",
    ttc: str = "392,66",
    balance: str | None = None,
    due: str = "31/01/2026",
) -> dict[str, str]:
    return {
        "number": number,
        "status": status,
        "date_piece": date_piece,
        "customer": customer,
        "company": company,
        "ht": ht,
        "tva": tva,
        "ttc": ttc,
        "balance": ttc if balance is None else balance,
        "due": due,
    }


def build_csv_text() -> str:
    rows: list[dict[str, str]] = [
        invoice_row(
            "FA2001",
            customer="CALANDRE 18",
            ht="2 017,10",
            tva="403,42",
            ttc="2 420,52",
            due="31/01/2026",
        ),
        invoice_row(
            "FA2002",
            customer="CALANDRE 18",
            ht="4 220,11",
            tva="844,02",
            ttc="5 064,13",
            due="31/01/2026",
        ),
        invoice_row(
            "FA2003",
            customer="GP AUTO 58",
            ht="14 389,36",
            tva="2 877,87",
            ttc="17 267,23",
            due="28/02/2026",
        ),
        invoice_row(
            "FA2004",
            customer="3G AUTOS",
            ht="8 054,18",
            tva="1 610,83",
            ttc="9 665,01",
            due="31/03/2026",
        ),
    ]
    for index in range(5, 23):
        rows.append(
            invoice_row(
                f"FA20{index:02d}",
                status="Soldée" if index % 3 == 0 else "À relancer",
                customer=f"CLIENT {index:02d}",
                due="31/03/2026" if index % 2 else "28/02/2026",
            )
        )
    rows.append(
        invoice_row(
            "FA2023",
            customer="CLIENT 23",
            ht="327,16",
            tva="65,53",
            ttc="392,69",
            due="31/03/2026",
        )
    )
    lines = [CSV_HEADER]
    for row in rows:
        lines.append(
            ";".join(
                (
                    row["number"],
                    row["status"],
                    row["date_piece"],
                    row["customer"],
                    row["company"],
                    row["ht"],
                    row["tva"],
                    row["ttc"],
                    row["balance"],
                    row["due"],
                )
            )
        )
    lines.append(
        ";;;;;;;;34 897,87;6 979,59;41 877,46;41 877,46;"
    )
    return "\n".join(lines) + "\n"


PDF_SPECS: tuple[tuple[str, str, str, str, str, int], ...] = (
    ("FA2001", "CALANDRE 18", "123 456 789 00012", "FR 12 345678901", "LCR", 3),
    ("FA2002", "CALANDRE 18", "123 456 789 00012", "FR 12 345678901", "LCR", 2),
    ("FA2003", "GP AUTO 58", "234 567 891 00023", "FR 23 456789012", "LCR", 2),
    ("FA2004", "3G AUTOS", "345 678 912 00034", "FR 34 567890123", "LCR", 3),
)
for _index in range(5, 24):
    PDF_SPECS += (
        (
            f"FA20{_index:02d}",
            f"CLIENT {_index:02d}",
            f"456 789 123 0{_index:04d}",
            f"FR 45 6789012{_index % 10:02d}",
            "VIREMENT" if _index % 2 else "CHEQUE",
            1 if _index % 4 else 2,
        ),
    )


def build_multi_invoice_pdf(path: Path) -> Path:
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    for number, customer, siret, vat, payment, page_count in PDF_SPECS:
        first_page = (
            f"FACTURE {number}\n"
            f"Client : {customer}\n"
            f"SIRET : {siret}\n"
            f"TVA intracommunautaire : {vat}\n"
            f"Date : 02/01/2026\n"
            "Total HT : 327,22 EUR\n"
            "Total TVA : 65,44 EUR\n"
            "Total TTC : 392,66 EUR\n"
        )
        for page_index in range(1, page_count + 1):
            page = document.new_page()
            if page_index == 1:
                text = first_page
            elif page_index == page_count:
                text = (
                    f"FACTURE {number} - suite et fin\n"
                    f"Client : {customer}\n"
                    "Détail des prestations et annexes.\n"
                )
            else:
                text = (
                    f"FACTURE {number} - annexe {page_index - 1}\n"
                    f"Client : {customer}\n"
                )
            if page_index == page_count:
                if payment == "LCR":
                    text += "Règlement par LCR 30 jours fin de mois\n"
                elif payment == "CHEQUE":
                    text += "Règlement par Chèque\n"
                else:
                    text += "Règlement par Virement\n"
            page.insert_textbox(fitz.Rect(40, 40, 560, 800), text, fontsize=9)
    document.save(path)
    document.close()
    return path


def ofx_with_remittances() -> str:
    return """OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:UTF-8
CHARSET:UTF-8
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<BANKMSGSRSV1>
<STMTTRNRS>
<TRNUID>1
<STATUS><CODE>0<SEVERITY>INFO</STATUS>
<STMTRS>
<CURDEF>EUR
<BANKACCTFROM><BANKID>14505<ACCTID>145050000208001409859</BANKACCTFROM>
<BANKTRANLIST>
<DTSTART>20260101
<DTEND>20260331
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20260131
<TRNAMT>+7484,65
<FITID>CE-20260131-91374
<NAME>REMISE ENCAISSEMENT N 91374
<MEMO>ECHEANCE 31/01/2026
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20260228
<TRNAMT>+17267,23
<FITID>CE-20260228-91787
<NAME>REMISE ENCAISSEMENT N 91787
<MEMO>ECHEANCE 28/02/2026
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20260331
<TRNAMT>+9665,01
<FITID>CE-20260331-92174
<NAME>REMISE ENCAISSEMENT N 92174
<MEMO>ECHEANCE 31/03/2026
</STMTTRN>
</BANKTRANLIST>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>
"""


def ofx_with_94694() -> str:
    return """OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:UTF-8
CHARSET:UTF-8
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<BANKMSGSRSV1>
<STMTTRNRS>
<TRNUID>1
<STATUS><CODE>0<SEVERITY>INFO</STATUS>
<STMTRS>
<CURDEF>EUR
<BANKACCTFROM><BANKID>14505<ACCTID>145050000208001409859</BANKACCTFROM>
<BANKTRANLIST>
<DTSTART>20261001
<DTEND>20261031
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20261005
<TRNAMT>+9240,48
<FITID>CE-20261005-94694
<NAME>REMISE ENCAISSEMENT N 94694
<MEMO>BORDEREAU LCR 7 EFFETS
</STMTTRN>
</BANKTRANLIST>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>
"""


def ofx_with_transfers() -> str:
    lines: list[str] = []
    for fitid, label, amount in (
        ("VIR-SCTI-1", "VIR SEPA SCTI SARL", "1771,20"),
        ("VIR-SCTI-2", "VIR SEPA SCTI SARL", "1946,20"),
        ("VIR-YUCE-1", "VIR INST SARL CARROSSERIE YUCE", "654,92"),
        ("VIR-YUCE-2", "VIR INST SARL CARROSSERIE YUCE", "950,76"),
    ):
        lines.append(
            "<STMTTRN>\n"
            "<TRNTYPE>CREDIT\n"
            "<DTPOSTED>20260131\n"
            f"<TRNAMT>+{amount}\n"
            f"<FITID>{fitid}\n"
            f"<NAME>{label}\n"
            "<MEMO>REGLEMENT CLIENT\n"
            "</STMTTRN>\n"
        )
    return (
        "OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nSECURITY:NONE\n"
        "ENCODING:UTF-8\nCHARSET:UTF-8\nCOMPRESSION:NONE\n"
        "OLDFILEUID:NONE\nNEWFILEUID:NONE\n\n<OFX>\n<BANKMSGSRSV1>\n"
        "<STMTTRNRS>\n<TRNUID>1\n<STATUS><CODE>0<SEVERITY>INFO</STATUS>\n"
        "<STMTRS>\n<CURDEF>EUR\n<BANKACCTFROM><BANKID>14505"
        "<ACCTID>145050000208001409859</BANKACCTFROM>\n<BANKTRANLIST>\n"
        "<DTSTART>20260101\n<DTEND>20260201\n"
        + "".join(lines)
        + "</BANKTRANLIST>\n</STMTRS>\n</STMTTRNRS>\n</BANKMSGSRSV1>\n</OFX>\n"
    )


def make_invoice(
    number: str,
    customer: str,
    amount: str,
    *,
    due: date,
    method: str,
) -> CustomerInvoice:
    value = Decimal(amount)
    return CustomerInvoice(
        number=number,
        customer=customer,
        total_ht=(value / Decimal("1.2")).quantize(Decimal("0.01")),
        total_tva=(value - (value / Decimal("1.2")).quantize(Decimal("0.01"))),
        total_ttc=value,
        balance_due=value,
        due_date=due,
        payment_method=method,
        piece_date=date(2026, 1, 2),
    )


# -- Gmail P20 filter --------------------------------------------------------


@pytest.mark.parametrize(
    "subject, body, attachment, expected, reason",
    [
        ("Facture fournisseur", "", make_pdf_attachment(), True, ELIGIBLE_REASON),
        ("", "Merci de traiter la facture en pièce jointe", make_pdf_attachment(), True, ELIGIBLE_REASON),
        ("", "", make_pdf_attachment("relevé janvier.pdf"), True, ELIGIBLE_REASON),
        ("Newsletter", "", make_pdf_attachment("newsletter.pdf"), False, REASON_NO_KEYWORD),
        ("Facture", "", make_pdf_attachment(size=MIN_APD_PDF_BYTES - 1), False, REASON_PDF_TOO_SMALL),
        ("Facture", "", EmailAttachment("tableau.xlsx", b"x" * 7000), False, REASON_NO_PDF),
        ("Avoir", "", make_pdf_attachment(), True, ELIGIBLE_REASON),
        ("", "invoice", make_pdf_attachment(), True, ELIGIBLE_REASON),
    ],
)
def test_apd_eligibility_requires_pdf_and_keyword(subject, body, attachment, expected, reason):
    decision = evaluate_apd_email_eligibility(
        mail("m1", subject=subject, body=body, attachments=(attachment,))
    )
    assert decision.eligible is expected
    assert decision.reason == reason


def test_every_fiscal_keyword_is_accepted():
    for keyword in APD_ELIGIBILITY_KEYWORDS:
        decision = evaluate_apd_email_eligibility(
            mail(
                "m1",
                subject=f"Votre {keyword}",
                attachments=(make_pdf_attachment(),),
            )
        )
        assert decision.eligible, keyword


def test_bridge_inbox_filter_stages_only_eligible_and_never_deletes(tmp_path):
    client = FakeMailClient(
        {
            "INBOX": [
                mail("m1", subject="Facture", attachments=(make_pdf_attachment(),)),
                mail("m2", subject="Newsletter", attachments=(make_pdf_attachment("newsletter.pdf"),)),
                mail("m3", subject="Facture", attachments=(make_pdf_attachment(size=100),)),
            ]
        }
    )
    bridge = EmailInboxBridge(
        tenant_id=TENANT,
        root=tmp_path,
        client=client,
        process_attachments=False,
        mailbox_prefix=APD_MAILBOX_PREFIX,
        min_attachment_bytes=MIN_APD_PDF_BYTES,
    )
    report = bridge.fetch_and_stage_emails(folder="INBOX")
    assert set(client.folders) == {f"APD/{state}" for state in MAILBOX_STATES}
    assert report.source_folder == "INBOX"
    assert report.scanned_messages == 3
    assert report.messages_ignored == 2
    assert report.staged == 1
    assert report.records[0].filename == "facture.pdf"
    assert (tmp_path / TENANT / "00_INBOX" / "facture.pdf").is_file()
    assert client.moves == []
    assert client.deleted == []
    assert {item.reason for item in report.eligibility} == {
        ELIGIBLE_REASON,
        REASON_NO_KEYWORD,
        REASON_PDF_TOO_SMALL,
    }


def test_bridge_falls_back_to_inbox_when_apd_label_is_empty(tmp_path):
    client = FakeMailClient(
        {
            "APD/00_INBOX": [],
            "INBOX": [mail("m1", body="facture", attachments=(make_pdf_attachment(),))],
        }
    )
    bridge = EmailInboxBridge(
        tenant_id=TENANT,
        root=tmp_path,
        client=client,
        process_attachments=False,
        mailbox_prefix=APD_MAILBOX_PREFIX,
        min_attachment_bytes=MIN_APD_PDF_BYTES,
    )
    report = bridge.fetch_and_stage_emails()
    assert report.fallback_used is True
    assert report.source_folder == "INBOX"
    assert report.staged == 1
    assert client.listed == ["APD/00_INBOX", "INBOX"]
    assert client.moves == []


def test_dry_run_cli_folder_inbox_fallback_and_summary(tmp_path, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APD_INVOICE_EMAIL=facturesapd@gmail.com\nGMAIL_APP_PASSWORD=secret\n",
        encoding="utf-8",
    )
    client = FakeMailClient(
        {
            "APD/00_INBOX": [],
            "INBOX": [
                mail("m1", subject="Facture", attachments=(make_pdf_attachment(),)),
                mail("m2", subject="Publicité", attachments=(make_pdf_attachment("publicite.pdf"),)),
            ],
        }
    )
    code = fetch_main(
        ["--dry-run", "--limit", "39", "--env-file", str(env_file)],
        client=client,
        environ={},
    )
    output = capsys.readouterr().out
    assert code == 0
    assert "Fallback automatique" in output
    assert "Filtre lexical P20" in output
    assert "1 eligible(s), 1 ignore(s)" in output
    assert "aucun message supprime" in output
    assert client.listed == ["APD/00_INBOX", "INBOX"]
    assert client.folders == []
    assert client.moves == []


def test_dry_run_cli_explicit_folder_inbox_does_not_fallback(tmp_path, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APD_INVOICE_EMAIL=facturesapd@gmail.com\nGMAIL_APP_PASSWORD=secret\n",
        encoding="utf-8",
    )
    client = FakeMailClient({"INBOX": [mail("m1", subject="Facture", attachments=(make_pdf_attachment(),))]})
    code = fetch_main(
        [
            "--folder",
            "INBOX",
            "--dry-run",
            "--limit",
            "39",
            "--env-file",
            str(env_file),
        ],
        client=client,
        environ={},
    )
    assert code == 0
    assert client.listed == ["INBOX"]
    assert client.moves == []


def test_build_dry_run_report_counts_eligible_and_ignored_messages():
    messages = [
        mail("m1", subject="Facture", attachments=(make_pdf_attachment(),)),
        mail("m2", subject="Newsletter", attachments=(make_pdf_attachment("newsletter.pdf"),)),
        mail("m3", subject="Facture", attachments=(make_pdf_attachment(size=10),)),
    ]
    report = build_dry_run_report(
        messages, folder="INBOX", filter_lexical=True, fallback_used=True
    )
    assert report.filter_applied is True
    assert report.fallback_used is True
    assert report.messages_eligible == 1
    assert report.messages_ignored == 2
    assert report.attachments_seen == 3


# -- Sage CSV / XLSX / XML and grouped PDF ----------------------------------


def test_sage_csv_imports_23_invoices_and_ignores_total_row(tmp_path):
    payload = build_csv_text().encode("cp1252")
    invoices = parse_sage_customer_invoices(
        payload, filename="Liste des factures clients.csv"
    )
    assert len(invoices) == 23
    assert sum(item.total_ht for item in invoices) == Decimal("34897.87")
    assert sum(item.total_tva for item in invoices) == Decimal("6979.59")
    assert sum(item.total_ttc for item in invoices) == Decimal("41877.46")
    assert invoices[0].number == "FA2001"
    assert invoices[0].customer == "CALANDRE 18"
    assert invoices[0].due_date == date(2026, 1, 31)
    assert invoices[0].sage_status == "À relancer"


def test_sage_csv_supports_utf8_sig_and_xlsx_and_xml(tmp_path):
    payload = build_csv_text().encode("utf-8-sig")
    assert len(parse_sage_customer_invoices(payload, filename="factures.csv")) == 23

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(CSV_HEADER.split(";"))
    sheet.append(
        ["FA9001", "À relancer", "02/01/2026", "CLIENT XML", "APD", "100,00", "20,00", "120,00", "120,00", "31/01/2026"]
    )
    xlsx_path = tmp_path / "factures.xlsx"
    workbook.save(xlsx_path)
    xlsx_invoices = parse_sage_customer_invoices(xlsx_path)
    assert len(xlsx_invoices) == 1
    assert xlsx_invoices[0].total_ttc == Decimal("120.00")

    xml = """<?xml version="1.0" encoding="utf-8"?>
<factures>
  <facture>
    <npiece>FA9002</npiece>
    <statut>À relancer</statut>
    <datepiece>02/01/2026</datepiece>
    <client>CLIENT XML</client>
    <societe>APD</societe>
    <totalht>200,00</totalht>
    <totaltva>40,00</totaltva>
    <totalttc>240,00</totalttc>
    <soldedu>240,00</soldedu>
    <dateecheance>31/01/2026</dateecheance>
  </facture>
</factures>"""
    xml_invoices = parse_sage_customer_invoices(
        xml.encode("utf-8"), filename="factures.xml"
    )
    assert len(xml_invoices) == 1
    assert xml_invoices[0].number == "FA9002"
    assert xml_invoices[0].total_ttc == Decimal("240.00")


def test_grouped_pdf_is_split_logically_and_extracts_payment_terms(tmp_path):
    pdf_path = build_multi_invoice_pdf(tmp_path / "factures clients.pdf")
    batch = parse_multi_invoice_pdf(pdf_path)
    assert batch.page_count == sum(spec[5] for spec in PDF_SPECS)
    assert len(batch.invoices) == 23
    first = batch.by_number()["FA2001"]
    assert first.page_start == 1
    assert first.page_end == 3
    assert first.client_siret == "12345678900012"
    assert first.client_vat_number == "FR12345678901"
    assert first.payment_method == "LCR"
    assert "30 jours fin de mois" in first.payment_terms
    assert batch.by_number()["FA2005"].payment_method == "VIREMENT"
    assert batch.by_number()["FA2006"].payment_method == "CHEQUE"


def test_import_batch_merges_pdf_and_populates_payment_rules(tmp_path):
    store = make_store(tmp_path)
    report = import_customer_invoice_batch(
        store,
        TENANT,
        build_csv_text().encode("cp1252"),
        pdf_source=build_multi_invoice_pdf(tmp_path / "factures clients.pdf"),
        filename="Liste des factures clients.csv",
    )
    assert report.created == 23
    assert report.total_ht == Decimal("34897.87")
    assert report.total_tva == Decimal("6979.59")
    assert report.total_ttc == Decimal("41877.46")
    assert report.errors == ()
    rows = store.list_customer_invoices(TENANT)
    assert len(rows) == 23
    assert all(row["category"] == "VENTES_CLIENTS" for row in rows)
    chosen = next(row for row in rows if row["invoice_id"] == "FA2001")
    assert chosen["client_siret"] == "12345678900012"
    assert chosen["client_vat_number"] == "FR12345678901"
    assert chosen["payment_terms"] == "Règlement par LCR 30 jours fin de mois"
    assert store.get_counterparty_payment_rule(TENANT, "CALANDRE 18")[
        "expected_payment_method"
    ] == "LCR"
    assert store.get_counterparty_payment_rule(TENANT, "CLIENT 05")[
        "expected_payment_method"
    ] == "VIREMENT"


def test_sales_workbook_contains_pending_status_column(tmp_path):
    store = make_store(tmp_path)
    import_customer_invoice_batch(
        store,
        TENANT,
        build_csv_text().encode("cp1252"),
        pdf_source=build_multi_invoice_pdf(tmp_path / "factures clients.pdf"),
    )
    export = export_customer_sales_workbook(
        TENANT, store=store, export_dir=tmp_path / "exports"
    )
    assert export.path.name == "TABLEAU_VENTES_CLIENTS.xlsx"
    assert export.row_count == 23
    assert export.reconciled == 0
    assert export.pending == 23
    workbook = load_workbook(export.path)
    sheet = workbook.active
    headers = [cell.value for cell in sheet[1]]
    assert tuple(headers) == SALES_WORKBOOK_HEADERS
    status_column = headers.index("Statut Rapprochement Bancaire") + 1
    assert status_column == 15
    statuses = {
        sheet.cell(row=index, column=status_column).value
        for index in range(2, 25)
    }
    assert statuses == {PENDING_STATUS}
    assert sheet.cell(row=2, column=1).value == "FA2001"
    workbook.close()


# -- Exact lettering --------------------------------------------------------


def test_find_exact_combination_is_cent_exact_and_deterministic():
    invoices = [
        make_invoice("FA1", "A", "2420.52", due=date(2026, 1, 31), method="LCR"),
        make_invoice("FA2", "A", "5064.13", due=date(2026, 1, 31), method="LCR"),
        make_invoice("FA3", "B", "100.00", due=date(2026, 2, 28), method="LCR"),
    ]
    combination = find_exact_invoice_combination(
        invoices, Decimal("7484.65"), preferred_date=date(2026, 1, 31)
    )
    assert combination is not None
    assert [item.number for item in combination] == ["FA1", "FA2"]
    assert sum(item.amount for item in combination) == Decimal("7484.65")
    assert find_exact_invoice_combination(
        invoices, Decimal("7484.66"), preferred_date=date(2026, 1, 31)
    ) is None


def test_remittances_91374_91787_92174_and_workbook_lettering(tmp_path):
    store = make_store(tmp_path)
    import_customer_invoice_batch(
        store,
        TENANT,
        build_csv_text().encode("cp1252"),
        pdf_source=build_multi_invoice_pdf(tmp_path / "factures clients.pdf"),
    )
    import_ofx_statement(store, TENANT, ofx_with_remittances())
    report = reconcile_customer_invoices(store, TENANT, window_days=60)
    assert report.reconciled_invoices == 4
    assert report.pending_invoices == 19
    by_number = {
        row["invoice_id"]: row for row in store.list_customer_invoices(TENANT)
    }
    assert by_number["FA2001"]["reconciliation_status"] == PAID_STATUS
    assert by_number["FA2001"]["reconciliation_reference"] == "REMISE 91374"
    assert by_number["FA2001"]["reconciliation_date"] == "2026-01-31"
    assert by_number["FA2002"]["reconciliation_status"] == PAID_STATUS
    assert by_number["FA2002"]["reconciliation_reference"] == "REMISE 91374"
    assert by_number["FA2003"]["reconciliation_status"] == PAID_STATUS
    assert by_number["FA2003"]["reconciliation_reference"] == "REMISE 91787"
    assert by_number["FA2003"]["reconciliation_date"] == "2026-02-28"
    assert by_number["FA2004"]["reconciliation_status"] == PAID_STATUS
    assert by_number["FA2004"]["reconciliation_reference"] == "REMISE 92174"
    assert by_number["FA2004"]["reconciliation_date"] == "2026-03-31"
    assert by_number["FA2005"]["reconciliation_status"] == PENDING_STATUS
    payment = report.by_reference("91374")
    assert payment is not None
    assert payment.matched is True
    assert payment.allocated_total == Decimal("7484.65")
    assert payment.invoice_numbers == ("FA2001", "FA2002")

    export = export_customer_sales_workbook(
        TENANT, store=store, export_dir=tmp_path / "exports"
    )
    assert export.reconciled == 4
    assert export.pending == 19
    workbook = load_workbook(export.path)
    sheet = workbook.active
    headers = [cell.value for cell in sheet[1]]
    status_column = headers.index("Statut Rapprochement Bancaire") + 1
    reference_column = headers.index("Référence OFX") + 1
    date_column = headers.index("Date Rapprochement") + 1
    rows = {
        sheet.cell(row=index, column=1).value: index
        for index in range(2, sheet.max_row + 1)
    }
    assert sheet.cell(row=rows["FA2001"], column=status_column).value == PAID_STATUS
    assert sheet.cell(row=rows["FA2001"], column=reference_column).value == "REMISE 91374"
    assert sheet.cell(row=rows["FA2001"], column=date_column).value.date() == date(2026, 1, 31)
    assert sheet.cell(row=rows["FA2005"], column=status_column).value == PENDING_STATUS
    workbook.close()


def test_bordereau_94694_is_split_into_seven_exact_invoices(tmp_path):
    store = make_store(tmp_path)
    invoices = [
        make_invoice("FA3001", "CALANDRE 18", "144.00", due=date(2026, 1, 15), method="LCR"),
        make_invoice("FA3002", "CALANDRE 18", "669.07", due=date(2026, 2, 15), method="LCR"),
        make_invoice("FA3003", "CARROSSERIE VINOLIENNE", "225.60", due=date(2026, 1, 15), method="LCR"),
        make_invoice("FA3004", "GP AUTO 36", "1045.88", due=date(2026, 1, 15), method="LCR"),
        make_invoice("FA3005", "GP AUTO 58", "3658.21", due=date(2026, 1, 15), method="LCR"),
        make_invoice("FA3006", "SAVOIE JANTES", "2000.08", due=date(2026, 2, 15), method="LCR"),
        make_invoice("FA3007", "3G AUTOS", "1497.64", due=date(2026, 2, 15), method="LCR"),
    ]
    import_customer_invoices(store, TENANT, invoices, update_payment_rules=False)
    import_ofx_statement(store, TENANT, ofx_with_94694())
    report = reconcile_customer_invoices(store, TENANT, bordereaux=[LCR_TEXT])
    assert report.reconciled_invoices == 7
    assert report.pending_invoices == 0
    payment = report.by_reference("94694")
    assert payment is not None
    assert payment.matched is True
    assert payment.bordereau_reference == "32000028917640"
    assert payment.allocated_total == Decimal("9240.48")
    assert len(payment.invoices) == 7
    assert set(payment.invoice_numbers) == {f"FA300{index}" for index in range(1, 8)}
    by_number = {
        row["invoice_id"]: row for row in store.list_customer_invoices(TENANT)
    }
    assert all(
        by_number[f"FA300{index}"]["reconciliation_status"] == PAID_STATUS
        for index in range(1, 8)
    )
    assert all(
        by_number[f"FA300{index}"]["reconciliation_reference"] == "REMISE 94694"
        for index in range(1, 8)
    )


def test_direct_transfers_scti_and_carrosserie_yucell(tmp_path):
    store = make_store(tmp_path)
    invoices = [
        make_invoice("FA4001", "SCTI SARL", "1771.20", due=date(2026, 1, 31), method="VIREMENT"),
        make_invoice("FA4002", "SCTI SARL", "1946.20", due=date(2026, 2, 28), method="VIREMENT"),
        make_invoice("FA4003", "SARL CARROSSERIE YUCE", "654.92", due=date(2026, 1, 31), method="VIREMENT"),
        make_invoice("FA4004", "SARL CARROSSERIE YUCE", "950.76", due=date(2026, 2, 28), method="VIREMENT"),
    ]
    import_customer_invoices(store, TENANT, invoices, update_payment_rules=False)
    import_ofx_statement(store, TENANT, ofx_with_transfers())
    report = reconcile_customer_invoices(store, TENANT)
    assert report.reconciled_invoices == 4
    assert report.pending_invoices == 0
    by_number = {
        row["invoice_id"]: row for row in store.list_customer_invoices(TENANT)
    }
    for number in ("FA4001", "FA4002", "FA4003", "FA4004"):
        assert by_number[number]["reconciliation_status"] == PAID_STATUS
        assert by_number[number]["reconciliation_reference"].startswith("VIREMENT ")
    assert counterparty_names_match("SARL CARROSSERIE YUCE", "CARROSSERIE YUCELL")
    assert counterparty_names_match("SCTI", "SCTI SARL")
    assert not counterparty_names_match("SCTI SARL", "GP AUTO 58")


def test_pending_invoices_are_visible_before_lettering(tmp_path):
    store = make_store(tmp_path)
    invoices = [
        make_invoice("FA5001", "CALANDRE 18", "2420.52", due=date(2026, 1, 31), method="LCR")
    ]
    import_customer_invoices(store, TENANT, invoices, update_payment_rules=False)
    pending = load_pending_invoices(store, TENANT)
    assert len(pending) == 1
    assert pending[0].pending is True
    assert pending[0].number == "FA5001"

# -- P20 closure: LCR priority and N-invoice grouping ------------------------


def _ofx_single_credit(fitid: str, label: str, amount: str, date: str) -> str:
    return (
        "OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nSECURITY:NONE\n"
        "ENCODING:UTF-8\nCHARSET:UTF-8\nCOMPRESSION:NONE\n"
        "OLDFILEUID:NONE\nNEWFILEUID:NONE\n\n<OFX>\n<BANKMSGSRSV1>\n"
        "<STMTTRNRS>\n<TRNUID>1\n<STATUS><CODE>0<SEVERITY>INFO</STATUS>\n"
        "<STMTRS>\n<CURDEF>EUR\n<BANKACCTFROM><BANKID>14505"
        "<ACCTID>145050000208001409859</BANKACCTFROM>\n<BANKTRANLIST>\n"
        f"<DTSTART>{date[:8]}\n<DTEND>{date[:8]}\n<STMTTRN>\n"
        "<TRNTYPE>CREDIT\n"
        f"<DTPOSTED>{date}\n<TRNAMT>+{amount}\n<FITID>{fitid}\n"
        f"<NAME>{label}\n<MEMO>REGLEMENT CLIENT\n</STMTTRN>\n"
        "</BANKTRANLIST>\n</STMTRS>\n</STMTTRNRS>\n</BANKMSGSRSV1>\n</OFX>\n"
    )


def test_lcr_priority_survives_one_off_cheque_mention(tmp_path):
    store = make_store(tmp_path)
    store.set_counterparty_payment_rule(TENANT, "CARROSSERIE VINOLIENNE", "LCR")
    invoice = make_invoice(
        "FA16464",
        "CARROSSERIE VINOLIENNE",
        "82.63",
        due=date(2026, 10, 16),
        method="CHEQUE",
    )
    report = import_customer_invoices(
        store, TENANT, [invoice], update_payment_rules=True
    )
    assert report.created == 1
    rule = store.get_counterparty_payment_rule(
        TENANT, "CARROSSERIE VINOLIENNE"
    )
    assert rule is not None
    assert rule["expected_payment_method"] == "LCR"
    assert "CHEQUE" in rule["fallback_methods"]
    assert "VIREMENT" in rule["fallback_methods"]


def test_lcr_bordereau_allocation_groups_two_small_same_client_invoices(tmp_path):
    store = make_store(tmp_path)
    invoices = [
        make_invoice(
            "FA16464",
            "CARROSSERIE VINOLIENNE",
            "82.63",
            due=date(2026, 10, 16),
            method="CHEQUE",
        ),
        make_invoice(
            "FA16465",
            "CARROSSERIE VINOLIENNE",
            "142.97",
            due=date(2026, 10, 16),
            method="LCR",
        ),
    ]
    import_customer_invoices(store, TENANT, invoices, update_payment_rules=False)
    lcr = """Bordereau d'effets a l'encaissement
Reference de transmission : 12345678901234
Compte : 145050000208001409859
Nombre d'operations : 1
Montant total au credit : 225,60 EUR

Tire | Rang | Domiciliation | Acceptation | Echeance | Montant au credit
CARROSSERIE VINOLIENNE | 1 | LYON | ACC | 16/10/2026 | 225,60
"""
    import_ofx_statement(
        store,
        TENANT,
        _ofx_single_credit(
            "CE-20261021-94695",
            "REMISE ENCAISSEMENT N 94695",
            "225,60",
            "20261021",
        ),
    )
    report = reconcile_customer_invoices(store, TENANT, bordereaux=[lcr])
    assert report.reconciled_invoices == 2
    assert report.pending_invoices == 0
    payment = report.by_reference("94695")
    assert payment is not None
    assert payment.matched is True
    assert payment.allocated_total == Decimal("225.60")
    assert payment.invoice_numbers == ("FA16464", "FA16465")
    by_number = {
        row["invoice_id"]: row for row in store.list_customer_invoices(TENANT)
    }
    assert by_number["FA16464"]["reconciliation_status"] == PAID_STATUS
    assert by_number["FA16465"]["reconciliation_status"] == PAID_STATUS


def test_one_transfer_or_one_cheque_groups_two_small_same_client_invoices(tmp_path):
    transfer_store = make_store(tmp_path / "transfer")
    transfer_store.set_counterparty_payment_rule(
        TENANT, "SCTI SARL", "LCR", fallback_methods=["CHEQUE", "VIREMENT"]
    )
    import_customer_invoices(
        transfer_store,
        TENANT,
        [
            make_invoice(
                "FA7001",
                "SCTI SARL",
                "82.63",
                due=date(2026, 10, 16),
                method="CHEQUE",
            ),
            make_invoice(
                "FA7002",
                "SCTI SARL",
                "142.97",
                due=date(2026, 10, 16),
                method="LCR",
            ),
        ],
        update_payment_rules=False,
    )
    import_ofx_statement(
        transfer_store,
        TENANT,
        _ofx_single_credit(
            "VIR-SCTI-GROUPE",
            "VIR SEPA SCTI SARL",
            "225,60",
            "20261021",
        ),
    )
    transfer_report = reconcile_customer_invoices(transfer_store, TENANT)
    assert transfer_report.reconciled_invoices == 2
    transfer_payment = transfer_report.payments[0]
    assert transfer_payment.matched is True
    assert transfer_payment.allocated_total == Decimal("225.60")
    assert transfer_payment.invoice_numbers == ("FA7001", "FA7002")

    cheque_store = make_store(tmp_path / "cheque")
    cheque_store.set_counterparty_payment_rule(
        TENANT, "CARROSSERIE VINOLIENNE", "LCR"
    )
    import_customer_invoices(
        cheque_store,
        TENANT,
        [
            make_invoice(
                "FA8001",
                "CARROSSERIE VINOLIENNE",
                "82.63",
                due=date(2026, 10, 16),
                method="CHEQUE",
            ),
            make_invoice(
                "FA8002",
                "CARROSSERIE VINOLIENNE",
                "142.97",
                due=date(2026, 10, 16),
                method="LCR",
            ),
        ],
        update_payment_rules=False,
    )
    import_ofx_statement(
        cheque_store,
        TENANT,
        _ofx_single_credit(
            "REM-CHEQUE-GROUPE",
            "REMISE CHEQUES N 70001",
            "225,60",
            "20261021",
        ),
    )
    cheque_report = reconcile_customer_invoices(cheque_store, TENANT)
    assert cheque_report.reconciled_invoices == 2
    cheque_payment = cheque_report.payments[0]
    assert cheque_payment.kind == REMISE_CHEQUES
    assert cheque_payment.matched is True
    assert cheque_payment.allocated_total == Decimal("225.60")
    assert cheque_payment.invoice_numbers == ("FA8001", "FA8002")

