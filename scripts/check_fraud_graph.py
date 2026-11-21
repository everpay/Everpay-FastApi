"""Verify the fraud graph configuration without writing data.

Checks that FRAUD_GRAPH_HMAC_KEY is usable, that the Supabase login connects with
verify-full TLS, and that it may call public.record_fraud_graph_event.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import supabase_connection_parameters  # noqa: E402
from app.fraud_graph.fingerprints import validate_key  # noqa: E402

WRITER = "public.record_fraud_graph_event(uuid,text,text,text,numeric,text,timestamptz,jsonb,jsonb)"


def check() -> dict:
    settings = get_settings()
    if settings.fraud_graph_hmac_key is None:
        raise RuntimeError("FRAUD_GRAPH_HMAC_KEY is not configured")
    validate_key(settings.fraud_graph_hmac_key.get_secret_value())
    with psycopg.connect(**supabase_connection_parameters(settings)) as connection:
        row = connection.execute(
            "SELECT current_user, to_regprocedure(%s) IS NOT NULL, "
            "CASE WHEN to_regprocedure(%s) IS NULL THEN false ELSE has_function_privilege(%s, 'EXECUTE') END",
            (WRITER, WRITER, WRITER),
        ).fetchone()
    user, exists, can_execute = row
    if not exists:
        raise RuntimeError("record_fraud_graph_event is missing; apply the Enki migration 20261121200000 first")
    if not can_execute:
        raise RuntimeError(f"{user} cannot execute record_fraud_graph_event; GRANT fraud_graph_writer TO {user}")
    return {"status": "ok", "user": user, "tls": "verify-full", "writer": "executable"}


if __name__ == "__main__":
    try:
        print(json.dumps(check()))
    except Exception as exc:
        # The message never includes the connection string or the key.
        detail = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else None
        print(json.dumps({"status": "error", "error": type(exc).__name__, "detail": detail}))
        sys.exit(1)
