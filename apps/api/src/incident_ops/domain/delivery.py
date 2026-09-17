"""Provider-neutral delivery outcomes and bounded retry policy."""

from dataclasses import dataclass
from typing import Literal

DeliveryCode = Literal[
    "delivered",
    "rate_limited",
    "provider_unavailable",
    "network_error",
    "invalid_response",
    "provider_rejected",
    "invalid_payload",
    "attempts_exhausted",
]


@dataclass(frozen=True)
class DeliveryResult:
    code: DeliveryCode
    retryable: bool = False
    retry_after: float | None = None


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_seconds: float = 5
    max_seconds: float = 300

    def __post_init__(self) -> None:
        if (
            not 1 <= self.max_attempts <= 25
            or not 1 <= self.base_seconds <= self.max_seconds <= 3600
        ):
            raise ValueError("invalid retry policy")

    def delay(self, attempt: int, random_value: float, retry_after: float | None = None) -> float:
        """Equal jitter, bounded by configuration, with a bounded provider minimum."""
        if not 1 <= attempt <= 25 or not 0 <= random_value <= 1:
            raise ValueError("invalid retry inputs")
        ceiling = min(self.max_seconds, self.base_seconds * 2 ** (attempt - 1))
        jittered = ceiling * (0.5 + random_value / 2)
        return max(jittered, min(self.max_seconds, max(0, retry_after or 0)))
