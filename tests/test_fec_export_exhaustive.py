"""Exhaustive offline tests for the tenant-scoped FEC export."""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from io import StringIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.middleware.tenant_middleware import TenantContextMiddleware
from backend.app.routers.accounting_export import (
    InMemoryFecEntryStore,
    get_fec_entry_store,
    router,
)
from services.fec_export_service import (
    FecEntry,
    FecGenerator,
    UnbalancedAccountingEntryError,
)

EXPECTED_COLUMNS = (
    "JournalCode",
    "JournalLib",
    "EcritureNum",
    "EcritureDate",
    "CompteNum",
    "CompteLib",
    "CompAuxNum",
    "CompAuxLib",
    "PieceRef",
    "PieceDate",
    "EcritureLib",
    "Debit",
    "Credit",
    "EcritureLet",
    "DateLet",
    "ValidDate",
    "Montantdevise",
    "Idevise",
)


def make_entry(
    *,
    writing_number: str = "EC-01",
    account: str = "512000",
    debit: str = "450.00",
    credit: str = "0.00",
    entry_date: date = date(2026, 1, 15),
) -> FecEntry:
    return FecEntry(
        JournalCode="VE",
        JournalLib="Ventes",
        EcritureNum=writing_number,
        EcritureDate=entry_date,
        CompteNum=account,
        CompteLib="Banque",
        CompAuxNum="AUX-1",
        CompAuxLib="Client",
        PieceRef="FAC-1",
        PieceDate=date(2026, 1, 14),
        EcritureLib="Facture client",
        Debit=Decimal(debit),
        Credit=Decimal(credit),
        EcritureLet="",
        DateLet=None,
        ValidDate=date(2026, 1, 16),
        Montantdevise=Decimal("450"),
        Idevise="EUR",
    )


def balanced_pair(writing_number: str = "EC-01") -> list[FecEntry]:
    return [
        make_entry(writing_number=writing_number, debit="450.00", credit="0.00"),
        make_entry(
            writing_number=writing_number,
            account="706000",
            debit="0.00",
            credit="450.00",
        ),
    ]


@pytest.fixture()
def store() -> InMemoryFecEntryStore:
    return InMemoryFecEntryStore()


@pytest.fixture()
def client(store: InMemoryFecEntryStore) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.add_middleware(TenantContextMiddleware)
    app.dependency_overrides[get_fec_entry_store] = lambda: store
    return TestClient(app)


def test_fec_entry_exposes_exact_official_18_columns() -> None:
    entry = make_entry()

    assert tuple(entry.model_dump()) == EXPECTED_COLUMNS
    assert len(EXPECTED_COLUMNS) == 18


def test_generator_writes_exact_header_dates_and_two_decimal_amounts() -> None:
    text = FecGenerator.generate_fec(balanced_pair())

    assert text == (
        "JournalCode\tJournalLib\tEcritureNum\tEcritureDate\tCompteNum\tCompteLib\t"
        "CompAuxNum\tCompAuxLib\tPieceRef\tPieceDate\tEcritureLib\tDebit\tCredit\t"
        "EcritureLet\tDateLet\tValidDate\tMontantdevise\tIdevise\n"
        "VE\tVentes\tEC-01\t20260115\t512000\tBanque\tAUX-1\tClient\tFAC-1\t"
        "20260114\tFacture client\t450.00\t0.00\t\t\t20260116\t450.00\tEUR\n"
        "VE\tVentes\tEC-01\t20260115\t706000\tBanque\tAUX-1\tClient\tFAC-1\t"
        "20260114\tFacture client\t0.00\t450.00\t\t\t20260116\t450.00\tEUR\n"
    )
    rows = list(csv.reader(StringIO(text), delimiter="\t"))
    assert rows[0] == list(EXPECTED_COLUMNS)
    assert all(len(row) == 18 for row in rows)


def test_generator_accepts_pipe_delimiter_and_formats_compact_dates() -> None:
    text = FecGenerator.generate_fec(balanced_pair(), delimiter="|")
    rows = list(csv.reader(StringIO(text), delimiter="|"))

    assert rows[0] == list(EXPECTED_COLUMNS)
    assert rows[1][3] == "20260115"
    assert rows[1][11:13] == ["450.00", "0.00"]
    assert all(len(row) == 18 for row in rows)


@pytest.mark.parametrize("delimiter", ["", "||", "\n"])
def test_generator_rejects_invalid_delimiter(delimiter: str) -> None:
    with pytest.raises(ValueError, match="delimiter"):
        FecGenerator.generate_fec([], delimiter=delimiter)


def test_generator_rejects_unbalanced_writing() -> None:
    with pytest.raises(UnbalancedAccountingEntryError, match="EC-01"):
        FecGenerator.generate_fec(
            [make_entry(debit="450.00", credit="449.99")]
        )


def test_balance_is_checked_separately_for_each_writing_number() -> None:
    entries = balanced_pair("EC-01") + [
        make_entry(writing_number="EC-02", debit="10.00", credit="0.00")
    ]

    with pytest.raises(UnbalancedAccountingEntryError, match="EC-02"):
        FecGenerator.generate_fec(entries)


def test_endpoint_uses_tenant_context_and_returns_attachment(client, store) -> None:
    for entry in balanced_pair():
        store.add_entry("CLIENT_01", entry)

    response = client.get(
        "/accounting/export/fec",
        headers={"X-Tenant-ID": "CLIENT_01"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["content-disposition"] == (
        'attachment; filename="FEC_CLIENT_01_all_all.txt"'
    )
    assert response.text.startswith("\t".join(EXPECTED_COLUMNS) + "\n")


def test_endpoint_supports_pipe_delimiter(client, store) -> None:
    for entry in balanced_pair():
        store.add_entry("CLIENT_01", entry)

    response = client.get(
        "/accounting/export/fec",
        headers={"X-Tenant-ID": "CLIENT_01"},
        params={"delimiter": "pipe"},
    )

    assert response.status_code == 200
    assert response.text.startswith("|".join(EXPECTED_COLUMNS) + "\n")
    assert all(len(row) == 18 for row in csv.reader(StringIO(response.text), delimiter="|"))


def test_endpoint_rejects_missing_tenant_before_export(client) -> None:
    response = client.get("/accounting/export/fec")

    assert response.status_code == 400
    assert response.json() == {"detail": "INVALID_OR_MISSING_TENANT_ID"}


def test_endpoint_export_is_hermetically_isolated_between_tenants(client, store) -> None:
    for entry in balanced_pair("CLIENT_01-WRITING"):
        store.add_entry("CLIENT_01", entry)
    for entry in balanced_pair("CLIENT_02-WRITING"):
        store.add_entry("CLIENT_02", entry)

    response = client.get(
        "/accounting/export/fec", headers={"X-Tenant-ID": "CLIENT_01"}
    )

    assert response.status_code == 200
    assert "CLIENT_01-WRITING" in response.text
    assert "CLIENT_02-WRITING" not in response.text


def test_endpoint_excludes_unvalidated_entries_and_applies_date_range(client, store) -> None:
    for entry in balanced_pair("IN-RANGE"):
        store.add_entry("CLIENT_01", entry)
    for entry in balanced_pair("UNVALIDATED"):
        store.add_entry("CLIENT_01", entry, validated=False)
    for entry in balanced_pair("OUT-OF-RANGE"):
        store.add_entry(
            "CLIENT_01",
            entry.model_copy(update={"EcritureDate": date(2026, 2, 1)}),
        )

    response = client.get(
        "/accounting/export/fec",
        headers={"X-Tenant-ID": "CLIENT_01"},
        params={"date_from": "2026-01-01", "date_to": "2026-01-31"},
    )

    assert response.status_code == 200
    assert "IN-RANGE" in response.text
    assert "UNVALIDATED" not in response.text
    assert "OUT-OF-RANGE" not in response.text
    assert response.headers["content-disposition"] == (
        'attachment; filename="FEC_CLIENT_01_2026-01-01_2026-01-31.txt"'
    )


def test_endpoint_returns_validation_error_for_unbalanced_store_data(client, store) -> None:
    store.add_entry("CLIENT_01", make_entry(debit="10.00", credit="0.00"))

    response = client.get(
        "/accounting/export/fec", headers={"X-Tenant-ID": "CLIENT_01"}
    )

    assert response.status_code == 422
    assert "unbalanced" in response.json()["detail"]


def test_endpoint_rejects_invalid_date_range(client) -> None:
    response = client.get(
        "/accounting/export/fec",
        headers={"X-Tenant-ID": "CLIENT_01"},
        params={"date_from": "2026-02-01", "date_to": "2026-01-01"},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "INVALID_DATE_RANGE"}


def test_balance_matches_rounded_values_that_are_actually_exported() -> None:
    entries = [
        make_entry(debit="0.004", credit="0.00"),
        make_entry(debit="0.004", credit="0.00"),
        make_entry(debit="0.00", credit="0.008"),
    ]

    with pytest.raises(UnbalancedAccountingEntryError):
        FecGenerator.generate_fec(entries)
