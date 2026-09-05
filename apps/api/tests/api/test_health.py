"""Health endpoint contract tests."""

from fastapi.testclient import TestClient

from incident_ops.config import Settings
from incident_ops.main import create_app


def test_liveness_is_minimal(test_settings: Settings) -> None:
    response = TestClient(create_app(test_settings)).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_readiness_does_not_expose_configuration(test_settings: Settings) -> None:
    response = TestClient(create_app(test_settings)).get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"configuration": "ok", "database": "deferred_to_phase_2"},
    }
    assert "postgresql" not in response.text
