from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..db import get_db
from ..models import Agent, ParentStudentLink, Person, PersonRole, RendezVous, SchoolProgram, SchoolTrack
from ..security import Principal, get_principal, require_role
from ..services.admission_requirements import format_requirements_for_channel
from ..services.appointment_service import (
    _select_agent,
    assert_no_appointment_conflicts,
    persist_rendezvous_and_sync_event,
    serialize_rendezvous,
)
from ..services.kb import create_email_log
from ..services.outbox import enqueue_appointment_notification_events

router = APIRouter(prefix="/school", tags=["school"])

VALID_PERSON_ROLES = {"candidate", "parent", "student"}
VALID_RELATIONS = {"pere", "mere", "tuteur", "guardian", "other"}
VALID_APPOINTMENT_STATUS = {
    "created",
    "confirmed",
    "reminder_sent",
    "completed",
    "follow_up_sent",
    "cancelled",
}
VALID_PUBLIC_CHANNELS = {"email", "phone", "whatsapp", "sms"}


class PersonCreate(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=120)
    last_name: Optional[str] = Field(default=None, max_length=120)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=40)
    preferred_language: Optional[str] = Field(default=None, max_length=10)
    notes: Optional[str] = None
    roles: list[str] = Field(default_factory=lambda: ["candidate"])
    status: Optional[str] = "active"


class PersonRolePayload(BaseModel):
    role: str


class PersonUpdate(BaseModel):
    first_name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    last_name: Optional[str] = Field(default=None, max_length=120)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=40)
    preferred_language: Optional[str] = Field(default=None, max_length=10)
    status: Optional[str] = None
    notes: Optional[str] = None


class ParentStudentPayload(BaseModel):
    parent_id: UUID
    student_id: UUID
    relation: Optional[str] = "tuteur"


class SchoolAppointmentCreate(BaseModel):
    person_id: UUID
    track_id: UUID
    start_at: datetime
    end_at: datetime
    statut: str = "created"
    agent_id: Optional[UUID] = None


class SchoolAppointmentStatusUpdate(BaseModel):
    statut: str


class SchoolAppointmentUpdate(BaseModel):
    person_id: Optional[UUID] = None
    track_id: Optional[UUID] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    statut: Optional[str] = None
    agent_id: Optional[UUID] = None


class PublicContactRequest(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=120)
    last_name: Optional[str] = Field(default=None, max_length=120)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=40)
    requester_type: str = Field(default="candidate", pattern="^(candidate|parent|student|other)$")
    subject: str = Field(..., min_length=3, max_length=180)
    message: str = Field(..., min_length=10, max_length=5000)
    preferred_language: Optional[str] = Field(default=None, max_length=10)
    preferred_channel: Optional[str] = Field(default=None, max_length=20)
    track_id: Optional[UUID] = None
    consent: bool = False


def _appointment_messages(person: Person, track: SchoolTrack, start_at: datetime, kind: str) -> dict:
    display_name = f"{person.first_name} {person.last_name or ''}".strip()
    date_label = start_at.strftime("%d/%m/%Y")
    time_label = start_at.strftime("%H:%M")
    if kind == "created":
        return {
            "email_subject": "Demande de rendez-vous enregistree",
            "email_html": (
                f"<p>Bonjour {display_name or 'Cher candidat'},</p>"
                f"<p>Votre demande de rendez-vous pour la filiere <b>{track.name}</b> a ete enregistree.</p>"
                f"<p>Date proposee: <b>{date_label}</b> a <b>{time_label}</b>.</p>"
            ),
            "sms": f"Votre demande RDV ({track.name}) est enregistree pour le {date_label} a {time_label}.",
            "wa": f"Bonjour {display_name}, votre demande de RDV ({track.name}) est enregistree pour le {date_label} a {time_label}.",
        }
    if kind == "confirmed":
        return {
            "email_subject": "Confirmation de votre rendez-vous admission",
            "email_html": (
                f"<p>Bonjour {display_name or 'Cher candidat'},</p>"
                f"<p>Votre rendez-vous admission est confirme pour la filiere <b>{track.name}</b>.</p>"
                f"<p>Date: <b>{date_label}</b> a <b>{time_label}</b>.</p>"
            ),
            "sms": f"RDV admission confirme ({track.name}) le {date_label} a {time_label}.",
            "wa": f"Bonjour {display_name}, votre RDV admission pour {track.name} est confirme le {date_label} a {time_label}.",
        }
    if kind == "modified":
        return {
            "email_subject": "Modification de votre rendez-vous admission",
            "email_html": (
                f"<p>Bonjour {display_name or 'Cher candidat'},</p>"
                f"<p>Votre rendez-vous admission pour la filiere <b>{track.name}</b> a ete modifie.</p>"
                f"<p>Nouvelle date: <b>{date_label}</b> a <b>{time_label}</b>.</p>"
            ),
            "sms": f"RDV admission modifie ({track.name}). Nouvelle date: {date_label} a {time_label}.",
            "wa": f"Bonjour {display_name}, votre RDV admission pour {track.name} a ete modifie. Nouvelle date: {date_label} a {time_label}.",
        }
    if kind == "cancelled":
        return {
            "email_subject": "Annulation de votre rendez-vous admission",
            "email_html": (
                f"<p>Bonjour {display_name or 'Cher candidat'},</p>"
                f"<p>Votre rendez-vous admission pour la filiere <b>{track.name}</b> "
                f"prevu le <b>{date_label}</b> a <b>{time_label}</b> a ete annule.</p>"
                "<p>N'hesitez pas a nous recontacter pour reprogrammer un nouveau rendez-vous.</p>"
            ),
            "sms": f"RDV admission annule ({track.name}) du {date_label} a {time_label}. Contactez-nous pour reprogrammer.",
            "wa": f"Bonjour {display_name}, votre RDV admission pour {track.name} du {date_label} a {time_label} a ete annule. Contactez-nous pour reprogrammer.",
        }
    return {
        "email_subject": "Suivi de votre rendez-vous admission",
        "email_html": (
            f"<p>Bonjour {display_name or 'Cher candidat'},</p>"
            "<p>Merci pour votre echange avec l'etablissement. "
            "N'hesitez pas a revenir vers nous pour finaliser votre dossier.</p>"
        ),
        "sms": "Merci pour votre rendez-vous. Nous restons disponibles pour la suite de votre dossier.",
        "wa": f"Merci {display_name}, nous restons disponibles pour la suite de votre dossier.",
    }


def _notification_summary(email_queued: bool, sms_queued: bool, *, email_failed_reason: Optional[str] = None) -> dict:
    payload = {
        "email": {"status": "pending" if email_queued else "failed" if email_failed_reason else "skipped"},
        "sms": {"status": "pending" if sms_queued else "skipped"},
    }
    if email_failed_reason:
        payload["email"]["reason"] = email_failed_reason
    return payload


async def _notify_school_appointment(db: Session, rdv: RendezVous, person: Person, track: SchoolTrack, kind: str) -> dict:
    msg = _appointment_messages(person, track, rdv.start_at, kind)
    lang = (person.preferred_language or "fr").strip().lower()
    if lang not in {"fr", "en", "wo"}:
        lang = "fr"
    if kind in {"created", "confirmed"}:
        requirements = format_requirements_for_channel(
            db,
            lang=lang,
            with_policies=True,
            bullet_prefix="• ",
        )
        if requirements:
            msg["wa"] = f"{msg['wa']}\n\n{requirements}"
            msg["email_html"] = f"{msg['email_html']}<p><b>Pieces/conditions:</b></p><pre style='white-space:pre-wrap'>{requirements}</pre>"
            msg["sms"] = f"{msg['sms']} Liste des pieces envoyee sur WhatsApp."
    dedupe_base = f"appointment:{kind}:{rdv.id}"
    email_queued = False
    sms_queued = False
    email_failed_reason = None
    email_payload = None
    sms_payload = None

    email_recipient = str(person.email or "").strip().lower() or None
    sms_recipient = str(person.phone or "").strip() or None

    if email_recipient:
        email_payload = {
            "dedupe_key": f"{dedupe_base}:email",
            "person_id": str(person.id),
            "recipient": email_recipient,
            "subject": msg["email_subject"],
            "html_body": msg["email_html"],
            "text_body": msg["sms"],
        }
        email_queued = True
    else:
        email_failed_reason = "missing_email_recipient"
        create_email_log(
            db,
            person_id=str(person.id),
            sujet=msg["email_subject"],
            statut="failed",
            dedupe_key=f"{dedupe_base}:email",
            recipient=None,
            last_error=email_failed_reason,
        )

    if sms_recipient:
        sms_payload = {
            "dedupe_key": f"{dedupe_base}:sms",
            "person_id": str(person.id),
            "recipient": sms_recipient,
            "body": msg["sms"],
        }
        sms_queued = True

    enqueue_appointment_notification_events(
        db,
        tenant_id=str(getattr(rdv, "tenant_id", None)),
        appointment_id=str(rdv.id),
        person_id=str(person.id),
        action=kind,
        email_payload=email_payload,
        sms_payload=sms_payload,
    )
    return _notification_summary(email_queued, sms_queued, email_failed_reason=email_failed_reason)


def _serialize_person(db: Session, person: Person) -> dict:
    roles = (
        db.query(PersonRole)
        .filter(PersonRole.person_id == person.id)
        .order_by(PersonRole.role.asc())
        .all()
    )
    return {
        "id": str(person.id),
        "first_name": person.first_name,
        "last_name": person.last_name,
        "email": person.email,
        "phone": person.phone,
        "preferred_language": person.preferred_language,
        "status": person.status,
        "notes": person.notes,
        "roles": [r.role for r in roles],
        "created_at": person.created_at,
        "updated_at": person.updated_at,
    }


@router.get("/public/tracks")
def list_public_tracks(
    q: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    query = (
        db.query(SchoolTrack, SchoolProgram)
        .join(SchoolProgram, SchoolTrack.program_id == SchoolProgram.id)
        .filter(SchoolTrack.is_active == True, SchoolProgram.is_active == True)
    )
    if q:
        pattern = f"%{q}%"
        query = query.filter(
            (SchoolTrack.name.ilike(pattern))
            | (SchoolProgram.name.ilike(pattern))
            | (SchoolProgram.delivery_mode.ilike(pattern))
        )
    items = query.order_by(SchoolProgram.name.asc(), SchoolTrack.name.asc()).limit(limit).all()
    return {
        "items": [
            {
                "id": str(track.id),
                "name": track.name,
                "program_id": str(program.id),
                "program_name": program.name,
                "delivery_mode": program.delivery_mode,
                "annual_fee": float(track.annual_fee),
                "registration_fee": float(track.registration_fee),
                "monthly_fee": float(track.monthly_fee),
            }
            for track, program in items
        ],
        "total": len(items),
    }


@router.post("/contact-requests")
def create_public_contact_request(payload: PublicContactRequest, db: Session = Depends(get_db)):
    if not payload.email and not payload.phone:
        raise HTTPException(status_code=400, detail="email_or_phone_required")
    if not payload.consent:
        raise HTTPException(status_code=400, detail="consent_required")
    if payload.preferred_channel and payload.preferred_channel not in VALID_PUBLIC_CHANNELS:
        raise HTTPException(status_code=400, detail="invalid_preferred_channel")

    email_value = payload.email.lower() if payload.email else None
    phone_value = payload.phone.strip() if payload.phone else None

    person_query = db.query(Person)
    if email_value and phone_value:
        person_query = person_query.filter((Person.email == email_value) | (Person.phone == phone_value))
    elif email_value:
        person_query = person_query.filter(Person.email == email_value)
    else:
        person_query = person_query.filter(Person.phone == phone_value)

    person = person_query.first()
    if not person:
        person = Person(
            first_name=payload.first_name.strip(),
            last_name=payload.last_name.strip() if payload.last_name else None,
            email=email_value,
            phone=phone_value,
            preferred_language=payload.preferred_language,
            status="active",
            notes=None,
        )
        db.add(person)
        db.flush()
    else:
        if not person.email and email_value:
            person.email = email_value
        if not person.phone and phone_value:
            person.phone = phone_value
        if payload.preferred_language:
            person.preferred_language = payload.preferred_language
        if payload.last_name and not person.last_name:
            person.last_name = payload.last_name.strip()
        if payload.first_name and not person.first_name:
            person.first_name = payload.first_name.strip()

    role = payload.requester_type if payload.requester_type in VALID_PERSON_ROLES else "candidate"
    existing_role = (
        db.query(PersonRole)
        .filter(PersonRole.person_id == person.id, PersonRole.role == role)
        .first()
    )
    if not existing_role:
        db.add(PersonRole(person_id=person.id, role=role))

    track = None
    if payload.track_id:
        track = db.get(SchoolTrack, payload.track_id)
        if not track:
            raise HTTPException(status_code=404, detail="track_not_found")

    note_lines = [
        "[Contact public]",
        f"Sujet: {payload.subject.strip()}",
        f"Message: {payload.message.strip()}",
        f"Profil: {payload.requester_type}",
        f"Canal prefere: {payload.preferred_channel or 'non_precise'}",
    ]
    if track:
        note_lines.append(f"Filiere interessee: {track.name}")
    note_block = "\n".join(note_lines)
    person.notes = f"{person.notes}\n\n{note_block}".strip() if person.notes else note_block

    db.add(person)
    db.commit()
    db.refresh(person)

    return {
        "submitted": True,
        "person_id": str(person.id),
        "role": role,
        "track_id": str(track.id) if track else None,
    }


@router.post("/persons", dependencies=[Depends(require_role("manager|admin"))])
def create_person(payload: PersonCreate, db: Session = Depends(get_db)):
    person = Person(
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip() if payload.last_name else None,
        email=payload.email.lower() if payload.email else None,
        phone=payload.phone,
        preferred_language=payload.preferred_language,
        notes=payload.notes,
        status=(payload.status or "active"),
    )
    db.add(person)
    db.flush()

    roles = set(r.strip().lower() for r in payload.roles if r and r.strip())
    if not roles:
        roles = {"candidate"}
    invalid_roles = [r for r in roles if r not in VALID_PERSON_ROLES]
    if invalid_roles:
        raise HTTPException(status_code=400, detail=f"invalid_roles: {', '.join(invalid_roles)}")

    for role in sorted(roles):
        db.add(PersonRole(person_id=person.id, role=role))
    db.commit()
    db.refresh(person)
    return _serialize_person(db, person)


@router.get("/persons/{person_id}", dependencies=[Depends(require_role("agent|viewer|manager|admin"))])
def get_person(person_id: UUID, db: Session = Depends(get_db)):
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="person_not_found")
    return _serialize_person(db, person)


@router.get("/persons", dependencies=[Depends(require_role("agent|viewer|manager|admin"))])
def list_persons(
    q: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0, le=10_000),
    db: Session = Depends(get_db),
):
    query = db.query(Person)
    if q:
        pattern = f"%{q}%"
        query = query.filter(
            (Person.first_name.ilike(pattern))
            | (Person.last_name.ilike(pattern))
            | (Person.email.ilike(pattern))
            | (Person.phone.ilike(pattern))
        )
    if role:
        query = query.join(PersonRole, PersonRole.person_id == Person.id).filter(PersonRole.role == role)

    total = query.count()
    items = query.order_by(Person.created_at.desc()).offset(offset).limit(limit).all()
    result = [_serialize_person(db, p) for p in items]
    return {
        "items": result,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(result)) < total,
    }


@router.post("/persons/{person_id}/roles", dependencies=[Depends(require_role("manager|admin"))])
def add_person_role(person_id: UUID, payload: PersonRolePayload, db: Session = Depends(get_db)):
    role = payload.role.strip().lower()
    if role not in VALID_PERSON_ROLES:
        raise HTTPException(status_code=400, detail="invalid_role")
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="person_not_found")
    existing = db.query(PersonRole).filter(PersonRole.person_id == person_id, PersonRole.role == role).first()
    if existing:
        return _serialize_person(db, person)
    db.add(PersonRole(person_id=person_id, role=role))
    db.commit()
    return _serialize_person(db, person)


@router.put("/persons/{person_id}", dependencies=[Depends(require_role("manager|admin"))])
def update_person(person_id: UUID, payload: PersonUpdate, db: Session = Depends(get_db)):
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="person_not_found")

    if payload.first_name is not None:
        person.first_name = payload.first_name.strip()
    if payload.last_name is not None:
        person.last_name = payload.last_name.strip() if payload.last_name else None
    if payload.email is not None:
        person.email = payload.email.lower()
    if payload.phone is not None:
        person.phone = payload.phone
    if payload.preferred_language is not None:
        person.preferred_language = payload.preferred_language
    if payload.status is not None:
        person.status = payload.status
    if payload.notes is not None:
        person.notes = payload.notes

    db.add(person)
    db.commit()
    db.refresh(person)
    return _serialize_person(db, person)


@router.delete("/persons/{person_id}", dependencies=[Depends(require_role("manager|admin"))])
def delete_person(person_id: UUID, db: Session = Depends(get_db)):
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="person_not_found")
    db.delete(person)
    db.commit()
    return {"deleted": True, "id": str(person_id)}


@router.get("/persons/stats/overview", dependencies=[Depends(require_role("agent|viewer|manager|admin"))])
def persons_stats(db: Session = Depends(get_db)):
    now = datetime.now()
    week_start = now - timedelta(days=7)

    total = db.query(Person).count()
    active = db.query(Person).filter(Person.status == "active").count()
    inactive = max(int(total - active), 0)
    candidates = (
        db.query(func.count(func.distinct(PersonRole.person_id)))
        .filter(PersonRole.role == "candidate")
        .scalar()
        or 0
    )
    parents = (
        db.query(func.count(func.distinct(PersonRole.person_id)))
        .filter(PersonRole.role == "parent")
        .scalar()
        or 0
    )
    students = (
        db.query(func.count(func.distinct(PersonRole.person_id)))
        .filter(PersonRole.role == "student")
        .scalar()
        or 0
    )
    new_7d = (
        db.query(func.count(Person.id))
        .filter(Person.created_at >= week_start)
        .scalar()
        or 0
    )
    conversion_rate = round((float(students) / float(candidates)) * 100, 1) if candidates else 0.0
    return {
        "total": total,
        "active": active,
        "inactive": inactive,
        "candidates": candidates,
        "parents": parents,
        "students": students,
        "new_7d": int(new_7d),
        "conversion_rate": conversion_rate,
    }


@router.post("/persons/links/parent-student", dependencies=[Depends(require_role("manager|admin"))])
def link_parent_student(payload: ParentStudentPayload, db: Session = Depends(get_db)):
    if payload.relation and payload.relation not in VALID_RELATIONS:
        raise HTTPException(status_code=400, detail="invalid_relation")
    if payload.parent_id == payload.student_id:
        raise HTTPException(status_code=400, detail="invalid_link_same_person")

    parent = db.get(Person, payload.parent_id)
    student = db.get(Person, payload.student_id)
    if not parent or not student:
        raise HTTPException(status_code=404, detail="person_not_found")

    parent_role = db.query(PersonRole).filter(PersonRole.person_id == payload.parent_id, PersonRole.role == "parent").first()
    student_role = db.query(PersonRole).filter(PersonRole.person_id == payload.student_id, PersonRole.role == "student").first()
    if not parent_role or not student_role:
        raise HTTPException(status_code=400, detail="missing_required_roles")

    existing = (
        db.query(ParentStudentLink)
        .filter(ParentStudentLink.parent_id == payload.parent_id, ParentStudentLink.student_id == payload.student_id)
        .first()
    )
    if existing:
        return {"linked": True, "id": existing.id}

    link = ParentStudentLink(parent_id=payload.parent_id, student_id=payload.student_id, relation=payload.relation)
    db.add(link)
    db.commit()
    db.refresh(link)
    return {"linked": True, "id": link.id}


@router.post("/appointments", dependencies=[Depends(require_role("manager|admin"))])
async def create_school_appointment(payload: SchoolAppointmentCreate, db: Session = Depends(get_db)):
    incoming_status = payload.statut
    if incoming_status == "pending":
        incoming_status = "created"
    if incoming_status not in VALID_APPOINTMENT_STATUS:
        raise HTTPException(status_code=400, detail="invalid_status")
    if payload.end_at <= payload.start_at:
        raise HTTPException(status_code=400, detail="invalid_timeslot")

    person = db.get(Person, payload.person_id)
    track = db.get(SchoolTrack, payload.track_id)
    if not person:
        raise HTTPException(status_code=404, detail="person_not_found")
    if not track:
        raise HTTPException(status_code=404, detail="track_not_found")

    assigned_agent = _select_agent(
        db,
        start_at=payload.start_at,
        end_at=payload.end_at,
        track_id=payload.track_id,
        preferred_agent_id=payload.agent_id,
    )
    assert_no_appointment_conflicts(
        db,
        person_id=payload.person_id,
        agent_id=assigned_agent.id,
        start_at=payload.start_at,
        end_at=payload.end_at,
    )

    rdv = RendezVous(
        person_id=payload.person_id,
        track_id=payload.track_id,
        agent_id=assigned_agent.id,
        start_at=payload.start_at,
        end_at=payload.end_at,
        statut=incoming_status,
    )
    persist_rendezvous_and_sync_event(db, rdv)
    notifications = await _notify_school_appointment(db, rdv, person, track, "created")
    payload_out = serialize_rendezvous(db, rdv)
    payload_out["notifications"] = notifications
    return payload_out


@router.put("/appointments/{appointment_id}", dependencies=[Depends(require_role("manager|admin"))])
async def update_school_appointment(appointment_id: UUID, payload: SchoolAppointmentUpdate, db: Session = Depends(get_db)):
    rdv = db.get(RendezVous, appointment_id)
    if not rdv or not rdv.person_id or not rdv.track_id:
        raise HTTPException(status_code=404, detail="appointment_not_found")
    if rdv.deleted_at is not None:
        raise HTTPException(status_code=404, detail="appointment_not_found")

    payload_data = payload.model_dump(exclude_unset=True)
    previous_status = rdv.statut
    time_changed = False
    payload_changed = False

    if "person_id" in payload_data:
        person = db.get(Person, payload.person_id) if payload.person_id else None
        if payload.person_id and not person:
            raise HTTPException(status_code=404, detail="person_not_found")
        if payload.person_id:
            rdv.person_id = payload.person_id
            payload_changed = True

    if "track_id" in payload_data:
        track = db.get(SchoolTrack, payload.track_id) if payload.track_id else None
        if payload.track_id and not track:
            raise HTTPException(status_code=404, detail="track_not_found")
        if payload.track_id:
            rdv.track_id = payload.track_id
            payload_changed = True

    if "start_at" in payload_data and payload.start_at:
        time_changed = True
        rdv.start_at = payload.start_at
        payload_changed = True
    if "end_at" in payload_data and payload.end_at:
        time_changed = True
        rdv.end_at = payload.end_at
        payload_changed = True
    if rdv.end_at <= rdv.start_at:
        raise HTTPException(status_code=400, detail="invalid_timeslot")

    if "agent_id" in payload_data:
        rdv.agent_id = payload.agent_id
        payload_changed = True

    selected_agent = _select_agent(
        db,
        start_at=rdv.start_at,
        end_at=rdv.end_at,
        track_id=rdv.track_id,
        preferred_agent_id=rdv.agent_id,
        exclude_rdv_id=rdv.id,
    )
    rdv.agent_id = selected_agent.id
    assert_no_appointment_conflicts(
        db,
        person_id=rdv.person_id,
        agent_id=rdv.agent_id,
        start_at=rdv.start_at,
        end_at=rdv.end_at,
        exclude_rdv_id=rdv.id,
    )

    if "statut" in payload_data and payload.statut:
        if payload.statut not in VALID_APPOINTMENT_STATUS:
            raise HTTPException(status_code=400, detail="invalid_status")
        rdv.statut = payload.statut
        payload_changed = True

    persist_rendezvous_and_sync_event(db, rdv)

    notifications = {"email": {"status": "skipped"}, "sms": {"status": "skipped"}}
    person = db.get(Person, rdv.person_id) if rdv.person_id else None
    track = db.get(SchoolTrack, rdv.track_id) if rdv.track_id else None

    if person and track:
        if payload.statut == "cancelled" and previous_status != "cancelled":
            notifications = await _notify_school_appointment(db, rdv, person, track, "cancelled")
        elif previous_status != rdv.statut:
            if previous_status != "confirmed" and rdv.statut == "confirmed":
                notifications = await _notify_school_appointment(db, rdv, person, track, "confirmed")
            elif previous_status not in {"completed", "follow_up_sent"} and rdv.statut in {"completed", "follow_up_sent"}:
                notifications = await _notify_school_appointment(db, rdv, person, track, "followup")
                if rdv.statut == "completed":
                    rdv.statut = "follow_up_sent"
                    persist_rendezvous_and_sync_event(db, rdv)
            else:
                notifications = await _notify_school_appointment(db, rdv, person, track, "modified")
        elif payload_changed or time_changed:
            notifications = await _notify_school_appointment(db, rdv, person, track, "modified")

    payload_out = serialize_rendezvous(db, rdv)
    payload_out["notifications"] = notifications
    return payload_out


@router.patch("/appointments/{appointment_id}/status", dependencies=[Depends(require_role("manager|admin"))])
async def update_school_appointment_status(appointment_id: UUID, payload: SchoolAppointmentStatusUpdate, db: Session = Depends(get_db)):
    target_status = payload.statut
    if target_status == "pending":
        target_status = "created"
    if target_status not in VALID_APPOINTMENT_STATUS:
        raise HTTPException(status_code=400, detail="invalid_status")

    rdv = db.get(RendezVous, appointment_id)
    if not rdv or not rdv.person_id or not rdv.track_id:
        raise HTTPException(status_code=404, detail="appointment_not_found")
    if rdv.deleted_at is not None:
        raise HTTPException(status_code=404, detail="appointment_not_found")

    previous_status = rdv.statut
    rdv.statut = target_status
    persist_rendezvous_and_sync_event(db, rdv)

    notifications = {"email": {"status": "skipped"}, "sms": {"status": "skipped"}}
    person = db.get(Person, rdv.person_id)
    track = db.get(SchoolTrack, rdv.track_id)

    if person and track:
        if target_status == "cancelled" and previous_status != "cancelled":
            notifications = await _notify_school_appointment(db, rdv, person, track, "cancelled")
        elif previous_status != "confirmed" and rdv.statut == "confirmed":
            notifications = await _notify_school_appointment(db, rdv, person, track, "confirmed")
        elif previous_status not in {"completed", "follow_up_sent"} and rdv.statut in {"completed", "follow_up_sent"}:
            notifications = await _notify_school_appointment(db, rdv, person, track, "followup")
            if rdv.statut == "completed":
                rdv.statut = "follow_up_sent"
                persist_rendezvous_and_sync_event(db, rdv)

    return {
        "id": str(rdv.id),
        "previous_status": previous_status,
        "status": rdv.statut,
        "notifications": notifications,
    }


@router.delete("/appointments/{appointment_id}", dependencies=[Depends(require_role("manager|admin"))])
async def delete_school_appointment(appointment_id: UUID, db: Session = Depends(get_db)):
    rdv = db.get(RendezVous, appointment_id)
    if not rdv or not rdv.person_id:
        raise HTTPException(status_code=404, detail="appointment_not_found")
    if rdv.deleted_at is not None:
        raise HTTPException(status_code=404, detail="appointment_not_found")

    # Soft delete: set deleted_at and mark as cancelled
    rdv.deleted_at = datetime.now()
    previous_status = rdv.statut
    if rdv.statut != "cancelled":
        rdv.statut = "cancelled"
    persist_rendezvous_and_sync_event(db, rdv)

    # Send cancellation notification
    notifications = {"email": {"status": "skipped"}, "sms": {"status": "skipped"}}
    if previous_status != "cancelled":
        person = db.get(Person, rdv.person_id) if rdv.person_id else None
        track = db.get(SchoolTrack, rdv.track_id) if rdv.track_id else None
        if person and track:
            notifications = await _notify_school_appointment(db, rdv, person, track, "cancelled")

    return {"deleted": True, "id": str(appointment_id), "soft_delete": True, "notifications": notifications}


@router.get("/appointments/{appointment_id}", dependencies=[Depends(require_role("agent|manager|admin"))])
def get_school_appointment(
    appointment_id: UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    rdv = db.get(RendezVous, appointment_id)
    if not rdv or rdv.deleted_at is not None:
        raise HTTPException(status_code=404, detail="appointment_not_found")
    if "agent" in principal.roles and "manager" not in principal.roles and "admin" not in principal.roles:
        agent = db.query(Agent).filter(Agent.user_id == int(principal.sub)).first()
        if not agent or rdv.agent_id != agent.id:
            raise HTTPException(status_code=403, detail="forbidden")
    return serialize_rendezvous(db, rdv)


@router.get("/appointments", dependencies=[Depends(require_role("agent|manager|admin"))])
def list_school_appointments(
    person_id: Optional[UUID] = Query(None),
    status: Optional[str] = Query(None),
    include_deleted: bool = Query(False),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0, le=10_000),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)

    query = db.query(RendezVous, Person, SchoolTrack, SchoolProgram).join(
        Person, RendezVous.person_id == Person.id
    ).join(
        SchoolTrack, RendezVous.track_id == SchoolTrack.id
    ).join(
        SchoolProgram, SchoolTrack.program_id == SchoolProgram.id
    )
    # Filter out soft-deleted appointments by default
    if not include_deleted:
        query = query.filter(RendezVous.deleted_at.is_(None))
    if person_id:
        query = query.filter(RendezVous.person_id == person_id)
    if status:
        query = query.filter(RendezVous.statut == status)
    if "agent" in principal.roles and "manager" not in principal.roles and "admin" not in principal.roles:
        agent = db.query(Agent).filter(Agent.user_id == int(principal.sub)).first()
        if not agent:
            return {
                "items": [],
                "total": 0,
                "limit": limit,
                "offset": offset,
                "has_more": False,
                "status_counts": {},
                "today_count": 0,
                "week_count": 0,
            }
        query = query.filter(RendezVous.agent_id == agent.id)

    total = query.count()
    base_rdv_query = db.query(RendezVous)
    if not include_deleted:
        base_rdv_query = base_rdv_query.filter(RendezVous.deleted_at.is_(None))
    if person_id:
        base_rdv_query = base_rdv_query.filter(RendezVous.person_id == person_id)
    if status:
        base_rdv_query = base_rdv_query.filter(RendezVous.statut == status)
    if "agent" in principal.roles and "manager" not in principal.roles and "admin" not in principal.roles:
        agent = db.query(Agent).filter(Agent.user_id == int(principal.sub)).first()
        if agent:
            base_rdv_query = base_rdv_query.filter(RendezVous.agent_id == agent.id)
    status_rows = base_rdv_query.with_entities(RendezVous.statut, func.count(RendezVous.id)).group_by(RendezVous.statut).all()
    status_counts = {status_key or "unknown": int(count) for status_key, count in status_rows}
    today_count = (
        base_rdv_query.filter(RendezVous.start_at >= today_start, RendezVous.start_at < today_start + timedelta(days=1)).count()
    )
    week_count = base_rdv_query.filter(RendezVous.start_at >= week_start).count()

    items = query.order_by(RendezVous.start_at.desc()).offset(offset).limit(limit).all()
    data = []
    for rdv, person, track, program in items:
        item = serialize_rendezvous(db, rdv)
        item["program"] = {"id": str(program.id), "name": program.name}
        data.append(item)
    return {
        "items": data,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(data)) < total,
        "status_counts": status_counts,
        "today_count": int(today_count),
        "week_count": int(week_count),
    }
