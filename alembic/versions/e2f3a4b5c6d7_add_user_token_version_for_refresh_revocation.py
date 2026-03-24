"""add user token_version for refresh token revocation

Revision ID: e2f3a4b5c6d7
Revises: c7e9f4a1b2c3
Create Date: 2026-02-07 00:10:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "e2f3a4b5c6d7"
down_revision = "c7e9f4a1b2c3"
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
    if _table_exists("users") and not _column_exists("users", "token_version"):
        with op.batch_alter_table("users") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "token_version",
                    sa.BigInteger(),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )
        with op.batch_alter_table("users") as batch_op:
            batch_op.alter_column("token_version", server_default=None)


def downgrade() -> None:
    if _table_exists("users") and _column_exists("users", "token_version"):
        with op.batch_alter_table("users") as batch_op:
            batch_op.drop_column("token_version")
