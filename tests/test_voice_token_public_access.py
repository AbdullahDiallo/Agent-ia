from __future__ import annotations

import hashlib
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.db import Base, engine, open_db_session
from app.models import Tenant, BillingPlan, TenantChannel
from app.routers.voice import router as voice_router
from app.services.tenant_context import tenant_context_middleware


TENANT_ID = "00000000-0000-0000-0000-0000000000fe"
PROVIDER_KEY = "voice-public-key"
TENANT_TOKEN = "voice-public-token"


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _seed_tenant_voice_channel() -> None:
    Base.metadata.create_all(bind=engine, tables=[BillingPlan.__table__, Tenant.__table__, TenantChannel.__table__], checkfirst=True)
    db = open_db_session(allow_unscoped=True)
    try:
        tenant_uuid = UUID(TENANT_ID)
        if not db.get(Tenant, tenant_uuid):
            db.add(Tenant(id=tenant_uuid, slug="tenant-voice-public", name="Tenant Voice Public", is_active=True))
            db.flush()
        db.query(TenantChannel).filter(
            TenantChannel.provider == "twilio_voice",
            TenantChannel.provider_key == PROVIDER_KEY,
        ).delete()
        db.add(
            TenantChannel(
                tenant_id=tenant_uuid,
                provider="twilio_voice",
                provider_key=PROVIDER_KEY,
                token_hash=_hash_token(TENANT_TOKEN),
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()


def test_voice_token_allows_public_fail_closed_access_when_widget_token_not_configured(monkeypatch):
    _seed_tenant_voice_channel()

    monkeypatch.setattr(settings, "widget_public_token", None, raising=False)
    monkeypatch.setattr(settings, "twilio_account_sid", "ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", raising=False)
    monkeypatch.setattr(settings, "twilio_api_key", "SKaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", raising=False)
    monkeypatch.setattr(settings, "twilio_api_secret", "super-secret", raising=False)
    monkeypatch.setattr(settings, "twilio_twiml_app_sid", "APaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", raising=False)

    app = FastAPI()
    app.middleware("http")(tenant_context_middleware)
    app.include_router(voice_router)

    with TestClient(app) as client:
        res = client.get(
            "/voice/token",
            params={"provider_key": PROVIDER_KEY, "tenant_token": TENANT_TOKEN},
        )

    assert res.status_code == 200
    payload = res.json()
    assert isinstance(payload.get("token"), str) and payload["token"]
    assert isinstance(payload.get("identity"), str) and payload["identity"]
