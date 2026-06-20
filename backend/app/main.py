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
from app.api.stats import router as stats_router
from app.api.slack import router as slack_router
from app.api.whatsapp import router as whatsapp_router
from app.api.tool_configs import router as tool_configs_router
from app.config import get_settings
from app.db import create_all, seed_defaults
from app.logging import configure_logging
from app.runtime.checkpointer import build_checkpointer
from app.services.run_service import drain_pending, reconcile_orphaned_runs, set_checkpointer
from app.users import UserCreate, UserRead, UserUpdate, auth_backend, fastapi_users

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    log.info("app.startup", env=settings.app_env)

    # Fail-fast: never run prod with the placeholder JWT secret — it forfeits auth
    # integrity (anyone can forge tokens). Make accidental deploy impossible.
    if settings.app_env == "prod" and settings.jwt_secret == "CHANGE_ME_IN_PROD":
        raise RuntimeError("JWT_SECRET must be overridden in prod (still the default placeholder)")

    # App-data DB. create_all is idempotent. README documents this is v1-grade
    # (no Alembic); prod swaps DATABASE_URL to Postgres and adds migrations.
    await create_all()
    await seed_defaults()

    # Backstop: fail any run left non-terminal by a prior crash so waiters aren't
    # stuck forever. Safe in multi-worker mode (only reaps runs older than 2× job_timeout).
    await reconcile_orphaned_runs()

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

    # arq pool — only in "queue" mode. The API enqueues; a separate `arq
    # app.worker.WorkerSettings` process consumes. If the pool can't be created,
    # start_run falls back to inline execution (logged loudly).
    app.state.arq_pool = None
    if settings.run_executor == "queue":
        try:
            from arq import create_pool
            from arq.connections import RedisSettings

            from app.services.run_service import set_arq_pool

            app.state.arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
            set_arq_pool(app.state.arq_pool)
            log.info("arq.pool_ready")
        except Exception as exc:
            log.error("arq.pool_unavailable", error=str(exc), hint="runs fall back to inline")

    # Slack — single platform bot (Socket Mode). Socket Mode is single-consumer:
    # if every API replica opened a socket, each message would process N times.
    # A Redis leader lock ensures exactly ONE replica runs it; a poller takes over
    # if the leader dies. See _slack_leadership.
    app.state.slack = None
    app.state.slack_task = None
    app.state.slack_leader = None
    app.state.slack_leader_task = asyncio.create_task(_slack_leadership(app))

    # WhatsApp — pre-warm adapter cache for all users with saved Twilio creds.
    # No persistent connection needed (webhook-based, stateless).
    from app.api.whatsapp import warm_adapters_from_db
    await warm_adapters_from_db()

    try:
        yield
    finally:
        log.info("app.shutdown")
        if app.state.slack_leader_task is not None:
            app.state.slack_leader_task.cancel()
        if app.state.slack is not None:
            await app.state.slack.stop()
        if app.state.slack_task is not None:
            app.state.slack_task.cancel()
        if app.state.slack_leader is not None:
            await app.state.slack_leader.release()  # let another replica take over promptly
        await drain_pending()
        if app.state.arq_pool is not None:
            await app.state.arq_pool.aclose()
        if app.state.redis_client is not None:
            await app.state.redis_client.aclose()
        from app.redis_client import aclose_redis
        await aclose_redis()


async def _resolve_slack_tokens() -> tuple[str | None, str | None]:
    """Env tokens, else the first user who connected via POST /slack/connect."""
    s = get_settings()
    if s.slack_bot_token and s.slack_app_token:
        return s.slack_bot_token, s.slack_app_token
    from sqlalchemy import select
    from app.db import get_session_factory
    from app.db.models import UserDB as _UserDB
    async with get_session_factory()() as _s:
        row = (await _s.execute(
            select(_UserDB).where(
                _UserDB.slack_bot_token.isnot(None), _UserDB.slack_app_token.isnot(None)
            ).limit(1)
        )).scalar_one_or_none()
    return (row.slack_bot_token, row.slack_app_token) if row else (None, None)


def _start_slack(app: FastAPI, bot: str, app_token: str) -> None:
    from app.integrations.channels.slack_adapter import SlackAdapter
    app.state.slack = SlackAdapter(bot, app_token)
    app.state.slack_task = asyncio.create_task(app.state.slack.start())


async def _slack_leadership(app: FastAPI) -> None:
    """Run Slack Socket Mode on exactly one replica. Acquire a Redis leader lock;
    only the holder starts the adapter; refresh the lease while leading. If Redis is
    unavailable (single-process dev), start unguarded — there's no contention to lose."""
    from app.leader import Leader

    bot, app_token = await _resolve_slack_tokens()
    if not (bot and app_token):
        return  # Slack not configured

    leader = Leader("slack:leader", ttl=30)
    app.state.slack_leader = leader
    while True:
        try:
            if app.state.slack is None:
                if await leader.acquire():
                    _start_slack(app, bot, app_token)
                    log.info("slack.leader_acquired")
            else:
                await leader.refresh()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Redis down: in a single-process deployment there's no contention, so
            # start unguarded rather than never starting Slack at all.
            if app.state.slack is None:
                log.warning("slack.leader_unavailable_starting_unguarded", error=str(exc))
                _start_slack(app, bot, app_token)
        await asyncio.sleep(10)


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


# Redis-backed storage in prod so the IP rate limit is shared across replicas and
# survives restarts (in-memory only works for a single process). dev/test stay
# in-memory so they need no Redis. Per-user fairness is the concurrency cap (3b);
# this limiter is the coarse cross-replica DoS guard.
_lim_storage = get_settings().redis_url if get_settings().app_env == "prod" else None
limiter = Limiter(
    key_func=get_remote_address, default_limits=["60/minute"], storage_uri=_lim_storage
)


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
    origins = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
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
    app.include_router(whatsapp_router)
    app.include_router(personas_router)
    app.include_router(providers_router)
    app.include_router(skills_router)
    app.include_router(mcp_router)
    app.include_router(tool_configs_router)
    app.include_router(chats_router)
    app.include_router(runs_router)
    app.include_router(stats_router)

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
