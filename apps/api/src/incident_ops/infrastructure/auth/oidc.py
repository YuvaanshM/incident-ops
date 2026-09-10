"""Provider-neutral verification for hosted human OIDC access tokens."""

import asyncio
from collections.abc import Callable
from typing import Any

import jwt
from jwt import PyJWKClient

from incident_ops.application.auth import Principal
from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import AuthenticationRequiredError

SigningKeyResolver = Callable[[str], object]


class OidcTokenVerifier:
    """Verify RS256 access tokens and derive a least-privilege human principal."""

    def __init__(
        self,
        *,
        issuer_url: str,
        audience: str,
        jwks_url: str,
        actor_type_claim: str,
        signing_key_resolver: SigningKeyResolver | None = None,
    ) -> None:
        self._issuer_url = issuer_url
        self._audience = audience
        self._actor_type_claim = actor_type_claim
        jwks_client = PyJWKClient(
            jwks_url,
            cache_jwk_set=True,
            lifespan=300,
            timeout=5,
        )
        self._signing_key_resolver = signing_key_resolver or (
            lambda token: jwks_client.get_signing_key_from_jwt(token).key
        )

    async def verify(self, token: str) -> Principal:
        try:
            signing_key = await asyncio.to_thread(self._signing_key_resolver, token)
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer_url,
                leeway=30,
                options={"require": ["aud", "exp", "iat", "iss", "sub"]},
            )
            subject = claims.get("sub")
            actor_type = claims.get(self._actor_type_claim)
            scope_claim = claims.get("scope", "")
            permissions_claim = claims.get("permissions", [])
            if not isinstance(subject, str) or not subject.strip():
                raise ValueError("invalid subject")
            if actor_type != ActorType.HUMAN.value:
                raise ValueError("invalid actor type")
            if not isinstance(scope_claim, str):
                raise ValueError("invalid scope claim")
            if not isinstance(permissions_claim, list) or not all(
                isinstance(permission, str) for permission in permissions_claim
            ):
                raise ValueError("invalid permissions claim")
            scopes = frozenset(
                [scope for scope in scope_claim.split() if scope]
                + [permission for permission in permissions_claim if permission]
            )
            return Principal(
                actor_type=ActorType.HUMAN,
                subject=subject,
                scopes=scopes,
            )
        except Exception:
            raise AuthenticationRequiredError() from None
