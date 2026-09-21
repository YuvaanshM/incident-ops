"""Provider-neutral monitoring observations and their intrinsic invariants."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from incident_ops.domain.enums import IncidentSeverity

type JsonScalar = str | int | float | bool | None
type FrozenJson = JsonScalar | tuple[FrozenJson, ...] | Mapping[str, FrozenJson]

_SOURCE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}")
_SERVICE_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,99}")


class AlertStatus(StrEnum):
    FIRING = "FIRING"
    RESOLVED = "RESOLVED"


def _freeze_json(value: object) -> FrozenJson:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list | tuple):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    raise TypeError("alert JSON values must use JSON-compatible types")


@dataclass(frozen=True, slots=True)
class NormalizedAlert:
    """An immutable source fact; it carries no incident or remediation authority."""

    source: str
    external_alert_id: str
    service: str
    severity: IncidentSeverity
    status: AlertStatus
    title: str
    description: str
    started_at: datetime
    resolved_at: datetime | None
    event_version: int
    metadata: Mapping[str, FrozenJson]
    raw_payload: Mapping[str, FrozenJson] | None = None

    def __post_init__(self) -> None:
        if _SOURCE_PATTERN.fullmatch(self.source) is None:
            raise ValueError("invalid alert source")
        if not self.external_alert_id.strip() or len(self.external_alert_id) > 200:
            raise ValueError("invalid external alert identifier")
        if _SERVICE_PATTERN.fullmatch(self.service) is None:
            raise ValueError("invalid alert service")
        if not self.title.strip() or len(self.title) > 200:
            raise ValueError("invalid alert title")
        if not self.description.strip() or len(self.description) > 5000:
            raise ValueError("invalid alert description")
        if not 1 <= self.event_version <= 9_223_372_036_854_775_807:
            raise ValueError("invalid alert event version")
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise ValueError("alert start must be timezone-aware")
        if self.resolved_at is not None and (
            self.resolved_at.tzinfo is None or self.resolved_at.utcoffset() is None
        ):
            raise ValueError("alert resolution must be timezone-aware")
        if self.status is AlertStatus.FIRING and self.resolved_at is not None:
            raise ValueError("firing alert cannot have a resolution time")
        if self.status is AlertStatus.RESOLVED and self.resolved_at is None:
            raise ValueError("resolved alert requires a resolution time")
        if self.resolved_at is not None and self.resolved_at < self.started_at:
            raise ValueError("alert resolution cannot precede its start")

        object.__setattr__(self, "source", self.source.strip())
        object.__setattr__(self, "external_alert_id", self.external_alert_id.strip())
        object.__setattr__(self, "service", self.service.strip())
        object.__setattr__(self, "title", self.title.strip())
        object.__setattr__(self, "description", self.description.strip())
        object.__setattr__(self, "started_at", self.started_at.astimezone(UTC))
        if self.resolved_at is not None:
            object.__setattr__(self, "resolved_at", self.resolved_at.astimezone(UTC))
        object.__setattr__(self, "metadata", _freeze_json(self.metadata))
        if self.raw_payload is not None:
            object.__setattr__(self, "raw_payload", _freeze_json(self.raw_payload))

    @property
    def logical_identity(self) -> tuple[str, str, str]:
        return (self.source, self.external_alert_id, self.service)
