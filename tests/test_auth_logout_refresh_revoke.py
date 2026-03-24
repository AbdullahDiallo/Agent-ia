from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func

from app.config import settings
from app.db import Base, engine, get_db, open_db_session
from app.models import LoginAttempt, BillingPlan, Tenant, User
from app.routers.auth import router as auth_router
from app.routers import auth as auth_router_module
from app.services.auth import hash_password
from app.services import auth as auth_service
from app.services import auth_security
from app import security as security_module
from tests.helpers import FakeRedis


TENANT_ID = "00000000-0000-0000-0000-0000000000dd"
TEST_EMAIL = "logout.integration@example.com"
TEST_PASSWORD = "VerySecurePassword!123"
JWT_TEST_PRIVATE_KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEAqjDB42btTK4noWdSaLQi01Jp6IPaWDrO0LxuL7jp3tgaQmud
7JQLEYMxUuZ9v26XPMoFN3UIFRVOvngZRzcTat2Zf5N54CxvReYt/lnX0oF4RNm8
oGHd8EZg34vQqKT0h8YgQSuDlXO9KoX1nr/yAisnjcALA6cPNjiH6T7jIDylpBch
1NpS/J6Oru2aas6qstDILGJoGj79wYAJuQuS7M3Oz3Fhc5Go3MfzM3ibnu4FpPN8
HbNHz+/k22kfmskOv2Y0GbvOri5iFSA6vZpuMA4wER3bKSDHNSTDUA5qNxvAgC8G
/Iy8jbrxJNj9I1+lLV18RMPf2zhZEeRHGjSvXwIDAQABAoIBAQCRJsqGv2dOig5k
23FuuYwmPdj0JViY/XcckklLzGMy247x7UUg3FbgVctAkDLZjWHRx99RIDCHgsfJ
LTcqBPA7qcpcA5TjvCIfNKUvlMK82A2Scz4sb6vRXMUMg/uvihjAoBssWgd89Nx2
oxAMmFq4vMOcYxlBwT75GJfaN/eQqqRdGdrOBX06aiYj1c0XzrBziJ7nPzWRLg7l
Rxtrrh7NM8xQFb5xCiKgiVz8HyBVGnGgDTxYpFKA32wL9trQ9R2oGnhRHgn+pdah
JK1bDDcPtroIFBSS2e7AGiYEAWgyHh7L8gLTdL9TefkhIq0xklfZhw4UzLWt1Ptl
HNyHYVPJAoGBAN0JPvyqGArL5rzeSnFnuNNSBzsZaGa2urdxouzDXrxhVxt5I+VU
vViP0PKRWZuuo/+vCvJCYRcqnvd1t8PQWKQGjKkA5quQpWCBqv0PNMebAtNZz4En
l1j4LTL/Y+Zze3E6q4egv2rOh9m/ImHANYkzr/vjq/n13iyMmgKbLrltAoGBAMUc
iktkdORu9EOcMkk5oAmcVO7FY/4P1kGwbvAJBx8o0jT3QgcezcZiVdMiGGu4Fruh
FR+F5pPJ2euqCnRhuf31nL3gpdtuLk1gF3jULfm8v+Ahnxgi80f8VsJCbMQY5T+a
XlYXLCUdFFHwRytKUxk20ka2hCT/NBJb8yAPoPh7AoGAZ+7RDz1r0KfP9z8PAgQj
hDot7DwmOyXw5hEo6utywGGE9AYiOtN9tQbq2SQ/XlTgCHnmS8Oqo5oG5ZUUs55k
D7yEp3MlA8cf/CD8pcFgr/rTeU3hpHlZURxhJHmyH8ptYPCVd1C+sRosBtc4833N
rpX/ShHj68UQkyIJyO/vKIECgYEAwSjRwUYFYuH0TtkfUjC9Sw2/EWmwLoWYgjEC
1gkSyI85R5xSQSYHovQ0hL2xzsXMyTv2tjiCl6tD+bRdoGUwXdW2L0CZaCpWB482
ETtkfopgQaTRAlclrxJydtWfPp/i7+w3rAfzQ792bUGYjKy+OERH1fIAFz1b6u3e
mDmYlkcCgYAi3j238JzjT1RTJaTnBLyDlRweV1wxvVVXO9erlkdVcrifPoTW1tON
mqoWaAhr1MQcl78sSsv2xqppw9CjGLpP1Ie4busmfPTD+1q/ZZQQk24R4Cs6I6Xp
omV8r6BC+SJyFmmRX260l8XTYObf4rMNgh4Vpp3M7w/Vj8azw+m9sA==
-----END RSA PRIVATE KEY-----"""
JWT_TEST_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAqjDB42btTK4noWdSaLQi
01Jp6IPaWDrO0LxuL7jp3tgaQmud7JQLEYMxUuZ9v26XPMoFN3UIFRVOvngZRzcT
at2Zf5N54CxvReYt/lnX0oF4RNm8oGHd8EZg34vQqKT0h8YgQSuDlXO9KoX1nr/y
AisnjcALA6cPNjiH6T7jIDylpBch1NpS/J6Oru2aas6qstDILGJoGj79wYAJuQuS
7M3Oz3Fhc5Go3MfzM3ibnu4FpPN8HbNHz+/k22kfmskOv2Y0GbvOri5iFSA6vZpu
MA4wER3bKSDHNSTDUA5qNxvAgC8G/Iy8jbrxJNj9I1+lLV18RMPf2zhZEeRHGjSv
XwIDAQAB
-----END PUBLIC KEY-----"""


def _login_and_issue_tokens(client: TestClient, *, origin: str | None = None) -> tuple[int, str, str]:
    login_res = client.post("/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    assert login_res.status_code == 200
    user_id = int(login_res.json()["user_id"])

    headers = {"Origin": origin} if origin else None
    verify_res = client.post("/auth/verify-otp", json={"user_id": user_id, "otp": "000000"}, headers=headers)
    assert verify_res.status_code == 200
    access_token = verify_res.cookies.get("access_token")
    refresh_token = verify_res.cookies.get("refresh_token")
    assert access_token
    assert refresh_token
    return user_id, access_token, refresh_token


@pytest.fixture
def auth_client(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(auth_service, "get_redis", lambda: fake_redis)
    monkeypatch.setattr(auth_security, "get_redis", lambda: fake_redis)
    monkeypatch.setattr(security_module, "get_redis", lambda: fake_redis)
    monkeypatch.setattr(auth_router_module, "_log_login_attempt", lambda *args, **kwargs: None)

    settings.disable_otp = True
    settings.auth_jitter_min_ms = 0
    settings.auth_jitter_max_ms = 0
    settings.auth_rate_limit_ip_max = 1000
    settings.auth_rate_limit_identifier_max = 1000
    settings.auth_lock_threshold = 1000
    settings.jwt_private_key = JWT_TEST_PRIVATE_KEY
    settings.jwt_public_key = JWT_TEST_PUBLIC_KEY

    Base.metadata.create_all(
        bind=engine,
        tables=[
            BillingPlan.__table__, Tenant.__table__,
            User.__table__,
            LoginAttempt.__table__,
        ],
        checkfirst=True,
    )

    db = open_db_session(allow_unscoped=True)
    try:
        tenant_uuid = UUID(TENANT_ID)
        tenant = db.get(Tenant, tenant_uuid)
        if not tenant:
            db.add(Tenant(id=tenant_uuid, slug="tenant-logout", name="Tenant Logout", is_active=True))
            db.flush()

        user = db.query(User).filter(User.email == TEST_EMAIL, User.tenant_id == tenant_uuid).first()
        if not user:
            max_id = db.query(func.max(User.id)).scalar()
            next_id = int(max_id or 0) + 1
            user = User(
                id=next_id,
                tenant_id=tenant_uuid,
                email=TEST_EMAIL,
                password_hash=hash_password(TEST_PASSWORD),
                role_id=None,
                token_version=0,
            )
            db.add(user)
        else:
            user.password_hash = hash_password(TEST_PASSWORD)
            user.role_id = None
            user.token_version = 0
            db.add(user)
        db.commit()
    finally:
        db.close()

    app = FastAPI()
    app.include_router(auth_router)

    def _override_get_db():
        session = open_db_session(TENANT_ID)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db

    with TestClient(app) as client:
        yield client


def test_login_then_refresh_ok(auth_client: TestClient):
    _, _, refresh_token = _login_and_issue_tokens(auth_client, origin="https://frontend.example.com")
    refresh_res = auth_client.post("/auth/refresh", cookies={"refresh_token": refresh_token})
    assert refresh_res.status_code == 200
    assert refresh_res.cookies.get("refresh_token")


def test_logout_revokes_stolen_refresh_token(auth_client: TestClient):
    _, _, refresh_token = _login_and_issue_tokens(auth_client, origin="https://frontend.example.com")

    logout_res = auth_client.post("/auth/logout")
    assert logout_res.status_code == 200
    assert logout_res.json().get("logout") is True

    refresh_after_logout = auth_client.post("/auth/refresh", cookies={"refresh_token": refresh_token})
    assert refresh_after_logout.status_code == 401
    assert refresh_after_logout.json().get("detail") == "token_revoked"


def test_rotation_invalidates_previous_refresh_token(auth_client: TestClient):
    _, _, refresh_token_v1 = _login_and_issue_tokens(auth_client)

    rotate_res = auth_client.post("/auth/refresh", cookies={"refresh_token": refresh_token_v1})
    assert rotate_res.status_code == 200
    refresh_token_v2 = rotate_res.cookies.get("refresh_token")
    assert refresh_token_v2

    replay_old_refresh = auth_client.post("/auth/refresh", cookies={"refresh_token": refresh_token_v1})
    assert replay_old_refresh.status_code == 401
    assert replay_old_refresh.json().get("detail") == "token_revoked"


def test_logout_all_revokes_all_refresh_tokens(auth_client: TestClient):
    _login_and_issue_tokens(auth_client, origin="https://frontend.example.com")
    refresh_token_1 = auth_client.cookies.get("refresh_token")
    assert refresh_token_1

    _login_and_issue_tokens(auth_client, origin="https://frontend.example.com")
    refresh_token_2 = auth_client.cookies.get("refresh_token")
    assert refresh_token_2
    assert refresh_token_2 != refresh_token_1

    logout_all_res = auth_client.post("/auth/logout-all")
    assert logout_all_res.status_code == 200
    assert logout_all_res.json().get("logout_all") is True

    res_1 = auth_client.post("/auth/refresh", cookies={"refresh_token": refresh_token_1})
    res_2 = auth_client.post("/auth/refresh", cookies={"refresh_token": refresh_token_2})
    assert res_1.status_code == 401
    assert res_1.json().get("detail") == "token_revoked"
    assert res_2.status_code == 401
    assert res_2.json().get("detail") == "token_revoked"


def test_verify_otp_sets_cross_site_cookie_attributes_in_production(auth_client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "env", "production", raising=False)
    monkeypatch.setattr(settings, "allowed_origins", "https://frontend.example.com", raising=False)
    monkeypatch.setattr(settings, "auth_cookie_samesite", "auto", raising=False)
    monkeypatch.setattr(settings, "auth_cookie_secure", None, raising=False)

    login_res = auth_client.post("/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    user_id = int(login_res.json()["user_id"])

    verify_res = auth_client.post(
        "/auth/verify-otp",
        json={"user_id": user_id, "otp": "000000"},
        headers={"Origin": "https://frontend.example.com"},
    )

    assert verify_res.status_code == 200
    set_cookie_headers = verify_res.headers.get_list("set-cookie")
    assert any("SameSite=none" in header for header in set_cookie_headers)
    assert any("Secure" in header for header in set_cookie_headers)


def test_refresh_rejects_untrusted_origin_in_production(auth_client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "env", "production", raising=False)
    monkeypatch.setattr(settings, "allowed_origins", "https://frontend.example.com", raising=False)

    _, _, refresh_token = _login_and_issue_tokens(auth_client, origin="https://frontend.example.com")
    refresh_res = auth_client.post(
        "/auth/refresh",
        cookies={"refresh_token": refresh_token},
        headers={"Origin": "https://evil.example.com"},
    )

    assert refresh_res.status_code == 403
    assert refresh_res.json().get("detail") == "origin_not_allowed"


def test_cookie_authenticated_write_rejects_untrusted_origin_in_production(auth_client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "env", "production", raising=False)
    monkeypatch.setattr(settings, "allowed_origins", "https://frontend.example.com", raising=False)

    _, access_token, _ = _login_and_issue_tokens(auth_client, origin="https://frontend.example.com")
    res = auth_client.put(
        "/auth/profile",
        json={"nom": "Alice"},
        headers={"Origin": "https://evil.example.com"},
        cookies={"access_token": access_token},
    )

    assert res.status_code == 403
    assert res.json().get("detail") == "origin_not_allowed"
