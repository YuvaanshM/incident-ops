"""Incident request and response schemas."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from incident_ops.domain.enums import IncidentSeverity, IncidentStatus

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Description = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000),
]
Service = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IncidentCreate(StrictSchema):
    title: Title
    description: Description
    service: Service
    severity: IncidentSeverity


class IncidentUpdate(StrictSchema):
    expected_version: int = Field(ge=1)
    title: Title | None = None
    description: Description | None = None
    service: Service | None = None
    severity: IncidentSeverity | None = None
    status: IncidentStatus | None = None

    @model_validator(mode="after")
    def require_change(self) -> IncidentUpdate:
        if not self.model_fields_set.difference({"expected_version"}):
            raise ValueError("at least one incident field must be supplied")
        return self


class DemoJobResponse(StrictSchema):
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


class IncidentResponse(StrictSchema):
    id: UUID
    title: str
    description: str
    service: str
    severity: IncidentSeverity
    status: IncidentStatus
    version: int
    created_by_type: str
    created_by_id: str
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    closed_at: datetime | None
    monitoring_status: str | None = None
    monitoring_updated_at: datetime | None = None
    jobs: list[DemoJobResponse] = Field(default_factory=list)


class IncidentListResponse(StrictSchema):
    items: list[IncidentResponse]
    next_cursor: str | None


class IncidentEventResponse(StrictSchema):
    id: UUID
    incident_id: UUID
    action_request_id: UUID | None
    event_type: str
    actor_type: str
    actor_id: str
    request_id: UUID
    payload: dict[str, Any]
    occurred_at: datetime


class TimelineResponse(StrictSchema):
    items: list[IncidentEventResponse]
    next_cursor: str | None


class SeverityRecommendationResponse(StrictSchema):
    advisory: bool
    recommended_severity: IncidentSeverity
    reasons: list[str]


class PostmortemDraftResponse(StrictSchema):
    advisory: bool
    incident_id: UUID
    title: str
    summary: str
    impact: str
    timeline: list[str]
    action_items: list[str]
