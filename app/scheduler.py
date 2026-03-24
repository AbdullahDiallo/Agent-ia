"""Scheduler pour les tâches automatiques (rappels, suivis, alertes)."""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

from .logger import get_logger
from .config import settings
from .db import open_db_session
from .services.email import EmailService
from .services.sms import SMSService
from .services.whatsapp import WhatsAppService
from .services.notification_dispatch import send_preferred_notification
from .services.outbox import enqueue_event, process_outbox_batch
from .services import kb as kb_service
from .models import RendezVous, Person, Tenant

logger = get_logger(__name__)

STATUS_CREATED = "created"
STATUS_CONFIRMED = "confirmed"
STATUS_REMINDER_SENT = "reminder_sent"
STATUS_COMPLETED = "completed"
STATUS_FOLLOWUP_SENT = "follow_up_sent"
STATUS_CANCELLED = "cancelled"

ACTIVE_STATUSES = {STATUS_CREATED, STATUS_CONFIRMED, STATUS_REMINDER_SENT, "pending"}
REMINDER_STATUSES = {STATUS_CONFIRMED, STATUS_REMINDER_SENT}

# Instance globale du scheduler
_scheduler: Optional[AsyncIOScheduler] = None


def _is_missing_tenant_column_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return ("tenant_id" in msg) and ("does not exist" in msg)


def _resolve_rdv_contact(db, rdv: RendezVous):
    target_email = None
    target_phone = None
    target_name = "Cher contact"
    target_person_id = None
    if rdv.person_id:
        person = db.get(Person, rdv.person_id)
        if person:
            target_email = person.email
            target_phone = person.phone
            target_name = f"{person.first_name} {person.last_name or ''}".strip() or target_name
            target_person_id = str(person.id)
    return target_email, target_phone, target_name, target_person_id


async def _dispatch_or_enqueue_notification(
    *,
    db,
    person: Person,
    dedupe_key: str,
    email_subject: str,
    email_html: str,
    email_text: str,
    sms_text: str,
    wa_text: str,
    email_service: EmailService,
    sms_service: SMSService,
    wa_service: WhatsAppService,
):
    if settings.outbox_mode_enabled:
        tenant_id = str(getattr(person, "tenant_id", None) or settings.default_tenant_id)
        enqueue_event(
            db,
            tenant_id=tenant_id,
            event_type="notification.preferred",
            aggregate_type="person",
            aggregate_id=str(person.id),
            payload={
                "recipient_scope": "applicant",
                "person_id": str(person.id),
                "applicant_person_id": str(person.id),
                "applicant_email": str(getattr(person, "email", "") or "").strip().lower() or None,
                "applicant_phone": str(getattr(person, "phone", "") or "").strip() or None,
                "dedupe_key": dedupe_key,
                "email_subject": email_subject,
                "email_html": email_html,
                "email_text": email_text,
                "sms_text": sms_text,
                "wa_text": wa_text,
            },
        )
        return {"sent": True, "queued": True}

    return await send_preferred_notification(
        db=db,
        person=person,
        dedupe_key=dedupe_key,
        email_subject=email_subject,
        email_html=email_html,
        email_text=email_text,
        sms_text=sms_text,
        wa_text=wa_text,
        email_service=email_service,
        sms_service=sms_service,
        wa_service=wa_service,
        target_email=str(getattr(person, "email", "") or "").strip().lower() or None,
        target_phone=str(getattr(person, "phone", "") or "").strip() or None,
        recipient_scope="applicant",
        event_type="notification.preferred.direct",
        event_id=str(dedupe_key),
    )


async def _process_email_reminders_for_tenant(tenant_id: str):
    """Job de rappels RDV multi-canal (WhatsApp -> Email -> SMS) pour un tenant."""
    db = open_db_session(str(tenant_id))
    try:
        tz_name = (settings.app_timezone or "UTC").strip()
        try:
            tzinfo = ZoneInfo(tz_name)
        except Exception:
            logger.warning("Invalid APP_TIMEZONE, falling back to UTC", extra={"extra_fields": {"app_timezone": tz_name}})
            tzinfo = timezone.utc
        now = datetime.now(tzinfo)

        # Normaliser legacy 'pending' -> 'created'
        db.query(RendezVous).filter(RendezVous.statut == "pending").update({"statut": STATUS_CREATED})
        db.commit()

        # Marquer comme completes les RDV passes
        to_complete = db.query(RendezVous).filter(
            RendezVous.end_at <= now - timedelta(minutes=5),
            RendezVous.statut.in_([STATUS_CONFIRMED, STATUS_REMINDER_SENT]),
        ).all()
        for rdv in to_complete:
            rdv.statut = STATUS_COMPLETED
            db.add(rdv)
        if to_complete:
            db.commit()

        window_24h_start = now + timedelta(hours=23, minutes=50)
        window_24h_end = now + timedelta(hours=24, minutes=10)
        rdv_24h = db.query(RendezVous).filter(
            RendezVous.start_at >= window_24h_start,
            RendezVous.start_at <= window_24h_end,
            RendezVous.statut.in_(list(REMINDER_STATUSES)),
        ).all()

        window_2h_start = now + timedelta(hours=1, minutes=50)
        window_2h_end = now + timedelta(hours=2, minutes=10)
        rdv_2h = db.query(RendezVous).filter(
            RendezVous.start_at >= window_2h_start,
            RendezVous.start_at <= window_2h_end,
            RendezVous.statut.in_(list(REMINDER_STATUSES)),
        ).all()

        window_followup_start = now - timedelta(days=1, hours=1)
        window_followup_end = now - timedelta(hours=23)
        rdv_followup = db.query(RendezVous).filter(
            RendezVous.end_at >= window_followup_start,
            RendezVous.end_at <= window_followup_end,
            RendezVous.statut == STATUS_COMPLETED,
        ).all()

        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        rdv_today = db.query(RendezVous).filter(
            RendezVous.start_at >= day_start,
            RendezVous.start_at < day_end,
            RendezVous.statut.in_(list(ACTIVE_STATUSES)),
        ).order_by(RendezVous.start_at.asc()).all()

        email_service = EmailService()
        sms_service = SMSService()
        wa_service = WhatsAppService()

        count_24h = 0
        for rdv in rdv_24h:
            target_email, target_phone, target_name, target_person_id = _resolve_rdv_contact(db, rdv)
            if not target_person_id:
                continue
            dedupe_key = f"reminder_j1:{rdv.id}"
            person = db.get(Person, rdv.person_id)
            if not person:
                continue
            result = await _dispatch_or_enqueue_notification(
                db=db,
                person=person,
                dedupe_key=dedupe_key,
                email_subject="Rappel J-1 : Rendez-vous admission",
                email_html=(
                    f"<p>Bonjour {target_name},</p>"
                    f"<p>Rappel: votre rendez-vous admission est prevu demain a {rdv.start_at.strftime('%H:%M')}.</p>"
                ),
                email_text=f"Rappel J-1: votre rendez-vous est prevu demain a {rdv.start_at.strftime('%H:%M')}.",
                sms_text=f"Rappel J-1: RDV admission demain a {rdv.start_at.strftime('%H:%M')}.",
                wa_text=f"Rappel J-1: votre RDV admission est demain a {rdv.start_at.strftime('%H:%M')}.",
                email_service=email_service,
                sms_service=sms_service,
                wa_service=wa_service,
            )
            if result.get("sent"):
                count_24h += 1
                if rdv.statut == STATUS_CONFIRMED:
                    rdv.statut = STATUS_REMINDER_SENT
                    db.add(rdv)

        count_2h = 0
        for rdv in rdv_2h:
            target_email, target_phone, target_name, target_person_id = _resolve_rdv_contact(db, rdv)
            if not target_person_id:
                continue
            dedupe_key = f"reminder_j0:{rdv.id}"
            person = db.get(Person, rdv.person_id)
            if not person:
                continue
            result = await _dispatch_or_enqueue_notification(
                db=db,
                person=person,
                dedupe_key=dedupe_key,
                email_subject="Rappel aujourd'hui : Rendez-vous admission",
                email_html=(
                    f"<p>Bonjour {target_name},</p>"
                    f"<p>Votre rendez-vous admission est prevu aujourd'hui a {rdv.start_at.strftime('%H:%M')}.</p>"
                ),
                email_text=f"Rappel J0: votre rendez-vous est aujourd'hui a {rdv.start_at.strftime('%H:%M')}.",
                sms_text=f"Rappel J0: RDV admission aujourd'hui a {rdv.start_at.strftime('%H:%M')}.",
                wa_text=f"Rappel J0: votre RDV admission est aujourd'hui a {rdv.start_at.strftime('%H:%M')}.",
                email_service=email_service,
                sms_service=sms_service,
                wa_service=wa_service,
            )
            if result.get("sent"):
                count_2h += 1

        count_followup = 0
        for rdv in rdv_followup:
            target_email, target_phone, target_name, target_person_id = _resolve_rdv_contact(db, rdv)
            if not target_person_id:
                continue
            dedupe_key = f"followup_j1:{rdv.id}"
            person = db.get(Person, rdv.person_id)
            if not person:
                continue
            result = await _dispatch_or_enqueue_notification(
                db=db,
                person=person,
                dedupe_key=dedupe_key,
                email_subject="Suivi apres votre entretien admission",
                email_html=(
                    f"<p>Bonjour {target_name},</p>"
                    "<p>Merci pour votre entretien. Nous restons disponibles pour finaliser votre dossier.</p>"
                ),
                email_text="Merci pour votre entretien. Nous restons disponibles pour la suite du dossier.",
                sms_text="Merci pour votre entretien. Nous restons disponibles pour la suite de votre dossier.",
                wa_text="Merci pour votre entretien admission. Ecrivez-nous pour la suite de votre dossier.",
                email_service=email_service,
                sms_service=sms_service,
                wa_service=wa_service,
            )
            if result.get("sent"):
                count_followup += 1
                rdv.statut = STATUS_FOLLOWUP_SENT
                db.add(rdv)

        admin_alerts = 0
        if settings.admin_alert_email and rdv_today:
            dedupe_key = f"admin_dayof:{day_start.date().isoformat()}"
            if not kb_service.email_log_exists(db, dedupe_key=dedupe_key):
                rows = []
                for rdv in rdv_today[:50]:
                    _, _, target_name, _ = _resolve_rdv_contact(db, rdv)
                    rows.append(
                        f"<li>{rdv.start_at.strftime('%H:%M')} - {target_name} - statut: {rdv.statut}</li>"
                    )
                try:
                    await email_service.send_email(
                        to_email=settings.admin_alert_email,
                        subject=f"Rendez-vous du jour ({day_start.date().isoformat()})",
                        html_body=f"<p>Rendez-vous a traiter aujourd'hui:</p><ul>{''.join(rows)}</ul>",
                        text_body=f"{len(rdv_today)} rendez-vous prevus aujourd'hui.",
                    )
                    kb_service.create_email_log(
                        db,
                        person_id=None,
                        sujet=f"Rendez-vous du jour ({day_start.date().isoformat()})",
                        statut="sent",
                        provider_id=f"{dedupe_key}|{email_service.provider or 'unknown'}",
                    )
                    admin_alerts = 1
                except Exception:
                    kb_service.create_email_log(
                        db,
                        person_id=None,
                        sujet=f"Rendez-vous du jour ({day_start.date().isoformat()})",
                        statut="failed",
                        provider_id=f"{dedupe_key}|{email_service.provider or 'unknown'}",
                    )

        db.commit()
        logger.info(
            "Appointment reminders job completed",
            extra={
                "extra_fields": {
                    "tenant_id": str(tenant_id),
                    "reminder_j1": count_24h,
                    "reminder_j0": count_2h,
                    "followup_j1": count_followup,
                    "admin_alerts": admin_alerts,
                }
            },
        )

    except Exception as e:
        if _is_missing_tenant_column_error(e):
            logger.critical(
                "Email reminders job blocked by schema mismatch (missing tenant_id column). "
                "Run DB migrations first (example: `venv/bin/alembic upgrade head`).",
                extra={"extra_fields": {"tenant_id": str(tenant_id), "error": str(e)}},
            )
        else:
            logger.error(
                "Email reminders job failed",
                extra={"extra_fields": {"tenant_id": str(tenant_id), "error": str(e)}},
                exc_info=True,
            )
    finally:
        db.close()


def _list_active_tenant_ids() -> list[str]:
    seed_db = open_db_session(allow_unscoped=True)
    try:
        rows = seed_db.query(Tenant.id).filter(Tenant.is_active == True).all()
        tenant_ids = [str(row[0]) for row in rows if row and row[0]]
        return tenant_ids or [str(settings.default_tenant_id)]
    except Exception as exc:
        logger.warning(
            "Unable to list active tenants for scheduler, using default tenant only",
            extra={"extra_fields": {"error": str(exc), "default_tenant_id": str(settings.default_tenant_id)}},
        )
        return [str(settings.default_tenant_id)]
    finally:
        seed_db.close()


async def send_email_reminders_job():
    """Job scheduler multi-tenant pour rappels RDV et suivis."""
    tenant_ids = _list_active_tenant_ids()
    for tenant_id in tenant_ids:
        await _process_email_reminders_for_tenant(tenant_id)


async def _process_outbox_for_tenant(tenant_id: str):
    db = open_db_session(str(tenant_id))
    try:
        stats = await process_outbox_batch(db)
        if int(stats.get("processed") or 0) > 0:
            logger.info(
                "Outbox job completed",
                extra={"extra_fields": {"tenant_id": str(tenant_id), **stats}},
            )
    except Exception as exc:
        logger.error(
            "Outbox job failed",
            extra={"extra_fields": {"tenant_id": str(tenant_id), "error": str(exc)}},
            exc_info=True,
        )
    finally:
        db.close()


async def process_outbox_events_job():
    tenant_ids = _list_active_tenant_ids()
    for tenant_id in tenant_ids:
        await _process_outbox_for_tenant(tenant_id)


def start_scheduler():
    """Démarre le scheduler avec tous les jobs configurés."""
    global _scheduler

    if _scheduler is not None:
        logger.warning("Scheduler already running")
        return _scheduler

    _scheduler = AsyncIOScheduler()

    _scheduler.add_job(
        send_email_reminders_job,
        trigger=CronTrigger(minute="*/10"),
        id="email_reminders",
        name="Send email reminders",
        replace_existing=True,
    )

    _scheduler.add_job(
        process_outbox_events_job,
        trigger=CronTrigger(minute="*"),
        id="outbox_events",
        name="Process outbox events",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info("Scheduler started with jobs: email_reminders, outbox_events")

    return _scheduler


def stop_scheduler():
    """Arrête le scheduler proprement."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown()
        _scheduler = None
        logger.info("Scheduler stopped")


def get_scheduler() -> Optional[AsyncIOScheduler]:
    """Retourne l'instance du scheduler."""
    return _scheduler
