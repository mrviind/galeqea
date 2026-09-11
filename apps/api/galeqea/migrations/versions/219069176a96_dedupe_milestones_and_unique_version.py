"""dedupe milestones and unique version

Revision ID: 219069176a96
Revises: 63d03d307909
Create Date: 2026-08-29 12:21:46.104899

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import galeqea.models.base


# revision identifiers, used by Alembic.
revision: str = '219069176a96'
down_revision: Union[str, Sequence[str], None] = '63d03d307909'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Archive older duplicate milestones (keep the newest per project+version), then
    add a partial unique index so a version can have only one live milestone."""
    conn = op.get_bind()
    # Keep the newest non-archived milestone per (project_id, version); archive the rest.
    conn.exec_driver_sql(
        """
        UPDATE milestones SET status = 'archived'
        WHERE id IN (
            SELECT m.id FROM milestones m
            JOIN (
                SELECT project_id, version, MAX(created_at) AS newest
                FROM milestones WHERE status != 'archived'
                GROUP BY project_id, version
            ) k
            ON m.project_id = k.project_id AND m.version = k.version
            WHERE m.status != 'archived' AND m.created_at < k.newest
        )
        """
    )
    op.create_index(
        "uq_milestone_project_version", "milestones", ["project_id", "version"],
        unique=True,
        sqlite_where=sa.text("status != 'archived'"),
        postgresql_where=sa.text("status != 'archived'"),
    )


def downgrade() -> None:
    op.drop_index("uq_milestone_project_version", table_name="milestones")
