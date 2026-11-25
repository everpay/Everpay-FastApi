from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from app.core.config import settings

_engine: AsyncEngine | None = None

def get_engine() -> AsyncEngine | None:
    global _engine
    if not settings.database_url:
        return None
    if _engine is None:
        _engine = create_async_engine(settings.database_url, pool_pre_ping=True,
            pool_size=settings.database_pool_size, max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout_seconds,
            connect_args={"server_settings": {"application_name": "everpay-fastapi"}})
    return _engine

async def database_check() -> bool:
    engine = get_engine()
    if engine is None:
        return False
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    return True

async def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
