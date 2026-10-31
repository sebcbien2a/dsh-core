"""P11.1 - Exhaustive tests of the HITL arbitration endpoint (offline)."""

from __future__ import annotations

import re

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from services.rule_registry_service import (
    ObsidianCandidateRuleWriter,
    RuleRegistryError,
    RuleRegistryService,
)

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


@pytest.fixture()
def rules_root(tmp_path):
    return tmp_path / "rules"


@pytest.fixture()
def disk_store(rules_root) -> InMemoryArbitrationStore:
    s = InMemoryArbitrationStore(ObsidianCandidateRuleWriter(rules_root))
    s.lines[("CLIENT_01", "L1")] = {"status": "PENDING", "counterparty": "ACME Corp"}
    s.lines[("CLIENT_02", "L2")] = {"status": "PENDING", "counterparty": "Other"}
    return s


@pytest.fixture()
def disk_client(disk_store) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.add_middleware(TenantContextMiddleware)
    app.dependency_overrides[get_arbitration_store] = lambda: disk_store
    return TestClient(app)


def test_candidate_rule_written_to_disk(disk_client, disk_store, rules_root):
    r = post(
        disk_client,
        {"line_id": "L1", "action": "ATTACH_INVOICE", "invoice_id": "I1",
         "debit_account": "627000", "credit_account": "512000", "remember_rule": True},
        tenant="CLIENT_01",
    )
    assert r.status_code == 200
    files = list((rules_root / "tenants" / "CLIENT_01").glob("*.md"))
    assert len(files) == 1 and disk_store.written_rule_paths == files
    text = files[0].read_text(encoding="utf-8")
    assert text.startswith("---\n")
    data = yaml.safe_load(text.split("---\n")[1])
    assert re.fullmatch(r"R-HITL-CLIENT_01-acme-corp-[0-9a-f]{8}", data["rule_id"])
    assert files[0].name == f"{data['rule_id']}.md"
    assert data["version"] == "1.0.0"
    assert data["status"] == "candidate"
    assert data["domain"] == "accounting"
    assert data["applies_to_nodes"] == ["rule_evaluation"]
    assert data["conditions"] == [{"field": "counterparty", "operator": "contains", "value": "ACME Corp"}]
    assert data["deterministic_actions"] == [
        {"action": "assign_accounts",
         "parameters": {"debit_account": "627000", "credit_account": "512000"}}
    ]


def test_candidate_rule_readable_by_registry(rules_root):
    writer_c = ObsidianCandidateRuleWriter(rules_root)
    writer_a = ObsidianCandidateRuleWriter(rules_root, status="approved")
    cand = {"counterparty": "ACME", "action": "PERSONAL_EXPENSE",
            "debit_account": "108000", "credit_account": "455000"}
    path_c = writer_c.write("CLIENT_01", cand)
    path_a = writer_a.write("CLIENT_01", cand, operator="eq")

    registry = RuleRegistryService()
    rules = registry.load_tenant_rules(rules_root, "CLIENT_01")
    assert [r.rule_id for r in rules] == [path_a.stem]
    assert rules[0].conditions[0].operator == "eq"
    tenant_layer = registry._tenant_layers["CLIENT_01"]
    assert path_c in tenant_layer.ignored_rules
    assert tenant_layer.ignored_rules[path_c] == "candidate"


def test_candidate_rule_isolated_between_tenants(disk_client, rules_root):
    post(disk_client, {"line_id": "L1", "action": "PERSONAL_EXPENSE", "remember_rule": True}, tenant="CLIENT_01")
    assert len(list((rules_root / "tenants" / "CLIENT_01").glob("*.md"))) == 1
    assert not (rules_root / "tenants" / "CLIENT_02").exists()

    ObsidianCandidateRuleWriter(rules_root, status="approved").write(
        "CLIENT_01", {"counterparty": "ACME", "debit_account": "1"}
    )
    registry = RuleRegistryService()
    assert registry.load_tenant_rules(rules_root, "CLIENT_02") == []
    assert len(registry.load_tenant_rules(rules_root, "CLIENT_01")) == 1


@pytest.mark.parametrize("tenant", ["..", ".", "../x", "a/b", "a\\b", "", "x" * 65, "t\n"])
def test_writer_rejects_traversal(rules_root, tenant):
    writer = ObsidianCandidateRuleWriter(rules_root)
    with pytest.raises(RuleRegistryError):
        writer.write(tenant, {"counterparty": "ACME"})
    assert not rules_root.exists() or not any(rules_root.rglob("*.md"))


def test_writer_rejects_symlink_escape(rules_root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (rules_root / "tenants").mkdir(parents=True)
    link = rules_root / "tenants" / "EVIL"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(RuleRegistryError):
        ObsidianCandidateRuleWriter(rules_root).write("EVIL", {"counterparty": "ACME"})
    assert list(outside.iterdir()) == []


def test_writer_validations(rules_root):
    with pytest.raises(ValueError):
        ObsidianCandidateRuleWriter(rules_root, status="draft")
    writer = ObsidianCandidateRuleWriter(rules_root)
    with pytest.raises(ValueError):
        writer.write("CLIENT_01", {"counterparty": None})
    with pytest.raises(ValueError):
        writer.write("CLIENT_01", {"counterparty": "A"}, operator="regex")


def test_missing_counterparty_returns_422_and_no_decision(disk_client, disk_store):
    disk_store.lines[("CLIENT_01", "L3")] = {"status": "PENDING"}
    r = post(disk_client, {"line_id": "L3", "action": "PERSONAL_EXPENSE", "remember_rule": True}, tenant="CLIENT_01")
    assert r.status_code == 422
    assert ("CLIENT_01", "L3") not in disk_store.decisions


def get_lines(client, tenant="CLIENT_01", **params):
    return client.get("/reconciliation/lines", params=params, headers={"X-Tenant-ID": tenant})


@pytest.fixture()
def lines_store() -> InMemoryArbitrationStore:
    s = InMemoryArbitrationStore()
    for i in range(1, 6):
        s.lines[("CLIENT_01", f"L{i}")] = {
            "status": "PENDING", "operation_date": f"2026-01-0{i}", "label": f"VIR ACME {i}",
            "debit_amount": f"{i}0.00", "credit_amount": None, "currency": "EUR",
            "counterparty": "ACME" if i < 5 else "Zeta",
        }
    s.lines[("CLIENT_02", "X1")] = {"status": "PENDING", "label": "SECRET", "counterparty": "ACME"}
    return s


@pytest.fixture()
def lines_client(lines_store) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.add_middleware(TenantContextMiddleware)
    app.dependency_overrides[get_arbitration_store] = lambda: lines_store
    return TestClient(app)


def test_lines_pagination(lines_client):
    r = get_lines(lines_client, limit=2, offset=1)
    body = r.json()
    assert r.status_code == 200
    assert [i["line_id"] for i in body["items"]] == ["L2", "L3"]
    assert body["total"] == 5 and body["limit"] == 2 and body["offset"] == 1
    assert get_lines(lines_client, offset=10).json()["items"] == []
    assert get_lines(lines_client).json()["limit"] == 50


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"status": "BOGUS"}])
def test_lines_invalid_params(lines_client, params):
    assert get_lines(lines_client, **params).status_code == 422


def test_lines_requires_tenant(lines_client):
    assert lines_client.get("/reconciliation/lines").status_code == 400


def test_lines_tenant_isolation(lines_client):
    ids = {i["line_id"] for i in get_lines(lines_client, limit=200).json()["items"]}
    assert ids == {"L1", "L2", "L3", "L4", "L5"}
    other = get_lines(lines_client, tenant="CLIENT_02").json()
    assert [i["line_id"] for i in other["items"]] == ["X1"]
    assert get_lines(lines_client, search="SECRET").json()["total"] == 0


def test_lines_search(lines_client):
    assert get_lines(lines_client, search="zeta").json()["total"] == 1
    assert get_lines(lines_client, search="VIR ACME 3").json()["items"][0]["line_id"] == "L3"
    assert get_lines(lines_client, search="nothing").json()["total"] == 0


def test_lines_status_filter_and_arbitrated_view(lines_client):
    h = {"X-Tenant-ID": "CLIENT_01"}
    assert lines_client.post("/reconciliation/arbitrate", headers=h, json={
        "line_id": "L1", "action": "ATTACH_INVOICE", "invoice_id": "INV-9",
        "debit_account": "627000", "credit_account": "512000"}).status_code == 200
    assert lines_client.post("/reconciliation/arbitrate", headers=h, json={
        "line_id": "L2", "action": "PERSONAL_EXPENSE"}).status_code == 200
    assert lines_client.post("/reconciliation/arbitrate", headers=h, json={
        "line_id": "L3", "action": "VALIDATE_WITHOUT_INVOICE", "reason": "perdu"}).status_code == 200

    assert get_lines(lines_client, status="PENDING").json()["total"] == 2
    assert get_lines(lines_client, status="ALL").json()["total"] == 5

    matched = get_lines(lines_client, status="INVOICE_ATTACHED").json()["items"][0]
    assert matched["status"] == "MATCHED"
    assert matched["invoice_id"] == "INV-9" and matched["invoice_url"].endswith("INV-9/document")
    assert (matched["debit_account"], matched["credit_account"]) == ("627000", "512000")
    assert matched["currency"] == "EUR" and matched["label"] == "VIR ACME 1"
    assert get_lines(lines_client, search="INV-9").json()["total"] == 1

    personal = get_lines(lines_client, status="BOOKED_PERSONAL").json()["items"][0]
    assert personal["status"] == "ARBITRATED_PERSONAL"
    assert personal["vat_amount"] == "0.00" and personal["debit_account"] == "108000"
    assert personal["arbitration_metadata"]["arbitrated_at"]

    free = get_lines(lines_client, status="RELEASED_WITHOUT_INVOICE").json()["items"][0]
    assert free["status"] == "ARBITRATED_NO_INVOICE"
    assert free["arbitration_metadata"]["reason"] == "perdu"
    assert free["invoice_url"] is None
    assert get_lines(lines_client, tenant="CLIENT_02").json()["items"][0]["status"] == "PENDING"
