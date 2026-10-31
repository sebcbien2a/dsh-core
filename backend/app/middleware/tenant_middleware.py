"""P10.2 - Multi-tenant context middleware (pure ASGI).

Tenant resolution order:
1. ``X-Tenant-ID`` header;
2. ``tenant_id`` claim of the ``Authorization: Bearer`` token, ONLY if a
   ``token_claims_resolver`` (which must verify the signature) is injected.
   Unverified token claims are never trusted.

The tenant is validated against the official pattern; otherwise the request is
rejected with HTTP 400 ``{"detail": "INVALID_OR_MISSING_TENANT_ID"}`` before any
route, graph node or persistence call runs. Technical probe routes
(``DEFAULT_EXEMPT_PATHS``) and CORS preflight ``OPTIONS`` are exempt. The
validated value is exposed via ``request.state.tenant_id`` and the
``current_tenant_id`` ContextVar.
"""

from __future__ import annotations

import json
import re
from contextvars import ContextVar
from typing import Any, Awaitable, Callable, Iterable

TENANT_HEADER = b"x-tenant-id"
# \Z (not $) so a trailing newline is never accepted.
TENANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}\Z")

ERROR_DETAIL = "INVALID_OR_MISSING_TENANT_ID"
DEFAULT_EXEMPT_PATHS = ("/health", "/metrics", "/docs", "/openapi.json")
ClaimsResolver = Callable[[str], "dict[str, Any] | None"]

current_tenant_id: ContextVar[str | None] = ContextVar("current_tenant_id", default=None)

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


def get_current_tenant_id() -> str:
    """Return the tenant bound to the current request or raise."""
    tenant = current_tenant_id.get()
    if tenant is None:
        raise LookupError("No tenant bound to the current request context")
    return tenant


def _extract_tenant(
    headers: Iterable[tuple[bytes, bytes]],
    resolver: ClaimsResolver | None = None,
) -> str | None:
    """Return the validated tenant id or ``None`` if missing/invalid."""
    headers = list(headers)
    values = [v for k, v in headers if k.lower() == TENANT_HEADER]
    if values:
        if len(values) > 1:
            return None
        try:
            raw = values[0].decode("ascii")
        except UnicodeDecodeError:
            return None
        return raw if TENANT_ID_PATTERN.match(raw) else None

    if resolver is None:
        return None
    auth = [v for k, v in headers if k.lower() == b"authorization"]
    if len(auth) != 1:
        return None
    try:
        scheme, _, token = auth[0].decode("ascii").partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None
        claim = (resolver(token) or {}).get("tenant_id")
    except Exception:
        return None
    return claim if isinstance(claim, str) and TENANT_ID_PATTERN.match(claim) else None


class TenantContextMiddleware:
    def __init__(
        self,
        app: Any,
        exempt_paths: Iterable[str] = DEFAULT_EXEMPT_PATHS,
        token_claims_resolver: ClaimsResolver | None = None,
    ) -> None:
        self.app = app
        self.exempt_paths = frozenset(exempt_paths)
        self.token_claims_resolver = token_claims_resolver

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if (
            kind not in ("http", "websocket")
            or (kind == "http" and scope.get("method") == "OPTIONS")  # CORS preflight
            or scope.get("path") in self.exempt_paths
        ):
            await self.app(scope, receive, send)
            return

        tenant_id = _extract_tenant(scope.get("headers", []), self.token_claims_resolver)
        if tenant_id is None:
            await self._reject(kind, send)
            return

        scope.setdefault("state", {})["tenant_id"] = tenant_id
        token = current_tenant_id.set(tenant_id)
        try:
            await self.app(scope, receive, send)
        finally:
            current_tenant_id.reset(token)

    @staticmethod
    async def _reject(kind: str, send: Send) -> None:
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"detail": ERROR_DETAIL}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 400,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
