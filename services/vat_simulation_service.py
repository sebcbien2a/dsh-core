"""P18 - CA12 VAT annual simulation (regime reel simplifie) for APD.

The simulator turns the validated document lines of one tenant into a CA12
comparison without talking to any network resource:

* VAT collected on sales (``VENTES_CLIENTS``);
* VAT deductible on purchases / services split between goods
  (``ACHATS_MARCHANDISES``), transport (``TRANSPORTS_LOGISTIQUE``) and general
  expenses (``FRAIS_GENERAUX``);
* VAT deductible on fixed assets (``IMMOBILISATIONS``);
* the APD specific rules validated in P17: manager-funded expenses
  (``COMPTE_PERSO_DIRIGEANT`` / ``455000_DIRIGEANT``) keep their deductible
  VAT, and hotel ``Soiree Etape`` bills only deduct the meals / breakfast VAT
  (CGI Art. 206);
* the anti-duplicate VAT key of P17 is honoured so a piece is counted once.

Two scenarios are produced:

* **A** - every piece is attached to its original exercise year (``year``);
* **B** - the entered January / February deferrals
  (``fiscal_year_target = year + 1`` and ``deferred_month_override`` in
  ``{"01", "02"}``) are pushed to ``year + 1`` while the incoming reports from
  ``year - 1`` are pulled into ``year``.

The result exposes the net VAT (due or credit), the two next-year half-yearly
instalments (``acomptes``) and the taxable-result impact of the switch.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from datetime import date as date_cls
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Sequence

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from schemas.folder_state_schema import (
    APD_PROFILE,
    DEFERRED_MONTHS,
    DocumentCategory,
    ExpenseSubCategory,
    TvaDedupStatus,
)
from services.accounting_spreadsheet_export_service import (
    DEFAULT_EXPORT_DIR,
    _atomic_save_workbook,
    _hotel_vat_from_row,
    _infer_expense_subcategory,
    _piece_number_from_row,
    _piece_reference,
    _summary_record,
    compute_tva_dedup,
    counterparty_account_of,
    is_apd_self_row,
    payment_source_of,
)
from services.apd_evidence_rules import NON_ACCOUNTABLE_DOCUMENT_TYPES
from services.sqlite_accounting_store import (
    AccountingStoreError,
    SqliteAccountingStore,
    get_shared_sqlite_store,
    validate_tenant_id,
)

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
CA12_SIMULATION_FILENAME = "SIMULATION_CA12_{year}.xlsx"
CA12_OUTPUT_SHEET = "COMPARATIF"
CA12_SUMMARY_SHEET = "SYNTHESE"
CA12_DETAIL_A_SHEET = "DETAIL_A"
CA12_DETAIL_B_SHEET = "DETAIL_B"
CA12_DEFERRED_SHEET = "REPORTS_N_PLUS_1"
CA12_INCOMING_SHEET = "REPORTS_N"

COLLECTED_CATEGORIES = frozenset({DocumentCategory.VENTES_CLIENTS.value})
DEDUCTIBLE_GOODS_CATEGORIES = frozenset(
    {DocumentCategory.ACHATS_MARCHANDISES.value}
)
DEDUCTIBLE_SERVICE_CATEGORIES = frozenset(
    {
        DocumentCategory.TRANSPORTS_LOGISTIQUE.value,
        DocumentCategory.FRAIS_GENERAUX.value,
    }
)
DEDUCTIBLE_ASSET_CATEGORIES = frozenset(
    {DocumentCategory.IMMOBILISATIONS.value}
)
EXPENSE_CATEGORIES = (
    DEDUCTIBLE_GOODS_CATEGORIES
    | DEDUCTIBLE_SERVICE_CATEGORIES
    | DEDUCTIBLE_ASSET_CATEGORIES
)
SIMULATED_CATEGORIES = COLLECTED_CATEGORIES | EXPENSE_CATEGORIES

SENSITIVE_SUBCATEGORIES = frozenset(
    {
        ExpenseSubCategory.RESTAURATION.value,
        ExpenseSubCategory.HOTELLERIE.value,
        ExpenseSubCategory.FRAIS_REPRESENTATION_PRESIDENT.value,
    }
)
SENSITIVE_COUNTERPARTY_KEYWORDS = (
    "temps des cerises",
    "indigo gallery",
    "tradeinn",
)
PAID_RECONCILIATION_STATUSES = frozenset({"PAYEE_RAPPROCHEE", "PAYE", "SOLDEE"})

ACOMPTE_RATES: tuple[tuple[str, Decimal, int, int], ...] = (
    ("JUILLET", Decimal("0.55"), 7, 31),
    ("DECEMBRE", Decimal("0.45"), 12, 31),
)

_COMPARISON_HEADERS: tuple[str, ...] = (
    "Rubrique",
    "Scenario A",
    "Scenario B",
    "Ecart (B - A)",
)
_DETAIL_HEADERS: tuple[str, ...] = (
    "Date",
    "Fournisseur",
    "Piece",
    "Categorie",
    "Sous-categorie",
    "HT",
    "TVA",
    "TVA deductible",
    "TVA non deductible",
    "TTC",
    "Source de paiement",
    "Compte de contrepartie",
    "Exercice cible",
    "Mois decale",
    "Report",
)
_TEXT_MAX_LENGTH = 60


class VatSimulationError(ValueError):
    """Invalid CA12 simulation request; ``code`` is stable for callers."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class VatLine:
    """One normalised line used by a CA12 scenario."""

    line_id: str
    operation_date: date_cls | None
    counterparty: str
    piece: str
    category: str
    subcategory: str
    ht: Decimal
    tva: Decimal
    deductible_tva: Decimal
    non_deductible_tva: Decimal
    ttc: Decimal
    charges: Decimal
    payment_source: str
    counterparty_account: str
    fiscal_year_target: int | None
    deferred_month: str | None
    deferred_to_next_year: bool = False
    incoming_from_previous_year: bool = False

    @property
    def report_label(self) -> str:
        if self.deferred_to_next_year:
            return "REPORTE_N_PLUS_1"
        if self.incoming_from_previous_year:
            return "REPORT_ENTRANT_N"
        return ""


@dataclass(frozen=True)
class VatAcompte:
    """One half-yearly CA12 instalment due the year after the exercise."""

    period: str
    rate: Decimal
    amount: Decimal
    due_date: date_cls


@dataclass(frozen=True)
class VatScenario:
    """Aggregated CA12 figures of one comparison scenario."""

    code: str
    label: str
    year: int
    lines: tuple[VatLine, ...]
    collected_tva: Decimal
    deductible_achats_marchandises: Decimal
    deductible_transports: Decimal
    deductible_frais_generaux: Decimal
    deductible_immobilisations: Decimal
    non_deductible_tva: Decimal
    charges: Decimal
    sensitive_tva: Decimal = ZERO
    deductible_sensitive_tva: Decimal = ZERO
    acomptes: tuple[VatAcompte, ...] = ()

    @property
    def pieces(self) -> int:
        return len(self.lines)

    @property
    def deductible_goods_services(self) -> Decimal:
        return (
            self.deductible_achats_marchandises
            + self.deductible_transports
            + self.deductible_frais_generaux
        ).quantize(CENT)

    @property
    def deductible_total(self) -> Decimal:
        return (
            self.deductible_goods_services
            + self.deductible_immobilisations
        ).quantize(CENT)

    @property
    def net_tva(self) -> Decimal:
        return (self.collected_tva - self.deductible_total).quantize(CENT)

    @property
    def tva_due(self) -> Decimal:
        return max(self.net_tva, ZERO).quantize(CENT)

    @property
    def credit_tva(self) -> Decimal:
        return max(-self.net_tva, ZERO).quantize(CENT)

    @property
    def total_acomptes(self) -> Decimal:
        return sum(
            (item.amount for item in self.acomptes), ZERO
        ).quantize(CENT)


@dataclass(frozen=True)
class Ca12SimulationReport:
    """Complete comparative CA12 simulation for one tenant and exercise."""

    tenant_id: str
    year: int
    scenario_a: VatScenario
    scenario_b: VatScenario
    deferred_to_next_year: tuple[VatLine, ...]
    incoming_from_previous_year: tuple[VatLine, ...]
    generated_at: datetime
    cash_basis: bool = False

    @property
    def next_year(self) -> int:
        return self.year + 1

    @property
    def vat_due_delta(self) -> Decimal:
        """Scenario B due VAT minus scenario A due VAT (positive = B pays more)."""
        return (self.scenario_b.tva_due - self.scenario_a.tva_due).quantize(CENT)

    @property
    def credit_delta(self) -> Decimal:
        return (self.scenario_b.credit_tva - self.scenario_a.credit_tva).quantize(
            CENT
        )

    @property
    def taxable_result_impact(self) -> Decimal:
        """Taxable-result increase caused by pushing charges to ``year + 1``.

        The computation is ``scenario A charges - scenario B charges``. A
        positive value means scenario B leaves more taxable profit in ``year``
        because the deferred pieces are no longer booked in that exercise.
        """
        return (self.scenario_a.charges - self.scenario_b.charges).quantize(CENT)

    @property
    def next_year_acomptes(self) -> tuple[VatAcompte, ...]:
        return self.scenario_b.acomptes

    @property
    def deferred_charges(self) -> Decimal:
        return sum(
            (line.charges for line in self.deferred_to_next_year), ZERO
        ).quantize(CENT)

    @property
    def incoming_charges(self) -> Decimal:
        return sum(
            (line.charges for line in self.incoming_from_previous_year), ZERO
        ).quantize(CENT)


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


def _parse_date(value: Any) -> date_cls | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date_cls):
        return value
    text = str(value or "").strip()
    if len(text) >= 10:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _month(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        text = text.zfill(2)
    return text if text in DEFERRED_MONTHS else None


def _target_year(row: dict[str, Any]) -> int | None:
    text = str(row.get("fiscal_year_target") or "").strip()
    if re.fullmatch(r"\d{4}", text):
        return int(text)
    return None


def _declared_target_year(row: dict[str, Any]) -> int | None:
    """Return the explicit target year or the one implied by the mode."""
    explicit = _target_year(row)
    if explicit is not None:
        return explicit
    mode = str(row.get("fiscal_year_mode") or "").strip().upper()
    operation = _parse_date(row.get("operation_date"))
    if not mode or operation is None:
        return None
    if mode == "SUIVANT":
        return operation.year + 1
    if mode == "PRECEDENT":
        return operation.year - 1
    if mode == "ACTUEL":
        return operation.year
    return None


def _row_category(row: dict[str, Any]) -> str:
    stored = str(row.get("category") or "").strip()
    if stored:
        return stored
    reference = str(row.get("invoice_url") or "")
    parts = re.split(r"[\\/]+", reference)
    for category in DocumentCategory:
        if category.value in parts:
            return category.value
    return ""


def _row_identity(row: dict[str, Any]) -> str:
    for key in ("line_id", "invoice_id", "id"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _operation_year(row: dict[str, Any], cash_basis: bool = False) -> int | None:
    parsed = _cash_date(row) if cash_basis else _parse_date(row.get("operation_date"))
    return parsed.year if parsed is not None else None


def _cash_date(row: dict[str, Any]) -> date_cls | None:
    """Payment date used by the P24 encaissement / décaissement rule."""
    for key in ("reconciliation_date", "payment_date", "fitid_date"):
        parsed = _parse_date(row.get(key))
        if parsed is not None:
            return parsed
    return _parse_date(row.get("operation_date"))


def _is_paid(row: dict[str, Any]) -> bool:
    status = str(row.get("reconciliation_status") or "").strip().upper()
    if status in PAID_RECONCILIATION_STATUSES:
        return True
    balance = _decimal(row.get("balance_due"))
    if balance is not None and balance == ZERO:
        return True
    return str(row.get("already_paid_on_invoice") or "").strip() in {"1", "true", "oui"}


def _is_sensitive_row(row: dict[str, Any]) -> bool:
    subcategory = _infer_expense_subcategory(row)
    if subcategory is not None and subcategory.value in SENSITIVE_SUBCATEGORIES:
        return True
    folded = re.sub(
        r"[^a-z0-9]+",
        " ",
        str(
            " ".join(
                str(row.get(key) or "")
                for key in ("counterparty", "supplier", "label", "invoice_url")
            )
        ).casefold(),
    )
    return any(keyword in folded for keyword in SENSITIVE_COUNTERPARTY_KEYWORDS)


def _is_sensitive_line(line: "VatLine") -> bool:
    if line.subcategory in SENSITIVE_SUBCATEGORIES:
        return True
    folded = (line.counterparty or "").casefold()
    return any(keyword in folded for keyword in SENSITIVE_COUNTERPARTY_KEYWORDS)


def _is_deferred_to_next_year(
    row: dict[str, Any], year: int, *, cash_basis: bool = False
) -> bool:
    return (
        _operation_year(row, cash_basis) == year
        and _declared_target_year(row) == year + 1
        and _month(row.get("deferred_month_override")) is not None
    )


def _is_incoming_from_previous_year(
    row: dict[str, Any], year: int, *, cash_basis: bool = False
) -> bool:
    operation_year = _operation_year(row, cash_basis)
    return (
        operation_year is not None
        and operation_year == year - 1
        and _declared_target_year(row) == year
        and _month(row.get("deferred_month_override")) is not None
    )


def _selected_rows(
    store: SqliteAccountingStore, tenant_id: str, *, cash_basis: bool = False
) -> list[dict[str, Any]]:
    """Validated lines in the CA12 scope, with duplicate VAT pieces removed."""
    selected: list[dict[str, Any]] = []
    for row in store.list_master_lines(tenant_id):
        if is_apd_self_row(row):
            continue
        if (
            str(row.get("document_type") or "").strip().upper()
            in NON_ACCOUNTABLE_DOCUMENT_TYPES
        ):
            continue
        status = str(
            row.get("raw_status") or row.get("status") or ""
        ).strip().upper()
        if status != "VALIDE":
            continue
        if _row_category(row) not in SIMULATED_CATEGORIES:
            continue
        if cash_basis and not _is_paid(row):
            continue
        selected.append(row)
    statuses, _duplicates = compute_tva_dedup(selected)
    return [
        row
        for row, status in zip(selected, statuses)
        if status is not TvaDedupStatus.DUPLICATE_EXCLUDED
    ]


def _deductible_tva(
    row: dict[str, Any], record: dict[str, Any], *, optimized: bool = False
) -> Decimal:
    """Return the deductible VAT of one expense line.

    ``optimized=False`` is the director's **prudent** scenario: representation
    expenses and restaurant VAT are excluded, hotel VAT keeps only the legally
    deductible meals / breakfast part. ``optimized=True`` includes 100 % of the
    VAT of representation, hotel and restaurant expenses (decision scenario B).
    """
    subcategory = _infer_expense_subcategory(row)
    if optimized:
        return (record["tva"] or ZERO).quantize(CENT)
    if subcategory is ExpenseSubCategory.FRAIS_REPRESENTATION_PRESIDENT:
        return ZERO
    if subcategory is ExpenseSubCategory.RESTAURATION:
        return ZERO
    explicit_deductible = _decimal(row.get("tva_deductible"))
    explicit_non_deductible = _decimal(row.get("tva_non_deductible"))
    if explicit_deductible is not None or explicit_non_deductible is not None:
        deductible, _non, _accommodation, _meals = _hotel_vat_from_row(
            row, record
        )
        return deductible.quantize(CENT)
    if subcategory is ExpenseSubCategory.HOTELLERIE:
        deductible, _non, _accommodation, _meals = _hotel_vat_from_row(
            row, record
        )
        return deductible.quantize(CENT)
    if _is_sensitive_row(row) and any(
        keyword in str(row.get("counterparty") or "").casefold()
        for keyword in SENSITIVE_COUNTERPARTY_KEYWORDS
    ):
        return ZERO
    return (record["tva"] or ZERO).quantize(CENT)


def _build_line(
    row: dict[str, Any],
    *,
    deferred_to_next_year: bool = False,
    incoming_from_previous_year: bool = False,
    cash_basis: bool = False,
    optimized: bool = False,
) -> VatLine:
    record = _summary_record(row)
    category = _row_category(row)
    subcategory = _infer_expense_subcategory(row)
    tva = (record["tva"] or ZERO).quantize(CENT)
    if category in COLLECTED_CATEGORIES:
        deductible = ZERO
        non_deductible = ZERO
        charges = ZERO
    else:
        deductible = _deductible_tva(row, record, optimized=optimized)
        non_deductible = (tva - deductible).quantize(CENT)
        charges = ((record["ht"] or ZERO) + non_deductible).quantize(CENT)
    line_id = _row_identity(row)
    operation_date = (
        _cash_date(row) if cash_basis else _parse_date(record["date"])
    )
    return VatLine(
        line_id=line_id,
        operation_date=operation_date,
        counterparty=str(record["counterparty"] or ""),
        piece=_piece_number_from_row(row)
        or _piece_reference(row, row.get("invoice_id") or row.get("line_id") or ""),
        category=category,
        subcategory=subcategory.value if subcategory is not None else "",
        ht=(record["ht"] or ZERO).quantize(CENT),
        tva=tva,
        deductible_tva=deductible,
        non_deductible_tva=non_deductible,
        ttc=(record["ttc"] or ZERO).quantize(CENT),
        charges=charges,
        payment_source=payment_source_of(row),
        counterparty_account=counterparty_account_of(row),
        fiscal_year_target=_declared_target_year(row),
        deferred_month=_month(row.get("deferred_month_override")),
        deferred_to_next_year=deferred_to_next_year,
        incoming_from_previous_year=incoming_from_previous_year,
    )


def _line_sort_key(line: VatLine) -> tuple[str, str]:
    return (
        line.operation_date.isoformat() if line.operation_date else "",
        line.line_id,
    )


def _sum_lines(
    lines: Iterable[VatLine], predicate: Any
) -> Decimal:
    return sum(
        (line.deductible_tva for line in lines if predicate(line)), ZERO
    ).quantize(CENT)


def _build_acomptes(
    next_year: int, tva_due: Decimal
) -> tuple[VatAcompte, ...]:
    if tva_due <= ZERO:
        return tuple(
            VatAcompte(
                period=period,
                rate=rate,
                amount=ZERO,
                due_date=date_cls(next_year, month, day),
            )
            for period, rate, month, day in ACOMPTE_RATES
        )
    first_amount = (tva_due * ACOMPTE_RATES[0][1]).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
    amounts = [first_amount, (tva_due - first_amount).quantize(CENT)]
    return tuple(
        VatAcompte(
            period=period,
            rate=rate,
            amount=amount,
            due_date=date_cls(next_year, month, day),
        )
        for (period, rate, month, day), amount in zip(ACOMPTE_RATES, amounts)
    )


def _build_scenario(
    code: str,
    label: str,
    year: int,
    lines: Iterable[VatLine],
) -> VatScenario:
    ordered = tuple(sorted(lines, key=_line_sort_key))
    collected = sum(
        (line.tva for line in ordered if line.category in COLLECTED_CATEGORIES),
        ZERO,
    ).quantize(CENT)
    scenario = VatScenario(
        code=code,
        label=label,
        year=year,
        lines=ordered,
        collected_tva=collected,
        deductible_achats_marchandises=_sum_lines(
            ordered, lambda line: line.category in DEDUCTIBLE_GOODS_CATEGORIES
        ),
        deductible_transports=_sum_lines(
            ordered,
            lambda line: line.category
            == DocumentCategory.TRANSPORTS_LOGISTIQUE.value,
        ),
        deductible_frais_generaux=_sum_lines(
            ordered,
            lambda line: line.category == DocumentCategory.FRAIS_GENERAUX.value,
        ),
        deductible_immobilisations=_sum_lines(
            ordered, lambda line: line.category in DEDUCTIBLE_ASSET_CATEGORIES
        ),
        non_deductible_tva=sum(
            (line.non_deductible_tva for line in ordered), ZERO
        ).quantize(CENT),
        charges=sum((line.charges for line in ordered), ZERO).quantize(CENT),
        sensitive_tva=sum(
            (line.tva for line in ordered if _is_sensitive_line(line)), ZERO
        ).quantize(CENT),
        deductible_sensitive_tva=sum(
            (
                line.deductible_tva
                for line in ordered
                if _is_sensitive_line(line)
            ),
            Decimal("0.00"),
        ).quantize(CENT),
    )
    return replace(
        scenario, acomptes=_build_acomptes(year + 1, scenario.tva_due)
    )


class VatSimulationService:
    """Compute and export the APD CA12 scenarios."""

    def __init__(
        self,
        store: SqliteAccountingStore | None = None,
        *,
        export_dir: str | os.PathLike[str] | None = None,
        cash_basis: bool = False,
    ) -> None:
        self._store = store
        self._export_dir = (
            Path(export_dir) if export_dir is not None else DEFAULT_EXPORT_DIR
        )
        self._cash_basis = bool(cash_basis)

    @property
    def store(self) -> SqliteAccountingStore:
        return self._store if self._store is not None else get_shared_sqlite_store()

    @property
    def export_dir(self) -> Path:
        return self._export_dir

    def simulate(self, tenant_id: str, year: int) -> Ca12SimulationReport:
        try:
            tenant = validate_tenant_id(tenant_id)
        except AccountingStoreError:
            raise VatSimulationError(
                "INVALID_TENANT_ID", repr(tenant_id)
            ) from None
        if (
            not isinstance(year, int)
            or isinstance(year, bool)
            or not 1900 <= year <= 2200
        ):
            raise VatSimulationError("INVALID_YEAR", repr(year))
        rows = _selected_rows(
            self.store, tenant, cash_basis=self._cash_basis
        )
        scenario_a_rows = [
            row
            for row in rows
            if _operation_year(row, self._cash_basis) == year
        ]
        deferred_rows = [
            row
            for row in scenario_a_rows
            if _is_deferred_to_next_year(row, year, cash_basis=self._cash_basis)
        ]
        incoming_rows = [
            row
            for row in rows
            if _is_incoming_from_previous_year(
                row, year, cash_basis=self._cash_basis
            )
        ]
        scenario_b_rows = [
            row
            for row in scenario_a_rows
            if not _is_deferred_to_next_year(
                row, year, cash_basis=self._cash_basis
            )
        ] + incoming_rows

        scenario_a_lines = tuple(
            _build_line(
                row,
                deferred_to_next_year=_is_deferred_to_next_year(
                    row, year, cash_basis=self._cash_basis
                ),
                cash_basis=self._cash_basis,
                optimized=False,
            )
            for row in scenario_a_rows
        )
        scenario_b_lines = tuple(
            _build_line(
                row,
                cash_basis=self._cash_basis,
                optimized=True,
            )
            for row in scenario_b_rows
        )
        deferred_lines = tuple(
            _build_line(
                row,
                deferred_to_next_year=True,
                cash_basis=self._cash_basis,
                optimized=False,
            )
            for row in deferred_rows
        )
        incoming_lines = tuple(
            _build_line(
                row,
                incoming_from_previous_year=True,
                cash_basis=self._cash_basis,
                optimized=True,
            )
            for row in incoming_rows
        )
        return Ca12SimulationReport(
            tenant_id=tenant,
            year=year,
            scenario_a=_build_scenario(
                "A",
                "Prudent - exercice d'origine, dépenses sensibles exclues",
                year,
                scenario_a_lines,
            ),
            scenario_b=_build_scenario(
                "B",
                "Optimisé - reports janvier/février + TVA dépenses sensibles",
                year,
                scenario_b_lines,
            ),
            deferred_to_next_year=tuple(
                sorted(deferred_lines, key=_line_sort_key)
            ),
            incoming_from_previous_year=tuple(
                sorted(incoming_lines, key=_line_sort_key)
            ),
            generated_at=datetime.now(timezone.utc),
            cash_basis=self._cash_basis,
        )

    def export(self, tenant_id: str, year: int) -> Path:
        report = self.simulate(tenant_id, year)
        return self.write_report(report)

    def write_report(self, report: Ca12SimulationReport) -> Path:
        target = ca12_export_path(
            report.tenant_id, report.year, self._export_dir
        )
        _write_ca12_workbook(report, target)
        try:
            from services.accounting_spreadsheet_export_service import (
                write_vat_3way_control_sheet,
            )

            workbook = load_workbook(target)
            try:
                write_vat_3way_control_sheet(
                    workbook, self._store, report.tenant_id
                )
                _atomic_save_workbook(workbook, target)
            finally:
                workbook.close()
        except Exception:  # noqa: BLE001 - control sheet never blocks CA12
            pass
        return target


def ca12_export_path(
    tenant_id: str,
    year: int,
    export_dir: str | os.PathLike[str] | None = None,
) -> Path:
    try:
        tenant = validate_tenant_id(tenant_id)
    except AccountingStoreError:
        raise VatSimulationError(
            "INVALID_TENANT_ID", repr(tenant_id)
        ) from None
    if (
        not isinstance(year, int)
        or isinstance(year, bool)
        or not 1900 <= year <= 2200
    ):
        raise VatSimulationError("INVALID_YEAR", repr(year))
    base = Path(export_dir) if export_dir is not None else DEFAULT_EXPORT_DIR
    return base / tenant / CA12_SIMULATION_FILENAME.format(year=year)


def simulate_ca12(
    tenant_id: str,
    year: int,
    *,
    store: SqliteAccountingStore | None = None,
    export_dir: str | os.PathLike[str] | None = None,
    cash_basis: bool = False,
) -> Ca12SimulationReport:
    """Convenience wrapper around :class:`VatSimulationService`."""
    return VatSimulationService(
        store, export_dir=export_dir, cash_basis=cash_basis
    ).simulate(tenant_id, year)


def export_ca12_simulation(
    tenant_id: str,
    year: int,
    *,
    store: SqliteAccountingStore | None = None,
    export_dir: str | os.PathLike[str] | None = None,
    cash_basis: bool = False,
) -> Path:
    """Compute the simulation and write its Excel workbook."""
    return VatSimulationService(
        store, export_dir=export_dir, cash_basis=cash_basis
    ).export(tenant_id, year)


def _safe_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def _cell_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.date()
    return value


def _style_header(sheet: Any, headers: Sequence[str]) -> None:
    fill = PatternFill("solid", fgColor="1F4E78")
    font = Font(bold=True, color="FFFFFF", size=11)
    thin = Side(style="thin", color="B7C4D4")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for column, header in enumerate(headers, start=1):
        cell = sheet.cell(row=1, column=column, value=header)
        cell.font = font
        cell.fill = fill
        cell.border = border
        cell.alignment = Alignment(horizontal="center", vertical="center")
    sheet.row_dimensions[1].height = 28
    sheet.freeze_panes = "A2"


def _style_body(sheet: Any, rows: int, columns: int) -> None:
    thin = Side(style="thin", color="B7C4D4")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    band = PatternFill("solid", fgColor="F2F7FB")
    for row in range(2, rows + 2):
        for column in range(1, columns + 1):
            cell = sheet.cell(row=row, column=column)
            cell.border = border
            if row % 2 == 0:
                cell.fill = band
            if isinstance(cell.value, float):
                cell.number_format = "#,##0.00"
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif isinstance(cell.value, date_cls):
                cell.number_format = "DD/MM/YYYY"
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(vertical="center", wrap_text=True)


def _autosize(sheet: Any, headers: Sequence[str]) -> None:
    for column, header in enumerate(headers, start=1):
        longest = len(str(header))
        for row in range(2, sheet.max_row + 1):
            value = sheet.cell(row=row, column=column).value
            longest = max(longest, len(str(value or "")))
        sheet.column_dimensions[get_column_letter(column)].width = max(
            12, min(longest + 2, _TEXT_MAX_LENGTH)
        )


def _write_table(
    sheet: Any,
    headers: Sequence[str],
    rows: Iterable[Sequence[Any]],
) -> None:
    _style_header(sheet, headers)
    row_count = 0
    for row_count, values in enumerate(rows, start=2):
        for column, value in enumerate(values, start=1):
            sheet.cell(
                row=row_count,
                column=column,
                value=_cell_value(_safe_text(value)),
            )
    _style_body(sheet, max(row_count - 1, 0), len(headers))
    _autosize(sheet, headers)
    if row_count >= 1:
        sheet.auto_filter.ref = (
            f"A1:{get_column_letter(len(headers))}{row_count}"
        )


def _detail_values(line: VatLine) -> tuple[Any, ...]:
    return (
        line.operation_date,
        line.counterparty,
        line.piece,
        line.category,
        line.subcategory,
        line.ht,
        line.tva,
        line.deductible_tva,
        line.non_deductible_tva,
        line.ttc,
        line.payment_source,
        line.counterparty_account,
        line.fiscal_year_target or "",
        line.deferred_month or "",
        line.report_label,
    )


def _comparison_rows(report: Ca12SimulationReport) -> list[tuple[Any, ...]]:
    a = report.scenario_a
    b = report.scenario_b
    rows: list[tuple[Any, ...]] = []

    def add(
        label: str, value_a: Any, value_b: Any, *, money: bool = True
    ) -> None:
        if money:
            delta = (Decimal(str(value_b)) - Decimal(str(value_a))).quantize(CENT)
            rows.append((label, value_a, value_b, delta))
        else:
            rows.append((label, value_a, value_b, value_b - value_a))

    add("TVA collectee", a.collected_tva, b.collected_tva)
    add(
        "TVA deductible - Achats marchandises",
        a.deductible_achats_marchandises,
        b.deductible_achats_marchandises,
    )
    add(
        "TVA deductible - Transports / logistique",
        a.deductible_transports,
        b.deductible_transports,
    )
    add(
        "TVA deductible - Frais generaux",
        a.deductible_frais_generaux,
        b.deductible_frais_generaux,
    )
    add(
        "TVA deductible - Biens et services",
        a.deductible_goods_services,
        b.deductible_goods_services,
    )
    add(
        "TVA deductible - Immobilisations",
        a.deductible_immobilisations,
        b.deductible_immobilisations,
    )
    add(
        "TVA deductible - Depenses sensibles (representation / hotel / restaurant)",
        a.deductible_sensitive_tva,
        b.deductible_sensitive_tva,
    )
    add(
        "TVA totale des depenses sensibles",
        a.sensitive_tva,
        b.sensitive_tva,
    )
    add(
        "TVA deductible - Total",
        a.deductible_total,
        b.deductible_total,
    )
    add("TVA nette", a.net_tva, b.net_tva)
    add("TVA due", a.tva_due, b.tva_due)
    add("Credit de TVA", a.credit_tva, b.credit_tva)
    add("Pieces", a.pieces, b.pieces, money=False)
    add(
        "TVA non deductible (reintegree au resultat)",
        a.non_deductible_tva,
        b.non_deductible_tva,
    )
    add(
        "Charges (base resultat imposable)",
        a.charges,
        b.charges,
    )
    for index in range(max(len(a.acomptes), len(b.acomptes))):
        period = (
            a.acomptes[index].period
            if index < len(a.acomptes)
            else b.acomptes[index].period
        )
        add(
            f"Acompte {period} {report.next_year}",
            a.acomptes[index].amount if index < len(a.acomptes) else ZERO,
            b.acomptes[index].amount if index < len(b.acomptes) else ZERO,
        )
    add(
        f"Total acomptes {report.next_year}",
        a.total_acomptes,
        b.total_acomptes,
    )
    rows.append(
        (
            "Impact resultat imposable (A - B)",
            report.taxable_result_impact,
            "",
            "",
        )
    )
    return rows


def _summary_rows(report: Ca12SimulationReport) -> list[tuple[Any, ...]]:
    a = report.scenario_a
    b = report.scenario_b
    return [
        ("Tenant", report.tenant_id, ""),
        ("Exercice", report.year, ""),
        ("Genere le", report.generated_at.isoformat(), ""),
        (
            "Regime TVA",
            "ENCAISSEMENTS / DECAISSEMENTS REELS"
            if report.cash_basis
            else "FACTURATION",
            "",
        ),
        ("Scenario A", a.label, ""),
        ("Scenario B", b.label, ""),
        ("TVA nette A", a.net_tva, ""),
        ("TVA nette B", b.net_tva, ""),
        ("TVA due A", a.tva_due, ""),
        ("TVA due B", b.tva_due, ""),
        ("Credit TVA A", a.credit_tva, ""),
        ("Credit TVA B", b.credit_tva, ""),
        (
            "TVA deductible depenses sensibles (A prudent)",
            a.deductible_sensitive_tva,
            "",
        ),
        (
            "TVA deductible depenses sensibles (B optimise)",
            b.deductible_sensitive_tva,
            "",
        ),
        ("Impact resultat imposable A - B", report.taxable_result_impact, ""),
        ("Total acomptes A", a.total_acomptes, ""),
        ("Total acomptes B", b.total_acomptes, ""),
        ("Pieces reportees N+1", len(report.deferred_to_next_year), ""),
        ("Pieces rapportees de N-1", len(report.incoming_from_previous_year), ""),
    ]


def _write_ca12_workbook(
    report: Ca12SimulationReport, output_path: Path
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    comparison = workbook.active
    comparison.title = CA12_OUTPUT_SHEET
    _write_table(
        comparison, _COMPARISON_HEADERS, _comparison_rows(report)
    )

    summary = workbook.create_sheet(CA12_SUMMARY_SHEET)
    _write_table(summary, ("Rubrique", "Valeur", "Complement"), _summary_rows(report))

    detail_a = workbook.create_sheet(CA12_DETAIL_A_SHEET)
    _write_table(
        detail_a,
        _DETAIL_HEADERS,
        (_detail_values(line) for line in report.scenario_a.lines),
    )

    detail_b = workbook.create_sheet(CA12_DETAIL_B_SHEET)
    _write_table(
        detail_b,
        _DETAIL_HEADERS,
        (_detail_values(line) for line in report.scenario_b.lines),
    )

    deferred = workbook.create_sheet(CA12_DEFERRED_SHEET)
    _write_table(
        deferred,
        _DETAIL_HEADERS,
        (_detail_values(line) for line in report.deferred_to_next_year),
    )

    incoming = workbook.create_sheet(CA12_INCOMING_SHEET)
    _write_table(
        incoming,
        _DETAIL_HEADERS,
        (_detail_values(line) for line in report.incoming_from_previous_year),
    )

    workbook.properties.creator = "DSH-Core"
    workbook.properties.title = (
        f"SIMULATION_CA12_{report.tenant_id}_{report.year}"
    )
    _atomic_save_workbook(workbook, output_path)


def report_display_name(tenant_id: str) -> str:
    if tenant_id.upper() == APD_PROFILE.tenant_id:
        return APD_PROFILE.name
    return tenant_id


def report_payload(report: Ca12SimulationReport) -> dict[str, Any]:
    """Plain JSON-serialisable view used by the CLI ``--json`` option."""
    def scenario_payload(scenario: VatScenario) -> dict[str, Any]:
        return {
            "code": scenario.code,
            "label": scenario.label,
            "pieces": scenario.pieces,
            "collected_tva": str(scenario.collected_tva),
            "deductible_achats_marchandises": str(
                scenario.deductible_achats_marchandises
            ),
            "deductible_transports": str(scenario.deductible_transports),
            "deductible_frais_generaux": str(
                scenario.deductible_frais_generaux
            ),
            "deductible_immobilisations": str(
                scenario.deductible_immobilisations
            ),
            "deductible_goods_services": str(
                scenario.deductible_goods_services
            ),
            "deductible_total": str(scenario.deductible_total),
            "non_deductible_tva": str(scenario.non_deductible_tva),
            "charges": str(scenario.charges),
            "net_tva": str(scenario.net_tva),
            "tva_due": str(scenario.tva_due),
            "credit_tva": str(scenario.credit_tva),
            "acomptes": [
                {
                    "period": item.period,
                    "rate": str(item.rate),
                    "amount": str(item.amount),
                    "due_date": item.due_date.isoformat(),
                }
                for item in scenario.acomptes
            ],
            "total_acomptes": str(scenario.total_acomptes),
        }

    return {
        "tenant": report.tenant_id,
        "year": report.year,
        "cash_basis": report.cash_basis,
        "scenario_a": scenario_payload(report.scenario_a),
        "scenario_b": scenario_payload(report.scenario_b),
        "vat_due_delta": str(report.vat_due_delta),
        "credit_delta": str(report.credit_delta),
        "taxable_result_impact": str(report.taxable_result_impact),
        "deferred_pieces": len(report.deferred_to_next_year),
        "deferred_charges": str(report.deferred_charges),
        "incoming_pieces": len(report.incoming_from_previous_year),
        "incoming_charges": str(report.incoming_charges),
        "generated_at": report.generated_at.isoformat(),
    }


__all__ = [
    "ACOMPTE_RATES",
    "CA12_DETAIL_A_SHEET",
    "CA12_DETAIL_B_SHEET",
    "CA12_DEFERRED_SHEET",
    "CA12_INCOMING_SHEET",
    "CA12_OUTPUT_SHEET",
    "CA12_SIMULATION_FILENAME",
    "CA12_SUMMARY_SHEET",
    "Ca12SimulationReport",
    "VatAcompte",
    "VatLine",
    "VatScenario",
    "VatSimulationError",
    "VatSimulationService",
    "ca12_export_path",
    "export_ca12_simulation",
    "report_display_name",
    "report_payload",
    "simulate_ca12",
]
