"""Allow incident readers to inspect action status.

Revision ID: 20260908_0003
Revises: 20260905_0002
Create Date: 2026-09-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260908_0003"
down_revision: str | None = "20260905_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP POLICY actions_select ON action_requests")
    op.execute(
        """
        CREATE POLICY actions_select ON action_requests FOR SELECT
            USING (
                incident_ops_has_scope('incidents:read')
                OR incident_ops_has_scope('actions:read')
                OR incident_ops_has_scope('actions:approve')
                OR (
                    incident_ops_has_scope('actions:request')
                    AND requested_by_type = incident_ops_actor_type()
                    AND requested_by_id = incident_ops_actor_id()
                )
            )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY actions_select ON action_requests")
    op.execute(
        """
        CREATE POLICY actions_select ON action_requests FOR SELECT
            USING (
                incident_ops_has_scope('actions:read')
                OR incident_ops_has_scope('actions:approve')
                OR (
                    incident_ops_has_scope('actions:request')
                    AND requested_by_type = incident_ops_actor_type()
                    AND requested_by_id = incident_ops_actor_id()
                )
            )
        """
    )
