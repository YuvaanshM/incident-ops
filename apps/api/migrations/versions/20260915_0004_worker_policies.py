"""Least-privilege system-worker access to outbox facts and delivery audit events.

Revision ID: 20260915_0004
Revises: 20260908_0003
"""

from alembic import op

revision: str = "20260915_0004"
down_revision: str | None = "20260908_0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # A non-inlined invoker function avoids recursively expanding this INSERT
    # policy while checking the immutable source under its ordinary SELECT RLS.
    op.execute("""
        CREATE FUNCTION incident_ops_delivery_source_matches(
            source_id uuid, target_incident uuid, target_action uuid, target_request uuid
        ) RETURNS boolean LANGUAGE plpgsql STABLE
        SET search_path = pg_catalog, public
        AS $$ BEGIN
            RETURN EXISTS (
                SELECT 1 FROM public.incident_events source
                WHERE source.id = source_id AND source.incident_id = target_incident
                  AND source.action_request_id IS NOT DISTINCT FROM target_action
                  AND source.request_id = target_request
            );
        END $$
    """)
    op.execute("""
        ALTER POLICY outbox_select ON outbox_events USING (
            incident_ops_actor_type() = 'system' AND incident_ops_has_scope('outbox:deliver')
        )
    """)
    op.execute("""
        ALTER POLICY outbox_update ON outbox_events USING (
            incident_ops_actor_type() = 'system' AND incident_ops_has_scope('outbox:deliver')
        ) WITH CHECK (
            incident_ops_actor_type() = 'system' AND incident_ops_has_scope('outbox:deliver')
        )
    """)
    op.execute("""
        CREATE POLICY worker_source_events_select ON incident_events FOR SELECT USING (
            incident_ops_actor_type() = 'system'
            AND incident_ops_has_scope('outbox:deliver')
            AND EXISTS (SELECT 1 FROM outbox_events o WHERE o.source_event_id = incident_events.id)
        )
    """)
    op.execute("""
        CREATE POLICY worker_delivery_events_insert ON incident_events FOR INSERT WITH CHECK (
            incident_ops_actor_type() = 'system'
            AND incident_ops_has_scope('outbox:deliver')
            AND actor_type = 'system' AND actor_id = incident_ops_actor_id()
            AND event_type IN ('slack.delivered', 'slack.delivery_failed')
            AND EXISTS (
                SELECT 1 FROM outbox_events o
                WHERE o.id::text = incident_events.payload->>'outbox_event_id'
                  AND o.source_event_id::text = incident_events.payload->>'source_event_id'
                  AND incident_ops_delivery_source_matches(
                      o.source_event_id, incident_events.incident_id,
                      incident_events.action_request_id, incident_events.request_id
                  )
                  AND ((o.status = 'delivered' AND incident_events.event_type = 'slack.delivered')
                    OR (o.status = 'dead_letter' AND incident_events.event_type = 'slack.delivery_failed'))
            )
        )
    """)


def downgrade() -> None:
    op.execute("DROP POLICY worker_delivery_events_insert ON incident_events")
    op.execute("DROP POLICY worker_source_events_select ON incident_events")
    op.execute("DROP FUNCTION IF EXISTS incident_ops_delivery_source_matches(uuid, uuid, uuid, uuid)")
    op.execute(
        "ALTER POLICY outbox_select ON outbox_events USING (incident_ops_has_scope('outbox:deliver'))"
    )
    op.execute("""
        ALTER POLICY outbox_update ON outbox_events
        USING (incident_ops_has_scope('outbox:deliver'))
        WITH CHECK (incident_ops_has_scope('outbox:deliver'))
    """)
