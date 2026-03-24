"""split school programs and tracks

Revision ID: b7c2d9e4f1a0
Revises: a1f4b7c9d2e3
Create Date: 2026-02-03 23:10:00.000000

"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "b7c2d9e4f1a0"
down_revision = "a1f4b7c9d2e3"
branch_labels = None
depends_on = None


def upgrade():
    # Rename existing table to tracks, then create a normalized programs table.
    op.rename_table("school_programs", "school_tracks")

    op.drop_index("ix_school_programs_department_id", table_name="school_tracks")
    op.drop_index("ix_school_programs_level", table_name="school_tracks")
    op.drop_index("ix_school_programs_delivery_mode", table_name="school_tracks")
    op.drop_constraint("uq_school_program_identity", "school_tracks", type_="unique")

    op.create_table(
        "school_programs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("delivery_mode", sa.String(length=30), nullable=False, server_default="onsite"),
        sa.Column("access_level", sa.String(length=120), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.ForeignKeyConstraint(["department_id"], ["school_departments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("department_id", "name", "delivery_mode", "access_level", name="uq_school_program_identity"),
    )
    op.create_index("ix_school_programs_department_id", "school_programs", ["department_id"], unique=False)
    op.create_index("ix_school_programs_name", "school_programs", ["name"], unique=False)
    op.create_index("ix_school_programs_delivery_mode", "school_programs", ["delivery_mode"], unique=False)

    op.add_column("school_tracks", sa.Column("program_id", postgresql.UUID(as_uuid=True), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT DISTINCT department_id, level, delivery_mode, access_level
            FROM school_tracks
            """
        )
    ).fetchall()

    for row in rows:
        program_id = uuid.uuid4()
        bind.execute(
            sa.text(
                """
                INSERT INTO school_programs (id, department_id, name, delivery_mode, access_level, is_active)
                VALUES (:id, :department_id, :name, :delivery_mode, :access_level, true)
                """
            ),
            {
                "id": str(program_id),
                "department_id": str(row.department_id),
                "name": row.level,
                "delivery_mode": row.delivery_mode,
                "access_level": row.access_level,
            },
        )
        bind.execute(
            sa.text(
                """
                UPDATE school_tracks
                SET program_id = :program_id
                WHERE department_id = :department_id
                  AND level = :level
                  AND delivery_mode = :delivery_mode
                  AND (
                    (access_level IS NULL AND :access_level IS NULL)
                    OR access_level = :access_level
                  )
                """
            ),
            {
                "program_id": str(program_id),
                "department_id": str(row.department_id),
                "level": row.level,
                "delivery_mode": row.delivery_mode,
                "access_level": row.access_level,
            },
        )

    op.alter_column("school_tracks", "program_id", nullable=False)
    op.create_foreign_key(
        "fk_school_tracks_program_id_school_programs",
        "school_tracks",
        "school_programs",
        ["program_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.alter_column("school_tracks", "title", new_column_name="name")
    op.drop_column("school_tracks", "department_id")
    op.drop_column("school_tracks", "level")
    op.drop_column("school_tracks", "access_level")
    op.drop_column("school_tracks", "delivery_mode")
    op.create_unique_constraint("uq_school_track_identity", "school_tracks", ["program_id", "name"])
    op.create_index("ix_school_tracks_program_id", "school_tracks", ["program_id"], unique=False)
    op.create_index("ix_school_tracks_name", "school_tracks", ["name"], unique=False)


def downgrade():
    op.drop_index("ix_school_tracks_name", table_name="school_tracks")
    op.drop_index("ix_school_tracks_program_id", table_name="school_tracks")
    op.drop_constraint("uq_school_track_identity", "school_tracks", type_="unique")
    op.drop_constraint("fk_school_tracks_program_id_school_programs", "school_tracks", type_="foreignkey")

    op.add_column("school_tracks", sa.Column("delivery_mode", sa.String(length=30), nullable=False, server_default="onsite"))
    op.add_column("school_tracks", sa.Column("access_level", sa.String(length=120), nullable=True))
    op.add_column("school_tracks", sa.Column("level", sa.String(length=60), nullable=False, server_default="unknown"))
    op.add_column("school_tracks", sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.alter_column("school_tracks", "name", new_column_name="title")

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT t.id, p.department_id, p.name, p.delivery_mode, p.access_level
            FROM school_tracks t
            JOIN school_programs p ON p.id = t.program_id
            """
        )
    ).fetchall()
    for row in rows:
        bind.execute(
            sa.text(
                """
                UPDATE school_tracks
                SET department_id = :department_id,
                    level = :level,
                    delivery_mode = :delivery_mode,
                    access_level = :access_level
                WHERE id = :track_id
                """
            ),
            {
                "track_id": str(row.id),
                "department_id": str(row.department_id),
                "level": row.name,
                "delivery_mode": row.delivery_mode,
                "access_level": row.access_level,
            },
        )

    op.alter_column("school_tracks", "department_id", nullable=False)
    op.drop_column("school_tracks", "program_id")
    op.create_unique_constraint(
        "uq_school_program_identity",
        "school_tracks",
        ["department_id", "title", "level", "delivery_mode"],
    )
    op.create_index("ix_school_programs_department_id", "school_tracks", ["department_id"], unique=False)
    op.create_index("ix_school_programs_level", "school_tracks", ["level"], unique=False)
    op.create_index("ix_school_programs_delivery_mode", "school_tracks", ["delivery_mode"], unique=False)

    op.drop_index("ix_school_programs_delivery_mode", table_name="school_programs")
    op.drop_index("ix_school_programs_name", table_name="school_programs")
    op.drop_index("ix_school_programs_department_id", table_name="school_programs")
    op.drop_table("school_programs")
    op.rename_table("school_tracks", "school_programs")

