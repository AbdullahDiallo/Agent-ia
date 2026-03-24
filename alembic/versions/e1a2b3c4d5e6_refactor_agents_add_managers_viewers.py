"""refactor agents add managers viewers

Revision ID: e1a2b3c4d5e6
Revises: f05bb4acadaa
Create Date: 2026-01-07 17:10:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'e1a2b3c4d5e6'
down_revision = 'f05bb4acadaa'
branch_labels = None
depends_on = None


def upgrade():
    # Créer les tables managers et viewers
    op.create_table('managers',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id')
    )
    
    op.create_table('viewers',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id')
    )
    
    # Restructurer la table agents
    # 1. Supprimer les colonnes dupliquées (nom, email, telephone)
    op.drop_column('agents', 'nom')
    op.drop_column('agents', 'email')
    op.drop_column('agents', 'telephone')
    
    # 2. Modifier user_id pour être NOT NULL et CASCADE
    op.alter_column('agents', 'user_id',
                    existing_type=sa.BigInteger(),
                    nullable=False)
    
    # 3. Recréer la foreign key avec CASCADE
    op.drop_constraint('agents_user_id_fkey', 'agents', type_='foreignkey')
    op.create_foreign_key('agents_user_id_fkey', 'agents', 'users', ['user_id'], ['id'], ondelete='CASCADE')


def downgrade():
    # Restaurer la foreign key originale
    op.drop_constraint('agents_user_id_fkey', 'agents', type_='foreignkey')
    op.create_foreign_key('agents_user_id_fkey', 'agents', 'users', ['user_id'], ['id'], ondelete='SET NULL')
    
    # Restaurer user_id nullable
    op.alter_column('agents', 'user_id',
                    existing_type=sa.BigInteger(),
                    nullable=True)
    
    # Restaurer les colonnes supprimées
    op.add_column('agents', sa.Column('telephone', sa.String(length=50), nullable=True))
    op.add_column('agents', sa.Column('email', sa.String(length=200), nullable=True))
    op.add_column('agents', sa.Column('nom', sa.String(length=200), nullable=False, server_default=''))
    
    # Supprimer les tables managers et viewers
    op.drop_table('viewers')
    op.drop_table('managers')
