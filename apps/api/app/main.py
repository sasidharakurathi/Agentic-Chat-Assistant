from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.agent import interrupts
from app.api.errors import register_exception_handlers
from app.api.middleware import RequestContextMiddleware
from app.api.ratelimit import RateLimitMiddleware
from app.api.router import api_v1, root_router
from app.config import settings
from app.datasources import pool as datasource_pool
from app.logging import configure_logging, get_logger
from app.observability.otel import setup_tracing, shutdown_tracing
from app.services import turns

configure_logging(settings.log_level, settings.log_format)
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    log.info(
        "api_starting",
        version=__version__,
        env=settings.app_env,
        database=settings.database_url.rsplit("@", 1)[-1],
    )
    # The cross-worker half of interrupts: a "stop" that lands on a
    # different worker from the turn it is for. (Approvals had one too, until
    # Phase 9.1: no turn waits for a decision any more.)
    await interrupts.start_listener()
    yield
    # Turns running on this process (QOS-01) are stopped properly, each
    # recorded as aborted, rather than dropped with the process.
    await turns.shutdown()
    await interrupts.stop_listener()
    # Requests this process was running in development (`ACTION_EXECUTOR=
    # inline`) finish first; in production they run on the worker.
    from app.services import executor

    await executor.drain()
    # Close pooled connections to tenant databases. `close_all` was defined
    # for this and never called, so a shutdown just dropped live sessions on
    # the tenants' servers for them to time out.
    await datasource_pool.close_all()
    shutdown_tracing()
    log.info("api_stopping")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        summary="Backend for the dynamic agentic chat assistant platform.",
        lifespan=lifespan,
    )

    # Innermost of the three: a 429 still gets a request id and CORS
    # headers (a browser can't read a response without them).
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        # Sign-in is a bearer header, never a cookie: nothing for the browser
        # to attach by itself, so a careless CORS_ORIGINS can't be used to
        # ride a session.
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        # Retry-After: so the web app can read how long a 429 asks it to wait.
        # X-Turn-Id: the turn to watch again after a dropped stream (8.6).
        # Content-Disposition: the name an export is saved under (10.5).
        expose_headers=["x-request-id", "retry-after", "x-turn-id", "content-disposition"],
    )

    register_exception_handlers(app)
    setup_tracing(app)

    app.include_router(root_router)
    app.include_router(api_v1)
    return app


app = create_app()
