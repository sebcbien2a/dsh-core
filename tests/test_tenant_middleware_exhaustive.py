"""Exhaustive tests for TenantContextMiddleware (P10.2), fully offline."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI, Request

from backend.app.middleware.tenant_middleware import (
    TENANT_ID_PATTERN,
    TenantContextMiddleware,
    current_tenant_id,
    get_current_tenant_id,
)

CALLS: list[str] = []


def _build_app(**kwargs) -> FastAPI:
    app = FastAPI()
    app.add_middleware(TenantContextMiddleware, **kwargs)

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/metrics")
    async def metrics() -> dict:
        return {"m": 1}

    @app.get("/whoami")
    async def whoami(request: Request) -> dict:
        CALLS.append(request.state.tenant_id)
        return {"state": request.state.tenant_id, "ctx": get_current_tenant_id()}

    @app.get("/slow")
    async def slow(request: Request, delay: float = 0.0) -> dict:
        before = get_current_tenant_id()
        await asyncio.sleep(delay)
        await asyncio.sleep(0)
        return {"before": before, "after": get_current_tenant_id(), "state": request.state.tenant_id}

    return app


@pytest.fixture(autouse=True)
def _reset_calls():
    CALLS.clear()
    yield


def _client(**kwargs) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_build_app(**kwargs)), base_url="http://t"
    )


async def _raw_call(headers: list[tuple[bytes, bytes]], path: str = "/whoami", **kwargs):
    app = _build_app(**kwargs)
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "method": "GET", "path": path, "headers": headers}
    await app(scope, receive, send)
    return sent


@pytest.mark.asyncio
async def test_error_body_is_exact_json_detail():
    async with _client() as c:
        r = await c.get("/whoami")
    assert r.status_code == 400
    assert r.json() == {"detail": "INVALID_OR_MISSING_TENANT_ID"}
    assert r.headers["content-type"] == "application/json"


@pytest.mark.asyncio
async def test_nul_byte_rejected():
    sent = await _raw_call([(b"x-tenant-id", b"CLIENT\x0001")])
    assert sent[0]["status"] == 400
    assert CALLS == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/health", "/metrics", "/docs", "/openapi.json"])
async def test_probe_routes_work_without_header(path):
    async with _client() as c:
        r = await c.get(path)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_jwt_claim_ignored_without_verifying_resolver():
    async with _client() as c:
        r = await c.get("/whoami", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_jwt_claim_used_with_resolver_and_header_has_priority():
    resolver = lambda tok: {"tenant_id": "FROM_JWT"} if tok == "good" else None
    async with _client(token_claims_resolver=resolver) as c:
        ok = await c.get("/whoami", headers={"Authorization": "Bearer good"})
        prio = await c.get(
            "/whoami", headers={"Authorization": "Bearer good", "X-Tenant-ID": "HDR"}
        )
        bad_tok = await c.get("/whoami", headers={"Authorization": "Bearer nope"})
        bad_hdr = await c.get(
            "/whoami", headers={"Authorization": "Bearer good", "X-Tenant-ID": "a b"}
        )
    assert ok.json()["state"] == "FROM_JWT"
    assert prio.json()["state"] == "HDR"
    assert bad_tok.status_code == 400
    assert bad_hdr.status_code == 400


@pytest.mark.asyncio
async def test_jwt_claim_malformed_rejected_and_resolver_errors_swallowed():
    def boom(tok):
        raise ValueError("bad signature")

    async with _client(token_claims_resolver=lambda t: {"tenant_id": "a/b"}) as c:
        assert (await c.get("/whoami", headers={"Authorization": "Bearer x"})).status_code == 400
    async with _client(token_claims_resolver=boom) as c:
        assert (await c.get("/whoami", headers={"Authorization": "Bearer x"})).status_code == 400


@pytest.mark.asyncio
async def test_valid_tenant_returns_200_and_injects_context():
    async with _client() as c:
        r = await c.get("/whoami", headers={"X-Tenant-ID": "CLIENT_01"})
    assert r.status_code == 200
    assert r.json() == {"state": "CLIENT_01", "ctx": "CLIENT_01"}


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant", ["a", "A.b:c-1", "0" * 64, "tenant:prod.eu-1"])
async def test_valid_formats_accepted(tenant):
    async with _client() as c:
        r = await c.get("/whoami", headers={"X-Tenant-ID": tenant})
    assert r.status_code == 200
    assert r.json()["state"] == tenant


@pytest.mark.asyncio
async def test_missing_header_rejected():
    async with _client() as c:
        r = await c.get("/whoami")
    assert r.status_code == 400
    assert CALLS == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tenant",
    [
        "",
        " ",
        "CLIENT 01",
        " CLIENT",
        "CLIENT ",
        "a/b",
        "../etc",
        "a\\b",
        "x'; DROP TABLE t;--",
        "' OR '1'='1",
        "a;b",
        "a*",
        "a%20b",
        "<script>",
        "x" * 65,
        "a,b",
        "a=b",
    ],
)
async def test_invalid_header_rejected_before_handler(tenant):
    async with _client() as c:
        r = await c.get("/whoami", headers={"X-Tenant-ID": tenant})
    assert r.status_code in (400, 401)
    assert CALLS == []


@pytest.mark.asyncio
async def test_duplicate_headers_rejected():
    async with _client() as c:
        r = await c.get("/whoami", headers=[("X-Tenant-ID", "A"), ("X-Tenant-ID", "B")])
    assert r.status_code == 400
    assert CALLS == []


@pytest.mark.asyncio
async def test_non_ascii_raw_bytes_rejected():
    app = _build_app()
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        sent.append(msg)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/whoami",
        "headers": [(b"x-tenant-id", b"\xff\xfe")],
    }
    await app(scope, receive, send)
    assert sent[0]["status"] == 400
    assert CALLS == []


def test_pattern_rejects_trailing_newline():
    assert TENANT_ID_PATTERN.match("abc\n") is None
    assert TENANT_ID_PATTERN.match("abc") is not None


@pytest.mark.asyncio
async def test_exempt_path_and_options_preflight_bypass():
    async with _client() as c:
        assert (await c.get("/health")).status_code == 200
        assert (await c.options("/whoami")).status_code != 400


@pytest.mark.asyncio
async def test_context_is_reset_after_request():
    async with _client() as c:
        await c.get("/whoami", headers={"X-Tenant-ID": "A"})
        await c.get("/whoami")
    assert current_tenant_id.get() is None
    with pytest.raises(LookupError):
        get_current_tenant_id()


@pytest.mark.asyncio
async def test_concurrent_requests_never_leak_tenant():
    n = 20
    async with _client() as c:

        async def one(i: int):
            tenant = f"CLIENT_{i:02d}"
            delay = 0.05 if i % 2 else 0.0
            r = await c.get(f"/slow?delay={delay}", headers={"X-Tenant-ID": tenant})
            return tenant, r

        results = await asyncio.gather(*(one(i) for i in range(1, n + 1)))
    for tenant, r in results:
        assert r.status_code == 200
        assert r.json() == {"before": tenant, "after": tenant, "state": tenant}


@pytest.mark.asyncio
async def test_concurrent_mixed_valid_and_invalid():
    async with _client() as c:
        good = [c.get("/slow?delay=0.02", headers={"X-Tenant-ID": f"OK{i}"}) for i in range(10)]
        bad = [c.get("/slow", headers={"X-Tenant-ID": "bad id"}) for _ in range(10)]
        res = await asyncio.gather(*good, *bad)
    assert [r.status_code for r in res] == [200] * 10 + [400] * 10
    assert [r.json()["after"] for r in res[:10]] == [f"OK{i}" for i in range(10)]
