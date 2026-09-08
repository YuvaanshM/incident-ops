"""Principal-aware in-process rate limiting for the single-instance v1 API."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock

from incident_ops.application.auth import Principal
from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import RateLimitExceededError


@dataclass(slots=True)
class _Window:
    started_at: datetime
    count: int


class FixedWindowRateLimiter:
    """Bound requests by verified principal and logical endpoint bucket."""

    def __init__(
        self,
        *,
        window: timedelta = timedelta(minutes=1),
        human_limit: int = 120,
        agent_limit: int = 30,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if window <= timedelta(0) or human_limit < 1 or agent_limit < 1:
            raise ValueError("rate-limit settings must be positive")
        if agent_limit > human_limit:
            raise ValueError("agent limit cannot exceed human limit")
        self._window_duration = window
        self._human_limit = human_limit
        self._agent_limit = agent_limit
        self._clock = clock
        self._windows: dict[tuple[str, str, str], _Window] = {}
        self._lock = Lock()

    async def check(self, principal: Principal, *, bucket: str) -> None:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("rate-limit clock must return timezone-aware time")
        key = (principal.actor_type.value, principal.subject, bucket)
        limit = self._agent_limit if principal.actor_type is ActorType.AGENT else self._human_limit

        with self._lock:
            current = self._windows.get(key)
            if current is None or now - current.started_at >= self._window_duration:
                self._windows[key] = _Window(started_at=now, count=1)
                return
            if current.count >= limit:
                retry_after = max(
                    1,
                    int((current.started_at + self._window_duration - now).total_seconds()),
                )
                raise RateLimitExceededError(details={"retry_after_seconds": retry_after})
            current.count += 1
