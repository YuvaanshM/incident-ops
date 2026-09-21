"""Persist source-bound monitoring observations and narrow RLS mutations.

Revision ID: 20260925_0006
Revises: 20260917_0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260925_0006"
down_revision = "20260917_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("monitoring_status", sa.String(20)))
    op.add_column("incidents", sa.Column("monitoring_updated_at", sa.DateTime(timezone=True)))
    # Keep the additive check from scanning incident rows under an ACCESS
    # EXCLUSIVE lock. Validation uses PostgreSQL's lighter validation lock.
    op.execute("""
        ALTER TABLE incidents ADD CONSTRAINT ck_incidents_monitoring_status
        CHECK (monitoring_status IS NULL OR monitoring_status IN ('FIRING','RESOLVED')) NOT VALID
    """)
    op.execute("ALTER TABLE incidents VALIDATE CONSTRAINT ck_incidents_monitoring_status")
    op.create_table(
        "alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("external_alert_id", sa.String(200), nullable=False),
        sa.Column("service", sa.String(100), nullable=False),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("incidents.id", ondelete="RESTRICT")),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("event_version", sa.BigInteger(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("state_fingerprint", sa.String(64), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("first_received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("char_length(btrim(source)) BETWEEN 1 AND 100", name="ck_alerts_source"),
        sa.CheckConstraint("char_length(btrim(external_alert_id)) BETWEEN 1 AND 200", name="ck_alerts_external_id"),
        sa.CheckConstraint("char_length(btrim(service)) BETWEEN 1 AND 100", name="ck_alerts_service"),
        sa.CheckConstraint("char_length(btrim(title)) BETWEEN 1 AND 200", name="ck_alerts_title"),
        sa.CheckConstraint("char_length(btrim(description)) BETWEEN 1 AND 5000", name="ck_alerts_description"),
        sa.CheckConstraint("severity IN ('low','medium','high','critical')", name="ck_alerts_severity"),
        sa.CheckConstraint("status IN ('FIRING','RESOLVED')", name="ck_alerts_status"),
        sa.CheckConstraint("event_version >= 1 AND version >= 1", name="ck_alerts_versions"),
        sa.CheckConstraint("jsonb_typeof(metadata) = 'object'", name="ck_alerts_metadata"),
        sa.CheckConstraint("state_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_alerts_fingerprint"),
        sa.CheckConstraint("(status = 'FIRING' AND resolved_at IS NULL) OR (status = 'RESOLVED' AND resolved_at >= started_at)", name="ck_alerts_lifecycle"),
        sa.UniqueConstraint("source", "external_alert_id", "service", name="uq_alerts_identity"),
    )
    op.create_index("ix_alerts_incident", "alerts", ["incident_id"])
    op.create_index("ix_alerts_source_status_received", "alerts", ["source", "status", sa.text("last_received_at DESC")])
    op.create_table(
        "alert_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("alert_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("alerts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("incidents.id", ondelete="RESTRICT")),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_version", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("state_fingerprint", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(40), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("char_length(btrim(source)) BETWEEN 1 AND 100", name="ck_alert_events_source"),
        sa.CheckConstraint("char_length(btrim(actor_id)) BETWEEN 1 AND 255", name="ck_alert_events_actor"),
        sa.CheckConstraint("event_version >= 1", name="ck_alert_events_version"),
        sa.CheckConstraint("status IN ('FIRING','RESOLVED')", name="ck_alert_events_status"),
        sa.CheckConstraint("severity IN ('low','medium','high','critical')", name="ck_alert_events_severity"),
        sa.CheckConstraint("outcome IN ('incident_created','incident_updated','resolution_recorded','unchanged','stale_ignored','orphan_resolution','suppressed_terminal')", name="ck_alert_events_outcome"),
        sa.CheckConstraint("jsonb_typeof(metadata) = 'object'", name="ck_alert_events_metadata"),
        sa.CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$' AND state_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_alert_events_fingerprints"),
        sa.UniqueConstraint("source", "idempotency_key", name="uq_alert_events_delivery"),
    )
    op.create_index("ix_alert_events_alert_received", "alert_events", ["alert_id", sa.text("received_at DESC"), sa.text("id DESC")])
    op.create_index("ix_alert_events_incident_received", "alert_events", ["incident_id", sa.text("received_at DESC"), sa.text("id DESC")])
    op.create_index("ix_alert_events_occurrence", "alert_events", ["alert_id", "started_at"])
    for table in ("alerts", "alert_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    _create_source_policies()


def _create_source_policies() -> None:
    op.execute("""
        CREATE FUNCTION incident_ops_monitor_context() RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT incident_ops_actor_type() = 'system'
                AND incident_ops_has_scope('alerts:ingest')
                AND incident_ops_actor_id() = 'monitoring:' || current_setting('app.monitor_source', true)
                AND coalesce(current_setting('app.monitor_service', true), '') <> ''
        $$
    """)
    op.execute("""
        CREATE POLICY monitor_alerts_select ON alerts FOR SELECT USING (
            (incident_ops_monitor_context() AND source = current_setting('app.monitor_source', true)
                AND service = current_setting('app.monitor_service', true))
            OR (incident_ops_has_scope('incidents:read') AND incident_id IS NOT NULL)
        )
    """)
    op.execute("""
        CREATE POLICY monitor_alerts_insert ON alerts FOR INSERT WITH CHECK (
            incident_ops_monitor_context() AND source = current_setting('app.monitor_source', true)
            AND service = current_setting('app.monitor_service', true)
            AND (incident_id IS NULL OR EXISTS (
                SELECT 1 FROM incidents i WHERE i.id = incident_id
                AND i.created_by_type = 'system' AND i.created_by_id = incident_ops_actor_id()
                AND i.service = current_setting('app.monitor_service', true)
            ))
        )
    """)
    op.execute("""
        CREATE POLICY monitor_alerts_update ON alerts FOR UPDATE USING (
            incident_ops_monitor_context() AND source = current_setting('app.monitor_source', true)
            AND service = current_setting('app.monitor_service', true)
        ) WITH CHECK (
            incident_ops_monitor_context() AND source = current_setting('app.monitor_source', true)
            AND service = current_setting('app.monitor_service', true)
            AND (incident_id IS NULL OR EXISTS (
                SELECT 1 FROM incidents i WHERE i.id = incident_id
                AND i.created_by_type = 'system' AND i.created_by_id = incident_ops_actor_id()
                AND i.service = current_setting('app.monitor_service', true)
            ))
        )
    """)
    op.execute("""
        CREATE FUNCTION reject_monitor_alert_identity() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.source IS DISTINCT FROM OLD.source
                OR NEW.external_alert_id IS DISTINCT FROM OLD.external_alert_id
                OR NEW.service IS DISTINCT FROM OLD.service
                OR NEW.first_received_at IS DISTINCT FROM OLD.first_received_at
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
                OR NEW.version <> OLD.version + 1
            THEN RAISE EXCEPTION 'alert identity is immutable';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("CREATE TRIGGER alert_identity_immutable BEFORE UPDATE ON alerts FOR EACH ROW EXECUTE FUNCTION reject_monitor_alert_identity()")
    op.execute("""
        CREATE POLICY monitor_alert_events_select ON alert_events FOR SELECT USING (
            (incident_ops_monitor_context() AND source = current_setting('app.monitor_source', true))
            OR (incident_ops_has_scope('incidents:read') AND incident_id IS NOT NULL)
        )
    """)
    op.execute("""
        CREATE POLICY monitor_alert_events_insert ON alert_events FOR INSERT WITH CHECK (
            incident_ops_monitor_context() AND source = current_setting('app.monitor_source', true)
            AND actor_id = incident_ops_actor_id()
            AND EXISTS (SELECT 1 FROM alerts a WHERE a.id = alert_id
                AND a.source = source AND a.service = current_setting('app.monitor_service', true))
            AND (incident_id IS NULL OR EXISTS (
                SELECT 1 FROM incidents i WHERE i.id = incident_id
                AND i.created_by_type = 'system' AND i.created_by_id = incident_ops_actor_id()
                AND i.service = current_setting('app.monitor_service', true)
            ))
        )
    """)
    op.execute("""
        CREATE POLICY monitor_incidents_select ON incidents FOR SELECT USING (
            incident_ops_monitor_context() AND created_by_type = 'system'
            AND created_by_id = incident_ops_actor_id()
            AND service = current_setting('app.monitor_service', true)
        )
    """)
    op.execute("""
        CREATE POLICY monitor_incidents_insert ON incidents FOR INSERT WITH CHECK (
            incident_ops_monitor_context() AND created_by_type = 'system'
            AND created_by_id = incident_ops_actor_id()
            AND service = current_setting('app.monitor_service', true)
            AND severity = current_setting('app.monitor_severity', true)
            AND status = 'open' AND version = 1 AND monitoring_status = 'FIRING'
        )
    """)
    op.execute("""
        CREATE POLICY monitor_incidents_update ON incidents FOR UPDATE USING (
            incident_ops_monitor_context() AND created_by_type = 'system'
            AND created_by_id = incident_ops_actor_id()
            AND service = current_setting('app.monitor_service', true)
        ) WITH CHECK (
            incident_ops_monitor_context() AND created_by_type = 'system'
            AND created_by_id = incident_ops_actor_id()
            AND service = current_setting('app.monitor_service', true)
        )
    """)
    op.execute("""
        CREATE FUNCTION reject_monitor_incident_fields() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF public.incident_ops_monitor_context() AND (
                NEW.title IS DISTINCT FROM OLD.title OR NEW.description IS DISTINCT FROM OLD.description
                OR NEW.service IS DISTINCT FROM OLD.service OR NEW.severity IS DISTINCT FROM OLD.severity
                OR NEW.status IS DISTINCT FROM OLD.status OR NEW.resolved_at IS DISTINCT FROM OLD.resolved_at
                OR NEW.closed_at IS DISTINCT FROM OLD.closed_at OR NEW.created_by_type IS DISTINCT FROM OLD.created_by_type
                OR NEW.created_by_id IS DISTINCT FROM OLD.created_by_id OR NEW.version <> OLD.version + 1
            ) THEN
                RAISE EXCEPTION 'monitoring source may update monitoring context only';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("CREATE TRIGGER monitor_incident_fields BEFORE UPDATE ON incidents FOR EACH ROW EXECUTE FUNCTION reject_monitor_incident_fields()")
    op.execute("""
        CREATE POLICY monitor_incident_events_select ON incident_events FOR SELECT USING (
            incident_ops_monitor_context() AND actor_type = 'system' AND actor_id = incident_ops_actor_id()
        )
    """)
    op.execute("""
        CREATE POLICY monitor_incident_events_insert ON incident_events FOR INSERT WITH CHECK (
            incident_ops_monitor_context() AND actor_type = 'system' AND actor_id = incident_ops_actor_id()
            AND event_type IN ('incident.created','alert.linked','alert.updated','alert.resolved','alert.refired','alert.severity_review_required')
            AND action_request_id IS NULL
            AND EXISTS (SELECT 1 FROM incidents i WHERE i.id = incident_id
                AND i.created_by_type = 'system' AND i.created_by_id = incident_ops_actor_id()
                AND i.service = current_setting('app.monitor_service', true))
        )
    """)
    op.execute("""
        CREATE FUNCTION incident_ops_monitor_outbox_source(source_id uuid, target_id uuid, kind text)
        RETURNS boolean LANGUAGE plpgsql STABLE SET search_path = pg_catalog, public AS $$
        BEGIN RETURN EXISTS (
            SELECT 1 FROM public.incident_events e WHERE e.id = source_id
                AND e.incident_id = target_id AND e.actor_type = 'system'
                AND e.actor_id = public.incident_ops_actor_id()
                AND ((kind = 'slack.incident.created' AND e.event_type = 'incident.created')
                  OR (kind = 'slack.alert.severity_review_required'
                      AND e.event_type = 'alert.severity_review_required'))
        ); END $$
    """)
    op.execute("""
        CREATE POLICY monitor_outbox_insert ON outbox_events FOR INSERT WITH CHECK (
            incident_ops_monitor_context() AND event_type IN ('slack.incident.created','slack.alert.severity_review_required')
            AND aggregate_type = 'incident' AND status = 'pending' AND attempt_count = 0
            AND payload->>'incident_id' = aggregate_id::text
            AND payload->>'event_id' = source_event_id::text
            AND incident_ops_monitor_outbox_source(source_event_id, aggregate_id, event_type)
            AND EXISTS (SELECT 1 FROM incidents i WHERE i.id = aggregate_id
                AND i.created_by_type = 'system' AND i.created_by_id = incident_ops_actor_id()
                AND i.service = current_setting('app.monitor_service', true))
        )
    """)
    for operation in ("SELECT", "INSERT", "UPDATE"):
        suffix = operation.lower()
        condition = "incident_ops_monitor_context() AND principal_type = 'system' AND principal_id = incident_ops_actor_id() AND operation = 'alerts.ingest'"
        if operation == "SELECT":
            op.execute(f"CREATE POLICY monitor_idempotency_{suffix} ON idempotency_records FOR {operation} USING ({condition})")
        elif operation == "INSERT":
            op.execute(f"CREATE POLICY monitor_idempotency_{suffix} ON idempotency_records FOR {operation} WITH CHECK ({condition})")
        else:
            op.execute(f"CREATE POLICY monitor_idempotency_{suffix} ON idempotency_records FOR {operation} USING ({condition}) WITH CHECK ({condition})")
    op.execute("""
        CREATE FUNCTION reject_alert_event_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'alert events are immutable'; END $$
    """)
    op.execute("CREATE TRIGGER alert_events_immutable BEFORE UPDATE OR DELETE ON alert_events FOR EACH ROW EXECUTE FUNCTION reject_alert_event_mutation()")


def downgrade() -> None:
    op.drop_table("alert_events")
    op.execute("DROP TRIGGER IF EXISTS alert_identity_immutable ON alerts")
    op.execute("DROP FUNCTION IF EXISTS reject_monitor_alert_identity()")
    op.drop_table("alerts")
    op.execute("DROP FUNCTION reject_alert_event_mutation()")
    for suffix in ("select", "insert", "update"):
        op.execute(f"DROP POLICY monitor_idempotency_{suffix} ON idempotency_records")
    op.execute("DROP POLICY monitor_outbox_insert ON outbox_events")
    op.execute("DROP FUNCTION IF EXISTS incident_ops_monitor_outbox_source(uuid, uuid, text)")
    op.execute("DROP POLICY monitor_incident_events_insert ON incident_events")
    op.execute("DROP POLICY monitor_incident_events_select ON incident_events")
    op.execute("DROP TRIGGER monitor_incident_fields ON incidents")
    op.execute("DROP FUNCTION reject_monitor_incident_fields()")
    op.execute("DROP POLICY monitor_incidents_update ON incidents")
    op.execute("DROP POLICY monitor_incidents_insert ON incidents")
    op.execute("DROP POLICY monitor_incidents_select ON incidents")
    op.execute("DROP FUNCTION incident_ops_monitor_context()")
    op.drop_constraint("ck_incidents_monitoring_status", "incidents", type_="check")
    op.drop_column("incidents", "monitoring_updated_at")
    op.drop_column("incidents", "monitoring_status")
