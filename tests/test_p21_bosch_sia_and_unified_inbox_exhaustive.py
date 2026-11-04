"""P21 - Exhaustive offline coverage of multi-piece PDFs, Bosch/Sia, Boxtal,
recursive Outlook unpacking and the unified ``00_INBOX`` dispatcher.

Every fixture PDF is generated locally with PyMuPDF: the suite never depends on
files from the user's real ``~/Downloads`` folder.
"""

from __future__ import annotations

import base64
from datetime import date
from decimal import Decimal
from email.message import EmailMessage as StdEmailMessage
from pathlib import Path

import pytest
from openpyxl import load_workbook

from schemas.folder_state_schema import DocumentCategory
from services.accounting_spreadsheet_export_service import (
    CATEGORY_OVERVIEW_FILENAME,
    CategoryOverviewExporter,
    SupplierLedgerExporter,
)
from services.bosch_sia_invoice_parser import (
    BOSCH_SIA_SUPPLIER_NAME,
    ORIGIN_MISSING_WARNING,
    collect_bosch_sia_origin_alerts,
    import_bosch_sia_document,
    parse_bosch_sia_invoice,
)
from services.customer_invoice_batch_importer import (
    CustomerInvoice,
    export_customer_sales_workbook,
    import_customer_invoices,
)
from services.document_flow_watcher import DEFAULT_TENANT, DocumentFlowWatcher
from services.email_inbox_bridge import (
    ELIGIBLE_REASON,
    evaluate_apd_email_eligibility,
    parse_email_message,
)
from services.folder_tree_service import ensure_tree
from services.inbox_ingestion_service import InboxIngestionService
from services.pdf_splitter_and_linker import (
    PIECE_LINK_HEADER,
    split_customer_invoices_pdf,
    split_supplier_pdf,
)
from services.settled_invoice_parser import (
    BOXTAL,
    FREE_TELECOM,
    SMTRT,
    SESSION_PAID_STATUS,
    find_settled_ofx_match,
    import_settled_invoice,
    parse_settled_invoice,
)
from services.sqlite_accounting_store import SqliteAccountingStore
from scripts.refresh_cockpit import main as refresh_cockpit_main

TENANT = "APD"


def make_store(tmp_path: Path) -> SqliteAccountingStore:
    return SqliteAccountingStore(tmp_path / "accounting.sqlite3")


def make_text_pdf(path: Path, page_texts: list[str]) -> Path:
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    for text in page_texts:
        page = document.new_page()
        page.insert_textbox(fitz.Rect(40, 40, 560, 800), text, fontsize=9)
    document.save(path)
    document.close()
    return path


def customer_invoice(number: str, customer: str, ttc: str, due: date) -> CustomerInvoice:
    value = Decimal(ttc)
    ht = (value / Decimal("1.2")).quantize(Decimal("0.01"))
    return CustomerInvoice(
        number=number,
        customer=customer,
        total_ht=ht,
        total_tva=value - ht,
        total_ttc=value,
        balance_due=value,
        due_date=due,
        piece_date=date(2026, 2, 28),
        payment_method="LCR",
    )


def build_customer_batch_pdf(path: Path) -> Path:
    return make_text_pdf(
        path,
        [
            "Facture N° FA16443\nClient : GP AUTO 36\nDate : 28/02/2026\n"
            "Total HT : 100,00 EUR\nTotal TVA : 20,00 EUR\nTotal TTC : 120,00 EUR\n"
            "Règlement par LCR\n",
            "FA16443 - annexe 1\nClient : GP AUTO 36\n",
            "Facture N° FA16444\nClient : CHOLLET\nDate : 31/03/2026\n"
            "Total HT : 200,00 EUR\nTotal TVA : 40,00 EUR\nTotal TTC : 240,00 EUR\n"
            "Règlement par LCR\n",
        ],
    )


def bosch_text(
    kind: str,
    number: str,
    day: str,
    ht: str,
    tva: str,
    ttc: str,
    *,
    origin: str | None = None,
    refac: bool = False,
) -> str:
    lines = [
        "Pos. No. d'article Qt. Prix tarif Remises/ Montant net",
        "Robert Bosch (France) S.A.S",
        "R.C.S. Bobigny B 572 067 684, N° Siren: 572 067 684 / NI: FR 09 572 067 684",
        "Client livré(43978631):",
        "ASSISTANCE PEINTURES DIFFUSION SAS",
        f"{kind} {number} Du {day}",
    ]
    if origin:
        lines.append(f"Document d'origine : {origin}, 04.09.2026")
    if refac:
        lines.append("AVOIR ET REFAC CONDITIONS ERRONEES")
    lines.extend(
        [
            "TVA Description Montant Valeur TVA Montant TTC",
            "avoir" if kind == "Avoir" else "debit",
            "20.00%",
            f"      {ht}       {tva}",
            f"      {ttc} EUR",
            f"Echeance de paiement 13.11.2026",
        ]
    )
    return "\n".join(lines)


def boxtal_text() -> str:
    return "\n".join(
        [
            "BOXTAL SAS au capital de 652 018 EUR, 69 Boulevard Haussmann, 75008 Paris",
            "N° SIRET : 489 553 081 00063, N° RCS : RCS PARIS B 489 553 081",
            "N° TVA intra communautaire : FR 06 489 553 081",
            "Facture n° 2600023334 du 12 janvier 2026",
            "assistance peinture diffusion",
            "145 Chemin des Roches",
            "Total HT",
            "54,56 EUR",
            "dont 54,56 EUR soumis a 20% de TVA",
            "Total TVA",
            "10,91 EUR",
            "Total TTC",
            "65,47 EUR",
            "Règle",
            "-65,47 EUR",
            "Net à payer",
            "0,00 EUR",
            "Synthèse de vos règlements",
            "Date",
            "Moyen",
            "Montant",
            "12/01/2026",
            "Carte Bancaire (T2601WEQRIN)",
            "65,47 EUR",
        ]
    )


def free_text() -> str:
    return "\n".join(
        [
            "FREE TELECOM",
            "Facture n° 30827543 du 05/02/2026",
            "Total HT 30,00 EUR",
            "Total TVA 6,00 EUR",
            "Total TTC 36,00 EUR",
            "Net à payer 0,00 EUR",
            "Prélèvement SEPA",
        ]
    )


def smtrt_text() -> str:
    return "\n".join(
        [
            "SMTRT",
            "Facture n° SM2026001 du 10/03/2026",
            "Total HT 100,00 EUR",
            "Total TVA 20,00 EUR",
            "Total TTC 120,00 EUR",
            "Virement",
        ]
    )


def ofx_with_boxtal() -> str:
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
<DTEND>20260131
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20260115
<TRNAMT>-65,47
<FITID>CB-BOXTAL-120126
<NAME>CB Boxtal FACT 120126
<MEMO>FACTURE BOXTAL
</STMTTRN>
</BANKTRANLIST>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>
"""


# -- 1. Physical split + relative OneDrive links ----------------------------


def test_customer_pdf_is_physically_split_and_excel_links_are_relative(tmp_path):
    pdf = build_customer_batch_pdf(tmp_path / "factures clients.pdf")
    report = split_customer_invoices_pdf(pdf, tmp_path / "exports")
    assert report.page_count == 3
    assert report.written == 2
    names = [piece.filename for piece in report.pieces]
    assert names[0] == "FA16443_2026-02-28_GP_AUTO_36.pdf"
    assert names[1] == "FA16444_2026-03-31_CHOLLET.pdf"
    first = report.pieces[0]
    assert first.start_page == 1 and first.end_page == 2
    assert first.path is not None and first.path.is_file()
    fitz = pytest.importorskip("fitz")
    split_document = fitz.open(first.path)
    assert split_document.page_count == 2
    split_document.close()

    store = make_store(tmp_path)
    import_customer_invoices(
        store,
        TENANT,
        [
            customer_invoice("FA16443", "GP AUTO 36", "120.00", date(2026, 2, 28)),
            customer_invoice("FA16444", "CHOLLET", "240.00", date(2026, 3, 31)),
        ],
        update_payment_rules=False,
    )
    export = export_customer_sales_workbook(
        TENANT, store=store, export_dir=tmp_path / "exports"
    )
    workbook = load_workbook(export.path)
    sheet = workbook.active
    headers = [cell.value for cell in sheet[1]]
    assert headers[-1] == PIECE_LINK_HEADER
    link_column = headers.index(PIECE_LINK_HEADER) + 1
    link = sheet.cell(row=2, column=link_column).value
    assert link and link.startswith("PIECES_JOINTES/VENTES/")
    assert ":" not in link
    target = (export.path.parent / link).resolve()
    assert target.is_file()
    assert sheet.cell(row=2, column=link_column).hyperlink.target == link
    workbook.close()


def test_supplier_pdf_split_uses_purchase_naming(tmp_path):
    pdf = make_text_pdf(
        tmp_path / "lot fournisseur.pdf",
        [
            bosch_text("Facture", "3956106093", "14.09.2026", "135,00", "27,00", "162,00"),
            bosch_text("Avoir", "3956106092", "14.09.2026", "150,00", "30,00", "180,00"),
        ],
    )
    report = split_supplier_pdf(pdf, tmp_path / "exports", "BOSCH_SIA")
    assert [piece.document_type for piece in report.pieces] == ["FACTURE", "AVOIR"]
    assert report.pieces[0].filename == (
        "BOSCH_SIA_FACTURE_3956106093_2026-09-14.pdf"
    )


# -- 2. Bosch / Sia ----------------------------------------------------------


def test_bosch_sia_credit_notes_are_detected_from_content_not_filename(tmp_path):
    credit = make_text_pdf(
        tmp_path / "Y16V_SD_INVOICE202609243956114620.pdf",
        [bosch_text("Avoir", "3956114620", "24.09.2026", "60,00", "12,00", "72,00", origin="3956107265")],
    )
    pair = make_text_pdf(
        tmp_path / "_data_-_[archived__IC].pdf",
        [bosch_text("Avoir", "3956106092", "14.09.2026", "150,00", "30,00", "180,00", origin="3956096339", refac=True)],
    )
    invoice = make_text_pdf(
        tmp_path / "_data_-_[archived__IC][1].pdf",
        [bosch_text("Facture", "3956106093", "14.09.2026", "135,00", "27,00", "162,00", origin="3956096339", refac=True)],
    )
    first = parse_bosch_sia_invoice(credit)
    second = parse_bosch_sia_invoice(
        pair, known_document_numbers={"3956096339"}
    )
    third = parse_bosch_sia_invoice(
        invoice, known_document_numbers={"3956096339"}
    )
    assert first.document_type == "AVOIR"
    assert first.normalized_filename == "BOSCH_SIA_AVOIR_3956114620_2026-09-24.pdf"
    assert second.is_refacturation_pair is True
    assert third.document_type == "FACTURE"
    assert third.supplier == BOSCH_SIA_SUPPLIER_NAME
    alerts = collect_bosch_sia_origin_alerts(
        [first, second, third], known_document_numbers={"3956096339"}
    )
    assert alerts == (ORIGIN_MISSING_WARNING.format(number="3956107265"),)


def test_bosch_sia_ledger_debits_credit_notes_and_warns_on_missing_origin(tmp_path):
    store = make_store(tmp_path)
    credit = parse_bosch_sia_invoice(
        make_text_pdf(
            tmp_path / "credit.pdf",
            [bosch_text("Avoir", "3956114620", "24.09.2026", "60,00", "12,00", "72,00", origin="3956107265")],
        ),
        known_document_numbers={"3956106093"},
    )
    invoice = parse_bosch_sia_invoice(
        make_text_pdf(
            tmp_path / "invoice.pdf",
            [bosch_text("Facture", "3956106093", "14.09.2026", "135,00", "27,00", "162,00", origin="3956096339")],
        ),
        known_document_numbers={"3956096339"},
    )
    assert credit.warnings == (ORIGIN_MISSING_WARNING.format(number="3956107265"),)
    import_bosch_sia_document(store, TENANT, invoice)
    import_bosch_sia_document(store, TENANT, credit)
    rows = store.list_supplier_ledger(TENANT, "BOSCH")
    assert len(rows) == 2
    credit_row = next(row for row in rows if row["document_type"] == "AVOIR")
    assert credit_row["amount_ttc"] == "-72.00"
    ledger = SupplierLedgerExporter(store, export_dir=tmp_path / "exports").export_supplier_ledger(
        TENANT
    )
    workbook = load_workbook(ledger)
    sheet = workbook.active
    values = [
        str(sheet.cell(row=index, column=4).value).casefold()
        for index in range(2, 4)
    ]
    assert any("facture d'origine 3956107265" in value for value in values)
    workbook.close()


# -- 3. Already-settled invoices (Boxtal / Free / SMTRT) --------------------


def test_boxtal_uses_total_ttc_not_net_to_pay_and_matches_ofx(tmp_path):
    invoice = parse_settled_invoice(
        make_text_pdf(tmp_path / "boxtal_2600023334.pdf", [boxtal_text()])
    )
    assert invoice.supplier == BOXTAL
    assert invoice.invoice_number == "2600023334"
    assert invoice.invoice_date == date(2026, 1, 12)
    assert invoice.total_ht == Decimal("54.56")
    assert invoice.total_tva == Decimal("10.91")
    assert invoice.total_ttc == Decimal("65.47")
    assert invoice.net_to_pay == Decimal("0.00")
    assert invoice.already_paid_on_invoice is True
    assert invoice.payment_method == "CARTE_BANCAIRE"
    assert invoice.category == DocumentCategory.TRANSPORTS_LOGISTIQUE.value
    match = find_settled_ofx_match(invoice, ofx_with_boxtal())
    assert match is not None
    assert match.amount == Decimal("65.47")
    assert match.fitid == "CB-BOXTAL-120126"
    assert match.operation_date == date(2026, 1, 15)

    store = make_store(tmp_path)
    result = import_settled_invoice(
        store, TENANT, invoice, ofx_statement=ofx_with_boxtal()
    )
    assert result.match is not None
    line = store.get_line(TENANT, invoice.line_id)
    assert line is not None
    assert line["amount_ttc"] == "65.47"
    assert line["already_paid_on_invoice"] == "1"
    assert line["reconciliation_status"] == SESSION_PAID_STATUS
    assert line["fitid"] == "CB-BOXTAL-120126"


def test_free_and_smtrt_categories_and_payment_methods(tmp_path):
    free = parse_settled_invoice(
        make_text_pdf(tmp_path / "free.pdf", [free_text()])
    )
    smtrt = parse_settled_invoice(
        make_text_pdf(tmp_path / "smtrt.pdf", [smtrt_text()])
    )
    assert free.supplier == FREE_TELECOM
    assert free.category == DocumentCategory.FRAIS_GENERAUX.value
    assert free.expense_subcategory == "TELECOM_INTERNET"
    assert free.payment_method == "PRELEVEMENT_SEPA"
    assert free.total_ttc == Decimal("36.00")
    assert smtrt.supplier == SMTRT
    assert smtrt.category == DocumentCategory.TRANSPORTS_LOGISTIQUE.value
    assert smtrt.payment_method == "VIREMENT"


# -- 4. Recursive Outlook unpacking -----------------------------------------


def build_outlook_batch() -> bytes:
    inner = StdEmailMessage()
    inner["Subject"] = "Facture Bosch"
    inner.set_content("Merci de traiter la facture fournisseur.")
    inner.add_attachment(
        b"%PDF-1.7\n" + b"x" * 7000,
        maintype="application",
        subtype="pdf",
        filename="bosch.pdf",
    )
    outer = StdEmailMessage()
    outer["Subject"] = "Transfert du lot comptable"
    outer.set_content("Voir les elements transferes.")
    outer.add_attachment(inner)
    return outer.as_bytes()


def build_eml_attachment_batch() -> bytes:
    inner = StdEmailMessage()
    inner["Subject"] = "Factura Boxtal"
    inner.set_content("factura adjunta")
    inner.add_attachment(
        b"%PDF-1.7\n" + b"y" * 7000,
        maintype="application",
        subtype="pdf",
        filename="boxtal.pdf",
    )
    outer = StdEmailMessage()
    outer["Subject"] = "Lot Outlook"
    outer.set_content("Pieces jointes")
    outer.add_attachment(
        inner.as_bytes(),
        maintype="application",
        subtype="octet-stream",
        filename="mail.eml",
    )
    return outer.as_bytes()


@pytest.mark.parametrize("factory", [build_outlook_batch, build_eml_attachment_batch])
def test_nested_rfc822_and_eml_attachments_are_unpacked(factory):
    message = parse_email_message(factory(), message_id="m1")
    assert len(message.attachments) == 1
    assert message.attachments[0].filename in {"bosch.pdf", "boxtal.pdf"}
    assert "facture" in message.body.casefold() or "factura" in message.body.casefold()
    eligibility = evaluate_apd_email_eligibility(message)
    assert eligibility.eligible is True
    assert eligibility.reason == ELIGIBLE_REASON


def test_ineligible_nested_message_stays_untouched():
    inner = StdEmailMessage()
    inner["Subject"] = "Newsletter"
    inner.set_content("Promotions")
    inner.add_attachment(
        b"%PDF-1.7\n" + b"z" * 7000,
        maintype="application",
        subtype="pdf",
        filename="newsletter.pdf",
    )
    outer = StdEmailMessage()
    outer["Subject"] = "Transfert"
    outer.set_content("Bonne lecture")
    outer.add_attachment(inner)
    decision = evaluate_apd_email_eligibility(
        parse_email_message(outer.as_bytes(), message_id="m2")
    )
    assert decision.eligible is False


# -- 5. Unified inbox, watcher Path tolerance and cockpit --------------------


def test_document_flow_watcher_accepts_a_path_as_first_argument(tmp_path):
    watcher = DocumentFlowWatcher(
        tmp_path, store=make_store(tmp_path), auto_export=False
    )
    assert watcher.tenant_id == DEFAULT_TENANT
    assert watcher.tree.root == tmp_path.resolve()


def test_unified_inbox_accepts_ofx_sage_bosch_boxtal_lcr_and_customer_batch(tmp_path):
    store = make_store(tmp_path)
    tree = ensure_tree(TENANT, tmp_path)
    from schemas.folder_state_schema import FolderState

    inbox = tree.state_dir(FolderState.INBOX)
    (inbox / "releve.ofx").write_text(ofx_with_boxtal(), encoding="utf-8")
    (inbox / "Liste des factures clients.csv").write_text(
        "N° pièce;Date Pièce;Client;Total HT;Total TVA;Total TTC\n"
        "FA90001;02/01/2026;CLIENT TEST;100,00;20,00;120,00\n",
        encoding="utf-8",
    )
    make_text_pdf(
        inbox / "Y16V_SD_INVOICE202609243956114620.pdf",
        [bosch_text("Avoir", "3956114620", "24.09.2026", "60,00", "12,00", "72,00", origin="3956107265")],
    )
    make_text_pdf(inbox / "boxtal_2600023334.pdf", [boxtal_text()])
    make_text_pdf(
        inbox / "bordereau_lcr.pdf",
        [
            "CAISSE D'EPARGNE\nBordereau d'effets a l'encaissement\n"
            "Reference de transmission : 32000028917640\n"
            "Compte : 145050000208001409859\nNombre d'operations : 1\n"
            "Montant total au credit : 1 000,00 EUR\n"
            "Tire | Rang | Domiciliation | Acceptation | Echeance | Montant au credit\n"
            "CALANDRE 18 | 1 | LYON | ACC | 15/01/2026 | 1 000,00\n"
        ],
    )
    build_customer_batch_pdf(inbox / "factures clients.pdf")

    report = InboxIngestionService(TENANT, store, tmp_path).process_inbox()
    assert report.archived == 0, report.traces
    assert report.scanned == 6
    assert report.moved_to_03 == 6
    assert (tmp_path / TENANT / "04_EXPORT_COMPTA" / TENANT / "PIECES_JOINTES" / "VENTES").is_dir()
    assert store.list_customer_invoices(TENANT)
    assert any(
        row["document_type"] == "AVOIR"
        for row in store.list_supplier_ledger(TENANT, "BOSCH")
    )
    boxtal_line = store.get_line(TENANT, "SETTLED_BOXTAL_2600023334")
    assert boxtal_line is not None
    assert boxtal_line["reconciliation_status"] == SESSION_PAID_STATUS
    assert boxtal_line["fitid"] == "CB-BOXTAL-120126"


def test_refresh_cockpit_creates_note_and_prints_absolute_paths(tmp_path, capsys):
    root = tmp_path / "flow"
    vault = tmp_path / "vault"
    code = refresh_cockpit_main(
        [
            "--tenant",
            TENANT,
            "--root",
            str(root),
            "--vault-root",
            str(vault),
            "--db",
            str(tmp_path / "accounting.sqlite3"),
            "--export-dir",
            str(tmp_path / "exports"),
            "--no-ingest",
        ]
    )
    assert code == 0
    output = capsys.readouterr().out
    assert "Cockpit Obsidian :" in output
    assert "Dossier 00_INBOX :" in output
    cockpit = vault / "07-compta" / "Cockpit_Compta.md"
    assert cockpit.is_file()
    assert str(cockpit.resolve()) in output
    assert str((root / TENANT / "00_INBOX").resolve()) in output
    assert (tmp_path / "exports" / TENANT / CATEGORY_OVERVIEW_FILENAME).is_file()
