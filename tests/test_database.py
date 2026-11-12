from contextlib import asynccontextmanager
from importlib import import_module
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.database import connection_parameters

DSN = "postgresql://app:private-test-password@db.example:26257/staging?sslmode=verify-full"


def settings(url=DSN):
    return Settings(_env_file=None, redis_url="redis://localhost:6379/0", database_url=url)


@pytest.mark.parametrize("mode", ["disable", "require", "verify-ca", "prefer"])
def test_tls_verification_cannot_be_disabled(mode):
    with pytest.raises(ValueError, match="verify-full"):
        connection_parameters(settings(DSN.replace("verify-full", mode)))


def test_connection_requires_complete_login_and_masks_settings():
    with pytest.raises(ValueError, match="requires"):
        connection_parameters(settings("postgresql://db.example/staging?sslmode=verify-full"))
    assert "private-test-password" not in repr(settings())
    parameters = connection_parameters(settings())
    assert parameters["sslrootcert"]
    assert parameters["connect_timeout"] == 10


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("DATABASE_URL", DSN)
    get_settings.cache_clear()
    main = import_module("app.main")
    pool = AsyncMock()
    redis = AsyncMock()
    connection = AsyncMock()
    connection.execute.return_value.fetchone.return_value = (1,)

    @asynccontextmanager
    async def acquire():
        yield connection

    pool.connection = acquire
    monkeypatch.setattr(main, "create_pool", lambda _: pool)
    monkeypatch.setattr(main.Redis, "from_url", lambda *args, **kwargs: redis)
    with TestClient(main.app) as client:
        yield client, redis, connection
    pool.close.assert_awaited_once()
    redis.aclose.assert_awaited_once()
    get_settings.cache_clear()


def test_readiness_checks_both_dependencies(app):
    client, redis, connection = app
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["dependencies"] == {"redis": "ok", "cockroachdb": "ok"}
    redis.ping.assert_awaited_once()
    connection.execute.assert_awaited_once_with("SELECT 1")


def test_database_failure_is_redacted_and_does_not_break_liveness(app):
    client, _, connection = app
    connection.execute.side_effect = RuntimeError(DSN)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}
    assert "private-test-password" not in response.text
    assert client.get("/health/live").status_code == 200


def test_redis_failure_is_not_reported_as_ready(app):
    client, redis, connection = app
    redis.ping.side_effect = RuntimeError("Redis unavailable")
    assert client.get("/health/ready").status_code == 503
    connection.execute.assert_not_awaited()


def test_unexpected_database_response_is_not_ready(app):
    client, _, connection = app
    connection.execute.return_value.fetchone.return_value = (0,)
    assert client.get("/health/ready").status_code == 503
