from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api.health import router as health_router
from app.config import get_settings
from app.logging import configure_logging

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan.

    Future owners attach shared clients here (DB pool, AsyncOpenAI, Slack,
    httpx) and detach on shutdown. Empty for P0.
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    log.info("app.startup", env=settings.app_env)
    try:
        yield
    finally:
        log.info("app.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Agent Orchestration Platform",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(health_router)
    return app


app = create_app()
