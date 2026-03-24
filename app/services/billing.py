"""Billing service — DB-driven plans, Stripe integration, usage enforcement.

Plans are stored in the billing_plans table, not hardcoded.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from ..config import settings
from ..logger import get_logger
from ..models import (
    BillingInvoice,
    BillingPlan,
    Tenant,
    TenantQuotaUsage,
    TenantSettings,
)

logger = get_logger(__name__)

try:
    import stripe as stripe_lib

    _STRIPE_AVAILABLE = True
except ImportError:
    stripe_lib = None  # type: ignore
    _STRIPE_AVAILABLE = False


# ----------------------------------------------------------------
# Plan CRUD (DB-driven)
# ----------------------------------------------------------------


def list_plans(db: Session, *, include_inactive: bool = False) -> List[Dict[str, Any]]:
    query = db.query(BillingPlan).order_by(BillingPlan.sort_order.asc(), BillingPlan.created_at.asc())
    if not include_inactive:
        query = query.filter(BillingPlan.is_active == True)  # noqa: E712
    rows = query.all()
    return [_plan_to_dict(p) for p in rows]


def get_plan(db: Session, plan_id: str) -> Optional[Dict[str, Any]]:
    """Get plan by UUID or slug."""
    row = _resolve_plan(db, plan_id)
    return _plan_to_dict(row) if row else None


def get_plan_by_slug(db: Session, slug: str) -> Optional[BillingPlan]:
    return db.query(BillingPlan).filter(BillingPlan.slug == slug, BillingPlan.is_active == True).first()  # noqa: E712


def create_plan(db: Session, *, data: Dict[str, Any]) -> Dict[str, Any]:
    plan = BillingPlan(
        slug=str(data["slug"]).strip().lower(),
        name=str(data["name"]).strip(),
        description=str(data.get("description") or "").strip() or None,
        monthly_price_cents=int(data.get("monthly_price_cents") or 0),
        currency=str(data.get("currency") or "usd").strip().lower(),
        monthly_message_limit=int(data.get("monthly_message_limit") or 100),
        monthly_call_limit=int(data.get("monthly_call_limit") or 10),
        monthly_rdv_limit=int(data.get("monthly_rdv_limit") or 20),
        monthly_ai_token_limit=int(data.get("monthly_ai_token_limit") or 100_000),
        enabled_channels=str(data.get("enabled_channels") or "chat").strip(),
        features=json.dumps(data.get("features") or [], ensure_ascii=False),
        stripe_price_id=str(data.get("stripe_price_id") or "").strip() or None,
        is_active=bool(data.get("is_active", True)),
        sort_order=int(data.get("sort_order") or 0),
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return _plan_to_dict(plan)


def update_plan(db: Session, plan_id: str, *, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    row = _resolve_plan(db, plan_id)
    if not row:
        return None
    for key in (
        "name", "description", "monthly_price_cents", "currency",
        "monthly_message_limit", "monthly_call_limit", "monthly_rdv_limit",
        "monthly_ai_token_limit", "enabled_channels", "stripe_price_id",
        "is_active", "sort_order",
    ):
        if key in data:
            setattr(row, key, data[key])
    if "features" in data:
        row.features = json.dumps(data["features"] or [], ensure_ascii=False)
    db.commit()
    db.refresh(row)
    return _plan_to_dict(row)


def deactivate_plan(db: Session, plan_id: str) -> bool:
    row = _resolve_plan(db, plan_id)
    if not row:
        return False
    row.is_active = False
    db.commit()
    return True


def seed_default_plans(db: Session) -> List[Dict[str, Any]]:
    """Seed default plans if none exist. Idempotent."""
    existing = db.query(BillingPlan).count()
    if existing > 0:
        return list_plans(db, include_inactive=True)

    defaults = [
        {
            "slug": "free", "name": "Free", "monthly_price_cents": 0,
            "monthly_message_limit": 100, "monthly_call_limit": 10,
            "monthly_rdv_limit": 20, "monthly_ai_token_limit": 100_000,
            "enabled_channels": "chat", "sort_order": 0,
            "features": ["basic_chat"],
        },
        {
            "slug": "starter", "name": "Starter", "monthly_price_cents": 4900,
            "monthly_message_limit": 2000, "monthly_call_limit": 200,
            "monthly_rdv_limit": 200, "monthly_ai_token_limit": 1_000_000,
            "enabled_channels": "chat,email,sms", "sort_order": 1,
            "features": ["basic_chat", "email_integration", "sms_integration", "dashboard"],
        },
        {
            "slug": "pro", "name": "Pro", "monthly_price_cents": 14900,
            "monthly_message_limit": 10000, "monthly_call_limit": 1000,
            "monthly_rdv_limit": 500, "monthly_ai_token_limit": 5_000_000,
            "enabled_channels": "chat,email,sms,whatsapp,call", "sort_order": 2,
            "features": [
                "basic_chat", "email_integration", "sms_integration",
                "whatsapp_integration", "voice_calls", "dashboard", "analytics", "custom_persona",
            ],
        },
        {
            "slug": "enterprise", "name": "Enterprise", "monthly_price_cents": 49900,
            "monthly_message_limit": 50000, "monthly_call_limit": 5000,
            "monthly_rdv_limit": 2000, "monthly_ai_token_limit": 20_000_000,
            "enabled_channels": "chat,email,sms,whatsapp,call", "sort_order": 3,
            "features": [
                "basic_chat", "email_integration", "sms_integration",
                "whatsapp_integration", "voice_calls", "dashboard", "analytics",
                "custom_persona", "api_access", "priority_support", "sla_99_9",
            ],
        },
    ]
    result = []
    for d in defaults:
        result.append(create_plan(db, data=d))
    return result


# ----------------------------------------------------------------
# Tenant plan management
# ----------------------------------------------------------------


def get_tenant_plan(db: Session, tenant_id: str) -> Optional[Dict[str, Any]]:
    tenant = db.query(Tenant).filter(Tenant.id == UUID(str(tenant_id))).first()
    if not tenant or not tenant.plan_id:
        return None
    plan = db.query(BillingPlan).filter(BillingPlan.id == tenant.plan_id).first()
    return _plan_to_dict(plan) if plan else None


def assign_plan_to_tenant(db: Session, tenant_id: str, plan_slug: str) -> bool:
    """Assign a plan to a tenant and sync limits to TenantSettings."""
    plan = get_plan_by_slug(db, plan_slug)
    if not plan:
        return False
    tenant_uuid = UUID(str(tenant_id))
    tenant = db.query(Tenant).filter(Tenant.id == tenant_uuid).first()
    if not tenant:
        return False

    tenant.plan_id = plan.id
    db.flush()

    # Sync limits to TenantSettings
    ts = db.query(TenantSettings).filter(TenantSettings.tenant_id == tenant_uuid).first()
    if ts:
        ts.monthly_message_limit = plan.monthly_message_limit
        ts.monthly_call_limit = plan.monthly_call_limit
        ts.monthly_rdv_limit = plan.monthly_rdv_limit
        ts.enabled_channels = plan.enabled_channels
    db.commit()
    logger.info(
        "Plan assigned to tenant",
        extra={"extra_fields": {"tenant_id": tenant_id, "plan_slug": plan_slug}},
    )
    return True


def is_channel_allowed(db: Session, tenant_id: str, channel: str) -> bool:
    """Check if a channel is allowed by the tenant's plan."""
    plan = get_tenant_plan(db, tenant_id)
    if not plan:
        return True  # No plan = no restriction (backward compat)
    allowed = [c.strip() for c in str(plan.get("enabled_channels") or "").split(",")]
    return channel.lower() in allowed


# ----------------------------------------------------------------
# Usage tracking
# ----------------------------------------------------------------


def _period_key(dt: Optional[datetime] = None) -> str:
    now = dt or datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def get_tenant_usage(db: Session, tenant_id: str) -> Dict[str, Any]:
    tenant_uuid = UUID(str(tenant_id))
    period = _period_key()

    rows = (
        db.query(TenantQuotaUsage)
        .filter(TenantQuotaUsage.tenant_id == tenant_uuid, TenantQuotaUsage.period == period)
        .all()
    )
    usage = {row.metric: int(row.used_count or 0) for row in rows}

    plan = get_tenant_plan(db, tenant_id)
    ts = db.query(TenantSettings).filter(TenantSettings.tenant_id == tenant_uuid).first()

    return {
        "period": period,
        "tenant_id": tenant_id,
        "plan": plan.get("slug") if plan else None,
        "messages": {
            "used": usage.get("messages", 0),
            "limit": int(plan["monthly_message_limit"]) if plan else (int(ts.monthly_message_limit) if ts else 5000),
        },
        "calls": {
            "used": usage.get("calls", 0),
            "limit": int(plan["monthly_call_limit"]) if plan else (int(ts.monthly_call_limit) if ts else 2000),
        },
        "rendezvous": {
            "used": usage.get("rendezvous", 0),
            "limit": int(plan["monthly_rdv_limit"]) if plan else (int(ts.monthly_rdv_limit) if ts else 500),
        },
        "ai_tokens": {
            "used": usage.get("ai_tokens", 0),
            "limit": int(plan["monthly_ai_token_limit"]) if plan else 5_000_000,
        },
    }


def get_tenant_usage_history(db: Session, tenant_id: str, *, months: int = 6) -> List[Dict[str, Any]]:
    tenant_uuid = UUID(str(tenant_id))
    rows = (
        db.query(TenantQuotaUsage)
        .filter(TenantQuotaUsage.tenant_id == tenant_uuid)
        .order_by(TenantQuotaUsage.period.desc())
        .limit(months * 6)
        .all()
    )
    history: Dict[str, Dict[str, int]] = {}
    for row in rows:
        p = str(row.period)
        history.setdefault(p, {})[row.metric] = int(row.used_count or 0)
    return [{"period": p, **m} for p, m in sorted(history.items(), reverse=True)][:months]


# ----------------------------------------------------------------
# Stripe integration
# ----------------------------------------------------------------


def _get_stripe_key() -> Optional[str]:
    return getattr(settings, "stripe_secret_key", None) or None


def is_stripe_configured() -> bool:
    return _STRIPE_AVAILABLE and bool(_get_stripe_key())


def create_checkout_session(
    db: Session,
    *,
    tenant_id: str,
    plan_slug: str,
    success_url: str,
    cancel_url: str,
) -> Optional[Dict[str, Any]]:
    if not is_stripe_configured():
        return None
    plan = get_plan_by_slug(db, plan_slug)
    if not plan or plan.monthly_price_cents == 0:
        return None

    stripe_lib.api_key = _get_stripe_key()
    try:
        line_item: Dict[str, Any]
        if plan.stripe_price_id:
            line_item = {"price": plan.stripe_price_id, "quantity": 1}
        else:
            line_item = {
                "price_data": {
                    "currency": plan.currency or "usd",
                    "product_data": {"name": f"Agentia Scolaire - {plan.name}"},
                    "unit_amount": int(plan.monthly_price_cents),
                    "recurring": {"interval": "month"},
                },
                "quantity": 1,
            }

        session = stripe_lib.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[line_item],
            mode="subscription",
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"tenant_id": tenant_id, "plan_slug": plan.slug},
        )
        return {"checkout_url": session.url, "session_id": session.id}
    except Exception as e:
        logger.error(f"Stripe checkout failed: {e}", exc_info=True)
        return None


def handle_stripe_webhook(db: Session, payload: bytes, sig_header: str) -> Optional[Dict[str, Any]]:
    if not is_stripe_configured():
        return None
    webhook_secret = getattr(settings, "stripe_webhook_secret", None)
    if not webhook_secret:
        return None

    stripe_lib.api_key = _get_stripe_key()
    try:
        event = stripe_lib.Webhook.construct_event(payload, sig_header, webhook_secret)
    except Exception as e:
        logger.error(f"Stripe webhook verification failed: {e}")
        return None

    event_type = event.get("type", "")
    data = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        tenant_id = data.get("metadata", {}).get("tenant_id")
        plan_slug = data.get("metadata", {}).get("plan_slug")
        customer_id = data.get("customer")
        subscription_id = data.get("subscription")

        if tenant_id and plan_slug:
            # Assign plan and store Stripe IDs
            assign_plan_to_tenant(db, tenant_id, plan_slug)
            tenant = db.query(Tenant).filter(Tenant.id == UUID(str(tenant_id))).first()
            if tenant:
                tenant.stripe_customer_id = customer_id
                tenant.stripe_subscription_id = subscription_id
                tenant.subscription_status = "active"
                db.commit()

        return {"action": "subscription_created", "tenant_id": tenant_id, "plan_slug": plan_slug}

    elif event_type == "invoice.paid":
        customer_id = data.get("customer")
        tenant = db.query(Tenant).filter(Tenant.stripe_customer_id == customer_id).first()
        if tenant:
            invoice = BillingInvoice(
                tenant_id=tenant.id,
                period_start=datetime.fromtimestamp(data.get("period_start", 0), tz=timezone.utc),
                period_end=datetime.fromtimestamp(data.get("period_end", 0), tz=timezone.utc),
                amount_cents=int(data.get("amount_paid", 0)),
                status="paid",
                stripe_invoice_id=data.get("id"),
                stripe_payment_intent_id=data.get("payment_intent"),
            )
            db.add(invoice)
            db.commit()
        return {"action": "invoice_paid", "customer_id": customer_id}

    elif event_type in ("customer.subscription.deleted", "customer.subscription.paused"):
        customer_id = data.get("customer")
        tenant = db.query(Tenant).filter(Tenant.stripe_customer_id == customer_id).first()
        if tenant:
            tenant.subscription_status = "cancelled"
            db.commit()
        return {"action": "subscription_cancelled", "customer_id": customer_id}

    return {"action": "unhandled", "event_type": event_type}


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------


def _resolve_plan(db: Session, plan_id: str) -> Optional[BillingPlan]:
    try:
        return db.query(BillingPlan).filter(BillingPlan.id == UUID(str(plan_id))).first()
    except (ValueError, AttributeError):
        return db.query(BillingPlan).filter(BillingPlan.slug == str(plan_id).strip().lower()).first()


def _plan_to_dict(plan: Optional[BillingPlan]) -> Optional[Dict[str, Any]]:
    if not plan:
        return None
    features = []
    try:
        features = json.loads(plan.features or "[]")
    except Exception:
        features = []
    return {
        "id": str(plan.id),
        "slug": plan.slug,
        "name": plan.name,
        "description": plan.description,
        "monthly_price_cents": int(plan.monthly_price_cents),
        "monthly_price_usd": round(int(plan.monthly_price_cents) / 100, 2),
        "currency": plan.currency,
        "monthly_message_limit": int(plan.monthly_message_limit),
        "monthly_call_limit": int(plan.monthly_call_limit),
        "monthly_rdv_limit": int(plan.monthly_rdv_limit),
        "monthly_ai_token_limit": int(plan.monthly_ai_token_limit),
        "enabled_channels": plan.enabled_channels,
        "channels": [c.strip() for c in (plan.enabled_channels or "").split(",") if c.strip()],
        "features": features,
        "stripe_price_id": plan.stripe_price_id,
        "is_active": plan.is_active,
        "sort_order": int(plan.sort_order),
    }
