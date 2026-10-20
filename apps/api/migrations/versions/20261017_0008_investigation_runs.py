"""Principal-owned optional investigation receipts and bounded results.

Revision ID: 20261017_0008
Revises: 20261012_0007
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261017_0008"
down_revision = "20261012_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("investigation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("principal_id", sa.String(255), nullable=False),
        sa.Column("request_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("incident_version", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("outcome", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("principal_id", "request_key", name="uq_investigation_principal_key"),
        sa.CheckConstraint("char_length(principal_id) BETWEEN 1 AND 255", name="ck_investigation_owner"),
        sa.CheckConstraint("version IN (1, 2) AND incident_version >= 1", name="ck_investigation_versions"),
        sa.CheckConstraint("deadline > created_at", name="ck_investigation_deadline"),
        sa.CheckConstraint("jsonb_typeof(outcome) = 'object'", name="ck_investigation_outcome"),
    )
    op.create_index("ix_investigation_owner_deadline", "investigation_runs", ["principal_id", "deadline"])
    op.execute("ALTER TABLE investigation_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE investigation_runs FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY investigation_select ON investigation_runs FOR SELECT USING (
        incident_ops_actor_type() = 'agent' AND principal_id = incident_ops_actor_id()
        AND incident_ops_has_scope('incidents:read') AND incident_ops_has_scope('actions:request'))""")
    op.execute("""CREATE POLICY investigation_insert ON investigation_runs FOR INSERT WITH CHECK (
        incident_ops_actor_type() = 'agent' AND principal_id = incident_ops_actor_id()
        AND incident_ops_has_scope('incidents:read') AND incident_ops_has_scope('actions:request')
        AND incident_ops_has_scope('investigations:write'))""")
    op.execute("""CREATE POLICY investigation_update ON investigation_runs FOR UPDATE USING (
        incident_ops_actor_type() = 'agent' AND principal_id = incident_ops_actor_id()
        AND incident_ops_has_scope('incidents:read') AND incident_ops_has_scope('actions:request')
        AND incident_ops_has_scope('investigations:write')) WITH CHECK (
        incident_ops_actor_type() = 'agent' AND principal_id = incident_ops_actor_id()
        AND incident_ops_has_scope('incidents:read') AND incident_ops_has_scope('actions:request')
        AND incident_ops_has_scope('investigations:write'))""")


def downgrade() -> None:
    op.drop_table("investigation_runs")
