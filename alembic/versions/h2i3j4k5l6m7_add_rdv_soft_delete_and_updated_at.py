"""add rdv soft delete and updated_at

Revision ID: h2i3j4k5l6m7
Revises: a7c1e9f2d4b6, g1h2i3j4k5l6
Create Date: 2026-03-22 15:06:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'h2i3j4k5l6m7'
down_revision = ('a7c1e9f2d4b6', 'g1h2i3j4k5l6')
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('rendezvous', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('rendezvous', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_rendezvous_deleted_at', 'rendezvous', ['deleted_at'], unique=False)
    op.create_index('ix_rendezvous_start_at_end_at', 'rendezvous', ['start_at', 'end_at'], unique=False)


def downgrade():
    op.drop_index('ix_rendezvous_start_at_end_at', table_name='rendezvous')
    op.drop_index('ix_rendezvous_deleted_at', table_name='rendezvous')
    op.drop_column('rendezvous', 'deleted_at')
    op.drop_column('rendezvous', 'updated_at')
