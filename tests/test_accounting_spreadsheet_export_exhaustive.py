"""P12.2 - Exhaustive offline tests of the spreadsheet (CSV) accounting export."""

from __future__ import annotations

import csv
from io import StringIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.middleware.tenant_middleware import TenantContextMiddleware
from backend.app.routers.accounting_export import router
from backend.app.routers.reconciliation_arbitration import (
    InMemoryArbitrationStore,
    get_arbitration_store,
)
from services.accounting_spreadsheet_export_service import (
    RECONCILIATION_COLUMNS,
    UTF8_BOM,
    generate_arbitrations_summary_csv,
    generate_reconciliation_csv,
)

HEADER = (
    "Date;Libellé;Débit;Crédit;Statut;Tiers;N° Facture;Compte Débit;"
    "Compte Crédit;TVA;Lien Justificatif;Motif Arbitrage"
)


def parse(content: str) -> list[list[str]]:
    return list(csv.reader(StringIO(content.lstrip(UTF8_BOM)), delimiter=";"))


def entries() -> list[dict]:
    return [
        {
            "operation_date": "2026-01-10",
            "label": "Achat fournitures",
            "debit_amount": "120.50",
            "status": "MATCHED",
            "counterparty": "ACME",
            "invoice_id": "FA-1",
            "invoice_url": "/invoices/FA-1/document",
            "debit_account": "606300",
            "credit_account": "512000",
            "vat_amount": "20.08",
        },
        {
            "operation_date": "2026-01-12",
            "label": "Restaurant perso",
            "debit_amount": "45",
            "status": "ARBITRATED_PERSONAL",
            "debit_account": "108000",
            "credit_account": "455000",
            "arbitration_metadata": {"reason": "Dépense personnelle"},
        },
        {
            "operation_date": "2026-01-15",
            "label": "Péage",
            "debit_amount": "8,40",
            "status": "ARBITRATED_NO_INVOICE",
            "debit_account": "625100",
            "credit_account": "512000",
            "reason": "Ticket perdu",
        },
    ]


def test_header_and_delimiter() -> None:
    content = generate_reconciliation_csv(entries())
    assert content.splitlines()[0] == HEADER
    assert tuple(HEADER.split(";")) == RECONCILIATION_COLUMNS
    assert all(len(r) == 12 for r in parse(content))


def test_bom_optional_and_custom_delimiter_and_decimal_point() -> None:
    assert not generate_reconciliation_csv([]).startswith(UTF8_BOM)
    assert generate_reconciliation_csv([], bom=True).startswith(UTF8_BOM)
    content = generate_reconciliation_csv(entries()[:1], delimiter="\t", decimal_separator=".")
    assert content.splitlines()[0].count("\t") == 11
    assert "120.50" in content
    with pytest.raises(ValueError):
        generate_reconciliation_csv([], delimiter=";;")


def test_decimal_comma_default() -> None:
    rows = parse(generate_reconciliation_csv(entries()))
    assert rows[1][2] == "120,50"
    assert rows[1][9] == "20,08"
    assert rows[3][2] == "8,40"


def test_receipt_links_and_pcg_accounts_present() -> None:
    rows = parse(generate_reconciliation_csv(entries()))
    assert rows[1][10] == "/invoices/FA-1/document"
    assert (rows[1][7], rows[1][8]) == ("606300", "512000")
    assert (rows[2][7], rows[2][8]) == ("108000", "455000")
    assert rows[2][11] == "Dépense personnelle"
    assert rows[3][11] == "Ticket perdu"


def test_formula_injection_neutralised() -> None:
    rows = parse(generate_reconciliation_csv([{"label": "=HYPERLINK(1)", "counterparty": "@x"}]))
    assert rows[1][1] == "'=HYPERLINK(1)"
    assert rows[1][5] == "'@x"


def test_arbitrations_summary_only_targets_arbitrations() -> None:
    rows = parse(generate_arbitrations_summary_csv(entries()))
    assert [r[1] for r in rows[1:]] == ["Restaurant perso", "Péage"]
    only_account = {"label": "x", "debit_account": "108000", "status": "PENDING"}
    assert len(parse(generate_arbitrations_summary_csv([only_account]))) == 2
    assert len(parse(generate_arbitrations_summary_csv(entries()[:1]))) == 1


@pytest.fixture()
def store() -> InMemoryArbitrationStore:
    s = InMemoryArbitrationStore()
    s.lines[("CLIENT_01", "L1")] = {
        "operation_date": "2026-01-10",
        "label": "Ligne client 1",
        "debit_amount": "10.00",
        "invoice_id": "FA-1",
        "debit_account": "606300",
        "credit_account": "512000",
    }
    s.lines[("CLIENT_01", "L2")] = {
        "operation_date": "2026-03-10",
        "label": "Perso client 1",
        "debit_amount": "5.00",
        "debit_account": "108000",
        "credit_account": "455000",
        "status": "BOOKED_PERSONAL",
    }
    s.lines[("CLIENT_02", "L3")] = {
        "operation_date": "2026-01-10",
        "label": "Ligne client 2 SECRET",
        "debit_amount": "99.00",
    }
    return s


@pytest.fixture()
def client(store: InMemoryArbitrationStore) -> TestClient:
    app = FastAPI()
    app.add_middleware(TenantContextMiddleware)
    app.include_router(router)
    app.dependency_overrides[get_arbitration_store] = lambda: store
    return TestClient(app)


def get(client: TestClient, tenant: str, **params: str):
    return client.get(
        "/accounting/export/spreadsheet", params=params, headers={"X-Tenant-ID": tenant}
    )


def test_endpoint_reconciliation_and_headers(client: TestClient) -> None:
    r = get(client, "CLIENT_01", date_from="2026-01-01", date_to="2026-12-31")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"] == (
        'attachment; filename="REVISION_CLIENT_01_reconciliation_2026-01-01_2026-12-31.csv"'
    )
    assert r.content.startswith(UTF8_BOM.encode("utf-8"))
    rows = parse(r.content.decode("utf-8"))
    assert ";".join(rows[0]) == HEADER
    assert [x[1] for x in rows[1:]] == ["Ligne client 1", "Perso client 1"]
    assert rows[1][10] == "/invoices/FA-1/document"


def test_endpoint_arbitrations_scope_and_default_filename(client: TestClient) -> None:
    r = get(client, "CLIENT_01", scope="arbitrations")
    assert r.headers["content-disposition"] == (
        'attachment; filename="REVISION_CLIENT_01_arbitrations_all_all.csv"'
    )
    rows = parse(r.text)
    assert [x[1] for x in rows[1:]] == ["Perso client 1"]


def test_endpoint_date_filter(client: TestClient) -> None:
    rows = parse(get(client, "CLIENT_01", date_to="2026-02-01").text)
    assert [x[1] for x in rows[1:]] == ["Ligne client 1"]


def test_tenant_isolation(client: TestClient) -> None:
    one = get(client, "CLIENT_01").text
    two = get(client, "CLIENT_02").text
    assert "SECRET" not in one and "Ligne client 1" not in two
    assert "Ligne client 2 SECRET" in two
    assert "CLIENT_02" not in get(client, "CLIENT_01").headers["content-disposition"]


def test_missing_tenant_rejected_400(client: TestClient) -> None:
    r = client.get("/accounting/export/spreadsheet")
    assert r.status_code == 400
    assert r.json() == {"detail": "INVALID_OR_MISSING_TENANT_ID"}


def test_missing_tenant_state_rejected_400(store: InMemoryArbitrationStore) -> None:
    app = FastAPI()  # no middleware: request.state.tenant_id absent
    app.include_router(router)
    app.dependency_overrides[get_arbitration_store] = lambda: store
    assert TestClient(app).get("/accounting/export/spreadsheet").status_code == 400


def test_invalid_params_rejected(client: TestClient) -> None:
    assert get(client, "CLIENT_01", scope="x").status_code == 422
    assert get(client, "CLIENT_01", format="xlsx").status_code == 422
    r = get(client, "CLIENT_01", date_from="2026-02-01", date_to="2026-01-01")
    assert r.status_code == 400
