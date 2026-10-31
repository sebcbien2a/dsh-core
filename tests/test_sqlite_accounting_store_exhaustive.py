"""P13.1 - Exhaustive tests of the durable SQLite accounting store (offline)."""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.middleware.tenant_middleware import TenantContextMiddleware
from backend.app.routers import accounting_export, reconciliation_arbitration
from backend.app.routers.accounting_export import get_fec_entry_store
from backend.app.routers.reconciliation_arbitration import get_arbitration_store
from services.fec_export_service import FecEntry
from services.sqlite_accounting_store import (
    AccountingStoreError,
    LineAlreadyArbitratedError,
    LineNotFoundError,
    SqliteAccountingStore,
)


def make_entry(num: str, day: date, debit: str = "0.00", credit: str = "0.00", **kw) -> FecEntry:
    return FecEntry(
        JournalCode="AC",
        JournalLib="Achats",
        EcritureNum=num,
        EcritureDate=day,
        CompteNum="607000",
        CompteLib="Achats",
        PieceRef="P-" + num,
        PieceDate=day,
        EcritureLib="Facture " + num,
        Debit=Decimal(debit),
        Credit=Decimal(credit),
        **kw,
    )


@pytest.fixture()
def db_file(tmp_path):
    return tmp_path / "accounting" / "store.sqlite3"


@pytest.fixture()
def store(db_file) -> SqliteAccountingStore:
    return SqliteAccountingStore(db_file)


def decision(status="BOOKED_PERSONAL", action="PERSONAL_EXPENSE", **kw):
    return {"action": action, "status": status, "arbitrated_at": "2026-01-02T03:04:05+00:00", **kw}


# -- schema -------------------------------------------------------------------


def test_schema_tables_and_tenant_indexes(store, db_file):
    with sqlite3.connect(db_file) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"accounting_entries", "reconciliation_lines", "arbitration_decisions"} <= tables
        for table in ("accounting_entries", "reconciliation_lines", "arbitration_decisions"):
            indexed = {
                col[2]
                for idx in conn.execute(f"PRAGMA index_list({table})")
                for col in conn.execute(f"PRAGMA index_info({idx[1]})")
            }
            assert "tenant_id" in indexed
        cols = {r[1] for r in conn.execute("PRAGMA table_info(accounting_entries)")}
    assert set(FecEntry.COLUMNS) <= cols and {"tenant_id", "validated", "created_at"} <= cols


# -- entries ------------------------------------------------------------------


def test_entry_roundtrip_all_18_fields(store):
    entry = make_entry(
        "1",
        date(2026, 3, 1),
        debit="1234.50",
        CompAuxNum="C1",
        CompAuxLib="Client",
        EcritureLet="AA",
        DateLet=date(2026, 3, 5),
        ValidDate=date(2026, 3, 2),
        Montantdevise=Decimal("99.90"),
        Idevise="USD",
    )
    store.add_entry("CLIENT_01", entry)
    assert store.list_validated_entries("CLIENT_01", None, None) == [entry]


def test_optional_fields_none_roundtrip(store):
    entry = make_entry("2", date(2026, 3, 1), credit="10.00")
    store.add_entry("CLIENT_01", entry)
    (got,) = store.list_validated_entries("CLIENT_01", None, None)
    assert got.DateLet is None and got.ValidDate is None and got.Montantdevise is None
    assert str(got.Credit) == "10.00"


def test_only_validated_entries_listed(store):
    store.add_entry("CLIENT_01", make_entry("1", date(2026, 1, 1)), validated=False)
    store.add_entry("CLIENT_01", make_entry("2", date(2026, 1, 1)))
    assert [e.EcritureNum for e in store.list_validated_entries("CLIENT_01", None, None)] == ["2"]


def test_date_filters_inclusive_and_insertion_order(store):
    for num, day in (("3", date(2026, 3, 1)), ("1", date(2026, 1, 1)), ("2", date(2026, 2, 1))):
        store.add_entry("CLIENT_01", make_entry(num, day))
    nums = lambda a, b: [  # noqa: E731
        e.EcritureNum for e in store.list_validated_entries("CLIENT_01", a, b)
    ]
    assert nums(None, None) == ["3", "1", "2"]
    assert nums(date(2026, 2, 1), None) == ["3", "2"]
    assert nums(None, date(2026, 2, 1)) == ["1", "2"]
    assert nums(date(2026, 2, 1), date(2026, 2, 1)) == ["2"]
    assert nums(date(2027, 1, 1), None) == []


# -- lines & arbitration -------------------------------------------------------


def test_line_defaults_to_pending_and_roundtrips(store):
    store.add_line(
        "CLIENT_01",
        "L1",
        {"operation_date": "2026-01-05", "label": "Virement", "debit_amount": "10.00",
         "currency": "EUR", "counterparty": "ACME"},
    )
    line = store.get_line("CLIENT_01", "L1")
    assert line["raw_status"] == "PENDING"
    assert line["label"] == "Virement" and line["counterparty"] == "ACME"
    assert store.get_line("CLIENT_01", "NOPE") is None


def test_arbitrate_merges_decision_into_list_lines(store):
    store.add_line("CLIENT_01", "L1", {"operation_date": "2026-01-05", "label": "A"})
    store.add_line("CLIENT_01", "L2", {"operation_date": "2026-01-06", "label": "B"})
    store.arbitrate(
        "CLIENT_01", "L1", decision(debit_account="108000", credit_account="455000",
                                    vat_amount="0.00", reason="perso"),
    )
    rows, total = store.list_lines("CLIENT_01")
    assert total == 2
    first, second = rows
    assert first["raw_status"] == "BOOKED_PERSONAL"
    assert first["debit_account"] == "108000" and first["reason"] == "perso"
    assert first["arbitrated_at"] == "2026-01-02T03:04:05+00:00"
    assert first["label"] == "A"
    assert second["raw_status"] == "PENDING" and second["arbitrated_at"] is None
    assert store.get_line("CLIENT_01", "L1")["raw_status"] == "BOOKED_PERSONAL"


def test_attach_invoice_overrides_line_invoice(store):
    store.add_line("CLIENT_01", "L1", {"invoice_id": "OLD"})
    store.arbitrate("CLIENT_01", "L1",
                    decision("INVOICE_ATTACHED", "ATTACH_INVOICE", invoice_id="INV-9"))
    assert store.get_line("CLIENT_01", "L1")["invoice_id"] == "INV-9"


def test_double_arbitration_and_unknown_line_rejected(store):
    store.add_line("CLIENT_01", "L1", {})
    store.arbitrate("CLIENT_01", "L1", decision())
    with pytest.raises(LineAlreadyArbitratedError):
        store.arbitrate("CLIENT_01", "L1", decision("RELEASED_WITHOUT_INVOICE"))
    with pytest.raises(LineNotFoundError):
        store.arbitrate("CLIENT_01", "ZZ", decision())
    assert store.get_line("CLIENT_01", "L1")["raw_status"] == "BOOKED_PERSONAL"


def test_invalid_decision_rejected(store):
    store.add_line("CLIENT_01", "L1", {})
    with pytest.raises(AccountingStoreError):
        store.arbitrate("CLIENT_01", "L1", {"action": "X"})
    assert store.get_line("CLIENT_01", "L1")["raw_status"] == "PENDING"


def test_status_filter_search_and_pagination(store):
    for i in range(5):
        store.add_line("CLIENT_01", f"L{i}", {"operation_date": f"2026-01-0{i + 1}",
                                              "label": f"Op {i}", "counterparty": "ACME_100%"})
    store.add_line("CLIENT_01", "LX", {"operation_date": "2026-02-01", "label": "Café Über"})
    store.arbitrate("CLIENT_01", "L1", decision())
    assert store.list_lines("CLIENT_01", status="BOOKED_PERSONAL")[1] == 1
    assert store.list_lines("CLIENT_01", status="PENDING")[1] == 5
    assert store.list_lines("CLIENT_01", search="acme_100%")[1] == 5
    assert store.list_lines("CLIENT_01", search="ACME_100\\")[1] == 0
    assert store.list_lines("CLIENT_01", search="%")[1] == 5  # literal, not a wildcard
    assert store.list_lines("CLIENT_01", search="CAFÉ")[1] == 1
    rows, total = store.list_lines("CLIENT_01", limit=2, offset=2)
    assert total == 6 and [r["line_id"] for r in rows] == ["L2", "L3"]
    with pytest.raises(AccountingStoreError):
        store.list_lines("CLIENT_01", limit=-1)


# -- tenant isolation ---------------------------------------------------------


def test_tenant_isolation_entries_lines_decisions(store):
    store.add_entry("CLIENT_01", make_entry("A1", date(2026, 1, 1), debit="5.00"))
    store.add_entry("CLIENT_02", make_entry("B1", date(2026, 1, 1), debit="7.00"))
    store.add_line("CLIENT_01", "L1", {"label": "secret-1", "counterparty": "ACME"})
    store.add_line("CLIENT_02", "L1", {"label": "secret-2", "counterparty": "ACME"})
    store.arbitrate("CLIENT_01", "L1", decision(reason="only-01"))

    assert [e.EcritureNum for e in store.list_validated_entries("CLIENT_01", None, None)] == ["A1"]
    assert [e.EcritureNum for e in store.list_validated_entries("CLIENT_02", None, None)] == ["B1"]

    rows1, total1 = store.list_lines("CLIENT_01")
    rows2, total2 = store.list_lines("CLIENT_02")
    assert (total1, total2) == (1, 1)
    assert rows1[0]["label"] == "secret-1" and rows1[0]["raw_status"] == "BOOKED_PERSONAL"
    assert rows2[0]["label"] == "secret-2" and rows2[0]["raw_status"] == "PENDING"
    assert rows2[0]["reason"] is None
    assert store.list_lines("CLIENT_02", search="secret-1")[1] == 0
    assert store.list_lines("CLIENT_02", status="BOOKED_PERSONAL")[1] == 0
    # same line id in another tenant stays arbitrable independently
    store.arbitrate("CLIENT_02", "L1", decision("RELEASED_WITHOUT_INVOICE",
                                                "VALIDATE_WITHOUT_INVOICE", reason="x"))
    assert store.get_line("CLIENT_01", "L1")["reason"] == "only-01"


def test_unknown_tenant_sees_nothing_and_cannot_arbitrate(store):
    store.add_line("CLIENT_01", "L1", {})
    assert store.list_lines("CLIENT_03") == ([], 0)
    assert store.get_line("CLIENT_03", "L1") is None
    with pytest.raises(LineNotFoundError):
        store.arbitrate("CLIENT_03", "L1", decision())
    assert store.get_line("CLIENT_01", "L1")["raw_status"] == "PENDING"


def test_candidate_rules_isolated(store):
    store.save_candidate_rule("CLIENT_01", {"counterparty": "ACME", "action": "X"})
    assert store.list_candidate_rules("CLIENT_01") == [{"counterparty": "ACME", "action": "X"}]
    assert store.list_candidate_rules("CLIENT_02") == []


@pytest.mark.parametrize(
    "tenant", ["", "..", ".", "../x", "a/b", "a\\b", "a b", "x" * 65, "a\x00b", "é", None, 5]
)
def test_invalid_tenant_rejected_everywhere(store, tenant):
    with pytest.raises(AccountingStoreError):
        store.add_entry(tenant, make_entry("1", date(2026, 1, 1)))
    with pytest.raises(AccountingStoreError):
        store.list_validated_entries(tenant, None, None)
    with pytest.raises(AccountingStoreError):
        store.add_line(tenant, "L1", {})
    with pytest.raises(AccountingStoreError):
        store.list_lines(tenant)
    with pytest.raises(AccountingStoreError):
        store.get_line(tenant, "L1")
    with pytest.raises(AccountingStoreError):
        store.arbitrate(tenant, "L1", decision())
    with pytest.raises(AccountingStoreError):
        store.save_candidate_rule(tenant, {})


def test_sql_injection_in_ids_and_search_is_inert(store):
    store.add_line("CLIENT_01", "L1", {"label": "x"})
    payload = "'; DROP TABLE reconciliation_lines; --"
    assert store.list_lines("CLIENT_01", search=payload) == ([], 0)
    assert store.list_lines("CLIENT_01", status=payload) == ([], 0)
    assert store.get_line("CLIENT_01", payload) is None
    assert store.list_lines("CLIENT_01")[1] == 1


# -- paths --------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", ":memory:", "file:x.db", "a/../b.db", "..", "x\x00.db"])
def test_bad_db_paths_rejected(bad):
    with pytest.raises(AccountingStoreError):
        SqliteAccountingStore(bad)


def test_db_path_directory_rejected(tmp_path):
    with pytest.raises(AccountingStoreError):
        SqliteAccountingStore(tmp_path)


def test_base_dir_confinement(tmp_path):
    base = tmp_path / "data"
    s = SqliteAccountingStore("t/acc.sqlite3", base_dir=base)
    assert s.db_path == (base / "t" / "acc.sqlite3").resolve() and s.db_path.exists()
    for bad in ("../escape.db", str(tmp_path / "abs.db")):
        with pytest.raises(AccountingStoreError):
            SqliteAccountingStore(bad, base_dir=base)
    assert not (tmp_path / "escape.db").exists() and not (tmp_path / "abs.db").exists()


def test_default_path_from_environment(tmp_path, monkeypatch):
    target = tmp_path / "env" / "acc.sqlite3"
    monkeypatch.setenv("DSH_ACCOUNTING_DB", str(target))
    assert SqliteAccountingStore().db_path == target.resolve() and target.exists()


# -- persistence --------------------------------------------------------------


def test_restart_preserves_everything(db_file):
    first = SqliteAccountingStore(db_file)
    e1 = make_entry("1", date(2026, 1, 10), debit="100.00")
    e2 = make_entry("1", date(2026, 1, 10), credit="100.00", Montantdevise=Decimal("1.5"))
    first.add_entry("CLIENT_01", e1)
    first.add_entry("CLIENT_01", e2)
    first.add_entry("CLIENT_02", make_entry("9", date(2026, 1, 10)))
    first.add_line("CLIENT_01", "L1", {"label": "A", "operation_date": "2026-01-01"})
    first.add_line("CLIENT_01", "L2", {"label": "B", "operation_date": "2026-01-02"})
    first.add_line("CLIENT_02", "L1", {"label": "C"})
    first.arbitrate("CLIENT_01", "L1", decision(reason="r", debit_account="108000"))
    first.save_candidate_rule("CLIENT_01", {"k": "v"})
    before = (first.list_lines("CLIENT_01"), first.list_lines("CLIENT_02"))
    del first

    second = SqliteAccountingStore(db_file)
    assert second.list_validated_entries("CLIENT_01", None, None) == [e1, e2]
    assert len(second.list_validated_entries("CLIENT_02", None, None)) == 1
    assert (second.list_lines("CLIENT_01"), second.list_lines("CLIENT_02")) == before
    assert second.get_line("CLIENT_01", "L1")["raw_status"] == "BOOKED_PERSONAL"
    assert second.get_line("CLIENT_02", "L1")["raw_status"] == "PENDING"
    assert second.list_candidate_rules("CLIENT_01") == [{"k": "v"}]
    with pytest.raises(LineAlreadyArbitratedError):
        second.arbitrate("CLIENT_01", "L1", decision())
    second.arbitrate("CLIENT_01", "L2", decision("RELEASED_WITHOUT_INVOICE",
                                                 "VALIDATE_WITHOUT_INVOICE", reason="ok"))
    assert SqliteAccountingStore(db_file).list_lines("CLIENT_01", status="RELEASED_WITHOUT_INVOICE")[1] == 1


def test_reopen_is_idempotent_for_schema(db_file):
    SqliteAccountingStore(db_file).add_entry("CLIENT_01", make_entry("1", date(2026, 1, 1)))
    SqliteAccountingStore(db_file)
    assert len(SqliteAccountingStore(db_file).list_validated_entries("CLIENT_01", None, None)) == 1


def test_two_instances_share_one_file_concurrently(db_file):
    a, b = SqliteAccountingStore(db_file), SqliteAccountingStore(db_file)
    a.add_line("CLIENT_01", "L1", {})
    assert b.get_line("CLIENT_01", "L1") is not None
    a.arbitrate("CLIENT_01", "L1", decision())
    with pytest.raises(LineAlreadyArbitratedError):
        b.arbitrate("CLIENT_01", "L1", decision())


# -- HTTP integration ---------------------------------------------------------


@pytest.fixture()
def client(store) -> TestClient:
    app = FastAPI()
    app.include_router(accounting_export.router)
    app.include_router(reconciliation_arbitration.router)
    app.add_middleware(TenantContextMiddleware)
    app.dependency_overrides[get_fec_entry_store] = lambda: store
    app.dependency_overrides[get_arbitration_store] = lambda: store
    return TestClient(app)


def h(tenant: str) -> dict[str, str]:
    return {"X-Tenant-ID": tenant}


def test_http_arbitrate_and_list_via_sqlite(client, store, db_file):
    store.add_line("CLIENT_01", "L1", {"label": "Op", "counterparty": "ACME",
                                       "operation_date": "2026-01-05"})
    r = client.post("/reconciliation/arbitrate", headers=h("CLIENT_01"),
                    json={"line_id": "L1", "action": "PERSONAL_EXPENSE"})
    assert r.status_code == 200 and r.json()["status"] == "BOOKED_PERSONAL"
    assert client.post("/reconciliation/arbitrate", headers=h("CLIENT_01"),
                       json={"line_id": "L1", "action": "PERSONAL_EXPENSE"}).status_code == 409
    assert client.post("/reconciliation/arbitrate", headers=h("CLIENT_02"),
                       json={"line_id": "L1", "action": "PERSONAL_EXPENSE"}).status_code == 404

    page = client.get("/reconciliation/lines", headers=h("CLIENT_01")).json()
    assert page["total"] == 1
    assert page["items"][0]["status"] == "ARBITRATED_PERSONAL"
    assert page["items"][0]["arbitration_metadata"]["arbitrated_at"]
    assert client.get("/reconciliation/lines", headers=h("CLIENT_02")).json()["total"] == 0

    reopened = SqliteAccountingStore(db_file)
    assert reopened.get_line("CLIENT_01", "L1")["raw_status"] == "BOOKED_PERSONAL"


def test_http_remember_rule_persisted(client, store):
    store.add_line("CLIENT_01", "L1", {"counterparty": "ACME"})
    r = client.post("/reconciliation/arbitrate", headers=h("CLIENT_01"),
                    json={"line_id": "L1", "action": "PERSONAL_EXPENSE", "remember_rule": True})
    assert r.status_code == 200
    assert store.list_candidate_rules("CLIENT_01")[0]["counterparty"] == "ACME"
    assert store.list_candidate_rules("CLIENT_02") == []


def test_http_fec_export_isolated_and_filtered(client, store):
    store.add_entry("CLIENT_01", make_entry("1", date(2026, 1, 10), debit="50.00"))
    store.add_entry("CLIENT_01", make_entry("1", date(2026, 1, 10), credit="50.00"))
    store.add_entry("CLIENT_01", make_entry("2", date(2026, 6, 10), debit="1.00"), validated=False)
    store.add_entry("CLIENT_02", make_entry("7", date(2026, 1, 10), debit="9.00"))
    store.add_entry("CLIENT_02", make_entry("7", date(2026, 1, 10), credit="9.00"))

    body = client.get("/accounting/export/fec", headers=h("CLIENT_01")).text
    assert "Facture 1" in body and "Facture 7" not in body and "Facture 2" not in body
    other = client.get("/accounting/export/fec", headers=h("CLIENT_02")).text
    assert "Facture 7" in other and "Facture 1" not in other
    out_of_range = client.get("/accounting/export/fec?date_from=2026-02-01", headers=h("CLIENT_01"))
    assert "Facture 1" not in out_of_range.text


def test_http_spreadsheet_export_uses_sqlite_lines(client, store):
    store.add_line("CLIENT_01", "L1", {"label": "VISIBLE", "operation_date": "2026-01-05"})
    store.add_line("CLIENT_02", "L1", {"label": "HIDDEN", "operation_date": "2026-01-05"})
    text = client.get("/accounting/export/spreadsheet", headers=h("CLIENT_01")).content.decode("utf-8")
    assert "VISIBLE" in text and "HIDDEN" not in text


# -- dependency selection -------------------------------------------------------


def test_dependency_default_is_in_memory(monkeypatch):
    monkeypatch.delenv("DSH_ACCOUNTING_STORE", raising=False)
    assert isinstance(get_fec_entry_store(), accounting_export.InMemoryFecEntryStore)
    assert isinstance(
        get_arbitration_store(), reconciliation_arbitration.InMemoryArbitrationStore
    )


def test_dependency_selects_sqlite_from_env(monkeypatch, tmp_path):
    from services import sqlite_accounting_store as mod

    mod._shared_store.cache_clear()
    monkeypatch.setenv("DSH_ACCOUNTING_STORE", "sqlite")
    monkeypatch.setenv("DSH_ACCOUNTING_DB", str(tmp_path / "sel.sqlite3"))
    try:
        s1, s2 = get_fec_entry_store(), get_arbitration_store()
        assert isinstance(s1, SqliteAccountingStore) and s1 is s2
        assert s1.db_path == (tmp_path / "sel.sqlite3").resolve()
    finally:
        mod._shared_store.cache_clear()
