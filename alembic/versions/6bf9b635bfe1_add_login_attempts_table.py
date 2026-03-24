"""add_login_attempts_table

Revision ID: 6bf9b635bfe1
Revises: f05bb4acadaa
Create Date: 2026-01-06 23:12:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '6bf9b635bfe1'
down_revision = 'f05bb4acadaa'
branch_labels = None
depends_on = None


def upgrade():
    # Créer la table login_attempts pour le rate limiting
    op.create_table(
        'login_attempts',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('email', sa.String(320), nullable=False),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.String(500), nullable=True),
        sa.Column('attempted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('success', sa.Boolean(), default=False, nullable=False),
        sa.Column('failure_reason', sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Index pour recherche rapide par email et date
    op.create_index('ix_login_attempts_email_attempted_at', 'login_attempts', ['email', 'attempted_at'])
    op.create_index('ix_login_attempts_ip_attempted_at', 'login_attempts', ['ip_address', 'attempted_at'])


def downgrade():
    op.drop_index('ix_login_attempts_ip_attempted_at', table_name='login_attempts')
    op.drop_index('ix_login_attempts_email_attempted_at', table_name='login_attempts')
    op.drop_table('login_attempts')
