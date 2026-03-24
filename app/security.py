from datetime import datetime, timezone
from typing import Annotated, Optional
from urllib.parse import urlsplit
import jwt
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer
from fastapi.security.http import HTTPAuthorizationCredentials
from .config import settings
from .redis_client import get_redis
from .logger import get_logger

logger = get_logger(__name__)

SAFE_HTTP_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def _is_production_environment() -> bool:
    return (getattr(settings, "env", "dev") or "dev").lower() in ("prod", "production")


def _normalize_origin(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
    except Exception:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def get_allowed_origin_set() -> set[str]:
    return {
        normalized
        for origin in (settings.allowed_origins or "").split(",")
        if (normalized := _normalize_origin(origin)) is not None
    }


def get_auth_cookie_settings() -> dict[str, object]:
    configured = str(getattr(settings, "auth_cookie_samesite", "auto") or "auto").strip().lower()
    if configured == "auto":
        samesite = "none" if _is_production_environment() else "lax"
    elif configured in {"lax", "strict", "none"}:
        samesite = configured
    else:
        raise RuntimeError("AUTH_COOKIE_SAMESITE must be one of: auto, lax, strict, none")

    explicit_secure = getattr(settings, "auth_cookie_secure", None)
    secure = bool(explicit_secure) if explicit_secure is not None else (_is_production_environment() or samesite == "none")

    if samesite == "none" and not secure:
        raise RuntimeError("AUTH_COOKIE_SECURE must be true when AUTH_COOKIE_SAMESITE is none")

    return {
        "httponly": True,
        "secure": secure,
        "samesite": samesite,
        "path": "/",
    }


def enforce_allowed_origin(request: Request, *, require_for_all_unsafe: bool = False) -> None:
    if not _is_production_environment():
        return
    if request.method.upper() in SAFE_HTTP_METHODS:
        return

    has_cookie_auth = bool(request.cookies.get("access_token") or request.cookies.get("refresh_token"))
    if not require_for_all_unsafe and not has_cookie_auth:
        return

    allowed = get_allowed_origin_set()
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="origin_not_allowed")

    origin = _normalize_origin(request.headers.get("origin"))
    if origin is None:
        origin = _normalize_origin(request.headers.get("referer"))
    if origin is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="missing_origin")
    if origin not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="origin_not_allowed")

class CookieOrBearerAuth(HTTPBearer):
    """
    Authentification qui accepte le token depuis:
    1. Cookie httpOnly (priorité)
    2. Header Authorization Bearer (fallback pour API)
    """
    async def __call__(self, request: Request) -> Optional[HTTPAuthorizationCredentials]:
        # 1. Essayer de lire depuis le cookie
        access_token = request.cookies.get("access_token")
        
        if access_token:
            enforce_allowed_origin(request)
            # Retourner comme si c'était un Bearer token
            return HTTPAuthorizationCredentials(scheme="Bearer", credentials=access_token)
        
        # 2. Fallback sur Authorization header (pour compatibilité API)
        return await super().__call__(request)

security = CookieOrBearerAuth(auto_error=True)

class Principal:
    def __init__(self, sub: str, roles: list[str], tenant_id: str):
        self.sub = sub
        self.roles = roles
        self.tenant_id = tenant_id
        self.permissions: list[str] = []


def verify_jwt(token: str) -> Principal:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_public_key,
            algorithms=["RS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
        )
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token")
    # Blacklist check (by jti)
    jti = payload.get("jti")
    if jti:
        try:
            r = get_redis()
            if r.exists(f"bl:{jti}"):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token_revoked")
        except HTTPException:
            raise
        except Exception as e:
            # If Redis is unavailable, only fail-closed when configured
            if getattr(settings, "auth_fail_closed", False):
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="auth_store_unavailable")
            # else: log and continue without blacklist check
            logger.warning(
                "Redis unavailable, skipping blacklist check",
                extra={"extra_fields": {"error": str(e)}}
            )
    exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    if exp < datetime.now(tz=timezone.utc):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token_expired")
    roles = payload.get("roles", [])
    tenant_id = payload.get("tenant_id") or getattr(settings, "default_tenant_id", "")
    if getattr(settings, "enforce_tenant_scope", True) and not tenant_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_tenant_scope")
    p = Principal(sub=payload.get("sub", "unknown"), roles=roles, tenant_id=str(tenant_id))
    perms = payload.get("permissions", [])
    if isinstance(perms, list):
        p.permissions = [str(x) for x in perms]
    return p


def require_role(required: str):
    def dependency(principal: Annotated[Principal, Depends(get_principal)]):
        # 'admin' is a super-role: always allowed
        if 'admin' in principal.roles:
            return principal
        
        # Support pour plusieurs rôles séparés par |
        if '|' in required:
            required_roles = [r.strip() for r in required.split('|')]
            if any(role in principal.roles for role in required_roles):
                return principal
        elif required in principal.roles:
            return principal
        
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    return dependency


def get_principal(creds: HTTPAuthorizationCredentials = Depends(security)) -> Principal:
    return verify_jwt(creds.credentials)


def require_permission(required: str):
    def dependency(principal: Annotated[Principal, Depends(get_principal)]):
        if required not in getattr(principal, "permissions", []) and required not in principal.roles:
            # allow role name equal to permission for coarse-grained backwards compatibility
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
        return principal
    return dependency


def require_dev_endpoint(principal: Annotated[Principal, Depends(get_principal)]):
    if not getattr(settings, "enable_dev_endpoints", False):
        # Hide dev endpoints when disabled.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if "admin" not in principal.roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    return principal
