"""Strict action and approval HTTP schemas."""

from datetime import datetime
from typing import Annotated, Any, Self
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from incident_ops.api.schemas.incidents import StrictSchema
from incident_ops.domain.enums import ActionStatus, IncidentStatus

Rationale = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000),
]


class RetryFailedJobParameters(StrictSchema):
    job_id: UUID


class StatusUpdateParameters(StrictSchema):
    target_status: IncidentStatus


class ActionRequestCreate(StrictSchema):
    expected_incident_version: int = Field(ge=1)
    action_type: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)
    ]
    parameters: dict[str, Any]
    rationale: Rationale | None = None

    @model_validator(mode="after")
    def validate_registered_parameters(self) -> Self:
        if self.action_type == "retry_failed_job":
            RetryFailedJobParameters.model_validate(self.parameters)
        elif self.action_type == "request_status_update":
            StatusUpdateParameters.model_validate(self.parameters)
        return self

    def normalized_parameters(self) -> dict[str, object]:
        if self.action_type == "retry_failed_job":
            retry_parameters = RetryFailedJobParameters.model_validate(self.parameters)
            return {"job_id": retry_parameters.job_id}
        if self.action_type == "request_status_update":
            status_parameters = StatusUpdateParameters.model_validate(self.parameters)
            return {"target_status": status_parameters.target_status}
        return dict(self.parameters)


class ActionReject(StrictSchema):
    reason: Rationale | None = None


class ActionResponse(StrictSchema):
    id: UUID
    incident_id: UUID
    action_type: str
    status: ActionStatus
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


class ActionListResponse(StrictSchema):
    items: list[ActionResponse]
    next_cursor: str | None
