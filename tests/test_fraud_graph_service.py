import json
import os
import uuid
from decimal import Decimal
from unittest.mock import MagicMock

import psycopg
import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.database import supabase_connection_parameters
from app.fraud_graph.service import (
    FraudGraphEntityIn,
    FraudGraphEventIn,
    PreparedFraudGraphEvent,
    enqueue_fraud_graph_event,
    prepare_event,
    record_prepared,
)

KEY = "everpay-fraud-graph-test-vector-key-0001"
MERCHANT = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
PAN = "4111111111111111"
SUPABASE_DSN = (
    "postgresql://everpay_fastapi.ref:private-supabase-password@pooler.example:6543/postgres"
    "?sslmode=verify-full&sslrootcert=/run/secrets/supabase-root.crt"
)


def event(**overrides):
    base = dict(
        merchant_id=MERCHANT,
        direction="payin",
        source_ref="tx_1",
        amount=Decimal("120.50"),
        currency="usd",
        entities=[
            FraudGraphEntityIn(kind="card", role="payer", value=PAN),
            FraudGraphEntityIn(kind="email", role="customer", value="Jane.Doe@example.com"),
            FraudGraphEntityIn(kind="email", role="customer", value=" jane.doe@EXAMPLE.com "),
            FraudGraphEntityIn(kind="ip", role="ip", value="10.0.0.1"),
        ],
        attributes={"status": "completed"},
    )
    base.update(overrides)
    return FraudGraphEventIn(**base)


def test_prepared_events_carry_fingerprints_only():
    prepared = prepare_event(event(), KEY)
    payload = json.dumps(prepared.model_dump(mode="json"))
    assert PAN not in payload and "jane.doe" not in payload
    assert [e["kind"] for e in prepared.entities] == ["card", "email"]  # private IP dropped, email merged
    assert prepared.rail == "card"
    assert prepared.currency == "USD"
    assert prepared.occurred_at.tzinfo is not None
    assert PreparedFraudGraphEvent.model_validate(json.loads(payload)) == prepared


def test_raw_values_never_appear_in_reprs():
    assert PAN not in repr(event())


def test_events_with_no_usable_entity_are_dropped():
    assert prepare_event(event(entities=[FraudGraphEntityIn(kind="ip", role="ip", value="127.0.0.1")]), KEY) is None


def test_payout_rail_defaults_from_the_payee():
    payee = FraudGraphEntityIn(
        kind="bank_account",
        role="payee",
        value={"institution_number": "3", "transit_number": "12345", "account_number": "1234567"},
    )
    assert prepare_event(event(direction="payout", entities=[payee]), KEY).rail == "eft"


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": Decimal("-1")},
        {"currency": "US$"},
        {"source_ref": "   "},
        {"entities": []},
        {"entities": [FraudGraphEntityIn(kind="email", role="customer", value="a@b.co")] * 33},
        {"attributes": {"status": "x" * 121}},
        {"direction": "sideways"},
    ],
)
def test_invalid_events_are_rejected(overrides):
    with pytest.raises(ValidationError):
        event(**overrides)


def test_prepared_payloads_refuse_raw_fields():
    good = prepare_event(event(), KEY).model_dump(mode="json")
    tampered = {**good, "entities": [{**good["entities"][0], "value": PAN}]}
    with pytest.raises(ValidationError):
        PreparedFraudGraphEvent.model_validate(tampered)


def test_record_prepared_calls_the_database_writer():
    prepared = prepare_event(event(), KEY)
    connection = MagicMock()
    connection.execute.return_value.fetchone.return_value = (uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002"),)
    assert record_prepared(connection, prepared) == "bbbbbbbb-0000-0000-0000-000000000002"
    sql, params = connection.execute.call_args.args
    assert "public.record_fraud_graph_event(" in sql
    assert params["merchant_id"] == MERCHANT and params["direction"] == "payin" and params["amount"] == Decimal("120.50")
    assert params["entities"].obj == prepared.entities
    connection.commit.assert_called_once()


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:pw@db.example:26257/staging?sslmode=verify-full")
    monkeypatch.setenv("FRAUD_GRAPH_HMAC_KEY", KEY)
    monkeypatch.setenv("SUPABASE_DATABASE_URL", SUPABASE_DSN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_enqueue_sends_only_the_prepared_payload(configured, monkeypatch):
    from app.tasks import fraud_graph

    sent = MagicMock()
    monkeypatch.setattr(fraud_graph.record_event, "apply_async", sent)
    assert enqueue_fraud_graph_event(event()) is True
    payload = sent.call_args.kwargs["args"][0]
    assert PAN not in json.dumps(payload)
    assert PreparedFraudGraphEvent.model_validate(payload).source_ref == "tx_1"


def test_enqueue_never_raises(configured, monkeypatch):
    from app.tasks import fraud_graph

    monkeypatch.setattr(fraud_graph.record_event, "apply_async", MagicMock(side_effect=ConnectionError("redis down")))
    assert enqueue_fraud_graph_event(event()) is False


def test_enqueue_is_off_until_configured(configured, monkeypatch):
    from app.tasks import fraud_graph

    monkeypatch.delenv("FRAUD_GRAPH_HMAC_KEY")
    get_settings.cache_clear()
    sent = MagicMock()
    monkeypatch.setattr(fraud_graph.record_event, "apply_async", sent)
    assert enqueue_fraud_graph_event(event()) is False
    sent.assert_not_called()


def test_task_drops_payloads_the_database_rejects(configured, monkeypatch):
    from app.tasks import fraud_graph

    pool = MagicMock()
    connection = pool.connection.return_value.__enter__.return_value
    connection.execute.side_effect = psycopg.errors.InvalidParameterValue("Invalid entity role")
    monkeypatch.setattr(fraud_graph, "_supabase_pool", lambda: pool)
    payload = prepare_event(event(), KEY).model_dump(mode="json")
    assert fraud_graph.record_event.apply(args=[payload]).get() is None


def test_task_retries_connection_failures(configured, monkeypatch):
    from celery.exceptions import Retry

    from app.tasks import fraud_graph

    pool = MagicMock()
    pool.connection.side_effect = psycopg.OperationalError("connection refused")
    monkeypatch.setattr(fraud_graph, "_supabase_pool", lambda: pool)
    payload = prepare_event(event(), KEY).model_dump(mode="json")
    result = fraud_graph.record_event.apply(args=[payload], throw=False)
    assert isinstance(result.result, (Retry, psycopg.OperationalError))


def supabase_settings(url=SUPABASE_DSN):
    return Settings(
        _env_file=None,
        redis_url="redis://localhost:6379/0",
        database_url="postgresql://app:pw@db.example:26257/staging?sslmode=verify-full",
        supabase_database_url=url,
    )


@pytest.mark.parametrize("mode", ["disable", "require", "verify-ca", "prefer"])
def test_supabase_tls_verification_cannot_be_disabled(mode):
    with pytest.raises(ValueError, match="verify-full"):
        supabase_connection_parameters(supabase_settings(SUPABASE_DSN.replace("verify-full", mode)))


def test_supabase_requires_its_root_certificate_and_disables_prepares():
    with pytest.raises(ValueError, match="sslrootcert"):
        supabase_connection_parameters(supabase_settings(SUPABASE_DSN.split("&sslrootcert")[0]))
    parameters = supabase_connection_parameters(supabase_settings())
    assert parameters["prepare_threshold"] is None
    assert parameters["sslrootcert"] == "/run/secrets/supabase-root.crt"
    assert "private-supabase-password" not in repr(supabase_settings())


@pytest.mark.skipif(
    not os.getenv("FRAUD_GRAPH_TEST_DSN"),
    reason="set FRAUD_GRAPH_TEST_DSN to a database with the Enki migration applied",
)
def test_records_into_a_real_database():
    """End-to-end against Postgres with migration 20261121200000 and merchant MERCHANT present."""
    payee = FraudGraphEntityIn(kind="bank_account", role="payee", value={"iban": "GB82 WEST 1234 5698 7654 32"})
    prepared = prepare_event(event(direction="payout", source_ref=f"po_{uuid.uuid4()}", entities=[payee]), KEY)
    with psycopg.connect(os.environ["FRAUD_GRAPH_TEST_DSN"]) as connection:
        first = record_prepared(connection, prepared)
        assert record_prepared(connection, prepared) == first  # idempotent replay
