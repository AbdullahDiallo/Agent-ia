from __future__ import annotations

from typing import Optional, Dict

from ..models import Person
from ..services.email import EmailService
from ..services.sms import SMSService
from ..services.whatsapp import WhatsAppService
from ..services import kb as kb_service
from ..services.tenant_governance import check_and_increment_quota
from ..config import settings
from ..logger import get_logger

logger = get_logger(__name__)


def _normalize_email(value: Optional[str]) -> Optional[str]:
    email = str(value or "").strip().lower()
    return email or None


def _normalize_phone(value: Optional[str]) -> Optional[str]:
    phone = str(value or "").strip()
    return phone or None


def _notification_already_sent(db, *, dedupe_key: str) -> bool:
    if not dedupe_key:
        return False
    if kb_service.email_log_exists(db, dedupe_key=dedupe_key):
        return True
    if kb_service.sms_log_exists(db, dedupe_key=dedupe_key):
        return True
    if kb_service.sms_log_exists(db, dedupe_key=f"{dedupe_key}:wa"):
        return True
    return False


async def send_preferred_notification(
    *,
    db,
    person: Person,
    dedupe_key: str,
    email_subject: str,
    email_html: str,
    email_text: str,
    sms_text: str,
    wa_text: str,
    target_email: Optional[str] = None,
    target_phone: Optional[str] = None,
    email_service: Optional[EmailService] = None,
    sms_service: Optional[SMSService] = None,
    wa_service: Optional[WhatsAppService] = None,
    recipient_scope: str = "person",
    assigned_agent_email: Optional[str] = None,
    event_type: Optional[str] = None,
    event_id: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """Send a notification using WhatsApp -> Email -> SMS priority.

    Returns {"channel": "whatsapp"|"email"|"sms"|None, "sent": bool}.
    """
    result = {"channel": None, "sent": False}
    if _notification_already_sent(db, dedupe_key=dedupe_key):
        return {"channel": None, "sent": False, "reason": "already_sent"}

    tenant_id = str(getattr(person, "tenant_id", None) or settings.default_tenant_id)
    if not check_and_increment_quota(db, tenant_id=tenant_id, metric="messages", increment=1):
        return {"channel": None, "sent": False, "reason": "quota_exceeded"}

    email_service = email_service or EmailService()
    sms_service = sms_service or SMSService()
    wa_service = wa_service or WhatsAppService()
    resolved_email = _normalize_email(target_email or person.email)
    resolved_phone = _normalize_phone(target_phone or person.phone)
    applicant_email = _normalize_email(target_email)
    applicant_phone = _normalize_phone(target_phone)
    normalized_agent_email = _normalize_email(assigned_agent_email)

    if recipient_scope == "applicant" and (
        (resolved_email and not applicant_email) or (resolved_phone and not applicant_phone)
    ):
        logger.warning(
            "notification_applicant_contact_fallback",
            extra={
                "extra_fields": {
                    "event_type": str(event_type or ""),
                    "event_id": str(event_id or ""),
                    "recipient_scope": str(recipient_scope or ""),
                    "applicant_email": applicant_email,
                    "applicant_phone": applicant_phone,
                    "assigned_agent_email": normalized_agent_email,
                    "final_resolved_target_email": resolved_email,
                    "final_resolved_target_phone": resolved_phone,
                    "person_id": str(getattr(person, "id", "") or ""),
                }
            },
        )

    logger.info(
        "Preferred notification recipient resolved",
        extra={
            "extra_fields": {
                "event_type": str(event_type or ""),
                "event_id": str(event_id or ""),
                "recipient_scope": str(recipient_scope or ""),
                "applicant_email": applicant_email,
                "applicant_phone": applicant_phone,
                "assigned_agent_email": normalized_agent_email,
                "final_resolved_target_email": resolved_email,
                "final_resolved_target_phone": resolved_phone,
                "person_id": str(getattr(person, "id", "") or ""),
            }
        },
    )

    if recipient_scope == "applicant" and resolved_email and normalized_agent_email and resolved_email == normalized_agent_email:
        logger.warning(
            "recipient_scope_violation",
            extra={
                "extra_fields": {
                    "event_type": str(event_type or ""),
                    "event_id": str(event_id or ""),
                    "recipient_scope": "applicant",
                    "applicant_email": applicant_email,
                    "applicant_phone": applicant_phone,
                    "assigned_agent_email": normalized_agent_email,
                    "final_resolved_target_email": resolved_email,
                    "final_resolved_target_phone": resolved_phone,
                    "person_id": str(getattr(person, "id", "") or ""),
                }
            },
        )
        return {"channel": None, "sent": False, "reason": "recipient_scope_violation"}

    if not resolved_email and not resolved_phone:
        return {"channel": None, "sent": False, "reason": "no_recipient_available"}

    # WhatsApp first
    if resolved_phone and wa_service.is_configured():
        try:
            sent = await wa_service.send_message(resolved_phone, wa_text)
        except Exception:
            sent = False
        kb_service.create_sms_log(
            db,
            person_id=str(person.id),
            contenu=wa_text,
            statut="sent" if sent else "failed",
            provider_id=f"{dedupe_key}:wa|{wa_service.provider or 'unknown'}",
        )
        if sent:
            return {"channel": "whatsapp", "sent": True}

    # Email fallback
    if resolved_email and email_service.is_configured():
        try:
            sent = await email_service.send_email(
                to_email=resolved_email,
                subject=email_subject,
                html_body=email_html,
                text_body=email_text,
            )
        except Exception:
            sent = False
        kb_service.create_email_log(
            db,
            person_id=str(person.id),
            sujet=email_subject,
            statut="sent" if sent else "failed",
            provider_id=f"{dedupe_key}|{email_service.provider or 'unknown'}",
        )
        if sent:
            return {"channel": "email", "sent": True}

    # SMS fallback
    if resolved_phone and sms_service.is_configured():
        try:
            sent = await sms_service.send_sms(resolved_phone, sms_text)
        except Exception:
            sent = False
        kb_service.create_sms_log(
            db,
            person_id=str(person.id),
            contenu=sms_text,
            statut="sent" if sent else "failed",
            provider_id=f"{dedupe_key}|{sms_service.provider or 'unknown'}",
        )
        if sent:
            return {"channel": "sms", "sent": True}

    return result
