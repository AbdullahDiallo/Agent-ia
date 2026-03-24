"""add allowed_origins to tenant_channels for widget embed security

Revision ID: k4l5m6n7o8p9
Revises: 3e1a5b1f7d6a, j3k4l5m6n7o8, 9a1d2c3e4f5a, a7c1e9f2d4b6, g1h2i3j4k5l6
Create Date: 2026-03-01 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "k4l5m6n7o8p9"
down_revision = ("3e1a5b1f7d6a", "j3k4l5m6n7o8", "9a1d2c3e4f5a", "a7c1e9f2d4b6", "g1h2i3j4k5l6")
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
    # Add allowed_origins column to tenant_channels for per-channel origin validation.
    # Stores a comma-separated list of allowed origins (e.g. "https://school.com,https://www.school.com").
    # NULL means no origin restriction (backwards compatible).
    if _table_exists("tenant_channels") and not _column_exists("tenant_channels", "allowed_origins"):
        op.add_column(
            "tenant_channels",
            sa.Column("allowed_origins", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    if _table_exists("tenant_channels") and _column_exists("tenant_channels", "allowed_origins"):
        op.drop_column("tenant_channels", "allowed_origins")
