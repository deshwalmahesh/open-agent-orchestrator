from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Single source of truth for env-driven config. Never read os.environ elsewhere."""

    # Look for .env in backend/ first, then project root — so devs can keep one
    # .env at the repo root and Docker/CI can override with backend/.env.
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # LLM (OpenAI-compatible gateway: vLLM / LiteLLM / OpenAI / Gemini-via-proxy)
    vllm_base_url: str | None = None
    vllm_api_key: str | None = None
    vllm_default_model: str | None = None
    # Provider-SDK retry count (Layer 1: SDK retries 429/5xx respecting Retry-After).
    # Our tenacity wrapper is the outer backstop. Keep low so the two don't stack badly.
    llm_max_retries: int = 2
    # Circuit breaker: after N consecutive infra failures to a provider endpoint, fail
    # fast (PROVIDER_UNAVAILABLE) for a cooldown instead of piling retries on a down provider.
    llm_breaker_threshold: int = 5
    llm_breaker_cooldown_s: int = 30

    # Web search (tool auto-disables if unset)
    tavily_api_key: str | None = None

    # Langfuse (optional, off-the-shelf LLM tracing). All three unset = disabled (no-op).
    # The SDK reads these same names from env directly; we mirror them here for the
    # enabled-check and to keep config in one place.
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    # LangGraph checkpointer (ephemeral session/thread state)
    redis_url: str = "redis://localhost:6379/0"
    redis_ttl_seconds: int = 60 * 60 * 24 * 30  # 30d idle threads expire

    # Run execution mode:
    #   "inline" — asyncio.create_task in the API process (dev/test/single-box, default).
    #   "queue"  — enqueue to an arq worker on Redis (prod: durable, bounded, crash-safe).
    run_executor: Literal["inline", "queue"] = "inline"
    worker_max_jobs: int = 10   # arq worker global concurrency cap = backpressure
    run_timeout_s: int = 300    # per-run wall-clock cap (arq job_timeout)
    run_max_tries: int = 3      # arq max attempts (bounds crash redelivery)
    # Global load-shed: reject new runs with 503 once the arq backlog exceeds this
    # (fail fast > unbounded latency). 0 = disabled (inline mode / no cap).
    max_queue_depth: int = 0

    # App data DB. SQLite for v1 (single file, no service). Swap URL for Postgres prod.
    database_url: str = "sqlite+aiosqlite:///./dev.db"
    # Connection pool (Postgres only; ignored for SQLite). At N replicas keep
    # N*(pool_size+max_overflow) under Postgres max_connections (~100), or front
    # with PgBouncer and set db_use_null_pool=true so the proxy owns pooling.
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30        # seconds to wait for a free connection before erroring
    db_pool_recycle: int = 1800      # recycle conns older than this (drops stale/closed ones)
    db_pool_pre_ping: bool = True    # validate a conn before use (cheap SELECT 1)
    db_use_null_pool: bool = False   # True behind PgBouncer (transaction mode)

    # fastapi-users JWT. Override `jwt_secret` in prod via env (startup refuses the
    # default when app_env=="prod" — see main.lifespan).
    jwt_secret: str = "CHANGE_ME_IN_PROD"
    jwt_lifetime_seconds: int = 60 * 60 * 24 * 7  # 7d

    # Field-encryption keys for secrets at rest (Fernet), comma-separated, newest
    # first (rotation: prepend a new key). Generate one with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Unset = passthrough (dev/test); prod refuses to boot without it (see main.lifespan).
    secret_encryption_keys: str | None = None

    # CORS allowed origins (comma-separated). Defaults cover local dev; set real
    # origins per deploy. "*" is intentionally NOT a default (credentials are sent).
    cors_origins: str = "http://localhost:5173,http://localhost:80,http://localhost,http://127.0.0.1:5173"

    # Attachment limits at the message boundary — base64 is decoded into memory, so
    # unbounded uploads are an OOM/DoS vector. Reject with 413 past these.
    max_upload_files: int = 5
    max_upload_mb: int = 10

    # Slack — single platform bot (Socket Mode). Both unset = adapter stays off.
    slack_bot_token: str | None = None  # xoxb- or xoxe- (bot user / access token)
    slack_app_token: str | None = None  # xapp- (Socket Mode app-level token)

    # Twilio WhatsApp — per-user credentials (env vars are optional bootstrap).
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_whatsapp_from: str | None = None  # "whatsapp:+14155238886"

    # Public base URL of this server. Used to compute webhook URLs.
    # Overridden per-user via webhook_base_url on UserDB after first deploy.
    base_url: str = "http://localhost:8000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
