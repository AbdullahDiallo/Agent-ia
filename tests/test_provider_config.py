from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from starlette.requests import Request

from app.config import settings
from app.routers import whatsapp as whatsapp_router
from app.services import email as email_module
from app.services import notification_dispatch as dispatch_module
from app.services import provider_config
from app.services.notification_dispatch import send_preferred_notification


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


def _configure_valid_twilio_whatsapp(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_provider", "twilio", raising=False)
    monkeypatch.setattr(settings, "twilio_account_sid", "AC123", raising=False)
    monkeypatch.setattr(settings, "twilio_auth_token", "secret", raising=False)
    monkeypatch.setattr(settings, "twilio_whatsapp_number", "whatsapp:+221700000000", raising=False)


def _configure_valid_meta_whatsapp(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_provider", "meta", raising=False)
    monkeypatch.setattr(settings, "meta_whatsapp_phone_number_id", "phone-id", raising=False)
    monkeypatch.setattr(settings, "meta_whatsapp_access_token", "access-token", raising=False)
    monkeypatch.setattr(settings, "meta_whatsapp_verify_token", "verify-token", raising=False)
    monkeypatch.setattr(settings, "meta_whatsapp_app_secret", "app-secret", raising=False)


def _configure_valid_brevo_email(monkeypatch) -> None:
    monkeypatch.setattr(settings, "email_provider", "brevo", raising=False)
    monkeypatch.setattr(settings, "from_email", "admission@example.com", raising=False)
    monkeypatch.setattr(settings, "brevo_api_key", "brevo-key", raising=False)


def _configure_valid_smtp_email(monkeypatch) -> None:
    monkeypatch.setattr(settings, "email_provider", "smtp", raising=False)
    monkeypatch.setattr(settings, "from_email", "admission@example.com", raising=False)
    monkeypatch.setattr(settings, "mail_host", "smtp.example.com", raising=False)
    monkeypatch.setattr(settings, "mail_username", "smtp-user", raising=False)
    monkeypatch.setattr(settings, "mail_password", "smtp-pass", raising=False)


def _clear_email_credentials(monkeypatch) -> None:
    monkeypatch.setattr(settings, "from_email", None, raising=False)
    monkeypatch.setattr(settings, "brevo_api_key", None, raising=False)
    monkeypatch.setattr(settings, "mail_host", None, raising=False)
    monkeypatch.setattr(settings, "mail_username", None, raising=False)
    monkeypatch.setattr(settings, "mail_password", None, raising=False)
    monkeypatch.setattr(settings, "gmail_smtp_user", None, raising=False)
    monkeypatch.setattr(settings, "gmail_smtp_pass", None, raising=False)
    monkeypatch.setattr(settings, "sendgrid_api_key", None, raising=False)
    monkeypatch.setattr(settings, "mail_mailer", None, raising=False)


def _clear_whatsapp_credentials(monkeypatch) -> None:
    monkeypatch.setattr(settings, "twilio_account_sid", None, raising=False)
    monkeypatch.setattr(settings, "twilio_auth_token", None, raising=False)
    monkeypatch.setattr(settings, "twilio_whatsapp_number", None, raising=False)
    monkeypatch.setattr(settings, "meta_whatsapp_phone_number_id", None, raising=False)
    monkeypatch.setattr(settings, "meta_whatsapp_access_token", None, raising=False)
    monkeypatch.setattr(settings, "meta_whatsapp_verify_token", None, raising=False)
    monkeypatch.setattr(settings, "meta_whatsapp_app_secret", None, raising=False)


def test_whatsapp_validation_twilio_is_authoritative_and_meta_credentials_are_ignored(monkeypatch, caplog):
    _clear_whatsapp_credentials(monkeypatch)
    _clear_email_credentials(monkeypatch)
    _configure_valid_twilio_whatsapp(monkeypatch)
    _configure_valid_brevo_email(monkeypatch)
    monkeypatch.setattr(settings, "meta_whatsapp_phone_number_id", "ignored-phone-id", raising=False)
    monkeypatch.setattr(settings, "meta_whatsapp_access_token", "ignored-token", raising=False)
    monkeypatch.setattr(settings, "meta_whatsapp_verify_token", "ignored-verify", raising=False)
    monkeypatch.setattr(settings, "meta_whatsapp_app_secret", "ignored-secret", raising=False)

    with caplog.at_level(logging.WARNING):
        report = provider_config.validate_provider_configuration(settings)

    assert report["whatsapp"]["provider"] == "twilio"
    assert report["whatsapp"]["configured"] is True
    assert report["whatsapp"]["ignored_credentials"] == [
        "META_WHATSAPP_PHONE_NUMBER_ID",
        "META_WHATSAPP_ACCESS_TOKEN",
        "META_WHATSAPP_VERIFY_TOKEN",
        "META_WHATSAPP_APP_SECRET",
    ]
    assert any(record.getMessage() == "provider_configuration_ignored_credentials" for record in caplog.records)


def test_whatsapp_validation_fails_when_selected_meta_provider_is_incomplete(monkeypatch):
    _clear_whatsapp_credentials(monkeypatch)
    _clear_email_credentials(monkeypatch)
    _configure_valid_brevo_email(monkeypatch)
    monkeypatch.setattr(settings, "whatsapp_provider", "meta", raising=False)
    monkeypatch.setattr(settings, "meta_whatsapp_phone_number_id", "phone-id", raising=False)
    monkeypatch.setattr(settings, "meta_whatsapp_access_token", "access-token", raising=False)
    monkeypatch.setattr(settings, "meta_whatsapp_verify_token", "verify-token", raising=False)
    monkeypatch.setattr(settings, "meta_whatsapp_app_secret", None, raising=False)

    with pytest.raises(RuntimeError, match="META_WHATSAPP_APP_SECRET"):
        provider_config.validate_provider_configuration(settings)


def test_whatsapp_validation_rejects_invalid_provider_value(monkeypatch):
    _clear_email_credentials(monkeypatch)
    _configure_valid_brevo_email(monkeypatch)
    monkeypatch.setattr(settings, "whatsapp_provider", "unsupported", raising=False)

    with pytest.raises(RuntimeError, match="WHATSAPP_PROVIDER"):
        provider_config.validate_provider_configuration(settings)


def test_email_service_uses_explicit_brevo_provider_even_when_other_credentials_are_present(monkeypatch):
    _clear_email_credentials(monkeypatch)
    _configure_valid_brevo_email(monkeypatch)
    monkeypatch.setattr(settings, "mail_host", "smtp.example.com", raising=False)
    monkeypatch.setattr(settings, "mail_username", "smtp-user", raising=False)
    monkeypatch.setattr(settings, "mail_password", "smtp-pass", raising=False)
    monkeypatch.setattr(settings, "gmail_smtp_user", "gmail-user", raising=False)
    monkeypatch.setattr(settings, "gmail_smtp_pass", "gmail-pass", raising=False)
    monkeypatch.setattr(settings, "sendgrid_api_key", "sendgrid-key", raising=False)
    monkeypatch.setattr(settings, "mail_mailer", "smtp", raising=False)

    service = email_module.EmailService()

    assert service.provider == "brevo"
    assert service.is_configured() is True
    assert service.from_email == "admission@example.com"


def test_email_service_uses_explicit_smtp_provider_even_when_brevo_credentials_are_present(monkeypatch):
    _clear_email_credentials(monkeypatch)
    _configure_valid_smtp_email(monkeypatch)
    monkeypatch.setattr(settings, "brevo_api_key", "brevo-key", raising=False)

    service = email_module.EmailService()

    assert service.provider == "smtp"
    assert service.is_configured() is True


def test_email_validation_fails_when_selected_provider_is_incomplete(monkeypatch):
    _clear_email_credentials(monkeypatch)
    _clear_whatsapp_credentials(monkeypatch)
    _configure_valid_twilio_whatsapp(monkeypatch)
    monkeypatch.setattr(settings, "email_provider", "smtp", raising=False)
    monkeypatch.setattr(settings, "from_email", "admission@example.com", raising=False)
    monkeypatch.setattr(settings, "mail_host", "smtp.example.com", raising=False)
    monkeypatch.setattr(settings, "mail_username", "smtp-user", raising=False)
    monkeypatch.setattr(settings, "mail_password", None, raising=False)

    with pytest.raises(RuntimeError, match="MAIL_PASSWORD"):
        provider_config.validate_provider_configuration(settings)


def test_email_validation_rejects_invalid_provider_value(monkeypatch):
    _clear_whatsapp_credentials(monkeypatch)
    _configure_valid_twilio_whatsapp(monkeypatch)
    monkeypatch.setattr(settings, "email_provider", "unsupported", raising=False)

    with pytest.raises(RuntimeError, match="EMAIL_PROVIDER"):
        provider_config.validate_provider_configuration(settings)


def test_notification_dispatch_uses_explicit_email_provider_selection(monkeypatch):
    _clear_email_credentials(monkeypatch)
    _configure_valid_brevo_email(monkeypatch)
    monkeypatch.setattr(settings, "mail_host", "smtp.example.com", raising=False)
    monkeypatch.setattr(settings, "mail_username", "smtp-user", raising=False)
    monkeypatch.setattr(settings, "mail_password", "smtp-pass", raising=False)

    providers: list[str] = []

    class RecordingEmailService(email_module.EmailService):
        async def send_email(self, to_email: str, subject: str, html_body: str, text_body: str | None = None) -> bool:
            providers.append(self.provider)
            return True

    class DisabledSMSService:
        provider = "test-sms"

        def is_configured(self) -> bool:
            return False

    class DisabledWhatsAppService:
        provider = "test-whatsapp"

        def is_configured(self) -> bool:
            return False

    monkeypatch.setattr(dispatch_module, "EmailService", RecordingEmailService)
    monkeypatch.setattr(dispatch_module, "_notification_already_sent", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(dispatch_module, "check_and_increment_quota", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(dispatch_module.kb_service, "create_email_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(dispatch_module.kb_service, "create_sms_log", lambda *_args, **_kwargs: None)

    person = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000444",
        tenant_id="00000000-0000-0000-0000-000000000555",
        email="candidate@example.com",
        phone="+221770000001",
    )

    result = asyncio.run(
        send_preferred_notification(
            db=SimpleNamespace(),
            person=person,
            dedupe_key="dispatch:test",
            email_subject="Admission",
            email_html="<p>Bonjour</p>",
            email_text="Bonjour",
            sms_text="Bonjour",
            wa_text="Bonjour",
            target_email="candidate@example.com",
            target_phone=None,
            sms_service=DisabledSMSService(),
            wa_service=DisabledWhatsAppService(),
            recipient_scope="applicant",
            event_type="notification.preferred.direct",
            event_id="evt-provider-selection",
        )
    )

    assert result == {"channel": "email", "sent": True}
    assert providers == ["brevo"]


def test_twilio_inbound_is_disabled_when_meta_provider_is_selected(monkeypatch):
    _clear_whatsapp_credentials(monkeypatch)
    _configure_valid_meta_whatsapp(monkeypatch)
    request = _make_request(
        "/whatsapp/incoming",
        urlencode({"From": "whatsapp:+221700000000", "Body": "Bonjour"}).encode("utf-8"),
        {"Content-Type": "application/x-www-form-urlencoded"},
    )

    response = asyncio.run(whatsapp_router.whatsapp_incoming(request=request, db=SimpleNamespace(info={})))

    assert response.status_code == 503


def test_meta_inbound_is_disabled_when_twilio_provider_is_selected(monkeypatch):
    _clear_whatsapp_credentials(monkeypatch)
    _configure_valid_twilio_whatsapp(monkeypatch)
    request = _make_request(
        "/webhooks/meta/whatsapp",
        b'{"entry":[]}',
        {"Content-Type": "application/json"},
    )

    response = asyncio.run(whatsapp_router.meta_whatsapp_incoming(request=request, db=SimpleNamespace(info={})))

    assert response.status_code == 503


def test_meta_verify_is_disabled_when_twilio_provider_is_selected(monkeypatch):
    _clear_whatsapp_credentials(monkeypatch)
    _configure_valid_twilio_whatsapp(monkeypatch)

    response = asyncio.run(
        whatsapp_router.meta_whatsapp_verify(
            hub_mode="subscribe",
            hub_verify_token="verify-token",
            hub_challenge="challenge",
        )
    )

    assert response.status_code == 503
