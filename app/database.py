"""TLS-verified connections to the external CockroachDB cluster and to Supabase Postgres."""

import certifi
from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import AsyncConnectionPool, ConnectionPool

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


def supabase_connection_parameters(settings: Settings) -> dict:
    """Connection settings for the Supabase Postgres login used by the fraud graph writer.

    Supabase serves a certificate from its own CA, so the root certificate must be given
    explicitly (sslrootcert=/path/to/supabase-root.crt); public CA roots are not a fallback.
    Prepared statements are disabled so the transaction pooler (port 6543) can be used.
    """
    if settings.supabase_database_url is None:
        raise ValueError("SUPABASE_DATABASE_URL is not configured")
    try:
        parameters = conninfo_to_dict(settings.supabase_database_url.get_secret_value())
    except Exception:
        raise ValueError("SUPABASE_DATABASE_URL must be a valid PostgreSQL connection string") from None
    if parameters.get("sslmode") != "verify-full":
        raise ValueError("SUPABASE_DATABASE_URL must use sslmode=verify-full")
    if not parameters.get("sslrootcert"):
        raise ValueError("SUPABASE_DATABASE_URL must set sslrootcert to the Supabase root certificate")
    if not all(parameters.get(key) for key in ("host", "user", "password", "dbname")):
        raise ValueError("SUPABASE_DATABASE_URL requires a host, database, SQL user and password")
    parameters.update(
        connect_timeout=DATABASE_TIMEOUT_SECONDS,
        application_name="everpay-fastapi-fraud-graph",
        prepare_threshold=None,
    )
    return parameters


def create_supabase_pool(settings: Settings) -> ConnectionPool:
    """Small synchronous pool for Celery workers writing to the fraud graph."""
    return ConnectionPool(
        kwargs=supabase_connection_parameters(settings),
        min_size=0,
        max_size=2,
        max_waiting=10,
        timeout=DATABASE_TIMEOUT_SECONDS,
        max_idle=60,
        max_lifetime=300,
        open=False,
    )
