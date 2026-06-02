from fastapi import APIRouter

from app.runtime.tools import REGISTRY

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/tools")
async def list_tools() -> list[dict]:
    """Available tools from the platform REGISTRY. Returns REGISTRY keys (what agents
    reference in config.tools), not the LangChain tool.name (which may differ)."""
    return [
        {"name": key, "description": tool.description or ""}
        for key, tool in REGISTRY.items()
    ]
