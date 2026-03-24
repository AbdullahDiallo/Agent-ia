"""add person core and school appointments

Revision ID: c3d8e1f6a2b4
Revises: b7c2d9e4f1a0
Create Date: 2026-02-04 00:20:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "c3d8e1f6a2b4"
down_revision = "b7c2d9e4f1a0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "persons",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("first_name", sa.String(length=120), nullable=False),
        sa.Column("last_name", sa.String(length=120), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("preferred_language", sa.String(length=10), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="active"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_persons_email", "persons", ["email"], unique=False)
    op.create_index("ix_persons_phone", "persons", ["phone"], unique=False)

    op.create_table(
        "person_roles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["person_id"], ["persons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("person_id", "role", name="uq_person_role"),
    )
    op.create_index("ix_person_roles_person_id", "person_roles", ["person_id"], unique=False)
    op.create_index("ix_person_roles_role", "person_roles", ["role"], unique=False)

    op.create_table(
        "parent_student_links",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["persons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["persons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("parent_id", "student_id", name="uq_parent_student_link"),
    )
    op.create_index("ix_parent_student_parent", "parent_student_links", ["parent_id"], unique=False)
    op.create_index("ix_parent_student_student", "parent_student_links", ["student_id"], unique=False)

    op.add_column("rendezvous", sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("rendezvous", sa.Column("track_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_rendezvous_person_id_persons",
        "rendezvous",
        "persons",
        ["person_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_rendezvous_track_id_school_tracks",
        "rendezvous",
        "school_tracks",
        ["track_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_rendezvous_person_id", "rendezvous", ["person_id"], unique=False)
    op.create_index("ix_rendezvous_track_id", "rendezvous", ["track_id"], unique=False)


def downgrade():
    op.drop_index("ix_rendezvous_track_id", table_name="rendezvous")
    op.drop_index("ix_rendezvous_person_id", table_name="rendezvous")
    op.drop_constraint("fk_rendezvous_track_id_school_tracks", "rendezvous", type_="foreignkey")
    op.drop_constraint("fk_rendezvous_person_id_persons", "rendezvous", type_="foreignkey")
    op.drop_column("rendezvous", "track_id")
    op.drop_column("rendezvous", "person_id")

    op.drop_index("ix_parent_student_student", table_name="parent_student_links")
    op.drop_index("ix_parent_student_parent", table_name="parent_student_links")
    op.drop_table("parent_student_links")

    op.drop_index("ix_person_roles_role", table_name="person_roles")
    op.drop_index("ix_person_roles_person_id", table_name="person_roles")
    op.drop_table("person_roles")

    op.drop_index("ix_persons_phone", table_name="persons")
    op.drop_index("ix_persons_email", table_name="persons")
    op.drop_table("persons")

