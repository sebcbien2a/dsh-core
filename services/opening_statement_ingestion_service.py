"""P26 - Client opening-statement ingestion (historical arrears truth).

The director can drop a ``RELEVE_OUVERTURE_*.xlsx`` / ``.csv`` (or a vision
table) with the columns::

    N° Facture | Date/Mois | Montant TTC | État restant dû | Réglé |
    Échéance | Date de paiement

The service initialises the customer arrears without rewriting previous
exercises: the original invoice date is preserved, paid invoices are lettered
directly against the matching OFX virement (100 %), and prior-exercise settled
invoices are flagged ``SOLDEE_EXERCICE_ANTERIEUR_2025`` so they never inflate
the current balance.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from openpyxl import Workbook, load_workbook

from schemas.folder_state_schema import DocumentCategory
from services.accounting_memory_learning_service import (
    AccountingMemoryLearningService,
    normalize_counterparty_name,
)
from services.sqlite_accounting_store import SqliteAccountingStore
from services.tier_reference_service import TierReferenceService

OPENING_STATEMENT_COLUMNS: tuple[str, ...] = (
    "N° Facture",
    "Date/Mois",
    "Montant TTC",
    "État restant dû",
    "Réglé",
    "Échéance",
    "Date de paiement",
)
OPENING_STATEMENT_OPTIONAL_COLUMNS: tuple[str, ...] = (
    "Statut",
    "Référence règlement",
)
OPENING_STATEMENT_STATUS_PAID = "PAYEE_RAPPROCHEE"
OPENING_STATEMENT_STATUS_DUE = "EN_ATTENTE_REGLEMENT"
OPENING_STATEMENT_STATUS_PRIOR = "SOLDEE_EXERCICE_ANTERIEUR_2025"
OPENING_STATEMENT_FILENAME_PREFIX = "RELEVE_OUVERTURE_"
_CENT = Decimal("0.01")
_VAT_RATE = Decimal("0.20")
_PAID_WORDS = frozenset(
    {"OUI", "YES", "TRUE", "1", "X", "REGLE", "RÉGLÉ", "PAYE", "PAYÉ", "SOLDEE", "SOLDÉE"}
)
_DUE_WORDS = frozenset({"NON", "NO", "FALSE", "0", "DU", "DÛ", "ATTENTE"})


class OpeningStatementError(ValueError):
    """Invalid opening statement; ``code`` is stable for tests and logs."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class OpeningStatementRow:
    invoice_id: str
    date: str
    amount_ttc: Decimal
    balance_due: Decimal
    paid: bool
    due_date: str = ""
    payment_date: str = ""
    status: str = ""
    payment_reference: str = ""


@dataclass(frozen=True)
class OpeningStatementReport:
    path: Path | None
    client: str
    rows: int
    created: int
    updated: int
    purged: int
    balance_due: Decimal


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _decimal(value: Any) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip().replace("€", "").replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", ".")
    try:
        return Decimal(text).quantize(_CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def _parse_date(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date_cls):
        return value.isoformat()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except ValueError:
            continue
    month = re.match(r"^(\d{4})[-/](\d{1,2})$", text)
    if month:
        year, month_number = int(month.group(1)), int(month.group(2))
        if 1 <= month_number <= 12:
            if month_number == 12:
                return date_cls(year, 12, 31).isoformat()
            return (date_cls(year, month_number + 1, 1) - date_cls.resolution).isoformat()
    month = re.match(r"^(\d{1,2})[-/](\d{4})$", text)
    if month:
        month_number, year = int(month.group(1)), int(month.group(2))
        if 1 <= month_number <= 12:
            if month_number == 12:
                return date_cls(year, 12, 31).isoformat()
            return (date_cls(year, month_number + 1, 1) - date_cls.resolution).isoformat()
    return text[:10]


def _is_paid(value: Any, balance_due: Decimal) -> bool:
    text = _text(value).upper()
    if text in _PAID_WORDS:
        return True
    if text in _DUE_WORDS:
        return False
    amount = _decimal(value)
    if amount is not None:
        return amount > 0
    return balance_due <= 0


def _row_status(value: Any, paid: bool) -> str:
    text = _text(value).upper()
    if "SOLDEE_EXERCICE_ANTERIEUR" in text:
        return OPENING_STATEMENT_STATUS_PRIOR
    if text in {OPENING_STATEMENT_STATUS_PAID, OPENING_STATEMENT_STATUS_DUE}:
        return text
    return OPENING_STATEMENT_STATUS_PAID if paid else OPENING_STATEMENT_STATUS_DUE


def _header_index(headers: Iterable[Any]) -> dict[str, int]:
    index: dict[str, int] = {}
    for position, header in enumerate(headers):
        text = _text(header)
        if text:
            index[text] = position
    return index


def _cell(row: tuple[Any, ...], index: dict[str, int], name: str) -> Any:
    position = index.get(name)
    if position is None or position >= len(row):
        return None
    return row[position]


def _rows_from_matrix(matrix: list[tuple[Any, ...]]) -> list[OpeningStatementRow]:
    if not matrix:
        return []
    index = _header_index(matrix[0])
    if "N° Facture" not in index:
        raise OpeningStatementError("MISSING_INVOICE_COLUMN")
    rows: list[OpeningStatementRow] = []
    for raw in matrix[1:]:
        invoice_id = _text(_cell(raw, index, "N° Facture"))
        if not invoice_id:
            continue
        amount = _decimal(_cell(raw, index, "Montant TTC")) or Decimal("0.00")
        balance = _decimal(_cell(raw, index, "État restant dû"))
        if balance is None:
            balance = Decimal("0.00") if _is_paid(
                _cell(raw, index, "Réglé"), amount
            ) else amount
        paid = _is_paid(_cell(raw, index, "Réglé"), balance)
        status = _row_status(_cell(raw, index, "Statut"), paid)
        if status == OPENING_STATEMENT_STATUS_PRIOR:
            paid = True
            balance = Decimal("0.00")
        rows.append(
            OpeningStatementRow(
                invoice_id=invoice_id,
                date=_parse_date(_cell(raw, index, "Date/Mois")),
                amount_ttc=amount,
                balance_due=balance,
                paid=paid,
                due_date=_parse_date(_cell(raw, index, "Échéance")),
                payment_date=_parse_date(_cell(raw, index, "Date de paiement")),
                status=status,
                payment_reference=_text(
                    _cell(raw, index, "Référence règlement")
                ),
            )
        )
    return rows


def read_opening_statement(path: str | Path) -> list[OpeningStatementRow]:
    """Read an opening statement from ``.xlsx`` or ``.csv``."""
    source = Path(path)
    if not source.is_file():
        raise OpeningStatementError("FILE_NOT_FOUND", str(source))
    if source.suffix.lower() in {".xlsx", ".xlsm"}:
        workbook = load_workbook(source, data_only=True)
        try:
            sheet = workbook.active
            matrix = [tuple(row) for row in sheet.iter_rows(values_only=True)]
        finally:
            workbook.close()
        return _rows_from_matrix(matrix)
    if source.suffix.lower() in {".csv", ".txt"}:
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            sample = handle.read(4096)
            handle.seek(0)
            delimiter = ";" if sample.count(";") >= sample.count(",") else ","
            reader = csv.reader(handle, delimiter=delimiter)
            matrix = [tuple(row) for row in reader]
        return _rows_from_matrix(matrix)
    raise OpeningStatementError("UNSUPPORTED_FORMAT", source.suffix)


def write_opening_statement(
    path: str | Path, rows: Iterable[OpeningStatementRow]
) -> Path:
    """Write a canonical ``RELEVE_OUVERTURE_*.xlsx`` workbook."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "RELEVE_OUVERTURE"
    headers = (*OPENING_STATEMENT_COLUMNS, *OPENING_STATEMENT_OPTIONAL_COLUMNS)
    for column, header in enumerate(headers, start=1):
        sheet.cell(row=1, column=column, value=header)
    for row_index, row in enumerate(rows, start=2):
        values = (
            row.invoice_id,
            row.date,
            float(row.amount_ttc),
            float(row.balance_due),
            "OUI" if row.paid else "NON",
            row.due_date,
            row.payment_date,
            row.status,
            row.payment_reference,
        )
        for column, value in enumerate(values, start=1):
            sheet.cell(row=row_index, column=column, value=value)
    workbook.save(target)
    workbook.close()
    return target


class OpeningStatementIngestionService:
    """Apply an opening statement to the SQLite customer ledger."""

    def __init__(
        self,
        store: SqliteAccountingStore,
        tenant_id: str = "APD",
        *,
        client: str = "CARROSSERIE_YUCEL",
        memory: AccountingMemoryLearningService | None = None,
    ) -> None:
        self._store = store
        self._tenant_id = tenant_id
        self._client = client
        self._memory = memory or AccountingMemoryLearningService()
        self._tiers = TierReferenceService(memory=self._memory)
        self._tiers.bootstrap(write_missing=False)

    @property
    def client(self) -> str:
        return self._client

    def _canonical(self, value: Any) -> str:
        resolved = self._tiers.canonical_name(value)
        return normalize_counterparty_name(resolved or value)

    def _client_lines(self) -> list[dict[str, Any]]:
        lines: list[dict[str, Any]] = []
        target = self._canonical(self._client)
        for row in self._store.list_master_lines(self._tenant_id):
            if str(row.get("category") or "") != DocumentCategory.VENTES_CLIENTS.value:
                continue
            name = str(row.get("counterparty") or "")
            if self._canonical(name) == target:
                lines.append(row)
        return lines

    def purge_remittance_lettering(
        self, remittance_references: Iterable[str]
    ) -> int:
        """Remove false ``REMISE <ref>`` lettering from the client's invoices."""
        needles = {
            _text(reference).upper()
            for reference in remittance_references
            if _text(reference)
        }
        if not needles:
            return 0
        purged = 0
        for row in self._client_lines():
            reference = _text(row.get("reconciliation_reference")).upper()
            if not reference:
                continue
            if not any(needle in reference for needle in needles):
                continue
            line = dict(row)
            line["reconciliation_status"] = OPENING_STATEMENT_STATUS_DUE
            line["reconciliation_date"] = None
            line["reconciliation_reference"] = None
            line["reconciliation_amount"] = None
            line["balance_due"] = str(
                (_decimal(row.get("amount_ttc")) or Decimal("0.00")).quantize(_CENT)
            )
            self._store.force_upsert_document_line(
                self._tenant_id, str(row.get("line_id") or ""), line
            )
            purged += 1
        return purged

    def _find_virement(
        self, amount: Decimal, payment_date: str
    ) -> dict[str, Any] | None:
        candidates: list[dict[str, Any]] = []
        for row in self._store.list_master_lines(self._tenant_id):
            if str(row.get("category") or "") != DocumentCategory.VENTES_CLIENTS.value:
                continue
            document_type = str(row.get("document_type") or "").upper()
            if document_type not in {"OFX", "QFX"}:
                continue
            credit = _decimal(row.get("credit_amount"))
            if credit is None or credit != amount:
                continue
            name = str(row.get("counterparty") or "")
            if self._canonical(name) != self._canonical(self._client):
                continue
            candidates.append(row)
        if not candidates:
            return None
        if payment_date:
            for row in candidates:
                if _text(row.get("operation_date"))[:10] == payment_date[:10]:
                    return row
        return candidates[0]

    def _existing_line(self, invoice_id: str) -> dict[str, Any] | None:
        for row in self._client_lines():
            if _text(row.get("invoice_id")).upper() == invoice_id.upper():
                return row
        return None

    def apply_rows(
        self, rows: Iterable[OpeningStatementRow]
    ) -> OpeningStatementReport:
        created = 0
        updated = 0
        balance = Decimal("0.00")
        for row in rows:
            existing = self._existing_line(row.invoice_id)
            line = dict(existing or {})
            line_id = str(line.get("line_id") or f"SALE-{row.invoice_id}")
            amount = row.amount_ttc.quantize(_CENT)
            line.update(
                {
                    "operation_date": row.date or line.get("operation_date"),
                    "label": f"Facture {row.invoice_id} {self._client}",
                    "counterparty": self._client,
                    "invoice_id": row.invoice_id,
                    "category": DocumentCategory.VENTES_CLIENTS.value,
                    "document_type": "CUSTOMER_INVOICE",
                    "amount_ttc": str(amount),
                    "status": "VALIDE",
                }
            )
            if not line.get("amount_ht") or not line.get("amount_tva"):
                ht = (amount / (Decimal("1") + _VAT_RATE)).quantize(
                    _CENT, rounding=ROUND_HALF_UP
                )
                line["amount_ht"] = str(ht)
                line["amount_tva"] = str((amount - ht).quantize(_CENT))
            if row.status == OPENING_STATEMENT_STATUS_PRIOR:
                line.update(
                    {
                        "reconciliation_status": OPENING_STATEMENT_STATUS_PRIOR,
                        "reconciliation_date": "2025-12-31",
                        "reconciliation_reference": "SOLDEE_EXERCICE_ANTERIEUR_2025",
                        "reconciliation_amount": str(amount),
                        "balance_due": "0.00",
                    }
                )
            elif row.paid:
                virement = self._find_virement(amount, row.payment_date)
                if virement is not None:
                    reference = _text(
                        virement.get("fitid")
                        or virement.get("payment_reference")
                        or virement.get("line_id")
                    )
                    payment_date = _text(virement.get("operation_date"))
                else:
                    reference = row.payment_reference or (
                        f"VIREMENT {row.payment_date}" if row.payment_date else ""
                    )
                    payment_date = row.payment_date
                line.update(
                    {
                        "reconciliation_status": OPENING_STATEMENT_STATUS_PAID,
                        "reconciliation_date": payment_date or None,
                        "reconciliation_reference": reference or None,
                        "reconciliation_amount": str(amount),
                        "balance_due": "0.00",
                    }
                )
            else:
                line.update(
                    {
                        "reconciliation_status": OPENING_STATEMENT_STATUS_DUE,
                        "reconciliation_date": None,
                        "reconciliation_reference": None,
                        "reconciliation_amount": None,
                        "balance_due": str(row.balance_due.quantize(_CENT)),
                    }
                )
                balance += row.balance_due.quantize(_CENT)
            if existing is None:
                created += 1
            else:
                updated += 1
            self._store.force_upsert_document_line(
                self._tenant_id, line_id, line
            )
        return OpeningStatementReport(
            path=None,
            client=self._client,
            rows=created + updated,
            created=created,
            updated=updated,
            purged=0,
            balance_due=balance.quantize(_CENT),
        )

    def ingest(
        self,
        path: str | Path,
        *,
        purge_remittances: Iterable[str] = (),
    ) -> OpeningStatementReport:
        rows = read_opening_statement(path)
        purged = self.purge_remittance_lettering(purge_remittances)
        report = self.apply_rows(rows)
        return OpeningStatementReport(
            path=Path(path),
            client=report.client,
            rows=report.rows,
            created=report.created,
            updated=report.updated,
            purged=purged,
            balance_due=report.balance_due,
        )


__all__ = [
    "OPENING_STATEMENT_COLUMNS",
    "OPENING_STATEMENT_FILENAME_PREFIX",
    "OPENING_STATEMENT_OPTIONAL_COLUMNS",
    "OPENING_STATEMENT_STATUS_DUE",
    "OPENING_STATEMENT_STATUS_PAID",
    "OPENING_STATEMENT_STATUS_PRIOR",
    "OpeningStatementError",
    "OpeningStatementIngestionService",
    "OpeningStatementReport",
    "OpeningStatementRow",
    "read_opening_statement",
    "write_opening_statement",
]
