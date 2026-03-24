"""add user profile columns

Revision ID: f05bb4acadaa
Revises: bb34b43b2d18
Create Date: 2026-01-06 21:31:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f05bb4acadaa'
down_revision = 'bb34b43b2d18'
branch_labels = None
depends_on = None


def upgrade():
    # Ajouter seulement les colonnes manquantes à la table users
    # (first_name, last_name, phone existent déjà)
    op.add_column('users', sa.Column('avatar_url', sa.String(length=500), nullable=True))
    op.add_column('users', sa.Column('last_login', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    # Supprimer les colonnes ajoutées à users
    op.drop_column('users', 'last_login')
    op.drop_column('users', 'avatar_url')
