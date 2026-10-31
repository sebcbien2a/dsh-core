"""P11.1 - Manual (HITL) arbitration endpoint for bank reconciliation.

The tenant is taken exclusively from ``request.state.tenant_id`` (set by
``TenantContextMiddleware``); a tenant carried in the body is only accepted if
it matches. A line owned by another tenant is reported as not found.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class ArbitrationStore(Protocol):
    def get_line(self, tenant_id: str, line_id: str) -> dict[str, Any] | None: ...
    def save_decision(self, tenant_id: str, line_id: str, decision: dict[str, Any]) -> None: ...
    def save_candidate_rule(self, tenant_id: str, rule: dict[str, Any]) -> None: ...


class InMemoryArbitrationStore:
    def __init__(self) -> None:
        self.lines: dict[tuple[str, str], dict[str, Any]] = {}
        self.decisions: dict[tuple[str, str], dict[str, Any]] = {}
        self.candidate_rules: list[tuple[str, dict[str, Any]]] = []

    def get_line(self, tenant_id: str, line_id: str) -> dict[str, Any] | None:
        return self.lines.get((tenant_id, line_id))

    def save_decision(self, tenant_id: str, line_id: str, decision: dict[str, Any]) -> None:
        self.decisions[(tenant_id, line_id)] = decision

    def save_candidate_rule(self, tenant_id: str, rule: dict[str, Any]) -> None:
        self.candidate_rules.append((tenant_id, rule))


_default_store = InMemoryArbitrationStore()


def get_arbitration_store() -> ArbitrationStore:
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

    decision: dict[str, Any] = {"action": body.action.value, "reason": body.reason}
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
        store.save_candidate_rule(tenant_id, candidate)

    store.save_decision(tenant_id, body.line_id, decision)
    return ArbitrationResult(
        line_id=body.line_id,
        tenant_id=tenant_id,
        candidate_rule=candidate,
        **decision,
    )
