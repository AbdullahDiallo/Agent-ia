"""Widget authentication endpoint.

Provides a secure way for embedded widgets to authenticate without
exposing permanent tenant credentials in client-side JavaScript.

Flow:
1. School embeds widget with only their public embed_key (not secret)
2. Widget calls POST /widget/init with embed_key + origin domain
3. Backend verifies embed_key exists AND origin is in allowed_origins
4. Backend returns a short-lived session_token (JWT, 30min TTL)
5. Widget uses session_token for all subsequent /chat and /voice requests
6. No permanent secrets ever appear in client-side code

This replaces the current pattern of putting provider_key + tenant_token
directly in the widget JavaScript configuration.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional

import jwt
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..db import open_db_session
from ..logger import get_logger
from ..models import Tenant, TenantChannel

router = APIRouter(prefix="/widget", tags=["widget-auth"])
logger = get_logger(__name__)

# Session token TTL: 30 minutes (widget refreshes automatically)
_SESSION_TTL_SEC = 1800


class WidgetInitRequest(BaseModel):
    embed_key: str  # Public key, safe to expose in HTML


class WidgetInitResponse(BaseModel):
    session_token: str
    expires_in: int
    tenant_name: str
    tenant_slug: str


class WidgetRefreshRequest(BaseModel):
    session_token: str


@router.post("/init", response_model=WidgetInitResponse)
async def widget_init(payload: WidgetInitRequest, request: Request):
    """Initialize a widget session.

    The embed_key is a public identifier for the tenant's widget integration.
    The backend verifies it exists and optionally checks the Origin/Referer header
    against the tenant's allowed domains.

    Returns a short-lived JWT session token that the widget uses for all API calls.
    """
    embed_key = (payload.embed_key or "").strip()
    if not embed_key:
        raise HTTPException(status_code=400, detail="missing_embed_key")

    # Extract origin for domain validation
    origin = (
        request.headers.get("origin")
        or request.headers.get("referer")
        or ""
    ).strip()

    db = open_db_session(allow_unscoped=True)
    try:
        # Look up the channel by embed_key.
        # Priority: dedicated widget_embed provider, then any provider (backwards compat).
        base_query = (
            db.query(TenantChannel)
            .join(Tenant, Tenant.id == TenantChannel.tenant_id)
            .filter(
                TenantChannel.provider_key == embed_key,
                TenantChannel.is_active == True,  # noqa: E712
                Tenant.is_active == True,  # noqa: E712
            )
        )
        channel = (
            base_query.filter(TenantChannel.provider == "widget_embed").first()
            or base_query.first()
        )

        if not channel:
            logger.warning(
                "Widget init: unknown embed_key",
                extra={"extra_fields": {"embed_key": embed_key[:20], "origin": origin[:200]}},
            )
            raise HTTPException(status_code=403, detail="invalid_embed_key")

        tenant = db.query(Tenant).filter(Tenant.id == channel.tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=403, detail="tenant_not_found")

        tenant_id = str(tenant.id)
        tenant_name = str(tenant.name or "")
        tenant_slug = str(tenant.slug or "")

        # Validate origin against channel-level allowed_origins (per-tenant),
        # then fall back to global ALLOWED_ORIGINS setting.
        if origin:
            _origin_normalized = origin.rstrip("/").split("?")[0].split("#")[0]

            # Per-channel allowed origins (highest priority)
            _channel_origins = [
                o.strip().rstrip("/")
                for o in (getattr(channel, "allowed_origins", None) or "").split(",")
                if o.strip()
            ]
            # Global allowed origins (fallback)
            _global_origins = [
                o.strip().rstrip("/")
                for o in (settings.allowed_origins or "").split(",")
                if o.strip()
            ]
            _all_allowed = _channel_origins or _global_origins

            if _all_allowed and _origin_normalized not in _all_allowed:
                env_lower = (settings.env or "").lower()
                if env_lower in ("prod", "production"):
                    logger.warning(
                        "Widget init: origin not allowed (rejected)",
                        extra={"extra_fields": {
                            "embed_key": embed_key[:20],
                            "origin": origin[:200],
                            "channel_origins": bool(_channel_origins),
                        }},
                    )
                    raise HTTPException(status_code=403, detail="origin_not_allowed")
                else:
                    logger.warning(
                        "Widget init: origin not in allowed list (allowed in dev)",
                        extra={"extra_fields": {
                            "embed_key": embed_key[:20],
                            "origin": origin[:200],
                        }},
                    )

        logger.info(
            "Widget session initialized",
            extra={
                "extra_fields": {
                    "tenant_id": tenant_id,
                    "tenant_slug": tenant_slug,
                    "embed_key": embed_key[:20] + "...",
                    "origin": origin[:200],
                }
            },
        )

        # Generate short-lived session token
        now = int(time.time())
        token_payload = {
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": now,
            "exp": now + _SESSION_TTL_SEC,
            "typ": "widget_session",
            "tenant_id": tenant_id,
            "embed_key": embed_key,
            "origin": origin[:500],
        }
        session_token = jwt.encode(token_payload, settings.jwt_private_key, algorithm="RS256")

        return WidgetInitResponse(
            session_token=session_token,
            expires_in=_SESSION_TTL_SEC,
            tenant_name=tenant_name,
            tenant_slug=tenant_slug,
        )

    finally:
        db.close()


@router.post("/refresh")
async def widget_refresh(payload: WidgetRefreshRequest, request: Request):
    """Refresh an expiring widget session token.

    The widget calls this before the current token expires to get a new one
    without re-sending the embed_key.
    """
    try:
        decoded = jwt.decode(
            payload.session_token,
            settings.jwt_public_key,
            algorithms=["RS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="session_expired")
    except Exception:
        raise HTTPException(status_code=401, detail="invalid_session_token")

    if decoded.get("typ") != "widget_session":
        raise HTTPException(status_code=401, detail="invalid_token_type")

    tenant_id = decoded.get("tenant_id")
    embed_key = decoded.get("embed_key", "")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="invalid_session")

    # Issue new token
    now = int(time.time())
    new_payload = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + _SESSION_TTL_SEC,
        "typ": "widget_session",
        "tenant_id": tenant_id,
        "embed_key": embed_key,
        "origin": decoded.get("origin", ""),
    }
    new_token = jwt.encode(new_payload, settings.jwt_private_key, algorithm="RS256")

    return {"session_token": new_token, "expires_in": _SESSION_TTL_SEC}
