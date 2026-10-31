"""P13.2 - Exhaustive tests of bank statement parsing and the import endpoint (offline)."""

from __future__ import annotations

import hashlib
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.middleware.tenant_middleware import TenantContextMiddleware
from backend.app.routers import reconciliation_arbitration as arb
from backend.app.routers.reconciliation_arbitration import (
    InMemoryArbitrationStore,
    get_arbitration_store,
    router,
)
from services import sqlite_accounting_store as sqlite_mod
from services.bank_statement_parser_service import (
    MAX_BYTES,
    BankStatementError,
    parse_amount,
    parse_bank_statement_csv,
    parse_date,
)
from services.sqlite_accounting_store import SqliteAccountingStore

HEADER = "Date;Libellé;Débit;Crédit;Tiers;Devise\n"
CSV = (
    HEADER
    + "2026-01-05;Achat Cristaux;1 234,56;;ACME;EUR\n"
    + "06/01/2026;Virement client;;500.00;DUPONT;eur\n"
    + "07/01/2026;Frais bancaires;12,5;;;\n"
)


def err(call, *args, **kwargs) -> BankStatementError:
    with pytest.raises(BankStatementError) as info:
        call(*args, **kwargs)
    return info.value


# -- parsing ------------------------------------------------------------------


def test_parse_basic_csv_formats_and_defaults():
    lines = parse_bank_statement_csv(CSV, tenant_id="CLIENT_01")
    assert [l["operation_date"] for l in lines] == ["2026-01-05", "2026-01-06", "2026-01-07"]
    assert lines[0]["debit_amount"] == "1234.56" and lines[0]["credit_amount"] == "0.00"
    assert lines[1]["credit_amount"] == "500.00" and lines[1]["debit_amount"] == "0.00"
    assert lines[2]["debit_amount"] == "12.50"
    assert [l["currency"] for l in lines] == ["EUR", "EUR", "EUR"]
    assert lines[0]["counterparty"] == "ACME" and lines[2]["counterparty"] is None
    assert all(l["status"] == "PENDING" and len(l["line_id"]) == 64 for l in lines)
    assert len({l["line_id"] for l in lines}) == 3


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1234.56", "1234.56"), ("1234,56", "1234.56"), ("1 234,56", "1234.56"),
        ("1\u00a0234,56", "1234.56"), ("1.234,56", "1234.56"), ("1,234.56", "1234.56"),
        ("1.234.567", "1234567.00"), ("1,234,567", "1234567.00"), ("12", "12.00"),
        ("0,5", "0.50"), (".5", "0.50"), ("-12,30", "-12.30"), ("+7", "7.00"),
        ("12,3", "12.30"), ("  8.10 ", "8.10"),
    ],
)
def test_amount_formats(raw, expected):
    assert parse_amount(raw) == Decimal(expected)


@pytest.mark.parametrize(
    "raw", ["abc", "1,234", "12.345", "1.2.3", "1,2,3", "--5", "1e5", "NaN", "Infinity", "12,", "€5"]
)
def test_amount_rejects_garbage(raw):
    # "1,234" is a single separator followed by 3 digits: refused as ambiguous
    assert err(parse_amount, raw).code == "INVALID_AMOUNT"


def test_amount_empty_is_none():
    assert parse_amount("") is None and parse_amount("  ") is None


@pytest.mark.parametrize(
    "raw, iso", [("2026-02-28", "2026-02-28"), ("28/02/2026", "2026-02-28"), ("29/02/2024", "2024-02-29")]
)
def test_dates_accepted(raw, iso):
    assert parse_date(raw) == iso


@pytest.mark.parametrize(
    "raw", ["", "2026-13-01", "31/04/2026", "29/02/2025", "1/2/2026", "2026/01/05",
            "05-01-2026", "20260105", "2026-1-5", "5 janvier 2026", "2026-01-05T10:00"]
)
def test_dates_rejected(raw):
    assert err(parse_date, raw).code == "INVALID_DATE"


def test_header_aliases_case_and_accents():
    lines = parse_bank_statement_csv(
        "DATE OPÉRATION;LIBELLÉ;Débit;CRÉDIT;Contrepartie;Currency\n"
        "2026-01-05;X;5;;Bob;usd\n"
    )
    assert lines[0]["counterparty"] == "Bob" and lines[0]["currency"] == "USD"


def test_signed_amount_column():
    lines = parse_bank_statement_csv("date;libelle;montant\n2026-01-01;A;-10,00\n2026-01-02;B;20\n")
    assert (lines[0]["debit_amount"], lines[0]["credit_amount"]) == ("10.00", "0.00")
    assert (lines[1]["debit_amount"], lines[1]["credit_amount"]) == ("0.00", "20.00")


@pytest.mark.parametrize("delim", [";", ",", "\t", "|"])
def test_delimiters(delim):
    text = delim.join(["date", "libelle", "debit"]) + "\n" + delim.join(["2026-01-01", "A", "5,5"]) + "\n"
    if delim == ",":
        text = "date,libelle,debit\n2026-01-01,A,\"5,5\"\n"
    assert parse_bank_statement_csv(text, delim)[0]["debit_amount"] == "5.50"


def test_quoted_fields_with_delimiter_and_newline():
    lines = parse_bank_statement_csv('date;libelle;debit\n2026-01-01;"A; B\nC";3\n')
    assert lines[0]["label"] == "A; B\nC"


def test_bytes_utf8_bom_and_cp1252():
    assert parse_bank_statement_csv(CSV.encode("utf-8-sig"))[0]["label"] == "Achat Cristaux"
    legacy = "date;libelle;debit\n2026-01-01;Café;5\n".encode("cp1252")
    assert parse_bank_statement_csv(legacy)[0]["label"] == "Café"


def test_blank_lines_ignored_and_crlf():
    lines = parse_bank_statement_csv("date;libelle;debit\r\n\r\n2026-01-01;A;5\r\n;;\r\n")
    assert len(lines) == 1


def test_provided_line_id_kept_and_validated():
    lines = parse_bank_statement_csv("date;libelle;debit;id\n2026-01-01;A;5;BK-1\n")
    assert lines[0]["line_id"] == "BK-1"
    assert err(parse_bank_statement_csv, "date;libelle;debit;id\n2026-01-01;A;5;a/b\n").code == "INVALID_LINE_ID"
    dup = "date;libelle;debit;id\n2026-01-01;A;5;K\n2026-01-02;B;6;K\n"
    assert err(parse_bank_statement_csv, dup).code == "DUPLICATE_LINE_ID"


def test_line_id_is_documented_sha256_and_deterministic():
    a = parse_bank_statement_csv(CSV, tenant_id="CLIENT_01")
    b = parse_bank_statement_csv(CSV, tenant_id="CLIENT_01")
    assert [x["line_id"] for x in a] == [x["line_id"] for x in b]
    material = "\x1f".join(["CLIENT_01", "2026-01-05", "1234.56", "0.00", "EUR", "Achat Cristaux", "1"])
    assert a[0]["line_id"] == hashlib.sha256(material.encode()).hexdigest()
    other = parse_bank_statement_csv(CSV, tenant_id="CLIENT_02")
    assert not {x["line_id"] for x in a} & {x["line_id"] for x in other}


def test_identical_operations_in_one_file_stay_distinct():
    text = "date;libelle;debit\n2026-01-01;Café;2\n2026-01-01;Café;2\n"
    lines = parse_bank_statement_csv(text, tenant_id="T")
    assert len({l["line_id"] for l in lines}) == 2
    again = parse_bank_statement_csv(text, tenant_id="T")
    assert [l["line_id"] for l in lines] == [l["line_id"] for l in again]


@pytest.mark.parametrize(
    "text, code, row",
    [
        ("", "EMPTY_STATEMENT", None),
        ("date;libelle\n2026-01-01;A\n", "MISSING_COLUMNS", None),
        ("libelle;debit\nA;5\n", "MISSING_COLUMNS", None),
        ("date;libelle;debit\n2026-01-01;A;5\n2026-99-01;B;5\n", "INVALID_DATE", 2),
        ("date;libelle;debit\n2026-01-01;;5\n", "MISSING_LABEL", 1),
        ("date;libelle;debit\n2026-01-01;A;x\n", "INVALID_AMOUNT", 1),
        ("date;libelle;debit\n2026-01-01;A;-5\n", "NEGATIVE_AMOUNT", 1),
        ("date;libelle;debit;credit\n2026-01-01;A;5;5\n", "INVALID_AMOUNT", 1),
        ("date;libelle;debit;credit\n2026-01-01;A;;\n", "INVALID_AMOUNT", 1),
        ("date;libelle;debit;credit\n2026-01-01;A;0;0\n", "INVALID_AMOUNT", 1),
        ("date;libelle;debit;devise\n2026-01-01;A;5;EURO\n", "INVALID_CURRENCY", 1),
        ("date;libelle;debit;devise\n2026-01-01;A;5;E1R\n", "INVALID_CURRENCY", 1),
        ("date;libelle;debit;montant\n2026-01-01;A;5;5\n", "AMBIGUOUS_AMOUNT", 1),
        ("date;libelle;debit\n2026-01-01;A;5;extra\n", "EXTRA_COLUMNS", 1),
        ("date;date op;libelle;debit\n2026-01-01;2026-01-01;A;5\n", "DUPLICATE_COLUMN", 1),
        ("date;libelle;debit\n2026-01-01;A\x00B;5\n", "INVALID_CONTENT", None),
    ],
)
def test_strict_validation_errors(text, code, row):
    e = err(parse_bank_statement_csv, text)
    assert (e.code, e.row) == (code, row)


def test_invalid_delimiter_and_limits():
    assert err(parse_bank_statement_csv, CSV, "ab").code == "INVALID_DELIMITER"
    assert err(parse_bank_statement_csv, CSV, "x").code == "INVALID_DELIMITER"
    assert err(parse_bank_statement_csv, b"x" * (MAX_BYTES + 1)).code == "FILE_TOO_LARGE"
    long = "date;libelle;debit\n2026-01-01;" + "a" * 501 + ";5\n"
    assert err(parse_bank_statement_csv, long).code == "INVALID_LABEL"


# -- HTTP: in-memory store --------------------------------------------------------


@pytest.fixture()
def mem_store() -> InMemoryArbitrationStore:
    return InMemoryArbitrationStore()


def build_client(store) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.add_middleware(TenantContextMiddleware)
    app.dependency_overrides[get_arbitration_store] = lambda: store
    return TestClient(app)


@pytest.fixture()
def client(mem_store) -> TestClient:
    return build_client(mem_store)


def h(tenant: str | None) -> dict[str, str]:
    return {"X-Tenant-ID": tenant} if tenant else {}


def upload(client, text, tenant="CLIENT_01", name="releve.csv", **extra):
    data = text.encode("utf-8") if isinstance(text, str) else text
    return client.post(
        "/reconciliation/import",
        headers=h(tenant),
        files={"file": (name, data, "text/csv")},
        **extra,
    )


def total(client, tenant, **params) -> int:
    return client.get("/reconciliation/lines", headers=h(tenant), params=params).json()["total"]


def test_import_csv_upload_creates_pending_lines(client, mem_store):
    r = upload(client, CSV)
    assert r.status_code == 200
    assert r.json() == {"imported": 3, "skipped": 0, "tenant_id": "CLIENT_01"}
    page = client.get("/reconciliation/lines", headers=h("CLIENT_01")).json()
    assert page["total"] == 3 and {i["status"] for i in page["items"]} == {"PENDING"}
    assert [i["operation_date"] for i in page["items"]] == ["2026-01-05", "2026-01-06", "2026-01-07"]
    assert page["items"][0]["debit_amount"] == "1234.56"
    assert all(t == "CLIENT_01" for t, _ in mem_store.lines)


def test_reimport_is_deduplicated_and_never_resets_arbitration(client):
    assert upload(client, CSV).json()["imported"] == 3
    page = client.get("/reconciliation/lines", headers=h("CLIENT_01")).json()
    line_id = page["items"][0]["line_id"]
    assert client.post("/reconciliation/arbitrate", headers=h("CLIENT_01"),
                       json={"line_id": line_id, "action": "PERSONAL_EXPENSE"}).status_code == 200
    again = upload(client, CSV)
    assert again.json() == {"imported": 0, "skipped": 3, "tenant_id": "CLIENT_01"}
    assert total(client, "CLIENT_01") == 3
    assert total(client, "CLIENT_01", status="BOOKED_PERSONAL") == 1


def test_overlapping_statement_imports_only_new_lines(client):
    upload(client, CSV)
    more = CSV + "08/01/2026;Nouvelle op;9,99;;;\n"
    assert upload(client, more).json() == {"imported": 1, "skipped": 3, "tenant_id": "CLIENT_01"}


def test_import_json_rows_with_mixed_formats(client):
    payload = {"lines": [
        {"date": "05/01/2026", "libelle": "A", "debit": "1 000,50"},
        {"Date": "2026-01-06", "Label": "B", "Credit": 20.5, "Currency": "gbp", "Tiers": "Bob"},
        {"date": "2026-01-07", "libelle": "C", "montant": "-3,2", "line_id": "EXT-1"},
    ]}
    r = client.post("/reconciliation/import", json=payload, headers=h("CLIENT_01"))
    assert r.status_code == 200 and r.json()["imported"] == 3
    items = {i["label"]: i for i in client.get("/reconciliation/lines", headers=h("CLIENT_01")).json()["items"]}
    assert items["A"]["debit_amount"] == "1000.50"
    assert items["B"]["credit_amount"] == "20.50" and items["B"]["currency"] == "GBP"
    assert items["B"]["counterparty"] == "Bob" and items["C"]["line_id"] == "EXT-1"
    assert client.post("/reconciliation/import", json=payload, headers=h("CLIENT_01")).json()["skipped"] == 3


def test_json_numeric_amounts_not_float_corrupted(client):
    r = client.post("/reconciliation/import", headers=h("CLIENT_01"),
                    json={"lines": [{"date": "2026-01-01", "libelle": "A", "debit": 0.1}]})
    assert r.status_code == 200
    assert client.get("/reconciliation/lines", headers=h("CLIENT_01")).json()["items"][0]["debit_amount"] == "0.10"


def test_import_delimiter_query_and_form(client):
    comma = "date,libelle,debit\n2026-01-01,A,\"5,5\"\n"
    assert upload(client, comma, params={"delimiter": ","}).json()["imported"] == 1
    pipe = "date|libelle|debit\n2026-01-02|B|7\n"
    r = client.post("/reconciliation/import", headers=h("CLIENT_01"),
                    files={"file": ("a.csv", pipe.encode(), "text/csv")}, data={"delimiter": "|"})
    assert r.json()["imported"] == 1
    assert upload(client, comma).status_code == 422  # wrong delimiter: header not recognized
    assert upload(client, comma, params={"delimiter": "ab"}).status_code == 422


def test_tenant_isolation_same_file_two_tenants(client, mem_store):
    assert upload(client, CSV, "CLIENT_01").json()["imported"] == 3
    r2 = upload(client, CSV, "CLIENT_02")
    assert r2.json() == {"imported": 3, "skipped": 0, "tenant_id": "CLIENT_02"}
    assert total(client, "CLIENT_01") == 3 and total(client, "CLIENT_02") == 3
    ids1 = {l["line_id"] for l in client.get("/reconciliation/lines", headers=h("CLIENT_01")).json()["items"]}
    ids2 = {l["line_id"] for l in client.get("/reconciliation/lines", headers=h("CLIENT_02")).json()["items"]}
    assert not ids1 & ids2  # tenant is part of the generated id
    lid = next(iter(ids1))
    assert client.post("/reconciliation/arbitrate", headers=h("CLIENT_02"),
                       json={"line_id": lid, "action": "PERSONAL_EXPENSE"}).status_code == 404
    assert total(client, "CLIENT_01", status="PENDING") == 3
    assert total(client, "CLIENT_03") == 0


def test_tenant_comes_from_context_not_payload(client, mem_store):
    r = client.post("/reconciliation/import", headers=h("CLIENT_01"),
                    json={"tenant_id": "CLIENT_02", "lines": [{"date": "2026-01-01", "libelle": "A", "debit": "1"}]})
    assert r.status_code == 403 and not mem_store.lines
    r = client.post("/reconciliation/import", headers=h("CLIENT_01"),
                    files={"file": ("a.csv", CSV.encode(), "text/csv")}, data={"tenant_id": "CLIENT_02"})
    assert r.status_code == 403 and not mem_store.lines
    ok = client.post("/reconciliation/import", headers=h("CLIENT_01"),
                     json={"tenant_id": "CLIENT_01", "lines": [{"date": "2026-01-01", "libelle": "A", "debit": "1"}]})
    assert ok.status_code == 200


def test_missing_tenant_rejected(client, mem_store):
    assert upload(client, CSV, tenant=None).status_code in (400, 401, 403)
    assert not mem_store.lines


def test_invalid_file_is_rejected_atomically(client, mem_store):
    bad = CSV + "99/99/2026;Oops;1;;;\n"
    r = upload(client, bad)
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "INVALID_DATE", "row": 4}
    assert not mem_store.lines


@pytest.mark.parametrize(
    "kwargs, status",
    [
        ({"content": b"x", "headers": {"content-type": "text/plain"}}, 415),
        ({"content": b"{", "headers": {"content-type": "application/json"}}, 422),
        ({"json": []}, 422),
        ({"json": {"lines": "nope"}}, 422),
        ({"json": {"lines": ["str"]}}, 422),
        ({"json": {"lines": [{"date": "2026-01-01", "libelle": "A"}]}}, 422),
    ],
)
def test_bad_payloads(client, kwargs, status):
    headers = {**h("CLIENT_01"), **kwargs.pop("headers", {})}
    assert client.post("/reconciliation/import", headers=headers, **kwargs).status_code == status


def test_multipart_without_file_field(client):
    r = client.post("/reconciliation/import", headers=h("CLIENT_01"), data={"x": "y"},
                    files={"other": ("a.csv", b"x", "text/csv")})
    assert r.status_code == 422 and r.json()["detail"] == "MISSING_FILE"


def test_oversized_upload_rejected(client, mem_store):
    r = upload(client, b"x" * (MAX_BYTES + 100_000))
    assert r.status_code == 413 and not mem_store.lines


def test_import_then_arbitrate_end_to_end(client):
    upload(client, CSV)
    first = client.get("/reconciliation/lines", headers=h("CLIENT_01")).json()["items"][0]
    r = client.post("/reconciliation/arbitrate", headers=h("CLIENT_01"),
                    json={"line_id": first["line_id"], "action": "VALIDATE_WITHOUT_INVOICE", "reason": "ticket perdu"})
    assert r.status_code == 200 and r.json()["status"] == "RELEASED_WITHOUT_INVOICE"


# -- SQLite backend ---------------------------------------------------------------


@pytest.fixture()
def sqlite_env(tmp_path, monkeypatch):
    db = tmp_path / "acc" / "bank.sqlite3"
    sqlite_mod._shared_store.cache_clear()
    monkeypatch.setenv("DSH_ACCOUNTING_STORE", "sqlite")
    monkeypatch.setenv("DSH_ACCOUNTING_DB", str(db))
    app = FastAPI()
    app.include_router(router)
    app.add_middleware(TenantContextMiddleware)  # no override: real env-based selection
    yield TestClient(app), db
    sqlite_mod._shared_store.cache_clear()


def test_sqlite_env_selects_sqlite_and_persists_import(sqlite_env):
    client, db = sqlite_env
    assert isinstance(arb.get_arbitration_store(), SqliteAccountingStore)
    assert upload(client, CSV).json() == {"imported": 3, "skipped": 0, "tenant_id": "CLIENT_01"}
    assert db.exists()

    reopened = SqliteAccountingStore(db)
    rows, count = reopened.list_lines("CLIENT_01")
    assert count == 3 and {r["raw_status"] for r in rows} == {"PENDING"}
    assert rows[0]["debit_amount"] == "1234.56" and rows[0]["counterparty"] == "ACME"
    assert reopened.list_lines("CLIENT_02") == ([], 0)


def test_sqlite_dedup_across_restart_and_arbitration_preserved(sqlite_env):
    client, db = sqlite_env
    upload(client, CSV)
    lid = client.get("/reconciliation/lines", headers=h("CLIENT_01")).json()["items"][0]["line_id"]
    assert client.post("/reconciliation/arbitrate", headers=h("CLIENT_01"),
                       json={"line_id": lid, "action": "PERSONAL_EXPENSE"}).status_code == 200

    sqlite_mod._shared_store.cache_clear()  # simulated process restart
    again = upload(client, CSV)
    assert again.json() == {"imported": 0, "skipped": 3, "tenant_id": "CLIENT_01"}
    assert total(client, "CLIENT_01") == 3
    assert total(client, "CLIENT_01", status="BOOKED_PERSONAL") == 1
    assert total(client, "CLIENT_01", status="PENDING") == 2


def test_sqlite_isolation_and_json_import(sqlite_env):
    client, db = sqlite_env
    upload(client, CSV, "CLIENT_01")
    r = client.post("/reconciliation/import", headers=h("CLIENT_02"),
                    json={"lines": [{"date": "2026-03-01", "libelle": "Seulement 02", "credit": "42"}]})
    assert r.json() == {"imported": 1, "skipped": 0, "tenant_id": "CLIENT_02"}
    assert total(client, "CLIENT_01") == 3 and total(client, "CLIENT_02") == 1
    store = SqliteAccountingStore(db)
    assert store.list_lines("CLIENT_01", search="Seulement")[1] == 0
    assert store.list_lines("CLIENT_02", search="Cristaux")[1] == 0
    assert client.get("/reconciliation/lines", headers=h("CLIENT_02")).json()["items"][0]["label"] == "Seulement 02"


def test_sqlite_add_line_does_not_overwrite(tmp_path):
    s = SqliteAccountingStore(tmp_path / "x.sqlite3")
    assert s.add_line("CLIENT_01", "L1", {"label": "first"}) is True
    assert s.add_line("CLIENT_01", "L1", {"label": "second"}) is False
    assert s.add_line("CLIENT_02", "L1", {"label": "other"}) is True
    assert s.get_line("CLIENT_01", "L1")["label"] == "first"


def test_in_memory_add_line_does_not_overwrite(mem_store):
    assert mem_store.add_line("A", "L1", {"label": "first"}) is True
    assert mem_store.add_line("A", "L1", {"label": "second"}) is False
    assert mem_store.lines[("A", "L1")] == {"label": "first", "status": "PENDING"}
