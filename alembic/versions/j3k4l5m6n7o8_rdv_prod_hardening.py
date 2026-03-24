"""rdv production hardening

Revision ID: j3k4l5m6n7o8
Revises: h2i3j4k5l6m7
Create Date: 2026-03-22 20:20:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "j3k4l5m6n7o8"
down_revision = "h2i3j4k5l6m7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("events", sa.Column("rendezvous_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_events_rendezvous_id_rendezvous",
        "events",
        "rendezvous",
        ["rendezvous_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_events_rendezvous_id", "events", ["rendezvous_id"], unique=True)

    op.add_column("emails_logs", sa.Column("dedupe_key", sa.String(length=160), nullable=True))
    op.add_column("emails_logs", sa.Column("recipient", sa.String(length=320), nullable=True))
    op.add_column("emails_logs", sa.Column("provider_name", sa.String(length=80), nullable=True))
    op.add_column("emails_logs", sa.Column("direction", sa.String(length=20), nullable=False, server_default="outbound"))
    op.add_column("emails_logs", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("emails_logs", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("emails_logs", sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("emails_logs", sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_emails_logs_dedupe_key", "emails_logs", ["dedupe_key"], unique=False)

    op.add_column("sms_logs", sa.Column("dedupe_key", sa.String(length=160), nullable=True))
    op.add_column("sms_logs", sa.Column("recipient", sa.String(length=80), nullable=True))
    op.add_column("sms_logs", sa.Column("provider_name", sa.String(length=80), nullable=True))
    op.add_column("sms_logs", sa.Column("direction", sa.String(length=20), nullable=False, server_default="outbound"))
    op.add_column("sms_logs", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("sms_logs", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sms_logs", sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sms_logs", sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_sms_logs_dedupe_key", "sms_logs", ["dedupe_key"], unique=False)

    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute(
        """
        ALTER TABLE rendezvous
        ADD CONSTRAINT ex_rendezvous_agent_active_no_overlap
        EXCLUDE USING gist (
            tenant_id WITH =,
            agent_id WITH =,
            tstzrange(start_at, end_at, '[)') WITH &&
        )
        WHERE (agent_id IS NOT NULL AND deleted_at IS NULL AND statut <> 'cancelled')
        """
    )
    op.execute(
        """
        ALTER TABLE rendezvous
        ADD CONSTRAINT ex_rendezvous_person_active_no_overlap
        EXCLUDE USING gist (
            tenant_id WITH =,
            person_id WITH =,
            tstzrange(start_at, end_at, '[)') WITH &&
        )
        WHERE (person_id IS NOT NULL AND deleted_at IS NULL AND statut <> 'cancelled')
        """
    )


def downgrade():
    op.execute("ALTER TABLE rendezvous DROP CONSTRAINT IF EXISTS ex_rendezvous_person_active_no_overlap")
    op.execute("ALTER TABLE rendezvous DROP CONSTRAINT IF EXISTS ex_rendezvous_agent_active_no_overlap")

    op.drop_index("ix_sms_logs_dedupe_key", table_name="sms_logs")
    op.drop_column("sms_logs", "failed_at")
    op.drop_column("sms_logs", "delivered_at")
    op.drop_column("sms_logs", "sent_at")
    op.drop_column("sms_logs", "last_error")
    op.drop_column("sms_logs", "direction")
    op.drop_column("sms_logs", "provider_name")
    op.drop_column("sms_logs", "recipient")
    op.drop_column("sms_logs", "dedupe_key")

    op.drop_index("ix_emails_logs_dedupe_key", table_name="emails_logs")
    op.drop_column("emails_logs", "failed_at")
    op.drop_column("emails_logs", "delivered_at")
    op.drop_column("emails_logs", "sent_at")
    op.drop_column("emails_logs", "last_error")
    op.drop_column("emails_logs", "direction")
    op.drop_column("emails_logs", "provider_name")
    op.drop_column("emails_logs", "recipient")
    op.drop_column("emails_logs", "dedupe_key")

    op.drop_index("ix_events_rendezvous_id", table_name="events")
    op.drop_constraint("fk_events_rendezvous_id_rendezvous", "events", type_="foreignkey")
    op.drop_column("events", "rendezvous_id")
