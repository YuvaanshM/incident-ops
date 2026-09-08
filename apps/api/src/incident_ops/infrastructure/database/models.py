"""SQLAlchemy mappings for the PostgreSQL-authoritative core schema."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    desc,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IncidentModel(TimestampMixin, Base):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint("char_length(btrim(title)) BETWEEN 1 AND 200", name="ck_incidents_title"),
        CheckConstraint(
            "char_length(btrim(description)) BETWEEN 1 AND 10000",
            name="ck_incidents_description",
        ),
        CheckConstraint(
            "char_length(btrim(service)) BETWEEN 1 AND 100", name="ck_incidents_service"
        ),
        CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')", name="ck_incidents_severity"
        ),
        CheckConstraint(
            "status IN ('open', 'investigating', 'resolved', 'closed')",
            name="ck_incidents_status",
        ),
        CheckConstraint("version >= 1", name="ck_incidents_version"),
        CheckConstraint(
            "created_by_type IN ('human', 'agent', 'system')", name="ck_incidents_actor_type"
        ),
        CheckConstraint(
            "char_length(btrim(created_by_id)) BETWEEN 1 AND 255", name="ck_incidents_actor_id"
        ),
        CheckConstraint(
            "(status IN ('open', 'investigating') AND resolved_at IS NULL AND closed_at IS NULL) "
            "OR (status = 'resolved' AND resolved_at IS NOT NULL AND closed_at IS NULL) "
            "OR (status = 'closed' AND resolved_at IS NOT NULL AND closed_at IS NOT NULL "
            "AND closed_at >= resolved_at)",
            name="ck_incidents_lifecycle",
        ),
        CheckConstraint("updated_at >= created_at", name="ck_incidents_timestamp_order"),
        Index("ix_incidents_updated_id", desc("updated_at"), desc("id")),
        Index("ix_incidents_status", "status"),
        Index("ix_incidents_severity", "severity"),
        Index("ix_incidents_service", "service"),
        Index(
            "ix_incidents_status_severity_service_updated_id",
            "status",
            "severity",
            "service",
            desc("updated_at"),
            desc("id"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    service: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="open")
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")
    created_by_type: Mapped[str] = mapped_column(String(20), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(255), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ActionRequestModel(TimestampMixin, Base):
    __tablename__ = "action_requests"
    __table_args__ = (
        CheckConstraint(
            "action_type IN ('retry_failed_job', 'request_status_update')",
            name="ck_action_requests_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'applied', 'rejected', 'failed', 'stale')",
            name="ck_action_requests_status",
        ),
        CheckConstraint(
            "execution_mode IN ('auto', 'human_approved', 'human')",
            name="ck_action_requests_execution_mode",
        ),
        CheckConstraint(
            "requested_by_type IN ('human', 'agent', 'system')",
            name="ck_action_requests_actor_type",
        ),
        CheckConstraint(
            "char_length(btrim(requested_by_id)) BETWEEN 1 AND 255",
            name="ck_action_requests_actor_id",
        ),
        CheckConstraint("base_incident_version >= 1", name="ck_action_requests_base_version"),
        CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_action_requests_fingerprint"
        ),
        CheckConstraint("jsonb_typeof(parameters) = 'object'", name="ck_action_requests_params"),
        CheckConstraint(
            "char_length(btrim(policy_version)) BETWEEN 1 AND 100",
            name="ck_action_requests_policy_version",
        ),
        CheckConstraint(
            "jsonb_typeof(policy_snapshot) = 'object'", name="ck_action_requests_policy_snapshot"
        ),
        CheckConstraint(
            "rationale IS NULL OR char_length(rationale) <= 1000",
            name="ck_action_requests_rationale",
        ),
        CheckConstraint(
            "outcome_payload IS NULL OR jsonb_typeof(outcome_payload) = 'object'",
            name="ck_action_requests_outcome",
        ),
        CheckConstraint(
            "(decided_by_type IS NULL) = (decided_by_id IS NULL)",
            name="ck_action_requests_decider_pair",
        ),
        CheckConstraint(
            "decided_by_type IS NULL OR decided_by_type IN ('human', 'system')",
            name="ck_action_requests_decider_type",
        ),
        CheckConstraint(
            "(status = 'pending' AND decided_at IS NULL) OR "
            "(status <> 'pending' AND decided_at IS NOT NULL)",
            name="ck_action_requests_decision_time",
        ),
        CheckConstraint(
            "(status = 'pending' AND outcome_code IS NULL AND outcome_payload IS NULL "
            "AND decided_by_type IS NULL AND decided_by_id IS NULL) OR "
            "(status <> 'pending' AND outcome_code IS NOT NULL AND outcome_payload IS NOT NULL "
            "AND decided_by_type IS NOT NULL AND decided_by_id IS NOT NULL)",
            name="ck_action_requests_outcome_state",
        ),
        CheckConstraint("updated_at >= created_at", name="ck_action_requests_timestamp_order"),
        UniqueConstraint(
            "requested_by_type",
            "requested_by_id",
            "idempotency_key",
            name="uq_action_requests_principal_idempotency",
        ),
        Index(
            "ix_action_requests_pending",
            "incident_id",
            "status",
            desc("created_at"),
            desc("id"),
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_action_requests_incident_created", "incident_id", desc("created_at")),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    incident_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False
    )
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    requested_by_type: Mapped[str] = mapped_column(String(20), nullable=False)
    requested_by_id: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    base_incident_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(100), nullable=False)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    rationale: Mapped[str | None] = mapped_column(String(1000))
    outcome_code: Mapped[str | None] = mapped_column(String(100))
    outcome_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    decided_by_type: Mapped[str | None] = mapped_column(String(20))
    decided_by_id: Mapped[str | None] = mapped_column(String(255))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IncidentEventModel(Base):
    __tablename__ = "incident_events"
    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(event_type)) BETWEEN 1 AND 100", name="ck_incident_events_type"
        ),
        CheckConstraint(
            "actor_type IN ('human', 'agent', 'system')", name="ck_incident_events_actor_type"
        ),
        CheckConstraint(
            "char_length(btrim(actor_id)) BETWEEN 1 AND 255", name="ck_incident_events_actor_id"
        ),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_incident_events_payload"),
        Index(
            "ix_incident_events_timeline",
            "incident_id",
            desc("occurred_at"),
            desc("id"),
        ),
        Index("ix_incident_events_action", "action_request_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    incident_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False
    )
    action_request_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("action_requests.id", ondelete="RESTRICT")
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    request_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DemoJobModel(TimestampMixin, Base):
    __tablename__ = "demo_jobs"
    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(stable_key)) BETWEEN 1 AND 100", name="ck_demo_jobs_stable_key"
        ),
        CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 200", name="ck_demo_jobs_name"),
        CheckConstraint(
            "status IN ('failed', 'queued', 'running', 'succeeded')", name="ck_demo_jobs_status"
        ),
        CheckConstraint("retry_count BETWEEN 0 AND 2", name="ck_demo_jobs_retry_count"),
        CheckConstraint("version >= 1", name="ck_demo_jobs_version"),
        CheckConstraint(
            "last_error IS NULL OR char_length(last_error) <= 2000", name="ck_demo_jobs_error"
        ),
        CheckConstraint("updated_at >= created_at", name="ck_demo_jobs_timestamp_order"),
        UniqueConstraint("incident_id", "stable_key", name="uq_demo_jobs_incident_stable_key"),
        Index("ix_demo_jobs_incident", "incident_id", desc("updated_at"), desc("id")),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    incident_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    retry_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")
    last_error: Mapped[str | None] = mapped_column(String(2000))


class HistoricalIncidentModel(TimestampMixin, Base):
    __tablename__ = "historical_incidents"
    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(source_id)) BETWEEN 1 AND 100", name="ck_historical_source_id"
        ),
        CheckConstraint("char_length(btrim(title)) BETWEEN 1 AND 200", name="ck_historical_title"),
        CheckConstraint(
            "char_length(btrim(description)) BETWEEN 1 AND 10000",
            name="ck_historical_description",
        ),
        CheckConstraint(
            "char_length(btrim(resolution_summary)) BETWEEN 1 AND 10000",
            name="ck_historical_resolution_summary",
        ),
        CheckConstraint(
            "char_length(btrim(service)) BETWEEN 1 AND 100", name="ck_historical_service"
        ),
        CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')", name="ck_historical_severity"
        ),
        CheckConstraint(
            "jsonb_typeof(resolution_steps) = 'array'", name="ck_historical_resolution_steps"
        ),
        CheckConstraint(
            "char_length(btrim(embedding_provider)) BETWEEN 1 AND 100",
            name="ck_historical_embedding_provider",
        ),
        CheckConstraint(
            "char_length(btrim(embedding_model)) BETWEEN 1 AND 200",
            name="ck_historical_embedding_model",
        ),
        CheckConstraint("updated_at >= created_at", name="ck_historical_timestamp_order"),
        UniqueConstraint("source_id", name="uq_historical_source_id"),
        Index(
            "ix_historical_embedding_corpus",
            "embedding_provider",
            "embedding_model",
            "service",
            "severity",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    resolution_summary: Mapped[str] = mapped_column(Text, nullable=False)
    service: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    resolution_steps: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    embedding_provider: Mapped[str] = mapped_column(String(100), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(200), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1536), nullable=False)


class OutboxEventModel(TimestampMixin, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(event_type)) BETWEEN 1 AND 100", name="ck_outbox_event_type"
        ),
        CheckConstraint(
            "char_length(btrim(aggregate_type)) BETWEEN 1 AND 50",
            name="ck_outbox_aggregate_type",
        ),
        CheckConstraint(
            "char_length(btrim(dedupe_key)) BETWEEN 1 AND 255", name="ck_outbox_dedupe_key"
        ),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_outbox_payload"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'delivered', 'dead_letter')",
            name="ck_outbox_status",
        ),
        CheckConstraint("attempt_count BETWEEN 0 AND 25", name="ck_outbox_attempt_count"),
        CheckConstraint(
            "last_error IS NULL OR char_length(last_error) <= 2000", name="ck_outbox_error"
        ),
        CheckConstraint(
            "(status = 'processing' AND locked_by IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND delivered_at IS NULL) OR "
            "(status = 'delivered' AND locked_by IS NULL AND lease_expires_at IS NULL "
            "AND delivered_at IS NOT NULL) OR "
            "(status IN ('pending', 'dead_letter') AND locked_by IS NULL "
            "AND lease_expires_at IS NULL AND delivered_at IS NULL)",
            name="ck_outbox_lifecycle",
        ),
        CheckConstraint("updated_at >= created_at", name="ck_outbox_timestamp_order"),
        UniqueConstraint("dedupe_key", name="uq_outbox_dedupe_key"),
        Index(
            "ix_outbox_events_claimable",
            "status",
            "available_at",
            "lease_expires_at",
            "id",
            postgresql_where=text("status IN ('pending', 'processing')"),
        ),
        Index("ix_outbox_events_source", "source_event_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("incident_events.id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(50), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_by: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(2000))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotencyRecordModel(TimestampMixin, Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        CheckConstraint(
            "principal_type IN ('human', 'agent', 'system')", name="ck_idempotency_actor_type"
        ),
        CheckConstraint(
            "char_length(btrim(principal_id)) BETWEEN 1 AND 255",
            name="ck_idempotency_actor_id",
        ),
        CheckConstraint(
            "char_length(btrim(operation)) BETWEEN 1 AND 100", name="ck_idempotency_operation"
        ),
        CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_idempotency_fingerprint"
        ),
        CheckConstraint(
            "response_status IS NULL OR response_status BETWEEN 100 AND 599",
            name="ck_idempotency_response_status",
        ),
        CheckConstraint(
            "response_body IS NULL OR jsonb_typeof(response_body) = 'object'",
            name="ck_idempotency_response_body",
        ),
        CheckConstraint(
            "(resource_type IS NULL) = (resource_id IS NULL)", name="ck_idempotency_resource_pair"
        ),
        CheckConstraint(
            "(completed_at IS NULL AND response_status IS NULL AND response_body IS NULL) OR "
            "(completed_at IS NOT NULL AND response_status IS NOT NULL "
            "AND response_body IS NOT NULL)",
            name="ck_idempotency_completion",
        ),
        CheckConstraint("updated_at >= created_at", name="ck_idempotency_timestamp_order"),
        UniqueConstraint(
            "principal_type",
            "principal_id",
            "operation",
            "idempotency_key",
            name="uq_idempotency_principal_operation_key",
        ),
        Index("ix_idempotency_resource", "resource_type", "resource_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    principal_type: Mapped[str] = mapped_column(String(20), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(255), nullable=False)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    resource_type: Mapped[str | None] = mapped_column(String(50))
    resource_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
