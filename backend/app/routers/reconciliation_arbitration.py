"""P11.1 - Manual (HITL) arbitration endpoint for bank reconciliation.

The tenant is taken exclusively from ``request.state.tenant_id`` (set by
``TenantContextMiddleware``); a tenant carried in the body is only accepted if
it matches. A line owned by another tenant is reported as not found.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.concurrency import run_in_threadpool

from services.bank_statement_parser_service import (
    MAX_BYTES,
    BankStatementError,
    normalize_bank_rows,
    parse_bank_statement_csv,
)
from services.rule_registry_service import ObsidianCandidateRuleWriter, RuleRegistryError
from services.sqlite_accounting_store import (
    AccountingStoreError,
    LineAlreadyArbitratedError,
    LineNotFoundError,
    accounting_backend_is_sqlite,
    get_shared_sqlite_store,
)

ACCOUNT_PATTERN = re.compile(r"^\d{6}\Z")
PERSONAL_DEBIT_ACCOUNT = "108000"
PERSONAL_CREDIT_ACCOUNT = "455000"


class ArbitrationAction(str, Enum):
    ATTACH_INVOICE = "ATTACH_INVOICE"
    PERSONAL_EXPENSE = "PERSONAL_EXPENSE"
    VALIDATE_WITHOUT_INVOICE = "VALIDATE_WITHOUT_INVOICE"


class ArbitrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_id: str = Field(min_length=1, max_length=128)
    action: ArbitrationAction
    invoice_id: str | None = Field(default=None, min_length=1, max_length=128)
    debit_account: str | None = None
    credit_account: str | None = None
    reason: str | None = Field(default=None, max_length=500)
    remember_rule: bool = False
    tenant_id: str | None = None

    @field_validator("debit_account", "credit_account")
    @classmethod
    def _accounts(cls, v: str | None) -> str | None:
        if v is not None and not ACCOUNT_PATTERN.match(v):
            raise ValueError("account must be exactly 6 digits")
        return v

    @model_validator(mode="after")
    def _coherence(self) -> "ArbitrationRequest":
        if self.action is ArbitrationAction.ATTACH_INVOICE and not self.invoice_id:
            raise ValueError("invoice_id is required for ATTACH_INVOICE")
        if self.action is not ArbitrationAction.ATTACH_INVOICE and self.invoice_id:
            raise ValueError("invoice_id is only allowed for ATTACH_INVOICE")
        if self.action is ArbitrationAction.VALIDATE_WITHOUT_INVOICE and not self.reason:
            raise ValueError("reason is required for VALIDATE_WITHOUT_INVOICE")
        return self


class ArbitrationResult(BaseModel):
    line_id: str
    tenant_id: str
    action: ArbitrationAction
    status: str
    invoice_id: str | None = None
    debit_account: str | None = None
    credit_account: str | None = None
    vat_amount: str | None = None
    reason: str | None = None
    candidate_rule: dict[str, Any] | None = None


class LineStatusFilter(str, Enum):
    ALL = "ALL"
    PENDING = "PENDING"
    INVOICE_ATTACHED = "INVOICE_ATTACHED"
    BOOKED_PERSONAL = "BOOKED_PERSONAL"
    RELEASED_WITHOUT_INVOICE = "RELEASED_WITHOUT_INVOICE"


_VIEW_STATUS = {
    "PENDING": "PENDING",
    "INVOICE_ATTACHED": "MATCHED",
    "BOOKED_PERSONAL": "ARBITRATED_PERSONAL",
    "RELEASED_WITHOUT_INVOICE": "ARBITRATED_NO_INVOICE",
}


class ArbitrationMetadata(BaseModel):
    reason: str | None = None
    arbitrated_at: str | None = None


class ReconciliationLineView(BaseModel):
    line_id: str
    operation_date: str | None = None
    label: str | None = None
    debit_amount: str | None = None
    credit_amount: str | None = None
    currency: str | None = None
    status: str
    counterparty: str | None = None
    invoice_id: str | None = None
    invoice_url: str | None = None
    debit_account: str | None = None
    credit_account: str | None = None
    vat_amount: str | None = None
    arbitration_metadata: ArbitrationMetadata | None = None


class ReconciliationLinesPage(BaseModel):
    items: list[ReconciliationLineView]
    total: int
    limit: int
    offset: int


class ArbitrationStore(Protocol):
    def list_lines(
        self,
        tenant_id: str,
        *,
        status: str = "ALL",
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]: ...
    def get_line(self, tenant_id: str, line_id: str) -> dict[str, Any] | None: ...
    def add_line(self, tenant_id: str, line_id: str, line: dict[str, Any]) -> bool: ...
    def save_decision(self, tenant_id: str, line_id: str, decision: dict[str, Any]) -> None: ...
    def save_candidate_rule(self, tenant_id: str, rule: dict[str, Any]) -> None: ...


class InMemoryArbitrationStore:
    def __init__(self, rule_writer: ObsidianCandidateRuleWriter | None = None) -> None:
        self.rule_writer = rule_writer
        self.written_rule_paths: list[Path] = []
        self.lines: dict[tuple[str, str], dict[str, Any]] = {}
        self.decisions: dict[tuple[str, str], dict[str, Any]] = {}
        self.candidate_rules: list[tuple[str, dict[str, Any]]] = []

    def add_line(self, tenant_id: str, line_id: str, line: dict[str, Any]) -> bool:
        """Insert a line unless it already exists (never overwrites); True if inserted."""
        if (tenant_id, line_id) in self.lines:
            return False
        self.lines[(tenant_id, line_id)] = {**line, "status": line.get("status") or "PENDING"}
        return True

    def get_line(self, tenant_id: str, line_id: str) -> dict[str, Any] | None:
        return self.lines.get((tenant_id, line_id))

    def list_lines(
        self,
        tenant_id: str,
        *,
        status: str = "ALL",
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return one page of the tenant's lines merged with their decision, plus the total."""
        needle = search.strip().casefold() if search else ""
        rows: list[dict[str, Any]] = []
        for (owner, line_id), line in self.lines.items():
            if owner != tenant_id:
                continue
            decision = self.decisions.get((owner, line_id), {})
            row = {**line, **{k: v for k, v in decision.items() if v is not None}}
            row["line_id"] = line_id
            row["raw_status"] = decision.get("status") or line.get("status") or "PENDING"
            row["arbitrated_at"] = decision.get("arbitrated_at") or line.get("arbitrated_at")
            if status != "ALL" and row["raw_status"] != status:
                continue
            if needle and not any(
                needle in str(row.get(key) or "").casefold()
                for key in ("label", "counterparty", "invoice_id")
            ):
                continue
            rows.append(row)
        rows.sort(key=lambda r: (str(r.get("operation_date") or ""), r["line_id"]))
        return rows[offset : offset + limit], len(rows)

    def save_decision(self, tenant_id: str, line_id: str, decision: dict[str, Any]) -> None:
        self.decisions[(tenant_id, line_id)] = decision

    def save_candidate_rule(self, tenant_id: str, rule: dict[str, Any]) -> None:
        if self.rule_writer is not None:
            self.written_rule_paths.append(self.rule_writer.write(tenant_id, rule))
        self.candidate_rules.append((tenant_id, rule))


_default_store = InMemoryArbitrationStore(
    ObsidianCandidateRuleWriter(Path("knowledge") / "rules")
)


def get_arbitration_store() -> ArbitrationStore:
    """In-memory fallback by default; SQLite when ``DSH_ACCOUNTING_STORE=sqlite``."""
    if accounting_backend_is_sqlite():
        return get_shared_sqlite_store()
    return _default_store


router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


def _tenant_of(request: Request) -> str:
    tenant = getattr(request.state, "tenant_id", None)
    if not isinstance(tenant, str) or not tenant:
        raise HTTPException(status_code=400, detail="INVALID_OR_MISSING_TENANT_ID")
    return tenant


def _build_candidate_rule(tenant_id: str, line: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "obsidian_rule_candidate",
        "status": "CANDIDATE",
        "tenant_id": tenant_id,
        "counterparty": line.get("counterparty"),
        "action": decision["action"],
        "debit_account": decision.get("debit_account"),
        "credit_account": decision.get("credit_account"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _to_view(row: dict[str, Any]) -> ReconciliationLineView:
    def text(key: str) -> str | None:
        value = row.get(key)
        return None if value is None else str(value)

    invoice_id = text("invoice_id")
    invoice_url = text("invoice_url")
    if invoice_url is None and invoice_id:
        invoice_url = f"/invoices/{quote(invoice_id, safe='')}/document"
    reason, arbitrated_at = text("reason"), text("arbitrated_at")
    metadata = (
        ArbitrationMetadata(reason=reason, arbitrated_at=arbitrated_at)
        if reason or arbitrated_at
        else None
    )
    return ReconciliationLineView(
        line_id=row["line_id"],
        operation_date=text("operation_date"),
        label=text("label"),
        debit_amount=text("debit_amount"),
        credit_amount=text("credit_amount"),
        currency=text("currency"),
        status=_VIEW_STATUS.get(row["raw_status"], "PENDING"),
        counterparty=text("counterparty"),
        invoice_id=invoice_id,
        invoice_url=invoice_url,
        debit_account=text("debit_account"),
        credit_account=text("credit_account"),
        vat_amount=text("vat_amount"),
        arbitration_metadata=metadata,
    )


class ImportResult(BaseModel):
    imported: int
    skipped: int
    tenant_id: str


_MULTIPART_OVERHEAD = 64 * 1024


def _import_error(exc: BankStatementError) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": exc.code, "row": exc.row})


@router.post("/import", response_model=ImportResult)
async def import_bank_statement(
    request: Request,
    delimiter: str = Query(";", min_length=1, max_length=1),
    store: ArbitrationStore = Depends(get_arbitration_store),
) -> ImportResult:
    """Import a bank statement as ``PENDING`` lines.

    Accepts ``multipart/form-data`` (``file`` = CSV) or JSON ``{"lines": [...]}``.
    Lines already present for the tenant are skipped, never overwritten.
    """
    tenant_id = _tenant_of(request)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BYTES + _MULTIPART_OVERHEAD:
        raise HTTPException(status_code=413, detail="PAYLOAD_TOO_LARGE")
    content_type = request.headers.get("content-type", "").split(";")[0].strip().casefold()

    claimed_tenant: Any = None
    try:
        if content_type == "multipart/form-data":
            form = await request.form()
            upload = form.get("file")
            if upload is None or isinstance(upload, str):
                raise HTTPException(status_code=422, detail="MISSING_FILE")
            claimed_tenant = form.get("tenant_id")
            raw = await upload.read(MAX_BYTES + 1)
            chosen = form.get("delimiter")
            if isinstance(chosen, str) and chosen:
                delimiter = "\t" if chosen == "\\t" else chosen
            if claimed_tenant is not None and claimed_tenant != tenant_id:
                raise HTTPException(status_code=403, detail="TENANT_MISMATCH")
            lines = parse_bank_statement_csv(raw, delimiter, tenant_id=tenant_id)
        elif content_type == "application/json":
            try:
                payload = await request.json()
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="INVALID_JSON") from exc
            if not isinstance(payload, dict) or not isinstance(payload.get("lines"), list):
                raise HTTPException(status_code=422, detail="INVALID_PAYLOAD")
            claimed_tenant = payload.get("tenant_id")
            if claimed_tenant is not None and claimed_tenant != tenant_id:
                raise HTTPException(status_code=403, detail="TENANT_MISMATCH")
            lines = normalize_bank_rows(payload["lines"], tenant_id)
        else:
            raise HTTPException(status_code=415, detail="UNSUPPORTED_MEDIA_TYPE")
    except BankStatementError as exc:
        raise _import_error(exc) from exc

    def persist() -> tuple[int, int]:
        imported = skipped = 0
        for line in lines:
            if store.add_line(tenant_id, line["line_id"], line):
                imported += 1
            else:
                skipped += 1
        return imported, skipped

    try:
        imported, skipped = await run_in_threadpool(persist)
    except AccountingStoreError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return ImportResult(imported=imported, skipped=skipped, tenant_id=tenant_id)


@router.get("/lines", response_model=ReconciliationLinesPage)
def list_reconciliation_lines(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: LineStatusFilter = LineStatusFilter.ALL,
    search: str | None = Query(None, max_length=200),
    store: ArbitrationStore = Depends(get_arbitration_store),
) -> ReconciliationLinesPage:
    tenant_id = _tenant_of(request)
    rows, total = store.list_lines(
        tenant_id, status=status.value, search=search, limit=limit, offset=offset
    )
    return ReconciliationLinesPage(
        items=[_to_view(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.post("/arbitrate", response_model=ArbitrationResult)
def arbitrate(
    body: ArbitrationRequest,
    request: Request,
    store: ArbitrationStore = Depends(get_arbitration_store),
) -> ArbitrationResult:
    tenant_id = _tenant_of(request)
    if body.tenant_id is not None and body.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="TENANT_MISMATCH")

    line = store.get_line(tenant_id, body.line_id)
    if line is None:
        raise HTTPException(status_code=404, detail="LINE_NOT_FOUND")
    if line.get("status") not in (None, "PENDING"):
        raise HTTPException(status_code=409, detail="LINE_ALREADY_ARBITRATED")

    decision: dict[str, Any] = {
        "action": body.action.value,
        "reason": body.reason,
        "arbitrated_at": datetime.now(timezone.utc).isoformat(),
    }
    if body.action is ArbitrationAction.PERSONAL_EXPENSE:
        decision.update(
            status="BOOKED_PERSONAL",
            debit_account=body.debit_account or PERSONAL_DEBIT_ACCOUNT,
            credit_account=body.credit_account or PERSONAL_CREDIT_ACCOUNT,
            vat_amount="0.00",
        )
    elif body.action is ArbitrationAction.VALIDATE_WITHOUT_INVOICE:
        decision.update(
            status="RELEASED_WITHOUT_INVOICE",
            debit_account=body.debit_account,
            credit_account=body.credit_account,
        )
    else:
        decision.update(
            status="INVOICE_ATTACHED",
            invoice_id=body.invoice_id,
            debit_account=body.debit_account,
            credit_account=body.credit_account,
        )

    candidate = None
    if body.remember_rule:
        candidate = _build_candidate_rule(tenant_id, line, decision)
        try:
            store.save_candidate_rule(tenant_id, candidate)
        except RuleRegistryError as exc:
            raise HTTPException(status_code=400, detail=exc.code) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="INVALID_CANDIDATE_RULE") from exc

    try:
        store.save_decision(tenant_id, body.line_id, decision)
    except LineNotFoundError as exc:
        raise HTTPException(status_code=404, detail="LINE_NOT_FOUND") from exc
    except LineAlreadyArbitratedError as exc:
        raise HTTPException(status_code=409, detail="LINE_ALREADY_ARBITRATED") from exc
    return ArbitrationResult(
        line_id=body.line_id,
        tenant_id=tenant_id,
        candidate_rule=candidate,
        **decision,
    )
