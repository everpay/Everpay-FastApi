"""TLS-verified connections to the external CockroachDB cluster."""

import certifi
from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import AsyncConnectionPool

from app.config import Settings

DATABASE_TIMEOUT_SECONDS = 10


def connection_parameters(settings: Settings) -> dict:
    try:
        parameters = conninfo_to_dict(settings.database_url.get_secret_value())
    except Exception:
        raise ValueError("DATABASE_URL must be a valid PostgreSQL connection string") from None
    if parameters.get("sslmode") != "verify-full":
        raise ValueError("DATABASE_URL must use sslmode=verify-full")
    if not all(parameters.get(key) for key in ("host", "user", "password", "dbname")):
        raise ValueError("DATABASE_URL requires a host, database, SQL user and password")
    # Public CockroachDB Cloud certificates are verified against trusted CAs.
    # An explicit sslrootcert can override this for deployments with a private CA.
    parameters.setdefault("sslrootcert", certifi.where())
    parameters.update(connect_timeout=DATABASE_TIMEOUT_SECONDS, application_name="everpay-fastapi")
    return parameters


def create_pool(settings: Settings) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        kwargs=connection_parameters(settings),
        min_size=0,
        max_size=5,
        max_waiting=5,
        timeout=DATABASE_TIMEOUT_SECONDS,
        max_idle=60,
        max_lifetime=300,
        open=False,
    )
