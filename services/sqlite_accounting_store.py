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
from typing import Any

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
    "debit_amount",
    "credit_amount",
    "currency",
    "counterparty",
    "invoice_id",
    "invoice_url",
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
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)

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
            "SELECT l.line_id AS line_id, l.operation_date, l.label, l.debit_amount,"
            " l.credit_amount, l.currency, l.counterparty,"
            f" {invoice} AS invoice_id, l.invoice_url,"
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
