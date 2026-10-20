"""Retrieval denies unauthorized callers before database/provider work."""

from uuid import uuid4

import pytest
from httpx2 import ASGITransport, AsyncClient

from incident_ops.application.auth import Principal
from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import AuthenticationRequiredError
from incident_ops.main import create_app


class Verifier:
    async def verify(self, token):
        if token == "invalid":
            raise AuthenticationRequiredError()
        actor = ActorType.SYSTEM if token == "system" else ActorType.AGENT
        return Principal(
            actor, token, frozenset() if token == "no-scope" else frozenset({"incidents:read"})
        )


@pytest.mark.parametrize(
    "token,status", [(None, 401), ("invalid", 401), ("no-scope", 403), ("system", 403)]
)
async def test_retrieval_requires_verified_reader_of_allowed_actor_type(
    test_settings, token, status
):
    app = create_app(test_settings, token_verifier=Verifier())
    async with AsyncClient(transport=ASGITransport(app), base_url="http://testserver") as client:
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        response = await client.post(
            f"/api/v1/incidents/{uuid4()}/remediation-suggestions", json={}, headers=headers
        )
        assert response.status_code == status
        source = await client.get(f"/api/v1/historical-incidents/{uuid4()}", headers=headers)
        assert source.status_code == status


@pytest.mark.parametrize(
    "body",
    [
        {"query": " "},
        {"query": "x" * 2001},
        {"limit": True},
        {"limit": 6},
        {"actor_type": "human"},
        {"execute": True},
        {"severity": "urgent"},
    ],
)
async def test_retrieval_rejects_unknown_or_invalid_command_fields(test_settings, body):
    async with AsyncClient(
        transport=ASGITransport(create_app(test_settings, token_verifier=Verifier())),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/v1/incidents/{uuid4()}/remediation-suggestions",
            json=body,
            headers={"Authorization": "Bearer reader"},
        )
        assert response.status_code == 422
