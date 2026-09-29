from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    approvals,
    assistants,
    auth,
    conversations,
    data_sources,
    db_connections,
    health,
    mcp_servers,
    meta,
    orgs,
)

# Health lives at the root; everything else under /api/v1.
root_router = APIRouter()
root_router.include_router(health.router)

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(auth.router)
api_v1.include_router(orgs.router)
api_v1.include_router(assistants.router)
api_v1.include_router(conversations.router)
api_v1.include_router(data_sources.router)
api_v1.include_router(db_connections.router)
api_v1.include_router(mcp_servers.router)
api_v1.include_router(approvals.router)
api_v1.include_router(meta.router)

__all__ = ["api_v1", "root_router"]
