from __future__ import annotations

import os
from typing import Optional

import redis

from .config import settings

_redis: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    global _redis
    if _redis is not None:
        return _redis
    url = os.getenv("REDIS_URL") or getattr(settings, "redis_url", None) or "redis://127.0.0.1:6379/0"
    use_ssl = bool(getattr(settings, "redis_ssl", False)) or url.startswith("rediss://")
    password = getattr(settings, "redis_password", None)
    kwargs = {
        "decode_responses": False,
        "socket_timeout": 5,  # Timeout pour éviter blocage
        "socket_connect_timeout": 5,  # Timeout de connexion
        "retry_on_timeout": True,  # Retry automatique sur timeout
    }
    # Prefer SSL via connection_class when requested or scheme is rediss
    if use_ssl:
        kwargs["connection_class"] = redis.SSLConnection
    if password and "@" not in url:
        kwargs["password"] = password
    client = redis.from_url(url, **kwargs)
    _redis = client
    return client
