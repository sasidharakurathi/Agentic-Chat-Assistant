from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.agent import approval_registry, interrupts
from app.api.errors import register_exception_handlers
from app.api.middleware import RequestContextMiddleware
from app.api.router import api_v1, root_router
from app.config import settings
from app.datasources import pool as datasource_pool
from app.logging import configure_logging, get_logger
from app.observability.otel import setup_tracing, shutdown_tracing

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
    # The cross-worker halves of approvals and interrupts: a decision or a
    # "stop" that lands on a different worker from the turn it is for. The
    # approvals listener existed from task 3.8 but was never started, so that
    # fallback had never actually run.
    await approval_registry.start_listener()
    await interrupts.start_listener()
    yield
    await interrupts.stop_listener()
    await approval_registry.stop_listener()
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

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-request-id"],
    )

    register_exception_handlers(app)
    setup_tracing(app)

    app.include_router(root_router)
    app.include_router(api_v1)
    return app


app = create_app()
