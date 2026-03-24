import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List

from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from ..models import Event, EmailLog, SMSLog, Conversation, RendezVous


def _parse_uuid(val: Optional[str]):
    if not val:
        return None
    try:
        return uuid.UUID(val)
    except Exception:
        return None


def events_count_by_day(
    db: Session,
    *,
    time_min: datetime,
    time_max: datetime,
    calendar_id: Optional[str] = None,
    resource_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    filters = [Event.start_at >= time_min, Event.start_at < time_max]
    cal_uuid = _parse_uuid(calendar_id)
    if cal_uuid:
        filters.append(Event.calendar_id == cal_uuid)
    if resource_key:
        filters.append(Event.resource_key == resource_key)

    day = func.date_trunc('day', Event.start_at).label('day')
    q = (
        db.query(day, func.count(Event.id))
        .filter(and_(*filters))
        .group_by(day)
        .order_by(day)
    )
    return [{"date": d.isoformat(), "count": c} for d, c in q.all()]


def notifications_summary(
    db: Session,
    *,
    time_min: datetime,
    time_max: datetime,
    calendar_id: Optional[str] = None,  # kept for symmetry (not used directly)
    resource_key: Optional[str] = None,  # kept for symmetry (not used directly)
) -> Dict[str, Any]:
    # Emails
    e_sent = db.query(func.count(EmailLog.id)).filter(
        and_(EmailLog.created_at >= time_min, EmailLog.created_at < time_max, EmailLog.statut == 'sent')
    ).scalar() or 0
    e_failed = db.query(func.count(EmailLog.id)).filter(
        and_(EmailLog.created_at >= time_min, EmailLog.created_at < time_max, EmailLog.statut == 'failed')
    ).scalar() or 0

    # SMS
    s_sent = db.query(func.count(SMSLog.id)).filter(
        and_(SMSLog.created_at >= time_min, SMSLog.created_at < time_max, SMSLog.statut == 'sent')
    ).scalar() or 0
    s_failed = db.query(func.count(SMSLog.id)).filter(
        and_(SMSLog.created_at >= time_min, SMSLog.created_at < time_max, SMSLog.statut == 'failed')
    ).scalar() or 0

    return {
        "emails_sent": e_sent,
        "emails_failed": e_failed,
        "sms_sent": s_sent,
        "sms_failed": s_failed,
    }


def notifications_by_day(
    db: Session,
    *,
    time_min: datetime,
    time_max: datetime,
) -> Dict[str, List[Dict[str, Any]]]:
    # Emails by day
    e_day = func.date_trunc('day', EmailLog.created_at).label('day')
    e_q = (
        db.query(e_day, EmailLog.statut, func.count(EmailLog.id))
        .filter(and_(EmailLog.created_at >= time_min, EmailLog.created_at < time_max))
        .group_by(e_day, EmailLog.statut)
        .order_by(e_day)
    )
    emails: Dict[str, Dict[str, int]] = {}
    for d, s, c in e_q.all():
        key = d.isoformat()
        emails.setdefault(key, {"sent": 0, "failed": 0})
        emails[key][s] = c

    emails_series = [
        {"date": k, "sent": v.get("sent", 0), "failed": v.get("failed", 0)}
        for k, v in sorted(emails.items())
    ]

    # SMS by day
    s_day = func.date_trunc('day', SMSLog.created_at).label('day')
    s_q = (
        db.query(s_day, SMSLog.statut, func.count(SMSLog.id))
        .filter(and_(SMSLog.created_at >= time_min, SMSLog.created_at < time_max))
        .group_by(s_day, SMSLog.statut)
        .order_by(s_day)
    )
    sms: Dict[str, Dict[str, int]] = {}
    for d, s, c in s_q.all():
        key = d.isoformat()
        sms.setdefault(key, {"sent": 0, "failed": 0})
        sms[key][s] = c

    sms_series = [
        {"date": k, "sent": v.get("sent", 0), "failed": v.get("failed", 0)}
        for k, v in sorted(sms.items())
    ]

    return {"emails": emails_series, "sms": sms_series}


def recent_notifications(
    db: Session,
    *,
    time_min: Optional[datetime] = None,
    time_max: Optional[datetime] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    q_email = db.query(EmailLog)
    q_sms = db.query(SMSLog)
    if time_min:
        q_email = q_email.filter(EmailLog.created_at >= time_min)
        q_sms = q_sms.filter(SMSLog.created_at >= time_min)
    if time_max:
        q_email = q_email.filter(EmailLog.created_at < time_max)
        q_sms = q_sms.filter(SMSLog.created_at < time_max)

    emails = [
        {
            "type": "email",
            "id": str(e.id),
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "person_id": str(e.person_id) if e.person_id else None,
            "status": e.statut,
            "provider_id": e.provider_id,
            "subject": e.sujet,
        }
        for e in q_email.order_by(EmailLog.created_at.desc()).limit(limit).all()
    ]
    sms = [
        {
            "type": "sms",
            "id": str(s.id),
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "person_id": str(s.person_id) if s.person_id else None,
            "status": s.statut,
            "provider_id": s.provider_id,
            "content": (s.contenu[:140] + "…") if s.contenu and len(s.contenu) > 140 else s.contenu,
        }
        for s in q_sms.order_by(SMSLog.created_at.desc()).limit(limit).all()
    ]

    # Merge and sort by created_at desc
    all_rows = emails + sms
    all_rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return all_rows[:limit]


def conversations_by_channel(
    db: Session,
    *,
    time_min: datetime,
    time_max: datetime,
) -> Dict[str, int]:
    """Compte le nombre de conversations par canal sur la période donnée."""
    q = (
        db.query(Conversation.canal, func.count(Conversation.id))
        .filter(and_(Conversation.created_at >= time_min, Conversation.created_at < time_max))
        .group_by(Conversation.canal)
    )
    res: Dict[str, int] = {}
    for canal, count in q.all():
        key = canal or "unknown"
        res[key] = count
    return res


def rendezvous_stats(
    db: Session,
    *,
    time_min: datetime,
    time_max: datetime,
) -> Dict[str, Any]:
    """Retourne des statistiques simples sur les rendez-vous (RDV) sur la période."""
    q = db.query(RendezVous.statut, func.count(RendezVous.id)).filter(
        and_(RendezVous.created_at >= time_min, RendezVous.created_at < time_max)
    ).group_by(RendezVous.statut)
    stats: Dict[str, int] = {}
    total = 0
    for statut, count in q.all():
        key = statut or "unknown"
        stats[key] = count
        total += count
    return {"total": total, "by_status": stats}
