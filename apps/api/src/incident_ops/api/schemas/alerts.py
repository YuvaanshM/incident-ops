"""Strict normalized alert envelope used by the generic adapter."""

import json
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from incident_ops.domain.enums import IncidentSeverity


class GenericAlertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    schema_version: Literal[1]
    source: Annotated[
        str,
        Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
    ]
    external_alert_id: Annotated[str, Field(min_length=1, max_length=200)]
    service: Annotated[
        str,
        Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9._-]*$"),
    ]
    severity: IncidentSeverity
    status: Literal["FIRING", "ACTIVE", "RESOLVED"]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str, Field(min_length=1, max_length=5000)]
    started_at: datetime
    resolved_at: datetime | None
    event_version: Annotated[int, Field(ge=1, le=9_223_372_036_854_775_807)]
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    raw_payload: dict[str, JsonValue] | None = None

    @field_validator("severity", mode="before")
    @classmethod
    def parse_severity(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("severity must be a string")
        try:
            return IncidentSeverity(value)
        except ValueError:
            raise ValueError("unsupported alert severity") from None

    @field_validator("source", "external_alert_id", "service", "title", "description")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("field must not be blank")
        return stripped

    @field_validator("started_at", "resolved_at", mode="before")
    @classmethod
    def parse_rfc3339(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str) or "T" not in value:
            raise ValueError("timestamp must be RFC 3339")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("timestamp must be RFC 3339") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timestamp must include an offset")
        return parsed

    @model_validator(mode="after")
    def validate_lifecycle_and_json_sizes(self) -> Self:
        if self.status in {"FIRING", "ACTIVE"} and self.resolved_at is not None:
            raise ValueError("firing alert cannot have a resolution time")
        if self.status == "RESOLVED" and self.resolved_at is None:
            raise ValueError("resolved alert requires a resolution time")
        if self.resolved_at is not None and self.resolved_at < self.started_at:
            raise ValueError("alert resolution cannot precede its start")
        if _json_size(self.metadata) > 8 * 1024:
            raise ValueError("alert metadata is too large")
        if self.raw_payload is not None and _json_size(self.raw_payload) > 32 * 1024:
            raise ValueError("raw alert payload is too large")
        return self


def _json_size(value: object) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode(
            "utf-8"
        )
    )


class AlertEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    alert_id: UUID
    incident_id: UUID
    source: str
    started_at: datetime
    event_version: int
    status: Literal["FIRING", "RESOLVED"]
    severity: IncidentSeverity
    title: str
    outcome: Literal[
        "incident_created",
        "incident_updated",
        "resolution_recorded",
        "unchanged",
        "stale_ignored",
        "orphan_resolution",
        "suppressed_terminal",
    ]
    received_at: datetime


class AlertListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AlertEventResponse]
    next_cursor: str | None
