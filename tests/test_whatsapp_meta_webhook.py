from __future__ import annotations

import asyncio
import json
import time
from uuid import UUID

from starlette.requests import Request

from app.config import settings
from app.db import Base, engine, open_db_session
from app.models import Conversation, Message
from app.routers import whatsapp as whatsapp_router


TENANT_A = "00000000-0000-0000-0000-00000000000a"


def _make_request(path: str, body: bytes, headers: dict[str, str] | None = None) -> Request:
    encoded_headers = [(k.lower().encode("utf-8"), v.encode("utf-8")) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": encoded_headers,
        "client": ("127.0.0.1", 5100),
        "server": ("testserver", 443),
        "scheme": "https",
    }
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


def test_meta_webhook_uses_scoped_tenant_without_unboundlocal(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_provider", "meta", raising=False)
    Base.metadata.create_all(bind=engine, tables=[Conversation.__table__, Message.__table__], checkfirst=True)
    tenant_uuid = UUID(TENANT_A)
    db_cleanup = open_db_session(TENANT_A)
    try:
        db_cleanup.query(Message).filter(Message.tenant_id == tenant_uuid).delete()
        db_cleanup.query(Conversation).filter(Conversation.tenant_id == tenant_uuid).delete()
        db_cleanup.commit()
    finally:
        db_cleanup.close()

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

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.test-1",
                                    "timestamp": str(int(time.time())),
                                    "from": "+221700000000",
                                    "text": {"body": "Bonjour, je veux un rendez-vous"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    raw_body = json.dumps(payload).encode("utf-8")
    request = _make_request("/webhooks/meta/whatsapp", raw_body)

    db = open_db_session(TENANT_A)
    try:
        result = asyncio.run(whatsapp_router.meta_whatsapp_incoming(request=request, db=db))
    finally:
        db.close()

    assert result == {"status": "ok"}
    assert captured["tenant_id"] == TENANT_A
    assert captured["db_tenant_id"] == TENANT_A
