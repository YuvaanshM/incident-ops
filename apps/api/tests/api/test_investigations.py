"""Optional investigation API fails closed before touching storage or a provider."""

from uuid import uuid4

import pytest
from httpx2 import ASGITransport, AsyncClient

from incident_ops.application.auth import Principal
from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import AuthenticationRequiredError
from incident_ops.main import create_app


class Verifier:
    async def verify(self, token):
        if token == "human":
            return Principal(
                ActorType.HUMAN, token, frozenset({"incidents:read", "actions:request"})
            )
        if token == "agent":
            return Principal(
                ActorType.AGENT, token, frozenset({"incidents:read", "actions:request"})
            )
        if token == "reader":
            return Principal(ActorType.AGENT, token, frozenset({"incidents:read"}))
        raise AuthenticationRequiredError()


@pytest.mark.parametrize(
    "token,expected", [(None, 401), ("human", 403), ("agent", 403), ("reader", 403)]
)
async def test_requires_verified_delegated_agent_with_both_scopes(test_settings, token, expected):
    async with AsyncClient(
        transport=ASGITransport(app=create_app(test_settings, token_verifier=Verifier())),
        base_url="https://testserver",
    ) as client:
        headers = {"Idempotency-Key": str(uuid4())}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = await client.post(
            f"/api/v1/incidents/{uuid4()}/investigations",
            headers=headers,
            json={"expected_incident_version": 1},
        )
        assert response.status_code == expected
        response = await client.get(f"/api/v1/investigations/by-request/{uuid4()}", headers=headers)
        assert response.status_code == expected


@pytest.mark.parametrize(
    "body",
    [
        {"expected_incident_version": True},
        {"expected_incident_version": 1, "model": "arbitrary"},
        {"expected_incident_version": 1, "scopes": ["actions:approve"]},
    ],
)
async def test_unknown_authority_and_invalid_input_are_rejected(test_settings, body):
    async with AsyncClient(
        transport=ASGITransport(app=create_app(test_settings, token_verifier=Verifier())),
        base_url="https://testserver",
    ) as client:
        response = await client.post(
            f"/api/v1/incidents/{uuid4()}/investigations",
            headers={"Authorization": "Bearer human", "Idempotency-Key": str(uuid4())},
            json=body,
        )
        assert response.status_code == 422
