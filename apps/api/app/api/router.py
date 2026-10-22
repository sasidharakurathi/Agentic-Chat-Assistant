from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    alerts,
    analytics,
    approvals,
    assist,
    assistants,
    attachments,
    auth,
    budgets,
    chat,
    conversation_export,
    conversations,
    data_sources,
    db_connections,
    evals,
    google_auth,
    groups,
    handoffs,
    health,
    http_connections,
    mcp_servers,
    memories,
    meta,
    orgs,
    retention,
    review_queue,
    shares,
    two_factor,
)

# Health lives at the root; everything else under /api/v1.
root_router = APIRouter()
root_router.include_router(health.router)

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(auth.router)
api_v1.include_router(google_auth.router)
api_v1.include_router(two_factor.router)
api_v1.include_router(orgs.router)
api_v1.include_router(assistants.router)
api_v1.include_router(assist.router)
api_v1.include_router(conversations.router)
api_v1.include_router(conversation_export.router)
api_v1.include_router(attachments.router)
api_v1.include_router(shares.router)
api_v1.include_router(handoffs.router)
api_v1.include_router(chat.router)
api_v1.include_router(groups.router)
api_v1.include_router(data_sources.router)
api_v1.include_router(db_connections.router)
api_v1.include_router(mcp_servers.router)
api_v1.include_router(http_connections.router)
api_v1.include_router(memories.router)
api_v1.include_router(approvals.router)
api_v1.include_router(budgets.router)
api_v1.include_router(alerts.router)
api_v1.include_router(retention.router)
api_v1.include_router(evals.router)
api_v1.include_router(review_queue.router)
api_v1.include_router(meta.router)
api_v1.include_router(analytics.router)

__all__ = ["api_v1", "root_router"]
