"""Verify the configured SQL login and TLS connection without writing data."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.database import create_pool


async def check() -> None:
    async with create_pool(get_settings()) as pool:
        async with pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT current_database(), current_user, version()"
            )
            database, user, version = await cursor.fetchone()
            if "CockroachDB" not in version:
                raise RuntimeError("Configured server is not CockroachDB")
    print(json.dumps({"status": "ok", "database": database, "user": user, "tls": "verify-full"}))


if __name__ == "__main__":
    try:
        # libpq's asynchronous connection support requires a selector on Windows.
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(check())
    except Exception:
        # Database errors can contain connection metadata. Keep secrets out of logs.
        print("CockroachDB connection check failed", file=sys.stderr)
        sys.exit(1)
