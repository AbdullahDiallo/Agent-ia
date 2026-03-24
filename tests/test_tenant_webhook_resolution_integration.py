from __future__ import annotations

import hashlib
import time
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.db import Base, engine, open_db_session
from app.models import Conversation, Message, Tenant, BillingPlan, TenantChannel
from app.routers import whatsapp as whatsapp_router
from app.services.tenant_context import tenant_context_middleware


TENANT_A = "00000000-0000-0000-0000-0000000000ea"
PROVIDER_KEY = "meta-provider-key-a"
TENANT_TOKEN = "meta-tenant-token-a"


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def test_meta_webhook_valid_provider_key_resolves_tenant_and_returns_200(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_provider", "meta", raising=False)

    Base.metadata.create_all(
        bind=engine,
        tables=[BillingPlan.__table__, Tenant.__table__, TenantChannel.__table__, Conversation.__table__, Message.__table__],
        checkfirst=True,
    )
    db = open_db_session(allow_unscoped=True)
    try:
        tenant_uuid = UUID(TENANT_A)
        if not db.get(Tenant, tenant_uuid):
            db.add(Tenant(id=tenant_uuid, slug="tenant-meta-a", name="Tenant Meta A", is_active=True))
            db.flush()
        db.query(TenantChannel).filter(TenantChannel.provider_key == PROVIDER_KEY).delete()
        db.add(
            TenantChannel(
                tenant_id=tenant_uuid,
                provider="meta_whatsapp",
                provider_key=PROVIDER_KEY,
                token_hash=_token_hash(TENANT_TOKEN),
                is_active=True,
            )
        )
        db.query(Message).filter(Message.tenant_id == tenant_uuid).delete()
        db.query(Conversation).filter(Conversation.tenant_id == tenant_uuid).delete()
        db.commit()
    finally:
        db.close()

    captured: dict[str, str] = {}

    class FakeLLMService:
        async def generate_reply_with_tools(self, _body, session_state, db_session):
            captured["tenant_id"] = str(session_state.get("tenant_id"))
            captured["db_tenant_id"] = str(getattr(db_session, "info", {}).get("tenant_id"))
            return "ok"

    class FakeWhatsAppService:
        async def send_message(self, _to, _message):
            return True

    monkeypatch.setattr(whatsapp_router, "verify_webhook", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(whatsapp_router, "LLMService", FakeLLMService)
    monkeypatch.setattr(whatsapp_router, "WhatsAppService", FakeWhatsAppService)
    monkeypatch.setattr(
        whatsapp_router,
        "handle_create_or_get_person",
        lambda *_args, **_kwargs: {"success": True, "person_id": "00000000-0000-0000-0000-000000000123"},
    )
    monkeypatch.setattr(whatsapp_router, "_log_whatsapp_conversation", lambda *_args, **_kwargs: None)

    app = FastAPI()
    app.middleware("http")(tenant_context_middleware)
    app.include_router(whatsapp_router.router)

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.integration.1",
                                    "timestamp": str(int(time.time())),
                                    "from": "+221700000000",
                                    "text": {"body": "Bonjour"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }

    with TestClient(app) as client:
        res = client.post(
            "/webhooks/meta/whatsapp",
            params={"provider_key": PROVIDER_KEY, "tenant_token": TENANT_TOKEN},
            json=payload,
        )

    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
    assert captured["tenant_id"] == TENANT_A
    assert captured["db_tenant_id"] == TENANT_A
