"""Billing, plans, usage, onboarding, and Stripe endpoints."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db, open_db_session
from ..security import require_role
from ..services.billing import (
    assign_plan_to_tenant,
    create_checkout_session,
    create_plan,
    deactivate_plan,
    get_plan,
    get_tenant_plan,
    get_tenant_usage,
    get_tenant_usage_history,
    handle_stripe_webhook,
    is_stripe_configured,
    list_plans,
    seed_default_plans,
    update_plan,
)
from ..services.tenant_onboarding import (
    check_slug_available,
    create_tenant,
    get_tenant_onboarding_status,
)
from ..logger import get_logger

router = APIRouter(prefix="/billing", tags=["billing"])
logger = get_logger(__name__)


# ----------------------------------------------------------------
# Plans (DB-driven CRUD)
# ----------------------------------------------------------------


@router.get("/plans")
async def list_available_plans(db: Session = Depends(get_db)):
    """List all active plans. Public endpoint."""
    plans = list_plans(db)
    if not plans:
        plans = seed_default_plans(db)
    return {"plans": plans}


@router.get("/plans/{plan_id}")
async def get_plan_details(plan_id: str, db: Session = Depends(get_db)):
    plan = get_plan(db, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="plan_not_found")
    return plan


class PlanCreateRequest(BaseModel):
    slug: str
    name: str
    description: Optional[str] = None
    monthly_price_cents: int = 0
    currency: str = "usd"
    monthly_message_limit: int = 100
    monthly_call_limit: int = 10
    monthly_rdv_limit: int = 20
    monthly_ai_token_limit: int = 100_000
    enabled_channels: str = "chat"
    features: List[str] = []
    stripe_price_id: Optional[str] = None
    sort_order: int = 0


@router.post("/plans", dependencies=[Depends(require_role("admin"))])
async def create_new_plan(payload: PlanCreateRequest, db: Session = Depends(get_db)):
    """Create a new billing plan. Admin only."""
    return create_plan(db, data=payload.model_dump())


@router.put("/plans/{plan_id}", dependencies=[Depends(require_role("admin"))])
async def update_existing_plan(plan_id: str, payload: PlanCreateRequest, db: Session = Depends(get_db)):
    """Update an existing plan. Admin only."""
    result = update_plan(db, plan_id, data=payload.model_dump())
    if not result:
        raise HTTPException(status_code=404, detail="plan_not_found")
    return result


@router.delete("/plans/{plan_id}", dependencies=[Depends(require_role("admin"))])
async def delete_plan(plan_id: str, db: Session = Depends(get_db)):
    """Deactivate a plan (soft delete). Admin only."""
    if not deactivate_plan(db, plan_id):
        raise HTTPException(status_code=404, detail="plan_not_found")
    return {"deactivated": True}


@router.post("/plans/seed", dependencies=[Depends(require_role("admin"))])
async def seed_plans(db: Session = Depends(get_db)):
    """Seed default plans if none exist. Admin only."""
    return {"plans": seed_default_plans(db)}


# ----------------------------------------------------------------
# Tenant plan & usage
# ----------------------------------------------------------------


@router.get("/my-plan", dependencies=[Depends(require_role("admin|manager"))])
async def get_my_plan(request: Request, db: Session = Depends(get_db)):
    """Get the current tenant's plan."""
    tenant_id = str(getattr(request.state, "tenant_id", "") or "")
    if not tenant_id:
        raise HTTPException(status_code=403, detail="missing_tenant_scope")
    plan = get_tenant_plan(db, tenant_id)
    return {"plan": plan}


@router.post("/assign-plan", dependencies=[Depends(require_role("admin"))])
async def assign_plan(request: Request, plan_slug: str, db: Session = Depends(get_db)):
    """Assign a plan to the current tenant. Admin only."""
    tenant_id = str(getattr(request.state, "tenant_id", "") or "")
    if not tenant_id:
        raise HTTPException(status_code=403, detail="missing_tenant_scope")
    if not assign_plan_to_tenant(db, tenant_id, plan_slug):
        raise HTTPException(status_code=400, detail="plan_assignment_failed")
    return {"assigned": True, "plan_slug": plan_slug}


@router.get("/usage", dependencies=[Depends(require_role("admin|manager"))])
async def get_current_usage(request: Request, db: Session = Depends(get_db)):
    tenant_id = str(getattr(request.state, "tenant_id", "") or "")
    if not tenant_id:
        raise HTTPException(status_code=403, detail="missing_tenant_scope")
    return get_tenant_usage(db, tenant_id)


@router.get("/usage/history", dependencies=[Depends(require_role("admin|manager"))])
async def get_usage_history(request: Request, months: int = 6, db: Session = Depends(get_db)):
    tenant_id = str(getattr(request.state, "tenant_id", "") or "")
    if not tenant_id:
        raise HTTPException(status_code=403, detail="missing_tenant_scope")
    return {"history": get_tenant_usage_history(db, tenant_id, months=min(months, 12))}


# ----------------------------------------------------------------
# Self-service onboarding
# ----------------------------------------------------------------


class OnboardingRequest(BaseModel):
    school_name: str
    admin_email: str
    admin_password: str
    admin_first_name: str = "Admin"
    admin_last_name: str = ""
    default_language: str = "fr"
    plan_slug: str = "starter"
    slug: Optional[str] = None


@router.post("/onboard")
async def onboard_new_tenant(payload: OnboardingRequest):
    """Self-service tenant onboarding. Public endpoint."""
    if len(payload.admin_password) < 8:
        raise HTTPException(status_code=400, detail="password_too_short")

    db = open_db_session(tenant_id=None, allow_unscoped=True)
    try:
        # Ensure plans exist
        plans = list_plans(db)
        if not plans:
            seed_default_plans(db)

        result = create_tenant(
            db,
            school_name=payload.school_name,
            admin_email=payload.admin_email,
            admin_password=payload.admin_password,
            admin_first_name=payload.admin_first_name,
            admin_last_name=payload.admin_last_name,
            default_language=payload.default_language,
            plan_id=payload.plan_slug,
            slug=payload.slug,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error", "onboarding_failed"))
        return result
    finally:
        db.close()


@router.get("/onboarding-status", dependencies=[Depends(require_role("admin"))])
async def get_onboarding_status(request: Request, db: Session = Depends(get_db)):
    tenant_id = str(getattr(request.state, "tenant_id", "") or "")
    if not tenant_id:
        raise HTTPException(status_code=403, detail="missing_tenant_scope")
    return get_tenant_onboarding_status(db, tenant_id)


@router.get("/check-slug")
async def check_slug(slug: str):
    """Check slug availability. Public endpoint."""
    db = open_db_session(tenant_id=None, allow_unscoped=True)
    try:
        return {"slug": slug, "available": check_slug_available(db, slug)}
    finally:
        db.close()


# ----------------------------------------------------------------
# Stripe
# ----------------------------------------------------------------


@router.post("/checkout", dependencies=[Depends(require_role("admin"))])
async def create_checkout(request: Request, plan_slug: str, db: Session = Depends(get_db)):
    if not is_stripe_configured():
        raise HTTPException(status_code=503, detail="stripe_not_configured")
    tenant_id = str(getattr(request.state, "tenant_id", "") or "")
    if not tenant_id:
        raise HTTPException(status_code=403, detail="missing_tenant_scope")
    base_url = str(getattr(settings, "public_base_url", "http://localhost:8000") or "http://localhost:8000")
    result = create_checkout_session(
        db,
        tenant_id=tenant_id,
        plan_slug=plan_slug,
        success_url=f"{base_url}/dashboard/settings?billing=success",
        cancel_url=f"{base_url}/dashboard/settings?billing=cancelled",
    )
    if not result:
        raise HTTPException(status_code=400, detail="checkout_creation_failed")
    return result


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    db = open_db_session(tenant_id=None, allow_unscoped=True)
    try:
        result = handle_stripe_webhook(db, payload, sig_header)
        if result is None:
            raise HTTPException(status_code=400, detail="webhook_verification_failed")
        return {"received": True, "action": result.get("action")}
    finally:
        db.close()
