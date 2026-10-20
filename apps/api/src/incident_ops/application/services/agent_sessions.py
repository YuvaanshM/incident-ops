"""Human-authorized, fixed-scope delegation without a durable product mutation."""

import hashlib
from dataclasses import dataclass
from typing import Protocol

from pydantic import SecretStr

from incident_ops.application.auth import Principal, require_actor_type, require_scope
from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import ResourceNotFoundError

AGENT_SCOPES = frozenset({"incidents:read", "actions:request"})
AGENT_TTL_SECONDS = 300


def delegated_subject(human_subject: str) -> str:
    return "browser-agent:" + hashlib.sha256(human_subject.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class AgentCredential:
    access_token: SecretStr
    expires_in: int = AGENT_TTL_SECONDS


class AgentCredentialIssuer(Protocol):
    def issue(self, human: Principal) -> AgentCredential: ...


class AgentSessionService:
    def __init__(self, issuer: AgentCredentialIssuer | None) -> None:
        self._issuer = issuer

    def exchange(self, principal: Principal) -> AgentCredential:
        require_actor_type(principal, ActorType.HUMAN)
        for scope in AGENT_SCOPES:
            require_scope(principal, scope)
        if self._issuer is None:
            raise ResourceNotFoundError()
        return self._issuer.issue(principal)
