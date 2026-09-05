"""Generic authentication and error adapter contracts."""

from uuid import UUID

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from incident_ops.api.dependencies.auth import get_current_principal, require_scope
from incident_ops.application.auth import Principal
from incident_ops.config import Settings
from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import InvalidStateTransitionError
from incident_ops.main import create_app


class TestTokenVerifier:
    async def verify(self, token: str) -> Principal:
        if token != "valid-test-token":
            from incident_ops.domain.errors import AuthenticationRequiredError

            raise AuthenticationRequiredError()
        return Principal(
            actor_type=ActorType.HUMAN,
            subject="operator-1",
            scopes=frozenset({"incidents:read"}),
        )


@pytest.fixture
def contract_client(test_settings: Settings) -> TestClient:
    app = create_app(test_settings, token_verifier=TestTokenVerifier())

    async def protected(
        principal: Principal = Depends(require_scope("incidents:read")),
    ) -> dict[str, str]:
        return {"subject": principal.subject}

    async def failure(
        principal: Principal = Depends(get_current_principal),
    ) -> None:
        del principal
        raise InvalidStateTransitionError(details={"current_status": "closed"})

    app.add_api_route("/test/protected", protected, methods=["GET"])
    app.add_api_route("/test/failure", failure, methods=["GET"])
    return TestClient(app)


def test_missing_token_uses_stable_generic_error_envelope(contract_client: TestClient) -> None:
    response = contract_client.get("/test/protected")

    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "authentication_required", "message": "Authentication is required."}
    }
    UUID(response.headers["x-request-id"])
    assert response.headers["www-authenticate"] == "Bearer"


def test_verified_principal_is_derived_from_bearer_token(contract_client: TestClient) -> None:
    response = contract_client.get(
        "/test/protected",
        headers={"Authorization": "Bearer valid-test-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"subject": "operator-1"}


def test_domain_error_mapper_adds_details_and_request_id(contract_client: TestClient) -> None:
    request_id = "25d92e90-e252-4c55-8519-30cf2c5d1e05"
    response = contract_client.get(
        "/test/failure",
        headers={
            "Authorization": "Bearer valid-test-token",
            "X-Request-ID": request_id,
        },
    )

    assert response.status_code == 409
    assert response.headers["x-request-id"] == request_id
    assert response.json() == {
        "error": {
            "code": "invalid_state_transition",
            "message": "The requested state transition is not allowed.",
            "details": {"current_status": "closed"},
        }
    }
    assert "valid-test-token" not in response.text


def test_invalid_request_id_is_replaced(contract_client: TestClient) -> None:
    response = contract_client.get(
        "/test/protected",
        headers={
            "Authorization": "Bearer valid-test-token",
            "X-Request-ID": "not-a-safe-request-id\nsecret",
        },
    )

    UUID(response.headers["x-request-id"])
