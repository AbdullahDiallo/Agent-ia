"""add school catalog tables

Revision ID: a1f4b7c9d2e3
Revises: fd04d9089edc
Create Date: 2026-02-03 21:05:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "a1f4b7c9d2e3"
down_revision = "fd04d9089edc"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "school_departments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "school_programs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("level", sa.String(length=60), nullable=False),
        sa.Column("annual_fee", sa.Numeric(scale=2), nullable=False),
        sa.Column("registration_fee", sa.Numeric(scale=2), nullable=False),
        sa.Column("monthly_fee", sa.Numeric(scale=2), nullable=False),
        sa.Column("certifications", sa.Text(), nullable=True),
        sa.Column("options", sa.Text(), nullable=True),
        sa.Column("access_level", sa.String(length=120), nullable=True),
        sa.Column("delivery_mode", sa.String(length=30), nullable=False, server_default="onsite"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.ForeignKeyConstraint(["department_id"], ["school_departments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("department_id", "title", "level", "delivery_mode", name="uq_school_program_identity"),
    )

    op.create_index("ix_school_programs_department_id", "school_programs", ["department_id"], unique=False)
    op.create_index("ix_school_programs_level", "school_programs", ["level"], unique=False)
    op.create_index("ix_school_programs_delivery_mode", "school_programs", ["delivery_mode"], unique=False)


def downgrade():
    op.drop_index("ix_school_programs_delivery_mode", table_name="school_programs")
    op.drop_index("ix_school_programs_level", table_name="school_programs")
    op.drop_index("ix_school_programs_department_id", table_name="school_programs")
    op.drop_table("school_programs")
    op.drop_table("school_departments")

