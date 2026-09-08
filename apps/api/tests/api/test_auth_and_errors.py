"""Generic authentication and error adapter contracts."""

import logging
from typing import Annotated
from uuid import UUID

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from incident_ops.api.dependencies.auth import (
    get_current_principal,
    require_actor_type,
    require_scope,
)
from incident_ops.application.auth import Principal
from incident_ops.config import Settings
from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import InvalidStateTransitionError
from incident_ops.main import create_app


class TestTokenVerifier:
    async def verify(self, token: str) -> Principal:
        if token == "agent-test-token":
            return Principal(
                actor_type=ActorType.AGENT,
                subject="agent-1",
                scopes=frozenset({"incidents:read"}),
            )
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
        principal: Annotated[Principal, Depends(require_scope("incidents:read"))],
    ) -> dict[str, str]:
        return {"subject": principal.subject}

    async def failure(
        principal: Annotated[Principal, Depends(get_current_principal)],
    ) -> None:
        del principal
        raise InvalidStateTransitionError(details={"current_status": "closed"})

    async def human_only(
        principal: Annotated[Principal, Depends(require_actor_type(ActorType.HUMAN))],
    ) -> dict[str, str]:
        return {"actor_type": principal.actor_type.value}

    app.add_api_route("/test/protected", protected, methods=["GET"])
    app.add_api_route("/test/failure", failure, methods=["GET"])
    app.add_api_route("/test/human-only", human_only, methods=["GET"])
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


def test_invalid_token_uses_same_generic_authentication_error(contract_client: TestClient) -> None:
    response = contract_client.get(
        "/test/protected", headers={"Authorization": "Bearer invalid-token"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"
    assert "invalid-token" not in response.text


def test_actor_type_dependency_accepts_verified_human(contract_client: TestClient) -> None:
    response = contract_client.get(
        "/test/human-only", headers={"Authorization": "Bearer valid-test-token"}
    )

    assert response.status_code == 200
    assert response.json() == {"actor_type": "human"}


def test_actor_type_dependency_rejects_agent_with_generic_403(
    contract_client: TestClient,
) -> None:
    response = contract_client.get(
        "/test/human-only", headers={"Authorization": "Bearer agent-test-token"}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert "agent-1" not in response.text


def test_domain_error_mapper_adds_details_and_server_request_id(
    contract_client: TestClient,
) -> None:
    supplied_request_id = "25d92e90-e252-4c55-8519-30cf2c5d1e05"
    response = contract_client.get(
        "/test/failure",
        headers={
            "Authorization": "Bearer valid-test-token",
            "X-Request-ID": supplied_request_id,
        },
    )

    assert response.status_code == 409
    UUID(response.headers["x-request-id"])
    assert response.headers["x-request-id"] != supplied_request_id
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
            "X-Request-ID": "not-a-safe-request-id-secret",
        },
    )

    UUID(response.headers["x-request-id"])


def test_request_log_contains_correlation_not_bearer_token(
    contract_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="incident_ops.http"):
        response = contract_client.get(
            "/test/protected",
            headers={"Authorization": "Bearer valid-test-token"},
        )

    record = next(record for record in caplog.records if record.name == "incident_ops.http")
    assert record.request_id == response.headers["x-request-id"]
    assert record.path == "/test/protected"
    assert "valid-test-token" not in caplog.text


def test_explicit_development_auth_supports_local_swagger_without_real_secrets(
    test_settings: Settings,
) -> None:
    development_settings = Settings(
        _env_file=None,
        app_env="development",
        database_url=test_settings.database_url,
        development_auth_enabled=True,
        cors_origins=["http://localhost:3000"],
        allowed_hosts=["testserver"],
    )
    app = create_app(development_settings)

    async def protected(
        principal: Annotated[Principal, Depends(get_current_principal)],
    ) -> dict[str, str]:
        return {
            "actor_type": principal.actor_type.value,
            "subject": principal.subject,
        }

    app.add_api_route("/test/development-auth", protected, methods=["GET"])
    client = TestClient(app)

    human = client.get(
        "/test/development-auth",
        headers={"Authorization": "Bearer human-demo"},
    )
    agent = client.get(
        "/test/development-auth",
        headers={"Authorization": "Bearer agent-demo"},
    )
    unknown = client.get(
        "/test/development-auth",
        headers={"Authorization": "Bearer not-a-demo-credential"},
    )

    assert human.json() == {"actor_type": "human", "subject": "local-human"}
    assert agent.json() == {"actor_type": "agent", "subject": "local-agent"}
    assert unknown.status_code == 401
