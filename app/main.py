from contextlib import asynccontextmanager
from asyncio import timeout
from fastapi import FastAPI, HTTPException
from redis.asyncio import Redis
from app.config import get_settings
from app.database import DATABASE_TIMEOUT_SECONDS, create_pool

settings = get_settings()

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.database = create_pool(settings)
    app.state.redis = Redis.from_url(
        settings.redis_url, decode_responses=True,
        socket_connect_timeout=3, socket_timeout=3, health_check_interval=30,
    )
    await app.state.database.open()
    try:
        yield
    finally:
        await app.state.database.close()
        await app.state.redis.aclose()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs" if settings.api_docs_enabled else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.api_docs_enabled else None,
    lifespan=lifespan,
)

@app.get("/health/live", tags=["Health"])
async def liveness():
    return {"status": "alive", "environment": settings.app_env}

@app.get("/health/ready", tags=["Health"])
async def readiness():
    try:
        await app.state.redis.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Redis unavailable") from exc
    try:
        async with timeout(DATABASE_TIMEOUT_SECONDS):
            async with app.state.database.connection() as connection:
                cursor = await connection.execute("SELECT 1")
                if await cursor.fetchone() != (1,):
                    raise RuntimeError("Unexpected database health response")
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    return {"status": "ready", "dependencies": {"redis": "ok", "cockroachdb": "ok"}}

@app.get("/", tags=["Root"])
async def root():
    return {"service": settings.app_name, "environment": settings.app_env}
