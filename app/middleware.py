import re
import time
import threading
from typing import Callable
from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
import traceback
import jwt
from .config import settings
from .logger import get_logger
from .security import verify_jwt
from .services.security_controls import get_emergency_state, is_ip_blocked

logger = get_logger(__name__)

PHONE_RE = re.compile(r"(?:(?:\+|00)\d{1,3}[\s-]?)?(?:\(?\d{1,4}\)?[\s-]?)?\d[\d\s-]{5,}\d")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

PII_PLACEHOLDER = "[REDACTED]"

# Maximum request body size (10 MB). Prevents DoS via oversized payloads.
_MAX_BODY_SIZE = 10 * 1024 * 1024

async def pii_safe_logging_middleware(request: Request, call_next: Callable):
    start = time.time()
    body = b""
    try:
        body = await request.body()
    except Exception:
        body = b""

    # Reject oversized payloads
    if len(body) > _MAX_BODY_SIZE:
        return JSONResponse({"detail": "payload_too_large"}, status_code=413)
    redacted = EMAIL_RE.sub(PII_PLACEHOLDER, PHONE_RE.sub(PII_PLACEHOLDER, body.decode(errors="ignore")))
    # Attempt to extract principal from Authorization header (optional, non-failing)
    sub = None
    roles = None
    try:
        auth = request.headers.get("authorization") or request.headers.get("Authorization")
        if auth and auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
            payload = jwt.decode(
                token,
                settings.jwt_public_key,
                algorithms=["RS256"],
                audience=settings.jwt_audience,
                issuer=settings.jwt_issuer,
                options={"verify_exp": True},
            )
            sub = payload.get("sub")
            roles = payload.get("roles")
    except Exception:
        sub = None
        roles = None

    response: Response = await call_next(request)
    duration_ms = int((time.time() - start) * 1000)
    res_len = None
    try:
        cl = response.headers.get("content-length")
        if cl is not None:
            res_len = int(cl)
    except Exception:
        res_len = None
    logger.info(
        "HTTP request processed",
        extra={
            "extra_fields": {
                "method": request.method,
                "path": request.url.path,
                "status": getattr(response, "status_code", None),
                "duration_ms": duration_ms,
                "sub": sub,
                "roles": roles,
                "req_bytes": len(body) if body else 0,
                "res_bytes": res_len,
                "request_body": redacted[:500],
            }
        }
    )
    return response


# Distributed rate limiter using Redis (with in-memory fallback)
_RL_BUCKETS: dict[str, list[float]] = {}
_RL_LOCK = threading.Lock()


def _redis_rate_limit_check(key: str, window: int, limit: int) -> tuple[bool, int]:
    """Check rate limit using Redis sliding window. Returns (allowed, remaining)."""
    try:
        from .redis_client import get_redis
        r = get_redis()
        if r is None:
            raise RuntimeError("redis_unavailable")
        redis_key = f"rl:{key}"
        now = time.time()
        pipe = r.pipeline(transaction=True)
        pipe.zremrangebyscore(redis_key, 0, now - window)
        pipe.zadd(redis_key, {str(now): now})
        pipe.zcard(redis_key)
        pipe.expire(redis_key, window + 1)
        results = pipe.execute()
        count = int(results[2])
        if count > limit:
            return False, 0
        return True, max(0, limit - count)
    except Exception:
        return True, limit  # fail-open if Redis unavailable


async def rate_limit_middleware(request: Request, call_next: Callable):
    try:
        window = max(1, int(settings.rate_limit_window_sec))
        limit = max(1, int(settings.rate_limit_max_req))
    except Exception:
        window = 60
        limit = 60

    ip = (request.client.host if request.client else "unknown")
    key = f"{ip}:{request.url.path}"

    # Try Redis-based distributed rate limiting first
    allowed, remaining = _redis_rate_limit_check(key, window, limit)

    if not allowed:
        headers = {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": "0",
            "Retry-After": str(window),
        }
        return JSONResponse({"detail": "rate_limited"}, status_code=429, headers=headers)

    response: Response = await call_next(request)
    response.headers.setdefault("X-RateLimit-Limit", str(limit))
    response.headers.setdefault("X-RateLimit-Remaining", str(remaining))
    return response


async def security_headers_middleware(request: Request, call_next: Callable):
    """Add security headers to all responses."""
    response: Response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-XSS-Protection", "1; mode=block")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(self), geolocation=()")
    is_prod = (getattr(settings, "env", "dev") or "dev").lower() in ("prod", "production")
    if is_prod:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https:; connect-src 'self' wss: https:; "
            "frame-ancestors 'none';"
        )
    return response


async def exception_logging_middleware(request: Request, call_next: Callable):
    try:
        return await call_next(request)
    except Exception as e:
        def _extract_http_exception(exc: BaseException) -> HTTPException | None:
            if isinstance(exc, HTTPException):
                return exc
            nested = getattr(exc, "exceptions", None)
            if nested:
                for child in nested:
                    hit = _extract_http_exception(child)
                    if hit is not None:
                        return hit
            return None

        http_exc = _extract_http_exception(e)
        if http_exc is not None:
            payload = {"detail": http_exc.detail if http_exc.detail is not None else "http_error"}
            headers = dict(http_exc.headers) if http_exc.headers else None
            return JSONResponse(payload, status_code=int(http_exc.status_code), headers=headers)

        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        # Print structured error log. In prod, avoid leaking details in response.
        logger.error(
            "Unhandled exception in request",
            extra={
                "extra_fields": {
                    "method": request.method,
                    "path": request.url.path,
                    "error": str(e),
                    "traceback": tb[-4000:],
                }
            },
            exc_info=True
        )
        if (getattr(settings, "env", "dev") or "dev").lower() == "prod":
            return JSONResponse({"detail": "internal_error"}, status_code=500)
        else:
            return JSONResponse({"detail": "internal_error", "error": str(e)}, status_code=500)


def _is_admin_request(request: Request) -> bool:
    token = request.cookies.get("access_token")
    if not token:
        auth = request.headers.get("authorization") or request.headers.get("Authorization")
        if auth and auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
    if not token:
        return False
    try:
        principal = verify_jwt(token)
        return "admin" in principal.roles
    except Exception:
        return False


async def security_control_middleware(request: Request, call_next: Callable):
    ip = request.client.host if request.client else "unknown"
    if is_ip_blocked(ip):
        return JSONResponse({"detail": "ip_blocked"}, status_code=403)

    emergency = get_emergency_state()
    if emergency.get("enabled"):
        path = request.url.path
        if path.startswith("/monitoring") or path.startswith("/health"):
            return await call_next(request)
        if _is_admin_request(request):
            return await call_next(request)
        return JSONResponse({"detail": "emergency_mode"}, status_code=503)

    return await call_next(request)
