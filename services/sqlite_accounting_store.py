"""Durable multi-tenant accounting store backed by SQLite (P13.1).

One database file holds every tenant; each row carries an indexed ``tenant_id``
and every query is filtered on it. The class is structurally compatible with the
``FecEntryStore`` (accounting export) and ``ArbitrationStore`` (HITL
reconciliation) protocols.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from schemas.folder_state_schema import (
    DocumentCategory,
    counterparty_account_for_payment_source,
    normalize_payment_source,
)
from schemas.payment_method_schema import (
    PaymentMethodError,
    normalize_counterparty_name,
    normalize_expected_payment_method,
    payment_method_matches,
)
from services.fec_export_service import FecEntry
from services.rule_registry_service import ObsidianCandidateRuleWriter

STORE_BACKEND_ENV = "DSH_ACCOUNTING_STORE"
DB_PATH_ENV = "DSH_ACCOUNTING_DB"
DEFAULT_DB_PATH = Path("data") / "accounting" / "accounting.sqlite3"

_TENANT_ID = re.compile(r"[A-Za-z0-9._:-]{1,64}")
_PENDING = "PENDING"

_FEC_COLUMNS: tuple[str, ...] = FecEntry.COLUMNS
_DATE_COLUMNS = frozenset({"EcritureDate", "PieceDate", "DateLet", "ValidDate"})
_AMOUNT_COLUMNS = frozenset({"Debit", "Credit", "Montantdevise"})

_LINE_FIELDS = (
    "operation_date",
    "label",
    "memo",
    "debit_amount",
    "credit_amount",
    "currency",
    "counterparty",
    "invoice_id",
    "invoice_url",
    "category",
    "amount_ht",
    "amount_tva",
    "amount_ttc",
    "document_type",
    "expense_subcategory",
    "invite_restaurant",
    "lieu_restaurant",
    "fuel_station",
    "vehicle",
    "transport_nature",
    "transport_route",
    "hotel_place",
    "ht_accommodation",
    "ht_breakfast_meals",
    "tva_deductible",
    "tva_non_deductible",
    "is_classified_marker",
    "tva_dedup_status",
    "fiscal_year_mode",
    "fiscal_year_target",
    "deferred_month_override",
    "payment_source",
    "counterparty_account",
    "payment_method",
    "payment_reference",
    "purchase_date",
    "due_date",
    "fitid",
    "remittance_reference",
    "client_siret",
    "client_vat_number",
    "payment_terms",
    "reconciliation_status",
    "reconciliation_date",
    "reconciliation_reference",
    "reconciliation_amount",
    "source_status",
    "balance_due",
    "company_name",
    "piece_pdf",
    "already_paid_on_invoice",
    "origin_document_number",
    "origin_document_date",
    "is_refacturation_pair",
    "report",
)
_LINE_MIGRATION_COLUMNS = (
    ("memo", "TEXT"),
    ("category", "TEXT"),
    ("amount_ht", "TEXT"),
    ("amount_tva", "TEXT"),
    ("amount_ttc", "TEXT"),
    ("document_type", "TEXT"),
    ("expense_subcategory", "TEXT"),
    ("invite_restaurant", "TEXT"),
    ("lieu_restaurant", "TEXT"),
    ("fuel_station", "TEXT"),
    ("vehicle", "TEXT"),
    ("transport_nature", "TEXT"),
    ("transport_route", "TEXT"),
    ("hotel_place", "TEXT"),
    ("ht_accommodation", "TEXT"),
    ("ht_breakfast_meals", "TEXT"),
    ("tva_deductible", "TEXT"),
    ("tva_non_deductible", "TEXT"),
    ("is_classified_marker", "TEXT"),
    ("tva_dedup_status", "TEXT"),
    ("fiscal_year_mode", "TEXT"),
    ("fiscal_year_target", "TEXT"),
    ("deferred_month_override", "TEXT"),
    ("payment_source", "TEXT"),
    ("counterparty_account", "TEXT"),
    ("payment_method", "TEXT"),
    ("payment_reference", "TEXT"),
    ("purchase_date", "TEXT"),
    ("due_date", "TEXT"),
    ("fitid", "TEXT"),
    ("remittance_reference", "TEXT"),
    ("client_siret", "TEXT"),
    ("client_vat_number", "TEXT"),
    ("payment_terms", "TEXT"),
    ("reconciliation_status", "TEXT"),
    ("reconciliation_date", "TEXT"),
    ("reconciliation_reference", "TEXT"),
    ("reconciliation_amount", "TEXT"),
    ("source_status", "TEXT"),
    ("balance_due", "TEXT"),
    ("company_name", "TEXT"),
    ("piece_pdf", "TEXT"),
    ("already_paid_on_invoice", "TEXT"),
    ("origin_document_number", "TEXT"),
    ("origin_document_date", "TEXT"),
    ("is_refacturation_pair", "TEXT"),
    ("report", "TEXT"),
)
_P17_LINE_SELECT = (
    " l.expense_subcategory, l.invite_restaurant, l.lieu_restaurant,"
    " l.fuel_station, l.vehicle, l.transport_nature, l.transport_route,"
    " l.hotel_place, l.ht_accommodation, l.ht_breakfast_meals,"
    " l.tva_deductible, l.tva_non_deductible, l.is_classified_marker,"
    " l.tva_dedup_status, l.fiscal_year_mode, l.fiscal_year_target,"
    " l.deferred_month_override, l.payment_source, l.counterparty_account,"
    " l.payment_method, l.payment_reference, l.purchase_date, l.due_date,"
    " l.fitid, l.remittance_reference, l.client_siret, l.client_vat_number,"
    " l.payment_terms, l.reconciliation_status, l.reconciliation_date,"
    " l.reconciliation_reference, l.reconciliation_amount, l.source_status,"
    " l.balance_due, l.company_name, l.piece_pdf, l.already_paid_on_invoice,"
    " l.origin_document_number, l.origin_document_date,"
    " l.is_refacturation_pair, l.report"
)
_DECISION_FIELDS = (
    "action",
    "status",
    "invoice_id",
    "debit_account",
    "credit_account",
    "vat_amount",
    "reason",
    "arbitrated_at",
)


class AccountingStoreError(ValueError):
    """Invalid input or illegal operation on the accounting store."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


class LineNotFoundError(AccountingStoreError, LookupError):
    def __init__(self, tenant_id: str, line_id: str) -> None:
        super().__init__("LINE_NOT_FOUND", f"{tenant_id}/{line_id}")


class LineAlreadyArbitratedError(AccountingStoreError):
    def __init__(self, tenant_id: str, line_id: str) -> None:
        super().__init__("LINE_ALREADY_ARBITRATED", f"{tenant_id}/{line_id}")


def validate_tenant_id(tenant_id: object) -> str:
    if (
        not isinstance(tenant_id, str)
        or _TENANT_ID.fullmatch(tenant_id) is None
        or set(tenant_id) == {"."}
    ):
        raise AccountingStoreError("INVALID_TENANT_ID", repr(tenant_id))
    return tenant_id


def _validate_line_id(line_id: object) -> str:
    if not isinstance(line_id, str) or not 1 <= len(line_id) <= 128 or "\x00" in line_id:
        raise AccountingStoreError("INVALID_LINE_ID", repr(line_id))
    return line_id


def _resolve_db_path(db_path: str | os.PathLike[str], base_dir: Path | None) -> Path:
    raw = os.fspath(db_path)
    if not raw or "\x00" in raw or raw == ":memory:" or raw.startswith("file:"):
        raise AccountingStoreError("INVALID_DB_PATH", repr(raw))
    path = Path(raw)
    if ".." in path.parts:
        raise AccountingStoreError("DB_PATH_TRAVERSAL", raw)
    if base_dir is None:
        resolved = path.resolve()
    else:
        if path.is_absolute():
            raise AccountingStoreError("DB_PATH_TRAVERSAL", raw)
        root = base_dir.resolve()
        resolved = (root / path).resolve()
        if root != resolved and root not in resolved.parents:
            raise AccountingStoreError("DB_PATH_TRAVERSAL", raw)
    if resolved.is_dir():
        raise AccountingStoreError("INVALID_DB_PATH", f"{raw} is a directory")
    return resolved


def _casefold(value: object) -> str:
    return "" if value is None else str(value).casefold()


def _category_matches(row: dict[str, Any], selected: DocumentCategory) -> bool:
    """True when the stored category or the reference path names ``selected``."""
    stored = str(row.get("category") or "").strip()
    if stored:
        return stored == selected.value
    reference = str(row.get("invoice_url") or "")
    return selected.value in re.split(r"[\\/]+", reference)


def _normalize_fallback_methods(
    value: Any,
    *,
    primary: str,
) -> tuple[str, ...]:
    """Normalise a fallback method list, preserving priority order."""
    if value is None:
        return ()
    if isinstance(value, str):
        raw = re.split(r"[,;|/\s]+", value)
    else:
        try:
            raw = [str(item) for item in value]
        except TypeError:
            raise AccountingStoreError(
                "INVALID_FALLBACK_METHODS", repr(value)
            ) from None
    methods: list[str] = []
    for item in raw:
        if not str(item).strip():
            continue
        try:
            method = normalize_expected_payment_method(item)
        except PaymentMethodError as exc:
            raise AccountingStoreError(
                "INVALID_FALLBACK_METHODS", repr(item)
            ) from exc
        if method != primary and method not in methods:
            methods.append(method)
    return tuple(methods)


def _rule_from_row(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    data = dict(row)
    raw = str(data.get("fallback_methods") or "")
    data["fallback_methods"] = [
        item
        for item in re.split(r"[,;|/\s]+", raw)
        if item and item != data.get("expected_payment_method")
    ]
    return data


_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounting_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    JournalCode TEXT NOT NULL,
    JournalLib TEXT NOT NULL,
    EcritureNum TEXT NOT NULL,
    EcritureDate TEXT NOT NULL,
    CompteNum TEXT NOT NULL,
    CompteLib TEXT NOT NULL,
    CompAuxNum TEXT NOT NULL DEFAULT '',
    CompAuxLib TEXT NOT NULL DEFAULT '',
    PieceRef TEXT NOT NULL,
    PieceDate TEXT NOT NULL,
    EcritureLib TEXT NOT NULL,
    Debit TEXT NOT NULL,
    Credit TEXT NOT NULL,
    EcritureLet TEXT NOT NULL DEFAULT '',
    DateLet TEXT,
    ValidDate TEXT,
    Montantdevise TEXT,
    Idevise TEXT NOT NULL DEFAULT '',
    validated INTEGER NOT NULL DEFAULT 1 CHECK (validated IN (0, 1)),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_accounting_entries_tenant
    ON accounting_entries (tenant_id, validated, EcritureDate);

CREATE TABLE IF NOT EXISTS reconciliation_lines (
    tenant_id TEXT NOT NULL,
    line_id TEXT NOT NULL,
    operation_date TEXT,
    label TEXT,
    debit_amount TEXT,
    credit_amount TEXT,
    currency TEXT,
    counterparty TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    invoice_id TEXT,
    invoice_url TEXT,
    category TEXT,
    amount_ht TEXT,
    amount_tva TEXT,
    amount_ttc TEXT,
    document_type TEXT,
    expense_subcategory TEXT,
    invite_restaurant TEXT,
    lieu_restaurant TEXT,
    fuel_station TEXT,
    vehicle TEXT,
    transport_nature TEXT,
    transport_route TEXT,
    hotel_place TEXT,
    ht_accommodation TEXT,
    ht_breakfast_meals TEXT,
    tva_deductible TEXT,
    tva_non_deductible TEXT,
    is_classified_marker TEXT,
    tva_dedup_status TEXT,
    fiscal_year_mode TEXT,
    fiscal_year_target TEXT,
    deferred_month_override TEXT,
    payment_source TEXT,
    counterparty_account TEXT,
    payment_method TEXT,
    payment_reference TEXT,
    purchase_date TEXT,
    due_date TEXT,
    fitid TEXT,
    remittance_reference TEXT,
    client_siret TEXT,
    client_vat_number TEXT,
    payment_terms TEXT,
    reconciliation_status TEXT,
    reconciliation_date TEXT,
    reconciliation_reference TEXT,
    reconciliation_amount TEXT,
    source_status TEXT,
    balance_due TEXT,
    company_name TEXT,
    piece_pdf TEXT,
    already_paid_on_invoice TEXT,
    origin_document_number TEXT,
    origin_document_date TEXT,
    is_refacturation_pair TEXT,
    report TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, line_id)
);
CREATE INDEX IF NOT EXISTS idx_reconciliation_lines_tenant
    ON reconciliation_lines (tenant_id, operation_date);

CREATE TABLE IF NOT EXISTS arbitration_decisions (
    tenant_id TEXT NOT NULL,
    line_id TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    invoice_id TEXT,
    debit_account TEXT,
    credit_account TEXT,
    vat_amount TEXT,
    reason TEXT,
    arbitrated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, line_id)
);
CREATE INDEX IF NOT EXISTS idx_arbitration_decisions_tenant
    ON arbitration_decisions (tenant_id);

CREATE TABLE IF NOT EXISTS candidate_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    rule_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candidate_rules_tenant ON candidate_rules (tenant_id);

CREATE TABLE IF NOT EXISTS counterparty_payment_rules (
    tenant_id TEXT NOT NULL,
    counterparty TEXT NOT NULL,
    counterparty_key TEXT NOT NULL,
    expected_payment_method TEXT NOT NULL,
    fallback_methods TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT '1.0',
    source TEXT NOT NULL DEFAULT 'HUMAN',
    sample_count INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, counterparty_key)
);
CREATE INDEX IF NOT EXISTS idx_counterparty_payment_rules_tenant
    ON counterparty_payment_rules (tenant_id, expected_payment_method);

CREATE TABLE IF NOT EXISTS tier_reference (
    tenant_id TEXT NOT NULL,
    canonical TEXT NOT NULL,
    role TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    subcategory TEXT NOT NULL DEFAULT '',
    aliases TEXT NOT NULL DEFAULT '',
    prefixes TEXT NOT NULL DEFAULT '',
    payment_method TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'AUTORISE',
    siren TEXT NOT NULL DEFAULT '',
    siret TEXT NOT NULL DEFAULT '',
    iban TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, canonical)
);
CREATE INDEX IF NOT EXISTS idx_tier_reference_tenant
    ON tier_reference (tenant_id, role, status);
"""


class SqliteAccountingStore:
    """SQLite implementation of ``FecEntryStore`` and ``ArbitrationStore``."""

    def __init__(
        self,
        db_path: str | os.PathLike[str] | None = None,
        *,
        base_dir: str | os.PathLike[str] | None = None,
        rule_writer: ObsidianCandidateRuleWriter | None = None,
    ) -> None:
        if db_path is None:
            db_path = os.environ.get(DB_PATH_ENV) or DEFAULT_DB_PATH
        self.db_path = _resolve_db_path(
            db_path, Path(base_dir) if base_dir is not None else None
        )
        self.rule_writer = rule_writer
        self.written_rule_paths: list[Path] = []
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Upgrade legacy databases in place (idempotent, additive only)."""
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(reconciliation_lines)")
        }
        for name, declaration in _LINE_MIGRATION_COLUMNS:
            if name not in columns:
                conn.execute(
                    f"ALTER TABLE reconciliation_lines ADD COLUMN {name} {declaration}"
                )
        rule_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(counterparty_payment_rules)")
        }
        if "fallback_methods" not in rule_columns:
            conn.execute(
                "ALTER TABLE counterparty_payment_rules"
                " ADD COLUMN fallback_methods TEXT NOT NULL DEFAULT ''"
            )
        tier_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(tier_reference)")
        }
        for name in ("siren", "siret", "iban"):
            if name not in tier_columns:
                conn.execute(
                    f"ALTER TABLE tier_reference ADD COLUMN {name}"
                    " TEXT NOT NULL DEFAULT ''"
                )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reconciliation_lines_category"
            " ON reconciliation_lines (tenant_id, category)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reconciliation_lines_supplier"
            " ON reconciliation_lines (tenant_id, counterparty)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reconciliation_lines_payment"
            " ON reconciliation_lines (tenant_id, reconciliation_status)"
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.create_function("casefold", 1, _casefold, deterministic=True)
        return conn

    # -- FecEntryStore ---------------------------------------------------

    def add_entry(
        self, tenant_id: str, entry: FecEntry, *, validated: bool = True
    ) -> None:
        validate_tenant_id(tenant_id)
        if not isinstance(entry, FecEntry):
            raise AccountingStoreError("INVALID_ENTRY", type(entry).__name__)
        values: list[str | None] = []
        for column in _FEC_COLUMNS:
            value = getattr(entry, column)
            if value is None:
                values.append(None)
            elif column in _DATE_COLUMNS:
                values.append(value.isoformat())
            elif column in _AMOUNT_COLUMNS:
                values.append(str(value))
            else:
                values.append(value)
        columns = ", ".join(_FEC_COLUMNS)
        marks = ", ".join("?" for _ in _FEC_COLUMNS)
        with closing(self._connect()) as conn, conn:
            conn.execute(
                f"INSERT INTO accounting_entries (tenant_id, {columns}, validated, created_at)"
                f" VALUES (?, {marks}, ?, ?)",
                (tenant_id, *values, int(bool(validated)), _now()),
            )

    def list_validated_entries(
        self, tenant_id: str, date_from: date | None, date_to: date | None
    ) -> list[FecEntry]:
        validate_tenant_id(tenant_id)
        sql = (
            f"SELECT {', '.join(_FEC_COLUMNS)} FROM accounting_entries"
            " WHERE tenant_id = ? AND validated = 1"
        )
        params: list[Any] = [tenant_id]
        if date_from is not None:
            sql += " AND EcritureDate >= ?"
            params.append(date_from.isoformat())
        if date_to is not None:
            sql += " AND EcritureDate <= ?"
            params.append(date_to.isoformat())
        sql += " ORDER BY id"
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [FecEntry.model_validate(dict(row)) for row in rows]

    # -- ArbitrationStore ------------------------------------------------

    def add_line(self, tenant_id: str, line_id: str, line: dict[str, Any]) -> bool:
        """Insert a bank operation line (``PENDING`` unless stated).

        Returns ``False`` without touching the stored row when the line already
        exists, so a re-import never resets an arbitrated line.
        """
        validate_tenant_id(tenant_id)
        _validate_line_id(line_id)
        values = [None if line.get(k) is None else str(line[k]) for k in _LINE_FIELDS]
        status = str(line.get("status") or _PENDING)
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO reconciliation_lines"
                f" (tenant_id, line_id, {', '.join(_LINE_FIELDS)}, status, created_at)"
                f" VALUES (?, ?, {', '.join('?' for _ in _LINE_FIELDS)}, ?, ?)",
                (tenant_id, line_id, *values, status, _now()),
            )
            return cursor.rowcount == 1

    def delete_line(self, tenant_id: str, line_id: str) -> bool:
        validate_tenant_id(tenant_id)
        _validate_line_id(line_id)
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "DELETE FROM reconciliation_lines"
                " WHERE tenant_id = ? AND line_id = ?",
                (tenant_id, line_id),
            )
            return cursor.rowcount > 0

    def get_line(self, tenant_id: str, line_id: str) -> dict[str, Any] | None:
        validate_tenant_id(tenant_id)
        _validate_line_id(line_id)
        rows, _ = self._query_lines(tenant_id, line_id=line_id)
        return rows[0] if rows else None

    def list_lines(
        self,
        tenant_id: str,
        *,
        status: str = "ALL",
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        validate_tenant_id(tenant_id)
        if limit < 0 or offset < 0:
            raise AccountingStoreError("INVALID_PAGINATION")
        return self._query_lines(
            tenant_id,
            status=status,
            search=search,
            limit=limit,
            offset=offset,
            include_rejected=status != "ALL",
        )

    def list_category_lines(
        self, tenant_id: str, category: DocumentCategory | str
    ) -> list[dict[str, Any]]:
        """Non-rejected lines of one business category, in stable order.

        The category comes from the dedicated column; lines written before
        this column existed are matched on the canonical
        ``<STATE>/<CATEGORY>/...`` segment of ``invoice_url``. ``REJETE``
        lines are excluded even when an arbitration decision exists.
        """
        validate_tenant_id(tenant_id)
        try:
            selected = DocumentCategory(category)
        except (TypeError, ValueError):
            raise AccountingStoreError("INVALID_CATEGORY", repr(category)) from None
        raw_status = "COALESCE(d.status, l.status, 'PENDING')"
        escaped = (
            selected.value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        pattern = f"%/{escaped}/%"
        source = (
            " FROM reconciliation_lines l LEFT JOIN arbitration_decisions d"
            " ON d.tenant_id = l.tenant_id AND d.line_id = l.line_id"
        )
        select = (
            "SELECT l.line_id AS line_id, l.operation_date, l.label, l.memo, l.debit_amount,"
            " l.credit_amount, l.currency, l.counterparty,"
            f" COALESCE(d.invoice_id, l.invoice_id) AS invoice_id, l.invoice_url,"
            " COALESCE(l.category, '') AS category,"
            " l.amount_ht, l.amount_tva, l.amount_ttc, l.document_type,"
            " l.status AS status,"
            f"{_P17_LINE_SELECT},"
            f" {raw_status} AS raw_status, d.action, d.debit_account, d.credit_account,"
            " d.vat_amount, d.reason, d.arbitrated_at"
        )
        where = (
            " WHERE l.tenant_id = ? AND l.status != 'REJETE'"
            " AND COALESCE(d.status, l.status, 'PENDING') != 'REJETE'"
            " AND (l.category = ? OR (COALESCE(l.category, '') = ''"
            " AND l.invoice_url LIKE ? ESCAPE '\\'))"
        )
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"{select}{source}{where}"
                " ORDER BY COALESCE(l.operation_date, ''), l.line_id",
                (tenant_id, selected.value, pattern),
            ).fetchall()
        return [
            row
            for row in (dict(item) for item in rows)
            if _category_matches(row, selected)
        ]

    def list_master_lines(self, tenant_id: str) -> list[dict[str, Any]]:
        """Every non-rejected line, for the P17 master-table exporters.

        Unlike :meth:`list_category_lines` this includes bank-only lines so the
        ``TABLEAU_MERE_BANQUE_TVA`` workbook can mirror the TVA control pool.
        """
        validate_tenant_id(tenant_id)
        rows, _ = self._query_lines(
            tenant_id,
            status="ALL",
            limit=-1,
            include_rejected=False,
        )
        return rows

    def list_customer_invoices(self, tenant_id: str) -> list[dict[str, Any]]:
        """Return the non-rejected customer invoices imported in bulk (P20).

        LCR sub-payment rows created by the P19 bordereau allocator are sales
        lines too, but they are not invoices: only ``CUSTOMER_INVOICE`` rows
        (or the historical ``SALE-`` prefix) are returned.
        """
        validate_tenant_id(tenant_id)
        rows, _ = self._query_lines(
            tenant_id,
            status="ALL",
            limit=-1,
            include_rejected=False,
        )
        invoices: list[dict[str, Any]] = []
        for row in rows:
            if str(row.get("category") or "") != DocumentCategory.VENTES_CLIENTS.value:
                continue
            document_type = str(row.get("document_type") or "").strip().upper()
            line_id = str(row.get("line_id") or "")
            if document_type in {
                "CUSTOMER_INVOICE",
                "SALE",
                "SALE_INVOICE",
                "VENTE_CLIENT",
            } or (line_id.startswith("SALE-")):
                invoices.append(row)
        return invoices

    def reset_customer_invoice_reconciliation(self, tenant_id: str) -> int:
        """Clear payment lettering on customer invoices (idempotent reruns)."""
        validate_tenant_id(tenant_id)
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "UPDATE reconciliation_lines SET reconciliation_status = ?,"
                " reconciliation_date = NULL, reconciliation_reference = NULL,"
                " reconciliation_amount = NULL"
                " WHERE tenant_id = ? AND category = ?"
                " AND document_type = ? AND status != 'REJETE'",
                (
                    "EN_ATTENTE_REGLEMENT",
                    tenant_id,
                    DocumentCategory.VENTES_CLIENTS.value,
                    "CUSTOMER_INVOICE",
                ),
            )
            return cursor.rowcount

    def reconcile_customer_invoice(
        self,
        tenant_id: str,
        line_id: str,
        *,
        reconciliation_date: str | date,
        reference: str,
        amount: str | Decimal | None = None,
        status: str = "PAYEE_RAPPROCHEE",
    ) -> bool:
        """Persist the bank lettering of one customer invoice (P20).

        Returns ``True`` when a customer-invoice row was updated. The document
        itself keeps its ``VALIDE`` life-cycle status; payment lettering lives
        in the dedicated ``reconciliation_*`` columns so a re-import can never
        confuse a paid invoice with an unposted document.
        """
        validate_tenant_id(tenant_id)
        _validate_line_id(line_id)
        if isinstance(reconciliation_date, date):
            date_value = reconciliation_date.isoformat()
        else:
            date_value = str(reconciliation_date or "").strip()
            try:
                date_value = date.fromisoformat(date_value[:10]).isoformat()
            except ValueError:
                raise AccountingStoreError(
                    "INVALID_RECONCILIATION_DATE", repr(reconciliation_date)
                ) from None
        reference_value = str(reference or "").strip()
        if not reference_value or "\x00" in reference_value:
            raise AccountingStoreError("INVALID_RECONCILIATION_REFERENCE", repr(reference))
        status_value = str(status or "").strip().upper()
        if status_value not in {"PAYEE_RAPPROCHEE", "EN_ATTENTE_REGLEMENT"}:
            raise AccountingStoreError("INVALID_RECONCILIATION_STATUS", status_value)
        amount_value = None if amount is None else str(Decimal(str(amount)))
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "UPDATE reconciliation_lines SET reconciliation_status = ?,"
                " reconciliation_date = ?, reconciliation_reference = ?,"
                " reconciliation_amount = ?"
                " WHERE tenant_id = ? AND line_id = ?"
                " AND category = ? AND status != 'REJETE'",
                (
                    status_value,
                    date_value,
                    reference_value,
                    amount_value,
                    tenant_id,
                    line_id,
                    DocumentCategory.VENTES_CLIENTS.value,
                ),
            )
            return cursor.rowcount > 0

    update_customer_invoice_reconciliation = reconcile_customer_invoice
    mark_customer_invoice_paid = reconcile_customer_invoice

    def list_supplier_ledger(
        self, tenant_id: str, supplier: str | None = None
    ) -> list[dict[str, Any]]:
        """Return the chronological non-rejected supplier ledger rows.

        Document lines are kept only when their status is ``VALIDE``. Bank
        lines are kept when they carry a movement or an arbitration decision,
        so both attached and candidate payments remain visible. ``supplier``
        is a case-insensitive partial match on ``counterparty``.
        """
        validate_tenant_id(tenant_id)
        if supplier is not None and not isinstance(supplier, str):
            raise AccountingStoreError("INVALID_SUPPLIER", repr(supplier))
        raw_status = "COALESCE(d.status, l.status, 'PENDING')"
        source = (
            " FROM reconciliation_lines l LEFT JOIN arbitration_decisions d"
            " ON d.tenant_id = l.tenant_id AND d.line_id = l.line_id"
        )
        select = (
            "SELECT l.line_id AS line_id, l.operation_date, l.label, l.counterparty,"
            " COALESCE(NULLIF(l.counterparty, ''), '') AS supplier,"
            " l.debit_amount, l.credit_amount, l.currency,"
            " COALESCE(d.invoice_id, l.invoice_id) AS invoice_id,"
            " l.invoice_id AS document_invoice_id, l.invoice_url,"
            " COALESCE(l.category, '') AS category,"
            " l.amount_ht, l.amount_tva, l.amount_ttc,"
            " COALESCE(l.document_type, '') AS document_type,"
            " l.payment_source, l.counterparty_account,"
            " l.piece_pdf, l.already_paid_on_invoice,"
            " l.origin_document_number, l.origin_document_date,"
            " l.is_refacturation_pair,"
            " l.reconciliation_status, l.reconciliation_date,"
            " l.reconciliation_reference, l.reconciliation_amount,"
            " l.balance_due, l.fitid, l.payment_reference,"
            " l.remittance_reference,"
            f" {raw_status} AS raw_status, d.action AS decision_action,"
            " d.invoice_id AS decision_invoice_id, d.debit_account, d.credit_account"
        )
        where = [
            "l.tenant_id = ?",
            "l.status != 'REJETE'",
            "COALESCE(d.status, l.status, 'PENDING') != 'REJETE'",
            "(l.status = 'VALIDE'"
            " OR l.debit_amount IS NOT NULL"
            " OR l.credit_amount IS NOT NULL"
            " OR d.action IS NOT NULL)",
        ]
        params: list[Any] = [tenant_id]
        needle = supplier.strip().casefold() if supplier else ""
        if needle:
            escaped = (
                needle.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            where.append(
                "casefold(COALESCE(l.counterparty, '')) LIKE ? ESCAPE '\\'"
            )
            params.append(f"%{escaped}%")
        sql = (
            f"{select}{source} WHERE {' AND '.join(where)}"
            " ORDER BY casefold(COALESCE(l.counterparty, '')),"
            " COALESCE(l.operation_date, ''), l.line_id"
        )
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def upsert_tier_reference(
        self, tenant_id: str, canonical: str, fields: dict[str, Any]
    ) -> None:
        validate_tenant_id(tenant_id)
        values = (
            tenant_id,
            str(canonical),
            str(fields.get("role") or "A_AUTORISER"),
            str(fields.get("category") or ""),
            str(fields.get("subcategory") or ""),
            str(fields.get("aliases") or ""),
            str(fields.get("prefixes") or ""),
            str(fields.get("payment_method") or ""),
            str(fields.get("status") or "AUTORISE"),
            str(fields.get("siren") or ""),
            str(fields.get("siret") or ""),
            str(fields.get("iban") or ""),
            _now(),
        )
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO tier_reference"
                " (tenant_id, canonical, role, category, subcategory, aliases,"
                " prefixes, payment_method, status, siren, siret, iban,"
                " updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(tenant_id, canonical) DO UPDATE SET"
                " role=excluded.role, category=excluded.category,"
                " subcategory=excluded.subcategory, aliases=excluded.aliases,"
                " prefixes=excluded.prefixes, payment_method=excluded.payment_method,"
                " status=excluded.status, siren=excluded.siren,"
                " siret=excluded.siret, iban=excluded.iban,"
                " updated_at=excluded.updated_at",
                values,
            )

    def list_tier_references(self, tenant_id: str) -> list[dict[str, Any]]:
        validate_tenant_id(tenant_id)
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM tier_reference WHERE tenant_id = ?"
                " ORDER BY casefold(canonical)",
                (tenant_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_tier_reference(
        self, tenant_id: str, canonical: str
    ) -> dict[str, Any] | None:
        validate_tenant_id(tenant_id)
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM tier_reference WHERE tenant_id = ? AND canonical = ?",
                (tenant_id, str(canonical)),
            ).fetchone()
        return dict(row) if row is not None else None

    def delete_tier_reference(self, tenant_id: str, canonical: str) -> bool:
        validate_tenant_id(tenant_id)
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "DELETE FROM tier_reference WHERE tenant_id = ? AND canonical = ?",
                (tenant_id, str(canonical)),
            )
            return cursor.rowcount > 0

    def arbitrate(self, tenant_id: str, line_id: str, decision: dict[str, Any]) -> None:
        """Persist the HITL decision for a line, atomically and only once."""
        validate_tenant_id(tenant_id)
        _validate_line_id(line_id)
        action, status = decision.get("action"), decision.get("status")
        if not action or not status:
            raise AccountingStoreError("INVALID_DECISION", "action and status are required")
        values = [
            None if decision.get(k) is None else str(decision[k]) for k in _DECISION_FIELDS
        ]
        values[_DECISION_FIELDS.index("arbitrated_at")] = str(
            decision.get("arbitrated_at") or _now()
        )
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                line = conn.execute(
                    "SELECT status FROM reconciliation_lines WHERE tenant_id = ? AND line_id = ?",
                    (tenant_id, line_id),
                ).fetchone()
                if line is None:
                    raise LineNotFoundError(tenant_id, line_id)
                existing = conn.execute(
                    "SELECT 1 FROM arbitration_decisions WHERE tenant_id = ? AND line_id = ?",
                    (tenant_id, line_id),
                ).fetchone()
                if existing is not None or line["status"] != _PENDING:
                    raise LineAlreadyArbitratedError(tenant_id, line_id)
                conn.execute(
                    "INSERT INTO arbitration_decisions"
                    f" (tenant_id, line_id, {', '.join(_DECISION_FIELDS)})"
                    f" VALUES (?, ?, {', '.join('?' for _ in _DECISION_FIELDS)})",
                    (tenant_id, line_id, *values),
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def save_decision(self, tenant_id: str, line_id: str, decision: dict[str, Any]) -> None:
        self.arbitrate(tenant_id, line_id, decision)

    def deactivate_rejected_document(self, tenant_id: str, sha256: str) -> bool:
        """Force a document-flow line to ``REJETE`` (P14.3 "annule et remplace").

        Explicit exception to the arbitration-preservation rule of
        :meth:`upsert_document_line`: a rejected invoice must never stay active
        in the reconciliation pool or the CSV exports. The arbitration decision
        row is kept intact for audit. Returns ``True`` when the status changed.
        """
        from services.acompleter_workbook_service import derive_document_id

        validate_tenant_id(tenant_id)
        line_id = derive_document_id(sha256)
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "UPDATE reconciliation_lines SET status = ?"
                " WHERE tenant_id = ? AND line_id = ? AND status != ?",
                ("REJETE", tenant_id, line_id, "REJETE"),
            )
            return cursor.rowcount > 0

    def upsert_document_line(
        self, tenant_id: str, line_id: str, line: dict[str, Any]
    ) -> str:
        """Insert or refresh a document-flow line (P14.3 folder synchroniser).

        Returns ``created`` when the line is new, ``updated`` when its document
        fields (``label``, ``invoice_id``, ``invoice_url``, ``status``, ...) are
        refreshed, and ``preserved`` when a human arbitration decision already
        exists — the store's immutability invariant is never broken.
        """
        validate_tenant_id(tenant_id)
        _validate_line_id(line_id)
        line = dict(line)
        payment_source = normalize_payment_source(line.get("payment_source"))
        if payment_source is not None:
            line["payment_source"] = payment_source.value
            mandatory_account = counterparty_account_for_payment_source(
                payment_source
            )
            if mandatory_account is not None:
                line["counterparty_account"] = mandatory_account
        values = [None if line.get(k) is None else str(line[k]) for k in _LINE_FIELDS]
        status = str(line.get("status") or _PENDING)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT 1 FROM reconciliation_lines"
                    " WHERE tenant_id = ? AND line_id = ?",
                    (tenant_id, line_id),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        "INSERT INTO reconciliation_lines"
                        f" (tenant_id, line_id, {', '.join(_LINE_FIELDS)}, status, created_at)"
                        f" VALUES (?, ?, {', '.join('?' for _ in _LINE_FIELDS)}, ?, ?)",
                        (tenant_id, line_id, *values, status, _now()),
                    )
                    conn.execute("COMMIT")
                    return "created"
                decided = conn.execute(
                    "SELECT 1 FROM arbitration_decisions"
                    " WHERE tenant_id = ? AND line_id = ?",
                    (tenant_id, line_id),
                ).fetchone()
                if decided is not None:
                    conn.execute("COMMIT")
                    return "preserved"
                assignments = ", ".join(f"{field} = ?" for field in _LINE_FIELDS)
                conn.execute(
                    f"UPDATE reconciliation_lines SET {assignments}, status = ?"
                    " WHERE tenant_id = ? AND line_id = ?",
                    (*values, status, tenant_id, line_id),
                )
                conn.execute("COMMIT")
                return "updated"
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def force_upsert_document_line(
        self, tenant_id: str, line_id: str, line: dict[str, Any]
    ) -> str:
        """Insert or update a document line even when a decision exists.

        Reserved for the opening-statement ingestion (P26): the director's
        historical truth must overwrite a stale synthetic lettering. Normal
        document-flow synchronisation keeps using
        :meth:`upsert_document_line` and its immutability invariant.
        """
        validate_tenant_id(tenant_id)
        _validate_line_id(line_id)
        line = dict(line)
        payment_source = normalize_payment_source(line.get("payment_source"))
        if payment_source is not None:
            line["payment_source"] = payment_source.value
            mandatory_account = counterparty_account_for_payment_source(
                payment_source
            )
            if mandatory_account is not None:
                line["counterparty_account"] = mandatory_account
        values = [None if line.get(k) is None else str(line[k]) for k in _LINE_FIELDS]
        status = str(line.get("status") or _PENDING)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT 1 FROM reconciliation_lines"
                    " WHERE tenant_id = ? AND line_id = ?",
                    (tenant_id, line_id),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        "INSERT INTO reconciliation_lines"
                        f" (tenant_id, line_id, {', '.join(_LINE_FIELDS)}, status, created_at)"
                        f" VALUES (?, ?, {', '.join('?' for _ in _LINE_FIELDS)}, ?, ?)",
                        (tenant_id, line_id, *values, status, _now()),
                    )
                    conn.execute("COMMIT")
                    return "created"
                assignments = ", ".join(f"{field} = ?" for field in _LINE_FIELDS)
                conn.execute(
                    f"UPDATE reconciliation_lines SET {assignments}, status = ?"
                    " WHERE tenant_id = ? AND line_id = ?",
                    (*values, status, tenant_id, line_id),
                )
                conn.execute("COMMIT")
                return "updated"
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def rename_counterparty(
        self, tenant_id: str, old_name: str, new_name: str
    ) -> int:
        """Rename every line of one counterparty (P26 alias merge)."""
        validate_tenant_id(tenant_id)
        old_text = str(old_name or "").strip()
        new_text = str(new_name or "").strip()
        if not old_text or not new_text or old_text == new_text:
            return 0
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "UPDATE reconciliation_lines SET counterparty = ?"
                " WHERE tenant_id = ? AND counterparty = ?",
                (new_text, tenant_id, old_text),
            )
            return cursor.rowcount

    def save_candidate_rule(self, tenant_id: str, rule: dict[str, Any]) -> None:
        validate_tenant_id(tenant_id)
        if self.rule_writer is not None:
            self.written_rule_paths.append(self.rule_writer.write(tenant_id, rule))
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO candidate_rules (tenant_id, rule_json, created_at) VALUES (?, ?, ?)",
                (tenant_id, json.dumps(rule, default=str, sort_keys=True), _now()),
            )

    def list_candidate_rules(self, tenant_id: str) -> list[dict[str, Any]]:
        validate_tenant_id(tenant_id)
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT rule_json FROM candidate_rules WHERE tenant_id = ? ORDER BY id",
                (tenant_id,),
            ).fetchall()
        return [json.loads(row["rule_json"]) for row in rows]

    # -- counterparty payment rules (P19) --------------------------------

    def set_counterparty_payment_rule(
        self,
        tenant_id: str,
        counterparty: str,
        expected_payment_method: str,
        *,
        confidence: float | str | Decimal = Decimal("1.0"),
        source: str = "HUMAN",
        sample_count: int = 1,
        fallback_methods: str | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Create or replace the usual settlement method of one counterparty.

        ``fallback_methods`` keeps alternative accepted channels (P20): a
        customer whose main habit is ``LCR`` may also pay a small invoice by
        ``CHEQUE`` or ``VIREMENT`` without losing its LCR priority.
        """
        validate_tenant_id(tenant_id)
        name = self._require_counterparty(counterparty)
        try:
            method = normalize_expected_payment_method(expected_payment_method)
        except PaymentMethodError as exc:
            raise AccountingStoreError("INVALID_PAYMENT_METHOD", exc.args[0]) from exc
        fallbacks = _normalize_fallback_methods(
            fallback_methods, primary=method
        )
        try:
            score = Decimal(str(confidence))
        except Exception as exc:  # noqa: BLE001 - normalised below
            raise AccountingStoreError("INVALID_CONFIDENCE", repr(confidence)) from exc
        if not score.is_finite() or score < 0 or score > 1:
            raise AccountingStoreError("INVALID_CONFIDENCE", str(confidence))
        origin = str(source or "HUMAN").strip().upper() or "HUMAN"
        if origin not in {"HUMAN", "LEARNED", "IMPORT"}:
            raise AccountingStoreError("INVALID_RULE_SOURCE", origin)
        if not isinstance(sample_count, int) or isinstance(sample_count, bool):
            raise AccountingStoreError("INVALID_SAMPLE_COUNT", repr(sample_count))
        if sample_count < 1:
            raise AccountingStoreError("INVALID_SAMPLE_COUNT", repr(sample_count))
        key = normalize_counterparty_name(name)
        fallback_text = ",".join(fallbacks)
        with closing(self._connect()) as conn, conn:
            existing = conn.execute(
                "SELECT 1 FROM counterparty_payment_rules"
                " WHERE tenant_id = ? AND counterparty_key = ?",
                (tenant_id, key),
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO counterparty_payment_rules"
                    " (tenant_id, counterparty, counterparty_key,"
                    " expected_payment_method, fallback_methods, confidence,"
                    " source, sample_count, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        tenant_id,
                        name,
                        key,
                        method,
                        fallback_text,
                        str(score),
                        origin,
                        sample_count,
                        _now(),
                    ),
                )
            else:
                conn.execute(
                    "UPDATE counterparty_payment_rules SET counterparty = ?,"
                    " expected_payment_method = ?, fallback_methods = ?,"
                    " confidence = ?, source = ?, sample_count = ?, updated_at = ?"
                    " WHERE tenant_id = ? AND counterparty_key = ?",
                    (
                        name,
                        method,
                        fallback_text,
                        str(score),
                        origin,
                        sample_count,
                        _now(),
                        tenant_id,
                        key,
                    ),
                )
        rule = self.get_counterparty_payment_rule(tenant_id, name)
        assert rule is not None
        return rule

    def get_counterparty_payment_rule(
        self, tenant_id: str, counterparty: str
    ) -> dict[str, Any] | None:
        """Return the rule of one counterparty (case-insensitive lookup)."""
        validate_tenant_id(tenant_id)
        if not isinstance(counterparty, str) or not counterparty.strip():
            return None
        key = normalize_counterparty_name(counterparty)
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT tenant_id, counterparty, expected_payment_method,"
                " fallback_methods, confidence, source, sample_count, updated_at"
                " FROM counterparty_payment_rules"
                " WHERE tenant_id = ? AND counterparty_key = ?",
                (tenant_id, key),
            ).fetchone()
        return _rule_from_row(row) if row is not None else None

    def list_counterparty_payment_rules(self, tenant_id: str) -> list[dict[str, Any]]:
        """Every payment habit of the tenant, ordered by counterparty."""
        validate_tenant_id(tenant_id)
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT tenant_id, counterparty, expected_payment_method,"
                " fallback_methods, confidence, source, sample_count, updated_at"
                " FROM counterparty_payment_rules WHERE tenant_id = ?"
                " ORDER BY counterparty_key",
                (tenant_id,),
            ).fetchall()
        return [_rule_from_row(row) for row in rows]

    def get_counterparty_payment_methods(
        self, tenant_id: str, counterparty: str
    ) -> tuple[str, ...]:
        """Return primary + fallback methods, primary first."""
        rule = self.get_counterparty_payment_rule(tenant_id, counterparty)
        if rule is None:
            return ()
        methods = [rule["expected_payment_method"], *rule.get("fallback_methods", [])]
        return tuple(dict.fromkeys(methods))

    def delete_counterparty_payment_rule(
        self, tenant_id: str, counterparty: str
    ) -> bool:
        """Delete one rule; returns ``True`` when a row was removed."""
        validate_tenant_id(tenant_id)
        if not isinstance(counterparty, str) or not counterparty.strip():
            return False
        key = normalize_counterparty_name(counterparty)
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "DELETE FROM counterparty_payment_rules"
                " WHERE tenant_id = ? AND counterparty_key = ?",
                (tenant_id, key),
            )
            return cursor.rowcount > 0

    def learn_counterparty_payment_rules(
        self,
        tenant_id: str,
        *,
        min_occurrences: int = 1,
        overwrite_human: bool = False,
    ) -> list[dict[str, Any]]:
        """Infer the dominant settlement method from observed bank lines."""
        validate_tenant_id(tenant_id)
        if (
            not isinstance(min_occurrences, int)
            or isinstance(min_occurrences, bool)
            or min_occurrences < 1
        ):
            raise AccountingStoreError(
                "INVALID_MIN_OCCURRENCES", repr(min_occurrences)
            )
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT counterparty, payment_method, COUNT(*) AS occurrences"
                " FROM reconciliation_lines"
                " WHERE tenant_id = ? AND counterparty IS NOT NULL"
                " AND TRIM(counterparty) != '' AND payment_method IS NOT NULL"
                " AND TRIM(payment_method) != '' AND payment_method != 'AUTRE'"
                " GROUP BY casefold(counterparty), payment_method",
                (tenant_id,),
            ).fetchall()
        groups: dict[str, dict[str, Any]] = {}
        for row in rows:
            name = str(row["counterparty"]).strip()
            key = normalize_counterparty_name(name)
            if not key:
                continue
            group = groups.setdefault(
                key,
                {"counterparty": name, "methods": {}, "total": 0},
            )
            group["methods"][str(row["payment_method"])] = int(row["occurrences"])
            group["total"] += int(row["occurrences"])
        learned: list[dict[str, Any]] = []
        for key in sorted(groups):
            group = groups[key]
            if group["total"] < min_occurrences:
                continue
            existing = self.get_counterparty_payment_rule(
                tenant_id, group["counterparty"]
            )
            if (
                existing is not None
                and existing["source"] == "HUMAN"
                and not overwrite_human
            ):
                continue
            method, count = sorted(
                group["methods"].items(), key=lambda item: (-item[1], item[0])
            )[0]
            try:
                expected = normalize_expected_payment_method(method)
            except PaymentMethodError:
                continue
            confidence = (Decimal(count) / Decimal(group["total"])).quantize(
                Decimal("0.0001")
            )
            learned.append(
                self.set_counterparty_payment_rule(
                    tenant_id,
                    group["counterparty"],
                    expected,
                    confidence=confidence,
                    source="LEARNED",
                    sample_count=group["total"],
                    fallback_methods=(
                        existing.get("fallback_methods") if existing else None
                    ),
                )
            )
        return learned

    def filter_lines_by_payment_rule(
        self,
        tenant_id: str,
        lines: list[dict[str, Any]] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Split lines into ``matched`` / ``mismatched`` / ``without_rule``."""
        validate_tenant_id(tenant_id)
        rules = {
            normalize_counterparty_name(rule["counterparty"]): rule
            for rule in self.list_counterparty_payment_rules(tenant_id)
        }
        if lines is None:
            lines, _ = self._query_lines(
                tenant_id, status="PENDING", limit=-1, include_rejected=False
            )
        result: dict[str, list[dict[str, Any]]] = {
            "matched": [],
            "mismatched": [],
            "without_rule": [],
        }
        for line in lines:
            enriched = dict(line)
            rule = rules.get(
                normalize_counterparty_name(enriched.get("counterparty"))
            )
            if rule is None:
                enriched["expected_payment_method"] = None
                enriched["payment_rule_matches"] = None
                enriched["payment_rule_source"] = None
                result["without_rule"].append(enriched)
                continue
            matches = payment_method_matches(
                rule["expected_payment_method"], enriched.get("payment_method")
            )
            enriched["expected_payment_method"] = rule["expected_payment_method"]
            enriched["payment_rule_matches"] = matches
            enriched["payment_rule_source"] = rule["source"]
            result["matched" if matches else "mismatched"].append(enriched)
        return result

    def prioritize_lines_by_payment_rule(
        self,
        tenant_id: str,
        lines: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Order candidate lines: matching habit, unknown, then mismatching."""
        groups = self.filter_lines_by_payment_rule(tenant_id, lines)
        ordered: list[dict[str, Any]] = []
        for priority, bucket in enumerate(
            ("matched", "without_rule", "mismatched")
        ):
            for line in groups[bucket]:
                enriched = dict(line)
                enriched["payment_rule_priority"] = priority
                ordered.append(enriched)
        return ordered

    @staticmethod
    def _require_counterparty(counterparty: object) -> str:
        if not isinstance(counterparty, str):
            raise AccountingStoreError("INVALID_COUNTERPARTY", repr(counterparty))
        name = counterparty.strip()
        if not name or len(name) > 200 or "\x00" in name:
            raise AccountingStoreError("INVALID_COUNTERPARTY", repr(counterparty))
        return name

    save_counterparty_payment_rule = set_counterparty_payment_rule
    upsert_counterparty_payment_rule = set_counterparty_payment_rule
    learn_payment_rules = learn_counterparty_payment_rules
    filter_by_payment_rule = filter_lines_by_payment_rule
    prioritize_by_payment_rule = prioritize_lines_by_payment_rule

    # -- internals -------------------------------------------------------

    def _query_lines(
        self,
        tenant_id: str,
        *,
        line_id: str | None = None,
        status: str = "ALL",
        search: str | None = None,
        limit: int = -1,
        offset: int = 0,
        include_rejected: bool = True,
    ) -> tuple[list[dict[str, Any]], int]:
        raw_status = "COALESCE(d.status, l.status, 'PENDING')"
        invoice = "COALESCE(d.invoice_id, l.invoice_id)"
        where = ["l.tenant_id = ?"]
        params: list[Any] = [tenant_id]
        if line_id is not None:
            where.append("l.line_id = ?")
            params.append(line_id)
        if status != "ALL":
            where.append(f"{raw_status} = ?")
            params.append(status)
        elif not include_rejected:
            where.append("l.status != 'REJETE'")
        needle = search.strip().casefold() if search else ""
        if needle:
            escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append(
                "(casefold(l.label) LIKE ? ESCAPE '\\' OR casefold(l.counterparty) LIKE ?"
                f" ESCAPE '\\' OR casefold({invoice}) LIKE ? ESCAPE '\\')"
            )
            params.extend([f"%{escaped}%"] * 3)
        clause = " AND ".join(where)
        source = (
            " FROM reconciliation_lines l LEFT JOIN arbitration_decisions d"
            " ON d.tenant_id = l.tenant_id AND d.line_id = l.line_id"
        )
        select = (
            "SELECT l.line_id AS line_id, l.operation_date, l.label, l.memo, l.debit_amount,"
            " l.credit_amount, l.currency, l.counterparty,"
            f" {invoice} AS invoice_id, l.invoice_url,"
            " COALESCE(l.category, '') AS category,"
            " l.amount_ht, l.amount_tva, l.amount_ttc, l.document_type,"
            " l.status AS status,"
            f"{_P17_LINE_SELECT},"
            f" {raw_status} AS raw_status, d.action, d.debit_account, d.credit_account,"
            " d.vat_amount, d.reason, d.arbitrated_at"
        )
        with closing(self._connect()) as conn:
            total = conn.execute(f"SELECT COUNT(*){source} WHERE {clause}", params).fetchone()[0]
            rows = conn.execute(
                f"{select}{source} WHERE {clause}"
                " ORDER BY COALESCE(l.operation_date, ''), l.line_id LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [dict(row) for row in rows], total


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def accounting_backend_is_sqlite() -> bool:
    return os.environ.get(STORE_BACKEND_ENV, "").strip().lower() == "sqlite"


@lru_cache(maxsize=None)
def _shared_store(db_path: str) -> SqliteAccountingStore:
    return SqliteAccountingStore(
        db_path,
        rule_writer=ObsidianCandidateRuleWriter(Path("knowledge") / "rules"),
    )


def get_shared_sqlite_store() -> SqliteAccountingStore:
    """Process-wide store shared by the export and arbitration routers."""
    return _shared_store(os.environ.get(DB_PATH_ENV) or os.fspath(DEFAULT_DB_PATH))
