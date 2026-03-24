from __future__ import annotations

from app.services import whatsapp as whatsapp_module


def test_twilio_whatsapp_service_requires_dedicated_whatsapp_sender(monkeypatch):
    monkeypatch.setattr(whatsapp_module.settings, "whatsapp_provider", "twilio", raising=False)
    monkeypatch.setattr(whatsapp_module.settings, "twilio_account_sid", "AC123", raising=False)
    monkeypatch.setattr(whatsapp_module.settings, "twilio_auth_token", "secret", raising=False)
    monkeypatch.setattr(whatsapp_module.settings, "twilio_phone_number", "+221700000000", raising=False)
    monkeypatch.setattr(whatsapp_module.settings, "twilio_whatsapp_number", None, raising=False)
    monkeypatch.setattr(whatsapp_module, "TwilioClient", lambda *_args, **_kwargs: object())

    captured: list[str] = []

    def fake_warning(message, *args, **kwargs):
        captured.append(str(message))

    monkeypatch.setattr(whatsapp_module.logger, "warning", fake_warning)

    svc = whatsapp_module.WhatsAppService()

    assert svc.is_configured() is False
    assert any("TWILIO_WHATSAPP_NUMBER" in msg for msg in captured)
