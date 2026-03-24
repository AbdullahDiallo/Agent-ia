"""merge person-links and tenant heads

Revision ID: b1f6e2c4d9ab
Revises: 3e1a5b1f7d6a, 9a1d2c3e4f5a
Create Date: 2026-02-06 21:35:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "b1f6e2c4d9ab"
down_revision = ("3e1a5b1f7d6a", "9a1d2c3e4f5a")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Merge revision: no schema operation.
    pass


def downgrade() -> None:
    # Merge revision: no schema operation.
    pass
