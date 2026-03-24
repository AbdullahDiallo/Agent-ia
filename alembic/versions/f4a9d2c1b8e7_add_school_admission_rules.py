"""add school admission rules

Revision ID: f4a9d2c1b8e7
Revises: c3d8e1f6a2b4
Create Date: 2026-02-05 22:10:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "f4a9d2c1b8e7"
down_revision = "c3d8e1f6a2b4"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return column_name in [col["name"] for col in inspector.get_columns(table_name)]


def upgrade():
    if not _table_exists("school_admission_requirements"):
        op.create_table(
            "school_admission_requirements",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("code", sa.String(length=80), nullable=False),
            sa.Column("title_fr", sa.String(length=255), nullable=False),
            sa.Column("title_en", sa.String(length=255), nullable=True),
            sa.Column("title_wo", sa.String(length=255), nullable=True),
            sa.Column("details_fr", sa.Text(), nullable=True),
            sa.Column("details_en", sa.Text(), nullable=True),
            sa.Column("details_wo", sa.Text(), nullable=True),
            sa.Column("sort_order", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
            sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code", name="uq_school_admission_requirements_code"),
        )
        op.create_index(
            "ix_school_admission_requirements_sort_order",
            "school_admission_requirements",
            ["sort_order"],
            unique=False,
        )
        op.create_index(
            "ix_school_admission_requirements_is_active",
            "school_admission_requirements",
            ["is_active"],
            unique=False,
        )

    if not _table_exists("school_admission_policies"):
        op.create_table(
            "school_admission_policies",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("code", sa.String(length=80), nullable=False),
            sa.Column("text_fr", sa.Text(), nullable=False),
            sa.Column("text_en", sa.Text(), nullable=True),
            sa.Column("text_wo", sa.Text(), nullable=True),
            sa.Column("sort_order", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code", name="uq_school_admission_policies_code"),
        )
        op.create_index(
            "ix_school_admission_policies_sort_order",
            "school_admission_policies",
            ["sort_order"],
            unique=False,
        )
        op.create_index(
            "ix_school_admission_policies_is_active",
            "school_admission_policies",
            ["is_active"],
            unique=False,
        )

    # Guard against older DBs where this column is missing.
    if _table_exists("legacy_tracks") and not _column_exists("legacy_tracks", "delivery_mode_legacy"):
        op.add_column("legacy_tracks", sa.Column("delivery_mode_legacy", sa.String(length=20), nullable=True))


def downgrade():
    if _table_exists("legacy_tracks") and _column_exists("legacy_tracks", "delivery_mode_legacy"):
        op.drop_column("legacy_tracks", "delivery_mode_legacy")

    if _table_exists("school_admission_policies"):
        op.drop_index("ix_school_admission_policies_is_active", table_name="school_admission_policies")
        op.drop_index("ix_school_admission_policies_sort_order", table_name="school_admission_policies")
        op.drop_table("school_admission_policies")

    if _table_exists("school_admission_requirements"):
        op.drop_index("ix_school_admission_requirements_is_active", table_name="school_admission_requirements")
        op.drop_index("ix_school_admission_requirements_sort_order", table_name="school_admission_requirements")
        op.drop_table("school_admission_requirements")
