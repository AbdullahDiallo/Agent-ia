from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID
from typing import Optional
import re

from fastapi import APIRouter, HTTPException, Form, Query, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import internal_calendar as ical
from ..security import require_role
from ..models import Event
from ..utils.http_errors import public_error_detail

# Router monté avec le préfixe '/calendar' pour aligner avec le frontend
router = APIRouter(prefix="/calendar", tags=["calendar"])


def _parse_iso(dt_str: str) -> datetime:
    v = (dt_str or "").strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    return datetime.fromisoformat(v)


def _parse_uuid(u: str) -> UUID:
    v = (u or "").strip()
    if not v:
        raise HTTPException(status_code=400, detail="calendar_id_required")
    try:
        return UUID(v)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_calendar_id")


@router.post("/calendars", dependencies=[Depends(require_role("manager"))])
def create_calendar(name: str = Form(...), owner: Optional[str] = Form(None), timezone: Optional[str] = Form(None), db: Session = Depends(get_db)):
    try:
        cal = ical.create_calendar(db, name=name, owner=owner, timezone=timezone)
        return {"id": str(cal.id), "name": cal.name, "owner": cal.owner, "timezone": cal.timezone, "is_active": cal.is_active, "created_at": cal.created_at}
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=public_error_detail(code="calendar_create_error", exc=e, logger_name=__name__),
        )


@router.get("/calendars", dependencies=[Depends(require_role("viewer"))])
def list_calendars(db: Session = Depends(get_db)):
    try:
        items = ical.list_calendars(db)
        return [{"id": str(c.id), "name": c.name, "owner": c.owner, "timezone": c.timezone, "is_active": c.is_active, "created_at": c.created_at} for c in items]
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=public_error_detail(code="calendar_list_error", exc=e, logger_name=__name__),
        )


@router.get("/stats", dependencies=[Depends(require_role("viewer"))])
def get_calendar_stats(db: Session = Depends(get_db)):
    """Statistiques du calendrier"""
    def format_duration(seconds: Optional[float]) -> str:
        if not seconds or seconds <= 0:
            return "0s"
        total_seconds = int(round(seconds))
        minutes, secs = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}h {minutes}min"
        if minutes:
            return f"{minutes}min {secs}s"
        return f"{secs}s"

    total_events = db.query(Event).count()
    confirmed = db.query(Event).filter(Event.status == "confirmed").count()
    pending = db.query(Event).filter(Event.status == "pending").count()
    cancelled = db.query(Event).filter(Event.status == "cancelled").count()

    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    week_end = datetime.now() + timedelta(days=7)

    today_count = db.query(Event).filter(
        Event.start_at >= today_start,
        Event.start_at < today_end
    ).count()

    week_count = db.query(Event).filter(
        Event.start_at >= datetime.now(),
        Event.start_at < week_end
    ).count()

    attendee_rows = db.query(Event.attendees).filter(Event.attendees.isnot(None)).all()
    total_participants = 0
    for (attendees,) in attendee_rows:
        if attendees:
            parts = [p.strip() for p in re.split(r"[;,]", attendees) if p.strip()]
            total_participants += len(parts)

    duration_rows = db.query(Event.start_at, Event.end_at).filter(
        Event.start_at.isnot(None),
        Event.end_at.isnot(None),
    ).all()
    durations = []
    for start_at, end_at in duration_rows:
        if end_at and start_at and end_at >= start_at:
            durations.append((end_at - start_at).total_seconds())
    avg_duration_seconds = sum(durations) / len(durations) if durations else 0

    return {
        "attendance_rate": round((confirmed / total_events * 100), 1) if total_events > 0 else 0,
        "cancel_rate": round((cancelled / total_events * 100), 1) if total_events > 0 else 0,
        "today_count": today_count,
        "week_count": week_count,
        "total_participants": total_participants,
        "avg_duration": format_duration(avg_duration_seconds),
        "total_events": total_events,
        "confirmed_count": confirmed,
        "pending_count": pending,
        "cancelled_count": cancelled,
    }


@router.post("/events", dependencies=[Depends(require_role("manager"))])
def create_event(
    calendar_id: str = Form(...),
    title: str = Form(...),
    start: str = Form(...),
    end: str = Form(...),
    db: Session = Depends(get_db),
):
    raise HTTPException(
        status_code=409,
        detail={
            "error": "calendar_event_write_disabled",
            "message": "Les modifications d'evenements calendrier doivent passer par /school/appointments afin de garantir la synchronisation avec les rendez-vous.",
        },
    )


@router.put("/events/{event_id}", dependencies=[Depends(require_role("manager"))])
def update_event(
    event_id: str,
    title: Optional[str] = Form(None),
    start: Optional[str] = Form(None),
    end: Optional[str] = Form(None),
    resource_key: Optional[str] = Form(None),
    attendees: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    status: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    raise HTTPException(
        status_code=409,
        detail={
            "error": "calendar_event_write_disabled",
            "message": "Les modifications d'evenements calendrier doivent passer par /school/appointments afin de garantir la synchronisation avec les rendez-vous.",
        },
    )


@router.delete("/events/{event_id}", dependencies=[Depends(require_role("manager"))])
def delete_event(event_id: str, db: Session = Depends(get_db)):
    raise HTTPException(
        status_code=409,
        detail={
            "error": "calendar_event_write_disabled",
            "message": "Les suppressions d'evenements calendrier doivent passer par /school/appointments afin de garantir la synchronisation avec les rendez-vous.",
        },
    )


@router.get("/events/{event_id}", dependencies=[Depends(require_role("viewer"))])
def get_event(event_id: str, db: Session = Depends(get_db)):
    try:
        ev_id = _parse_uuid(event_id)
        ev = ical.get_event(db, ev_id)
        if not ev:
            raise HTTPException(status_code=404, detail="event_not_found")
        return {
            "id": str(ev.id),
            "calendar_id": str(ev.calendar_id),
            "rendezvous_id": str(ev.rendezvous_id) if ev.rendezvous_id else None,
            "title": ev.title,
            "start_at": ev.start_at,
            "end_at": ev.end_at,
            "resource_key": ev.resource_key,
            "attendees": ev.attendees,
            "description": ev.description,
            "status": ev.status,
            "created_at": ev.created_at,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=public_error_detail(code="calendar_event_get_error", exc=e, logger_name=__name__),
        )


@router.get("/events", dependencies=[Depends(require_role("viewer"))])
def list_events(
    calendar_id: Optional[str] = Query(None),
    resource_key: Optional[str] = Query(None),
    time_min: Optional[str] = Query(None),
    time_max: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0, le=10_000),
    db: Session = Depends(get_db),
):
    """
    Liste les événements. calendar_id devient optionnel pour éviter les 422.
    Supporte aussi offset pour la pagination côté frontend.
    """
    try:
        cal_id = _parse_uuid(calendar_id) if calendar_id else None
        tmin = _parse_iso(time_min) if time_min else None
        tmax = _parse_iso(time_max) if time_max else None
        items = ical.list_events(
            db,
            calendar_id=cal_id,
            resource_key=resource_key,
            time_min=tmin,
            time_max=tmax,
            limit=limit,
            offset=offset,
        )

        # Compter le total pour la pagination
        q = db.query(Event)
        if cal_id:
            q = q.filter(Event.calendar_id == cal_id)
        if resource_key:
            q = q.filter(Event.resource_key == resource_key)
        total = q.count()

        return {
            "items": [
                {
                    "id": str(ev.id),
                    "calendar_id": str(ev.calendar_id),
                    "rendezvous_id": str(ev.rendezvous_id) if ev.rendezvous_id else None,
                    "title": ev.title,
                    "start_at": ev.start_at,
                    "end_at": ev.end_at,
                    "resource_key": ev.resource_key,
                    "attendees": ev.attendees,
                    "description": ev.description,
                    "status": ev.status,
                    "created_at": ev.created_at,
                }
                for ev in items
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=public_error_detail(code="calendar_event_list_error", exc=e, logger_name=__name__),
        )


@router.get("/availability", dependencies=[Depends(require_role("viewer"))])
def availability(
    calendar_id: str = Query(...),
    time_min: str = Query(..., description="ISO datetime"),
    time_max: str = Query(..., description="ISO datetime"),
    db: Session = Depends(get_db),
):
    try:
        cal_id = _parse_uuid(calendar_id)
        start = _parse_iso(time_min)
        end = _parse_iso(time_max)
        resp = ical.availability(db, calendar_id=cal_id, time_min=start, time_max=end)
        return resp
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=public_error_detail(code="calendar_availability_error", exc=e, logger_name=__name__),
        )


@router.get("/free-slots", dependencies=[Depends(require_role("viewer"))])
def free_slots(
    calendar_id: str = Query(...),
    time_min: str = Query(..., description="ISO datetime"),
    time_max: str = Query(..., description="ISO datetime"),
    granularity_minutes: int = Query(30, ge=5, le=240),
    resource_key: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    try:
        cal_id = _parse_uuid(calendar_id)
        start = _parse_iso(time_min)
        end = _parse_iso(time_max)
        resp = ical.free_slots(db, calendar_id=cal_id, time_min=start, time_max=end, granularity_minutes=granularity_minutes, resource_key=resource_key)
        return resp
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=public_error_detail(code="calendar_slots_error", exc=e, logger_name=__name__),
        )
