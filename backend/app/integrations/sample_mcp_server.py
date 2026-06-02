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
    """Return the current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


@mcp.tool()
def word_count(text: str) -> int:
    """Count the number of words in the given text."""
    return len(text.split())


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8001)
