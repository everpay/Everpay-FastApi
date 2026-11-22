from fastapi.testclient import TestClient
from app.main import app

def test_liveness():
    with TestClient(app) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_root_does_not_expose_secrets():
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "secret" not in response.text.lower()
