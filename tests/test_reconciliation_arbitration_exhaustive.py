"""P11.1 - Exhaustive tests of the HITL arbitration endpoint (offline)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.middleware.tenant_middleware import TenantContextMiddleware
from backend.app.routers.reconciliation_arbitration import (
    ArbitrationRequest,
    InMemoryArbitrationStore,
    get_arbitration_store,
    router,
)


@pytest.fixture()
def store() -> InMemoryArbitrationStore:
    s = InMemoryArbitrationStore()
    s.lines[("tenant-a", "L1")] = {"status": "PENDING", "counterparty": "ACME"}
    s.lines[("tenant-b", "L2")] = {"status": "PENDING", "counterparty": "OTHER"}
    return s


@pytest.fixture()
def client(store: InMemoryArbitrationStore) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.add_middleware(TenantContextMiddleware)
    app.dependency_overrides[get_arbitration_store] = lambda: store
    return TestClient(app)


def post(client: TestClient, payload: dict, tenant: str | None = "tenant-a"):
    headers = {"X-Tenant-ID": tenant} if tenant else {}
    return client.post("/reconciliation/arbitrate", json=payload, headers=headers)


def test_personal_expense_defaults_no_vat(client, store):
    r = post(client, {"line_id": "L1", "action": "PERSONAL_EXPENSE"})
    assert r.status_code == 200
    body = r.json()
    assert body["debit_account"] == "108000"
    assert body["credit_account"] == "455000"
    assert body["vat_amount"] == "0.00"
    assert body["status"] == "BOOKED_PERSONAL"
    assert store.decisions[("tenant-a", "L1")]["status"] == "BOOKED_PERSONAL"


def test_validate_without_invoice_traces_reason(client, store):
    r = post(client, {"line_id": "L1", "action": "VALIDATE_WITHOUT_INVOICE", "reason": "Ticket perdu"})
    assert r.status_code == 200
    assert r.json()["status"] == "RELEASED_WITHOUT_INVOICE"
    assert r.json()["reason"] == "Ticket perdu"
    assert store.decisions[("tenant-a", "L1")]["reason"] == "Ticket perdu"


def test_validate_without_invoice_requires_reason(client):
    assert post(client, {"line_id": "L1", "action": "VALIDATE_WITHOUT_INVOICE"}).status_code == 422


def test_attach_invoice_links_invoice_id(client, store):
    r = post(client, {"line_id": "L1", "action": "ATTACH_INVOICE", "invoice_id": "INV-9", "debit_account": "627000"})
    assert r.status_code == 200
    assert r.json()["invoice_id"] == "INV-9"
    assert r.json()["debit_account"] == "627000"
    assert store.decisions[("tenant-a", "L1")]["invoice_id"] == "INV-9"


def test_attach_requires_invoice_id(client):
    assert post(client, {"line_id": "L1", "action": "ATTACH_INVOICE"}).status_code == 422


def test_invoice_id_forbidden_for_other_actions(client):
    assert post(client, {"line_id": "L1", "action": "PERSONAL_EXPENSE", "invoice_id": "X"}).status_code == 422


@pytest.mark.parametrize("acc", ["12345", "1234567", "62700a", "", "627 00", "627000\n"])
def test_invalid_accounts_rejected(client, acc):
    r = post(client, {"line_id": "L1", "action": "PERSONAL_EXPENSE", "debit_account": acc})
    assert r.status_code == 422


@pytest.mark.parametrize("acc", ["108000", "627000", "000001"])
def test_valid_accounts_accepted(acc):
    req = ArbitrationRequest(line_id="L1", action="PERSONAL_EXPENSE", credit_account=acc)
    assert req.credit_account == acc


def test_unknown_action_and_extra_field_rejected(client):
    assert post(client, {"line_id": "L1", "action": "DELETE"}).status_code == 422
    assert post(client, {"line_id": "L1", "action": "PERSONAL_EXPENSE", "x": 1}).status_code == 422


def test_missing_tenant_rejected_by_middleware(client):
    r = post(client, {"line_id": "L1", "action": "PERSONAL_EXPENSE"}, tenant=None)
    assert r.status_code == 400
    assert r.json()["detail"] == "INVALID_OR_MISSING_TENANT_ID"


def test_body_tenant_mismatch_rejected(client, store):
    r = post(client, {"line_id": "L1", "action": "PERSONAL_EXPENSE", "tenant_id": "tenant-b"})
    assert r.status_code == 403
    assert not store.decisions


def test_body_tenant_match_accepted(client):
    r = post(client, {"line_id": "L1", "action": "PERSONAL_EXPENSE", "tenant_id": "tenant-a"})
    assert r.status_code == 200


def test_cross_tenant_line_not_found(client, store):
    r = post(client, {"line_id": "L2", "action": "PERSONAL_EXPENSE"}, tenant="tenant-a")
    assert r.status_code == 404
    assert not store.decisions
    assert store.lines[("tenant-b", "L2")]["status"] == "PENDING"


def test_unknown_line_404(client):
    assert post(client, {"line_id": "NOPE", "action": "PERSONAL_EXPENSE"}).status_code == 404


def test_double_arbitration_conflict(client, store):
    store.lines[("tenant-a", "L1")]["status"] = "BOOKED_PERSONAL"
    assert post(client, {"line_id": "L1", "action": "PERSONAL_EXPENSE"}).status_code == 409


def test_remember_rule_generates_candidate(client, store):
    r = post(client, {"line_id": "L1", "action": "PERSONAL_EXPENSE", "remember_rule": True})
    rule = r.json()["candidate_rule"]
    assert rule["status"] == "CANDIDATE"
    assert rule["tenant_id"] == "tenant-a"
    assert rule["counterparty"] == "ACME"
    assert rule["debit_account"] == "108000"
    assert store.candidate_rules[0][0] == "tenant-a"


def test_no_rule_without_remember(client, store):
    r = post(client, {"line_id": "L1", "action": "PERSONAL_EXPENSE"})
    assert r.json()["candidate_rule"] is None
    assert store.candidate_rules == []


def test_model_direct_validation_errors():
    with pytest.raises(ValidationError):
        ArbitrationRequest(line_id="", action="PERSONAL_EXPENSE")
