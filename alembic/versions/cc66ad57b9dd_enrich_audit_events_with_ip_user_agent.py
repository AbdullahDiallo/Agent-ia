"""enrich_audit_events_with_ip_user_agent

Revision ID: cc66ad57b9dd
Revises: 6bf9b635bfe1
Create Date: 2026-01-06 23:15:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'cc66ad57b9dd'
down_revision = '6bf9b635bfe1'
branch_labels = None
depends_on = None


def upgrade():
    # Ajouter colonnes IP et user agent à audit_events
    op.add_column('audit_events', sa.Column('ip_address', sa.String(45), nullable=True))
    op.add_column('audit_events', sa.Column('user_agent', sa.String(500), nullable=True))
    
    # Index pour recherche par IP
    op.create_index('ix_audit_events_ip_address', 'audit_events', ['ip_address'])


def downgrade():
    op.drop_index('ix_audit_events_ip_address', table_name='audit_events')
    op.drop_column('audit_events', 'user_agent')
    op.drop_column('audit_events', 'ip_address')
