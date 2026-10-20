"""Phase 10 credential exchange and dedicated agent verification contracts."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import jwt
import pytest
from httpx2 import ASGITransport, AsyncClient
from pydantic import ValidationError

from incident_ops.application.auth import Principal
from incident_ops.config import Settings
from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import AuthenticationRequiredError
from incident_ops.main import create_app

SIGNING_KEY = "phase10-test-only-" * 4
ISSUER = "https://agents.example.test"
AUDIENCE = "incident-ops-agent-api"
SCOPES = "actions:request incidents:read"


class HumanVerifier:
    async def verify(self, token):
        if token in {"human", "another-human"}:
            return Principal(ActorType.HUMAN, token, frozenset(SCOPES.split()))
        if token == "reader":
            return Principal(ActorType.HUMAN, token, frozenset({"incidents:read"}))
        if token == "agent":
            return Principal(ActorType.AGENT, token, frozenset(SCOPES.split()))
        raise AuthenticationRequiredError()


@pytest.fixture
def agent_settings(test_settings):
    return Settings(
        **{
            **test_settings.model_dump(),
            "_env_file": None,
            "agent_tokens_enabled": True,
            "agent_token_issuer": ISSUER,
            "agent_token_audience": AUDIENCE,
            "agent_token_signing_key": SIGNING_KEY,
        }
    )


def token_client(settings):
    return AsyncClient(
        transport=ASGITransport(app=create_app(settings, token_verifier=HumanVerifier())),
        base_url="https://testserver",
    )


async def exchange(client, credential="human", body=None):
    return await client.post(
        "/api/v1/agent-sessions",
        headers={"Authorization": f"Bearer {credential}"},
        json={} if body is None else body,
    )


def claims_for(token):
    return jwt.decode(token, SIGNING_KEY, algorithms=["HS256"], audience=AUDIENCE, issuer=ISSUER)


async def test_human_exchange_is_capped_short_lived_and_never_delegates_human_authority(
    agent_settings, caplog
):
    async with token_client(agent_settings) as client:
        first = await exchange(client)
        assert first.status_code == 200, first.text
        second = await exchange(client)
        assert second.status_code == 200
    first_token = first.json()["access_token"]
    claims = claims_for(first_token)
    other = claims_for(second.json()["access_token"])
    assert claims["actor_type"] == "agent"
    assert claims["scope"] == SCOPES
    assert claims["exp"] - claims["iat"] == 300
    assert claims["nbf"] == claims["iat"]
    assert claims["sub"] == other["sub"] != "human"
    assert claims["jti"] != other["jti"]
    assert str(UUID(claims["jti"])) == claims["jti"]
    assert claims["on_behalf_of"] == "human"
    assert first.headers["cache-control"] == "no-store"
    assert SIGNING_KEY not in caplog.text and first_token not in caplog.text


@pytest.mark.parametrize("credential,status", [("missing", 401), ("reader", 403), ("agent", 403)])
async def test_exchange_requires_a_human_with_both_scopes(agent_settings, credential, status):
    async with token_client(agent_settings) as client:
        response = await exchange(client, credential)
        assert response.status_code == status


@pytest.mark.parametrize(
    "field,value",
    [
        ("scopes", ["actions:approve"]),
        ("actor_type", "human"),
        ("ttl", 3600),
        ("on_behalf_of", "someone-else"),
        ("subject", "spoofed"),
    ],
)
async def test_exchange_rejects_caller_owned_authority(agent_settings, field, value):
    async with token_client(agent_settings) as client:
        response = await exchange(client, body={field: value})
        assert response.status_code == 422


@pytest.mark.parametrize("version", [True, "2", 2.5, 2_147_483_648])
async def test_direct_agent_commands_reject_invalid_versions_before_database(
    agent_settings, version
):
    async with token_client(agent_settings) as client:
        token = (await exchange(client)).json()["access_token"]
        response = await client.post(
            f"/api/v1/incidents/{uuid4()}/actions",
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4())},
            json={
                "expected_incident_version": version,
                "action_type": "retry_failed_job",
                "parameters": {"job_id": str(uuid4())},
            },
        )
        assert response.status_code == 422


async def test_disabled_exchange_fails_closed(test_settings):
    async with token_client(test_settings) as client:
        assert (await exchange(client)).status_code == 404


async def test_exchange_is_rate_limited_per_human(agent_settings):
    async with token_client(agent_settings) as client:
        for _ in range(30):
            assert (await exchange(client)).status_code == 200
        assert (await exchange(client)).status_code == 429
        assert (await exchange(client, "another-human")).status_code == 200


@pytest.mark.parametrize(
    "change",
    [
        {"exp": 1},
        {"aud": "wrong"},
        {"iss": "wrong"},
        {"actor_type": "human"},
        {"scope": SCOPES + " actions:approve"},
        {"scope": "incidents:read"},
        {"permissions": ["actions:approve"]},
        {"jti": "not-a-uuid"},
        {"sub": "human"},
        {"on_behalf_of": "spoofed"},
        {"iat": True},
        {"exp": None},
    ],
)
async def test_agent_verifier_rejects_invalid_claims_even_with_valid_signature(
    agent_settings, change
):
    app = create_app(agent_settings, token_verifier=HumanVerifier())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://testserver"
    ) as client:
        issued = await exchange(client)
    assert issued.status_code == 200
    claims = claims_for(issued.json()["access_token"])
    claims.update(change)
    tampered = jwt.encode(
        claims, SIGNING_KEY, algorithm="HS256", headers={"typ": "incident-ops-agent+jwt"}
    )
    with pytest.raises(AuthenticationRequiredError):
        await app.state.token_verifier.verify(tampered)


async def test_future_or_overlong_agent_lifetime_and_wrong_token_types_fail(agent_settings):
    app = create_app(agent_settings, token_verifier=HumanVerifier())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://testserver"
    ) as client:
        issued = await exchange(client)
    assert issued.status_code == 200
    claims = claims_for(issued.json()["access_token"])
    now = int(datetime.now(UTC).timestamp())
    variants = [
        ({**claims, "exp": claims["iat"] + 301}, "HS256", SIGNING_KEY, "incident-ops-agent+jwt"),
        (
            {**claims, "iat": now + 60, "nbf": now + 60, "exp": now + 360},
            "HS256",
            SIGNING_KEY,
            "incident-ops-agent+jwt",
        ),
        (claims, "HS384", SIGNING_KEY, "incident-ops-agent+jwt"),
        (claims, "HS256", "different-test-only-key-" * 4, "incident-ops-agent+jwt"),
        (claims, "HS256", SIGNING_KEY, "JWT"),
    ]
    for body, algorithm, key, typ in variants:
        with pytest.raises(AuthenticationRequiredError):
            await app.state.token_verifier.verify(
                jwt.encode(body, key, algorithm=algorithm, headers={"typ": typ})
            )


async def test_signed_agent_cannot_exchange_or_approve(agent_settings):
    async with token_client(agent_settings) as client:
        issued = await exchange(client)
        assert issued.status_code == 200
        token = issued.json()["access_token"]
        assert (await exchange(client, token)).status_code == 403
        for route in ["approve", "reject"]:
            response = await client.post(
                f"/api/v1/actions/{uuid4()}/{route}",
                headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4())},
                json={},
            )
            assert response.status_code == 403


@pytest.mark.parametrize(
    "changes",
    [
        {"agent_tokens_enabled": True},
        {
            "agent_tokens_enabled": True,
            "agent_token_issuer": ISSUER,
            "agent_token_audience": AUDIENCE,
            "agent_token_signing_key": "short",
        },
        {
            "agent_tokens_enabled": True,
            "agent_token_issuer": ISSUER,
            "agent_token_audience": AUDIENCE,
            "agent_token_signing_key": SIGNING_KEY,
            "oidc_issuer_url": ISSUER,
        },
    ],
)
def test_agent_configuration_requires_complete_distinct_credentials(test_settings, changes):
    with pytest.raises(ValidationError):
        Settings(**{**test_settings.model_dump(), "_env_file": None, **changes})
