"""Create the Phase 2 authoritative core schema.

Revision ID: 20260905_0002
Revises: 20260905_0001
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "20260905_0002"
down_revision: str | None = "20260905_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")
UUID_DEFAULT = sa.text("gen_random_uuid()")


def upgrade() -> None:
    _create_tables()
    _create_indexes()
    _create_event_immutability_trigger()
    _create_rls_context_functions()
    _enable_rls_and_policies()


def _create_tables() -> None:
    op.create_table(
        "incidents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=UUID_DEFAULT),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("service", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("created_by_type", sa.String(20), nullable=False),
        sa.Column("created_by_id", sa.String(255), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("char_length(btrim(title)) BETWEEN 1 AND 200", name="ck_incidents_title"),
        sa.CheckConstraint(
            "char_length(btrim(description)) BETWEEN 1 AND 10000",
            name="ck_incidents_description",
        ),
        sa.CheckConstraint(
            "char_length(btrim(service)) BETWEEN 1 AND 100", name="ck_incidents_service"
        ),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')", name="ck_incidents_severity"
        ),
        sa.CheckConstraint(
            "status IN ('open', 'investigating', 'resolved', 'closed')",
            name="ck_incidents_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_incidents_version"),
        sa.CheckConstraint(
            "created_by_type IN ('human', 'agent', 'system')", name="ck_incidents_actor_type"
        ),
        sa.CheckConstraint(
            "char_length(btrim(created_by_id)) BETWEEN 1 AND 255", name="ck_incidents_actor_id"
        ),
        sa.CheckConstraint(
            "(status IN ('open', 'investigating') AND resolved_at IS NULL AND closed_at IS NULL) "
            "OR (status = 'resolved' AND resolved_at IS NOT NULL AND closed_at IS NULL) "
            "OR (status = 'closed' AND resolved_at IS NOT NULL AND closed_at IS NOT NULL "
            "AND closed_at >= resolved_at)",
            name="ck_incidents_lifecycle",
        ),
        sa.CheckConstraint("updated_at >= created_at", name="ck_incidents_timestamp_order"),
    )

    op.create_table(
        "action_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=UUID_DEFAULT),
        sa.Column(
            "incident_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incidents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("execution_mode", sa.String(30), nullable=False),
        sa.Column("requested_by_type", sa.String(20), nullable=False),
        sa.Column("requested_by_id", sa.String(255), nullable=False),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("base_incident_version", sa.BigInteger(), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("policy_version", sa.String(100), nullable=False),
        sa.Column("policy_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("rationale", sa.String(1000)),
        sa.Column("outcome_code", sa.String(100)),
        sa.Column("outcome_payload", postgresql.JSONB()),
        sa.Column("decided_by_type", sa.String(20)),
        sa.Column("decided_by_id", sa.String(255)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "action_type IN ('retry_failed_job', 'request_status_update')",
            name="ck_action_requests_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'applied', 'rejected', 'failed', 'stale')",
            name="ck_action_requests_status",
        ),
        sa.CheckConstraint(
            "execution_mode IN ('auto', 'human_approved', 'human')",
            name="ck_action_requests_execution_mode",
        ),
        sa.CheckConstraint(
            "requested_by_type IN ('human', 'agent', 'system')",
            name="ck_action_requests_actor_type",
        ),
        sa.CheckConstraint(
            "char_length(btrim(requested_by_id)) BETWEEN 1 AND 255",
            name="ck_action_requests_actor_id",
        ),
        sa.CheckConstraint("base_incident_version >= 1", name="ck_action_requests_base_version"),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_action_requests_fingerprint"
        ),
        sa.CheckConstraint("jsonb_typeof(parameters) = 'object'", name="ck_action_requests_params"),
        sa.CheckConstraint(
            "char_length(btrim(policy_version)) BETWEEN 1 AND 100",
            name="ck_action_requests_policy_version",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(policy_snapshot) = 'object'", name="ck_action_requests_policy_snapshot"
        ),
        sa.CheckConstraint(
            "rationale IS NULL OR char_length(rationale) <= 1000",
            name="ck_action_requests_rationale",
        ),
        sa.CheckConstraint(
            "outcome_payload IS NULL OR jsonb_typeof(outcome_payload) = 'object'",
            name="ck_action_requests_outcome",
        ),
        sa.CheckConstraint(
            "(decided_by_type IS NULL) = (decided_by_id IS NULL)",
            name="ck_action_requests_decider_pair",
        ),
        sa.CheckConstraint(
            "decided_by_type IS NULL OR decided_by_type IN ('human', 'system')",
            name="ck_action_requests_decider_type",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND decided_at IS NULL) OR "
            "(status <> 'pending' AND decided_at IS NOT NULL)",
            name="ck_action_requests_decision_time",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND outcome_code IS NULL AND outcome_payload IS NULL "
            "AND decided_by_type IS NULL AND decided_by_id IS NULL) OR "
            "(status <> 'pending' AND outcome_code IS NOT NULL AND outcome_payload IS NOT NULL "
            "AND decided_by_type IS NOT NULL AND decided_by_id IS NOT NULL)",
            name="ck_action_requests_outcome_state",
        ),
        sa.CheckConstraint("updated_at >= created_at", name="ck_action_requests_timestamp_order"),
        sa.UniqueConstraint(
            "requested_by_type",
            "requested_by_id",
            "idempotency_key",
            name="uq_action_requests_principal_idempotency",
        ),
    )

    op.create_table(
        "incident_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=UUID_DEFAULT),
        sa.Column(
            "incident_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incidents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "action_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("action_requests.id", ondelete="RESTRICT"),
        ),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("actor_type", sa.String(20), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "char_length(btrim(event_type)) BETWEEN 1 AND 100", name="ck_incident_events_type"
        ),
        sa.CheckConstraint(
            "actor_type IN ('human', 'agent', 'system')", name="ck_incident_events_actor_type"
        ),
        sa.CheckConstraint(
            "char_length(btrim(actor_id)) BETWEEN 1 AND 255", name="ck_incident_events_actor_id"
        ),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_incident_events_payload"),
    )

    op.create_table(
        "demo_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=UUID_DEFAULT),
        sa.Column(
            "incident_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incidents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("stable_key", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("retry_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("last_error", sa.String(2000)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "char_length(btrim(stable_key)) BETWEEN 1 AND 100", name="ck_demo_jobs_stable_key"
        ),
        sa.CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 200", name="ck_demo_jobs_name"),
        sa.CheckConstraint(
            "status IN ('failed', 'queued', 'running', 'succeeded')", name="ck_demo_jobs_status"
        ),
        sa.CheckConstraint("retry_count BETWEEN 0 AND 2", name="ck_demo_jobs_retry_count"),
        sa.CheckConstraint("version >= 1", name="ck_demo_jobs_version"),
        sa.CheckConstraint(
            "last_error IS NULL OR char_length(last_error) <= 2000", name="ck_demo_jobs_error"
        ),
        sa.CheckConstraint("updated_at >= created_at", name="ck_demo_jobs_timestamp_order"),
        sa.UniqueConstraint("incident_id", "stable_key", name="uq_demo_jobs_incident_stable_key"),
    )

    op.create_table(
        "historical_incidents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=UUID_DEFAULT),
        sa.Column("source_id", sa.String(100), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("resolution_summary", sa.Text(), nullable=False),
        sa.Column("service", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("resolution_steps", postgresql.JSONB(), nullable=False),
        sa.Column("embedding_provider", sa.String(100), nullable=False),
        sa.Column("embedding_model", sa.String(200), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "char_length(btrim(source_id)) BETWEEN 1 AND 100", name="ck_historical_source_id"
        ),
        sa.CheckConstraint(
            "char_length(btrim(title)) BETWEEN 1 AND 200", name="ck_historical_title"
        ),
        sa.CheckConstraint(
            "char_length(btrim(description)) BETWEEN 1 AND 10000",
            name="ck_historical_description",
        ),
        sa.CheckConstraint(
            "char_length(btrim(resolution_summary)) BETWEEN 1 AND 10000",
            name="ck_historical_resolution_summary",
        ),
        sa.CheckConstraint(
            "char_length(btrim(service)) BETWEEN 1 AND 100", name="ck_historical_service"
        ),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')", name="ck_historical_severity"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(resolution_steps) = 'array'", name="ck_historical_resolution_steps"
        ),
        sa.CheckConstraint(
            "char_length(btrim(embedding_provider)) BETWEEN 1 AND 100",
            name="ck_historical_embedding_provider",
        ),
        sa.CheckConstraint(
            "char_length(btrim(embedding_model)) BETWEEN 1 AND 200",
            name="ck_historical_embedding_model",
        ),
        sa.CheckConstraint("updated_at >= created_at", name="ck_historical_timestamp_order"),
        sa.UniqueConstraint("source_id", name="uq_historical_source_id"),
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=UUID_DEFAULT),
        sa.Column(
            "source_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incident_events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("aggregate_type", sa.String(50), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dedupe_key", sa.String(255), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("locked_by", sa.String(255)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(2000)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "char_length(btrim(event_type)) BETWEEN 1 AND 100", name="ck_outbox_event_type"
        ),
        sa.CheckConstraint(
            "char_length(btrim(aggregate_type)) BETWEEN 1 AND 50",
            name="ck_outbox_aggregate_type",
        ),
        sa.CheckConstraint(
            "char_length(btrim(dedupe_key)) BETWEEN 1 AND 255", name="ck_outbox_dedupe_key"
        ),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_outbox_payload"),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'delivered', 'dead_letter')",
            name="ck_outbox_status",
        ),
        sa.CheckConstraint("attempt_count BETWEEN 0 AND 25", name="ck_outbox_attempt_count"),
        sa.CheckConstraint(
            "last_error IS NULL OR char_length(last_error) <= 2000", name="ck_outbox_error"
        ),
        sa.CheckConstraint(
            "(status = 'processing' AND locked_by IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND delivered_at IS NULL) OR "
            "(status = 'delivered' AND locked_by IS NULL AND lease_expires_at IS NULL "
            "AND delivered_at IS NOT NULL) OR "
            "(status IN ('pending', 'dead_letter') AND locked_by IS NULL "
            "AND lease_expires_at IS NULL AND delivered_at IS NULL)",
            name="ck_outbox_lifecycle",
        ),
        sa.CheckConstraint("updated_at >= created_at", name="ck_outbox_timestamp_order"),
        sa.UniqueConstraint("dedupe_key", name="uq_outbox_dedupe_key"),
    )

    op.create_table(
        "idempotency_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=UUID_DEFAULT),
        sa.Column("principal_type", sa.String(20), nullable=False),
        sa.Column("principal_id", sa.String(255), nullable=False),
        sa.Column("operation", sa.String(100), nullable=False),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer()),
        sa.Column("response_body", postgresql.JSONB()),
        sa.Column("resource_type", sa.String(50)),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "principal_type IN ('human', 'agent', 'system')", name="ck_idempotency_actor_type"
        ),
        sa.CheckConstraint(
            "char_length(btrim(principal_id)) BETWEEN 1 AND 255",
            name="ck_idempotency_actor_id",
        ),
        sa.CheckConstraint(
            "char_length(btrim(operation)) BETWEEN 1 AND 100", name="ck_idempotency_operation"
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_idempotency_fingerprint"
        ),
        sa.CheckConstraint(
            "response_status IS NULL OR response_status BETWEEN 100 AND 599",
            name="ck_idempotency_response_status",
        ),
        sa.CheckConstraint(
            "response_body IS NULL OR jsonb_typeof(response_body) = 'object'",
            name="ck_idempotency_response_body",
        ),
        sa.CheckConstraint(
            "(resource_type IS NULL) = (resource_id IS NULL)", name="ck_idempotency_resource_pair"
        ),
        sa.CheckConstraint(
            "(completed_at IS NULL AND response_status IS NULL AND response_body IS NULL) OR "
            "(completed_at IS NOT NULL AND response_status IS NOT NULL "
            "AND response_body IS NOT NULL)",
            name="ck_idempotency_completion",
        ),
        sa.CheckConstraint("updated_at >= created_at", name="ck_idempotency_timestamp_order"),
        sa.UniqueConstraint(
            "principal_type",
            "principal_id",
            "operation",
            "idempotency_key",
            name="uq_idempotency_principal_operation_key",
        ),
    )


def _create_indexes() -> None:
    op.create_index(
        "ix_incidents_updated_id",
        "incidents",
        [sa.text("updated_at DESC"), sa.text("id DESC")],
    )
    op.create_index("ix_incidents_status", "incidents", ["status"])
    op.create_index("ix_incidents_severity", "incidents", ["severity"])
    op.create_index("ix_incidents_service", "incidents", ["service"])
    op.create_index(
        "ix_incidents_status_severity_service_updated_id",
        "incidents",
        ["status", "severity", "service", sa.text("updated_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_action_requests_pending",
        "action_requests",
        ["incident_id", "status", sa.text("created_at DESC"), sa.text("id DESC")],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_action_requests_incident_created",
        "action_requests",
        ["incident_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_incident_events_timeline",
        "incident_events",
        ["incident_id", sa.text("occurred_at DESC"), sa.text("id DESC")],
    )
    op.create_index("ix_incident_events_action", "incident_events", ["action_request_id"])
    op.create_index(
        "ix_demo_jobs_incident",
        "demo_jobs",
        ["incident_id", sa.text("updated_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_historical_embedding_corpus",
        "historical_incidents",
        ["embedding_provider", "embedding_model", "service", "severity", "id"],
    )
    op.create_index(
        "ix_outbox_events_claimable",
        "outbox_events",
        ["status", "available_at", "lease_expires_at", "id"],
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )
    op.create_index("ix_outbox_events_source", "outbox_events", ["source_event_id"])
    op.create_index(
        "ix_idempotency_resource",
        "idempotency_records",
        ["resource_type", "resource_id"],
    )


def _create_event_immutability_trigger() -> None:
    op.execute(
        """
        CREATE FUNCTION reject_incident_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'incident_events are immutable' USING ERRCODE = '23000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER incident_events_immutable
        BEFORE UPDATE OR DELETE ON incident_events
        FOR EACH ROW EXECUTE FUNCTION reject_incident_event_mutation()
        """
    )


def _create_rls_context_functions() -> None:
    op.execute(
        """
        CREATE FUNCTION incident_ops_actor_type()
        RETURNS text LANGUAGE sql STABLE
        AS $$ SELECT nullif(current_setting('app.actor_type', true), '') $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION incident_ops_actor_id()
        RETURNS text LANGUAGE sql STABLE
        AS $$ SELECT nullif(current_setting('app.actor_id', true), '') $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION incident_ops_has_scope(required_scope text)
        RETURNS boolean LANGUAGE sql STABLE
        AS $$
            SELECT COALESCE(
                nullif(current_setting('app.scopes', true), '')::jsonb ? required_scope,
                false
            )
        $$
        """
    )


def _enable_rls_and_policies() -> None:
    for table_name in (
        "incidents",
        "action_requests",
        "incident_events",
        "demo_jobs",
        "historical_incidents",
        "outbox_events",
        "idempotency_records",
    ):
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")

    policies = (
        """
        CREATE POLICY incidents_select ON incidents FOR SELECT
            USING (incident_ops_has_scope('incidents:read'))
        """,
        """
        CREATE POLICY incidents_insert ON incidents FOR INSERT
            WITH CHECK (
                incident_ops_has_scope('incidents:write')
                AND created_by_type = incident_ops_actor_type()
                AND created_by_id = incident_ops_actor_id()
            )
        """,
        """
        CREATE POLICY incidents_update ON incidents FOR UPDATE
            USING (incident_ops_has_scope('incidents:write'))
            WITH CHECK (incident_ops_has_scope('incidents:write'))
        """,
        """
        CREATE POLICY actions_select ON action_requests FOR SELECT
            USING (
                incident_ops_has_scope('actions:read')
                OR incident_ops_has_scope('actions:approve')
                OR (
                    requested_by_type = incident_ops_actor_type()
                    AND requested_by_id = incident_ops_actor_id()
                )
            )
        """,
        """
        CREATE POLICY actions_insert ON action_requests FOR INSERT
            WITH CHECK (
                incident_ops_has_scope('actions:request')
                AND requested_by_type = incident_ops_actor_type()
                AND requested_by_id = incident_ops_actor_id()
            )
        """,
        """
        CREATE POLICY actions_update ON action_requests FOR UPDATE
            USING (
                incident_ops_has_scope('actions:approve')
                OR incident_ops_has_scope('actions:execute')
            )
            WITH CHECK (
                incident_ops_has_scope('actions:approve')
                OR incident_ops_has_scope('actions:execute')
            )
        """,
        """
        CREATE POLICY events_select ON incident_events FOR SELECT
            USING (incident_ops_has_scope('incidents:read'))
        """,
        """
        CREATE POLICY events_insert ON incident_events FOR INSERT
            WITH CHECK (
                actor_type = incident_ops_actor_type()
                AND actor_id = incident_ops_actor_id()
                AND (
                    incident_ops_has_scope('incidents:write')
                    OR incident_ops_has_scope('actions:request')
                    OR incident_ops_has_scope('actions:approve')
                    OR incident_ops_has_scope('actions:execute')
                )
            )
        """,
        """
        CREATE POLICY jobs_select ON demo_jobs FOR SELECT
            USING (
                incident_ops_has_scope('incidents:read')
                OR incident_ops_has_scope('actions:request')
            )
        """,
        """
        CREATE POLICY jobs_insert ON demo_jobs FOR INSERT
            WITH CHECK (
                incident_ops_has_scope('actions:execute')
                OR incident_ops_has_scope('fixtures:write')
            )
        """,
        """
        CREATE POLICY jobs_update ON demo_jobs FOR UPDATE
            USING (incident_ops_has_scope('actions:execute'))
            WITH CHECK (incident_ops_has_scope('actions:execute'))
        """,
        """
        CREATE POLICY historical_select ON historical_incidents FOR SELECT
            USING (incident_ops_has_scope('retrieval:read'))
        """,
        """
        CREATE POLICY historical_insert ON historical_incidents FOR INSERT
            WITH CHECK (incident_ops_has_scope('retrieval:write'))
        """,
        """
        CREATE POLICY historical_update ON historical_incidents FOR UPDATE
            USING (incident_ops_has_scope('retrieval:write'))
            WITH CHECK (incident_ops_has_scope('retrieval:write'))
        """,
        """
        CREATE POLICY outbox_select ON outbox_events FOR SELECT
            USING (incident_ops_has_scope('outbox:deliver'))
        """,
        """
        CREATE POLICY outbox_insert ON outbox_events FOR INSERT
            WITH CHECK (
                incident_ops_has_scope('incidents:write')
                OR incident_ops_has_scope('actions:request')
                OR incident_ops_has_scope('actions:approve')
                OR incident_ops_has_scope('actions:execute')
            )
        """,
        """
        CREATE POLICY outbox_update ON outbox_events FOR UPDATE
            USING (incident_ops_has_scope('outbox:deliver'))
            WITH CHECK (incident_ops_has_scope('outbox:deliver'))
        """,
        """
        CREATE POLICY idempotency_select ON idempotency_records FOR SELECT
            USING (
                principal_type = incident_ops_actor_type()
                AND principal_id = incident_ops_actor_id()
            )
        """,
        """
        CREATE POLICY idempotency_insert ON idempotency_records FOR INSERT
            WITH CHECK (
                principal_type = incident_ops_actor_type()
                AND principal_id = incident_ops_actor_id()
            )
        """,
        """
        CREATE POLICY idempotency_update ON idempotency_records FOR UPDATE
            USING (
                principal_type = incident_ops_actor_type()
                AND principal_id = incident_ops_actor_id()
            )
            WITH CHECK (
                principal_type = incident_ops_actor_type()
                AND principal_id = incident_ops_actor_id()
            )
        """,
    )
    for policy in policies:
        op.execute(policy)


def downgrade() -> None:
    op.drop_table("idempotency_records")
    op.drop_table("outbox_events")
    op.drop_table("historical_incidents")
    op.drop_table("demo_jobs")
    op.execute("DROP TRIGGER IF EXISTS incident_events_immutable ON incident_events")
    op.execute("DROP FUNCTION IF EXISTS reject_incident_event_mutation()")
    op.drop_table("incident_events")
    op.drop_table("action_requests")
    op.drop_table("incidents")
    op.execute("DROP FUNCTION IF EXISTS incident_ops_has_scope(text)")
    op.execute("DROP FUNCTION IF EXISTS incident_ops_actor_id()")
    op.execute("DROP FUNCTION IF EXISTS incident_ops_actor_type()")
