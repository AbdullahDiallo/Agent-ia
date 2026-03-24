from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, status

from ..config import settings
from ..logger import get_logger
from ..redis_client import get_redis

logger = get_logger(__name__)


@dataclass
class AuthThrottleDecision:
    allowed: bool
    reason: Optional[str] = None
    retry_after: Optional[int] = None


def _safe_identifier(identifier: str) -> str:
    return (identifier or "").strip().lower()


def _safe_ip(ip_address: Optional[str]) -> str:
    return (ip_address or "unknown").strip().lower()


def _window_key(prefix: str, value: str) -> str:
    return f"auth:window:{prefix}:{value}"


def _lock_key(identifier: str) -> str:
    return f"auth:lock:{identifier}"


def _fail_key(identifier: str) -> str:
    return f"auth:fail:{identifier}"


def _get_redis():
    try:
        return get_redis()
    except Exception as exc:
        if settings.auth_security_fail_closed:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="auth_security_store_unavailable",
            ) from exc
        logger.warning(
            "Auth security Redis unavailable; falling back to degraded mode",
            extra={"extra_fields": {"error": str(exc)}},
        )
        return None


def _handle_store_operation_error(exc: Exception) -> None:
    if settings.auth_security_fail_closed:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="auth_security_store_unavailable",
        ) from exc
    logger.warning(
        "Auth security Redis operation failed; falling back to degraded mode",
        extra={"extra_fields": {"error": str(exc)}},
    )


def _increment_window(redis_client, key: str, window_sec: int) -> tuple[int, int]:
    count = int(redis_client.incr(key))
    if count == 1:
        redis_client.expire(key, window_sec)
    ttl = int(redis_client.ttl(key))
    if ttl < 0:
        ttl = window_sec
        redis_client.expire(key, window_sec)
    return count, ttl


def check_login_allowed(ip_address: Optional[str], identifier: str) -> AuthThrottleDecision:
    safe_ip = _safe_ip(ip_address)
    safe_id = _safe_identifier(identifier)
    if not safe_id:
        return AuthThrottleDecision(allowed=False, reason="invalid_identifier", retry_after=60)

    redis_client = _get_redis()
    if redis_client is None:
        return AuthThrottleDecision(allowed=True)

    try:
        lock_ttl = int(redis_client.ttl(_lock_key(safe_id)))
        if lock_ttl > 0:
            return AuthThrottleDecision(allowed=False, reason="account_locked", retry_after=lock_ttl)

        window = max(60, int(settings.auth_rate_limit_window_sec))
        ip_limit = max(1, int(settings.auth_rate_limit_ip_max))
        identifier_limit = max(1, int(settings.auth_rate_limit_identifier_max))

        ip_count, ip_ttl = _increment_window(redis_client, _window_key("ip", safe_ip), window)
        if ip_count > ip_limit:
            return AuthThrottleDecision(allowed=False, reason="rate_limited_ip", retry_after=ip_ttl)

        id_count, id_ttl = _increment_window(redis_client, _window_key("id", safe_id), window)
        if id_count > identifier_limit:
            return AuthThrottleDecision(allowed=False, reason="rate_limited_identifier", retry_after=id_ttl)
    except Exception as exc:
        _handle_store_operation_error(exc)
        return AuthThrottleDecision(allowed=True)

    return AuthThrottleDecision(allowed=True)


def register_login_failure(ip_address: Optional[str], identifier: str) -> None:
    safe_ip = _safe_ip(ip_address)
    safe_id = _safe_identifier(identifier)
    if not safe_id:
        return

    redis_client = _get_redis()
    if redis_client is None:
        return

    try:
        fail_key = _fail_key(safe_id)
        fail_count = int(redis_client.incr(fail_key))
        if fail_count == 1:
            redis_client.expire(fail_key, 24 * 3600)

        threshold = max(3, int(settings.auth_lock_threshold))
        if fail_count < threshold:
            return

        base = max(30, int(settings.auth_lock_base_sec))
        max_lock = max(base, int(settings.auth_lock_max_sec))
        level = fail_count - threshold
        cooldown = min(max_lock, base * (2 ** level))
        redis_client.setex(_lock_key(safe_id), cooldown, b"1")

        logger.warning(
            "Login lockout applied",
            extra={
                "extra_fields": {
                    "identifier": safe_id,
                    "ip": safe_ip,
                    "fail_count": fail_count,
                    "cooldown_sec": cooldown,
                }
            },
        )
    except Exception as exc:
        _handle_store_operation_error(exc)
        return


def register_login_success(identifier: str) -> None:
    safe_id = _safe_identifier(identifier)
    if not safe_id:
        return
    redis_client = _get_redis()
    if redis_client is None:
        return
    try:
        redis_client.delete(_fail_key(safe_id))
        redis_client.delete(_lock_key(safe_id))
        redis_client.delete(_window_key("id", safe_id))
    except Exception as exc:
        _handle_store_operation_error(exc)


async def apply_login_jitter(start_ts: float) -> None:
    min_ms = max(0, int(settings.auth_jitter_min_ms))
    max_ms = max(min_ms, int(settings.auth_jitter_max_ms))
    target = random.uniform(min_ms / 1000.0, max_ms / 1000.0)
    elapsed = time.perf_counter() - start_ts
    if elapsed < target:
        await asyncio.sleep(target - elapsed)
