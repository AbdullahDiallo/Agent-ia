from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from app.services import notification_dispatch as dispatch_module
from app.services.notification_dispatch import send_preferred_notification


def test_send_preferred_notification_logs_applicant_contact_fallback(monkeypatch, caplog):
    sent: dict[str, str] = {}

    class FakeEmailService:
        provider = "test-email"

        def is_configured(self) -> bool:
            return True

        async def send_email(self, to_email: str, subject: str, html_body: str, text_body: str | None = None) -> bool:
            sent["to_email"] = to_email
            return True

    class FakeSMSService:
        provider = "test-sms"

        def is_configured(self) -> bool:
            return False

    class FakeWhatsAppService:
        provider = "test-whatsapp"

        def is_configured(self) -> bool:
            return False

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

    with caplog.at_level(logging.WARNING):
        result = asyncio.run(
            send_preferred_notification(
                db=SimpleNamespace(),
                person=person,
                dedupe_key="direct:test",
                email_subject="Admission",
                email_html="<p>Bonjour</p>",
                email_text="Bonjour",
                sms_text="Bonjour",
                wa_text="Bonjour",
                target_email=None,
                target_phone=None,
                email_service=FakeEmailService(),
                sms_service=FakeSMSService(),
                wa_service=FakeWhatsAppService(),
                recipient_scope="applicant",
                event_type="notification.preferred.direct",
                event_id="evt-test",
            )
        )

    assert result == {"channel": "email", "sent": True}
    assert sent["to_email"] == "candidate@example.com"
    assert any(record.getMessage() == "notification_applicant_contact_fallback" for record in caplog.records)
