from __future__ import annotations

import asyncio
from types import SimpleNamespace
from urllib.parse import urlencode

from starlette.requests import Request

from app.db import open_db_session
from app.routers import email_handler, whatsapp as whatsapp_router


TENANT_ID = "00000000-0000-0000-0000-00000000f610".replace("f", "0")


def _make_form_request(path: str, body: bytes, headers: dict[str, str] | None = None) -> Request:
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


def test_email_incoming_formats_reply_as_professional_email(monkeypatch):
    captured: dict[str, str] = {}

    class FakePipeline:
        def __init__(self, *_args, **_kwargs):
            pass

        async def process_inbound_text(self, **_kwargs):
            return SimpleNamespace(
                reply="Bonjour,\n\n1. Programme: Genie Logiciel\n2. Niveau: Licence Professionnelle (L3)",
                person_id="00000000-0000-0000-0000-000000000321",
            )

    class FakeEmailService:
        provider = "test-email"

        async def send_email(self, to_email: str, subject: str, html_body: str, text_body: str | None = None) -> bool:
            captured["to_email"] = to_email
            captured["subject"] = subject
            captured["html_body"] = html_body
            captured["text_body"] = text_body or ""
            return True

    monkeypatch.setattr(email_handler, "verify_webhook", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(email_handler, "ChannelAgentPipeline", FakePipeline)
    monkeypatch.setattr(email_handler, "EmailService", FakeEmailService)
    monkeypatch.setattr(email_handler.kb_service, "create_email_log", lambda *_args, **_kwargs: None)

    body = urlencode(
        {
            "from": "Candidate <candidate@example.com>",
            "subject": "Admission Genie Logiciel",
            "text": "Bonjour, pouvez-vous m'aider sur les frais ?",
        }
    ).encode("utf-8")
    request = _make_form_request(
        "/email/incoming",
        body,
        {"content-type": "application/x-www-form-urlencoded"},
    )

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        response = asyncio.run(email_handler.email_incoming(request=request, db=db))
    finally:
        db.close()

    assert response.status_code == 200
    assert captured["to_email"] == "candidate@example.com"
    assert captured["subject"] == "Re: Admission Genie Logiciel"
    assert "Programme: Genie Logiciel" in captured["text_body"]
    assert "<ul>" in captured["html_body"]
    assert "Licence Professionnelle (L3)" in captured["html_body"]


def test_twilio_whatsapp_incoming_normalizes_mobile_reply(monkeypatch):
    class FakePipeline:
        def __init__(self, *_args, **_kwargs):
            pass

        async def process_inbound_text(self, **_kwargs):
            return SimpleNamespace(
                reply="Bonjour,\n\n\nVoici les filieres.\n\n\n1. Genie Logiciel\n2. Cyber Securite",
            )

    monkeypatch.setattr(whatsapp_router, "verify_webhook", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(whatsapp_router, "ChannelAgentPipeline", FakePipeline)

    body = urlencode(
        {
            "From": "whatsapp:+221700000000",
            "Body": "Bonjour",
            "NumMedia": "0",
        }
    ).encode("utf-8")
    request = _make_form_request(
        "/whatsapp/incoming",
        body,
        {"content-type": "application/x-www-form-urlencoded"},
    )

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        response = asyncio.run(whatsapp_router.whatsapp_incoming(request=request, db=db))
    finally:
        db.close()

    payload = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "Voici les filieres." in payload
    assert "\n\n\n" not in payload
    assert "<Message>Bonjour,\n\nVoici les filieres.\n\n1. Genie Logiciel\n2. Cyber Securite</Message>" in payload
