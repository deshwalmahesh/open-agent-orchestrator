import asyncio
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request

from app.api.agents import router as agents_router
from app.api.chats import router as chats_router
from app.api.health import router as health_router
from app.api.personas import router as personas_router
from app.api.runs import router as runs_router
from app.api.workflows import router as workflows_router
from app.config import get_settings
from app.db import create_all, get_session_factory
from app.db.seeds import seed_templates
from app.logging import configure_logging
from app.runtime.checkpointer import build_checkpointer
from app.services.run_service import drain_pending
from app.users import UserCreate, UserRead, UserUpdate, auth_backend, fastapi_users

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    log.info("app.startup", env=settings.app_env)

    # App-data DB. create_all is idempotent. README documents this is v1-grade
    # (no Alembic); prod swaps DATABASE_URL to Postgres and adds migrations.
    await create_all()
    await seed_templates(get_session_factory())

    # Checkpointer is required for any Run, but we want /health to stay green
    # if Redis is briefly down during dev. Log loudly; downstream endpoints
    # check for None and fail with a clear error.
    app.state.checkpointer = None
    app.state.redis_client = None
    try:
        saver, client = await build_checkpointer()
        app.state.checkpointer = saver
        app.state.redis_client = client
    except Exception as exc:
        log.warning(
            "checkpointer.unavailable",
            error=str(exc),
            hint="run `docker compose up redis -d` (runs without it work; chats won't)",
        )

    # Slack — single platform bot. Stays off unless BOTH tokens are configured.
    app.state.slack = None
    app.state.slack_task = None
    if settings.slack_bot_token and settings.slack_app_token:
        from app.integrations.channels.slack_adapter import SlackAdapter

        app.state.slack = SlackAdapter(settings.slack_bot_token, settings.slack_app_token)
        app.state.slack_task = asyncio.create_task(app.state.slack.start())

    try:
        yield
    finally:
        log.info("app.shutdown")
        if app.state.slack is not None:
            await app.state.slack.stop()
        if app.state.slack_task is not None:
            app.state.slack_task.cancel()
        await drain_pending()
        if app.state.redis_client is not None:
            await app.state.redis_client.aclose()


async def _request_id_middleware(request: Request, call_next):
    """Mint or accept X-Request-ID and bind to structlog contextvars."""
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    structlog.contextvars.bind_contextvars(request_id=rid)
    try:
        response = await call_next(request)
    finally:
        structlog.contextvars.clear_contextvars()
    response.headers["X-Request-ID"] = rid
    return response


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Agent Orchestration Platform",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.middleware("http")(_request_id_middleware)
    app.include_router(health_router)
    app.include_router(
        fastapi_users.get_auth_router(auth_backend), prefix="/auth/jwt", tags=["auth"]
    )
    app.include_router(
        fastapi_users.get_register_router(UserRead, UserCreate), prefix="/auth", tags=["auth"]
    )
    app.include_router(
        fastapi_users.get_users_router(UserRead, UserUpdate), prefix="/users", tags=["users"]
    )
    app.include_router(agents_router)
    app.include_router(personas_router)
    app.include_router(workflows_router)
    app.include_router(chats_router)
    app.include_router(runs_router)
    return app


app = create_app()
