"""Outbox delivery contracts; no database or provider dependencies."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from incident_ops.domain.delivery import DeliveryResult


@dataclass(frozen=True)
class OutboxClaim:
    id: UUID
    source_event_id: UUID
    incident_id: UUID
    action_id: UUID | None
    request_id: UUID
    event_type: str
    payload: Mapping[str, object] = field(repr=False)
    attempt_count: int
    lease_token: str
    lease_expires_at: datetime


class OutboxDeliveryRepository(Protocol):
    async def claim(self, *, batch_size: int, lease_seconds: float) -> tuple[OutboxClaim, ...]: ...

    async def finalize(
        self,
        claim: OutboxClaim,
        result: DeliveryResult,
        *,
        retry_delay: float | None,
    ) -> bool: ...

    async def ready(self) -> bool: ...


class NotificationSender(Protocol):
    async def send(self, claim: OutboxClaim) -> DeliveryResult: ...
