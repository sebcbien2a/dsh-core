"""Exhaustive tests for the spreadsheet invoice producer."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from services.invoice_producer_protocol import InvoiceProducerError
from services.spreadsheet_invoice_producer import (
    SpreadsheetInvoiceProducer,
    _normalise_currency,
    _parse_decimal,
    _parse_optional_date,
)

HEADER = ["Fournisseur", "Date", "HT", "TVA", "TTC", "Reference"]


def _xlsx(tmp_path: Path, sheets: dict[str, list[list]], *, hidden=(), name="j.xlsx",
          merges=()) -> Path:
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for row in rows:
            ws.append(row)
        if title in hidden:
            ws.sheet_state = "hidden"
        for rng in merges:
            ws.merge_cells(rng)
    path = tmp_path / name
    wb.save(path)
    return path


def _csv(tmp_path: Path, content: str | bytes, encoding="utf-8", name="j.csv") -> Path:
    path = tmp_path / name
    path.write_bytes(content if isinstance(content, bytes) else content.encode(encoding))
    return path


def _rows(path: Path, **kwargs):
    return SpreadsheetInvoiceProducer(path, **kwargs).parse_rows()


def _err(path: Path, **kwargs) -> str:
    with pytest.raises(InvoiceProducerError) as exc:
        _rows(path, **kwargs)
    return exc.value.code


# ------------------------------------------------------------ multi-sheet


class TestSheets:
    def test_default_is_first_sheet(self, tmp_path):
        path = _xlsx(tmp_path, {
            "A": [HEADER, ["Alpha", "2026-10-01", 100, 20, 120, "F1"]],
            "B": [HEADER, ["Beta", "2026-10-02", 50, 10, 60, "F2"]],
        })
        assert [r.supplier for r in _rows(path)] == ["Alpha"]

    def test_select_by_name_and_index(self, tmp_path):
        path = _xlsx(tmp_path, {
            "A": [HEADER, ["Alpha", "2026-10-01", 100, 20, 120, "F1"]],
            "B": [HEADER, ["Beta", "2026-10-02", 50, 10, 60, "F2"]],
        })
        assert _rows(path, sheet_name="B")[0].supplier == "Beta"
        assert _rows(path, sheet_name=1)[0].supplier == "Beta"

    @pytest.mark.parametrize("title", ["Écritures 2026", "Journal d'achats", "Sheet 1 (copie)",
                                       "A&B", "été", "日本語", "x" * 31, "2026-10"])
    def test_atypical_sheet_names(self, tmp_path, title):
        path = _xlsx(tmp_path, {title: [HEADER, ["Alpha", "2026-10-01", 100, 20, 120, "F1"]]})
        assert _rows(path, sheet_name=title)[0].supplier == "Alpha"

    def test_unknown_sheet_name_is_a_clean_error(self, tmp_path):
        path = _xlsx(tmp_path, {"A": [HEADER, ["Alpha", "2026-10-01", 100, 20, 120, "F1"]]})
        assert _err(path, sheet_name="Nope") == "INVALID_SPREADSHEET_SHEET"

    @pytest.mark.parametrize("index", [5, -9])
    def test_sheet_index_out_of_range_is_a_clean_error(self, tmp_path, index):
        path = _xlsx(tmp_path, {"A": [HEADER, ["Alpha", "2026-10-01", 100, 20, 120, "F1"]]})
        assert _err(path, sheet_name=index) == "INVALID_SPREADSHEET_SHEET"

    def test_hidden_sheet_can_be_selected_explicitly(self, tmp_path):
        path = _xlsx(tmp_path, {
            "Vis": [HEADER, ["Alpha", "2026-10-01", 100, 20, 120, "F1"]],
            "Cachée": [HEADER, ["Hidden", "2026-10-02", 50, 10, 60, "F2"]],
        }, hidden=("Cachée",))
        assert _rows(path, sheet_name="Cachée")[0].supplier == "Hidden"

    def test_hidden_first_sheet_is_still_index_zero(self, tmp_path):
        path = _xlsx(tmp_path, {
            "Cachée": [HEADER, ["Hidden", "2026-10-02", 50, 10, 60, "F2"]],
            "Vis": [HEADER, ["Alpha", "2026-10-01", 100, 20, 120, "F1"]],
        }, hidden=("Cachée",))
        assert _rows(path)[0].supplier == "Hidden"
        assert _rows(path, sheet_name="Vis")[0].supplier == "Alpha"

    def test_empty_first_sheet(self, tmp_path):
        path = _xlsx(tmp_path, {"Vide": [], "B": [HEADER, ["Beta", "2026-10-02", 50, 10, 60, "F2"]]})
        assert _err(path) == "NO_SPREADSHEET_ROWS"
        assert _rows(path, sheet_name="B")[0].supplier == "Beta"

    def test_sheet_name_ignored_for_csv(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;HT;TVA;TTC\nA;100;20;120\n")
        assert len(_rows(path, sheet_name="whatever")) == 1


# ------------------------------------------------------------------ dates


class TestDates:
    def _one(self, tmp_path, value):
        path = _xlsx(tmp_path, {"S": [HEADER, ["A", value, 100, 20, 120, "F"]]})
        return _rows(path)[0].invoice_date

    def test_native_excel_date_cell(self, tmp_path):
        assert self._one(tmp_path, datetime(2026, 10, 1)) == date(2026, 10, 1)

    def test_native_excel_datetime_with_time(self, tmp_path):
        assert self._one(tmp_path, datetime(2026, 10, 1, 23, 59, 59)) == date(2026, 10, 1)

    def test_python_date_cell(self, tmp_path):
        assert self._one(tmp_path, date(2026, 10, 1)) == date(2026, 10, 1)

    @pytest.mark.parametrize("text", ["2026-10-01", "01/10/2026", "01-10-2026",
                                      "01.10.2026", "20261001", "2026/10/01"])
    def test_text_dates(self, tmp_path, text):
        assert self._one(tmp_path, text) == date(2026, 10, 1)

    def test_excel_serial_number_in_numeric_cell(self, tmp_path):
        # 46296 is 2026-10-01 in the 1900 date system
        assert self._one(tmp_path, 46296) == date(2026, 10, 1)

    def test_excel_serial_number_as_text(self, tmp_path):
        assert self._one(tmp_path, "46296") == date(2026, 10, 1)

    def test_excel_serial_number_with_fraction(self, tmp_path):
        assert self._one(tmp_path, 46296.75) == date(2026, 10, 1)

    def test_excel_serial_as_float_text(self, tmp_path):
        assert self._one(tmp_path, "46296.0") == date(2026, 10, 1)

    def test_empty_date_gives_warning_not_error(self, tmp_path):
        path = _xlsx(tmp_path, {"S": [HEADER, ["A", None, 100, 20, 120, "F"]]})
        (row,) = _rows(path)
        assert row.invoice_date is None and "date_missing" in row.warnings

    @pytest.mark.parametrize("bad", ["31/02/2026", "2026-13-01", "n'importe quoi", "20261345", "hier"])
    def test_invalid_dates_rejected(self, tmp_path, bad):
        path = _xlsx(tmp_path, {"S": [HEADER, ["A", bad, 100, 20, 120, "F"]]})
        assert _err(path) == "INVALID_SPREADSHEET_ROW"

    def test_parse_optional_date_helpers(self):
        assert _parse_optional_date(None) is None
        assert _parse_optional_date("  ") is None
        assert _parse_optional_date("29/02/2028") == date(2028, 2, 29)
        with pytest.raises(InvoiceProducerError):
            _parse_optional_date("29/02/2027")

    def test_date_in_csv_with_time_suffix(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;Date;HT;TVA;TTC\nA;2026-10-01 00:00:00;100;20;120\n")
        assert _rows(path)[0].invoice_date == date(2026, 10, 1)


# --------------------------------------------- layout: headers, blanks, totals


class TestLayout:
    def test_title_rows_above_header(self, tmp_path):
        path = _xlsx(tmp_path, {"S": [
            ["Journal des achats"], ["Exercice 2026"], [],
            HEADER, ["A", "2026-10-01", 100, 20, 120, "F1"]]})
        (row,) = _rows(path)
        assert row.supplier == "A" and row.row_index == 0

    def test_header_beyond_scan_window_fails_then_explicit_header_row_works(self, tmp_path):
        rows = [["titre"]] * 12 + [HEADER, ["A", "2026-10-01", 100, 20, 120, "F1"]]
        path = _xlsx(tmp_path, {"S": rows})
        assert _err(path) == "INVALID_SPREADSHEET_HEADER"
        assert _rows(path, header_row=12)[0].supplier == "A"
        assert _rows(path, header_scan_rows=20)[0].supplier == "A"

    def test_header_row_out_of_range(self, tmp_path):
        path = _xlsx(tmp_path, {"S": [HEADER, ["A", "2026-10-01", 100, 20, 120, "F1"]]})
        assert _err(path, header_row=50) == "INVALID_SPREADSHEET_HEADER"
        assert _err(path, header_row=-1) == "INVALID_SPREADSHEET_HEADER"

    def test_no_recognisable_header(self, tmp_path):
        path = _xlsx(tmp_path, {"S": [["a", "b", "c"], [1, 2, 3]]})
        assert _err(path) == "INVALID_SPREADSHEET_HEADER"

    def test_header_without_amount_columns(self, tmp_path):
        path = _xlsx(tmp_path, {"S": [["Fournisseur", "Date", "Reference"], ["A", "2026-10-01", "F"]]})
        assert _err(path) == "INVALID_SPREADSHEET_HEADER"

    def test_fully_blank_rows_skipped(self, tmp_path):
        path = _xlsx(tmp_path, {"S": [HEADER, [], ["A", "2026-10-01", 100, 20, 120, "F1"],
                                      [None] * 6, ["B", "2026-10-02", 10, 2, 12, "F2"]]})
        assert [r.supplier for r in _rows(path)] == ["A", "B"]

    def test_empty_cells_in_row(self, tmp_path):
        path = _xlsx(tmp_path, {"S": [HEADER, [None, None, 100, None, None, None]]})
        (row,) = _rows(path)
        assert row.supplier == "FOURNISSEUR_INCONNU" and "supplier_missing" in row.warnings
        assert row.gross == Decimal("100") and row.reference is None

    def test_supplier_override_fills_blank_supplier(self, tmp_path):
        path = _xlsx(tmp_path, {"S": [HEADER, [None, "2026-10-01", 100, 20, 120, "F"]]})
        assert _rows(path, supplier_override="ACME")[0].supplier == "ACME"

    def test_row_without_any_amount_rejected(self, tmp_path):
        path = _xlsx(tmp_path, {"S": [HEADER, ["A", "2026-10-01", None, None, None, "F"]]})
        assert _err(path) == "INVALID_SPREADSHEET_ROW"

    def test_merged_cells_only_top_left_is_filled(self, tmp_path):
        path = _xlsx(tmp_path, {"S": [
            HEADER,
            ["Alpha", "2026-10-01", 100, 20, 120, "F1"],
            [None, "2026-10-02", 50, 10, 60, "F2"],
        ]}, merges=("A2:A3",))
        first, second = _rows(path)
        assert first.supplier == "Alpha"
        assert second.supplier == "FOURNISSEUR_INCONNU"

    def test_merged_title_row_does_not_break_header_detection(self, tmp_path):
        path = _xlsx(tmp_path, {"S": [["JOURNAL", None, None, None, None, None],
                                      HEADER, ["A", "2026-10-01", 100, 20, 120, "F"]]},
                     merges=("A1:F1",))
        assert len(_rows(path)) == 1

    def test_duplicate_and_blank_header_names(self, tmp_path):
        path = _xlsx(tmp_path, {"S": [["Fournisseur", "HT", "HT", None, "TVA", "TTC"],
                                      ["A", 100, 999, "x", 20, 120]]})
        (row,) = _rows(path)
        assert row.net == Decimal("100") and row.gross == Decimal("120")

    def test_extra_cells_beyond_header_are_ignored(self, tmp_path):
        path = _xlsx(tmp_path, {"S": [HEADER, ["A", "2026-10-01", 100, 20, 120, "F", "extra", "more"]]})
        assert _rows(path)[0].gross == Decimal("120")

    def test_accented_uppercase_headers_and_aliases(self, tmp_path):
        path = _xlsx(tmp_path, {"S": [["RAISON SOCIALE", "DATE FACTURE", "MONTANT HT", "MONTANT TVA",
                                       "MONTANT TTC", "N° FACTURE"],
                                      ["A", "2026-10-01", 100, 20, 120, "F9"]]})
        (row,) = _rows(path)
        assert (row.supplier, row.reference, row.net) == ("A", "F9", Decimal("100"))

    def test_column_map_override(self, tmp_path):
        path = _xlsx(tmp_path, {"S": [["Société", "Jour", "Base", "Taxe", "Fournisseur"],
                                      ["Soc", "2026-10-01", 100, 20, "ignored"]]})
        (row,) = _rows(path, column_map={"supplier": "Société", "date": "Jour", "net": "Base", "tax": "Taxe"})
        assert row.supplier == "Soc" and row.net == Decimal("100") and row.gross == Decimal("120")

    def test_totals_row_at_bottom_is_not_ingested_as_invoice(self, tmp_path):
        path = _xlsx(tmp_path, {"S": [HEADER,
                                      ["A", "2026-10-01", 100, 20, 120, "F1"],
                                      ["B", "2026-10-02", 50, 10, 60, "F2"],
                                      ["TOTAL", None, 150, 30, 180, None]]})
        rows = _rows(path)
        assert [r.supplier for r in rows] == ["A", "B"]

    @pytest.mark.parametrize("label", ["Total", "TOTAL GÉNÉRAL", "Sous-total", "Total TTC", "Totaux"])
    def test_total_labels_variants(self, tmp_path, label):
        path = _xlsx(tmp_path, {"S": [HEADER, ["A", "2026-10-01", 100, 20, 120, "F1"],
                                      [label, None, 100, 20, 120, None]]})
        assert len(_rows(path)) == 1

    def test_totals_row_in_csv(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;Date;HT;TVA;TTC\nA;01/10/2026;100;20;120\nTotal;;100;20;120\n")
        assert len(_rows(path)) == 1

    def test_summary_aggregates_rows(self, tmp_path):
        path = _xlsx(tmp_path, {"S": [HEADER, ["A", "2026-10-01", 100, 20, 120, "F1"],
                                      ["B", "2026-10-02", 50, 10, 60, "F2"]]})
        summary = SpreadsheetInvoiceProducer(path).produce_summary()
        assert summary.metadata["row_count"] == 2
        assert Decimal(summary.metadata["gross_total"]) == Decimal("180")
        assert Decimal(summary.metadata["net_total"]) == Decimal("150")

    def test_summary_without_net_has_none(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;TTC\nA;120\nB;60\n")
        summary = SpreadsheetInvoiceProducer(path).produce_summary()
        assert summary.metadata["net_total"] is None


# -------------------------------------------------------------- amounts etc.


class TestAmounts:
    @pytest.mark.parametrize(("raw", "expected"), [
        ("12", "12"), ("12,5", "12.5"), ("12.5", "12.5"), ("1 234,56", "1234.56"),
        ("1\u00a0234,56", "1234.56"), ("1.234,56", "1234.56"), ("1,234.56", "1234.56"),
        ("1.234.567,89", "1234567.89"), ("12,50 €", "12.50"), ("€12.50", "12.50"),
        ("-12,50", "-12.50"), ("+3", "3"), ("0,00", "0.00"),
    ])
    def test_parse_decimal(self, raw, expected):
        assert _parse_decimal(raw) == Decimal(expected)

    @pytest.mark.parametrize("raw", ["", "  ", "abc", "12,5x", "--1", "NaN", "Infinity", "-", "€"])
    def test_parse_decimal_rejects(self, raw):
        with pytest.raises(InvoiceProducerError) as exc:
            _parse_decimal(raw)
        assert exc.value.code == "INVALID_SPREADSHEET_ROW"

    def test_numeric_float_cells_keep_precision(self, tmp_path):
        path = _xlsx(tmp_path, {"S": [HEADER, ["A", "2026-10-01", 0.1, 0.02, 0.12, "F"]]})
        (row,) = _rows(path)
        assert row.net == Decimal("0.1") and row.gross == Decimal("0.12")

    def test_csv_french_decimal_commas(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;HT;TVA;TTC\nA;1 000,50;200,10;1 200,60\n")
        (row,) = _rows(path)
        assert (row.net, row.tax, row.gross) == (Decimal("1000.50"), Decimal("200.10"), Decimal("1200.60"))

    def test_csv_english_decimal_points(self, tmp_path):
        path = _csv(tmp_path, "supplier,net,tax,gross\nA,1000.50,200.10,1200.60\n")
        assert _rows(path)[0].gross == Decimal("1200.60")

    def test_gross_derived_from_net_and_tax(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;HT;TVA\nA;100;20\n")
        assert _rows(path)[0].gross == Decimal("120")

    def test_net_derived_from_gross_and_tax(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;TVA;TTC\nA;20;120\n")
        assert _rows(path)[0].net == Decimal("100")

    def test_tax_derived_from_net_and_gross(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;HT;TTC\nA;100;120\n")
        assert _rows(path)[0].tax == Decimal("20")

    def test_gross_equals_net_when_only_net(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;HT\nA;100\n")
        assert _rows(path)[0].gross == Decimal("100")

    @pytest.mark.parametrize("cell", ["-100", "-100,00", -100])
    def test_negative_gross_rejected_with_row_context(self, tmp_path, cell):
        path = _xlsx(tmp_path, {"S": [HEADER, ["A", "2026-10-01", None, None, cell, "F"]]})
        with pytest.raises(InvoiceProducerError) as exc:
            _rows(path)
        assert exc.value.code == "INVALID_SPREADSHEET_ROW" and "négatif" in str(exc.value)

    def test_credit_note_negative_net_rejected(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;HT;TVA;TTC\nA;-100;-20;-120\n")
        assert _err(path) == "INVALID_SPREADSHEET_ROW"

    def test_discount_row_as_negative_line_is_rejected(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;HT;TVA;TTC\nA;100;20;120\nRemise;-10;-2;-12\n")
        assert _err(path) == "INVALID_SPREADSHEET_ROW"

    def test_row_error_reports_row_index(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;HT;TVA;TTC\nA;100;20;120\nB;abc;1;2\n")
        with pytest.raises(InvoiceProducerError) as exc:
            _rows(path)
        assert "ligne 1" in str(exc.value)

    def test_discount_net_greater_than_gross_is_rejected_or_negative_tax_flagged(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;HT;TTC\nA;120;100\n")
        assert _err(path) == "INVALID_SPREADSHEET_ROW"

    def test_currency_column_multiple_currencies(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;HT;TVA;TTC;Devise\nA;100;20;120;eur\nB;50;0;50;usd\nC;1;0;1;\n")
        assert [r.currency for r in _rows(path)] == ["EUR", "USD", "EUR"]

    def test_default_currency(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;TTC\nA;1\n")
        assert _rows(path, currency="CHF")[0].currency == "CHF"

    @pytest.mark.parametrize("bad", ["EURO", "€", "12", "E R"])
    def test_invalid_currency(self, tmp_path, bad):
        path = _csv(tmp_path, f"Fournisseur;TTC;Devise\nA;1;{bad}\n")
        assert _err(path) == "INVALID_SPREADSHEET_ROW"

    def test_currency_helper(self):
        assert _normalise_currency(None, "gbp") == "GBP"

    def test_siret_and_vat_normalisation(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;TTC;SIRET;TVA intra\nA;1;401 381 363 00019;fr 12 345\nB;1;123;\n")
        a, b = _rows(path)
        assert a.siret == "40138136300019" and a.vat_number == "FR12345"
        assert b.siret is None and "siret_ignored" in b.warnings

    def test_direction_column(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;TTC;Sens\nA;1;Vente\nB;1;achat\nC;1;?\n")
        assert [r.direction for r in _rows(path)] == ["SALE", "PURCHASE", "PURCHASE"]

    def test_default_direction_sale_and_invalid(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;TTC\nA;1\n")
        assert _rows(path, default_direction="sale")[0].direction == "SALE"
        with pytest.raises(ValueError):
            SpreadsheetInvoiceProducer(path, default_direction="BOTH")

    def test_sale_rows_use_customer_party(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;TTC;Sens\nA;1;Vente\n")
        invoice = SpreadsheetInvoiceProducer(path).produce()
        assert invoice.direction.value == "SALE" and invoice.customer.name == "A"


# ----------------------------------------------------------- CSV & encodings


class TestEncodingsAndCsv:
    TEXT = "Fournisseur;Libellé;HT;TVA;TTC\nÉts Müller & Fils;Café à 5 €;100;20;120\n"

    def test_utf8(self, tmp_path):
        (row,) = _rows(_csv(tmp_path, self.TEXT))
        assert row.supplier == "Éts Müller & Fils" and row.description == "Café à 5 €"

    def test_utf8_bom(self, tmp_path):
        (row,) = _rows(_csv(tmp_path, b"\xef\xbb\xbf" + self.TEXT.encode("utf-8")))
        assert row.supplier == "Éts Müller & Fils"

    @pytest.mark.parametrize("encoding", ["cp1252", "iso-8859-1"])
    def test_legacy_encodings(self, tmp_path, encoding):
        text = self.TEXT.replace(" €", "") if encoding == "iso-8859-1" else self.TEXT
        (row,) = _rows(_csv(tmp_path, text, encoding))
        assert row.supplier == "Éts Müller & Fils"
        assert row.description.startswith("Café à 5")

    def test_cp1252_accented_header(self, tmp_path):
        text = "Fournisseur;Désignation;HT;TVA;TTC\nA;x;100;20;120\n"
        (row,) = _rows(_csv(tmp_path, text, "cp1252"))
        assert row.description == "x"

    def test_comma_tab_semicolon_delimiters(self, tmp_path):
        for sep in (",", "\t", ";"):
            text = sep.join(["Fournisseur", "HT", "TVA", "TTC"]) + "\n" + sep.join(["A", "100", "20", "120"]) + "\n"
            assert _rows(_csv(tmp_path, text, name=f"d{ord(sep)}.csv"))[0].gross == Decimal("120")

    def test_txt_suffix_is_accepted(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;TTC\nA;1\n", name="j.txt")
        assert len(_rows(path)) == 1

    def test_crlf_and_trailing_blank_lines(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;TTC\r\nA;1\r\n\r\n\r\n")
        assert len(_rows(path)) == 1

    def test_quoted_delimiter(self, tmp_path):
        path = _csv(tmp_path, 'Fournisseur;Libellé;TTC\n"A; B";"x ""y""";1\n')
        (row,) = _rows(path)
        assert row.supplier == "A; B" and row.description == 'x "y"'

    def test_title_rows_above_header_in_csv(self, tmp_path):
        path = _csv(tmp_path, "Journal;;\nExercice 2026;;\nFournisseur;Date;TTC\nA;01/10/2026;12\n")
        assert _rows(path)[0].invoice_date == date(2026, 10, 1)

    def test_ragged_csv_rows(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;Date;HT;TVA;TTC\nA;01/10/2026;100;20;120\nB;02/10/2026;5\n")
        rows = _rows(path)
        assert rows[1].net == Decimal("5") and rows[1].gross == Decimal("5")

    def test_empty_csv(self, tmp_path):
        assert _err(_csv(tmp_path, b"")) == "NO_SPREADSHEET_ROWS"

    def test_whitespace_only_csv(self, tmp_path):
        assert _err(_csv(tmp_path, "  \n \n")) in {"NO_SPREADSHEET_ROWS", "INVALID_SPREADSHEET_HEADER"}

    def test_header_only_csv_has_no_rows(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;Date;HT;TVA;TTC\n")
        assert _rows(path) == []
        with pytest.raises(InvoiceProducerError) as exc:
            SpreadsheetInvoiceProducer(path).produce_all()
        assert exc.value.code == "NO_SPREADSHEET_ROWS"

    def test_binary_garbage_csv_is_a_clean_error(self, tmp_path):
        assert _err(_csv(tmp_path, bytes(range(256)) * 8)).startswith(("INVALID_", "NO_"))

    def test_utf16_csv_is_a_clean_error_or_parsed(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;TTC\nA;1\n".encode("utf-16"))
        try:
            _rows(path)
        except InvoiceProducerError:
            pass


# ------------------------------------------------- corrupt / unsupported files


class TestCorruptAndErrors:
    def test_missing_file(self, tmp_path):
        assert _err(tmp_path / "absent.xlsx") == "SOURCE_NOT_FOUND"

    @pytest.mark.parametrize("name", ["a.pdf", "a.ods", "a.json", "a", "a.csv.bak"])
    def test_unsupported_suffix(self, tmp_path, name):
        path = _csv(tmp_path, "x", name=name)
        assert _err(path) == "UNSUPPORTED_SPREADSHEET_FORMAT"

    def test_unsupported_suffix_all_entry_points(self, tmp_path):
        path = _csv(tmp_path, "x", name="a.pdf")
        for method in ("produce", "produce_all", "produce_batch", "produce_summary"):
            with pytest.raises(InvoiceProducerError) as exc:
                getattr(SpreadsheetInvoiceProducer(path), method)()
            assert exc.value.code == "UNSUPPORTED_SPREADSHEET_FORMAT"

    def test_empty_xlsx_file_zero_bytes(self, tmp_path):
        path = tmp_path / "e.xlsx"
        path.write_bytes(b"")
        assert _err(path) == "CORRUPT_SPREADSHEET"

    def test_text_renamed_to_xlsx(self, tmp_path):
        path = tmp_path / "t.xlsx"
        path.write_text("Fournisseur;TTC\nA;1\n")
        assert _err(path) == "CORRUPT_SPREADSHEET"

    def test_truncated_xlsx(self, tmp_path):
        good = _xlsx(tmp_path, {"S": [HEADER, ["A", "2026-10-01", 100, 20, 120, "F"]]})
        data = good.read_bytes()
        bad = tmp_path / "trunc.xlsx"
        bad.write_bytes(data[: len(data) // 2])
        assert _err(bad) == "CORRUPT_SPREADSHEET"

    def test_zip_without_workbook_parts(self, tmp_path):
        import zipfile
        path = tmp_path / "z.xlsx"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("hello.txt", "hi")
        assert _err(path) == "CORRUPT_SPREADSHEET"

    def test_legacy_xls_without_engine_or_corrupt(self, tmp_path):
        path = tmp_path / "old.xls"
        path.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)
        assert _err(path) in {"MISSING_SPREADSHEET_ENGINE", "CORRUPT_SPREADSHEET"}

    def test_produce_row_index_out_of_range(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;TTC\nA;1\n")
        for index in (1, -1):
            with pytest.raises(InvoiceProducerError) as exc:
                SpreadsheetInvoiceProducer(path).produce(row_index=index)
            assert exc.value.code == "ROW_INDEX_OUT_OF_RANGE"

    def test_no_rows_in_produce_and_summary(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;TTC\n")
        for method in ("produce", "produce_all", "produce_summary"):
            with pytest.raises(InvoiceProducerError) as exc:
                getattr(SpreadsheetInvoiceProducer(path), method)()
            assert exc.value.code == "NO_SPREADSHEET_ROWS"

    def test_parse_rows_returns_copy(self, tmp_path):
        producer = SpreadsheetInvoiceProducer(_csv(tmp_path, "Fournisseur;TTC\nA;1\n"))
        producer.parse_rows().clear()
        assert len(producer.parse_rows()) == 1

    def test_invoice_payload_fields(self, tmp_path):
        path = _csv(tmp_path, "Fournisseur;Date;Reference;HT;TVA;TTC\nA;01/10/2026;F1;100;20;120\nB;;;5;1;6\n")
        a, b = SpreadsheetInvoiceProducer(path).produce_all()
        assert a.document_type.value == "INVOICE" and a.reference.invoice_number == "F1"
        assert a.reference.issue_date == date(2026, 10, 1)
        assert a.amount.gross_amount == Decimal("120") and a.amount.currency == "EUR"
        assert b.document_type.value == "UNKNOWN" and b.reference.issue_date is None
        assert a.metadata["raw_row"]["Fournisseur"] == "A"
