"""Live tests against the real LLM gateway + Tavily. Skipped if creds missing."""

import pytest

from app.config import get_settings

_s = get_settings()
_HAS_LLM = bool(_s.vllm_base_url and _s.vllm_api_key and _s.vllm_default_model)
_HAS_TAVILY = bool(_s.tavily_api_key)


@pytest.mark.skipif(not _HAS_LLM, reason="no live LLM creds")
def test_live_llm_text_response():
    from app.domain import LLMConfig
    from app.llm import build_chat_model

    m = build_chat_model(LLMConfig(
        base_url=_s.vllm_base_url, api_key=_s.vllm_api_key, model=_s.vllm_default_model,
        max_tokens=128, timeout_s=60,
    ))
    resp = m.invoke([{"role": "user", "content": "Reply with exactly one word: pong"}])
    assert resp.usage_metadata["total_tokens"] > 0


@pytest.mark.skipif(not _HAS_LLM, reason="no live LLM creds")
def test_live_llm_emits_tool_call():
    from app.domain import LLMConfig
    from app.llm import build_chat_model
    from app.runtime.tools import get_tools

    m = build_chat_model(LLMConfig(
        base_url=_s.vllm_base_url, api_key=_s.vllm_api_key, model=_s.vllm_default_model,
        max_tokens=512, timeout_s=60,
    )).bind_tools(get_tools(["calculator"]))
    resp = m.invoke([{"role": "user", "content": "What is 17 times 23? Use the calculator tool."}])
    assert resp.tool_calls and resp.tool_calls[0]["name"] == "calculator"
    assert "expression" in resp.tool_calls[0]["args"]


@pytest.mark.skipif(not _HAS_TAVILY, reason="no TAVILY_API_KEY")
def test_live_tavily_search():
    from app.runtime.tools import REGISTRY

    assert "web_search" in REGISTRY
    r = REGISTRY["web_search"].invoke({"query": "who founded Anthropic"})
    assert isinstance(r, dict) and r.get("results")
