"""Dedicated JWT signing and verification for short-lived browser-agent credentials."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import jwt
from pydantic import SecretStr

from incident_ops.application.auth import Principal
from incident_ops.application.ports import TokenVerifier
from incident_ops.application.services.agent_sessions import (
    AGENT_SCOPES,
    AGENT_TTL_SECONDS,
    AgentCredential,
    delegated_subject,
)
from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import AuthenticationRequiredError

TOKEN_TYPE = "incident-ops-agent+jwt"  # noqa: S105 - public JWT type, not a credential
_REQUIRED_CLAIMS = {
    "iss",
    "aud",
    "sub",
    "iat",
    "nbf",
    "exp",
    "jti",
    "actor_type",
    "scope",
    "on_behalf_of",
}


class AgentJwtCodec:
    def __init__(self, *, issuer: str, audience: str, signing_key: SecretStr) -> None:
        self._issuer = issuer
        self._audience = audience
        self._key = signing_key

    def issue(self, human: Principal) -> AgentCredential:
        now = int(datetime.now(UTC).timestamp())
        token = jwt.encode(
            {
                "iss": self._issuer,
                "aud": self._audience,
                "sub": delegated_subject(human.subject),
                "iat": now,
                "nbf": now,
                "exp": now + AGENT_TTL_SECONDS,
                "jti": str(uuid4()),
                "actor_type": "agent",
                "scope": " ".join(sorted(AGENT_SCOPES)),
                "on_behalf_of": human.subject,
            },
            self._key.get_secret_value(),
            algorithm="HS256",
            headers={"typ": TOKEN_TYPE},
        )
        return AgentCredential(SecretStr(token))

    async def verify(self, token: str) -> Principal:
        try:
            if len(token) > 8192 or jwt.get_unverified_header(token).get("typ") != TOKEN_TYPE:
                raise ValueError("invalid credential type")
            claims = jwt.decode(
                token,
                self._key.get_secret_value(),
                algorithms=["HS256"],
                issuer=self._issuer,
                audience=self._audience,
                options={"require": sorted(_REQUIRED_CLAIMS), "strict_aud": True},
            )
            if set(claims) != _REQUIRED_CLAIMS:
                raise ValueError("unexpected claims")
            if any(type(claims[name]) is not int for name in ("iat", "nbf", "exp")):
                raise ValueError("invalid times")
            if not (0 < claims["exp"] - claims["iat"] <= AGENT_TTL_SECONDS):
                raise ValueError("invalid lifetime")
            if claims["nbf"] != claims["iat"]:
                raise ValueError("invalid activation")
            if claims["actor_type"] != "agent" or claims["scope"] != " ".join(sorted(AGENT_SCOPES)):
                raise ValueError("invalid authority")
            human = claims["on_behalf_of"]
            if not isinstance(human, str) or not human.strip() or len(human) > 255:
                raise ValueError("invalid delegation")
            if claims["sub"] != delegated_subject(human):
                raise ValueError("invalid subject")
            credential_id = UUID(claims["jti"])
            if str(credential_id) != claims["jti"]:
                raise ValueError("invalid credential identity")
            return Principal(
                ActorType.AGENT,
                claims["sub"],
                AGENT_SCOPES,
                delegated_by=human,
                credential_id=credential_id,
            )
        except Exception:
            raise AuthenticationRequiredError() from None


class HumanOrAgentTokenVerifier:
    """Route by untrusted token type, then fully verify with exactly one verifier."""

    def __init__(self, human: TokenVerifier | None, agent: AgentJwtCodec | None) -> None:
        self._human = human
        self._agent = agent

    async def verify(self, token: str) -> Principal:
        if not token or len(token) > 8192:
            raise AuthenticationRequiredError()
        try:
            agent_type = jwt.get_unverified_header(token).get("typ") == TOKEN_TYPE
        except jwt.InvalidTokenError:
            agent_type = False
        if agent_type:
            if self._agent is None:
                raise AuthenticationRequiredError()
            return await self._agent.verify(token)
        if self._human is None:
            raise AuthenticationRequiredError()
        return await self._human.verify(token)
