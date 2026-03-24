"""tenant foundation and outbox

Revision ID: 9a1d2c3e4f5a
Revises: fd04d9089edc, f4a9d2c1b8e7
Create Date: 2026-02-06 18:20:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "9a1d2c3e4f5a"
down_revision = ("fd04d9089edc", "f4a9d2c1b8e7")
branch_labels = None
depends_on = None

DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"

TENANT_TABLES_REQUIRED = [
    "users",
    "agents",
    "managers",
    "viewers",
    "persons",
    "person_roles",
    "parent_student_links",
    "school_departments",
    "school_programs",
    "school_tracks",
    "school_admission_requirements",
    "school_admission_policies",
    "rendezvous",
    "conversations",
    "messages",
    "emails_logs",
    "sms_logs",
    "calendars",
    "events",
    "email_templates",
    "documents",
]

TENANT_TABLES_OPTIONAL = [
    "audit_events",
    "login_attempts",
]


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return column_name in [col["name"] for col in _inspector().get_columns(table_name)]


def _index_exists(table_name: str, index_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return index_name in [idx.get("name") for idx in _inspector().get_indexes(table_name)]


def _fk_exists(table_name: str, fk_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return fk_name in [fk.get("name") for fk in _inspector().get_foreign_keys(table_name)]


def _ensure_tenant_tables() -> None:
    if not _table_exists("tenants"):
        op.create_table(
            "tenants",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("slug", sa.String(length=80), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("slug", name="uq_tenants_slug"),
        )
    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO tenants (id, slug, name, is_active)
            VALUES (:id, :slug, :name, true)
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"id": DEFAULT_TENANT_ID, "slug": "default", "name": "Default Tenant"},
    )

    if not _table_exists("tenant_settings"):
        op.create_table(
            "tenant_settings",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("default_language", sa.String(length=10), nullable=False, server_default="fr"),
            sa.Column("enabled_channels", sa.String(length=200), nullable=False, server_default="chat,email,sms,whatsapp,call"),
            sa.Column("monthly_rdv_limit", sa.BigInteger(), nullable=False, server_default="500"),
            sa.Column("monthly_message_limit", sa.BigInteger(), nullable=False, server_default="5000"),
            sa.Column("monthly_call_limit", sa.BigInteger(), nullable=False, server_default="2000"),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE", name="fk_tenant_settings_tenant_id"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", name="uq_tenant_settings_tenant_id"),
        )

    if not _table_exists("tenant_quota_usage"):
        op.create_table(
            "tenant_quota_usage",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("metric", sa.String(length=40), nullable=False),
            sa.Column("period", sa.String(length=20), nullable=False),
            sa.Column("used_count", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE", name="fk_tenant_quota_usage_tenant_id"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_tenant_quota_usage_tenant_metric_period", "tenant_quota_usage", ["tenant_id", "metric", "period"], unique=False)

    if not _table_exists("outbox_events"):
        op.create_table(
            "outbox_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("event_type", sa.String(length=120), nullable=False),
            sa.Column("aggregate_type", sa.String(length=80), nullable=False),
            sa.Column("aggregate_id", sa.String(length=120), nullable=False),
            sa.Column("payload", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("attempts", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE", name="fk_outbox_events_tenant_id"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_outbox_events_status_available_at", "outbox_events", ["status", "available_at"], unique=False)
        op.create_index("ix_outbox_events_tenant_id", "outbox_events", ["tenant_id"], unique=False)


def _add_tenant_column(table_name: str, required: bool) -> None:
    if not _table_exists(table_name):
        return
    if not _column_exists(table_name, "tenant_id"):
        op.add_column(table_name, sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True))

    op.get_bind().execute(
        sa.text(f"UPDATE {table_name} SET tenant_id = :tenant_id WHERE tenant_id IS NULL"),
        {"tenant_id": DEFAULT_TENANT_ID},
    )

    fk_name = f"fk_{table_name}_tenant_id"
    if not _fk_exists(table_name, fk_name):
        op.create_foreign_key(fk_name, table_name, "tenants", ["tenant_id"], ["id"], ondelete="CASCADE")

    index_name = f"ix_{table_name}_tenant_id"
    if not _index_exists(table_name, index_name):
        op.create_index(index_name, table_name, ["tenant_id"], unique=False)

    if required:
        op.alter_column(table_name, "tenant_id", nullable=False)


def upgrade():
    _ensure_tenant_tables()
    for table_name in TENANT_TABLES_REQUIRED:
        _add_tenant_column(table_name, required=True)
    for table_name in TENANT_TABLES_OPTIONAL:
        _add_tenant_column(table_name, required=False)


def _drop_tenant_column(table_name: str) -> None:
    if not _table_exists(table_name):
        return
    if not _column_exists(table_name, "tenant_id"):
        return
    index_name = f"ix_{table_name}_tenant_id"
    if _index_exists(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)
    fk_name = f"fk_{table_name}_tenant_id"
    if _fk_exists(table_name, fk_name):
        op.drop_constraint(fk_name, table_name, type_="foreignkey")
    op.drop_column(table_name, "tenant_id")


def downgrade():
    for table_name in TENANT_TABLES_OPTIONAL:
        _drop_tenant_column(table_name)
    for table_name in TENANT_TABLES_REQUIRED:
        _drop_tenant_column(table_name)

    if _table_exists("outbox_events"):
        if _index_exists("outbox_events", "ix_outbox_events_tenant_id"):
            op.drop_index("ix_outbox_events_tenant_id", table_name="outbox_events")
        if _index_exists("outbox_events", "ix_outbox_events_status_available_at"):
            op.drop_index("ix_outbox_events_status_available_at", table_name="outbox_events")
        op.drop_table("outbox_events")

    if _table_exists("tenant_quota_usage"):
        if _index_exists("tenant_quota_usage", "ix_tenant_quota_usage_tenant_metric_period"):
            op.drop_index("ix_tenant_quota_usage_tenant_metric_period", table_name="tenant_quota_usage")
        op.drop_table("tenant_quota_usage")

    if _table_exists("tenant_settings"):
        op.drop_table("tenant_settings")

    if _table_exists("tenants"):
        op.drop_table("tenants")
