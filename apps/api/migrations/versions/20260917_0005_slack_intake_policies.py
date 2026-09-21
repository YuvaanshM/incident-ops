"""Source-bound Slack creation without incident update or action authority.

Revision ID: 20260917_0005
Revises: 20260915_0004
"""

from alembic import op

revision = "20260917_0005"
down_revision = "20260915_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION incident_ops_slack_context() RETURNS boolean
        LANGUAGE sql STABLE AS $$
            SELECT incident_ops_actor_type() = 'system'
                AND incident_ops_has_scope('slack:ingest')
                AND incident_ops_actor_id() ~ '^slack:T[A-Z0-9]{2,31}:[CG][A-Z0-9]{2,31}$'
                AND coalesce(current_setting('app.slack_service', true), '') <> ''
                AND current_setting('app.slack_severity', true) IN ('low','medium','high','critical')
        $$
    """)
    op.execute("""
        CREATE POLICY slack_incidents_select ON incidents FOR SELECT USING (
            incident_ops_slack_context() AND created_by_type = 'system'
            AND created_by_id = incident_ops_actor_id()
        )
    """)
    op.execute("""
        CREATE POLICY slack_incidents_insert ON incidents FOR INSERT WITH CHECK (
            incident_ops_slack_context() AND created_by_type = 'system'
            AND created_by_id = incident_ops_actor_id()
            AND service = current_setting('app.slack_service', true)
            AND severity = current_setting('app.slack_severity', true)
            AND status = 'open' AND version = 1 AND resolved_at IS NULL AND closed_at IS NULL
        )
    """)
    op.execute("""
        CREATE POLICY slack_events_select ON incident_events FOR SELECT USING (
            incident_ops_slack_context() AND actor_type = 'system'
            AND actor_id = incident_ops_actor_id()
        )
    """)
    op.execute("""
        CREATE POLICY slack_events_insert ON incident_events FOR INSERT WITH CHECK (
            incident_ops_slack_context() AND actor_type = 'system'
            AND actor_id = incident_ops_actor_id() AND event_type = 'incident.created'
            AND action_request_id IS NULL AND request_id IS NOT NULL
            AND EXISTS (SELECT 1 FROM incidents i WHERE i.id = incident_id
                AND i.created_by_type = 'system' AND i.created_by_id = incident_ops_actor_id())
        )
    """)
    # Invoker function prevents policy expansion recursion with worker policies.
    op.execute("""
        CREATE FUNCTION incident_ops_slack_creation_source(source_id uuid, target_id uuid)
        RETURNS boolean LANGUAGE plpgsql STABLE SET search_path = pg_catalog, public
        AS $$ BEGIN RETURN EXISTS (
            SELECT 1 FROM public.incident_events e WHERE e.id = source_id
                AND e.incident_id = target_id AND e.event_type = 'incident.created'
                AND e.actor_type = 'system' AND e.actor_id = public.incident_ops_actor_id()
        ); END $$
    """)
    op.execute("""
        CREATE POLICY slack_outbox_insert ON outbox_events FOR INSERT WITH CHECK (
            incident_ops_slack_context() AND event_type = 'slack.incident.created'
            AND aggregate_type = 'incident' AND status = 'pending' AND attempt_count = 0
            AND dedupe_key = 'slack.incident.created:' || source_event_id::text
            AND payload->>'incident_id' = aggregate_id::text
            AND payload->>'event_id' = source_event_id::text
            AND incident_ops_slack_creation_source(source_event_id, aggregate_id)
        )
    """)
    op.execute("""
        CREATE POLICY slack_outbox_select ON outbox_events FOR SELECT USING (
            incident_ops_slack_context() AND event_type = 'slack.incident.created'
            AND EXISTS (SELECT 1 FROM incidents i WHERE i.id = aggregate_id
                AND i.created_by_type = 'system' AND i.created_by_id = incident_ops_actor_id())
        )
    """)
    op.execute("""
        CREATE POLICY slack_idempotency_select ON idempotency_records FOR SELECT USING (
            incident_ops_slack_context() AND principal_type = 'system'
            AND principal_id = incident_ops_actor_id()
            AND operation IN ('slack.delivery', 'slack.message')
        )
    """)
    op.execute("""
        CREATE POLICY slack_idempotency_insert ON idempotency_records FOR INSERT WITH CHECK (
            incident_ops_slack_context() AND principal_type = 'system'
            AND principal_id = incident_ops_actor_id()
            AND operation IN ('slack.delivery', 'slack.message')
        )
    """)
    op.execute("""
        CREATE POLICY slack_idempotency_update ON idempotency_records FOR UPDATE USING (
            incident_ops_slack_context() AND principal_type = 'system'
            AND principal_id = incident_ops_actor_id()
            AND operation IN ('slack.delivery', 'slack.message') AND completed_at IS NULL
        ) WITH CHECK (
            incident_ops_slack_context() AND principal_type = 'system'
            AND principal_id = incident_ops_actor_id()
            AND operation IN ('slack.delivery', 'slack.message')
        )
    """)


def downgrade() -> None:
    op.execute("DROP POLICY slack_idempotency_update ON idempotency_records")
    op.execute("DROP POLICY slack_idempotency_insert ON idempotency_records")
    op.execute("DROP POLICY slack_idempotency_select ON idempotency_records")
    op.execute("DROP POLICY slack_outbox_select ON outbox_events")
    op.execute("DROP POLICY slack_outbox_insert ON outbox_events")
    op.execute("DROP FUNCTION incident_ops_slack_creation_source(uuid, uuid)")
    op.execute("DROP POLICY slack_events_insert ON incident_events")
    op.execute("DROP POLICY slack_events_select ON incident_events")
    op.execute("DROP POLICY slack_incidents_insert ON incidents")
    op.execute("DROP POLICY slack_incidents_select ON incidents")
    op.execute("DROP FUNCTION incident_ops_slack_context()")
