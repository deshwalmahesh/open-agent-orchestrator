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

    # Web search (tool auto-disables if unset)
    tavily_api_key: str | None = None

    # LangGraph checkpointer (ephemeral session/thread state)
    redis_url: str = "redis://localhost:6379/0"
    redis_ttl_seconds: int = 60 * 60 * 24 * 30  # 30d idle threads expire

    # App data DB. SQLite for v1 (single file, no service). Swap URL for Postgres prod.
    database_url: str = "sqlite+aiosqlite:///./dev.db"

    # fastapi-users JWT. Override `jwt_secret` in prod via env.
    jwt_secret: str = "CHANGE_ME_IN_PROD"
    jwt_lifetime_seconds: int = 60 * 60 * 24 * 7  # 7d

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
