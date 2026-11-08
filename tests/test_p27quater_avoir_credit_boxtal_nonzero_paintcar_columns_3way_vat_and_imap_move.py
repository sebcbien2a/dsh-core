"""P27-quater - Avoir credit, Boxtal, Paintcar, ledger columns, 3-way VAT, IMAP moves."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path

import fitz
import pytest
from openpyxl import load_workbook

from schemas.folder_state_schema import DocumentCategory
from services.accounting_spreadsheet_export_service import (
    SUPPLIER_LEDGER_COLUMNS,
    SupplierLedgerExporter,
    _build_supplier_diagnostics,
    _build_supplier_ledger_entries,
    compute_vat_3way_control,
    write_vat_3way_control_sheet,
)
from services.acompleter_workbook_service import derive_document_id
from services.email_inbox_bridge import EmailAttachment, EmailMessage
from services.gmail_imap_sync_service import (
    MAIL_FACTURES_FOURNISSEURS,
    MAIL_PENDING,
    GmailImapSyncService,
)
from services.payment_instrument_reconciliation_service import _match_invoices
from services.settled_invoice_parser import parse_settled_invoice
from services.sqlite_accounting_store import SqliteAccountingStore

TENANT = "APD"
BOXTAL_TEXT = """BOXTAL SAS
Facture n° 2600023334 du 12 janvier 2026
Total HT
54,56 €
Total TVA
10,91 €
Total TTC
65,47 €
Réglé
-65,47 €
Net à payer
0,00 €
Carte Bancaire
"""


def make_pdf(path: Path, text: str) -> Path:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((40, 40), text)
    document.save(str(path))
    document.close()
    return path


class FakeMailClient:
    def __init__(self, messages: dict[str, list[EmailMessage]]) -> None:
        self._messages = messages
        self.folders: list[str] = []
        self.moves: list[tuple[str, str, str]] = []

    def ensure_folder(self, folder: str) -> None:
        self.folders.append(folder)

    def list_messages(self, folder: str, *, limit: int | None = None):
        return list(self._messages.get(folder, []))

    def move_message(
        self, message_id: str, source_folder: str, target_folder: str
    ) -> None:
        self.moves.append((message_id, source_folder, target_folder))


def _store_line(
    store: SqliteAccountingStore,
    content: bytes,
    *,
    status: str,
    category: DocumentCategory = DocumentCategory.ACHATS_MARCHANDISES,
    document_type: str = "FACTURE",
) -> str:
    digest = hashlib.sha256(content).hexdigest()
    line_id = derive_document_id(digest)
    store.force_upsert_document_line(
        TENANT,
        line_id,
        {
            "operation_date": "2026-01-05",
            "label": "Piece test",
            "counterparty": "FOURNISSEUR TEST",
            "invoice_id": "T-1",
            "category": category.value,
            "document_type": document_type,
            "amount_ht": "100.00",
            "amount_tva": "20.00",
            "amount_ttc": "120.00",
            "status": status,
        },
    )
    return line_id


def test_boxtal_uses_total_ttc_not_net_to_pay(tmp_path):
    pdf = make_pdf(tmp_path / "boxtal_2600023334.pdf", BOXTAL_TEXT)
    invoice = parse_settled_invoice(pdf)
    assert invoice.total_ht == Decimal("54.56")
    assert invoice.total_tva == Decimal("10.91")
    assert invoice.total_ttc == Decimal("65.47")
    assert invoice.net_to_pay == Decimal("0.00")
    assert invoice.is_credit_note is False


def test_boxtal_avoir_is_negative_credit_note(tmp_path):
    text = BOXTAL_TEXT.replace(
        "Total HT\n54,56", "Total HT\n-54,56"
    ).replace("Total TVA\n10,91", "Total TVA\n-10,91").replace(
        "Total TTC\n65,47", "Total TTC\n-65,47"
    ) + "\nAnnulations de prestations"
    pdf = make_pdf(tmp_path / "boxtal_avoir.pdf", text)
    invoice = parse_settled_invoice(pdf)
    assert invoice.is_credit_note is True
    assert invoice.total_ttc == Decimal("-65.47")
    line = invoice.as_ledger_line()
    assert line["document_type"] == "AVOIR"
    assert line["amount_ttc"] == "-65.47"


def test_supplier_refund_is_a_bank_credit_and_avoir_is_negative(tmp_path):
    store = SqliteAccountingStore(tmp_path / "accounting.sqlite3")
    store.force_upsert_document_line(
        TENANT,
        "AMZ-1",
        {
            "operation_date": "2026-09-01",
            "label": "Facture Amazon",
            "counterparty": "AMAZON PAYMENTS",
            "invoice_id": "AMZ-1",
            "category": DocumentCategory.FRAIS_GENERAUX.value,
            "document_type": "FACTURE",
            "amount_ht": "100.00",
            "amount_tva": "20.00",
            "amount_ttc": "120.00",
            "status": "VALIDE",
        },
    )
    store.force_upsert_document_line(
        TENANT,
        "AV-1",
        {
            "operation_date": "2026-09-02",
            "label": "Avoir fournisseur",
            "counterparty": "AMAZON PAYMENTS",
            "invoice_id": "AV-1",
            "category": DocumentCategory.FRAIS_GENERAUX.value,
            "document_type": "AVOIR",
            "amount_ht": "10.00",
            "amount_tva": "2.00",
            "amount_ttc": "12.00",
            "status": "VALIDE",
        },
    )
    store.add_line(
        TENANT,
        "REFUND-1",
        {
            "operation_date": "2026-09-21",
            "label": "CB AMAZON PAYMENTS",
            "counterparty": "AMAZON PAYMENTS",
            "credit_amount": "36.61",
            "category": DocumentCategory.FRAIS_GENERAUX.value,
            "document_type": "OFX",
            "status": "PENDING",
        },
    )
    path = SupplierLedgerExporter(
        store, export_dir=tmp_path / "exports", strict_tiers=True
    ).export_supplier_ledger(TENANT)
    sheet = load_workbook(path)["AMAZON"]
    assert tuple(cell.value for cell in sheet[1]) == SUPPLIER_LEDGER_COLUMNS
    rows = [row for row in sheet.iter_rows(min_row=2, values_only=True)]
    refund = next(row for row in rows if row[1] == "AVOIR_REMBOURSEMENT")
    avoir = next(row for row in rows if row[1] == "AVOIR")
    assert refund[8] == 0
    assert refund[9] == pytest.approx(36.61)
    assert avoir[7] == pytest.approx(-12.00)
    assert any(
        str(row[0] or "").startswith("TOTAL BANQUE") for row in rows
    )


def test_paintcar_combination_is_not_forced_with_a_gap(tmp_path):
    store = SqliteAccountingStore(tmp_path / "accounting.sqlite3")
    invoices = []
    for number, ttc, day in (
        ("ILAA0F41", "2074.78", "2026-02-20"),
        ("ILA193A1", "3712.12", "2026-02-20"),
        ("ILA7CC61", "83.43", "2026-02-20"),
    ):
        store.force_upsert_document_line(
            TENANT,
            number,
            {
                "operation_date": day,
                "label": f"Facture {number}",
                "counterparty": "PAINTCAR",
                "invoice_id": number,
                "category": DocumentCategory.ACHATS_MARCHANDISES.value,
                "document_type": "FACTURE",
                "amount_ht": ttc,
                "amount_tva": "0.00",
                "amount_ttc": ttc,
                "status": "VALIDE",
            },
        )
    store.add_line(
        TENANT,
        "PRLV-PAINTCAR",
        {
            "operation_date": "2026-02-16",
            "label": "PRLV CRISOL COLOR S L",
            "counterparty": "CRISOL COLOR S L",
            "debit_amount": "7639.43",
            "category": DocumentCategory.ACHATS_MARCHANDISES.value,
            "document_type": "OFX",
            "status": "PENDING",
        },
    )
    rows = store.list_supplier_ledger(TENANT)
    entries = _build_supplier_ledger_entries(rows)
    diagnostics = _build_supplier_diagnostics(entries)
    assert diagnostics
    assert all(d.combination == () for d in diagnostics)
    assert diagnostics[0].status == "A_RAPPROCHER"
    assert "Paintcar" in diagnostics[0].message
    match = _match_invoices(
        store.list_master_lines(TENANT),
        direction="DEBIT",
        amount=Decimal("7639.43"),
        bank_date=__import__("datetime").date(2026, 2, 16),
    )
    assert match == []


def test_three_way_vat_control_is_zero_and_has_sheet(tmp_path):
    from openpyxl import Workbook

    store = SqliteAccountingStore(tmp_path / "accounting.sqlite3")
    for line_id, category, tva, ttc in (
        ("F1", DocumentCategory.ACHATS_MARCHANDISES, "20.00", "120.00"),
        ("V1", DocumentCategory.VENTES_CLIENTS, "30.00", "180.00"),
    ):
        store.force_upsert_document_line(
            TENANT,
            line_id,
            {
                "operation_date": "2026-01-05",
                "label": line_id,
                "counterparty": "AMAZON" if line_id == "F1" else "CHOLLET",
                "invoice_id": line_id,
                "category": category.value,
                "document_type": "FACTURE",
                "amount_ht": "100.00",
                "amount_tva": tva,
                "amount_ttc": ttc,
                "status": "VALIDE",
            },
        )
    report = compute_vat_3way_control(store, TENANT)
    assert report["level1"] == report["level2"] == report["level3"]
    assert report["gap12"] == 0
    assert report["gap13"] == 0
    workbook = Workbook()
    write_vat_3way_control_sheet(workbook, store, TENANT)
    assert "CONTROLE_TVA_3_NIVEAUX" in workbook.sheetnames
    sheet = workbook["CONTROLE_TVA_3_NIVEAUX"]
    assert any(
        cell.value == "ECART NUL"
        for row in sheet.iter_rows()
        for cell in row
    )


def test_gmail_moves_validated_mail_to_supplier_folder_and_pending_stays(tmp_path):
    store = SqliteAccountingStore(tmp_path / "accounting.sqlite3")
    valid_bytes = b"%PDF-1.4 valid supplier"
    pending_bytes = b"%PDF-1.4 pending supplier"
    _store_line(store, valid_bytes, status="VALIDE")
    _store_line(store, pending_bytes, status="EN_ATTENTE")
    valid_mail = EmailMessage(
        message_id="1",
        folder="INBOX",
        subject="factures bosch sia abrasive",
        attachments=(EmailAttachment("bosch.pdf", valid_bytes),),
    )
    pending_mail = EmailMessage(
        message_id="2",
        folder="INBOX",
        subject="factures les emaux",
        attachments=(EmailAttachment("pending.pdf", pending_bytes),),
    )
    client = FakeMailClient({"INBOX": [valid_mail, pending_mail]})
    service = GmailImapSyncService(
        TENANT,
        store=store,
        root=tmp_path / "flow",
        client=client,
        enabled=True,
        classify_messages=True,
    )
    report = service.sync()
    assert report.connected is True
    assert (MAIL_FACTURES_FOURNISSEURS, "INBOX") in {
        (target, source) for _uid, source, target in client.moves
    }
    assert any(
        target == MAIL_PENDING for _uid, _source, target in client.moves
    ) or report.messages_archived >= 1
