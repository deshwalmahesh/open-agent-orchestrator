from langchain_openai import ChatOpenAI

from app.domain import LLMConfig


def build_chat_model(cfg: LLMConfig) -> ChatOpenAI:
    return ChatOpenAI(
        model=cfg.model,
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
        timeout=cfg.timeout_s,
    )
