from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.config import settings
from app.services import webhook_security
from tests.helpers import FakeRedis


def _make_request(path: str, body: bytes, headers: dict[str, str]) -> Request:
    encoded_headers = [(k.lower().encode("utf-8"), v.encode("utf-8")) for k, v in headers.items()]
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


def test_meta_whatsapp_signature_valid_and_replay(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(webhook_security, "get_redis", lambda: fake_redis)
    settings.meta_whatsapp_app_secret = "meta-secret"
    settings.webhook_replay_ttl_sec = 300

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.abc123",
                                    "timestamp": str(int(time.time())),
                                    "text": {"body": "bonjour"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    raw_body = json.dumps(payload).encode("utf-8")
    signature = hmac.new(
        settings.meta_whatsapp_app_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    headers = {"X-Hub-Signature-256": f"sha256={signature}"}

    request = _make_request("/webhooks/meta/whatsapp", raw_body, headers)
    verified = webhook_security.verify_webhook(
        "meta_whatsapp",
        request=request,
        raw_body=raw_body,
        payload=payload,
    )
    assert verified.signature_valid is True
    assert verified.provider == "meta_whatsapp"
    assert verified.event_id == "wamid.abc123"

    # Same event must be rejected as replay.
    replay_request = _make_request("/webhooks/meta/whatsapp", raw_body, headers)
    with pytest.raises(HTTPException) as exc:
        webhook_security.verify_webhook(
            "meta_whatsapp",
            request=replay_request,
            raw_body=raw_body,
            payload=payload,
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "replay_detected"


def test_meta_whatsapp_signature_invalid(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(webhook_security, "get_redis", lambda: fake_redis)
    settings.meta_whatsapp_app_secret = "meta-secret"

    payload = {
        "entry": [
            {"changes": [{"value": {"messages": [{"id": "id-1", "timestamp": str(int(time.time()))}]}}]}
        ]
    }
    raw_body = json.dumps(payload).encode("utf-8")
    request = _make_request(
        "/webhooks/meta/whatsapp",
        raw_body,
        {"X-Hub-Signature-256": "sha256=invalid"},
    )
    with pytest.raises(HTTPException) as exc:
        webhook_security.verify_webhook(
            "meta_whatsapp",
            request=request,
            raw_body=raw_body,
            payload=payload,
        )
    assert exc.value.status_code == 401
    assert exc.value.detail == "invalid_signature"


def test_email_inbound_hmac_signature_valid(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(webhook_security, "get_redis", lambda: fake_redis)
    settings.email_webhook_secret = "email-secret"
    settings.email_webhook_signature_header = "X-Webhook-Signature"
    settings.email_webhook_ip_allowlist = None
    settings.mailgun_webhook_signing_key = None

    raw_body = b"from=test@example.com&subject=hello"
    timestamp = str(int(time.time()))
    nonce = "nonce-123"
    signature = hmac.new(
        settings.email_webhook_secret.encode("utf-8"),
        f"{timestamp}.{nonce}.".encode("utf-8") + raw_body,
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "X-Webhook-Timestamp": timestamp,
        "X-Webhook-Nonce": nonce,
        "X-Webhook-Signature": f"sha256={signature}",
        "X-Webhook-Event-Id": "evt-123",
    }
    request = _make_request("/email/incoming", raw_body, headers)

    verified = webhook_security.verify_webhook(
        "email_inbound",
        request=request,
        raw_body=raw_body,
        form_data={"from": "test@example.com"},
    )
    assert verified.provider == "email_inbound"
    assert verified.signature_valid is True
    assert verified.event_id == "evt-123"


def test_email_inbound_hmac_signature_invalid(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(webhook_security, "get_redis", lambda: fake_redis)
    settings.email_webhook_secret = "email-secret"
    settings.email_webhook_signature_header = "X-Webhook-Signature"
    settings.email_webhook_ip_allowlist = None
    settings.mailgun_webhook_signing_key = None

    raw_body = b"from=test@example.com"
    timestamp = str(int(time.time()))
    nonce = "nonce-456"
    request = _make_request(
        "/email/incoming",
        raw_body,
        {
            "X-Webhook-Timestamp": timestamp,
            "X-Webhook-Nonce": nonce,
            "X-Webhook-Signature": "sha256=bad",
        },
    )
    with pytest.raises(HTTPException) as exc:
        webhook_security.verify_webhook(
            "email_inbound",
            request=request,
            raw_body=raw_body,
            form_data={},
        )
    assert exc.value.status_code == 401
    assert exc.value.detail == "invalid_signature"


def test_webhook_tenant_injection_ignored(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(webhook_security, "get_redis", lambda: fake_redis)
    settings.meta_whatsapp_app_secret = "meta-secret"
    settings.default_tenant_id = "00000000-0000-0000-0000-000000000001"

    payload = {
        "tenant_id": "tenant-b",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.inject-1",
                                    "timestamp": str(int(time.time())),
                                    "text": {"body": "bonjour"},
                                }
                            ]
                        }
                    }
                ]
            }
        ],
    }
    raw_body = json.dumps(payload).encode("utf-8")
    signature = hmac.new(
        settings.meta_whatsapp_app_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    request = _make_request(
        "/webhooks/meta/whatsapp",
        raw_body,
        {
            "X-Hub-Signature-256": f"sha256={signature}",
            "X-Tenant-Id": "tenant-b",
        },
    )
    # Tenant must only come from trusted middleware context, never from payload/headers.
    request.state.tenant_id = "tenant-a"

    verified = webhook_security.verify_webhook(
        "meta_whatsapp",
        request=request,
        raw_body=raw_body,
        payload=payload,
    )
    assert verified.signature_valid is True
    assert verified.tenant == "tenant-a"
