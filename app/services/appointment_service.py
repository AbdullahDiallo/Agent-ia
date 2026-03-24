from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..logger import get_logger
from ..models import Agent, Calendar, Event, Person, RendezVous, SchoolTrack, User
from .agent_assignment import find_available_agents
from .kb import has_appointment_conflict

logger = get_logger(__name__)

DEFAULT_APPOINTMENT_CALENDAR_NAME = "Rendez-vous Admissions"
ACTIVE_APPOINTMENT_STATUSES = {"created", "confirmed", "reminder_sent", "completed", "follow_up_sent"}


def _agent_display_payload(db: Session, agent_id: Optional[UUID]) -> dict[str, Any] | None:
    if not agent_id:
        return None
    agent = db.get(Agent, agent_id)
    if not agent:
        return None
    user = db.get(User, agent.user_id) if agent.user_id else None
    display_name = None
    email = None
    first_name = None
    last_name = None
    if user:
        first_name = (user.first_name or "").strip() or None
        last_name = (user.last_name or "").strip() or None
        email = (user.email or "").strip().lower() or None
        display_name = " ".join(part for part in (first_name, last_name) if part).strip() or email
    if not display_name:
        display_name = str(agent.id)
    return {
        "id": str(agent.id),
        "display_name": display_name,
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
    }


def get_or_create_appointment_calendar(db: Session) -> Calendar:
    calendar = (
        db.query(Calendar)
        .filter(Calendar.name == DEFAULT_APPOINTMENT_CALENDAR_NAME, Calendar.is_active == True)
        .order_by(Calendar.created_at.asc())
        .first()
    )
    if calendar:
        return calendar
    calendar = Calendar(name=DEFAULT_APPOINTMENT_CALENDAR_NAME, owner="system", timezone="UTC", is_active=True)
    db.add(calendar)
    db.flush()
    return calendar


def _appointment_title(person: Optional[Person], track: Optional[SchoolTrack]) -> str:
    name = " ".join(part for part in ((person.first_name if person else None), (person.last_name if person else None)) if part).strip()
    track_name = str(getattr(track, "name", "") or "").strip()
    if name and track_name:
        return f"RDV admission - {name} - {track_name}"
    if name:
        return f"RDV admission - {name}"
    if track_name:
        return f"RDV admission - {track_name}"
    return "RDV admission"


def _appointment_description(person: Optional[Person], track: Optional[SchoolTrack], agent_payload: dict[str, Any] | None) -> str:
    lines = []
    if person:
        lines.append(f"Candidat: {' '.join(part for part in (person.first_name, person.last_name) if part).strip()}")
        if person.email:
            lines.append(f"Email: {person.email}")
        if person.phone:
            lines.append(f"Telephone: {person.phone}")
    if track:
        lines.append(f"Filiere: {track.name}")
    if agent_payload:
        lines.append(f"Agent: {agent_payload.get('display_name')}")
    return "\n".join(lines)


def sync_internal_event_for_rendezvous(db: Session, rdv: RendezVous) -> Event:
    calendar = get_or_create_appointment_calendar(db)
    person = db.get(Person, rdv.person_id) if rdv.person_id else None
    track = db.get(SchoolTrack, rdv.track_id) if rdv.track_id else None
    agent_payload = _agent_display_payload(db, rdv.agent_id)
    status = "cancelled" if rdv.deleted_at is not None or rdv.statut == "cancelled" else "confirmed"
    attendees = ",".join([value for value in [getattr(person, "email", None), getattr(person, "phone", None)] if value]) or None
    title = _appointment_title(person, track)
    description = _appointment_description(person, track, agent_payload)

    event = db.query(Event).filter(Event.rendezvous_id == rdv.id).first()
    if not event:
        event = Event(
            calendar_id=calendar.id,
            rendezvous_id=rdv.id,
            title=title,
            start_at=rdv.start_at,
            end_at=rdv.end_at,
            resource_key=str(rdv.agent_id) if rdv.agent_id else None,
            attendees=attendees,
            description=description,
            status=status,
        )
        db.add(event)
        db.flush()
        return event
    event.calendar_id = calendar.id
    event.title = title
    event.start_at = rdv.start_at
    event.end_at = rdv.end_at
    event.resource_key = str(rdv.agent_id) if rdv.agent_id else None
    event.attendees = attendees
    event.description = description
    event.status = status
    db.add(event)
    db.flush()
    return event


def _select_agent(
    db: Session,
    *,
    start_at: datetime,
    end_at: datetime,
    track_id: Optional[UUID],
    preferred_agent_id: Optional[UUID],
    exclude_rdv_id: Optional[UUID] = None,
) -> Agent:
    if preferred_agent_id:
        preferred_agent = db.get(Agent, preferred_agent_id)
        if not preferred_agent or not preferred_agent.disponible:
            raise HTTPException(status_code=409, detail="preferred_agent_unavailable")
        conflict = has_appointment_conflict(
            db,
            agent_id=preferred_agent.id,
            start_at=start_at,
            end_at=end_at,
            exclude_rdv_id=exclude_rdv_id,
        )
        if conflict:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "agent_time_conflict",
                    "message": "L'agent assigne a deja un rendez-vous sur ce creneau",
                    "conflict": {
                        "id": str(conflict.id),
                        "start_at": str(conflict.start_at),
                        "end_at": str(conflict.end_at),
                    },
                },
            )
        return preferred_agent

    available_agents = find_available_agents(db, start_at, end_at, track_id)
    if not available_agents:
        raise HTTPException(status_code=409, detail="no_agent_available")
    return available_agents[0][0]


def assert_no_appointment_conflicts(
    db: Session,
    *,
    person_id: Optional[UUID],
    agent_id: Optional[UUID],
    start_at: datetime,
    end_at: datetime,
    exclude_rdv_id: Optional[UUID] = None,
) -> None:
    if end_at <= start_at:
        raise HTTPException(status_code=400, detail="invalid_timeslot")

    if person_id:
        person_conflict = has_appointment_conflict(
            db,
            person_id=person_id,
            start_at=start_at,
            end_at=end_at,
            exclude_rdv_id=exclude_rdv_id,
        )
        if person_conflict:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "time_conflict",
                    "message": "Le candidat a deja un rendez-vous sur ce creneau",
                    "conflict": {
                        "id": str(person_conflict.id),
                        "start_at": str(person_conflict.start_at),
                        "end_at": str(person_conflict.end_at),
                        "statut": person_conflict.statut,
                    },
                },
            )

    if agent_id:
        agent_conflict = has_appointment_conflict(
            db,
            agent_id=agent_id,
            start_at=start_at,
            end_at=end_at,
            exclude_rdv_id=exclude_rdv_id,
        )
        if agent_conflict:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "agent_time_conflict",
                    "message": "L'agent assigne a deja un rendez-vous sur ce creneau",
                    "conflict": {
                        "id": str(agent_conflict.id),
                        "start_at": str(agent_conflict.start_at),
                        "end_at": str(agent_conflict.end_at),
                    },
                },
            )


def serialize_rendezvous(db: Session, rdv: RendezVous) -> dict[str, Any]:
    person = db.get(Person, rdv.person_id) if rdv.person_id else None
    track = db.get(SchoolTrack, rdv.track_id) if rdv.track_id else None
    agent_payload = _agent_display_payload(db, rdv.agent_id)
    return {
        "id": str(rdv.id),
        "person_id": str(rdv.person_id) if rdv.person_id else None,
        "track_id": str(rdv.track_id) if rdv.track_id else None,
        "agent_id": str(rdv.agent_id) if rdv.agent_id else None,
        "agent": agent_payload["display_name"] if agent_payload else None,
        "assigned_agent": agent_payload,
        "start_at": rdv.start_at,
        "end_at": rdv.end_at,
        "statut": rdv.statut,
        "created_at": rdv.created_at,
        "updated_at": rdv.updated_at,
        "deleted_at": rdv.deleted_at,
        "person": (
            {
                "id": str(person.id),
                "first_name": person.first_name,
                "last_name": person.last_name,
                "email": person.email,
                "phone": person.phone,
            }
            if person
            else None
        ),
        "track": (
            {
                "id": str(track.id),
                "name": track.name,
            }
            if track
            else None
        ),
    }


def persist_rendezvous_and_sync_event(db: Session, rdv: RendezVous) -> Event:
    db.add(rdv)
    try:
        db.flush()
        event = sync_internal_event_for_rendezvous(db, rdv)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        detail = str(exc.orig or exc).lower()
        if "ex_rendezvous_agent_active_no_overlap" in detail or "ex_rendezvous_person_active_no_overlap" in detail:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="time_conflict") from exc
        raise
    db.refresh(rdv)
    db.refresh(event)
    return event
