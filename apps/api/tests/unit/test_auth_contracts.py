"""Provider-neutral principal authorization tests."""

import pytest

from incident_ops.application.auth import Principal, require_actor_type, require_scope
from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import AuthorizationDeniedError


def test_principal_normalizes_scopes_and_checks_membership() -> None:
    principal = Principal(
        actor_type=ActorType.HUMAN,
        subject="operator-1",
        scopes=frozenset({" incidents:read ", "incidents:write"}),
    )

    assert principal.scopes == frozenset({"incidents:read", "incidents:write"})
    assert principal.has_scope("incidents:read")


@pytest.mark.parametrize("subject", ["", "   ", "x" * 256])
def test_principal_rejects_invalid_subjects(subject: str) -> None:
    with pytest.raises(ValueError):
        Principal(actor_type=ActorType.HUMAN, subject=subject, scopes=frozenset())


def test_missing_scope_is_denied_without_disclosing_credentials() -> None:
    principal = Principal(
        actor_type=ActorType.AGENT,
        subject="agent-1",
        scopes=frozenset({"incidents:read"}),
    )

    with pytest.raises(AuthorizationDeniedError, match="not authorized") as error:
        require_scope(principal, "actions:approve")

    assert "agent-1" not in str(error.value)
    assert "actions:approve" not in str(error.value)


def test_agent_cannot_satisfy_human_actor_requirement() -> None:
    principal = Principal(
        actor_type=ActorType.AGENT,
        subject="agent-1",
        scopes=frozenset({"actions:approve"}),
    )

    with pytest.raises(AuthorizationDeniedError):
        require_actor_type(principal, ActorType.HUMAN)
