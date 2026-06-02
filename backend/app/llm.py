import logging

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


def build_chat_model(cfg: LLMConfig) -> ChatOpenAI:
    return ChatOpenAI(
        model=cfg.model,
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
        timeout=cfg.timeout_s,
    )


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
