from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app import __version__
from app.api.deps import SessionDep
from app.logging import get_logger
from app.schemas.common import HealthResponse, ReadyResponse

router = APIRouter(tags=["health"])
log = get_logger(__name__)


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    """Liveness: the process is up. No dependency checks."""
    return HealthResponse(status="ok", version=__version__)


@router.get("/readyz", response_model=ReadyResponse)
async def readyz(session: SessionDep, response: Response) -> ReadyResponse:
    """Readiness: can we serve traffic? Checks datastore connectivity."""
    checks: dict[str, str] = {}
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc.__class__.__name__}"
        log.warning("readiness_db_check_failed", error=str(exc))

    ok = all(v == "ok" for v in checks.values())
    response.status_code = status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(status="ok" if ok else "degraded", checks=checks)
