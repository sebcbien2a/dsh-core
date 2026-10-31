"""Exhaustive tests for OFX/CSV/TXT ingestion in the banking producer."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from schemas.banking_schema import BankTransactionDirection
from services.banking_invoice_producer import (
    BankingInvoiceProducer,
    _normalise_currency,
    _parse_date,
    _parse_decimal,
)
from services.invoice_producer_protocol import InvoiceProducerError

DEBIT = BankTransactionDirection.DEBIT
CREDIT = BankTransactionDirection.CREDIT


def _write(tmp_path: Path, name: str, content: str | bytes, encoding: str = "utf-8") -> Path:
    path = tmp_path / name
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_bytes(content.encode(encoding))
    return path


def _parse(path: Path, **kwargs):
    return BankingInvoiceProducer(path, company_id="T", **kwargs).parse_transactions()


def _ofx(*transactions: str, curdef: str = "EUR", closed: bool = True) -> str:
    body = "".join(transactions)
    tail = "</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>" if closed else ""
    return (
        "OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\n\n<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS>"
        f"<CURDEF>{curdef}<BANKTRANLIST>{body}{tail}"
    )


def _trn(amount: str | None = "-10.00", dt: str | None = "20261001", **extra: str) -> str:
    parts = ["<STMTTRN>", f"<TRNTYPE>{extra.pop('type', 'DEBIT')}"]
    if dt is not None:
        parts.append(f"<DTPOSTED>{dt}")
    if amount is not None:
        parts.append(f"<TRNAMT>{amount}")
    parts.append(f"<FITID>{extra.pop('fitid', 'F1')}")
    for tag, value in extra.items():
        parts.append(f"<{tag.upper()}>{value}")
    parts.append("</STMTTRN>")
    return "\n".join(parts) + "\n"


# --------------------------------------------------------------------- OFX


class TestOfx:
    def test_valid_debit_and_credit(self, tmp_path):
        path = _write(tmp_path, "a.ofx", _ofx(_trn("-42.10", name="EDF", memo="Facture"), _trn("100.00", fitid="F2")))
        debit, credit = _parse(path)
        assert (debit.direction, debit.amount, debit.label, debit.counterparty) == (
            DEBIT, Decimal("42.10"), "Facture", "EDF")
        assert (credit.direction, credit.amount) == (CREDIT, Decimal("100.00"))
        assert debit.reference == "F1" and credit.reference == "F2"

    def test_qfx_suffix_and_uppercase_suffix(self, tmp_path):
        for name in ("a.qfx", "A.OFX"):
            assert len(_parse(_write(tmp_path, name, _ofx(_trn())))) == 1

    def test_xml_style_closing_tags(self, tmp_path):
        xml = (
            "<OFX><STMTTRN><TRNTYPE>CARD</TRNTYPE><DTPOSTED>20261002</DTPOSTED>"
            "<TRNAMT>-5.00</TRNAMT><FITID>X</FITID><NAME>CAFE</NAME></STMTTRN></OFX>"
        )
        (tx,) = _parse(_write(tmp_path, "x.ofx", xml))
        assert tx.amount == Decimal("5.00") and tx.counterparty == "CAFE"

    def test_lowercase_tags(self, tmp_path):
        text = "<ofx><stmttrn><trnamt>-3.00<dtposted>20261001<fitid>L</stmttrn></ofx>"
        (tx,) = _parse(_write(tmp_path, "l.ofx", text))
        assert tx.amount == Decimal("3.00") and tx.booking_date == date(2026, 10, 1)

    def test_ofx_date_with_time_and_timezone(self, tmp_path):
        path = _write(tmp_path, "t.ofx", _ofx(_trn(dt="20261001120000[+1:CET]")))
        assert _parse(path)[0].booking_date == date(2026, 10, 1)

    def test_truncated_file_without_closing_tags(self, tmp_path):
        path = _write(tmp_path, "t.ofx", _ofx(_trn(), _trn("5.00", fitid="F2"), closed=False))
        assert [t.reference for t in _parse(path)] == ["F1", "F2"]

    def test_truncated_in_the_middle_of_a_transaction(self, tmp_path):
        text = _ofx(_trn(), closed=False) + "<STMTTRN>\n<TRNTYPE>DEBIT\n<DTPOSTED>2026"
        assert len(_parse(_write(tmp_path, "t.ofx", text))) == 1

    def test_truncated_amount_is_skipped(self, tmp_path):
        text = _ofx(_trn(), closed=False) + "<STMTTRN>\n<TRNTYPE>DEBIT\n<DTPOSTED>20261001\n"
        assert len(_parse(_write(tmp_path, "t.ofx", text))) == 1

    def test_truncated_inside_amount_value_is_rejected_or_partial(self, tmp_path):
        text = _ofx(_trn(), closed=False) + "<STMTTRN><DTPOSTED>20261001<TRNAMT>-1x"
        with pytest.raises(InvoiceProducerError) as exc:
            _parse(_write(tmp_path, "t.ofx", text))
        assert exc.value.code == "INVALID_BANK_TRANSACTION"

    def test_no_transactions_at_all(self, tmp_path):
        path = _write(tmp_path, "e.ofx", "<OFX></OFX>")
        assert _parse(path) == []
        with pytest.raises(InvoiceProducerError) as exc:
            BankingInvoiceProducer(path).produce()
        assert exc.value.code == "NO_BANK_TRANSACTIONS"

    def test_empty_file(self, tmp_path):
        path = _write(tmp_path, "e.ofx", b"")
        assert _parse(path) == []
        with pytest.raises(InvoiceProducerError) as exc:
            BankingInvoiceProducer(path).produce_all()
        assert exc.value.code == "NO_BANK_TRANSACTIONS"

    def test_garbage_binary_content_does_not_crash(self, tmp_path):
        path = _write(tmp_path, "g.ofx", bytes(range(256)) * 4)
        assert _parse(path) == []

    def test_missing_amount_tag_skips_transaction(self, tmp_path):
        assert _parse(_write(tmp_path, "m.ofx", _ofx(_trn(amount=None)))) == []

    def test_zero_amount_skipped(self, tmp_path):
        assert _parse(_write(tmp_path, "z.ofx", _ofx(_trn("0.00")))) == []

    @pytest.mark.parametrize("amount", ["abc", "--5", "1.2.3,4x", "NaN-"])
    def test_malformed_amount_rejected(self, tmp_path, amount):
        with pytest.raises(InvoiceProducerError) as exc:
            _parse(_write(tmp_path, "b.ofx", _ofx(_trn(amount))))
        assert exc.value.code == "INVALID_BANK_TRANSACTION"

    def test_missing_dtposted_rejected(self, tmp_path):
        with pytest.raises(InvoiceProducerError) as exc:
            _parse(_write(tmp_path, "d.ofx", _ofx(_trn(dt=None))))
        assert exc.value.code == "INVALID_BANK_TRANSACTION"

    def test_missing_tags_fall_back_to_default_label(self, tmp_path):
        (tx,) = _parse(_write(tmp_path, "d.ofx", _ofx(_trn())))
        assert tx.label == "TRANSACTION SANS LIBELLE" and tx.counterparty is None

    def test_label_falls_back_to_name_and_payee(self, tmp_path):
        (a,) = _parse(_write(tmp_path, "a.ofx", _ofx(_trn(name="ACME"))))
        (b,) = _parse(_write(tmp_path, "b.ofx", _ofx(_trn(payee="PAYEE CO"))))
        assert a.label == "ACME" and b.counterparty == "PAYEE CO"

    def test_html_entities_unescaped(self, tmp_path):
        (tx,) = _parse(_write(tmp_path, "h.ofx", _ofx(_trn(name="AT&amp;T", memo="R&amp;D"))))
        assert tx.counterparty == "AT&T" and tx.label == "R&D"

    def test_missing_fitid_value_is_none(self, tmp_path):
        text = _ofx("<STMTTRN><DTPOSTED>20261001<TRNAMT>-1.00</STMTTRN>")
        assert _parse(_write(tmp_path, "f.ofx", text))[0].reference is None

    def test_curdef_used_and_validated(self, tmp_path):
        (tx,) = _parse(_write(tmp_path, "c.ofx", _ofx(_trn(), curdef="usd")))
        assert tx.currency == "USD"
        with pytest.raises(InvoiceProducerError):
            _parse(_write(tmp_path, "c2.ofx", _ofx(_trn(), curdef="EURO")))

    def test_missing_curdef_uses_producer_default(self, tmp_path):
        text = "<OFX><STMTTRN><DTPOSTED>20261001<TRNAMT>-1.00</STMTTRN></OFX>"
        path = _write(tmp_path, "c.ofx", text)
        assert _parse(path)[0].currency == "EUR"
        assert _parse(path, currency="CHF")[0].currency == "CHF"

    def test_per_transaction_currency_overrides_curdef(self, tmp_path):
        (tx,) = _parse(_write(tmp_path, "c.ofx", _ofx(_trn(currency="GBP"))))
        assert tx.currency == "GBP"

    def test_ofx_comma_decimal_amount(self, tmp_path):
        (tx,) = _parse(_write(tmp_path, "c.ofx", _ofx(_trn("-1234,56"))))
        assert tx.amount == Decimal("1234.56") and tx.direction is DEBIT

    def test_explicit_plus_sign_is_credit(self, tmp_path):
        (tx,) = _parse(_write(tmp_path, "c.ofx", _ofx(_trn("+7.50"))))
        assert tx.direction is CREDIT

    def test_transaction_type_and_raw_preserved(self, tmp_path):
        (tx,) = _parse(_write(tmp_path, "c.ofx", _ofx(_trn(type="CARD"))))
        assert tx.transaction_type == "CARD" and tx.raw["FITID"] == "F1"

    def test_invalid_utf8_bytes_in_ofx_do_not_crash(self, tmp_path):
        content = _ofx(_trn(name="CAF\u00c9 DU COIN")).encode("cp1252")
        (tx,) = _parse(_write(tmp_path, "c.ofx", content))
        assert tx.counterparty == "CAFÉ DU COIN"

    def test_utf8_accents_preserved_in_ofx(self, tmp_path):
        (tx,) = _parse(_write(tmp_path, "u.ofx", _ofx(_trn(name="Boulangerie Éloïse", memo="Café – crème"))))
        assert tx.counterparty == "Boulangerie Éloïse" and tx.label == "Café – crème"

    def test_utf8_bom_ofx(self, tmp_path):
        content = b"\xef\xbb\xbf" + _ofx(_trn()).encode("utf-8")
        assert len(_parse(_write(tmp_path, "b.ofx", content))) == 1

    def test_produce_all_builds_invoices(self, tmp_path):
        path = _write(tmp_path, "p.ofx", _ofx(_trn("-10.00", name="EDF"), _trn("20.00", fitid="F2", name="CLIENT")))
        invoices = BankingInvoiceProducer(path).produce_all()
        assert [i.direction.value for i in invoices] == ["PURCHASE", "SALE"]

    def test_parse_is_cached_and_returns_copy(self, tmp_path):
        producer = BankingInvoiceProducer(_write(tmp_path, "p.ofx", _ofx(_trn())))
        first = producer.parse_transactions()
        first.clear()
        assert len(producer.parse_transactions()) == 1


# --------------------------------------------------------------------- CSV


class TestCsv:
    def test_semicolon_french_csv(self, tmp_path):
        text = "Date;Libelle;Montant;Devise\n01/10/2026;CARTE LECLERC;-45,30;EUR\n02/10/2026;VIREMENT;1 200,00;EUR\n"
        a, b = _parse(_write(tmp_path, "a.csv", text))
        assert (a.amount, a.direction, a.booking_date) == (Decimal("45.30"), DEBIT, date(2026, 10, 1))
        assert (b.amount, b.direction) == (Decimal("1200.00"), CREDIT)

    def test_comma_delimiter_english_csv(self, tmp_path):
        text = "date,description,amount\n2026-10-01,Coffee,-3.50\n2026-10-02,Salary,2500.00\n"
        a, b = _parse(_write(tmp_path, "a.csv", text))
        assert a.amount == Decimal("3.50") and b.amount == Decimal("2500.00")

    def test_tab_delimiter(self, tmp_path):
        text = "date\tlabel\tamount\n2026-10-01\tX\t-3.50\n"
        assert _parse(_write(tmp_path, "a.csv", text))[0].amount == Decimal("3.50")

    def test_semicolon_and_comma_mixed_content_prefers_real_delimiter(self, tmp_path):
        text = "date;libelle;montant\n01/10/2026;A, B, C;-3,50\n02/10/2026;D;4,00\n"
        a, b = _parse(_write(tmp_path, "a.csv", text))
        assert a.label == "A, B, C" and a.amount == Decimal("3.50") and b.amount == Decimal("4.00")

    def test_separate_debit_credit_columns(self, tmp_path):
        text = "Date;Libelle;Debit;Credit\n01/10/2026;A;12,00;\n02/10/2026;B;;30,50\n03/10/2026;C;;\n"
        a, b = _parse(_write(tmp_path, "a.csv", text))
        assert (a.direction, a.amount) == (DEBIT, Decimal("12.00"))
        assert (b.direction, b.amount) == (CREDIT, Decimal("30.50"))

    def test_negative_value_in_debit_column_is_still_debit(self, tmp_path):
        text = "Date;Libelle;Debit;Credit\n01/10/2026;A;-12,00;\n"
        a, = _parse(_write(tmp_path, "a.csv", text))
        assert (a.direction, a.amount) == (DEBIT, Decimal("12.00"))

    def test_explicit_direction_column_overrides_sign(self, tmp_path):
        text = "date;libelle;montant;sens\n01/10/2026;A;12,00;D\n02/10/2026;B;-12,00;C\n03/10/2026;C;5,00;SORTIE\n04/10/2026;D;5,00;Entrée\n"
        assert [t.direction for t in _parse(_write(tmp_path, "a.csv", text))] == [DEBIT, CREDIT, DEBIT, CREDIT]

    def test_unknown_direction_word_falls_back_to_sign(self, tmp_path):
        text = "date;libelle;montant;sens\n01/10/2026;A;-12,00;???\n"
        assert _parse(_write(tmp_path, "a.csv", text))[0].direction is DEBIT

    def test_zero_amount_and_blank_rows_skipped(self, tmp_path):
        text = "date;libelle;montant\n01/10/2026;A;0,00\n;;\n02/10/2026;B;1,00\n"
        (tx,) = _parse(_write(tmp_path, "a.csv", text))
        assert tx.label == "B"

    def test_header_only_csv_has_no_transactions(self, tmp_path):
        path = _write(tmp_path, "a.csv", "date;libelle;montant\n")
        assert _parse(path) == []
        with pytest.raises(InvoiceProducerError) as exc:
            BankingInvoiceProducer(path).produce_records()
        assert exc.value.code == "NO_BANK_TRANSACTIONS"

    def test_empty_csv(self, tmp_path):
        assert _parse(_write(tmp_path, "a.csv", b"")) == []

    def test_missing_date_column(self, tmp_path):
        with pytest.raises(InvoiceProducerError) as exc:
            _parse(_write(tmp_path, "a.csv", "libelle;montant\nA;1,00\n"))
        assert exc.value.code == "INVALID_BANK_TRANSACTION"

    def test_missing_amount_columns(self, tmp_path):
        with pytest.raises(InvoiceProducerError) as exc:
            _parse(_write(tmp_path, "a.csv", "date;libelle\n01/10/2026;A\n"))
        assert exc.value.code == "INVALID_BANK_TRANSACTION"

    def test_accented_and_uppercase_headers(self, tmp_path):
        text = "DATE OPÉRATION;LIBELLÉ;MONTANT;DATE VALEUR\n01/10/2026;Achat;-9,99;03/10/2026\n"
        (tx,) = _parse(_write(tmp_path, "a.csv", text))
        assert tx.label == "Achat" and tx.value_date == date(2026, 10, 3)

    def test_counterparty_reference_and_type_columns(self, tmp_path):
        text = "date;tiers;libelle;reference;type;montant\n01/10/2026;EDF;Facture;R1;CARD;-9,99\n"
        (tx,) = _parse(_write(tmp_path, "a.csv", text))
        assert (tx.counterparty, tx.label, tx.reference, tx.transaction_type) == ("EDF", "Facture", "R1", "CARD")

    def test_label_falls_back_to_counterparty_then_default(self, tmp_path):
        text = "date;tiers;montant\n01/10/2026;EDF;-1,00\n02/10/2026;;-1,00\n"
        a, b = _parse(_write(tmp_path, "a.csv", text))
        assert a.label == "EDF" and b.label == "TRANSACTION SANS LIBELLE"

    def test_quoted_fields_with_delimiters(self, tmp_path):
        text = 'date;libelle;montant\n01/10/2026;"PAIEMENT; CARTE ""X""";-1,00\n'
        assert _parse(_write(tmp_path, "a.csv", text))[0].label == 'PAIEMENT; CARTE "X"'

    def test_currency_column_validated(self, tmp_path):
        ok = "date;montant;devise\n01/10/2026;-1,00;usd\n"
        assert _parse(_write(tmp_path, "a.csv", ok))[0].currency == "USD"
        bad = "date;montant;devise\n01/10/2026;-1,00;EURO\n"
        with pytest.raises(InvoiceProducerError):
            _parse(_write(tmp_path, "b.csv", bad))

    def test_default_currency_when_column_empty(self, tmp_path):
        text = "date;montant;devise\n01/10/2026;-1,00;\n"
        assert _parse(_write(tmp_path, "a.csv", text), currency="CHF")[0].currency == "CHF"

    def test_crlf_line_endings(self, tmp_path):
        text = "date;libelle;montant\r\n01/10/2026;A;-1,00\r\n02/10/2026;B;2,00\r\n"
        assert len(_parse(_write(tmp_path, "a.csv", text))) == 2

    def test_invalid_amount_in_csv(self, tmp_path):
        with pytest.raises(InvoiceProducerError) as exc:
            _parse(_write(tmp_path, "a.csv", "date;montant\n01/10/2026;abc\n"))
        assert exc.value.code == "INVALID_BANK_TRANSACTION"

    def test_ragged_row_does_not_crash_unexpectedly(self, tmp_path):
        text = "date;libelle;montant\n01/10/2026;A;-1,00\n02/10/2026;B\n"
        try:
            txs = _parse(_write(tmp_path, "a.csv", text))
        except InvoiceProducerError:
            return
        assert txs[0].amount == Decimal("1.00")

    def test_produce_records_from_csv(self, tmp_path):
        path = _write(tmp_path, "a.csv", "date;libelle;montant;type\n01/10/2026;A;-1,00;CARD\n02/10/2026;B;5,00;\n")
        records = BankingInvoiceProducer(path, company_id="T").produce_records()
        assert len(records) == 2
        assert records[0].direction is DEBIT and records[0].payment_method.name == "CARD"
        assert records[1].direction is CREDIT


# ------------------------------------------------------------------ Encodings


class TestEncodings:
    CSV = "date;libellé;tiers;montant\n01/10/2026;Café crème à 5€;Éts Müller – Ñ;-5,00\n"

    def test_utf8(self, tmp_path):
        (tx,) = _parse(_write(tmp_path, "u.csv", self.CSV))
        assert tx.label == "Café crème à 5€" and tx.counterparty == "Éts Müller – Ñ"

    def test_utf8_with_bom(self, tmp_path):
        content = b"\xef\xbb\xbf" + self.CSV.encode("utf-8")
        (tx,) = _parse(_write(tmp_path, "b.csv", content))
        assert tx.booking_date == date(2026, 10, 1) and tx.label == "Café crème à 5€"

    @pytest.mark.parametrize("encoding", ["iso-8859-1", "cp1252"])
    def test_legacy_encodings_decode_accents(self, tmp_path, encoding):
        text = "date;libellé;tiers;montant\n01/10/2026;Café à Noël;Éts Müller;-5,00\n"
        (tx,) = _parse(_write(tmp_path, "l.csv", text, encoding))
        assert tx.label == "Café à Noël" and tx.counterparty == "Éts Müller"
        assert tx.amount == Decimal("5.00") and tx.direction is DEBIT
        assert tx.booking_date == date(2026, 10, 1)

    def test_cp1252_ascii_columns_still_work(self, tmp_path):
        text = "Date;Libelle;Montant\n01/10/2026;Caf\u00e9 \u00e0 No\u00ebl;-5,00\n"
        (tx,) = _parse(_write(tmp_path, "l.csv", text, "cp1252"))
        assert tx.label == "Café à Noël"

    def test_cp1252_euro_sign_in_amount(self, tmp_path):
        # 0x80 is the euro sign in cp1252 and is invalid in UTF-8
        content = "date;montant\n01/10/2026;-12,50 €\n".encode("cp1252")
        (tx,) = _parse(_write(tmp_path, "e.csv", content))
        assert tx.amount == Decimal("12.50")

    def test_cp1252_smart_quotes_and_dashes(self, tmp_path):
        content = "date;libelle;montant\n01/10/2026;L\u2019achat \u2013 \u201cX\u201d;-1,00\n".encode("cp1252")
        (tx,) = _parse(_write(tmp_path, "q.csv", content))
        assert tx.label == "L\u2019achat \u2013 \u201cX\u201d"

    def test_ofx_cp1252_accents_decoded(self, tmp_path):
        content = _ofx(_trn(name="CAFÉ DU COIN", memo="Règlement")).encode("cp1252")
        (tx,) = _parse(_write(tmp_path, "c.ofx", content))
        assert tx.counterparty == "CAFÉ DU COIN" and tx.label == "Règlement"

    def test_latin1_accented_header_in_legacy_encoding(self, tmp_path):
        text = "Date opération;Libellé;Montant\n01/10/2026;Achat;-5,00\n"
        (tx,) = _parse(_write(tmp_path, "h.csv", text, "iso-8859-1"))
        assert tx.amount == Decimal("5.00") and tx.booking_date == date(2026, 10, 1)

    def test_utf16_csv_does_not_hang_or_corrupt(self, tmp_path):
        path = _write(tmp_path, "w.csv", "date;montant\n01/10/2026;-1,00\n".encode("utf-16"))
        try:
            txs = _parse(path)
        except InvoiceProducerError:
            return
        assert isinstance(txs, list)

    def test_null_bytes_do_not_raise_unexpected_exception(self, tmp_path):
        path = _write(tmp_path, "n.csv", b"date;montant\n01/10/2026;-1,00\x00\n")
        try:
            _parse(path)
        except InvoiceProducerError:
            pass

    def test_mojibake_utf8_read_as_latin1_text_is_not_silently_fixed(self, tmp_path):
        content = "date;libelle;montant\n01/10/2026;Café;-1,00\n".encode("utf-8").decode("latin-1").encode("utf-8")
        (tx,) = _parse(_write(tmp_path, "m.csv", content))
        assert tx.label == "CafÃ©"


# -------------------------------------------------------------- Amounts


class TestAmounts:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("12", "12"),
            ("12.5", "12.5"),
            ("12,5", "12.5"),
            ("1234,56", "1234.56"),
            ("1234.56", "1234.56"),
            ("1.234,56", "1234.56"),
            ("1,234.56", "1234.56"),
            ("1 234,56", "1234.56"),
            ("1\u00a0234,56", "1234.56"),
            ("1.234.567,89", "1234567.89"),
            ("1,234,567.89", "1234567.89"),
            ("1.234.567", "1234567"),
            ("1.234", "1234"),
            ("0,05", "0.05"),
            ("0.05", "0.05"),
            ("12,50 €", "12.50"),
            ("€12.50", "12.50"),
            ("12.50 EUR", "12.50"),
            ("+12,50", "12.50"),
            ("  7,00  ", "7.00"),
        ],
    )
    def test_parse_decimal_positive(self, raw, expected):
        assert _parse_decimal(raw) == Decimal(expected)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("-12,50", "-12.50"), ("-1.234,56", "-1234.56"), ("-1,234.56", "-1234.56"), ("-0,01", "-0.01")],
    )
    def test_parse_decimal_negative(self, raw, expected):
        assert _parse_decimal(raw) == Decimal(expected)

    @pytest.mark.parametrize("raw", ["", "   ", "abc", "12,5x", "--1", "1,2,3x", ".", ",", "€"])
    def test_parse_decimal_rejects_invalid(self, raw):
        with pytest.raises(InvoiceProducerError) as exc:
            _parse_decimal(raw)
        assert exc.value.code == "INVALID_BANK_TRANSACTION"

    def test_parse_decimal_returns_decimal_not_float(self):
        assert isinstance(_parse_decimal("0.1"), Decimal)
        assert _parse_decimal("0.1") + _parse_decimal("0.2") == Decimal("0.3")

    def test_parse_decimal_accepts_numbers(self):
        assert _parse_decimal(12) == Decimal("12")
        assert _parse_decimal(-3.5) == Decimal("-3.5")

    def test_large_amount_precision(self, tmp_path):
        text = "date;montant\n01/10/2026;-999 999 999,99\n"
        assert _parse(_write(tmp_path, "a.csv", text))[0].amount == Decimal("999999999.99")

    def test_amount_is_always_positive_with_direction(self, tmp_path):
        text = "date;montant\n01/10/2026;-5,00\n02/10/2026;5,00\n"
        a, b = _parse(_write(tmp_path, "a.csv", text))
        assert a.amount == b.amount == Decimal("5.00")
        assert (a.direction, b.direction) == (DEBIT, CREDIT)

    def test_ambiguous_thousands_dot_is_treated_as_thousands(self):
        # documented heuristic: three digits after a single dot -> thousands
        assert _parse_decimal("2.500") == Decimal("2500")

    def test_invoice_amount_serialisation(self, tmp_path):
        path = _write(tmp_path, "a.csv", "date;libelle;montant\n01/10/2026;A;-1 234,50\n")
        (invoice,) = BankingInvoiceProducer(path).produce_all()
        assert invoice.amount.gross_amount == Decimal("1234.50")
        assert invoice.amount.currency == "EUR"


# ------------------------------------------------------------- Currencies


class TestCurrencies:
    @pytest.mark.parametrize(("raw", "expected"), [("eur", "EUR"), (" usd ", "USD"), ("Chf", "CHF")])
    def test_valid(self, raw, expected):
        assert _normalise_currency(raw, "EUR") == expected

    @pytest.mark.parametrize("raw", ["EU", "EURO", "E1R", "12 ", "€€€"])
    def test_invalid(self, raw):
        with pytest.raises(InvoiceProducerError):
            _normalise_currency(raw, "EUR")

    @pytest.mark.parametrize("raw", [None, ""])
    def test_empty_uses_default(self, raw):
        assert _normalise_currency(raw, "GBP") == "GBP"

    def test_invalid_default_is_rejected(self):
        with pytest.raises(InvoiceProducerError):
            _normalise_currency(None, "EURO")


# ------------------------------------------------------------------ Dates


class TestDates:
    @pytest.mark.parametrize(
        "raw",
        ["2026-10-01", "01/10/2026", "01-10-2026", "01.10.2026", "20261001", "2026/10/01",
         "2026-10-01T12:30:00", "2026-10-01 12:30", "20261001120000", "  2026-10-01  "],
    )
    def test_accepted_formats(self, raw):
        assert _parse_date(raw) == date(2026, 10, 1)

    def test_date_objects(self):
        from datetime import datetime
        assert _parse_date(date(2026, 1, 2)) == date(2026, 1, 2)
        assert _parse_date(datetime(2026, 1, 2, 23, 59)) == date(2026, 1, 2)

    def test_leap_day(self):
        assert _parse_date("29/02/2028") == date(2028, 2, 29)

    @pytest.mark.parametrize(
        "raw",
        ["", "   ", None, "abc", "2026-13-01", "2026-02-30", "31/04/2026", "29/02/2027",
         "00/10/2026", "32/01/2026", "20261345", "20260230", "2026-00-10", "1/2/26x", "hier"],
    )
    def test_rejected(self, raw):
        with pytest.raises(InvoiceProducerError) as exc:
            _parse_date(raw)
        assert exc.value.code == "INVALID_BANK_TRANSACTION"

    def test_csv_invalid_operation_date(self, tmp_path):
        with pytest.raises(InvoiceProducerError):
            _parse(_write(tmp_path, "a.csv", "date;montant\n31/02/2026;-1,00\n"))

    def test_csv_empty_operation_date(self, tmp_path):
        with pytest.raises(InvoiceProducerError):
            _parse(_write(tmp_path, "a.csv", "date;montant\n;-1,00\n"))

    def test_csv_invalid_value_date(self, tmp_path):
        text = "date;date_valeur;montant\n01/10/2026;99/99/2026;-1,00\n"
        with pytest.raises(InvoiceProducerError):
            _parse(_write(tmp_path, "a.csv", text))

    def test_csv_value_date_optional(self, tmp_path):
        text = "date;date_valeur;montant\n01/10/2026;;-1,00\n"
        (tx,) = _parse(_write(tmp_path, "a.csv", text))
        assert tx.value_date is None

    def test_csv_value_date_distinct_from_booking_date(self, tmp_path):
        text = "date;value_date;montant\n01/10/2026;2026-10-04;-1,00\n"
        (tx,) = _parse(_write(tmp_path, "a.csv", text))
        assert (tx.booking_date, tx.value_date) == (date(2026, 10, 1), date(2026, 10, 4))

    def test_ofx_invalid_calendar_date(self, tmp_path):
        with pytest.raises(InvoiceProducerError):
            _parse(_write(tmp_path, "a.ofx", _ofx(_trn(dt="20261345"))))

    def test_ofx_short_date_rejected(self, tmp_path):
        with pytest.raises(InvoiceProducerError):
            _parse(_write(tmp_path, "a.ofx", _ofx(_trn(dt="2026"))))

    def test_issue_date_in_invoice_is_iso(self, tmp_path):
        path = _write(tmp_path, "a.csv", "date;libelle;montant\n05/10/2026;A;-1,00\n")
        (invoice,) = BankingInvoiceProducer(path).produce_all()
        assert invoice.reference.issue_date == date(2026, 10, 5)

    def test_ambiguous_day_month_is_read_day_first_for_slashes(self):
        assert _parse_date("03/04/2026") == date(2026, 4, 3)


# ------------------------------------------------------------ TXT & formats


class TestUnsupportedFormats:
    @pytest.mark.parametrize("name", ["releve.txt", "releve.pdf", "releve.xlsx", "releve", "releve.ofx.bak"])
    def test_unsupported_extensions(self, tmp_path, name):
        path = _write(tmp_path, name, "date;montant\n01/10/2026;-1,00\n")
        with pytest.raises(InvoiceProducerError) as exc:
            _parse(path)
        assert exc.value.code == "UNSUPPORTED_BANKING_FORMAT"

    def test_txt_is_rejected_by_every_entry_point(self, tmp_path):
        path = _write(tmp_path, "releve.txt", "date;montant\n01/10/2026;-1,00\n")
        for method in ("produce", "produce_all", "produce_records"):
            with pytest.raises(InvoiceProducerError) as exc:
                getattr(BankingInvoiceProducer(path), method)()
            assert exc.value.code == "UNSUPPORTED_BANKING_FORMAT"

    def test_missing_source(self, tmp_path):
        with pytest.raises(InvoiceProducerError) as exc:
            _parse(tmp_path / "absent.csv")
        assert exc.value.code == "SOURCE_NOT_FOUND"

    def test_directory_as_source(self, tmp_path):
        directory = tmp_path / "d.csv"
        directory.mkdir()
        with pytest.raises(InvoiceProducerError) as exc:
            _parse(directory)
        assert exc.value.code == "SOURCE_NOT_FOUND"


# ------------------------------------------------------ Determinism & records


class TestDeterminism:
    def test_uuid_and_hash_are_deterministic(self, tmp_path):
        path = _write(tmp_path, "a.ofx", _ofx(_trn("-1.00"), _trn("-1.00")))
        p = BankingInvoiceProducer(path, company_id="T")
        t1, t2 = p.parse_transactions()
        assert p._transaction_uuid(t1, 0) == BankingInvoiceProducer(path, company_id="T")._transaction_uuid(t1, 0)
        assert p._transaction_uuid(t1, 0) != p._transaction_uuid(t2, 1)
        assert p._content_hash(t1, 0) == p._content_hash(t1, 0)
        assert p._content_hash(t1, 0) != p._content_hash(t1, 1)

    def test_company_id_changes_uuid(self, tmp_path):
        path = _write(tmp_path, "a.ofx", _ofx(_trn()))
        tx = BankingInvoiceProducer(path).parse_transactions()[0]
        assert BankingInvoiceProducer(path, company_id="A")._transaction_uuid(tx, 0) != \
            BankingInvoiceProducer(path, company_id="B")._transaction_uuid(tx, 0)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("CARD", "CARD"), ("pos", "CARD"), ("direct-debit", "DIRECT_DEBIT"), ("Chèque", "CHEQUE"), ("???", "UNKNOWN"), (None, "UNKNOWN")],
    )
    def test_transaction_type_mapping(self, raw, expected):
        from services.banking_invoice_producer import BankTransaction
        tx = BankTransaction(date(2026, 1, 1), Decimal("1"), "EUR", DEBIT, "x", transaction_type=raw)
        assert BankingInvoiceProducer._transaction_type(tx).name == expected
