"""add tenant_channels mapping for deterministic tenant resolution

Revision ID: d1a2b3c4d5e6
Revises: e2f3a4b5c6d7
Create Date: 2026-02-06 23:40:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "d1a2b3c4d5e6"
down_revision = "e2f3a4b5c6d7"
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
    if not _table_exists("tenant_channels"):
        op.create_table(
            "tenant_channels",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("provider", sa.String(length=80), nullable=False),
            sa.Column("provider_key", sa.String(length=255), nullable=False),
            sa.Column("token_hash", sa.String(length=128), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("provider", "provider_key", name="uq_tenant_channels_provider_key"),
        )


def downgrade() -> None:
    if _table_exists("tenant_channels"):
        op.drop_table("tenant_channels")
