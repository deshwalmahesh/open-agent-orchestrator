"""Tool registry. LangChain BaseTool instances; tools raise on error and
ToolNode reports the message back to the LLM. Call `build_registry(tavily_api_key=...)`
to assemble a registry with a custom (e.g. per-user) Tavily key."""

from __future__ import annotations

import asyncio
import sys

import numexpr
import structlog
from langchain.tools import tool
from langchain_core.tools import BaseTool
from langchain_tavily import TavilySearch
from markdownify import markdownify
from pypdf import PdfReader

from app.config import get_settings

log = structlog.get_logger()


@tool
def calculator(expression: str) -> str:
    """Evaluate a numeric expression (e.g. '2*(3+4)', 'sqrt(16)'). Numbers + math fns only — no variables, no Python."""
    return str(numexpr.evaluate(expression).item())


@tool
def html_to_markdown(html: str) -> str:
    """Convert an HTML string to Markdown."""
    return markdownify(html)


@tool
def pdf_to_text(path: str) -> str:
    """Extract text from a PDF at the given local file path."""
    reader = PdfReader(path)
    return "\n\n".join((p.extract_text() or "") for p in reader.pages)


@tool
async def python_sandbox(code: str) -> str:
    """Run a short Python snippet in a subprocess with a 5s timeout. Returns stdout.

    Isolation is partial: `-I` blocks user site-packages, PYTHONPATH, and other env-driven
    site lookups, and we pass an empty environment so the process inherits no host secrets
    (API keys, JWT secrets, DB URLs). The subprocess still has the host's Python install
    on disk and full network — do NOT treat this as a security boundary for untrusted code.
    """
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
    return out.decode(errors="replace")


# Human-readable labels for the registry keys. The agent's config references the
# stable lowercase key (e.g. "web_search"); the UI shows DISPLAY_NAMES[key].
# Unknown keys fall back to the registry key itself.
DISPLAY_NAMES: dict[str, str] = {
    "calculator": "Calculator",
    "web_search": "Web Search",
    "html_to_markdown": "HTML → Markdown",
    "pdf_to_text": "PDF → Text",
    "python_sandbox": "Python Sandbox",
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
