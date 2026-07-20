from fastapi.testclient import TestClient

from flowmate.api.app import create_app
from flowmate.config import Settings


def create_test_client() -> TestClient:
    settings = Settings(_env_file=None, api_bearer_token="test-secret")
    return TestClient(create_app(settings=settings))


def test_status_rejects_missing_token() -> None:
    with create_test_client() as client:
        response = client.get("/api/v1/status")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_status_rejects_invalid_token() -> None:
    with create_test_client() as client:
        response = client.get(
            "/api/v1/status", headers={"Authorization": "Bearer wrong-secret"}
        )

    assert response.status_code == 401


def test_status_accepts_valid_token() -> None:
    with create_test_client() as client:
        response = client.get(
            "/api/v1/status", headers={"Authorization": "Bearer test-secret"}
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "flowmate"}
