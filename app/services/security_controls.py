from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from ..logger import get_logger
from ..redis_client import get_redis

logger = get_logger(__name__)

EMERGENCY_KEY = "security:emergency_mode"
BLOCKED_IPS_KEY = "security:blocked_ips"
BLOCKED_IP_TTL_PREFIX = "security:blocked_ip_ttl:"


def get_emergency_state() -> Dict[str, Any]:
    try:
        r = get_redis()
        raw = r.get(EMERGENCY_KEY)
        if not raw:
            return {"enabled": False}
        try:
            payload = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
            data = json.loads(payload)
            if isinstance(data, dict):
                data.setdefault("enabled", True)
                return data
        except Exception:
            return {"enabled": True}
    except Exception as e:
        logger.error(f"Emergency state read failed: {e}")
    return {"enabled": False}


def set_emergency_mode(enabled: bool, reason: Optional[str] = None, actor: Optional[str] = None) -> bool:
    try:
        r = get_redis()
        if enabled:
            payload = {
                "enabled": True,
                "reason": reason,
                "actor": actor,
                "at": datetime.now(timezone.utc).isoformat(),
            }
            r.set(EMERGENCY_KEY, json.dumps(payload))
        else:
            r.delete(EMERGENCY_KEY)
        return True
    except Exception as e:
        logger.error(f"Emergency state update failed: {e}")
        return False


def block_ip(ip: str, reason: Optional[str] = None, ttl_minutes: Optional[int] = None) -> bool:
    if not ip:
        return False
    try:
        r = get_redis()
        now = datetime.now(timezone.utc)
        expires_at = None
        if ttl_minutes and ttl_minutes > 0:
            expires_at = (now + timedelta(minutes=ttl_minutes)).isoformat()
            r.setex(f"{BLOCKED_IP_TTL_PREFIX}{ip}", int(ttl_minutes * 60), "1")
        entry = {
            "ip": ip,
            "reason": reason,
            "blocked_at": now.isoformat(),
            "expires_at": expires_at,
        }
        r.hset(BLOCKED_IPS_KEY, ip, json.dumps(entry))
        return True
    except Exception as e:
        logger.error(f"Failed to block ip: {e}")
        return False


def unblock_ip(ip: str) -> bool:
    if not ip:
        return False
    try:
        r = get_redis()
        r.hdel(BLOCKED_IPS_KEY, ip)
        r.delete(f"{BLOCKED_IP_TTL_PREFIX}{ip}")
        return True
    except Exception as e:
        logger.error(f"Failed to unblock ip: {e}")
        return False


def _cleanup_expired(ip: str, entry: Dict[str, Any]) -> bool:
    expires_at = entry.get("expires_at")
    if not expires_at:
        return False
    try:
        exp = datetime.fromisoformat(str(expires_at))
    except Exception:
        return False
    if exp <= datetime.now(timezone.utc):
        unblock_ip(ip)
        return True
    return False


def is_ip_blocked(ip: str) -> bool:
    if not ip:
        return False
    try:
        r = get_redis()
        if r.exists(f"{BLOCKED_IP_TTL_PREFIX}{ip}"):
            return True
        raw = r.hget(BLOCKED_IPS_KEY, ip)
        if not raw:
            return False
        try:
            payload = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
            entry = json.loads(payload)
            if isinstance(entry, dict):
                if _cleanup_expired(ip, entry):
                    return False
        except Exception:
            pass
        return True
    except Exception:
        return False


def list_blocked_ips() -> list[Dict[str, Any]]:
    items: list[Dict[str, Any]] = []
    try:
        r = get_redis()
        rows = r.hgetall(BLOCKED_IPS_KEY)
        for key, raw in rows.items():
            ip = key.decode() if isinstance(key, bytes) else str(key)
            try:
                payload = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
                entry = json.loads(payload)
                if isinstance(entry, dict):
                    if _cleanup_expired(ip, entry):
                        continue
                    items.append(entry)
                else:
                    items.append({"ip": ip, "reason": None, "blocked_at": None, "expires_at": None})
            except Exception:
                items.append({"ip": ip, "reason": None, "blocked_at": None, "expires_at": None})
    except Exception as e:
        logger.error(f"Failed to list blocked ips: {e}")
    return items
