"""Function-calling handlers for the school assistant."""
from __future__ import annotations

from datetime import datetime, timedelta
import unicodedata
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from ..logger import get_logger
from ..config import settings
from ..models import Agent, Person, PersonRole, RendezVous, SchoolDepartment, SchoolProgram, SchoolTrack, User
from ..services.agent_assignment import find_available_agents
from ..services.admission_requirements import format_requirements_for_channel
from ..services.appointment_service import (
    _agent_display_payload,
    _select_agent,
    assert_no_appointment_conflicts,
    persist_rendezvous_and_sync_event,
)
from ..services.notification_dispatch import send_preferred_notification  # compatibility for existing monkeypatch-based tests
from ..services.outbox import (
    EVENT_APPOINTMENT_CALENDAR_SYNC,
    EVENT_APPOINTMENT_CRM_SYNC,
    EVENT_APPOINTMENT_STAFF_NOTIFICATION,
    enqueue_appointment_notification_events,
    enqueue_event,
)

logger = get_logger(__name__)

VALID_PERSON_ROLES = {"candidate", "parent", "student"}
VALID_RDV_STATUSES = {"pending", "created", "confirmed", "reminder_sent", "completed", "follow_up_sent", "cancelled"}
ACTIVE_RDV_STATUSES = {"created", "confirmed", "reminder_sent"}
CATALOG_LISTING_LIMIT = 25


def _normalize_str(value: Any) -> str:
    return str(value or "").strip()


def _normalize_email(value: Any) -> Optional[str]:
    raw = _normalize_str(value).lower()
    return raw or None


def _normalize_phone(value: Any) -> Optional[str]:
    raw = _normalize_str(value)
    return raw or None


def _normalize_search_text(value: Any) -> str:
    raw = _normalize_str(value).lower()
    if not raw:
        return ""
    normalized = unicodedata.normalize("NFKD", raw)
    ascii_like = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    ascii_like = " ".join(ascii_like.split())
    return ascii_like


def _looks_like_catalog_listing_query(value: str) -> bool:
    q = _normalize_search_text(value)
    if not q:
        return False
    generic_markers = {
        "programme",
        "programmes",
        "program",
        "programs",
        "filiere",
        "filieres",
        "formation",
        "formations",
        "track",
        "tracks",
        "disponible",
        "disponibles",
        "available",
        "catalogue",
        "catalog",
        # Wolof (requêtes génériques catalogue)
        "program yi",
        "programme yi",
        "filiere yi",
        "formation yi",
        "yan program",
        "yan programme",
        "yan filiere",
        "am na program",
        "am na programme",
        "am na filiere",
        "wone ma program",
        "wone ma filiere",
    }
    return any(marker in q for marker in generic_markers)


def _display_name(person: Person) -> str:
    return f"{person.first_name} {person.last_name or ''}".strip()


def _ensure_person_role(db: Session, person_id: UUID, role: str) -> None:
    normalized = role.strip().lower()
    if normalized not in VALID_PERSON_ROLES:
        normalized = "candidate"
    existing = (
        db.query(PersonRole)
        .filter(PersonRole.person_id == person_id, PersonRole.role == normalized)
        .first()
    )
    if not existing:
        db.add(PersonRole(person_id=person_id, role=normalized))


def _find_person_by_contact(db: Session, *, email: Optional[str], phone: Optional[str]) -> Optional[Person]:
    if not email and not phone:
        return None
    query = db.query(Person)
    if email and phone:
        query = query.filter((Person.email == email) | (Person.phone == phone))
    elif email:
        query = query.filter(Person.email == email)
    else:
        query = query.filter(Person.phone == phone)
    return query.first()


def _find_track(
    db: Session,
    *,
    track_id: Optional[str] = None,
    track_name: Optional[str] = None,
    program_name: Optional[str] = None,
) -> Optional[tuple[SchoolTrack, SchoolProgram, SchoolDepartment]]:
    if track_id:
        try:
            track_uuid = UUID(track_id)
        except Exception:
            return None
        track = db.get(SchoolTrack, track_uuid)
        if not track:
            return None
        program = db.get(SchoolProgram, track.program_id)
        if not program:
            return None
        department = db.get(SchoolDepartment, program.department_id)
        if not department:
            return None
        return track, program, department

    query = (
        db.query(SchoolTrack, SchoolProgram, SchoolDepartment)
        .join(SchoolProgram, SchoolTrack.program_id == SchoolProgram.id)
        .join(SchoolDepartment, SchoolProgram.department_id == SchoolDepartment.id)
        .filter(SchoolTrack.is_active == True, SchoolProgram.is_active == True)
    )
    if track_name:
        query = query.filter(SchoolTrack.name.ilike(f"%{track_name}%"))
    if program_name:
        query = query.filter(SchoolProgram.name.ilike(f"%{program_name}%"))
    return query.order_by(SchoolTrack.name.asc()).first()


def handle_create_or_get_person(db: Session, arguments: Dict[str, Any]) -> Dict[str, Any]:
    try:
        first_name = _normalize_str(arguments.get("first_name")) or "Contact"
        last_name = _normalize_str(arguments.get("last_name")) or None
        email = _normalize_email(arguments.get("email"))
        phone = _normalize_phone(arguments.get("phone"))
        role = _normalize_str(arguments.get("role") or "candidate").lower()
        if role not in VALID_PERSON_ROLES:
            role = "candidate"

        if not email and not phone:
            return {"success": False, "error": "email_or_phone_required"}

        person = _find_person_by_contact(db, email=email, phone=phone)
        status = "existing"
        if not person:
            person = Person(
                first_name=first_name,
                last_name=last_name,
                email=email,
                phone=phone,
                preferred_language=_normalize_str(arguments.get("preferred_language")) or "fr",
                status="active",
            )
            db.add(person)
            db.flush()
            status = "created"
        else:
            # Keep existing values, but fill missing fields if the new payload has them.
            if first_name and not person.first_name:
                person.first_name = first_name
            if last_name and not person.last_name:
                person.last_name = last_name
            if email and not person.email:
                person.email = email
            if phone and not person.phone:
                person.phone = phone
            db.add(person)

        _ensure_person_role(db, person.id, role)
        db.commit()
        db.refresh(person)
        return {
            "success": True,
            "status": status,
            "person_id": str(person.id),
            "display_name": _display_name(person),
            "email": person.email,
            "phone": person.phone,
            "role": role,
        }
    except Exception as exc:
        logger.error("create_or_get_person failed", extra={"extra_fields": {"error": str(exc)}}, exc_info=True)
        return {"success": False, "error": str(exc)}


def handle_get_track_tuition(db: Session, arguments: Dict[str, Any]) -> Dict[str, Any]:
    try:
        query_text = _normalize_str(arguments.get("query") or arguments.get("track_name"))
        if not query_text:
            return {"success": False, "error": "track_name_required"}

        rows = (
            db.query(SchoolTrack, SchoolProgram, SchoolDepartment)
            .join(SchoolProgram, SchoolTrack.program_id == SchoolProgram.id)
            .join(SchoolDepartment, SchoolProgram.department_id == SchoolDepartment.id)
            .filter(
                SchoolTrack.is_active == True,
                SchoolProgram.is_active == True,
                (SchoolTrack.name.ilike(f"%{query_text}%") | SchoolProgram.name.ilike(f"%{query_text}%")),
            )
            .order_by(SchoolTrack.name.asc())
            .limit(5)
            .all()
        )
        if not rows:
            # Accent/diacritic-insensitive fallback (e.g. "Génie" vs "Genie").
            normalized_query = _normalize_search_text(query_text)
            if normalized_query:
                fallback_rows = (
                    db.query(SchoolTrack, SchoolProgram, SchoolDepartment)
                    .join(SchoolProgram, SchoolTrack.program_id == SchoolProgram.id)
                    .join(SchoolDepartment, SchoolProgram.department_id == SchoolDepartment.id)
                    .filter(SchoolTrack.is_active == True, SchoolProgram.is_active == True)
                    .order_by(SchoolTrack.name.asc())
                    .all()
                )
                rows = [
                    row for row in fallback_rows
                    if normalized_query in _normalize_search_text(row[0].name)
                    or normalized_query in _normalize_search_text(row[1].name)
                ][:5]

        if not rows and _looks_like_catalog_listing_query(query_text):
            # Generic catalog questions ("quels programmes avez-vous ?") should still return options.
            rows = (
                db.query(SchoolTrack, SchoolProgram, SchoolDepartment)
                .join(SchoolProgram, SchoolTrack.program_id == SchoolProgram.id)
                .join(SchoolDepartment, SchoolProgram.department_id == SchoolDepartment.id)
                .filter(SchoolTrack.is_active == True, SchoolProgram.is_active == True)
                .order_by(SchoolProgram.name.asc(), SchoolTrack.name.asc())
                .limit(CATALOG_LISTING_LIMIT)
                .all()
            )

        if not rows:
            return {"success": False, "error": "track_not_found"}

        items = []
        for track, program, department in rows:
            items.append(
                {
                    "track_id": str(track.id),
                    "track_name": track.name,
                    "program_name": program.name,
                    "department_name": department.name,
                    "delivery_mode": program.delivery_mode,
                    "annual_fee": float(track.annual_fee),
                    "registration_fee": float(track.registration_fee),
                    "monthly_fee": float(track.monthly_fee),
                    "certifications": track.certifications,
                    "access_level": program.access_level,
                }
            )
        return {"success": True, "count": len(items), "items": items}
    except Exception as exc:
        logger.error("get_track_tuition failed", extra={"extra_fields": {"error": str(exc)}}, exc_info=True)
        return {"success": False, "error": str(exc)}


def handle_get_admission_requirements(db: Session, arguments: Dict[str, Any]) -> Dict[str, Any]:
    try:
        lang = _normalize_str(arguments.get("lang") or "fr").lower()
        if lang not in {"fr", "en", "wo"}:
            lang = "fr"
        include_policies = bool(arguments.get("with_policies", True))
        text = format_requirements_for_channel(
            db,
            lang=lang,
            with_policies=include_policies,
            bullet_prefix="• ",
        )
        return {"success": True, "lang": lang, "text": text}
    except Exception as exc:
        logger.error(
            "get_admission_requirements failed",
            extra={"extra_fields": {"error": str(exc)}},
            exc_info=True,
        )
        return {"success": False, "error": str(exc)}


async def _send_school_rdv_notifications(
    db: Session,
    *,
    rdv: RendezVous,
    person: Person,
    track: SchoolTrack,
    program: SchoolProgram,
    department: SchoolDepartment,
    lang: str,
) -> Dict[str, Any]:
    date_label = rdv.start_at.strftime("%d/%m/%Y")
    time_label = rdv.start_at.strftime("%H:%M")
    display_name = _display_name(person) or "Contact"
    requirements_text = format_requirements_for_channel(
        db,
        lang=lang,
        with_policies=True,
        bullet_prefix="• ",
    )

    subject = "Confirmation de votre rendez-vous admission"
    if lang == "en":
        subject = "Admissions appointment confirmation"
    elif lang == "wo":
        subject = "Rendez-vous admission bi am na confirmation"

    html_body = (
        f"<p>Bonjour {display_name},</p>"
        f"<p>Votre rendez-vous pour la filiere <b>{track.name}</b> ({program.name}) est enregistre.</p>"
        f"<p>Date: <b>{date_label}</b> a <b>{time_label}</b>.</p>"
        f"<p>Liste des pieces a fournir:</p>"
        f"<pre style='white-space:pre-wrap'>{requirements_text}</pre>"
    )
    sms_text = (
        f"RDV admission enregistre le {date_label} a {time_label} pour {track.name}. "
        "Consultez WhatsApp pour la liste des pieces."
    )
    wa_text = (
        f"Bonjour {display_name}, votre RDV admission est enregistre pour {date_label} a {time_label}.\n\n"
        f"{requirements_text}"
    )

    tenant_id = str(getattr(rdv, "tenant_id", None) or getattr(person, "tenant_id", None) or settings.default_tenant_id)
    dedupe_key = f"appointment:created:{rdv.id}"
    assigned_agent = db.get(Agent, rdv.agent_id) if rdv.agent_id else None
    assigned_agent_user = db.get(User, assigned_agent.user_id) if assigned_agent and assigned_agent.user_id else None
    assigned_agent_email = str(getattr(assigned_agent_user, "email", "") or "").strip().lower() or None
    agent_payload = _agent_display_payload(db, rdv.agent_id)
    assigned_agent_name = agent_payload["display_name"] if agent_payload else None

    # Générer le PDF de confirmation avec QR code et le stocker sur disque
    pdf_path = None
    try:
        from .pdf_generator import generate_appointment_pdf
        from pathlib import Path
        pdf_bytes = generate_appointment_pdf(
            person_name=display_name,
            person_email=str(person.email or "").strip() or None,
            person_phone=str(person.phone or "").strip() or None,
            track_name=track.name,
            program_name=program.name,
            department_name=department.name,
            appointment_date=date_label,
            appointment_time=time_label,
            appointment_id=str(rdv.id),
            agent_name=assigned_agent_name,
            requirements_text=requirements_text,
        )
        if pdf_bytes:
            pdf_dir = Path("uploads") / "confirmations"
            pdf_dir.mkdir(parents=True, exist_ok=True)
            pdf_path = str(pdf_dir / f"confirmation_{rdv.id}.pdf")
            Path(pdf_path).write_bytes(pdf_bytes)
            logger.info(f"PDF confirmation saved: {pdf_path} ({len(pdf_bytes)} bytes)")
    except Exception as pdf_exc:
        logger.warning("PDF generation failed", extra={"extra_fields": {"error": str(pdf_exc)}})
    email_payload = None
    sms_payload = None
    email_recipient = str(person.email or "").strip().lower() or None
    sms_recipient = str(person.phone or "").strip() or None

    if email_recipient:
        email_payload = {
            "dedupe_key": f"{dedupe_key}:email",
            "person_id": str(person.id),
            "recipient": email_recipient,
            "subject": subject,
            "html_body": html_body,
            "text_body": sms_text,
            "pdf_path": pdf_path,
        }
    if sms_recipient:
        sms_payload = {
            "dedupe_key": f"{dedupe_key}:sms",
            "person_id": str(person.id),
            "recipient": sms_recipient,
            "body": sms_text,
        }
    enqueue_appointment_notification_events(
        db,
        tenant_id=tenant_id,
        appointment_id=str(rdv.id),
        person_id=str(person.id),
        action="created",
        email_payload=email_payload,
        sms_payload=sms_payload,
    )

    staff_subject = f"Nouveau rendez-vous admission ({track.name})"
    staff_html = (
        f"<p>Nouveau rendez-vous admission enregistre.</p>"
        f"<ul>"
        f"<li>Candidat: {display_name}</li>"
        f"<li>Programme: {program.name}</li>"
        f"<li>Filiere: {track.name}</li>"
        f"<li>Date: {date_label}</li>"
        f"<li>Heure: {time_label}</li>"
        f"<li>Agent assigne: {assigned_agent_name or 'non assigne'}</li>"
        f"</ul>"
    )
    staff_text = (
        f"Nouveau rendez-vous admission - {display_name} - {program.name}/{track.name} - {date_label} {time_label}"
    )
    try:
        staff_event = enqueue_event(
            db,
            tenant_id=tenant_id,
            event_type=EVENT_APPOINTMENT_STAFF_NOTIFICATION,
            aggregate_type="appointment",
            aggregate_id=str(rdv.id),
            payload={
                "recipient_scope": "staff",
                "appointment_id": str(rdv.id),
                "person_id": str(person.id),
                "person_name": display_name,
                "person_email": person.email,
                "person_phone": person.phone,
                "track_name": track.name,
                "program_name": program.name,
                "department_name": department.name,
                "assigned_agent_id": str(rdv.agent_id) if rdv.agent_id else None,
                "assigned_agent_name": assigned_agent_name,
                "assigned_agent_email": assigned_agent_email,
                "staff_recipient_emails": [assigned_agent_email] if assigned_agent_email else [],
                "subject": staff_subject,
                "html_body": staff_html,
                "text_body": staff_text,
                "start_at": rdv.start_at.isoformat(),
                "end_at": rdv.end_at.isoformat(),
            },
        )
        logger.info(
            "appointment_staff_notification_event_enqueued",
            extra={
                "extra_fields": {
                    "event_type": EVENT_APPOINTMENT_STAFF_NOTIFICATION,
                    "event_id": str(staff_event.id),
                    "recipient_scope": "staff",
                    "applicant_email": str(person.email or "").strip().lower() or None,
                    "applicant_phone": str(person.phone or "").strip() or None,
                    "assigned_agent_email": assigned_agent_email,
                    "final_resolved_target_email": assigned_agent_email,
                    "final_resolved_target_phone": None,
                    "appointment_id": str(rdv.id),
                }
            },
        )
    except Exception as exc:
        logger.warning(
            "appointment_staff_notification_enqueue_failed",
            extra={"extra_fields": {"appointment_id": str(rdv.id), "error": str(exc)}},
            exc_info=True,
        )

    calendar_summary = f"Rendez-vous admission - {display_name} - {track.name}"
    calendar_description = (
        f"Candidat: {display_name}\n"
        f"Programme: {program.name}\n"
        f"Filiere: {track.name}\n"
        f"Contact: {person.email or person.phone or 'n/a'}\n"
        f"Agent: {assigned_agent_name or 'n/a'}"
    )
    try:
        enqueue_event(
            db,
            tenant_id=tenant_id,
            event_type=EVENT_APPOINTMENT_CALENDAR_SYNC,
            aggregate_type="appointment",
            aggregate_id=str(rdv.id),
            payload={
                "appointment_id": str(rdv.id),
                "person_id": str(person.id),
                "track_name": track.name,
                "program_name": program.name,
                "summary": calendar_summary,
                "description": calendar_description,
            },
        )
    except Exception as exc:
        logger.warning(
            "appointment_calendar_sync_enqueue_failed",
            extra={"extra_fields": {"appointment_id": str(rdv.id), "error": str(exc)}},
            exc_info=True,
        )

    try:
        enqueue_event(
            db,
            tenant_id=tenant_id,
            event_type=EVENT_APPOINTMENT_CRM_SYNC,
            aggregate_type="appointment",
            aggregate_id=str(rdv.id),
            payload={
                "appointment_id": str(rdv.id),
                "person_id": str(person.id),
                "person_name": display_name,
                "person_email": person.email,
                "person_phone": person.phone,
                "track_name": track.name,
                "program_name": program.name,
                "department_name": department.name,
                "start_at": rdv.start_at.isoformat(),
                "end_at": rdv.end_at.isoformat(),
                "agent_name": assigned_agent_name,
            },
        )
    except Exception as exc:
        logger.warning(
            "appointment_crm_sync_enqueue_failed",
            extra={"extra_fields": {"appointment_id": str(rdv.id), "error": str(exc)}},
            exc_info=True,
        )

    return {
        "email": {"status": "pending" if email_payload else "failed"},
        "sms": {"status": "pending" if sms_payload else "skipped"},
        "queued": True,
        "reason": "queued_via_outbox",
    }


async def handle_create_school_appointment(db: Session, arguments: Dict[str, Any]) -> Dict[str, Any]:
    try:
        person_id_raw = _normalize_str(arguments.get("person_id"))
        phone = _normalize_phone(arguments.get("phone"))
        email = _normalize_email(arguments.get("email"))

        person: Optional[Person] = None
        if person_id_raw:
            try:
                person = db.get(Person, UUID(person_id_raw))
            except Exception:
                person = None
        if not person:
            person = _find_person_by_contact(db, email=email, phone=phone)
        if not person:
            return {"success": False, "error": "person_not_found"}

        track_match = _find_track(
            db,
            track_id=_normalize_str(arguments.get("track_id")) or None,
            track_name=_normalize_str(arguments.get("track_name")) or None,
            program_name=_normalize_str(arguments.get("program_name")) or None,
        )
        if not track_match:
            return {"success": False, "error": "track_not_found"}
        track, program, department = track_match

        date_str = _normalize_str(arguments.get("date"))
        time_str = _normalize_str(arguments.get("time"))
        duration_minutes = int(arguments.get("duration_minutes") or 45)
        duration_minutes = min(max(duration_minutes, 15), 240)
        if not date_str or not time_str:
            return {"success": False, "error": "date_and_time_required"}

        try:
            start_at = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except ValueError:
            return {"success": False, "error": "invalid_datetime_format_use_yyyy_mm_dd_and_hh_mm"}
        end_at = start_at + timedelta(minutes=duration_minutes)
        if start_at <= datetime.now():
            return {"success": False, "error": "appointment_must_be_in_future"}

        requested_status = _normalize_str(arguments.get("statut") or "created").lower()
        if requested_status == "pending":
            requested_status = "created"
        if requested_status not in VALID_RDV_STATUSES:
            requested_status = "created"

        try:
            assigned_agent = _select_agent(
                db,
                start_at=start_at,
                end_at=end_at,
                track_id=track.id,
                preferred_agent_id=None,
            )
            assert_no_appointment_conflicts(
                db,
                person_id=person.id,
                agent_id=assigned_agent.id,
                start_at=start_at,
                end_at=end_at,
            )
        except Exception as exc:
            detail = getattr(exc, "detail", None)
            if detail == "no_agent_available":
                return {"success": False, "error": "no_agent_available"}
            if detail == "time_conflict":
                return {"success": False, "error": "person_slot_conflict"}
            if isinstance(detail, dict) and detail.get("error") in {"time_conflict", "agent_time_conflict"}:
                return {"success": False, "error": detail["error"]}
            raise

        rdv = RendezVous(
            person_id=person.id,
            track_id=track.id,
            agent_id=assigned_agent.id,
            start_at=start_at,
            end_at=end_at,
            statut=requested_status,
        )
        persist_rendezvous_and_sync_event(db, rdv)

        lang = _normalize_str(arguments.get("lang") or person.preferred_language or "fr").lower()
        if lang not in {"fr", "en", "wo"}:
            lang = "fr"
        try:
            notifications = await _send_school_rdv_notifications(
                db,
                rdv=rdv,
                person=person,
                track=track,
                program=program,
                department=department,
                lang=lang,
            )
            db.commit()
        except Exception as notif_exc:
            logger.warning(
                "create_school_appointment_side_effect_enqueue_failed",
                extra={
                    "extra_fields": {
                        "appointment_id": str(rdv.id),
                        "error": str(notif_exc),
                    }
                },
                exc_info=True,
            )
            notifications = {"sent": False, "channel": None, "queued": False, "reason": "outbox_enqueue_failed"}

        return {
            "success": True,
            "appointment_id": str(rdv.id),
            "status": rdv.statut,
            "person_id": str(person.id),
            "person_name": _display_name(person),
            "agent_id": str(assigned_agent.id),
            "agent_name": (_agent_display_payload(db, assigned_agent.id) or {}).get("display_name"),
            "track_name": track.name,
            "program_name": program.name,
            "department_name": department.name,
            "start_at": rdv.start_at.isoformat(),
            "end_at": rdv.end_at.isoformat(),
            "notifications": notifications,
        }
    except Exception as exc:
        logger.error(
            "create_school_appointment failed",
            extra={"extra_fields": {"error": str(exc)}},
            exc_info=True,
        )
        return {"success": False, "error": str(exc)}


def handle_check_appointment_slot(db: Session, arguments: Dict[str, Any]) -> Dict[str, Any]:
    try:
        date_str = _normalize_str(arguments.get("date"))
        time_str = _normalize_str(arguments.get("time"))
        duration_minutes = int(arguments.get("duration_minutes") or 45)
        if not date_str or not time_str:
            return {"success": False, "error": "date_and_time_required"}
        try:
            start_at = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except ValueError:
            return {"success": False, "error": "invalid_datetime_format_use_yyyy_mm_dd_and_hh_mm"}
        end_at = start_at + timedelta(minutes=max(15, duration_minutes))

        track_id_raw = _normalize_str(arguments.get("track_id"))
        track_uuid: Optional[UUID] = None
        conflicts_query = db.query(RendezVous).filter(
            RendezVous.statut.in_(list(ACTIVE_RDV_STATUSES) + ["pending"]),
            RendezVous.start_at < end_at,
            RendezVous.end_at > start_at,
        )
        if track_id_raw:
            try:
                track_uuid = UUID(track_id_raw)
                conflicts_query = conflicts_query.filter(RendezVous.track_id == track_uuid)
            except Exception:
                pass
        conflicts = conflicts_query.count()
        available_agents = [agent for agent, _score in find_available_agents(db, start_at, end_at, track_uuid)]
        available_agents_count = len(available_agents)
        available = available_agents_count > 0
        return {
            "success": True,
            "available": available,
            "conflicts": conflicts,
            "available_agents_count": available_agents_count,
            "message": "slot_available" if available else "no_agent_available",
            "reason": None if available else "no_agent_available",
        }
    except Exception as exc:
        logger.error("check_appointment_slot failed", extra={"extra_fields": {"error": str(exc)}}, exc_info=True)
        return {"success": False, "error": str(exc)}


async def execute_function_call(
    db: Session,
    function_name: str,
    arguments: Dict[str, Any],
    *,
    allowed_function_names: Optional[set[str]] = None,
) -> Dict[str, Any]:
    handlers = {
        "create_or_get_person": handle_create_or_get_person,
        "get_track_tuition": handle_get_track_tuition,
        "get_admission_requirements": handle_get_admission_requirements,
        "create_school_appointment": handle_create_school_appointment,
        "check_appointment_slot": handle_check_appointment_slot,
    }

    if allowed_function_names is not None and function_name not in allowed_function_names:
        return {
            "success": False,
            "error": "tool_not_allowed",
            "tool_name": function_name,
        }

    handler = handlers.get(function_name)
    if not handler:
        return {"success": False, "error": f"unknown_function: {function_name}"}

    import inspect

    if inspect.iscoroutinefunction(handler):
        return await handler(db, arguments)
    return handler(db, arguments)
