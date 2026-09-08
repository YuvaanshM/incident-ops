"""Provider-neutral application ports and persistence intentions."""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol
from uuid import UUID

from incident_ops.application.auth import Principal


class TokenVerifier(Protocol):
    """Verify an opaque credential and derive a server-trusted principal."""

    async def verify(self, token: str) -> Principal: ...


class IncidentRepository(Protocol):
    async def transition_status(
        self,
        *,
        incident_id: UUID,
        expected_version: int,
        target_status: str,
        resolved_at: datetime | None,
        closed_at: datetime | None,
    ) -> bool: ...


class ActionRequestRepository(Protocol):
    async def lock_pending(self, action_id: UUID) -> object | None: ...


class IdempotencyRepository(Protocol):
    async def reserve(
        self,
        *,
        principal: Principal,
        operation: str,
        key: UUID,
        fingerprint: str,
    ) -> object: ...


class EventRepository(Protocol):
    async def append(
        self,
        *,
        incident_id: UUID,
        event_type: str,
        principal: Principal,
        request_id: UUID,
        payload: Mapping[str, object],
        action_request_id: UUID | None = None,
    ) -> object: ...


class OutboxRepository(Protocol):
    async def enqueue(
        self,
        *,
        source_event_id: UUID,
        event_type: str,
        aggregate_type: str,
        aggregate_id: UUID,
        dedupe_key: str,
        payload: Mapping[str, object],
    ) -> object: ...


class UnitOfWork(Protocol):
    incidents: IncidentRepository
    actions: ActionRequestRepository
    idempotency: IdempotencyRepository
    events: EventRepository
    outbox: OutboxRepository

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...
