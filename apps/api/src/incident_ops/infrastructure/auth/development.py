"""Explicitly enabled, development-only identities for local Swagger use."""

from incident_ops.application.auth import Principal
from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import AuthenticationRequiredError

DEVELOPMENT_HUMAN_CREDENTIAL = "human-demo"
DEVELOPMENT_AGENT_CREDENTIAL = "agent-demo"


class DevelopmentTokenVerifier:
    """Map public local fixture credentials to least-privilege demo principals."""

    async def verify(self, token: str) -> Principal:
        if token == DEVELOPMENT_HUMAN_CREDENTIAL:
            return Principal(
                actor_type=ActorType.HUMAN,
                subject="local-human",
                scopes=frozenset(
                    {
                        "incidents:read",
                        "incidents:write",
                        "actions:read",
                        "actions:request",
                        "actions:approve",
                        "fixtures:write",
                    }
                ),
            )
        if token == DEVELOPMENT_AGENT_CREDENTIAL:
            return Principal(
                actor_type=ActorType.AGENT,
                subject="local-agent",
                scopes=frozenset({"incidents:read", "actions:request"}),
            )
        raise AuthenticationRequiredError()
