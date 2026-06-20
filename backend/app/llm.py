import asyncio
import logging
import time

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
    before_sleep_log,
)

from app.config import get_settings
from app.domain import LLMConfig
from app.errors import PROVIDER_UNAVAILABLE, RATE_LIMITED, classify

_retry_log = logging.getLogger("app.llm.retry")


def build_chat_model(cfg: LLMConfig) -> BaseChatModel:
    """Dispatch to the right langchain chat client based on cfg.provider.

    vLLM is OpenAI-compatible — uses ChatOpenAI with the user's custom base_url.
    Anthropic and Google have their own clients and ignore base_url.

    max_retries (Layer 1): the provider SDK retries 429/5xx itself, respecting
    Retry-After with jitter — better at it than we are. invoke_with_retry below
    is the outer backstop for transients the SDK can't see (graph/connection).
    """
    p = cfg.provider
    max_retries = get_settings().llm_max_retries
    if p in ("openai", "vllm"):
        return ChatOpenAI(
            model=cfg.model,
            base_url=cfg.base_url or None,
            api_key=cfg.api_key or "EMPTY",
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout_s,
            max_retries=max_retries,
        )
    if p == "anthropic":
        return ChatAnthropic(
            model=cfg.model,
            api_key=cfg.api_key,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout_s,
            max_retries=max_retries,
        )
    if p == "google":
        return ChatGoogleGenerativeAI(
            model=cfg.model,
            google_api_key=cfg.api_key,
            temperature=cfg.temperature,
            max_output_tokens=cfg.max_tokens,
            timeout=cfg.timeout_s,
            max_retries=max_retries,
        )
    raise ValueError(f"unknown provider: {p}")


def _is_retryable(exc: BaseException) -> bool:
    """Retry only what the taxonomy marks retryable (429 / 5xx / transient I/O).
    AUTH / INPUT_INVALID / STEP_LIMIT fail fast. CancelledError is BaseException
    (tenacity won't catch it) but guarded anyway — cancellation is control flow."""
    if isinstance(exc, asyncio.CancelledError):
        return False
    return classify(exc).retryable


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    # Exponential with jitter so retries across replicas don't synchronize into spikes.
    wait=wait_random_exponential(multiplier=1, max=30),
    before_sleep=before_sleep_log(_retry_log, logging.WARNING),
    reraise=True,
)
async def invoke_with_retry(agent, messages: dict, config: dict):
    """Invoke a compiled agent with retry on transient LLM errors (see _is_retryable)."""
    return await agent.ainvoke(messages, config=config)


class ProviderCircuitOpen(Exception):
    """Breaker is open for a provider endpoint — fail fast instead of piling on a
    provider that's already down. status_code=503 → classify() → PROVIDER_UNAVAILABLE."""
    status_code = 503


class _Breaker:
    """Minimal consecutive-failure breaker. Opens after `threshold` infra failures,
    rejects for `cooldown_s`, then lets trials through (half-open via natural expiry)."""

    def __init__(self, threshold: int, cooldown_s: int) -> None:
        self.threshold, self.cooldown_s = threshold, cooldown_s
        self.failures = 0
        self.open_until = 0.0

    def allow(self) -> bool:
        return not (self.failures >= self.threshold and time.monotonic() < self.open_until)

    def record_success(self) -> None:
        self.failures = 0
        self.open_until = 0.0

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.open_until = time.monotonic() + self.cooldown_s


# ponytail: per-process breakers keyed by provider endpoint. One worker protects
# itself; no cross-process state (Redis) until an outage proves that's needed.
# After cooldown all concurrent callers retry at once (bounded by worker max_jobs).
_breakers: dict[str, _Breaker] = {}


async def invoke_with_breaker(agent, messages: dict, config: dict, *, breaker_key: str):
    """invoke_with_retry guarded by a per-endpoint circuit breaker. Only infra
    failures (PROVIDER_UNAVAILABLE / RATE_LIMITED) trip it — AUTH / INPUT_INVALID
    are the caller's fault, not the provider's, so they never open the circuit."""
    s = get_settings()
    br = _breakers.setdefault(
        breaker_key, _Breaker(s.llm_breaker_threshold, s.llm_breaker_cooldown_s)
    )
    if not br.allow():
        raise ProviderCircuitOpen(f"circuit open for {breaker_key}")
    try:
        result = await invoke_with_retry(agent, messages, config)
    except Exception as exc:  # noqa: BLE001 — inspect then re-raise; CancelledError is BaseException, not caught here
        if classify(exc).code in (PROVIDER_UNAVAILABLE, RATE_LIMITED):
            br.record_failure()
        raise
    br.record_success()
    return result
