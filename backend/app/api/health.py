import structlog
from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.db import get_session_factory
from app.redis_client import get_redis
from app.runtime.tools import DISPLAY_NAMES, REGISTRY

router = APIRouter(tags=["health"])
log = structlog.get_logger()


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: is the process up? K8s restarts the pod if this fails. Stays cheap
    and dependency-free so a transient Redis/DB blip never triggers a restart loop."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(response: Response) -> dict:
    """Readiness: can this pod actually serve? Checks Redis + DB. K8s routes traffic
    away (503) until both are reachable — so we never send requests to a pod that
    will immediately fail them."""
    checks: dict[str, str] = {}
    ok = True
    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {type(exc).__name__}"
        ok = False
    try:
        async with get_session_factory()() as s:
            await s.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {type(exc).__name__}"
        ok = False
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ok else "degraded", "checks": checks}


@router.get("/metrics/queue-depth")
async def queue_depth() -> dict[str, int]:
    """Backlog size for worker autoscaling (KEDA metrics-api scaler reads `depth`).
    arq stores its queue as a Redis SORTED SET, so depth = ZCARD (not LLEN). Returns
    0 if Redis is unreachable — a scaler should scale to floor, not crash."""
    from arq.constants import default_queue_name
    try:
        depth = await get_redis().zcard(default_queue_name)
    except Exception as exc:
        log.warning("queue_depth.unavailable", error=str(exc))
        depth = 0
    # Mirror to the Prometheus gauge — this endpoint is polled by KEDA, so the gauge
    # stays fresh for Grafana without a separate scheduler. (Plain JSON above is for KEDA.)
    from app.metrics import QUEUE_DEPTH
    QUEUE_DEPTH.set(depth)
    return {"depth": depth}


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
