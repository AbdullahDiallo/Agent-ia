from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..db import get_db
from ..services import metrics as mx
from ..security import require_role, get_principal, Principal
from ..models import (
    Agent,
    Conversation,
    EmailLog,
    Message,
    Person,
    PersonRole,
    RendezVous,
    Role,
    SchoolTrack,
    SMSLog,
    User,
)
from ..utils.rbac import get_agent_from_principal
from ..utils.http_errors import public_error_detail

# Statuts RDV actifs (inclut 'pending' pour legacy)
ACTIVE_RDV_STATUSES = ["created", "confirmed", "reminder_sent", "pending"]
APP_STARTED_AT = datetime.now()
# Router monté avec le préfixe '/dashboard' pour aligner avec le frontend
# Accessible à tous les rôles authentifiés (agent, viewer, manager, admin)
router = APIRouter(
    prefix="/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(require_role("agent|viewer|manager|admin"))],
)


def _format_uptime_delta(started_at: datetime) -> str:
    delta = datetime.now() - started_at
    seconds = max(0, int(delta.total_seconds()))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days > 0:
        return f"{days}j {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}min"
    return f"{minutes}min"


def _compute_response_time(db: Session) -> tuple[float, str]:
    user_first_rows = db.query(Message.conversation_id, func.min(Message.created_at)).filter(
        Message.role == "user"
    ).group_by(Message.conversation_id).all()
    assistant_first_rows = db.query(Message.conversation_id, func.min(Message.created_at)).filter(
        Message.role == "assistant"
    ).group_by(Message.conversation_id).all()
    user_first = {cid: ts for cid, ts in user_first_rows}
    assistant_first = {cid: ts for cid, ts in assistant_first_rows}
    wait_times: list[float] = []
    for cid, user_ts in user_first.items():
        assistant_ts = assistant_first.get(cid)
        if assistant_ts and assistant_ts >= user_ts:
            wait_times.append((assistant_ts - user_ts).total_seconds())
    if not wait_times:
        return 0.0, "0s"
    avg_seconds = sum(wait_times) / len(wait_times)
    total_seconds = int(round(avg_seconds))
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        formatted = f"{hours}h {minutes}min"
    elif minutes:
        formatted = f"{minutes}min {secs}s"
    else:
        formatted = f"{secs}s"
    return avg_seconds, formatted


@router.get("/metrics/overview")
def metrics_overview(
    time_min: str = Query(..., description="ISO datetime"),
    time_max: str = Query(..., description="ISO datetime"),
    calendar_id: Optional[str] = Query(None),
    resource_key: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    try:
        start = datetime.fromisoformat(time_min)
        end = datetime.fromisoformat(time_max)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_datetime")

    try:
        events_series = mx.events_count_by_day(
            db,
            time_min=start,
            time_max=end,
            calendar_id=calendar_id,
            resource_key=resource_key,
        )
        notif = mx.notifications_summary(
            db,
            time_min=start,
            time_max=end,
            calendar_id=calendar_id,
            resource_key=resource_key,
        )
        conv_channels = mx.conversations_by_channel(
            db,
            time_min=start,
            time_max=end,
        )
        rdv_stats = mx.rendezvous_stats(
            db,
            time_min=start,
            time_max=end,
        )
        return {
            "events_count_by_day": events_series,
            "notifications": notif,
            "conversations_by_channel": conv_channels,
            "rendezvous": rdv_stats,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=public_error_detail(code="dashboard_overview_error", exc=e, logger_name=__name__),
        )


@router.get("/metrics/notifications")
def metrics_notifications(
    time_min: str = Query(..., description="ISO datetime"),
    time_max: str = Query(..., description="ISO datetime"),
    db: Session = Depends(get_db),
):
    try:
        start = datetime.fromisoformat(time_min)
        end = datetime.fromisoformat(time_max)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_datetime")

    try:
        series = mx.notifications_by_day(db, time_min=start, time_max=end)
        return series
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=public_error_detail(code="dashboard_notifications_error", exc=e, logger_name=__name__),
        )


# Alias routes pour compatibilité avec le frontend
@router.get("/overview")
def overview_alias(
    time_min: Optional[str] = Query(None, description="ISO datetime"),
    time_max: Optional[str] = Query(None, description="ISO datetime"),
    calendar_id: Optional[str] = Query(None),
    resource_key: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Alias pour /dashboard/metrics/overview - Par défaut 7 derniers jours"""
    from datetime import datetime, timedelta
    
    # Valeurs par défaut : 7 derniers jours
    if not time_min:
        time_min = (datetime.now() - timedelta(days=7)).isoformat()
    if not time_max:
        time_max = datetime.now().isoformat()
    
    return metrics_overview(time_min, time_max, calendar_id, resource_key, db)


@router.get("/notifications-series")
def notifications_series_alias(
    time_min: Optional[str] = Query(None, description="ISO datetime"),
    time_max: Optional[str] = Query(None, description="ISO datetime"),
    db: Session = Depends(get_db),
):
    """Alias pour /dashboard/metrics/notifications - Par défaut 7 derniers jours"""
    from datetime import datetime, timedelta
    
    # Valeurs par défaut : 7 derniers jours
    if not time_min:
        time_min = (datetime.now() - timedelta(days=7)).isoformat()
    if not time_max:
        time_max = datetime.now().isoformat()
    
    return metrics_notifications(time_min, time_max, db)


@router.get("/notifications/logs")
def notifications_logs(
    time_min: str = Query(None, description="ISO datetime"),
    time_max: str = Query(None, description="ISO datetime"),
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    try:
        start = datetime.fromisoformat(time_min) if time_min else None
        end = datetime.fromisoformat(time_max) if time_max else None
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_datetime")
    try:
        rows = mx.recent_notifications(db, time_min=start, time_max=end, limit=limit)
        return {"items": rows}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=public_error_detail(code="dashboard_notifications_logs_error", exc=e, logger_name=__name__),
        )


# Nouveaux endpoints pour les statistiques détaillées
@router.get("/stats/persons")
def get_persons_stats(db: Session = Depends(get_db)):
    """Statistiques des personnes."""
    from datetime import timedelta
    
    total = db.query(func.count(Person.id)).scalar() or 0
    active = db.query(func.count(Person.id)).filter(Person.status == "active").scalar() or 0
    new_7d = (
        db.query(func.count(Person.id))
        .filter(Person.created_at >= datetime.now() - timedelta(days=7))
        .scalar()
        or 0
    )
    
    return {
        "total": total,
        "active": active,
        "new_7d": new_7d,
    }


@router.get("/stats/tracks")
def get_tracks_stats(db: Session = Depends(get_db)):
    """Statistiques des filieres."""
    from datetime import timedelta
    
    total = db.query(func.count(SchoolTrack.id)).scalar() or 0
    disponibles = (
        db.query(func.count(SchoolTrack.id))
        .filter(SchoolTrack.is_active == True)
        .scalar()
        or 0
    )
    new_7d = (
        db.query(func.count(SchoolTrack.id))
        .filter(SchoolTrack.created_at >= datetime.now() - timedelta(days=7))
        .scalar()
        or 0
    )
    avg_price = db.query(func.avg(SchoolTrack.annual_fee)).scalar() or 0
    
    return {
        "total": total,
        "disponibles": disponibles,
        "new_7d": new_7d,
        "avg_price": float(avg_price),
    }


@router.get("/stats/conversations")
def get_conversations_stats(db: Session = Depends(get_db)):
    """Statistiques des conversations"""
    from sqlalchemy import func
    from ..models import Conversation
    from datetime import timedelta
    
    total = db.query(Conversation).count()
    by_canal = db.query(
        Conversation.canal, 
        func.count(Conversation.id)
    ).group_by(Conversation.canal).all()
    new_7d = db.query(Conversation).filter(
        Conversation.created_at >= datetime.now() - timedelta(days=7)
    ).count()
    
    return {
        "total": total,
        "by_canal": {canal: count for canal, count in by_canal if canal},
        "new_7d": new_7d
    }


@router.get("/trends")
def get_trends(db: Session = Depends(get_db)):
    """Tendances (comparaison mois actuel vs mois précédent)"""
    from datetime import timedelta
    
    now = datetime.now()
    current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_start = (current_month_start - timedelta(days=1)).replace(day=1)
    
    # Personnes
    persons_current = (
        db.query(func.count(Person.id))
        .filter(Person.created_at >= current_month_start)
        .scalar()
        or 0
    )
    persons_last = (
        db.query(func.count(Person.id))
        .filter(Person.created_at >= last_month_start, Person.created_at < current_month_start)
        .scalar()
        or 0
    )
    
    # Filieres
    tracks_current = (
        db.query(func.count(SchoolTrack.id))
        .filter(SchoolTrack.created_at >= current_month_start)
        .scalar()
        or 0
    )
    tracks_last = (
        db.query(func.count(SchoolTrack.id))
        .filter(SchoolTrack.created_at >= last_month_start, SchoolTrack.created_at < current_month_start)
        .scalar()
        or 0
    )
    
    # RDV
    rdv_current = db.query(RendezVous).filter(
        RendezVous.created_at >= current_month_start
    ).count()
    rdv_last = db.query(RendezVous).filter(
        RendezVous.created_at >= last_month_start,
        RendezVous.created_at < current_month_start
    ).count()
    
    # Conversations
    conv_current = db.query(Conversation).filter(
        Conversation.created_at >= current_month_start
    ).count()
    conv_last = db.query(Conversation).filter(
        Conversation.created_at >= last_month_start,
        Conversation.created_at < current_month_start
    ).count()
    
    def calc_trend(current, last):
        if last == 0:
            return {"value": 100.0 if current > 0 else 0.0, "isPositive": current > 0}
        pct = ((current - last) / last) * 100
        return {"value": abs(round(pct, 1)), "isPositive": pct >= 0}
    
    return {
        "persons": calc_trend(persons_current, persons_last),
        "tracks": calc_trend(tracks_current, tracks_last),
        "rendezvous": calc_trend(rdv_current, rdv_last),
        "conversations": calc_trend(conv_current, conv_last)
    }


@router.get("/metrics/conversion-rate")
def get_conversion_rate(db: Session = Depends(get_db)):
    """Taux de conversion candidat -> etudiant."""
    candidate_count = (
        db.query(func.count(func.distinct(PersonRole.person_id)))
        .filter(PersonRole.role == "candidate")
        .scalar()
        or 0
    )
    student_count = (
        db.query(func.count(func.distinct(PersonRole.person_id)))
        .filter(PersonRole.role == "student")
        .scalar()
        or 0
    )
    rate = (student_count / candidate_count * 100) if candidate_count > 0 else 0
    
    return {"rate": round(rate, 1)}


@router.get("/metrics/satisfaction")
def get_satisfaction(db: Session = Depends(get_db)):
    """Score de satisfaction calculé à partir des sentiments des conversations."""
    sentiment_avg = db.query(func.avg(Conversation.sentiment_score)).scalar()
    if sentiment_avg is None:
        return {"score": 0, "total_reviews": 0}
    clamped = max(min(float(sentiment_avg), 1.0), -1.0)
    score = round(((clamped + 1) / 2) * 5, 1)
    total_reviews = db.query(Conversation).filter(Conversation.sentiment_score.isnot(None)).count()
    return {"score": score, "total_reviews": total_reviews}


@router.get("/metrics/response-time")
def get_response_time(db: Session = Depends(get_db)):
    """Temps de réponse moyen calculé depuis les timestamps des messages."""
    avg_seconds, formatted = _compute_response_time(db)
    return {"avg_seconds": avg_seconds, "formatted": formatted}


# ============= DASHBOARDS PAR RÔLE =============

@router.get("/admin/stats", dependencies=[Depends(require_role("admin"))])
def get_admin_stats(db: Session = Depends(get_db)):
    """Statistiques pour le dashboard Admin - Vue système complète"""
    now = datetime.now()
    week_start = now - timedelta(days=7)
    
    # Statistiques utilisateurs par rôle
    total_users = db.query(User).count()
    users_by_role = db.query(Role.name, func.count(User.id)).join(
        User, User.role_id == Role.id
    ).group_by(Role.name).all()
    
    role_counts = {role: count for role, count in users_by_role}
    
    # Utilisateurs actifs (ayant une session dans les 7 derniers jours)
    # Note: Nécessiterait une table de sessions pour être précis
    active_last_7d = db.query(User).filter(
        User.updated_at >= week_start
    ).count()
    
    # Statistiques système globales
    total_contacts = db.query(func.count(Person.id)).scalar() or 0
    total_tracks = db.query(func.count(SchoolTrack.id)).scalar() or 0
    total_rdv = db.query(func.count(RendezVous.id)).scalar() or 0
    total_conversations = db.query(func.count(Conversation.id)).scalar() or 0
    total_emails = db.query(func.count(EmailLog.id)).scalar() or 0
    total_sms = db.query(func.count(SMSLog.id)).scalar() or 0
    
    # Activité de la semaine
    new_users_week = db.query(User).filter(User.created_at >= week_start).count()
    new_contacts_week = (
        db.query(func.count(Person.id))
        .filter(Person.created_at >= week_start)
        .scalar()
        or 0
    )
    new_tracks_week = (
        db.query(func.count(SchoolTrack.id))
        .filter(SchoolTrack.created_at >= week_start)
        .scalar()
        or 0
    )
    new_rdv_week = (
        db.query(func.count(RendezVous.id))
        .filter(RendezVous.created_at >= week_start)
        .scalar()
        or 0
    )
    emails_sent_week = (
        db.query(func.count(EmailLog.id))
        .filter(
        EmailLog.created_at >= week_start,
        EmailLog.statut == "sent"
        )
        .scalar()
        or 0
    )
    sms_sent_week = (
        db.query(func.count(SMSLog.id))
        .filter(
        SMSLog.created_at >= week_start,
        SMSLog.statut == "sent"
        )
        .scalar()
        or 0
    )
    avg_response_seconds, avg_response_label = _compute_response_time(db)
    total_attempts = int(total_emails + total_sms)
    successful_attempts = int(
        (db.query(func.count(EmailLog.id)).filter(EmailLog.statut == "sent").scalar() or 0)
        + (db.query(func.count(SMSLog.id)).filter(SMSLog.statut == "sent").scalar() or 0)
    )
    success_rate = round((successful_attempts / total_attempts) * 100, 1) if total_attempts else 0.0
    uptime_label = _format_uptime_delta(APP_STARTED_AT)
    
    return {
        "users": {
            "total": total_users,
            "admins": role_counts.get("admin", 0),
            "managers": role_counts.get("manager", 0),
            "agents": role_counts.get("agent", 0),
            "viewers": role_counts.get("viewer", 0),
            "active_last_7d": active_last_7d
        },
        "system": {
            "total_contacts": total_contacts,
            "total_tracks": total_tracks,
            "total_rdv": total_rdv,
            "total_conversations": total_conversations,
            "total_emails": total_emails,
            "total_sms": total_sms
        },
        "activity": {
            "new_users_week": new_users_week,
            "new_contacts_week": new_contacts_week,
            "new_tracks_week": new_tracks_week,
            "new_rdv_week": new_rdv_week,
            "emails_sent_week": emails_sent_week,
            "sms_sent_week": sms_sent_week
        },
        "performance": {
            "avg_response_time": avg_response_label,
            "avg_response_seconds": round(float(avg_response_seconds), 2),
            "success_rate": success_rate,
            "uptime": uptime_label,
        }
    }


@router.get("/manager/stats", dependencies=[Depends(require_role("manager|admin"))])
def get_manager_stats(db: Session = Depends(get_db)):
    """Statistiques pour le dashboard Manager - Vue d'ensemble équipe"""
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    
    # Appels
    total_calls = db.query(Conversation).filter(Conversation.canal == "call").count()
    today_calls = db.query(Conversation).filter(
        Conversation.canal == "call",
        Conversation.created_at >= today_start
    ).count()
    week_calls = db.query(Conversation).filter(
        Conversation.canal == "call",
        Conversation.created_at >= week_start
    ).count()
    
    # SMS
    total_sms = db.query(SMSLog).count()
    sent_sms = db.query(SMSLog).filter(SMSLog.statut == "sent").count()
    
    # Emails
    total_emails = db.query(EmailLog).count()
    sent_emails = db.query(EmailLog).filter(EmailLog.statut == "sent").count()
    
    # Conversations
    active_conversations = db.query(Conversation).filter(Conversation.status == "active").count()
    
    # Rendez-vous
    upcoming_rdv = db.query(RendezVous).filter(
        RendezVous.start_at >= now,
        RendezVous.statut.in_(ACTIVE_RDV_STATUSES)
    ).count()
    past_rdv = db.query(RendezVous).filter(
        RendezVous.start_at < now
    ).count()
    
    # Contacts (personnes)
    total_contacts = db.query(func.count(Person.id)).scalar() or 0
    contacts_actifs = (
        db.query(func.count(Person.id))
        .filter(Person.status == "active")
        .scalar()
        or 0
    )
    candidates = (
        db.query(func.count(func.distinct(PersonRole.person_id)))
        .filter(PersonRole.role == "candidate")
        .scalar()
        or 0
    )
    students = (
        db.query(func.count(func.distinct(PersonRole.person_id)))
        .filter(PersonRole.role == "student")
        .scalar()
        or 0
    )
    taux_conversion = round((students / candidates) * 100, 1) if candidates > 0 else 0
    
    return {
        "calls": {
            "total": total_calls,
            "today": today_calls,
            "week": week_calls
        },
        "sms": {
            "total": total_sms,
            "sent": sent_sms
        },
        "emails": {
            "total": total_emails,
            "sent": sent_emails
        },
        "conversations": {
            "active": active_conversations
        },
        "rendezvous": {
            "upcoming": upcoming_rdv,
            "past": past_rdv
        },
        "contacts": {
            "total": total_contacts,
            "actifs": contacts_actifs,
            "candidats": candidates,
            "etudiants": students,
            "taux_conversion": taux_conversion
        },
    }


@router.get("/agent/stats", dependencies=[Depends(require_role("agent|manager|admin"))])
def get_agent_stats(
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal)
):
    """Statistiques pour le dashboard Agent - Métriques personnelles"""
    agent = get_agent_from_principal(db, principal)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent non trouvé")
    
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    
    # Mes rendez-vous
    mes_rdv_aujourdhui = db.query(RendezVous).filter(
        RendezVous.agent_id == agent.id,
        RendezVous.start_at >= today_start,
        RendezVous.start_at < today_start + timedelta(days=1)
    ).count()
    
    mes_rdv_semaine = db.query(RendezVous).filter(
        RendezVous.agent_id == agent.id,
        RendezVous.start_at >= week_start
    ).count()
    
    mes_rdv_a_venir = db.query(RendezVous).filter(
        RendezVous.agent_id == agent.id,
        RendezVous.start_at >= now,
        RendezVous.statut.in_(ACTIVE_RDV_STATUSES)
    ).order_by(RendezVous.start_at).limit(5).all()
    
    # Mes conversations
    mes_conversations_actives = db.query(Conversation).filter(
        Conversation.assigned_to == agent.user_id,
        Conversation.status == "active"
    ).count()
    
    # Mes contacts (via rendez-vous)
    mes_contacts = db.query(Person).join(
        RendezVous, Person.id == RendezVous.person_id
    ).filter(
        RendezVous.agent_id == agent.id
    ).distinct().count()
    
    return {
        "rendezvous": {
            "aujourdhui": mes_rdv_aujourdhui,
            "cette_semaine": mes_rdv_semaine,
            "a_venir": [
                {
                    "id": str(rdv.id),
                    "start_at": rdv.start_at,
                    "person_id": str(rdv.person_id) if rdv.person_id else None,
                    "statut": rdv.statut
                }
                for rdv in mes_rdv_a_venir
            ]
        },
        "conversations": {
            "actives": mes_conversations_actives
        },
        "contacts": {"total": mes_contacts},
        "agent_info": {
            "specialite": agent.specialite,
            "disponible": agent.disponible,
            "max_rdv_par_jour": agent.max_rdv_par_jour
        }
    }


@router.get("/viewer/stats")
def get_viewer_stats(db: Session = Depends(get_db)):
    """Statistiques pour le dashboard Viewer - Vue d'ensemble en lecture seule"""
    now = datetime.now()
    week_start = now - timedelta(days=7)
    
    # Statistiques globales
    total_contacts = db.query(func.count(Person.id)).scalar() or 0
    total_tracks = db.query(func.count(SchoolTrack.id)).scalar() or 0
    total_rdv = db.query(func.count(RendezVous.id)).scalar() or 0
    total_conversations = db.query(func.count(Conversation.id)).scalar() or 0
    
    # Nouveautés de la semaine
    new_contacts_week = (
        db.query(func.count(Person.id))
        .filter(Person.created_at >= week_start)
        .scalar()
        or 0
    )
    new_tracks_week = (
        db.query(func.count(SchoolTrack.id))
        .filter(SchoolTrack.created_at >= week_start)
        .scalar()
        or 0
    )
    new_rdv_week = (
        db.query(func.count(RendezVous.id))
        .filter(RendezVous.created_at >= week_start)
        .scalar()
        or 0
    )
    
    return {
        "totaux": {
            "contacts": total_contacts,
            "filieres": total_tracks,
            "rendezvous": total_rdv,
            "conversations": total_conversations
        },
        "cette_semaine": {
            "nouveaux_contacts": new_contacts_week,
            "nouvelles_filieres": new_tracks_week,
            "nouveaux_rdv": new_rdv_week
        }
    }
