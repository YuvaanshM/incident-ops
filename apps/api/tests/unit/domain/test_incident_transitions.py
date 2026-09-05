"""Exhaustive incident state-machine and lifecycle timestamp tests."""

from datetime import UTC, datetime, timedelta

import pytest

from incident_ops.domain.enums import IncidentStatus
from incident_ops.domain.errors import InvalidStateTransitionError
from incident_ops.domain.incidents import transition_lifecycle

ALLOWED_TRANSITIONS = {
    (IncidentStatus.OPEN, IncidentStatus.INVESTIGATING),
    (IncidentStatus.OPEN, IncidentStatus.RESOLVED),
    (IncidentStatus.INVESTIGATING, IncidentStatus.RESOLVED),
    (IncidentStatus.RESOLVED, IncidentStatus.INVESTIGATING),
    (IncidentStatus.RESOLVED, IncidentStatus.CLOSED),
}


@pytest.mark.parametrize("current", list(IncidentStatus))
@pytest.mark.parametrize("target", list(IncidentStatus))
def test_state_machine_exhaustively_enforces_every_edge(
    current: IncidentStatus,
    target: IncidentStatus,
) -> None:
    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    existing_resolved_at = now - timedelta(hours=1) if current is IncidentStatus.RESOLVED else None

    if (current, target) not in ALLOWED_TRANSITIONS:
        with pytest.raises(InvalidStateTransitionError):
            transition_lifecycle(
                current=current,
                target=target,
                now=now,
                resolved_at=existing_resolved_at,
                closed_at=None,
            )
        return

    lifecycle = transition_lifecycle(
        current=current,
        target=target,
        now=now,
        resolved_at=existing_resolved_at,
        closed_at=None,
    )

    if target is IncidentStatus.RESOLVED:
        assert lifecycle.resolved_at == now
        assert lifecycle.closed_at is None
    elif target is IncidentStatus.INVESTIGATING:
        assert lifecycle.resolved_at is None
        assert lifecycle.closed_at is None
    else:
        assert target is IncidentStatus.CLOSED
        assert lifecycle.resolved_at == existing_resolved_at
        assert lifecycle.closed_at == now


def test_transition_requires_database_compatible_aware_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        transition_lifecycle(
            current=IncidentStatus.OPEN,
            target=IncidentStatus.RESOLVED,
            now=datetime(2026, 9, 5, 12, 0),
            resolved_at=None,
            closed_at=None,
        )
