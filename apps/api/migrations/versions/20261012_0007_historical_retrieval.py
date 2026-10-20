"""Versioned synthetic history with atomic, append-only ingestion audit.

Revision ID: 20261012_0007
Revises: 20260925_0006
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261012_0007"
down_revision = "20260925_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("historical_incidents", sa.Column("version", sa.BigInteger(), nullable=False, server_default=sa.text("1")))
    op.add_column("historical_incidents", sa.Column("content_hash", sa.String(64)))
    op.add_column("historical_incidents", sa.Column("provenance", sa.String(30), nullable=False, server_default=sa.text("'unreviewed'")))
    op.add_column("historical_incidents", sa.Column("embedding_template", sa.String(100), nullable=False, server_default=sa.text("'legacy'")))
    op.create_check_constraint("ck_historical_version", "historical_incidents", "version >= 1")
    op.create_check_constraint("ck_historical_hash", "historical_incidents", "content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'")
    op.create_check_constraint("ck_historical_provenance", "historical_incidents", "provenance IN ('unreviewed', 'seeded_demo_data')")
    op.create_check_constraint("ck_historical_template", "historical_incidents", "char_length(embedding_template) BETWEEN 1 AND 100")
    op.create_table("historical_ingestion_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("historical_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("historical_incidents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_type", sa.String(20), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("embedding_provider", sa.String(100), nullable=False),
        sa.Column("embedding_model", sa.String(200), nullable=False),
        sa.Column("embedding_template", sa.String(100), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("version >= 1", name="ck_historical_events_version"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_historical_events_hash"),
        sa.CheckConstraint("actor_type = 'system' AND actor_id = 'historical-fixture-loader'", name="ck_historical_events_actor"),
    )
    op.execute("ALTER TABLE historical_ingestion_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE historical_ingestion_events FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY historical_events_select ON historical_ingestion_events FOR SELECT USING (incident_ops_has_scope('retrieval:read'))")
    op.execute("""CREATE POLICY historical_events_insert ON historical_ingestion_events FOR INSERT WITH CHECK (
        incident_ops_actor_type() = 'system' AND incident_ops_actor_id() = 'historical-fixture-loader'
        AND actor_type = incident_ops_actor_type() AND actor_id = incident_ops_actor_id()
        AND incident_ops_has_scope('retrieval:write'))""")
    op.execute("""CREATE TRIGGER historical_events_immutable BEFORE UPDATE OR DELETE ON historical_ingestion_events
        FOR EACH ROW EXECUTE FUNCTION reject_incident_event_mutation()""")
    op.execute("DROP POLICY historical_insert ON historical_incidents")
    op.execute("DROP POLICY historical_update ON historical_incidents")
    op.execute("""CREATE POLICY historical_insert ON historical_incidents FOR INSERT WITH CHECK (
        incident_ops_actor_type() = 'system' AND incident_ops_actor_id() = 'historical-fixture-loader'
        AND incident_ops_has_scope('retrieval:write'))""")
    op.execute("""CREATE POLICY historical_update ON historical_incidents FOR UPDATE USING (
        incident_ops_actor_type() = 'system' AND incident_ops_actor_id() = 'historical-fixture-loader'
        AND incident_ops_has_scope('retrieval:write')) WITH CHECK (
        incident_ops_actor_type() = 'system' AND incident_ops_actor_id() = 'historical-fixture-loader'
        AND incident_ops_has_scope('retrieval:write'))""")
    op.execute("""CREATE POLICY historical_idempotency_select ON idempotency_records FOR SELECT USING (
        principal_type = 'system' AND principal_id = 'historical-fixture-loader'
        AND principal_type = incident_ops_actor_type() AND principal_id = incident_ops_actor_id()
        AND operation = 'historical.ingest' AND incident_ops_has_scope('retrieval:write'))""")
    op.execute("""CREATE POLICY historical_idempotency_insert ON idempotency_records FOR INSERT WITH CHECK (
        principal_type = 'system' AND principal_id = 'historical-fixture-loader'
        AND principal_type = incident_ops_actor_type() AND principal_id = incident_ops_actor_id()
        AND operation = 'historical.ingest' AND incident_ops_has_scope('retrieval:write'))""")
    op.execute("""CREATE POLICY historical_idempotency_update ON idempotency_records FOR UPDATE USING (
        principal_type = 'system' AND principal_id = 'historical-fixture-loader'
        AND principal_type = incident_ops_actor_type() AND principal_id = incident_ops_actor_id()
        AND operation = 'historical.ingest' AND incident_ops_has_scope('retrieval:write')) WITH CHECK (
        principal_type = 'system' AND principal_id = 'historical-fixture-loader'
        AND principal_type = incident_ops_actor_type() AND principal_id = incident_ops_actor_id()
        AND operation = 'historical.ingest' AND incident_ops_has_scope('retrieval:write'))""")


def downgrade() -> None:
    op.execute("DROP POLICY historical_idempotency_update ON idempotency_records")
    op.execute("DROP POLICY historical_idempotency_insert ON idempotency_records")
    op.execute("DROP POLICY historical_idempotency_select ON idempotency_records")
    op.execute("DROP POLICY historical_update ON historical_incidents")
    op.execute("DROP POLICY historical_insert ON historical_incidents")
    op.execute("CREATE POLICY historical_insert ON historical_incidents FOR INSERT WITH CHECK (incident_ops_has_scope('retrieval:write'))")
    op.execute("CREATE POLICY historical_update ON historical_incidents FOR UPDATE USING (incident_ops_has_scope('retrieval:write')) WITH CHECK (incident_ops_has_scope('retrieval:write'))")
    op.drop_table("historical_ingestion_events")
    op.drop_constraint("ck_historical_template", "historical_incidents")
    op.drop_constraint("ck_historical_provenance", "historical_incidents")
    op.drop_constraint("ck_historical_hash", "historical_incidents")
    op.drop_constraint("ck_historical_version", "historical_incidents")
    op.drop_column("historical_incidents", "embedding_template")
    op.drop_column("historical_incidents", "provenance")
    op.drop_column("historical_incidents", "content_hash")
    op.drop_column("historical_incidents", "version")
