from types import SimpleNamespace
from uuid import UUID
import hashlib

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.config import settings
from app.db import Base, engine, open_db_session
from app.models import Tenant, BillingPlan, TenantChannel
from app.services import tenant_context
from app.services.tenant_context import require_tenant_guard, tenant_context_middleware


class DummyRequest:
    def __init__(self, tenant_id=None):
        self.state = SimpleNamespace(tenant_id=tenant_id)


TENANT_A = "00000000-0000-0000-0000-0000000000aa"
TENANT_B = "00000000-0000-0000-0000-0000000000bb"
META_KEY = "meta-key-tenant-a"
META_TOKEN = "meta-token-tenant-a"


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@pytest.fixture(scope="module", autouse=True)
def _setup_tenant_channel_tables():
    Base.metadata.create_all(
        bind=engine,
        tables=[
            BillingPlan.__table__, Tenant.__table__,
            TenantChannel.__table__,
        ],
    )
    db = open_db_session(allow_unscoped=True)
    try:
        db.query(TenantChannel).filter(TenantChannel.provider_key == META_KEY).delete()
        if not db.get(Tenant, UUID(TENANT_A)):
            db.add(Tenant(id=UUID(TENANT_A), slug="tenant-a", name="Tenant A", is_active=True))
        if not db.get(Tenant, UUID(TENANT_B)):
            db.add(Tenant(id=UUID(TENANT_B), slug="tenant-b", name="Tenant B", is_active=True))
        db.add(
            TenantChannel(
                tenant_id=UUID(TENANT_A),
                provider="meta_whatsapp",
                provider_key=META_KEY,
                token_hash=_hash_token(META_TOKEN),
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()
    yield
    db = open_db_session(allow_unscoped=True)
    try:
        db.query(TenantChannel).filter(TenantChannel.provider_key == META_KEY).delete()
        db.commit()
    finally:
        db.close()


def test_require_tenant_guard_fail_closed():
    settings.enforce_tenant_scope = True
    with pytest.raises(HTTPException) as exc:
        require_tenant_guard(DummyRequest(tenant_id=None))
    assert exc.value.status_code == 403
    assert exc.value.detail == "missing_tenant_scope"


def test_require_tenant_guard_ok():
    settings.enforce_tenant_scope = True
    tenant_id = "00000000-0000-0000-0000-000000000001"
    assert require_tenant_guard(DummyRequest(tenant_id=tenant_id)) == tenant_id


def _request_for_path(
    path: str,
    headers: list[tuple[str, str]] | None = None,
    query_string: str = "",
    method: str = "GET",
) -> Request:
    encoded_headers = [(k.lower().encode("utf-8"), v.encode("utf-8")) for k, v in (headers or [])]
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": query_string.encode("utf-8"),
        "headers": encoded_headers,
        "client": ("127.0.0.1", 5100),
        "server": ("testserver", 443),
        "scheme": "https",
    }
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    return Request(scope, receive)


@pytest.mark.asyncio
async def test_tenant_context_private_path_without_token_is_denied():
    request = _request_for_path("/dashboard/admin/stats")

    async def call_next(req):
        return SimpleNamespace(headers={})

    with pytest.raises(HTTPException) as exc:
        await tenant_context_middleware(request, call_next)
    assert exc.value.status_code == 403
    assert exc.value.detail == "missing_tenant_scope"


@pytest.mark.asyncio
async def test_tenant_context_options_preflight_bypasses_tenant_resolution():
    request = _request_for_path("/chat/chat", method="OPTIONS")

    async def call_next(req):
        return SimpleNamespace(headers={})

    response = await tenant_context_middleware(request, call_next)
    assert response.headers.get("X-Tenant-Id") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/dashboard/overview",
        "/calendar/stats",
        "/notifications/sms",
        "/kb/conversations",
        "/school/persons",
        "/school/appointments",
        "/users",
        "/agents",
        "/ai/sentiment/stats",
        "/monitoring/overview",
    ],
)
async def test_tenant_context_private_routes_fail_closed(path: str):
    request = _request_for_path(path)

    async def call_next(req):
        return SimpleNamespace(headers={})

    with pytest.raises(HTTPException) as exc:
        await tenant_context_middleware(request, call_next)
    assert exc.value.status_code == 403
    assert exc.value.detail == "missing_tenant_scope"


@pytest.mark.asyncio
async def test_tenant_context_public_path_uses_default_tenant():
    request = _request_for_path("/health")

    async def call_next(req):
        assert req.state.tenant_id is None
        return SimpleNamespace(headers={})

    response = await tenant_context_middleware(request, call_next)
    assert response.headers.get("X-Tenant-Id") is None


@pytest.mark.asyncio
async def test_tenant_context_api_prefixed_public_path_is_allowed():
    request = _request_for_path("/api/auth/login")

    async def call_next(req):
        assert req.state.tenant_id is None
        return SimpleNamespace(headers={})

    response = await tenant_context_middleware(request, call_next)
    assert response.headers.get("X-Tenant-Id") is None


@pytest.mark.asyncio
async def test_tenant_context_login_stays_public_when_public_paths_env_empty(monkeypatch):
    monkeypatch.setattr(settings, "tenant_public_paths", "", raising=False)
    request = _request_for_path("/auth/login")

    async def call_next(req):
        assert req.state.tenant_id is None
        return SimpleNamespace(headers={})

    response = await tenant_context_middleware(request, call_next)
    assert response.headers.get("X-Tenant-Id") is None


@pytest.mark.asyncio
async def test_tenant_context_public_path_uses_query_tenant_hint():
    hinted_tenant = "00000000-0000-0000-0000-0000000000cc"
    request = _request_for_path("/health", query_string=f"tenant_id={hinted_tenant}")

    async def call_next(req):
        assert req.state.tenant_id == hinted_tenant
        return SimpleNamespace(headers={})

    response = await tenant_context_middleware(request, call_next)
    assert response.headers.get("X-Tenant-Id") == hinted_tenant


@pytest.mark.asyncio
async def test_tenant_context_public_path_invalid_tenant_hint_rejected():
    request = _request_for_path("/health", query_string="tenant_id=not-a-uuid")

    async def call_next(req):
        return SimpleNamespace(headers={})

    with pytest.raises(HTTPException) as exc:
        await tenant_context_middleware(request, call_next)
    assert exc.value.status_code == 400
    assert exc.value.detail == "invalid_public_tenant_hint"


@pytest.mark.asyncio
async def test_tenant_context_token_wins_over_public_path(monkeypatch):
    request = _request_for_path("/email/incoming", headers=[("authorization", "Bearer token")])

    class Principal:
        tenant_id = "00000000-0000-0000-0000-0000000000AA"

    monkeypatch.setattr(tenant_context, "verify_jwt", lambda _token: Principal())

    async def call_next(req):
        assert req.state.tenant_id == Principal.tenant_id
        return SimpleNamespace(headers={})

    response = await tenant_context_middleware(request, call_next)
    assert response.headers.get("X-Tenant-Id") == Principal.tenant_id


@pytest.mark.asyncio
async def test_fail_closed_webhook_unknown_provider_key_rejected():
    request = _request_for_path(
        "/webhooks/meta/whatsapp",
        query_string="provider_key=unknown&tenant_token=bad-token",
    )

    async def call_next(req):
        return SimpleNamespace(headers={})

    with pytest.raises(HTTPException) as exc:
        await tenant_context_middleware(request, call_next)
    assert exc.value.status_code == 403
    assert exc.value.detail == "unknown_provider_key"


@pytest.mark.asyncio
async def test_fail_closed_webhook_api_prefixed_unknown_provider_key_rejected():
    request = _request_for_path(
        "/api/webhooks/meta/whatsapp",
        query_string="provider_key=unknown&tenant_token=bad-token",
    )

    async def call_next(req):
        return SimpleNamespace(headers={})

    with pytest.raises(HTTPException) as exc:
        await tenant_context_middleware(request, call_next)
    assert exc.value.status_code == 403
    assert exc.value.detail == "unknown_provider_key"


@pytest.mark.asyncio
async def test_fail_closed_webhook_invalid_tenant_token_rejected():
    request = _request_for_path(
        "/webhooks/meta/whatsapp",
        query_string=f"provider_key={META_KEY}&tenant_token=wrong-token",
    )

    async def call_next(req):
        return SimpleNamespace(headers={})

    with pytest.raises(HTTPException) as exc:
        await tenant_context_middleware(request, call_next)
    assert exc.value.status_code == 403
    assert exc.value.detail == "invalid_tenant_token"


@pytest.mark.asyncio
async def test_fail_closed_webhook_cross_tenant_injection_rejected():
    request = _request_for_path(
        "/webhooks/meta/whatsapp",
        query_string=f"provider_key={META_KEY}&tenant_token={META_TOKEN}&tenant_id={TENANT_B}",
    )

    async def call_next(req):
        return SimpleNamespace(headers={})

    with pytest.raises(HTTPException) as exc:
        await tenant_context_middleware(request, call_next)
    assert exc.value.status_code == 403
    assert exc.value.detail == "cross_tenant_injection"


@pytest.mark.asyncio
async def test_fail_closed_webhook_valid_credentials_resolve_tenant_context():
    request = _request_for_path(
        "/webhooks/meta/whatsapp",
        query_string=f"provider_key={META_KEY}&tenant_token={META_TOKEN}",
    )

    async def call_next(req):
        assert req.state.tenant_id == TENANT_A
        return SimpleNamespace(headers={})

    response = await tenant_context_middleware(request, call_next)
    assert response.headers.get("X-Tenant-Id") == TENANT_A


@pytest.mark.asyncio
async def test_tenant_context_token_wins_over_meta_webhook_public_path(monkeypatch):
    request = _request_for_path("/webhooks/meta/whatsapp", headers=[("authorization", "Bearer token")])

    class Principal:
        tenant_id = "00000000-0000-0000-0000-0000000000BB"

    monkeypatch.setattr(tenant_context, "verify_jwt", lambda _token: Principal())

    async def call_next(req):
        assert req.state.tenant_id == Principal.tenant_id
        return SimpleNamespace(headers={})

    response = await tenant_context_middleware(request, call_next)
    assert response.headers.get("X-Tenant-Id") == Principal.tenant_id


@pytest.mark.asyncio
async def test_tenant_context_invalid_token_rejected(monkeypatch):
    request = _request_for_path("/dashboard/admin/stats", headers=[("authorization", "Bearer invalid")])

    def _raise(_token):
        raise HTTPException(status_code=401, detail="invalid_token")

    monkeypatch.setattr(tenant_context, "verify_jwt", _raise)

    async def call_next(req):
        return SimpleNamespace(headers={})

    with pytest.raises(HTTPException) as exc:
        await tenant_context_middleware(request, call_next)
    assert exc.value.status_code == 401
