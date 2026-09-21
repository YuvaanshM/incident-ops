"""Provider-neutral application ports and persistence intentions."""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol
from uuid import UUID

from incident_ops.application.auth import Principal
from incident_ops.application.dto import (
    ActionRequestView,
    AlertEventView,
    AlertRecord,
    DemoJobView,
    IdempotencyReservation,
    IncidentEventView,
    IncidentView,
)
from incident_ops.domain.alerts import NormalizedAlert


class TokenVerifier(Protocol):
    """Verify an opaque credential and derive a server-trusted principal."""

    async def verify(self, token: str) -> Principal: ...


class CommandObserver(Protocol):
    """Optional owned hook for synchronization, tracing, and failure-boundary tests."""

    async def reached(self, point: str) -> None: ...


class RateLimiter(Protocol):
    async def check(self, principal: Principal, *, bucket: str) -> None: ...


class AlertPayloadAdapter(Protocol):
    """Map one authenticated provider envelope into the normalized alert contract."""

    def normalize(self, body: bytes) -> NormalizedAlert: ...


class AlertRepository(Protocol):
    async def lock_identity(
        self, source: str, external_alert_id: str, service: str
    ) -> AlertRecord | None: ...

    async def create(
        self,
        *,
        alert: NormalizedAlert,
        incident_id: UUID | None,
        state_fingerprint: str,
        metadata: Mapping[str, object],
    ) -> AlertRecord: ...

    async def advance(
        self, *, alert_id: UUID, expected_version: int, values: Mapping[str, object]
    ) -> AlertRecord | None: ...

    async def occurrence_history(
        self, *, alert_id: UUID, started_at: datetime
    ) -> tuple[bool, UUID | None]: ...

    async def append_event(
        self,
        *,
        alert_id: UUID,
        incident_id: UUID | None,
        principal: Principal,
        idempotency_key: UUID,
        request_id: UUID,
        alert: NormalizedAlert,
        metadata: Mapping[str, object],
        request_fingerprint: str,
        state_fingerprint: str,
        outcome: str,
    ) -> AlertEventView: ...

    async def list_for_incident(
        self,
        *,
        incident_id: UUID,
        cursor_timestamp: datetime | None,
        cursor_id: UUID | None,
        limit: int,
    ) -> tuple[AlertEventView, ...]: ...

    async def has_higher_firing_risk(self, incident_id: UUID, severity: str) -> bool: ...

    async def review_signaled(
        self, alert_id: UUID, started_at: datetime, severity: str
    ) -> bool: ...


class IncidentRepository(Protocol):
    async def add_incident(
        self,
        *,
        title: str,
        description: str,
        service: str,
        severity: str,
        principal: Principal,
        monitoring_status: str | None = None,
    ) -> IncidentView: ...

    async def get(self, incident_id: UUID, *, for_update: bool = False) -> IncidentView | None: ...

    async def get_with_jobs(self, incident_id: UUID) -> IncidentView | None: ...

    async def list_page(
        self,
        *,
        query: str | None,
        service: str | None,
        severity: str | None,
        status: str | None,
        cursor_timestamp: datetime | None,
        cursor_id: UUID | None,
        limit: int,
    ) -> tuple[IncidentView, ...]: ...

    async def conditional_update(
        self,
        *,
        incident_id: UUID,
        expected_version: int,
        values: Mapping[str, object],
    ) -> IncidentView | None: ...

    async def database_now(self) -> datetime: ...

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
    async def add_action(
        self,
        *,
        incident_id: UUID,
        action_type: str,
        status: str,
        execution_mode: str,
        principal: Principal,
        idempotency_key: UUID,
        request_fingerprint: str,
        base_incident_version: int,
        parameters: Mapping[str, object],
        policy_version: str,
        policy_snapshot: Mapping[str, object],
        rationale: str | None,
        outcome_code: str | None,
        outcome_payload: Mapping[str, object] | None,
        decided_by_type: str | None,
        decided_by_id: str | None,
        decided_at: datetime | None,
    ) -> ActionRequestView: ...

    async def get(self, action_id: UUID) -> ActionRequestView | None: ...

    async def list_page(
        self,
        *,
        status: str | None,
        incident_id: UUID | None,
        created_before: datetime | None,
        cursor_timestamp: datetime | None,
        cursor_id: UUID | None,
        limit: int,
    ) -> tuple[ActionRequestView, ...]: ...

    async def lock_pending(self, action_id: UUID) -> ActionRequestView | None: ...

    async def finalize(
        self,
        *,
        action_id: UUID,
        status: str,
        outcome_code: str,
        outcome_payload: Mapping[str, object],
        principal: Principal,
    ) -> ActionRequestView | None: ...


class DemoJobRepository(Protocol):
    async def get(self, job_id: UUID, *, for_update: bool = False) -> DemoJobView | None: ...

    async def queue_failed(
        self,
        *,
        job_id: UUID,
        incident_id: UUID,
        expected_version: int,
        expected_retry_count: int,
    ) -> DemoJobView | None: ...

    async def simulate_failure(self, *, job_id: UUID) -> DemoJobView | None: ...


class IdempotencyRepository(Protocol):
    async def reserve(
        self,
        *,
        principal: Principal,
        operation: str,
        key: UUID,
        fingerprint: str,
    ) -> IdempotencyReservation: ...

    async def complete(
        self,
        *,
        record_id: UUID,
        response_status: int,
        response_body: Mapping[str, object],
        resource_type: str | None = None,
        resource_id: UUID | None = None,
    ) -> None: ...


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

    async def list_for_incident(
        self,
        *,
        incident_id: UUID,
        cursor_timestamp: datetime | None,
        cursor_id: UUID | None,
        limit: int,
    ) -> tuple[IncidentEventView, ...]: ...


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
    ) -> None: ...


class UnitOfWork(Protocol):
    alerts: AlertRepository
    incidents: IncidentRepository
    actions: ActionRequestRepository
    jobs: DemoJobRepository
    idempotency: IdempotencyRepository
    events: EventRepository
    outbox: OutboxRepository

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...
