import logging

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from app.domain import LLMConfig

_retry_log = logging.getLogger("app.llm.retry")

# Transient errors worth retrying. AuthenticationError / BadRequestError are bugs — fail fast.
_RETRYABLE = (TimeoutError, ConnectionError, OSError)


def build_chat_model(cfg: LLMConfig) -> BaseChatModel:
    """Dispatch to the right langchain chat client based on cfg.provider.

    vLLM is OpenAI-compatible — uses ChatOpenAI with the user's custom base_url.
    Anthropic and Google have their own clients and ignore base_url.
    """
    p = cfg.provider
    if p in ("openai", "vllm"):
        return ChatOpenAI(
            model=cfg.model,
            base_url=cfg.base_url or None,
            api_key=cfg.api_key or "EMPTY",
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout_s,
        )
    if p == "anthropic":
        return ChatAnthropic(
            model=cfg.model,
            api_key=cfg.api_key,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout_s,
        )
    if p == "google":
        return ChatGoogleGenerativeAI(
            model=cfg.model,
            google_api_key=cfg.api_key,
            temperature=cfg.temperature,
            max_output_tokens=cfg.max_tokens,
            timeout=cfg.timeout_s,
        )
    raise ValueError(f"unknown provider: {p}")


@retry(
    retry=retry_if_exception_type(_RETRYABLE),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    before_sleep=before_sleep_log(_retry_log, logging.WARNING),
    reraise=True,
)
async def invoke_with_retry(agent, messages: dict, config: dict):
    """Invoke a compiled agent with retry on transient LLM errors."""
    return await agent.ainvoke(messages, config=config)
