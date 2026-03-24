"""Add person links to conversations and notification logs.

Revision ID: 3e1a5b1f7d6a
Revises: f4a9d2c1b8e7
Create Date: 2026-02-05 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "3e1a5b1f7d6a"
down_revision = "f4a9d2c1b8e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_conversations_person_id",
        "conversations",
        "persons",
        ["person_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "emails_logs",
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_emails_logs_person_id",
        "emails_logs",
        "persons",
        ["person_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "sms_logs",
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_sms_logs_person_id",
        "sms_logs",
        "persons",
        ["person_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_sms_logs_person_id", "sms_logs", type_="foreignkey")
    op.drop_column("sms_logs", "person_id")

    op.drop_constraint("fk_emails_logs_person_id", "emails_logs", type_="foreignkey")
    op.drop_column("emails_logs", "person_id")

    op.drop_constraint("fk_conversations_person_id", "conversations", type_="foreignkey")
    op.drop_column("conversations", "person_id")
