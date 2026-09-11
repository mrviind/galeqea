"""requirement rules + testcase.covers (WO#9-B)

Adds the requirement_rules table (atomic rules distilled from requirements) and a
`covers` column on test_cases linking a case to the rule_ids it exercises.

Revision ID: f3b8c2a6e1d9
Revises: e7f4a1c9d2b8
Create Date: 2026-08-29 14:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import galeqea.models.base


# revision identifiers, used by Alembic.
revision: str = 'f3b8c2a6e1d9'
down_revision: Union[str, Sequence[str], None] = 'e7f4a1c9d2b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'requirement_rules',
        sa.Column('project_id', sa.String(length=40), nullable=False),
        sa.Column('doc_id', sa.String(length=40), nullable=True),
        sa.Column('item_id', sa.String(length=40), nullable=True),
        sa.Column('requirement_ref', sa.String(length=64), nullable=False),
        sa.Column('rule_id', sa.String(length=64), nullable=False),
        sa.Column('rule_type', sa.String(length=24), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('inputs', galeqea.models.base.JSONish(), nullable=False),
        sa.Column('constraints', galeqea.models.base.JSONish(), nullable=False),
        sa.Column('technique', sa.String(length=32), nullable=False),
        sa.Column('source_anchor', galeqea.models.base.JSONish(), nullable=False),
        sa.Column('provenance', galeqea.models.base.JSONish(), nullable=False),
        sa.Column('open_questions', galeqea.models.base.JSONish(), nullable=False),
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('created_at', galeqea.models.base.UTCDateTime(timezone=True), nullable=False),
        sa.Column('updated_at', galeqea.models.base.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['item_id'], ['requirement_items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('requirement_rules', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_requirement_rules_project_id'), ['project_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_requirement_rules_doc_id'), ['doc_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_requirement_rules_item_id'), ['item_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_requirement_rules_requirement_ref'), ['requirement_ref'], unique=False)
        batch_op.create_index(batch_op.f('ix_requirement_rules_rule_id'), ['rule_id'], unique=False)

    with op.batch_alter_table('test_cases', schema=None) as batch_op:
        batch_op.add_column(sa.Column('covers', galeqea.models.base.JSONish(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('test_cases', schema=None) as batch_op:
        batch_op.drop_column('covers')

    with op.batch_alter_table('requirement_rules', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_requirement_rules_rule_id'))
        batch_op.drop_index(batch_op.f('ix_requirement_rules_requirement_ref'))
        batch_op.drop_index(batch_op.f('ix_requirement_rules_item_id'))
        batch_op.drop_index(batch_op.f('ix_requirement_rules_doc_id'))
        batch_op.drop_index(batch_op.f('ix_requirement_rules_project_id'))
    op.drop_table('requirement_rules')
