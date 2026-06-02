"""Tool-logic regressions. Anything we OWN (BYOK seam, calculator math + safety,
sandbox happy/error/timeout) lives here. Skipping tests that just prove a
LangChain primitive works — integration test catches those."""

import pytest

from app.runtime.tools import build_registry, calculator, html_to_markdown, python_sandbox


def test_calculator_basic_arithmetic():
    assert calculator.invoke({"expression": "2 + 2"}) == "4"
    assert calculator.invoke({"expression": "2*(3+4)"}) == "14"


def test_calculator_rejects_python_code():
    with pytest.raises(Exception):
        calculator.invoke({"expression": "__import__('os').system('ls')"})


def test_html_to_markdown_converts_headings():
    r = html_to_markdown.invoke({"html": "<h1>Title</h1><p>body</p>"})
    assert "Title" in r and "body" in r


async def test_python_sandbox_returns_stdout():
    out = await python_sandbox.ainvoke({"code": "print(2+2)"})
    assert out.strip() == "4"


async def test_python_sandbox_raises_on_subprocess_error():
    with pytest.raises(RuntimeError):
        await python_sandbox.ainvoke({"code": "1/0"})


async def test_python_sandbox_raises_on_timeout():
    with pytest.raises(TimeoutError):
        await python_sandbox.ainvoke({"code": "import time; time.sleep(10)"})


def test_per_user_byok_isolation():
    """Two different Tavily keys → two different web_search instances.
    Stateless tools shared. Locks the 'use ours / BYOK' seam."""
    a = build_registry(tool_configs={"web_search": {"api_key": "tvly-user-a"}})
    b = build_registry(tool_configs={"web_search": {"api_key": "tvly-user-b"}})
    assert "web_search" in a and "web_search" in b
    assert a["web_search"] is not b["web_search"]
    assert a["calculator"] is b["calculator"]
