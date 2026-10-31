"""End-to-end invoice rule evaluation and bank reconciliation flow."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.middleware.tenant_middleware import TenantContextMiddleware
from backend.app.routers.reconciliation_arbitration import (
    InMemoryArbitrationStore,
    get_arbitration_store,
    router,
)
from schemas.invoice_schema import InvoiceSchema
from services.rule_registry_service import RuleRegistryService

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COMMON_RULES = REPOSITORY_ROOT / "knowledge" / "rules" / "common"
TENANT_ID = "TENANT_LAB_01"
INVOICE_ID = "f1b3c108-c961-43d9-aec2-d54403fc6395"


def test_spectrophotometer_invoice_is_reconciled_to_bank_debit() -> None:
    invoice = InvoiceSchema.model_validate(
        {
            "document_id": INVOICE_ID,
            "document_type": "INVOICE",
            "direction": "PURCHASE",
            "supplier": {"name": "SPECTRO LAB SAS"},
            "reference": {
                "invoice_number": "SL-2026-0042",
                "issue_date": "2026-10-15",
            },
            "lines": [
                {
                    "description": "Location spectrophotomètre",
                    "quantite": "1",
                    "prix_unitaire_ht": "375.00",
                    "total_ht": "375.00",
                    "taux_tva": "20",
                }
            ],
            "amount": {
                "currency": "EUR",
                "net_amount": "375.00",
                "tax_amount": "75.00",
                "gross_amount": "450.00",
            },
            "validation_status": "VALIDATED",
        }
    )
    assert invoice.amount.gross_amount == 450
    assert invoice.supplier is not None
    assert invoice.supplier.name == "SPECTRO LAB SAS"

    registry = RuleRegistryService()
    registry.load_rules(COMMON_RULES)
    applicable_rules = registry.get_applicable_rules("rule_evaluation")
    invoice_line = invoice.lines[0]
    matching_rules = [
        rule
        for rule in applicable_rules
        if all(
            condition.field == "description"
            and condition.operator == "contains"
            and isinstance(condition.value, str)
            and condition.value.casefold() in invoice_line.description.casefold()
            for condition in rule.conditions
        )
    ]
    spectro_rule = next(
        rule for rule in matching_rules if rule.rule_id == "R-LOC-SPECTRO-001"
    )
    account_action = next(
        action
        for action in spectro_rule.deterministic_actions
        if action.action == "set_accounting_account"
    )
    debit_account = account_action.parameters["account"]
    assert debit_account == "613500"

    store = InMemoryArbitrationStore()
    line_id = "BANK-LINE-SPECTRO-001"
    store.lines[(TENANT_ID, line_id)] = {
        "status": "PENDING",
        "operation_date": "2026-10-20",
        "label": "PRELEVEMENT SPECTRO LAB SAS SL-2026-0042",
        "debit_amount": "450.00",
        "credit_amount": None,
        "currency": "EUR",
        "counterparty": "SPECTRO LAB SAS",
    }

    app = FastAPI()
    app.include_router(router)
    app.add_middleware(TenantContextMiddleware)
    app.dependency_overrides[get_arbitration_store] = lambda: store
    client = TestClient(app)
    headers = {"X-Tenant-ID": TENANT_ID}

    arbitration = client.post(
        "/reconciliation/arbitrate",
        headers=headers,
        json={
            "line_id": line_id,
            "action": "ATTACH_INVOICE",
            "invoice_id": str(UUID(INVOICE_ID)),
            "debit_account": debit_account,
            "credit_account": "401000",
        },
    )
    assert arbitration.status_code == 200
    assert arbitration.json()["status"] == "INVOICE_ATTACHED"

    response = client.get("/reconciliation/lines", headers=headers)
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    line = items[0]
    assert line["line_id"] == line_id
    assert line["debit_amount"] == "450.00"
    assert line["currency"] == "EUR"
    assert line["status"] == "MATCHED"
    assert line["counterparty"] == "SPECTRO LAB SAS"
    assert line["debit_account"] == "613500"
    assert line["invoice_id"] == INVOICE_ID
    assert line["invoice_url"] == f"/invoices/{INVOICE_ID}/document"

    other_tenant_response = client.get(
        "/reconciliation/lines", headers={"X-Tenant-ID": "TENANT_OTHER"}
    )
    assert other_tenant_response.status_code == 200
    assert other_tenant_response.json()["total"] == 0
    assert other_tenant_response.json()["items"] == []
