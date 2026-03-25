"""Self-service tenant onboarding.

Handles the complete flow of creating a new school tenant:
1. Create tenant record
2. Create admin user
3. Initialize default settings
4. Set up default channels
5. Seed admission rules skeleton
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from ..config import settings
from ..logger import get_logger
from ..models import (
    Tenant,
    TenantChannel,
    TenantSettings,
    User,
    Role,
)
from .agents import sync_role_satellite
from .auth import hash_password
from .tenant_governance import ensure_default_tenant_settings

logger = get_logger(__name__)


def _generate_slug(name: str) -> str:
    """Generate a URL-safe slug from a school name."""
    import re
    import unicodedata
    normalized = unicodedata.normalize("NFKD", name.lower())
    ascii_slug = "".join(c for c in normalized if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_slug).strip("-")
    return slug[:60] or f"school-{secrets.token_hex(4)}"


def _generate_provider_key(slug: str) -> str:
    """Generate a unique provider key for tenant channel authentication."""
    return f"pk_{slug}_{secrets.token_hex(8)}"


def _generate_tenant_token() -> str:
    """Generate a secure tenant token for webhook/widget authentication."""
    return f"tt_{secrets.token_hex(16)}"


def check_slug_available(db: Session, slug: str) -> bool:
    """Check if a tenant slug is available."""
    existing = db.query(Tenant).filter(Tenant.slug == slug).first()
    return existing is None


def create_tenant(
    db: Session,
    *,
    school_name: str,
    admin_email: str,
    admin_password: str,
    admin_first_name: str = "Admin",
    admin_last_name: str = "",
    default_language: str = "fr",
    plan_id: str = "starter",
    slug: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new tenant with admin user and default configuration.

    Returns:
        Dict with tenant_id, admin_user_id, provider_key, tenant_token, and setup status.
    """
    # 1. Generate or validate slug
    effective_slug = slug or _generate_slug(school_name)
    if not check_slug_available(db, effective_slug):
        return {"success": False, "error": "slug_already_taken", "slug": effective_slug}

    # Check email uniqueness
    existing_user = db.query(User).filter(User.email == admin_email.lower().strip()).first()
    if existing_user:
        return {"success": False, "error": "email_already_registered"}

    try:
        # 2. Create tenant
        tenant = Tenant(
            id=uuid4(),
            slug=effective_slug,
            name=school_name,
            is_active=True,
        )
        db.add(tenant)
        db.flush()

        tenant_id = str(tenant.id)

        # 3. Create admin user
        admin_role = db.query(Role).filter(Role.name == "admin").first()
        if not admin_role:
            admin_role = Role(name="admin", description="Administrator")
            db.add(admin_role)
            db.flush()

        admin_user = User(
            id=uuid4(),
            email=admin_email.lower().strip(),
            password_hash=hash_password(admin_password),
            first_name=admin_first_name,
            last_name=admin_last_name or school_name,
            role_id=admin_role.id,
            is_active=True,
            tenant_id=tenant.id,
        )
        db.add(admin_user)
        db.flush()

        # Synchroniser les tables satellites (agents/managers/viewers)
        sync_role_satellite(db, admin_user)

        # 4. Create default tenant settings
        from .billing import get_plan, PLANS
        plan = get_plan(plan_id) or PLANS["starter"]

        tenant_settings = TenantSettings(
            tenant_id=tenant.id,
            default_language=default_language,
            enabled_channels=",".join(plan.get("channels", ["chat"])),
            monthly_rdv_limit=plan.get("monthly_rdv_limit", 200),
            monthly_message_limit=plan.get("monthly_message_limit", 2000),
            monthly_call_limit=plan.get("monthly_call_limit", 200),
        )
        db.add(tenant_settings)

        # 5. Create default channel configurations
        provider_key = _generate_provider_key(effective_slug)
        tenant_token = _generate_tenant_token()

        # Chat channel (always enabled)
        chat_channel = TenantChannel(
            tenant_id=tenant.id,
            provider="chat",
            provider_key=provider_key,
            token_hash=hashlib.sha256(tenant_token.encode()).hexdigest(),
            is_active=True,
        )
        db.add(chat_channel)

        # WhatsApp channel placeholder
        if "whatsapp" in plan.get("channels", []):
            wa_channel = TenantChannel(
                tenant_id=tenant.id,
                provider="whatsapp",
                provider_key=f"wa_{provider_key}",
                token_hash=hashlib.sha256(tenant_token.encode()).hexdigest(),
                is_active=False,  # Requires manual configuration
            )
            db.add(wa_channel)

        db.commit()

        logger.info(
            "Tenant onboarded successfully",
            extra={
                "extra_fields": {
                    "tenant_id": tenant_id,
                    "slug": effective_slug,
                    "school_name": school_name,
                    "plan_id": plan_id,
                    "admin_email": admin_email,
                }
            },
        )

        return {
            "success": True,
            "tenant_id": tenant_id,
            "slug": effective_slug,
            "admin_user_id": str(admin_user.id),
            "provider_key": provider_key,
            "tenant_token": tenant_token,
            "plan_id": plan_id,
            "enabled_channels": plan.get("channels", ["chat"]),
            "next_steps": [
                "Configure school catalog via /school/catalog API or dashboard",
                "Set up admission requirements via /school/admission API",
                "Configure WhatsApp/SMS providers in tenant settings",
                "Embed the chat widget on your school website",
            ],
        }

    except Exception as e:
        db.rollback()
        logger.error(f"Tenant onboarding failed: {e}", exc_info=True)
        return {"success": False, "error": "onboarding_failed", "detail": str(e)}


def get_tenant_onboarding_status(db: Session, tenant_id: str) -> Dict[str, Any]:
    """Check the onboarding completion status for a tenant."""
    from ..models import SchoolDepartment, SchoolProgram, SchoolTrack, SchoolAdmissionRequirement

    tenant_uuid = UUID(str(tenant_id))
    tenant = db.query(Tenant).filter(Tenant.id == tenant_uuid).first()
    if not tenant:
        return {"error": "tenant_not_found"}

    dept_count = db.query(SchoolDepartment).filter(SchoolDepartment.tenant_id == tenant_uuid).count()
    prog_count = db.query(SchoolProgram).filter(SchoolProgram.tenant_id == tenant_uuid).count()
    track_count = db.query(SchoolTrack).filter(SchoolTrack.tenant_id == tenant_uuid).count()
    req_count = db.query(SchoolAdmissionRequirement).filter(SchoolAdmissionRequirement.tenant_id == tenant_uuid).count()
    channel_count = db.query(TenantChannel).filter(TenantChannel.tenant_id == tenant_uuid, TenantChannel.is_active == True).count()
    user_count = db.query(User).filter(User.tenant_id == tenant_uuid, User.is_active == True).count()

    steps = {
        "admin_user_created": user_count > 0,
        "catalog_configured": dept_count > 0 and prog_count > 0 and track_count > 0,
        "admission_rules_set": req_count > 0,
        "channels_active": channel_count > 0,
        "departments": dept_count,
        "programs": prog_count,
        "tracks": track_count,
        "admission_requirements": req_count,
        "active_channels": channel_count,
        "users": user_count,
    }

    completed = sum(1 for k in ("admin_user_created", "catalog_configured", "admission_rules_set", "channels_active") if steps[k])
    steps["completion_percent"] = int((completed / 4) * 100)
    steps["is_ready"] = completed == 4

    return steps
