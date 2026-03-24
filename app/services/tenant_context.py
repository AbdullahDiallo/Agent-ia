from __future__ import annotations

import hashlib
import hmac
from typing import Any
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import HTTPException, Request, status

from ..config import settings
from ..security import verify_jwt


_DEFAULT_PUBLIC_PATH_PREFIXES: tuple[str, ...] = (
    "/health",
    "/health/ready",
    "/health/live",
    "/auth/login",
    "/auth/verify-otp",
    "/auth/refresh",
    "/chat/chat",
    "/chat/message",
    "/sms/incoming",
    "/whatsapp/incoming",
    "/webhooks/meta/whatsapp",
    "/email/incoming",
    "/voice/token",
    "/voice/outbound",
    "/voice/incoming",
    "/voice/recording-status",
    "/events/call-status",
    "/school/public",
    "/school/contact-requests",
    "/uploads",
    "/billing/plans",
    "/billing/onboard",
    "/billing/check-slug",
    "/billing/webhooks/stripe",
    "/widget/init",
    "/widget/refresh",
)

_MANDATORY_PUBLIC_PATH_PREFIXES: tuple[str, ...] = (
    "/health",
    "/health/ready",
    "/health/live",
    "/auth/login",
    "/auth/verify-otp",
    "/auth/refresh",
)

_DEFAULT_FAIL_CLOSED_PUBLIC_PATH_PREFIXES: tuple[str, ...] = (
    "/sms/incoming",
    "/whatsapp/incoming",
    "/webhooks/meta/whatsapp",
    "/email/incoming",
    "/voice/incoming",
    "/voice/recording-status",
    "/events/call-status",
    "/chat/chat",
    "/chat/message",
    "/voice/token",
    "/voice/outbound",
    "/school/contact-requests",
)


def _parse_path_list(raw: str | None) -> list[str]:
    value = (raw or "").strip()
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _merge_unique_paths(*path_groups: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for group in path_groups:
        for path in group:
            candidate = (path or "").strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def _path_aliases(path: str) -> list[str]:
    normalized = (path or "").strip()
    if not normalized:
        return []
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"

    aliases: list[str] = [normalized]
    if normalized != "/" and normalized.endswith("/"):
        aliases.append(normalized.rstrip("/"))

    for prefix in ("/api",):
        if normalized == prefix:
            aliases.append("/")
        elif normalized.startswith(prefix + "/"):
            aliases.append(normalized[len(prefix) :])

    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in aliases:
        if candidate in seen:
            continue
        seen.add(candidate)
        ordered.append(candidate)
    return ordered


def _public_path_prefixes() -> list[str]:
    configured = _parse_path_list(getattr(settings, "tenant_public_paths", ""))
    if not configured:
        configured = list(_DEFAULT_PUBLIC_PATH_PREFIXES)
    # Keep auth/health public even if config is accidentally emptied or incomplete.
    return _merge_unique_paths(_MANDATORY_PUBLIC_PATH_PREFIXES, configured)


def _fail_closed_public_path_prefixes() -> list[str]:
    configured = _parse_path_list(getattr(settings, "tenant_fail_closed_public_paths", ""))
    if not configured:
        configured = list(_DEFAULT_FAIL_CLOSED_PUBLIC_PATH_PREFIXES)
    return _merge_unique_paths(configured)


def is_public_tenant_path(path: str) -> bool:
    aliases = _path_aliases(path)
    if not aliases:
        return False
    for normalized in aliases:
        for prefix in _public_path_prefixes():
            if normalized == prefix or normalized.startswith(prefix.rstrip("/") + "/"):
                return True
    return False


def is_fail_closed_public_path(path: str) -> bool:
    aliases = _path_aliases(path)
    if not aliases:
        return False
    for normalized in aliases:
        for prefix in _fail_closed_public_path_prefixes():
            if normalized == prefix or normalized.startswith(prefix.rstrip("/") + "/"):
                return True
    return False


def _extract_token(request: Request) -> str | None:
    token = request.cookies.get("access_token")
    if token:
        return token
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return None


def _extract_public_tenant_hint(request: Request) -> str | None:
    header_hint = (request.headers.get("x-tenant-id") or request.headers.get("X-Tenant-Id") or "").strip()
    query_hint = (request.query_params.get("tenant_id") or "").strip()
    candidate = header_hint or query_hint
    if not candidate:
        return None
    try:
        return str(UUID(candidate))
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_public_tenant_hint")


def _extract_public_provider(request: Request) -> str | None:
    provider = (request.headers.get("x-provider") or request.query_params.get("provider") or "").strip()
    if provider:
        return provider
    for path in _path_aliases(request.url.path):
        if path.startswith("/sms/incoming"):
            return "twilio_sms"
        if path.startswith("/whatsapp/incoming"):
            return "twilio_whatsapp"
        if path.startswith("/webhooks/meta/whatsapp"):
            return "meta_whatsapp"
        if path.startswith("/email/incoming"):
            return "email_inbound"
        if path.startswith("/voice/"):
            return "twilio_voice"
        if path.startswith("/events/call-status"):
            return "twilio_events"
        if path.startswith("/chat/"):
            return "chat_widget"
    return None


async def _extract_public_form_fields(request: Request) -> dict[str, str]:
    content_type = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        return {}
    try:
        raw_body = await request.body()
    except Exception:
        return {}
    if not raw_body:
        return {}
    try:
        parsed = parse_qs(raw_body.decode("utf-8"), keep_blank_values=True)
    except Exception:
        return {}
    values: dict[str, str] = {}
    for key, items in parsed.items():
        if not items:
            continue
        values[str(key)] = str(items[-1])
    return values


def _resolve_tenant_fail_closed(request: Request, form_fields: dict[str, str] | None = None) -> str:
    form_fields = form_fields or {}

    # --- Try widget session token first (new secure flow) ---
    # Check headers, query params, AND form fields (Twilio sends connectParams as form data)
    widget_session = (
        request.headers.get("x-widget-session")
        or request.query_params.get("widget_session")
        or str(form_fields.get("widget_session") or "")
    ).strip()
    if widget_session:
        try:
            import jwt as _jwt
            decoded = _jwt.decode(
                widget_session,
                settings.jwt_public_key,
                algorithms=["RS256"],
                audience=settings.jwt_audience,
                issuer=settings.jwt_issuer,
            )
            if decoded.get("typ") == "widget_session" and decoded.get("tenant_id"):
                return str(decoded["tenant_id"])
        except Exception:
            pass  # Fall through to legacy provider_key/tenant_token flow

    # --- Legacy flow: provider_key + tenant_token ---
    provider = _extract_public_provider(request)
    provider_key = (request.headers.get("x-provider-key") or request.query_params.get("provider_key") or "").strip()
    tenant_token = (request.headers.get("x-tenant-token") or request.query_params.get("tenant_token") or "").strip()
    if not provider_key:
        provider_key = str(form_fields.get("provider_key") or "").strip()
    if not tenant_token:
        tenant_token = str(form_fields.get("tenant_token") or "").strip()
    tenant_hint = _extract_public_tenant_hint(request)

    if not provider:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="missing_provider")
    if not provider_key:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="missing_provider_key")
    if not tenant_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="missing_tenant_token")

    from ..db import open_db_session
    from ..models import Tenant, TenantChannel

    db = open_db_session(allow_unscoped=True)
    try:
        provider_candidates = [provider]
        # Allow chat widget requests to reuse the voice channel credentials in unified web widget deployments.
        if provider == "chat_widget":
            provider_candidates.append("twilio_voice")

        channel = (
            db.query(TenantChannel)
            .join(Tenant, Tenant.id == TenantChannel.tenant_id)
            .filter(
                TenantChannel.provider.in_(provider_candidates),
                TenantChannel.provider_key == provider_key,
                TenantChannel.is_active == True,
                Tenant.is_active == True,
            )
            .first()
        )
        if not channel:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="unknown_provider_key")

        expected_hash = str(channel.token_hash or "").strip().lower()
        incoming_hash = hashlib.sha256(tenant_token.encode("utf-8")).hexdigest().lower()
        if not expected_hash or not hmac.compare_digest(incoming_hash, expected_hash):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid_tenant_token")

        tenant_id = str(channel.tenant_id)
        if tenant_hint and tenant_hint != tenant_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="cross_tenant_injection")
        return tenant_id
    finally:
        db.close()


async def tenant_context_middleware(request: Request, call_next):
    # Let CORS preflight pass through; no tenant context is needed for OPTIONS.
    if (request.method or "").upper() == "OPTIONS":
        return await call_next(request)

    tenant_id = None
    path = request.url.path
    token = _extract_token(request)
    if token:
        try:
            principal = verify_jwt(token)
            tenant_id = principal.tenant_id or None
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token")
    elif is_public_tenant_path(path):
        if is_fail_closed_public_path(path):
            form_fields = await _extract_public_form_fields(request)
            tenant_id = _resolve_tenant_fail_closed(request, form_fields=form_fields)
        else:
            tenant_id = _extract_public_tenant_hint(request)
    if getattr(settings, "enforce_tenant_scope", True) and not tenant_id and (
        not is_public_tenant_path(path) or is_fail_closed_public_path(path)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="missing_tenant_scope")
    request.state.tenant_id = tenant_id
    response = await call_next(request)
    if tenant_id:
        response.headers.setdefault("X-Tenant-Id", str(tenant_id))
    return response


def require_tenant_guard(request: Request) -> str:
    tenant_id = getattr(request.state, "tenant_id", None)
    if getattr(settings, "enforce_tenant_scope", True) and not tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="missing_tenant_scope")
    if tenant_id:
        return str(tenant_id)
    return str(settings.default_tenant_id)


def scoped_query(query: Any, model: Any, tenant_id: str):
    if hasattr(model, "tenant_id"):
        return query.filter(model.tenant_id == tenant_id)
    if getattr(settings, "enforce_tenant_scope", True):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="model_not_tenant_scoped")
    return query
