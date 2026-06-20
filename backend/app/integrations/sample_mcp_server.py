"""Sample FastMCP server exposing 2 tools for demo/testing.

Run standalone:  uv run python -m app.integrations.sample_mcp_server
Or via Docker:   included in the backend container, started on port 8001.

Tools:
  - timestamp: returns the current UTC ISO timestamp
  - word_count: counts words in a given text
"""
from datetime import datetime, timezone

from fastmcp import FastMCP

mcp = FastMCP("sample-tools")


@mcp.tool()
def timestamp() -> str:
    """Return the CURRENT real-world UTC date and time as an ISO 8601 string.

    USE WHEN: the user (or your own reasoning) references the present moment —
    "current", "now", "today", "this week / month / year", "latest", "what time
    is it", "how long ago was X", "is this still valid". Also call it BEFORE
    any web search whose answer depends on freshness ("current weather",
    "latest news", "today's price", "recent results") so you can: (a) include
    a date in the search query when helpful, and (b) reason about how stale
    a retrieved result is.
    DO NOT USE FOR: questions with no temporal component ("capital of France",
    "explain recursion").
    OUTPUT: e.g. "2026-06-03T10:34:17.885439+00:00". UTC only — convert to
    the user's locale yourself if they asked for local time."""
    return datetime.now(timezone.utc).isoformat()


@mcp.tool()
def word_count(text: str) -> int:
    """Return the number of whitespace-delimited words in the given text.

    USE WHEN: the user explicitly asks for a word count, or you need a quick
    length check before further processing.
    DO NOT USE FOR: character count, sentence count, token count, or
    estimating LLM cost (those are different metrics).
    OUTPUT: a single integer. Definition is naive — splits on whitespace, so
    "well-known" counts as 1 word, "U.S." counts as 1 word."""
    return len(text.split())


if __name__ == "__main__":
    print(
        "[sample-mcp] listening on 0.0.0.0:8001 — when registering in the UI from\n"
        "[sample-mcp] another container, use http://mcp-sample:8001/mcp (NOT 0.0.0.0/localhost).",
        flush=True,
    )
    mcp.run(transport="http", host="0.0.0.0", port=8001)  # nosec B104 — sample server binds all interfaces inside its container by design
