"""Tool registry. LangChain BaseTool instances; tools raise on error and
ToolNode reports the message back to the LLM. Call `build_registry(tavily_api_key=...)`
to assemble a registry with a custom (e.g. per-user) Tavily key."""

from __future__ import annotations

import asyncio
import sys

import httpx
import numexpr
import structlog
from langchain.tools import tool
from langchain_core.tools import BaseTool
from langchain_tavily import TavilySearch
from markdownify import markdownify
from pypdf import PdfReader

from app.config import get_settings

log = structlog.get_logger()


# Hard cap on tool output. Anything larger gets truncated with a hint so the
# LLM (a) still gets useful content and (b) doesn't blow its context window
# on a single 250KB page like goal.com. ~40k chars ≈ ~10k tokens — fits
# comfortably alongside system prompt + chat history + multiple tool calls
# even on a 128k-context model.
MAX_TOOL_OUTPUT_CHARS = 40_000


def _truncate(text: str, *, tool_name: str) -> str:
    if len(text) <= MAX_TOOL_OUTPUT_CHARS:
        return text
    head = text[:MAX_TOOL_OUTPUT_CHARS]
    return (
        f"{head}\n\n[truncated: {tool_name} returned {len(text):,} chars, "
        f"showing first {MAX_TOOL_OUTPUT_CHARS:,}. If you need more, request "
        f"a narrower URL/path or post-process this chunk with html_to_markdown.]"
    )


@tool
def calculator(expression: str) -> str:
    """Evaluate a single numeric expression and return the result as a string.

    USE WHEN: the user asks for arithmetic, percentages, unit conversions
    expressible as math (e.g. '2*(3+4)', 'sqrt(16)', '0.15 * 4200').
    DO NOT USE FOR: anything requiring variables, Python syntax, lookups, or
    multi-step logic — use `python_sandbox` for those.
    Engine: numexpr (numbers + math functions only)."""
    return str(numexpr.evaluate(expression).item())


@tool
def html_to_markdown(html: str) -> str:
    """Convert an HTML string to compact Markdown.

    USE WHEN: you have raw HTML (typically from `fetch_page`) and need to
    extract the readable text/structure for the LLM. Markdown is ~3× denser
    than HTML, so this is the right way to compress a fetched page before
    quoting or summarising it.
    DO NOT USE FOR: URLs (this takes an HTML string, not a URL — call
    `fetch_page` first) or non-HTML text."""
    return markdownify(html)


@tool
def pdf_to_text(path: str) -> str:
    """Extract plain text from a PDF file at a LOCAL file path.

    USE WHEN: the user has attached a PDF and you need its text content.
    Input is a filesystem path (e.g. an attachment path), NOT a URL — there
    is no network fetch. Returns concatenated page text separated by blank
    lines. Output is truncated if the PDF is very long."""
    reader = PdfReader(path)
    text = "\n\n".join((p.extract_text() or "") for p in reader.pages)
    return _truncate(text, tool_name="pdf_to_text")


@tool
def ask_human(question: str) -> str:
    """Ask a human a question and wait for their answer before continuing.

    USE WHEN: you genuinely need a decision, approval, or clarification that only a
    person can give (ambiguous request, missing info, an action that needs sign-off).
    The run PAUSES until the human replies; their reply is fed back to you as this
    tool's result. Ask one clear, specific question.
    DO NOT USE FOR: things you can answer yourself or look up with another tool.

    Note: this tool never runs on its own — when human-in-the-loop is enabled the
    human's reply is substituted as the result. This fallback string only appears if
    it is somehow invoked without a human review configured."""
    return "[No human answer was provided.]"


@tool
async def fetch_page(url: str) -> str:
    """Fetch the raw HTML of a single URL (10s timeout, follows redirects).

    USE WHEN: you have a specific URL and need its page contents. Typical
    follow-up: pipe the result through `html_to_markdown` to compress it
    before reasoning over it.
    DO NOT USE FOR: open-ended factual questions ('current weather in X',
    'latest news', 'who is Y') — those should go through `web_search`
    instead. Fetching a major consumer site (goal.com, amazon.com, etc.)
    returns hundreds of KB of HTML and is rarely what the user wants.
    LIMITS: response body is capped at ~40k chars; oversized pages are
    truncated with a hint. Non-2xx responses raise an error the LLM can
    read and react to."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return _truncate(resp.text, tool_name="fetch_page")


@tool
async def python_sandbox(code: str) -> str:
    """Run a short Python snippet in an isolated subprocess (5s timeout) and
    return its stdout as a string.

    USE WHEN: you need real Python — list/string manipulation, regex, json
    parsing, multi-step arithmetic, date math, simple data wrangling.
    DO NOT USE FOR: single arithmetic expressions (use `calculator`), code
    that needs network or file system access to user data, or anything the
    user could do with a built-in tool.
    OUTPUT CONTRACT: must `print(...)` what you want back — return values
    are discarded. Stdout is capped at ~40k chars (truncated with a hint).
    Stderr/non-zero exit becomes an error the LLM can read.
    ISOLATION: `-I` blocks user site-packages/PYTHONPATH; empty env so the
    subprocess inherits no host secrets. The subprocess still has full
    network access and the host's Python install — NOT a security boundary
    for untrusted code."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-c", code,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={},
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError("python_sandbox: execution exceeded 5s")
    if proc.returncode != 0:
        raise RuntimeError(f"python_sandbox: {err.decode(errors='replace').strip()}")
    return _truncate(out.decode(errors="replace"), tool_name="python_sandbox")


# Human-readable labels for the registry keys. The agent's config references the
# stable lowercase key (e.g. "web_search"); the UI shows DISPLAY_NAMES[key].
# Unknown keys fall back to the registry key itself.
DISPLAY_NAMES: dict[str, str] = {
    "calculator": "Calculator",
    "web_search": "Web Search",
    "html_to_markdown": "HTML → Markdown",
    "pdf_to_text": "PDF → Text",
    "python_sandbox": "Python Sandbox",
    "fetch_page": "Fetch Page",
    "ask_human": "Ask Human",
}


def build_registry(*, tool_configs: dict[str, dict] | None = None) -> dict[str, BaseTool]:
    """Assemble a tool registry. Stateless tools always included; credentialed
    tools included only when their credential is provided via tool_configs.
    tool_configs keys are tool names, values are config dicts (e.g. {"api_key": "..."})."""
    reg: dict[str, BaseTool] = {
        "calculator": calculator,
        "html_to_markdown": html_to_markdown,
        "pdf_to_text": pdf_to_text,
        "python_sandbox": python_sandbox,
        "fetch_page": fetch_page,
        "ask_human": ask_human,
    }
    configs = tool_configs or {}
    tavily_key = configs.get("web_search", {}).get("api_key")
    if tavily_key:
        reg["web_search"] = TavilySearch(max_results=5, tavily_api_key=tavily_key)
    return reg


# Global registry — uses platform-level keys from .env. Per-user registries
# are built at runtime with user-provided keys from UserToolConfigDB.
_global_tavily = get_settings().tavily_api_key
REGISTRY: dict[str, BaseTool] = build_registry(
    tool_configs={"web_search": {"api_key": _global_tavily}} if _global_tavily else None,
)
if "web_search" not in REGISTRY:
    log.warning("tools.web_search.disabled", reason="TAVILY_API_KEY not configured")


def get_tools(names: list[str], registry: dict[str, BaseTool] | None = None) -> list[BaseTool]:
    """Resolve tool names to BaseTool instances. Unknown names are skipped.
    Defaults to the platform REGISTRY; pass a custom one for per-agent creds."""
    reg = registry if registry is not None else REGISTRY
    return [reg[n] for n in names if n in reg]
