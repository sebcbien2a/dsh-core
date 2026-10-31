"""Tenant-scoped FEC export endpoint."""

from __future__ import annotations

from datetime import date
from typing import Literal, Protocol
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from services.fec_export_service import (
    FecEntry,
    FecGenerator,
    UnbalancedAccountingEntryError,
)


class FecEntryStore(Protocol):
    def list_validated_entries(
        self, tenant_id: str, date_from: date | None, date_to: date | None
    ) -> list[FecEntry]: ...


class InMemoryFecEntryStore:
    """Tenant-keyed store used until the accounting persistence adapter is wired."""

    def __init__(self) -> None:
        self._entries: dict[str, list[tuple[FecEntry, bool]]] = {}

    def add_entry(
        self, tenant_id: str, entry: FecEntry, *, validated: bool = True
    ) -> None:
        self._entries.setdefault(tenant_id, []).append((entry, validated))

    def list_validated_entries(
        self, tenant_id: str, date_from: date | None, date_to: date | None
    ) -> list[FecEntry]:
        return [
            entry
            for entry, validated in self._entries.get(tenant_id, [])
            if validated
            and (date_from is None or entry.EcritureDate >= date_from)
            and (date_to is None or entry.EcritureDate <= date_to)
        ]


_default_store = InMemoryFecEntryStore()


def get_fec_entry_store() -> FecEntryStore:
    return _default_store


router = APIRouter(prefix="/accounting/export", tags=["accounting"])


def _tenant_of(request: Request) -> str:
    tenant = getattr(request.state, "tenant_id", None)
    if not isinstance(tenant, str) or not tenant:
        raise HTTPException(status_code=400, detail="INVALID_OR_MISSING_TENANT_ID")
    return tenant


@router.get("/fec")
def export_fec(
    request: Request,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    delimiter: Literal["tab", "pipe"] = Query("tab"),
    store: FecEntryStore = Depends(get_fec_entry_store),
) -> Response:
    tenant_id = _tenant_of(request)
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(status_code=400, detail="INVALID_DATE_RANGE")

    entries = store.list_validated_entries(tenant_id, date_from, date_to)
    separator = "\t" if delimiter == "tab" else "|"
    try:
        content = FecGenerator.generate_fec(entries, delimiter=separator)
    except UnbalancedAccountingEntryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    start = date_from.isoformat() if date_from is not None else "all"
    end = date_to.isoformat() if date_to is not None else "all"
    filename = f"FEC_{quote(tenant_id, safe='')}_{start}_{end}.txt"
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
