from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..models import Calendar, Event


def create_calendar(db: Session, *, name: str, owner: Optional[str] = None, timezone: Optional[str] = None) -> Calendar:
    cal = Calendar(name=name, owner=owner, timezone=timezone)
    db.add(cal)
    db.commit()
    db.refresh(cal)
    return cal


def get_calendar(db: Session, calendar_id) -> Optional[Calendar]:
    return db.get(Calendar, calendar_id)


def list_calendars(db: Session) -> List[Calendar]:
    return db.query(Calendar).order_by(Calendar.created_at.desc()).all()


def create_event(
    db: Session,
    *,
    calendar_id: str,
    rendezvous_id: Optional[str] = None,
    title: str,
    start_at: datetime,
    end_at: datetime,
    resource_key: Optional[str] = None,
    attendees: Optional[str] = None,
    description: Optional[str] = None,
    status: str = "confirmed",
) -> Event:
    ev = Event(
        calendar_id=calendar_id,
        rendezvous_id=rendezvous_id,
        title=title,
        start_at=start_at,
        end_at=end_at,
        resource_key=resource_key,
        attendees=attendees,
        description=description,
        status=status,
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


def get_event(db: Session, event_id) -> Optional[Event]:
    return db.get(Event, event_id)


def update_event(
    db: Session,
    event_id,
    *,
    rendezvous_id: Optional[str] = ...,
    title: Optional[str] = None,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
    resource_key: Optional[str] = ...,
    attendees: Optional[str] = ...,
    description: Optional[str] = ...,
    status: Optional[str] = None,
) -> Optional[Event]:
    ev = db.get(Event, event_id)
    if not ev:
        return None
    if title is not None:
        ev.title = title
    if rendezvous_id is not ...:
        ev.rendezvous_id = rendezvous_id
    if start_at is not None:
        ev.start_at = start_at
    if end_at is not None:
        ev.end_at = end_at
    if resource_key is not ...:
        ev.resource_key = resource_key
    if attendees is not ...:
        ev.attendees = attendees
    if description is not ...:
        ev.description = description
    if status is not None:
        ev.status = status
    db.commit()
    db.refresh(ev)
    return ev


def delete_event(db: Session, event_id) -> bool:
    ev = db.get(Event, event_id)
    if not ev:
        return False
    db.delete(ev)
    db.commit()
    return True


def list_events(
    db: Session,
    *,
    calendar_id: Optional[str] = None,
    resource_key: Optional[str] = None,
    time_min: Optional[datetime] = None,
    time_max: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Event]:
    q = db.query(Event)
    if calendar_id:
        q = q.filter(Event.calendar_id == calendar_id)
    if resource_key:
        q = q.filter(Event.resource_key == resource_key)
    if time_min and time_max:
        q = q.filter(or_(
            and_(Event.start_at >= time_min, Event.start_at < time_max),
            and_(Event.end_at > time_min, Event.end_at <= time_max),
            and_(Event.start_at <= time_min, Event.end_at >= time_max),
        ))
    elif time_min:
        q = q.filter(Event.end_at > time_min)
    elif time_max:
        q = q.filter(Event.start_at < time_max)
    return q.order_by(Event.start_at.asc()).limit(limit).offset(offset).all()


def availability(db: Session, *, calendar_id: str, time_min: datetime, time_max: datetime) -> dict:
    events = list_events(db, calendar_id=calendar_id, time_min=time_min, time_max=time_max, limit=500)
    busy = [
        {
            "start": e.start_at.isoformat(),
            "end": e.end_at.isoformat(),
            "title": e.title,
        }
        for e in events
    ]
    return {"calendar_id": calendar_id, "timeMin": time_min.isoformat(), "timeMax": time_max.isoformat(), "busy": busy}


def has_conflict(db: Session, *, calendar_id: str, start_at: datetime, end_at: datetime, resource_key: Optional[str] = None, exclude_event_id: Optional[str] = None) -> Optional[Event]:
    # Conflict if any non-cancelled event overlaps the [start_at, end_at] interval
    q = db.query(Event).filter(Event.calendar_id == calendar_id)
    # Exclude cancelled events from conflict detection
    q = q.filter(Event.status != "cancelled")
    if resource_key:
        q = q.filter(Event.resource_key == resource_key)
    if exclude_event_id:
        q = q.filter(Event.id != exclude_event_id)
    q = q.filter(
        or_(
            and_(Event.start_at >= start_at, Event.start_at < end_at),
            and_(Event.end_at > start_at, Event.end_at <= end_at),
            and_(Event.start_at <= start_at, Event.end_at >= end_at),
        )
    )
    return q.first()


def free_slots(
    db: Session,
    *,
    calendar_id: str,
    time_min: datetime,
    time_max: datetime,
    granularity_minutes: int = 30,
    resource_key: Optional[str] = None,
) -> dict:
    # Get busy intervals sorted
    items = list_events(db, calendar_id=calendar_id, resource_key=resource_key, time_min=time_min, time_max=time_max, limit=500)
    busy = sorted([(e.start_at, e.end_at) for e in items], key=lambda x: x[0])

    # Merge overlapping busy intervals
    merged: list[tuple[datetime, datetime]] = []
    for s, e in busy:
        if not merged:
            merged.append((s, e))
        else:
            ps, pe = merged[-1]
            if s <= pe:
                merged[-1] = (ps, max(pe, e))
            else:
                merged.append((s, e))

    # Compute free intervals inside [time_min, time_max]
    cur = time_min
    free: list[tuple[datetime, datetime]] = []
    for s, e in merged:
        if s > cur:
            free.append((cur, min(s, time_max)))
        cur = max(cur, e)
        if cur >= time_max:
            break
    if cur < time_max:
        free.append((cur, time_max))

    # Split into slots of granularity
    from datetime import timedelta
    slot_len = timedelta(minutes=granularity_minutes)
    slots: list[dict] = []
    for s, e in free:
        t = s
        while t + slot_len <= e:
            slots.append({"start": t.isoformat(), "end": (t + slot_len).isoformat()})
            t = t + slot_len

    return {
        "calendar_id": calendar_id,
        "resource_key": resource_key,
        "timeMin": time_min.isoformat(),
        "timeMax": time_max.isoformat(),
        "granularity_minutes": granularity_minutes,
        "slots": slots,
    }
