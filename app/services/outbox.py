from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from ..config import settings
from ..logger import get_logger
from ..models import Agent, OutboxEvent, Person, RendezVous, User
from . import kb as kb_service
from .email import EmailService
from .notification_dispatch import send_preferred_notification
from .sms import SMSService
from .whatsapp import WhatsAppService
from .email import EmailSendResult

logger = get_logger(__name__)

EVENT_NOTIFICATION_PREFERRED = "notification.preferred"
EVENT_APPOINTMENT_STAFF_NOTIFICATION = "appointment.staff_notification"
EVENT_APPOINTMENT_CALENDAR_SYNC = "appointment.calendar_sync"
EVENT_APPOINTMENT_CRM_SYNC = "appointment.crm_sync"
EVENT_APPOINTMENT_NOTIFICATION_EMAIL = "appointment.notification.email"
EVENT_APPOINTMENT_NOTIFICATION_SMS = "appointment.notification.sms"


def _normalize_email(value: Any) -> Optional[str]:
    email = str(value or "").strip().lower()
    return email or None


def _normalize_phone(value: Any) -> Optional[str]:
    phone = str(value or "").strip()
    return phone or None


def _resolve_assigned_agent_email(
    db: Session,
    *,
    row: OutboxEvent,
    payload: Dict[str, Any],
    appointment: Optional[RendezVous] = None,
) -> Optional[str]:
    explicit_email = _normalize_email(payload.get("assigned_agent_email"))
    if explicit_email:
        return explicit_email
    rdv = appointment
    if rdv is None:
        appointment_id = payload.get("appointment_id") or (row.aggregate_id if row.aggregate_type == "appointment" else None)
        if appointment_id:
            try:
                rdv = db.get(RendezVous, UUID(str(appointment_id)))
            except Exception:
                rdv = None
    if not rdv or not rdv.agent_id:
        return None
    agent = db.get(Agent, rdv.agent_id)
    if not agent or not agent.user_id:
        return None
    agent_user = db.get(User, agent.user_id)
    return _normalize_email(getattr(agent_user, "email", None))


def enqueue_event(
    db: Session,
    *,
    tenant_id: str,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: Dict[str, Any],
    available_at: Optional[datetime] = None,
) -> OutboxEvent:
    row = OutboxEvent(
        tenant_id=UUID(str(tenant_id)),
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=json.dumps(payload, ensure_ascii=False),
        status="pending",
        available_at=available_at or datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def enqueue_appointment_notification_events(
    db: Session,
    *,
    tenant_id: str,
    appointment_id: str,
    person_id: Optional[str],
    action: str,
    email_payload: Optional[Dict[str, Any]] = None,
    sms_payload: Optional[Dict[str, Any]] = None,
) -> list[OutboxEvent]:
    events: list[OutboxEvent] = []
    if email_payload:
        dedupe_key = str(email_payload.get("dedupe_key") or "").strip()
        if dedupe_key:
            kb_service.upsert_email_log_pending(
                db,
                dedupe_key=dedupe_key,
                person_id=person_id,
                sujet=str(email_payload.get("subject") or "").strip() or None,
                recipient=_normalize_email(email_payload.get("recipient")),
                provider_name=str(email_payload.get("provider_name") or "").strip() or None,
            )
        events.append(
            enqueue_event(
                db,
                tenant_id=tenant_id,
                event_type=EVENT_APPOINTMENT_NOTIFICATION_EMAIL,
                aggregate_type="appointment",
                aggregate_id=appointment_id,
                payload={"action": action, **email_payload},
            )
        )
    if sms_payload:
        dedupe_key = str(sms_payload.get("dedupe_key") or "").strip()
        if dedupe_key:
            kb_service.upsert_sms_log_pending(
                db,
                dedupe_key=dedupe_key,
                person_id=person_id,
                contenu=str(sms_payload.get("body") or "").strip() or None,
                recipient=_normalize_phone(sms_payload.get("recipient")),
                provider_name=str(sms_payload.get("provider_name") or "").strip() or None,
            )
        events.append(
            enqueue_event(
                db,
                tenant_id=tenant_id,
                event_type=EVENT_APPOINTMENT_NOTIFICATION_SMS,
                aggregate_type="appointment",
                aggregate_id=appointment_id,
                payload={"action": action, **sms_payload},
            )
        )
    return events


def _retry_delay_seconds(attempts: int) -> int:
    base = max(5, int(settings.outbox_retry_base_sec))
    return min(3600, base * (2 ** max(0, attempts - 1)))


def claim_due_events(db: Session, *, limit: Optional[int] = None) -> list[OutboxEvent]:
    now = datetime.now(timezone.utc)
    size = limit if limit is not None else max(1, int(settings.outbox_batch_size))
    return (
        db.query(OutboxEvent)
        .filter(
            OutboxEvent.status.in_(["pending", "failed"]),
            OutboxEvent.available_at <= now,
        )
        .order_by(OutboxEvent.created_at.asc())
        .limit(size)
        .all()
    )


def mark_event_sent(db: Session, row: OutboxEvent) -> None:
    row.status = "sent"
    row.sent_at = datetime.now(timezone.utc)
    row.last_error = None
    db.add(row)
    db.commit()


def mark_event_failed(db: Session, row: OutboxEvent, error: str) -> None:
    row.status = "failed"
    row.attempts = int(row.attempts or 0) + 1
    row.last_error = error[:1500]
    row.available_at = datetime.now(timezone.utc) + timedelta(seconds=_retry_delay_seconds(int(row.attempts)))
    db.add(row)
    db.commit()


async def process_notification_event(db: Session, row: OutboxEvent) -> bool:
    payload = json.loads(row.payload or "{}")
    recipient_scope = str(payload.get("recipient_scope") or "applicant").strip() or "applicant"
    if recipient_scope != "applicant":
        raise ValueError("invalid_notification_recipient_scope")
    person_id = payload.get("applicant_person_id") or payload.get("person_id")
    dedupe_key = payload.get("dedupe_key")
    if not person_id or not dedupe_key:
        raise ValueError("invalid_notification_payload")

    person = db.get(Person, UUID(str(person_id)))
    if not person:
        raise ValueError("person_not_found")
    applicant_email = _normalize_email(payload.get("applicant_email"))
    applicant_phone = _normalize_phone(payload.get("applicant_phone"))
    if not applicant_email and not applicant_phone:
        logger.warning(
            "notification_preferred_legacy_contact_fallback",
            extra={
                "extra_fields": {
                    "event_type": row.event_type,
                    "event_id": str(row.id),
                    "recipient_scope": recipient_scope,
                    "applicant_email": applicant_email,
                    "applicant_phone": applicant_phone,
                    "aggregate_id": str(row.aggregate_id or ""),
                    "person_id": str(person.id),
                }
            },
        )
    final_target_email = applicant_email or _normalize_email(getattr(person, "email", None))
    final_target_phone = applicant_phone or _normalize_phone(getattr(person, "phone", None))
    assigned_agent_email = _resolve_assigned_agent_email(db, row=row, payload=payload)

    logger.info(
        "Outbox notification.preferred resolved recipient",
        extra={
            "extra_fields": {
                "event_type": row.event_type,
                "event_id": str(row.id),
                "recipient_scope": recipient_scope,
                "applicant_email": applicant_email,
                "applicant_phone": applicant_phone,
                "assigned_agent_email": assigned_agent_email,
                "final_resolved_target_email": final_target_email,
                "final_resolved_target_phone": final_target_phone,
                "person_id": str(person.id),
            }
        },
    )

    email_service = EmailService()
    sms_service = SMSService()
    wa_service = WhatsAppService()

    # Envoyer le PDF en pièce jointe si disponible
    pdf_path = str(payload.get("pdf_path") or "").strip()
    pdf_attachment = None
    if pdf_path:
        try:
            from pathlib import Path
            from .email import EmailAttachment
            pdf_file = Path(pdf_path)
            if pdf_file.exists():
                pdf_attachment = EmailAttachment(
                    filename="Confirmation_RDV.pdf",
                    content=pdf_file.read_bytes(),
                    content_type="application/pdf",
                )
        except Exception as pdf_exc:
            logger.warning(f"Failed to load PDF attachment: {pdf_exc}")

    result = await send_preferred_notification(
        db=db,
        person=person,
        dedupe_key=str(dedupe_key),
        email_subject=str(payload.get("email_subject") or ""),
        email_html=str(payload.get("email_html") or ""),
        email_text=str(payload.get("email_text") or ""),
        sms_text=str(payload.get("sms_text") or ""),
        wa_text=str(payload.get("wa_text") or ""),
        target_email=final_target_email,
        target_phone=final_target_phone,
        email_service=email_service,
        sms_service=sms_service,
        wa_service=wa_service,
        recipient_scope=recipient_scope,
        assigned_agent_email=assigned_agent_email,
        event_type=row.event_type,
        event_id=str(row.id),
        pdf_attachment=pdf_attachment,
    )
    reason = str(result.get("reason") or "")
    if reason in {"no_recipient_available", "recipient_scope_violation"}:
        logger.info(
            "Outbox applicant notification skipped",
            extra={
                "extra_fields": {
                    "event_id": str(row.id),
                    "appointment_id": str(row.aggregate_id or ""),
                    "person_id": str(person.id),
                    "reason": reason,
                    "recipient_scope": recipient_scope,
                    "applicant_email": applicant_email,
                    "applicant_phone": applicant_phone,
                    "assigned_agent_email": assigned_agent_email,
                    "final_resolved_target_email": final_target_email,
                    "final_resolved_target_phone": final_target_phone,
                }
            },
        )
    return bool(result.get("sent")) or reason in {"already_sent", "no_recipient_available", "recipient_scope_violation"}


async def process_appointment_email_event(db: Session, row: OutboxEvent) -> bool:
    payload = json.loads(row.payload or "{}")
    dedupe_key = str(payload.get("dedupe_key") or "").strip()
    recipient = _normalize_email(payload.get("recipient"))
    subject = str(payload.get("subject") or "").strip()
    html_body = str(payload.get("html_body") or "").strip()
    text_body = str(payload.get("text_body") or "").strip() or None

    if dedupe_key and kb_service.email_log_exists(db, dedupe_key=dedupe_key):
        return True
    if not recipient:
        kb_service.mark_email_log_status(
            db,
            dedupe_key=dedupe_key or None,
            statut="failed",
            last_error="missing_email_recipient",
        )
        return True
    if not subject or not html_body:
        kb_service.mark_email_log_status(
            db,
            dedupe_key=dedupe_key or None,
            statut="failed",
            recipient=recipient,
            last_error="invalid_email_payload",
        )
        return True

    email_service = EmailService()
    result = await email_service.send_email_result(recipient, subject, html_body, text_body)
    if result.ok:
        kb_service.mark_email_log_status(
            db,
            dedupe_key=dedupe_key or None,
            statut="sent",
            provider_id=result.provider_id,
            provider_name=result.provider,
            recipient=recipient,
        )
        return True

    kb_service.mark_email_log_status(
        db,
        dedupe_key=dedupe_key or None,
        statut="failed",
        provider_name=result.provider,
        recipient=recipient,
        last_error=result.error,
    )
    return False


async def process_appointment_sms_event(db: Session, row: OutboxEvent) -> bool:
    payload = json.loads(row.payload or "{}")
    dedupe_key = str(payload.get("dedupe_key") or "").strip()
    recipient = _normalize_phone(payload.get("recipient"))
    body = str(payload.get("body") or "").strip()

    if dedupe_key and kb_service.sms_log_exists(db, dedupe_key=dedupe_key):
        return True
    if not recipient:
        kb_service.mark_sms_log_status(
            db,
            dedupe_key=dedupe_key or None,
            statut="failed",
            last_error="missing_sms_recipient",
        )
        return True
    if not body:
        kb_service.mark_sms_log_status(
            db,
            dedupe_key=dedupe_key or None,
            statut="failed",
            recipient=recipient,
            last_error="invalid_sms_payload",
        )
        return True

    sms_service = SMSService()
    result = await sms_service.send_sms_result(recipient, body)
    if result.ok:
        kb_service.mark_sms_log_status(
            db,
            dedupe_key=dedupe_key or None,
            statut="sent",
            provider_id=result.provider_id,
            provider_name=result.provider,
            recipient=recipient,
        )
        return True

    kb_service.mark_sms_log_status(
        db,
        dedupe_key=dedupe_key or None,
        statut="failed",
        provider_name=result.provider,
        recipient=recipient,
        last_error=result.error,
    )
    return False


def _appointment_from_payload(db: Session, payload: Dict[str, Any]) -> RendezVous:
    appointment_id = payload.get("appointment_id")
    if not appointment_id:
        raise ValueError("appointment_id_required")
    rdv = db.get(RendezVous, UUID(str(appointment_id)))
    if not rdv:
        raise ValueError("appointment_not_found")
    return rdv


async def process_staff_notification_event(db: Session, row: OutboxEvent) -> bool:
    payload = json.loads(row.payload or "{}")
    recipient_scope = str(payload.get("recipient_scope") or "staff").strip() or "staff"
    if recipient_scope != "staff":
        raise ValueError("invalid_staff_notification_recipient_scope")
    rdv = _appointment_from_payload(db, payload)

    recipients: list[str] = []
    admin_email = str(settings.admin_alert_email or "").strip()
    if admin_email:
        recipients.append(admin_email)
    explicit_recipients = payload.get("staff_recipient_emails")
    if isinstance(explicit_recipients, list):
        for value in explicit_recipients:
            recipient = str(value or "").strip()
            if recipient and recipient not in recipients:
                recipients.append(recipient)
    agent_email = _normalize_email(payload.get("assigned_agent_email"))
    if agent_email and agent_email not in recipients:
        recipients.append(agent_email)
    applicant_email = _normalize_email(payload.get("person_email"))
    if not applicant_email and rdv.person_id:
        person = db.get(Person, rdv.person_id)
        applicant_email = _normalize_email(getattr(person, "email", None)) if person else None
    if applicant_email:
        filtered_recipients = [recipient for recipient in recipients if _normalize_email(recipient) != applicant_email]
        if len(filtered_recipients) != len(recipients):
            logger.warning(
                "recipient_scope_violation",
                extra={
                    "extra_fields": {
                        "event_type": row.event_type,
                        "event_id": str(row.id),
                        "recipient_scope": recipient_scope,
                        "applicant_email": applicant_email,
                        "applicant_phone": None,
                        "assigned_agent_email": agent_email,
                        "final_resolved_target_email": ",".join(recipients),
                        "final_resolved_target_phone": None,
                        "appointment_id": str(rdv.id),
                    }
                },
            )
            recipients = filtered_recipients
    logger.info(
        "Outbox appointment.staff_notification resolved recipient",
        extra={
            "extra_fields": {
                "event_type": row.event_type,
                "event_id": str(row.id),
                "recipient_scope": recipient_scope,
                "applicant_email": applicant_email,
                "applicant_phone": None,
                "assigned_agent_email": agent_email,
                "final_resolved_target_email": ",".join(recipients),
                "final_resolved_target_phone": None,
                "appointment_id": str(rdv.id),
            }
        },
    )
    if not recipients:
        logger.info(
            "Outbox staff notification skipped",
            extra={"extra_fields": {"appointment_id": str(rdv.id), "reason": "no_recipient_configured"}},
        )
        return True

    email_service = EmailService()
    if not email_service.is_configured():
        logger.info(
            "Outbox staff notification skipped",
            extra={"extra_fields": {"appointment_id": str(rdv.id), "reason": "email_service_not_configured"}},
        )
        return True

    subject = str(payload.get("subject") or "Nouveau rendez-vous admission")
    html_body = str(payload.get("html_body") or "").strip()
    text_body = str(payload.get("text_body") or "").strip() or None
    if not html_body:
        raise ValueError("staff_notification_body_required")

    sent_any = False
    provider_name = email_service.provider or "unknown"
    for recipient in recipients:
        sent = await email_service.send_email(
            to_email=recipient,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
        )
        kb_service.create_email_log(
            db,
            person_id=None,
            sujet=subject,
            statut="sent" if sent else "failed",
            provider_id=f"staff_notify:{rdv.id}:{recipient}|{provider_name}",
        )
        sent_any = sent_any or bool(sent)

    if not sent_any:
        raise RuntimeError("staff_notification_send_failed")
    return True


async def process_calendar_sync_event(db: Session, row: OutboxEvent) -> bool:
    payload = json.loads(row.payload or "{}")
    rdv = _appointment_from_payload(db, payload)
    if str(getattr(rdv, "event_id", "") or "").strip():
        return True
    if not settings.google_calendar_id or not settings.google_credentials_json_base64:
        logger.info(
            "Outbox calendar sync skipped",
            extra={"extra_fields": {"appointment_id": str(rdv.id), "reason": "google_calendar_not_configured"}},
        )
        return True

    from . import calendar as calendar_service

    summary = str(payload.get("summary") or "Rendez-vous admission").strip()
    description = str(payload.get("description") or "").strip() or None
    event = calendar_service.create_event(
        None,
        summary,
        rdv.start_at,
        rdv.end_at,
        attendees=None,
        description=description,
    )
    event_id = str((event or {}).get("id") or "").strip()
    if event_id:
        rdv.event_id = event_id
        db.add(rdv)
        db.commit()
    return True


async def process_crm_sync_event(db: Session, row: OutboxEvent) -> bool:
    payload = json.loads(row.payload or "{}")
    rdv = _appointment_from_payload(db, payload)
    logger.info(
        "Outbox CRM sync hook acknowledged",
        extra={
            "extra_fields": {
                "appointment_id": str(rdv.id),
                "person_id": str(payload.get("person_id") or ""),
                "track_name": str(payload.get("track_name") or ""),
            }
        },
    )
    return True


async def process_outbox_batch(db: Session, *, limit: Optional[int] = None) -> Dict[str, int]:
    rows = claim_due_events(db, limit=limit)
    stats = {"processed": 0, "sent": 0, "failed": 0}
    for row in rows:
        stats["processed"] += 1
        try:
            handled = False
            if row.event_type == EVENT_NOTIFICATION_PREFERRED:
                handled = await process_notification_event(db, row)
            elif row.event_type == EVENT_APPOINTMENT_NOTIFICATION_EMAIL:
                handled = await process_appointment_email_event(db, row)
            elif row.event_type == EVENT_APPOINTMENT_NOTIFICATION_SMS:
                handled = await process_appointment_sms_event(db, row)
            elif row.event_type == EVENT_APPOINTMENT_STAFF_NOTIFICATION:
                handled = await process_staff_notification_event(db, row)
            elif row.event_type == EVENT_APPOINTMENT_CALENDAR_SYNC:
                handled = await process_calendar_sync_event(db, row)
            elif row.event_type == EVENT_APPOINTMENT_CRM_SYNC:
                handled = await process_crm_sync_event(db, row)
            else:
                mark_event_failed(db, row, "unsupported_event_type")
                stats["failed"] += 1
                continue

            if handled:
                mark_event_sent(db, row)
                stats["sent"] += 1
            else:
                mark_event_failed(db, row, "provider_send_failed")
                stats["failed"] += 1
        except Exception as exc:
            mark_event_failed(db, row, str(exc))
            stats["failed"] += 1
            logger.error(
                "Outbox event processing failed",
                extra={"extra_fields": {"event_id": str(row.id), "error": str(exc)}},
            )
    return stats
