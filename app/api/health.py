from fastapi import APIRouter
from fastapi.responses import JSONResponse
from app.core.config import settings
from app.core.db import database_check

router = APIRouter(tags=["health"])

@router.get("/health/live", include_in_schema=False)
async def liveness():
    return {"status": "ok", "service": "everpay-fastapi"}

@router.get("/health/ready", include_in_schema=False)
async def readiness():
    checks = {"configuration": "ok"}
    if settings.database_url:
        try:
            await database_check()
            checks["cockroachdb"] = "ok"
        except Exception:
            checks["cockroachdb"] = "unavailable"
    else:
        checks["cockroachdb"] = "not_configured"
    if settings.redis_url:
        try:
            from redis.asyncio import Redis
            client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
            try:
                await client.ping()
                checks["redis"] = "ok"
            finally:
                await client.aclose()
        except Exception:
            checks["redis"] = "unavailable"
    else:
        checks["redis"] = "not_configured"
    ready = all(value in ("ok", "not_configured") for value in checks.values())
    return JSONResponse(status_code=200 if ready else 503, content={"status": "ready" if ready else "not_ready", "checks": checks})
