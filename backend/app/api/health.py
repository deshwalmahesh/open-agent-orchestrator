from fastapi import APIRouter

from app.runtime.tools import DISPLAY_NAMES, REGISTRY

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/tools")
async def list_tools() -> list[dict]:
    """Available tools from the platform REGISTRY. `name` is the stable registry key
    that agents reference in config.tools; `display_name` is the human label used by
    the UI (falls back to the key if not in DISPLAY_NAMES)."""
    return [
        {
            "name": key,
            "display_name": DISPLAY_NAMES.get(key, key),
            "description": tool.description or "",
        }
        for key, tool in REGISTRY.items()
    ]
