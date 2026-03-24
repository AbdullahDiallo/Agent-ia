"""add_sentiment_to_conversations

Revision ID: d8f3c4e5a6b7
Revises: cc66ad57b9dd
Create Date: 2026-01-06 23:18:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd8f3c4e5a6b7'
down_revision = 'cc66ad57b9dd'
branch_labels = None
depends_on = None


def upgrade():
    # Ajouter colonnes pour l'analyse de sentiment
    op.add_column('conversations', sa.Column('sentiment_score', sa.Float(), nullable=True))
    op.add_column('conversations', sa.Column('sentiment_label', sa.String(20), nullable=True))
    op.add_column('conversations', sa.Column('sentiment_analyzed_at', sa.DateTime(timezone=True), nullable=True))
    
    # Ajouter colonne pour le scoring des leads
    op.add_column('legacy_contacts', sa.Column('engagement_score', sa.Integer(), nullable=True))
    op.add_column('legacy_contacts', sa.Column('engagement_score_updated_at', sa.DateTime(timezone=True), nullable=True))
    
    # Index pour recherche par sentiment
    op.create_index('ix_conversations_sentiment_label', 'conversations', ['sentiment_label'])
    op.create_index('ix_legacy_contacts_engagement_score', 'legacy_contacts', ['engagement_score'])


def downgrade():
    op.drop_index('ix_legacy_contacts_engagement_score', table_name='legacy_contacts')
    op.drop_index('ix_conversations_sentiment_label', table_name='conversations')
    op.drop_column('legacy_contacts', 'engagement_score_updated_at')
    op.drop_column('legacy_contacts', 'engagement_score')
    op.drop_column('conversations', 'sentiment_analyzed_at')
    op.drop_column('conversations', 'sentiment_label')
    op.drop_column('conversations', 'sentiment_score')
