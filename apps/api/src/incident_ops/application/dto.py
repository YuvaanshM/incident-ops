"""Framework-neutral data transfer objects returned by application services."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class DemoJobView:
    id: UUID
    incident_id: UUID
    stable_key: str
    name: str
    status: str
    retry_count: int
    version: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class IncidentView:
    id: UUID
    title: str
    description: str
    service: str
    severity: str
    status: str
    version: int
    created_by_type: str
    created_by_id: str
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    closed_at: datetime | None
    jobs: tuple[DemoJobView, ...] = ()


@dataclass(frozen=True, slots=True)
class IncidentEventView:
    id: UUID
    incident_id: UUID
    action_request_id: UUID | None
    event_type: str
    actor_type: str
    actor_id: str
    request_id: UUID
    payload: dict[str, Any]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ActionRequestView:
    id: UUID
    incident_id: UUID
    action_type: str
    status: str
    execution_mode: str
    requested_by_type: str
    requested_by_id: str
    idempotency_key: UUID
    base_incident_version: int
    parameters: dict[str, Any]
    policy_version: str
    policy_snapshot: dict[str, Any]
    rationale: str | None
    outcome_code: str | None
    outcome_payload: dict[str, Any] | None
    decided_by_type: str | None
    decided_by_id: str | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class CommandResult:
    status_code: int
    body: dict[str, Any]


@dataclass(frozen=True, slots=True)
class IdempotencyReservation:
    created: bool
    record_id: UUID
    response_status: int | None
    response_body: dict[str, Any] | None
    resource_type: str | None
    resource_id: UUID | None


@dataclass(frozen=True, slots=True)
class Page:
    items: tuple[object, ...]
    next_cursor: str | None
