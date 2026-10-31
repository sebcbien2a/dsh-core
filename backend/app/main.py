"""ASGI entrypoint: the n8n webhook app wrapped with tenant isolation (P10.2)."""

from app.middleware.tenant_middleware import TenantContextMiddleware
from app.n8n_api import app

app.add_middleware(TenantContextMiddleware)
