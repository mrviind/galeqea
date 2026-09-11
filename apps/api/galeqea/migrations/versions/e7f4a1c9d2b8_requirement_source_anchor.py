"""requirement source_anchor (WO#9-A)

Adds a per-item source anchor {heading_path, page, line, char_start, doc_id} so a
generated test can point a reviewer back to exactly where a rule was written.

Revision ID: e7f4a1c9d2b8
Revises: 3c3acf14532a
Create Date: 2026-08-29 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import galeqea.models.base


# revision identifiers, used by Alembic.
revision: str = 'e7f4a1c9d2b8'
down_revision: Union[str, Sequence[str], None] = '3c3acf14532a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('requirement_items', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('source_anchor', galeqea.models.base.JSONish(), nullable=True)
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('requirement_items', schema=None) as batch_op:
        batch_op.drop_column('source_anchor')
