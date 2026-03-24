"""add conversation_state JSON text to conversations

Revision ID: a7c1e9f2d4b6
Revises: d1a2b3c4d5e6
Create Date: 2026-02-23 10:40:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "a7c1e9f2d4b6"
down_revision = "d1a2b3c4d5e6"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return column_name in {row["name"] for row in _inspector().get_columns(table_name)}


def upgrade() -> None:
    if _table_exists("conversations") and not _column_exists("conversations", "conversation_state"):
        op.add_column("conversations", sa.Column("conversation_state", sa.Text(), nullable=True))


def downgrade() -> None:
    if _table_exists("conversations") and _column_exists("conversations", "conversation_state"):
        op.drop_column("conversations", "conversation_state")
