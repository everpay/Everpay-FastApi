"""Celery task writing prepared fraud graph events to Supabase.

Payloads hold fingerprints and masked hints only (see app.fraud_graph.service). The
database function is idempotent per event, entity and role, so retries are safe.
Connection problems are retried with backoff; a payload the database rejects as
invalid is logged and dropped, since retrying cannot fix it.
"""

from __future__ import annotations

import logging
import threading

import psycopg
from psycopg_pool import ConnectionPool, PoolTimeout

from app.celery_app import celery_app
from app.config import get_settings
from app.database import create_supabase_pool
from app.fraud_graph.service import PreparedFraudGraphEvent, record_prepared

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def _supabase_pool() -> ConnectionPool:
    """One pool per worker process, opened on first use (after Celery forks)."""
    global _pool
    with _pool_lock:
        if _pool is None:
            pool = create_supabase_pool(get_settings())
            pool.open()
            _pool = pool
        return _pool


@celery_app.task(
    name="app.tasks.fraud_graph.record_event",
    bind=True,
    acks_late=True,
    autoretry_for=(psycopg.OperationalError, PoolTimeout),
    retry_backoff=5,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=8,
)
def record_event(self, payload: dict) -> str | None:
    prepared = PreparedFraudGraphEvent.model_validate(payload)
    try:
        with _supabase_pool().connection() as connection:
            return record_prepared(connection, prepared)
    except (psycopg.errors.InvalidParameterValue, psycopg.errors.CheckViolation, psycopg.errors.ForeignKeyViolation) as exc:
        logger.warning(
            "fraud graph event %s rejected by the database (%s); not retried",
            prepared.source_ref,
            exc.sqlstate,
        )
        return None
