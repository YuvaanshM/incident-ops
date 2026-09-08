"""Shared domain values represented as checked text in PostgreSQL."""

from enum import StrEnum


class ActorType(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    SYSTEM = "system"


class IncidentSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    CLOSED = "closed"


class ActionType(StrEnum):
    RETRY_FAILED_JOB = "retry_failed_job"
    REQUEST_STATUS_UPDATE = "request_status_update"


class ActionStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"
    STALE = "stale"


class ExecutionMode(StrEnum):
    AUTO = "auto"
    HUMAN_APPROVED = "human_approved"
    HUMAN = "human"


class DemoJobStatus(StrEnum):
    FAILED = "failed"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    DELIVERED = "delivered"
    DEAD_LETTER = "dead_letter"


class PolicyDecision(StrEnum):
    AUTO_APPLY = "auto_apply"
    REQUIRE_APPROVAL = "require_approval"
    REJECT = "reject"
