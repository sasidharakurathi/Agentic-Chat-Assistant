from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import auth, health, orgs

# Health lives at the root; everything else under /api/v1.
root_router = APIRouter()
root_router.include_router(health.router)

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(auth.router)
api_v1.include_router(orgs.router)

__all__ = ["api_v1", "root_router"]
