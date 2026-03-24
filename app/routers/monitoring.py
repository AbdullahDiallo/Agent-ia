from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text, func, case
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db, open_db_session
from ..models import AuditEvent, LoginAttempt, OutboxEvent
from ..redis_client import get_redis
from ..security import require_role, get_principal, Principal
from ..services.email import EmailService
from ..services.llm import LLMService
from ..services.sms import SMSService
from ..services.stt import STTService
from ..services.tts import TTSService
from ..services.whatsapp import WhatsAppService
from ..services.agent_observability import build_agent_observability_report
from ..services.security_controls import (
    block_ip,
    unblock_ip,
    list_blocked_ips,
    set_emergency_mode,
    get_emergency_state,
)
from ..services.tenant_context import require_tenant_guard
from ..utils.rbac import log_audit_event
import httpx

router = APIRouter(prefix="/monitoring", tags=["monitoring"], dependencies=[Depends(require_role("admin"))])


class EmergencyPayload(BaseModel):
    enabled: bool
    reason: Optional[str] = None


class BlockIpPayload(BaseModel):
    ip: str = Field(..., min_length=3)
    reason: Optional[str] = None
    ttl_minutes: Optional[int] = None


def _tenant_uuid_from_request(request: Request) -> UUID:
    return UUID(str(require_tenant_guard(request)))


def _scope(query, model: Any, tenant_uuid: UUID):
    if hasattr(model, "tenant_id"):
        return query.filter(model.tenant_id == tenant_uuid)
    return query


def _health_status() -> Dict[str, Any]:
    status = {
        "status": "healthy",
        "database": "unknown",
        "redis": "unknown",
    }

    try:
        db = open_db_session(allow_unscoped=True)
        db.execute(text("SELECT 1"))
        db.close()
        status["database"] = "healthy"
    except Exception as e:
        status["database"] = f"unhealthy: {str(e)[:120]}"
        status["status"] = "degraded"

    try:
        r = get_redis()
        r.ping()
        status["redis"] = "healthy"
    except Exception as e:
        status["redis"] = f"unhealthy: {str(e)[:120]}"
        status["status"] = "degraded"

    return status


def _queue_alert_email(subject: str, html_body: str) -> None:
    if not settings.admin_alert_email:
        return
    email = EmailService()
    if not email.is_configured():
        return
    try:
        email.send_followup(settings.admin_alert_email, subject, html_body)
    except Exception:
        return


def _dedupe_alert(key: str) -> bool:
    try:
        r = get_redis()
        if r.exists(key):
            return False
        r.setex(key, int(settings.alert_dedupe_ttl_sec), "1")
        return True
    except Exception:
        return False


def _build_alerts(db: Session, health: Dict[str, Any], tenant_uuid: Optional[UUID] = None) -> List[Dict[str, str]]:
    alerts: List[Dict[str, str]] = []
    now = datetime.now(timezone.utc)

    # Health alerts
    if health.get("status") != "healthy":
        msg = "Systeme degrade: verifier base de donnees / redis."
        alerts.append({"level": "warning", "type": "health", "message": msg})
        if _dedupe_alert("alert:health"):
            _queue_alert_email("Alerte monitoring: systeme degrade", f"<p>{msg}</p>")

    # Failed logins spike
    window = int(settings.alert_failed_logins_window_min)
    threshold = int(settings.alert_failed_logins_threshold)
    since = now - timedelta(minutes=window)
    failed_query = db.query(LoginAttempt).filter(
        LoginAttempt.attempted_at >= since,
        LoginAttempt.success.is_(False),
    )
    if tenant_uuid and hasattr(LoginAttempt, "tenant_id"):
        failed_query = failed_query.filter(LoginAttempt.tenant_id == tenant_uuid)
    failed_count = failed_query.count()
    if failed_count >= threshold:
        msg = f"{failed_count} echecs login sur {window} min (seuil {threshold})."
        alerts.append({"level": "danger", "type": "login_spike", "message": msg})
        dedupe_suffix = str(tenant_uuid) if tenant_uuid else "global"
        if _dedupe_alert(f"alert:login_spike:{dedupe_suffix}"):
            _queue_alert_email("Alerte securite: echecs login", f"<p>{msg}</p>")

    return alerts


@router.get("/overview")
def monitoring_overview(request: Request, db: Session = Depends(get_db)):
    tenant_uuid = _tenant_uuid_from_request(request)
    health = _health_status()

    llm = LLMService()
    tts = TTSService()
    stt = STTService()
    email = EmailService()
    sms = SMSService()
    whatsapp = WhatsAppService()

    providers = {
        "llm": {"configured": llm.is_configured(), "last_error": llm.last_error},
        "tts": {"configured": tts.is_configured(), "last_error": tts.last_error},
        "stt": {"configured": stt.is_configured()},
        "email": {"configured": email.is_configured(), "provider": email.provider},
        "sms": {"configured": sms.is_configured(), "provider": sms.provider},
        "whatsapp": {
            "configured": whatsapp.is_configured(),
            "provider": whatsapp.provider,
        },
        "sentry": {
            "configured": bool(settings.sentry_dsn),
        },
    }

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)

    attempts_q = _scope(db.query(LoginAttempt), LoginAttempt, tenant_uuid).filter(
        LoginAttempt.attempted_at >= since
    )
    total_attempts = attempts_q.count()
    failed_attempts = attempts_q.filter(LoginAttempt.success.is_(False)).count()
    unique_ips = (
        _scope(db.query(LoginAttempt.ip_address), LoginAttempt, tenant_uuid)
        .filter(LoginAttempt.attempted_at >= since, LoginAttempt.ip_address.isnot(None))
        .distinct()
        .count()
    )

    recent_attempts = (
        _scope(db.query(LoginAttempt), LoginAttempt, tenant_uuid)
        .order_by(LoginAttempt.attempted_at.desc())
        .limit(12)
        .all()
    )
    recent_failed = (
        _scope(db.query(LoginAttempt), LoginAttempt, tenant_uuid)
        .filter(LoginAttempt.success.is_(False))
        .order_by(LoginAttempt.attempted_at.desc())
        .limit(8)
        .all()
    )

    audit_rows = _scope(db.query(AuditEvent), AuditEvent, tenant_uuid).order_by(AuditEvent.at.desc()).limit(15).all()
    alerts = _build_alerts(db, health, tenant_uuid)

    def _serialize_attempt(row: LoginAttempt) -> Dict[str, Any]:
        return {
            "id": row.id,
            "email": row.email,
            "ip_address": row.ip_address,
            "success": row.success,
            "failure_reason": row.failure_reason,
            "attempted_at": row.attempted_at.isoformat() if row.attempted_at else None,
        }

    def _serialize_audit(row: AuditEvent) -> Dict[str, Any]:
        return {
            "id": str(row.id),
            "actor": row.actor,
            "action": row.action,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "at": row.at.isoformat() if row.at else None,
            "ip_address": row.ip_address,
            "tenant_id": str(row.tenant_id) if getattr(row, "tenant_id", None) else None,
        }

    return {
        "health": health,
        "providers": providers,
        "alerts": alerts,
        "emergency": get_emergency_state(),
        "security": {
            "login_attempts_24h": {
                "total": total_attempts,
                "failed": failed_attempts,
                "success": max(total_attempts - failed_attempts, 0),
                "unique_ips": unique_ips,
            },
            "recent_attempts": [_serialize_attempt(r) for r in recent_attempts],
            "recent_failed": [_serialize_attempt(r) for r in recent_failed],
        },
        "audit": {
            "recent": [_serialize_audit(r) for r in audit_rows],
        },
        "system": {
            "env": settings.env,
            "timezone": settings.app_timezone,
            "access_token_ttl": settings.access_token_ttl,
            "refresh_token_ttl": settings.refresh_token_ttl,
            "tenant_id": str(tenant_uuid),
        },
    }


@router.get("/login-attempts-series")
def login_attempts_series(request: Request, hours: int = 24, db: Session = Depends(get_db)) -> Dict[str, Any]:
    tenant_uuid = _tenant_uuid_from_request(request)
    hours = max(1, min(hours, 168))
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)

    bucket_col = func.date_trunc("hour", LoginAttempt.attempted_at).label("bucket")
    failed_col = func.sum(case((LoginAttempt.success.is_(False), 1), else_=0)).label("failed")
    total_col = func.count(LoginAttempt.id).label("total")

    rows = (
        db.query(bucket_col, total_col, failed_col)
        .filter(LoginAttempt.attempted_at >= since, LoginAttempt.tenant_id == tenant_uuid)
        .group_by(bucket_col)
        .order_by(bucket_col)
        .all()
    )

    buckets: Dict[str, Dict[str, int]] = {}
    for row in rows:
        bucket_dt = row.bucket.replace(tzinfo=timezone.utc) if row.bucket else None
        key = bucket_dt.isoformat() if bucket_dt else None
        if key:
            buckets[key] = {"total": int(row.total or 0), "failed": int(row.failed or 0)}

    labels: List[str] = []
    totals: List[int] = []
    failed: List[int] = []

    for i in range(hours, -1, -1):
        point = now - timedelta(hours=i)
        point = point.replace(minute=0, second=0, microsecond=0)
        key = point.isoformat()
        labels.append(point.strftime("%H:%M"))
        entry = buckets.get(key, {"total": 0, "failed": 0})
        totals.append(entry["total"])
        failed.append(entry["failed"])

    return {"labels": labels, "total": totals, "failed": failed}


@router.get("/incidents")
async def list_incidents(limit: int = 10) -> Dict[str, Any]:
    configured = bool(settings.sentry_auth_token and settings.sentry_org and settings.sentry_project)
    if not configured:
        return {"configured": False, "items": []}

    url = f"https://sentry.io/api/0/projects/{settings.sentry_org}/{settings.sentry_project}/issues/"
    headers = {"Authorization": f"Bearer {settings.sentry_auth_token}"}
    params = {"limit": max(1, min(limit, 50)), "statsPeriod": "24h"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=headers, params=params)
        if resp.status_code != 200:
            return {
                "configured": True,
                "items": [],
                "error": f"sentry_api_error_{resp.status_code}",
            }
        items = []
        for row in resp.json():
            items.append(
                {
                    "id": row.get("id"),
                    "title": row.get("title"),
                    "level": row.get("level"),
                    "status": row.get("status"),
                    "count": row.get("count"),
                    "last_seen": row.get("lastSeen"),
                    "first_seen": row.get("firstSeen"),
                }
            )
        return {"configured": True, "items": items}
    except Exception:
        return {"configured": True, "items": [], "error": "sentry_api_unreachable"}


@router.get("/agent-observability")
def agent_observability(
    request: Request,
    hours: int = 24,
    channel: Optional[str] = None,
    include_rotated: bool = True,
    max_events: int = 20000,
) -> Dict[str, Any]:
    tenant_uuid = _tenant_uuid_from_request(request)
    return build_agent_observability_report(
        tenant_id=str(tenant_uuid),
        channel=channel,
        hours=hours,
        include_rotated=include_rotated,
        max_events=max_events,
    )


@router.get("/emergency")
def get_emergency():
    return get_emergency_state()


@router.post("/emergency")
def set_emergency(payload: EmergencyPayload, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    ok = set_emergency_mode(payload.enabled, reason=payload.reason)
    log_audit_event(
        db,
        actor=principal.sub,
        action="emergency_on" if payload.enabled else "emergency_off",
        resource_type="monitoring",
        resource_id=None,
        details=payload.reason,
        tenant_id=principal.tenant_id,
    )
    return {"enabled": payload.enabled, "success": ok}


@router.get("/blocked-ips")
def blocked_ips():
    return {"items": list_blocked_ips()}


@router.post("/block-ip")
def block_ip_route(payload: BlockIpPayload, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    ok = block_ip(payload.ip, reason=payload.reason, ttl_minutes=payload.ttl_minutes)
    log_audit_event(
        db,
        actor=principal.sub,
        action="block_ip",
        resource_type="security",
        resource_id=payload.ip,
        details=payload.reason,
        tenant_id=principal.tenant_id,
    )
    return {"blocked": bool(ok), "ip": payload.ip}


@router.post("/unblock-ip")
def unblock_ip_route(payload: BlockIpPayload, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    ok = unblock_ip(payload.ip)
    log_audit_event(
        db,
        actor=principal.sub,
        action="unblock_ip",
        resource_type="security",
        resource_id=payload.ip,
        details=payload.reason,
        tenant_id=principal.tenant_id,
    )
    return {"unblocked": bool(ok), "ip": payload.ip}


@router.get("/outbox-overview")
def outbox_overview(request: Request, db: Session = Depends(get_db)):
    tenant_uuid = _tenant_uuid_from_request(request)
    pending = db.query(OutboxEvent).filter(OutboxEvent.tenant_id == tenant_uuid, OutboxEvent.status == "pending").count()
    failed = db.query(OutboxEvent).filter(OutboxEvent.tenant_id == tenant_uuid, OutboxEvent.status == "failed").count()
    sent_24h = (
        db.query(OutboxEvent)
        .filter(
            OutboxEvent.tenant_id == tenant_uuid,
            OutboxEvent.status == "sent",
            OutboxEvent.sent_at >= datetime.now(timezone.utc) - timedelta(hours=24),
        )
        .count()
    )
    retrying = (
        db.query(OutboxEvent)
        .filter(OutboxEvent.tenant_id == tenant_uuid, OutboxEvent.status == "failed", OutboxEvent.attempts > 0)
        .count()
    )
    return {
        "pending": pending,
        "failed": failed,
        "retrying": retrying,
        "sent_24h": sent_24h,
    }
