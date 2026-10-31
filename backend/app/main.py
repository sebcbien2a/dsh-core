"""ASGI entrypoint: the n8n webhook app wrapped with tenant isolation (P10.2)."""

from app.middleware.tenant_middleware import TenantContextMiddleware
from app.n8n_api import app
from app.routers.accounting_export import router as accounting_export_router
from app.routers.reconciliation_arbitration import router as arbitration_router

app.include_router(arbitration_router)
app.include_router(accounting_export_router)
app.add_middleware(TenantContextMiddleware)
