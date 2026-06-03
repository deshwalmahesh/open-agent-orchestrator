import asyncio
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.api.agents import router as agents_router
from app.api.chats import router as chats_router
from app.api.health import router as health_router
from app.api.mcp_servers import router as mcp_router
from app.api.personas import router as personas_router
from app.api.providers import router as providers_router
from app.api.runs import router as runs_router
from app.api.skills import router as skills_router
from app.api.slack import router as slack_router
from app.api.tool_configs import router as tool_configs_router
from app.config import get_settings
from app.db import create_all, seed_defaults
from app.logging import configure_logging
from app.runtime.checkpointer import build_checkpointer
from app.services.run_service import drain_pending, set_checkpointer
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
    await seed_defaults()

    # Checkpointer is required for any Run, but we want /health to stay green
    # if Redis is briefly down during dev. Log loudly; downstream endpoints
    # check for None and fail with a clear error.
    app.state.checkpointer = None
    app.state.redis_client = None
    try:
        saver, client = await build_checkpointer()
        app.state.checkpointer = saver
        app.state.redis_client = client
        set_checkpointer(saver)
    except Exception as exc:
        log.warning(
            "checkpointer.unavailable",
            error=str(exc),
            hint="run `docker compose up` — runs still work without Redis but no within-run checkpointing",
        )

    # Slack — single platform bot. Starts from env vars OR from the first user
    # who connected via POST /slack/connect (tokens saved in UserDB).
    app.state.slack = None
    app.state.slack_task = None
    _slack_bot = settings.slack_bot_token
    _slack_app = settings.slack_app_token
    if not (_slack_bot and _slack_app):
        # Fall back to any user's saved tokens
        from sqlalchemy import select
        from app.db import get_session_factory
        from app.db.models import UserDB as _UserDB
        async with get_session_factory()() as _s:
            _row = (await _s.execute(
                select(_UserDB).where(
                    _UserDB.slack_bot_token.isnot(None),
                    _UserDB.slack_app_token.isnot(None),
                ).limit(1)
            )).scalar_one_or_none()
            if _row:
                _slack_bot = _row.slack_bot_token
                _slack_app = _row.slack_app_token
    if _slack_bot and _slack_app:
        from app.integrations.channels.slack_adapter import SlackAdapter
        app.state.slack = SlackAdapter(_slack_bot, _slack_app)
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


limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])


def create_app() -> FastAPI:
    from pathlib import Path
    app = FastAPI(
        title="AI Agent Orchestration Platform",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",   # Vite dev (no Docker)
            "http://localhost:80",     # Docker compose frontend
            "http://localhost",        # Docker compose frontend (port 80, no explicit port)
            "http://127.0.0.1:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
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
    app.include_router(slack_router)
    app.include_router(personas_router)
    app.include_router(providers_router)
    app.include_router(skills_router)
    app.include_router(mcp_router)
    app.include_router(tool_configs_router)
    app.include_router(chats_router)
    app.include_router(runs_router)

    # Serve built frontend in prod (Dockerfile copies dist → /app/static).
    # Mounted AFTER API routes so they always win. SPA fallback: unknown paths
    # return index.html and let React Router handle them client-side.
    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.is_dir():
        from starlette.responses import FileResponse

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            file = (static_dir / full_path).resolve()
            if file.is_relative_to(static_dir.resolve()) and file.is_file():
                return FileResponse(file)
            return FileResponse(static_dir / "index.html")

    return app


app = create_app()
