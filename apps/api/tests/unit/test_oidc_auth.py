"""OIDC access-token verification at the human trust boundary."""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import AuthenticationRequiredError
from incident_ops.infrastructure.auth.oidc import OidcTokenVerifier

ISSUER = "https://incident-ops.us.auth0.com/"
AUDIENCE = "https://api.incident-ops.example"
ACTOR_CLAIM = "https://incident-ops.example/actor_type"
PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_KEY = PRIVATE_KEY.public_key()


def token(**overrides: Any) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "auth0|operator-123",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "scope": "openid incidents:read actions:read",
        "permissions": ["incidents:write", "actions:approve"],
        ACTOR_CLAIM: "human",
    }
    claims.update(overrides)
    return jwt.encode(claims, PRIVATE_KEY, algorithm="RS256", headers={"kid": "test-key"})


def verifier() -> OidcTokenVerifier:
    return OidcTokenVerifier(
        issuer_url=ISSUER,
        audience=AUDIENCE,
        jwks_url=f"{ISSUER}.well-known/jwks.json",
        actor_type_claim=ACTOR_CLAIM,
        signing_key_resolver=lambda _: PUBLIC_KEY,
    )


async def test_valid_auth0_token_derives_human_principal_and_scopes() -> None:
    principal = await verifier().verify(token())

    assert principal.actor_type is ActorType.HUMAN
    assert principal.subject == "auth0|operator-123"
    assert principal.scopes == frozenset(
        {
            "openid",
            "incidents:read",
            "incidents:write",
            "actions:read",
            "actions:approve",
        }
    )


@pytest.mark.parametrize(
    "claim_overrides",
    [
        {"aud": "https://other-api.example"},
        {"exp": datetime.now(UTC) - timedelta(minutes=1)},
        {ACTOR_CLAIM: "agent"},
        {"sub": ""},
        {"scope": ["incidents:read"]},
        {"permissions": "actions:approve"},
    ],
)
async def test_invalid_or_nonhuman_tokens_use_one_generic_failure(
    claim_overrides: dict[str, Any],
) -> None:
    with pytest.raises(AuthenticationRequiredError):
        await verifier().verify(token(**claim_overrides))


async def test_signing_key_lookup_failure_is_not_exposed() -> None:
    broken = OidcTokenVerifier(
        issuer_url=ISSUER,
        audience=AUDIENCE,
        jwks_url=f"{ISSUER}.well-known/jwks.json",
        actor_type_claim=ACTOR_CLAIM,
        signing_key_resolver=lambda _: (_ for _ in ()).throw(RuntimeError("provider detail")),
    )

    with pytest.raises(AuthenticationRequiredError) as raised:
        await broken.verify(token())

    assert "provider detail" not in str(raised.value)
