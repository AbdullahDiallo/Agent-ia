from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import HTTPException

from app.config import settings
from app.services import auth_security
from tests.helpers import FakeRedis


def test_lockout_after_threshold(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(auth_security, "get_redis", lambda: fake_redis)

    settings.auth_rate_limit_window_sec = 600
    settings.auth_rate_limit_ip_max = 100
    settings.auth_rate_limit_identifier_max = 100
    settings.auth_lock_threshold = 5
    settings.auth_lock_base_sec = 60
    settings.auth_lock_max_sec = 600

    ip = "192.168.1.10"
    user = "user@example.com"

    for _ in range(5):
        auth_security.register_login_failure(ip, user)

    decision = auth_security.check_login_allowed(ip, user)
    assert decision.allowed is False
    assert decision.reason == "account_locked"
    assert (decision.retry_after or 0) > 0


def test_lock_reset_after_success(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(auth_security, "get_redis", lambda: fake_redis)

    settings.auth_rate_limit_window_sec = 600
    settings.auth_rate_limit_ip_max = 100
    settings.auth_rate_limit_identifier_max = 100
    settings.auth_lock_threshold = 3
    settings.auth_lock_base_sec = 60
    settings.auth_lock_max_sec = 600

    ip = "192.168.1.22"
    user = "reset@example.com"

    for _ in range(3):
        auth_security.register_login_failure(ip, user)

    blocked = auth_security.check_login_allowed(ip, user)
    assert blocked.allowed is False

    auth_security.register_login_success(user)
    reopened = auth_security.check_login_allowed(ip, user)
    assert reopened.allowed is True


def test_rate_limit_by_ip_and_identifier(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(auth_security, "get_redis", lambda: fake_redis)

    settings.auth_rate_limit_window_sec = 120
    settings.auth_rate_limit_ip_max = 2
    settings.auth_rate_limit_identifier_max = 2
    settings.auth_lock_threshold = 50

    ip = "10.10.10.10"
    user = "limit@example.com"

    assert auth_security.check_login_allowed(ip, user).allowed is True
    assert auth_security.check_login_allowed(ip, user).allowed is True

    third = auth_security.check_login_allowed(ip, user)
    assert third.allowed is False
    assert third.reason in {"rate_limited_ip", "rate_limited_identifier"}


def test_apply_login_jitter_respects_minimum_delay():
    settings.auth_jitter_min_ms = 20
    settings.auth_jitter_max_ms = 20
    start = time.perf_counter()
    asyncio.run(auth_security.apply_login_jitter(start))
    elapsed = time.perf_counter() - start
    assert elapsed >= 0.018


def test_check_login_allowed_fail_open_when_redis_operation_fails(monkeypatch):
    class BrokenRedis:
        def ttl(self, _key):
            raise RuntimeError("redis_down")

    monkeypatch.setattr(auth_security, "get_redis", lambda: BrokenRedis())
    monkeypatch.setattr(settings, "auth_security_fail_closed", False, raising=False)

    decision = auth_security.check_login_allowed("127.0.0.1", "user@example.com")
    assert decision.allowed is True


def test_check_login_allowed_fail_closed_when_redis_operation_fails(monkeypatch):
    class BrokenRedis:
        def ttl(self, _key):
            raise RuntimeError("redis_down")

    monkeypatch.setattr(auth_security, "get_redis", lambda: BrokenRedis())
    monkeypatch.setattr(settings, "auth_security_fail_closed", True, raising=False)

    with pytest.raises(HTTPException) as exc:
        auth_security.check_login_allowed("127.0.0.1", "user@example.com")
    assert exc.value.status_code == 503
    assert exc.value.detail == "auth_security_store_unavailable"
