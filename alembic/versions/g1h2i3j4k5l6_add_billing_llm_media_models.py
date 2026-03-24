"""add billing plans, llm usage logs, media attachments, and tenant billing fields

Revision ID: g1h2i3j4k5l6
Revises: fd04d9089edc
Create Date: 2025-03-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "g1h2i3j4k5l6"
down_revision = "fd04d9089edc"
branch_labels = None
depends_on = "9a1d2c3e4f5a"


def upgrade() -> None:
    # --- billing_plans table ---
    op.create_table(
        "billing_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("slug", sa.String(40), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("monthly_price_cents", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("currency", sa.String(3), server_default="usd", nullable=False),
        sa.Column("monthly_message_limit", sa.BigInteger(), server_default="100", nullable=False),
        sa.Column("monthly_call_limit", sa.BigInteger(), server_default="10", nullable=False),
        sa.Column("monthly_rdv_limit", sa.BigInteger(), server_default="20", nullable=False),
        sa.Column("monthly_ai_token_limit", sa.BigInteger(), server_default="100000", nullable=False),
        sa.Column("enabled_channels", sa.String(200), server_default="chat", nullable=False),
        sa.Column("features", sa.Text(), nullable=True),
        sa.Column("stripe_price_id", sa.String(120), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("sort_order", sa.BigInteger(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )

    # --- billing_invoices table ---
    op.create_table(
        "billing_invoices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("currency", sa.String(3), server_default="usd", nullable=False),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("stripe_invoice_id", sa.String(120), nullable=True),
        sa.Column("stripe_payment_intent_id", sa.String(120), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- llm_usage_logs table ---
    op.create_table(
        "llm_usage_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("model", sa.String(60), nullable=False),
        sa.Column("call_type", sa.String(30), nullable=False),
        sa.Column("channel", sa.String(20), nullable=True),
        sa.Column("prompt_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("completion_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("total_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 6), server_default="0", nullable=False),
        sa.Column("latency_ms", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- media_attachments table ---
    op.create_table(
        "media_attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("person_id", sa.Uuid(), sa.ForeignKey("persons.id", ondelete="SET NULL"), nullable=True),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("original_filename", sa.String(500), nullable=True),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("storage_backend", sa.String(20), server_default="local", nullable=False),
        sa.Column("source_url", sa.String(2000), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- Add billing columns to tenants table ---
    op.add_column("tenants", sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("billing_plans.id", ondelete="SET NULL"), nullable=True))
    op.add_column("tenants", sa.Column("stripe_customer_id", sa.String(120), nullable=True))
    op.add_column("tenants", sa.Column("stripe_subscription_id", sa.String(120), nullable=True))
    op.add_column("tenants", sa.Column("subscription_status", sa.String(30), server_default="active", nullable=False))


def downgrade() -> None:
    op.drop_column("tenants", "subscription_status")
    op.drop_column("tenants", "stripe_subscription_id")
    op.drop_column("tenants", "stripe_customer_id")
    op.drop_column("tenants", "plan_id")
    op.drop_table("media_attachments")
    op.drop_table("llm_usage_logs")
    op.drop_table("billing_invoices")
    op.drop_table("billing_plans")
