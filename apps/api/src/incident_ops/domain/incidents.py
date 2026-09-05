"""Incident state transitions and lifecycle timestamp rules."""

from dataclasses import dataclass
from datetime import datetime

from incident_ops.domain.enums import IncidentStatus
from incident_ops.domain.errors import InvalidStateTransitionError

_ALLOWED_TRANSITIONS: frozenset[tuple[IncidentStatus, IncidentStatus]] = frozenset(
    {
        (IncidentStatus.OPEN, IncidentStatus.INVESTIGATING),
        (IncidentStatus.OPEN, IncidentStatus.RESOLVED),
        (IncidentStatus.INVESTIGATING, IncidentStatus.RESOLVED),
        (IncidentStatus.RESOLVED, IncidentStatus.INVESTIGATING),
        (IncidentStatus.RESOLVED, IncidentStatus.CLOSED),
    }
)


@dataclass(frozen=True, slots=True)
class LifecycleTimestamps:
    resolved_at: datetime | None
    closed_at: datetime | None


def can_transition(current: IncidentStatus, target: IncidentStatus) -> bool:
    """Return whether the directed state-machine edge is allowed."""

    return (current, target) in _ALLOWED_TRANSITIONS


def transition_lifecycle(
    *,
    current: IncidentStatus,
    target: IncidentStatus,
    now: datetime,
    resolved_at: datetime | None,
    closed_at: datetime | None,
) -> LifecycleTimestamps:
    """Validate a transition and return its authoritative lifecycle timestamps."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if not can_transition(current, target):
        raise InvalidStateTransitionError()

    if target is IncidentStatus.RESOLVED:
        return LifecycleTimestamps(resolved_at=now, closed_at=None)
    if target is IncidentStatus.INVESTIGATING:
        return LifecycleTimestamps(resolved_at=None, closed_at=None)

    if resolved_at is None:
        raise InvalidStateTransitionError()
    return LifecycleTimestamps(resolved_at=resolved_at, closed_at=now)
