"""P22 - Estimated 2026 profit & loss and balance sheet for APD.

The CA12 VAT simulation answers "how much VAT do we owe?". The manager also
needs a plain-language preview of the year: revenue, purchases, gross margin,
operating result, customer receivables, supplier payables, OFX cash and the
manager's current account (455). This service builds that preview from the
same validated SQLite ledger, without ever posting a real accounting entry.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from schemas.folder_state_schema import DIRIGEANT_COUNTERPART_ACCOUNT, DocumentCategory
from services.apd_evidence_rules import NON_ACCOUNTABLE_DOCUMENT_TYPES
from services.sqlite_accounting_store import SqliteAccountingStore, validate_tenant_id

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
BALANCE_FILENAME = "SIMULATION_BILAN_RESULTAT_{year}.xlsx"
BALANCE_SHEET_RESULT = "COMPTE_RESULTAT"
BALANCE_SHEET_BALANCE = "BILAN"
BALANCE_SHEET_DETAIL = "DETAIL_PIECES"
BALANCE_SHEET_METHOD = "METHODE"
PAID_STATUSES = frozenset({"PAYEE_RAPPROCHEE", "PAYE", "SOLDEE"})


class BalanceSimulationError(ValueError):
    """Invalid balance simulation request; ``code`` is stable for callers."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).replace("\u00a0", "").replace("\u202f", "").strip()
    if not text:
        return None
    try:
        number = Decimal(text.replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    return number.quantize(CENT)


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if len(text) < 10:
        return None
    for parser in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text[:10], parser).date()
        except ValueError:
            continue
    return None


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _is_apd_self(row: dict[str, Any]) -> bool:
    text = " ".join(
        _text(row.get(key))
        for key in ("counterparty", "supplier", "company_name", "label")
    ).casefold()
    if "autre s.a.s" in text or "assistance peintures diffusion" in text:
        return True
    digits = "".join(character for character in text if character.isdigit())
    return "810908392" in digits


def _category(row: dict[str, Any]) -> str:
    return _text(row.get("category")).upper()


def _is_avoir(row: dict[str, Any]) -> bool:
    return _text(row.get("document_type")).upper() == "AVOIR"


def _is_paid(row: dict[str, Any]) -> bool:
    if _text(row.get("already_paid_on_invoice")) in {"1", "true", "True"}:
        return True
    return _text(row.get("reconciliation_status")).upper() in PAID_STATUSES


@dataclass(frozen=True)
class BalanceDetailRow:
    operation_date: date | None
    counterparty: str
    document_number: str
    category: str
    amount_ht: Decimal
    amount_tva: Decimal
    amount_ttc: Decimal
    status: str
    piece_pdf: str | None = None


@dataclass(frozen=True)
class BalanceSimulationReport:
    tenant_id: str
    year: int
    revenue_ht: Decimal
    purchases_ht: Decimal
    gross_margin: Decimal
    transport_ht: Decimal
    general_expenses_ht: Decimal
    fiscal_social_ht: Decimal
    bank_finance_ht: Decimal
    operating_result: Decimal
    receivables: Decimal
    payables: Decimal
    cash: Decimal
    shareholder_current_account: Decimal
    pieces: int = 0
    details: tuple[BalanceDetailRow, ...] = ()
    output_path: Path | None = None
    assumptions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def chiffre_affaires_ht(self) -> Decimal:
        return self.revenue_ht

    @property
    def achats_marchandises_ht(self) -> Decimal:
        return self.purchases_ht

    @property
    def marge_brute(self) -> Decimal:
        return self.gross_margin

    @property
    def transports_frais_generaux_ht(self) -> Decimal:
        return (self.transport_ht + self.general_expenses_ht).quantize(CENT)

    @property
    def resultat_exploitation(self) -> Decimal:
        return self.operating_result

    @property
    def creances_clients(self) -> Decimal:
        return self.receivables

    @property
    def dettes_fournisseurs(self) -> Decimal:
        return self.payables

    @property
    def tresorerie_ofx(self) -> Decimal:
        return self.cash

    @property
    def compte_courant_associe(self) -> Decimal:
        return self.shareholder_current_account


class BalanceSimulationService:
    """Compute and export the estimated 2026 result and balance sheet."""

    def __init__(
        self,
        store: SqliteAccountingStore,
        *,
        export_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self._store = store
        self._export_dir = Path(export_dir) if export_dir is not None else None

    @property
    def export_dir(self) -> Path:
        if self._export_dir is not None:
            return self._export_dir
        return Path("workspace") / "document_flow" / "default" / "04_EXPORT_COMPTA" / "default"

    def simulate(self, tenant_id: str, year: int) -> BalanceSimulationReport:
        try:
            tenant = validate_tenant_id(tenant_id)
        except Exception as exc:  # noqa: BLE001 - re-raise a typed error
            raise BalanceSimulationError("INVALID_TENANT_ID", repr(tenant_id)) from exc
        if not isinstance(year, int) or isinstance(year, bool) or year < 2000 or year > 2100:
            raise BalanceSimulationError("INVALID_YEAR", repr(year))
        rows = self._store.list_master_lines(tenant)
        revenue = purchases = transport = general = fiscal = bank = ZERO
        cash = ZERO
        shareholder = ZERO
        details: list[BalanceDetailRow] = []
        seen: set[str] = set()
        for row in rows:
            if _is_apd_self(row):
                continue
            if _text(row.get("document_type")).upper() in NON_ACCOUNTABLE_DOCUMENT_TYPES:
                continue
            status = _text(row.get("raw_status") or row.get("status")).upper()
            operation_date = _date(row.get("operation_date"))
            debit = _decimal(row.get("debit_amount"))
            credit = _decimal(row.get("credit_amount"))
            if debit is not None or credit is not None:
                cash += (credit or ZERO) - (debit or ZERO)
            if status != "VALIDE":
                continue
            counter_account = _text(row.get("counterparty_account")).upper()
            if "455" in counter_account or counter_account == DIRIGEANT_COUNTERPART_ACCOUNT:
                shareholder += _decimal(row.get("amount_ttc")) or ZERO
            if operation_date is None or operation_date.year != year:
                continue
            category = _category(row)
            ht = _decimal(row.get("amount_ht"))
            ttc = _decimal(row.get("amount_ttc"))
            if category == DocumentCategory.BANQUE_FINANCE.value and ht is None:
                ht = ttc
            if ht is None:
                continue
            sign = Decimal("-1") if _is_avoir(row) else Decimal("1")
            amount = (ht * sign).quantize(CENT)
            if category == DocumentCategory.VENTES_CLIENTS.value:
                revenue += amount
            elif category == DocumentCategory.ACHATS_MARCHANDISES.value:
                purchases += amount
            elif category == DocumentCategory.TRANSPORTS_LOGISTIQUE.value:
                transport += amount
            elif category == DocumentCategory.FRAIS_GENERAUX.value:
                general += amount
            elif category == DocumentCategory.FISCAL_SOCIAL.value:
                fiscal += amount
            elif category == DocumentCategory.BANQUE_FINANCE.value:
                bank += amount
            else:
                continue
            identifier = _text(row.get("line_id") or row.get("invoice_id"))
            if identifier and identifier in seen:
                continue
            seen.add(identifier)
            details.append(
                BalanceDetailRow(
                    operation_date=operation_date,
                    counterparty=_text(row.get("counterparty") or row.get("supplier")),
                    document_number=_text(
                        row.get("invoice_id")
                        or row.get("piece_number")
                        or row.get("label")
                        or identifier
                    ),
                    category=category,
                    amount_ht=_decimal(row.get("amount_ht")) or ZERO,
                    amount_tva=_decimal(row.get("amount_tva")) or ZERO,
                    amount_ttc=ttc or ZERO,
                    status=status,
                    piece_pdf=_text(row.get("piece_pdf")) or None,
                )
            )
        receipts = self._receivables(tenant)
        payables = self._payables(tenant, rows)
        gross_margin = (revenue - purchases).quantize(CENT)
        operating = (
            gross_margin - transport - general - fiscal - bank
        ).quantize(CENT)
        assumptions = (
            "Données issues uniquement des pièces VALIDE du grand livre APD.",
            "Les avoirs diminuent le poste concerné ; les pièces REJETE sont exclues.",
            "La TVA n'entre pas dans le résultat : seuls les montants HT sont utilisés.",
            "La trésorerie OFX représente le flux net des opérations bancaires importées.",
            "Le compte 455 représente les dépenses avancées par le dirigeant.",
        )
        return BalanceSimulationReport(
            tenant_id=tenant,
            year=year,
            revenue_ht=revenue.quantize(CENT),
            purchases_ht=purchases.quantize(CENT),
            gross_margin=gross_margin,
            transport_ht=transport.quantize(CENT),
            general_expenses_ht=general.quantize(CENT),
            fiscal_social_ht=fiscal.quantize(CENT),
            bank_finance_ht=bank.quantize(CENT),
            operating_result=operating,
            receivables=receipts,
            payables=payables,
            cash=cash.quantize(CENT),
            shareholder_current_account=shareholder.quantize(CENT),
            pieces=len(details),
            details=tuple(details),
            assumptions=assumptions,
        )

    def _receivables(self, tenant_id: str) -> Decimal:
        list_invoices = getattr(self._store, "list_customer_invoices", None)
        if not callable(list_invoices):
            return ZERO
        total = ZERO
        for row in list_invoices(tenant_id):
            if _is_avoir(row) or _is_paid(row):
                continue
            balance = _decimal(row.get("balance_due"))
            total += balance if balance is not None else (_decimal(row.get("amount_ttc")) or ZERO)
        return total.quantize(CENT)

    def _payables(self, tenant_id: str, rows: list[dict[str, Any]]) -> Decimal:
        total = ZERO
        for row in rows:
            if _is_apd_self(row):
                continue
            if _text(row.get("document_type")).upper() in NON_ACCOUNTABLE_DOCUMENT_TYPES:
                continue
            if _text(row.get("raw_status") or row.get("status")).upper() != "VALIDE":
                continue
            category = _category(row)
            if category == DocumentCategory.VENTES_CLIENTS.value:
                continue
            counter_account = _text(row.get("counterparty_account")).upper()
            if "455" in counter_account or counter_account == DIRIGEANT_COUNTERPART_ACCOUNT:
                continue
            ttc = _decimal(row.get("amount_ttc")) or ZERO
            if _is_avoir(row):
                total -= abs(ttc)
                continue
            if _is_paid(row):
                continue
            balance = _decimal(row.get("balance_due"))
            total += balance if balance is not None else ttc
        return total.quantize(CENT)

    def export(self, tenant_id: str, year: int) -> Path:
        report = self.simulate(tenant_id, year)
        return self.write_report(report)

    def balance_export_path(self, tenant_id: str, year: int) -> Path:
        try:
            tenant = validate_tenant_id(tenant_id)
        except Exception as exc:  # noqa: BLE001
            raise BalanceSimulationError("INVALID_TENANT_ID", repr(tenant_id)) from exc
        if not isinstance(year, int) or isinstance(year, bool):
            raise BalanceSimulationError("INVALID_YEAR", repr(year))
        return self.export_dir / tenant / BALANCE_FILENAME.format(year=year)

    def write_report(self, report: BalanceSimulationReport) -> Path:
        path = self.balance_export_path(report.tenant_id, report.year)
        _write_balance_workbook(report, path)
        return path


def _write_balance_workbook(report: BalanceSimulationReport, output_path: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    from services.pdf_splitter_and_linker import PIECE_LINK_HEADER, relative_link

    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    first = True
    thin = Side(style="thin", color="B7C4D4")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill("solid", fgColor="1F4E78")
    band_fill = PatternFill("solid", fgColor="F2F7FB")
    total_fill = PatternFill("solid", fgColor="DDEBF7")

    def sheet(name: str):
        nonlocal first
        if first:
            selected = workbook.active
            selected.title = name[:31]
            selected.sheet_view.showGridLines = False
            first = False
            return selected
        selected = workbook.create_sheet(name[:31])
        selected.sheet_view.showGridLines = False
        return selected

    def write_table(selected, headers: tuple[str, ...], rows: list[tuple[Any, ...]]):
        for column, header in enumerate(headers, start=1):
            cell = selected.cell(row=1, column=column, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.border = border
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for offset, values in enumerate(rows, start=2):
            for column, value in enumerate(values, start=1):
                cell = selected.cell(row=offset, column=column)
                cell.value = value
                cell.border = border
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                if isinstance(value, (int, float, Decimal)):
                    cell.number_format = "#,##0.00"
                    cell.alignment = Alignment(horizontal="right", vertical="top")
                elif offset % 2 == 0:
                    cell.fill = band_fill
        selected.auto_filter.ref = (
            f"A1:{get_column_letter(len(headers))}{max(selected.max_row, 1)}"
        )
        selected.freeze_panes = "A2"
        for column, header in enumerate(headers, start=1):
            width = max(14, min(len(header) + 4, 32))
            if header in (PIECE_LINK_HEADER,):
                width = 42
            selected.column_dimensions[get_column_letter(column)].width = width
        return selected

    result_sheet = sheet(BALANCE_SHEET_RESULT)
    result_rows = [
        ("Chiffre d'Affaires HT", report.revenue_ht, "Ventes clients validées, avoirs déduits"),
        ("Achats Marchandises HT", report.purchases_ht, "Achats marchandises validés"),
        ("Marge Brute", report.gross_margin, "Chiffre d'Affaires HT - Achats Marchandises HT"),
        (
            "Transports & Frais Généraux HT",
            report.transports_frais_generaux_ht,
            "Transports, logistique et frais généraux",
        ),
        ("Résultat d'Exploitation estimé", report.operating_result, "Marge brute - charges d'exploitation"),
    ]
    write_table(result_sheet, ("Poste", "Montant HT", "Commentaire"), result_rows)
    for row_index in range(2, result_sheet.max_row + 1):
        if row_index in (4, 6):
            for column in range(1, 4):
                result_sheet.cell(row=row_index, column=column).fill = total_fill
                result_sheet.cell(row=row_index, column=column).font = Font(bold=True)

    balance_sheet = sheet(BALANCE_SHEET_BALANCE)
    balance_rows = [
        ("Créances clients restantes", report.receivables, "Factures clients non encore rapprochées"),
        ("Dettes fournisseurs", report.payables, "Factures fournisseurs non encore payées"),
        ("Trésorerie OFX (flux net importé)", report.cash, "Crédits OFX - débits OFX importés"),
        (
            "Compte Courant d'Associé 455",
            report.shareholder_current_account,
            "Dépenses avancées par le dirigeant",
        ),
    ]
    write_table(balance_sheet, ("Poste", "Montant", "Commentaire"), balance_rows)

    detail_sheet = sheet(BALANCE_SHEET_DETAIL)
    detail_rows: list[tuple[Any, ...]] = []
    for detail in sorted(
        report.details,
        key=lambda item: (item.operation_date or date.max, item.counterparty.casefold()),
    ):
        link = detail.piece_pdf
        if link:
            candidate = Path(link)
            if not candidate.is_absolute():
                candidate = output_path.parent / candidate
            if candidate.is_file():
                link = relative_link(candidate, output_path)
            else:
                link = None
        detail_rows.append(
            (
                detail.operation_date,
                detail.counterparty,
                detail.document_number,
                detail.category,
                float(detail.amount_ht),
                float(detail.amount_tva),
                float(detail.amount_ttc),
                detail.status,
                link,
            )
        )
    write_table(
        detail_sheet,
        (
            "Date Pièce",
            "Tiers",
            "N° Pièce",
            "Catégorie",
            "HT",
            "TVA",
            "TTC",
            "Statut",
            PIECE_LINK_HEADER,
        ),
        detail_rows,
    )
    link_column = len(detail_sheet[1])
    for row_index in range(2, detail_sheet.max_row + 1):
        cell = detail_sheet.cell(row=row_index, column=link_column)
        if isinstance(cell.value, str) and cell.value:
            cell.hyperlink = cell.value
            cell.font = Font(color="0563C1", underline="single")

    method_sheet = sheet(BALANCE_SHEET_METHOD)
    write_table(
        method_sheet,
        ("Hypothèse",),
        [(assumption,) for assumption in report.assumptions],
    )
    method_sheet.column_dimensions["A"].width = 110

    workbook.properties.creator = "DSH-Core P22"
    workbook.properties.title = f"APD - Simulation Bilan et Résultat {report.year}"
    temporary = output_path.with_name(output_path.name + ".tmp-p22")
    try:
        workbook.save(temporary)
        workbook.close()
        os.replace(temporary, output_path)
    except OSError as exc:
        workbook.close()
        temporary.unlink(missing_ok=True)
        raise BalanceSimulationError("WRITE_FAILED", str(output_path)) from exc


def simulate_balance(
    store: SqliteAccountingStore,
    tenant_id: str,
    year: int,
    *,
    export_dir: str | os.PathLike[str] | None = None,
) -> BalanceSimulationReport:
    return BalanceSimulationService(store, export_dir=export_dir).simulate(tenant_id, year)


def export_balance_simulation(
    store: SqliteAccountingStore,
    tenant_id: str,
    year: int,
    *,
    export_dir: str | os.PathLike[str] | None = None,
) -> Path:
    return BalanceSimulationService(store, export_dir=export_dir).export(tenant_id, year)


__all__ = [
    "BALANCE_FILENAME",
    "BALANCE_SHEET_BALANCE",
    "BALANCE_SHEET_DETAIL",
    "BALANCE_SHEET_METHOD",
    "BALANCE_SHEET_RESULT",
    "BalanceDetailRow",
    "BalanceSimulationError",
    "BalanceSimulationReport",
    "BalanceSimulationService",
    "export_balance_simulation",
    "simulate_balance",
]
