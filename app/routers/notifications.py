from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import Body
from sqlalchemy.orm import Session
from sqlalchemy import case, func

from ..db import get_db
from ..security import require_role, require_dev_endpoint, security
from ..config import settings
from ..services.tenant_context import require_tenant_guard
from ..services.templates import upsert_email_template, get_email_template, render_email_template
from ..services.email_templates import get_all_professional_templates
from ..services.email import EmailService
from ..services.sms import SMSService
from ..services import kb as kb_service
from ..services.webhook_security import verify_webhook
from ..models import EmailLog, SMSLog, Message, EmailTemplate
from ..utils.http_errors import public_error_detail

router = APIRouter(prefix="/notifications", tags=["notifications"], dependencies=[Depends(require_role("agent|manager|admin"))])
webhook_router = APIRouter(prefix="/notifications", tags=["notifications-webhooks"])


def _tenant_uuid(request: Request) -> UUID:
    return UUID(str(require_tenant_guard(request)))


def _notification_status_counts(rows: list[tuple[str | None, int]]) -> dict[str, int]:
    return {str(status or "unknown"): int(count) for status, count in rows}


def _email_log_payload(row: EmailLog) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "type": "email",
        "person_id": str(row.person_id) if row.person_id else None,
        "subject": row.sujet,
        "status": row.statut,
        "recipient": row.recipient,
        "provider_name": row.provider_name,
        "provider_id": row.provider_id,
        "last_error": row.last_error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        "delivered_at": row.delivered_at.isoformat() if row.delivered_at else None,
        "failed_at": row.failed_at.isoformat() if row.failed_at else None,
        "direction": row.direction or "outbound",
    }


def _sms_log_payload(row: SMSLog) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "type": "sms",
        "person_id": str(row.person_id) if row.person_id else None,
        "content": row.contenu,
        "status": row.statut,
        "recipient": row.recipient,
        "provider_name": row.provider_name,
        "provider_id": row.provider_id,
        "last_error": row.last_error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        "delivered_at": row.delivered_at.isoformat() if row.delivered_at else None,
        "failed_at": row.failed_at.isoformat() if row.failed_at else None,
        "direction": row.direction or "outbound",
    }


def _notification_kpis(*, total: int, status_counts: dict[str, int], unit_cost: float) -> dict[str, Any]:
    delivered_count = int(status_counts.get("delivered", 0))
    sent_count = int(status_counts.get("sent", 0))
    failed_count = int(status_counts.get("failed", 0))
    pending_count = int(status_counts.get("pending", 0))
    billable_count = delivered_count or sent_count
    delivery_count = delivered_count or sent_count
    return {
        "delivery_rate": round((delivery_count / total) * 100, 1) if total else 0.0,
        "sent_rate": round(((sent_count + delivered_count) / total) * 100, 1) if total else 0.0,
        "failure_rate": round((failed_count / total) * 100, 1) if total else 0.0,
        "pending_count": pending_count,
        "unit_cost": unit_cost,
        "cost_total": round(float(unit_cost) * float(billable_count), 4),
    }


@router.post("/templates")
def create_or_update_template(
    request: Request,
    payload: Dict[str, Any] = Body(..., example={
        "name": "default",
        "subject_template": "Confirmation RDV {{ event.title }}",
        "html_template": "<b>Bonjour {{ person_name }}</b><br/>RDV le {{ event.start_at }}",
        "text_template": "Bonjour {{ person_name }}, RDV le {{ event.start_at }}",
    }),
    db: Session = Depends(get_db),
):
    tenant_uuid = _tenant_uuid(request)
    try:
        tpl = upsert_email_template(
            db,
            name=payload.get("name"),
            subject_template=payload.get("subject_template"),
            html_template=payload.get("html_template"),
            text_template=payload.get("text_template"),
            tenant_id=str(tenant_uuid),
        )
        return {"id": str(tpl.id), "name": tpl.name}
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=public_error_detail(
                code="template_upsert_error",
                exc=e,
                logger_name=__name__,
            ),
        )


@router.post("/templates/bootstrap-school", dependencies=[Depends(require_role("manager|admin"))])
def bootstrap_school_templates(request: Request, db: Session = Depends(get_db)):
    """Cree/maj le catalogue de templates email scolaire V1."""
    tenant_uuid = _tenant_uuid(request)
    created = []
    for tpl in get_all_professional_templates():
        row = upsert_email_template(
            db,
            name=tpl["name"],
            subject_template=tpl["subject_template"],
            html_template=tpl["html_template"],
            text_template=tpl.get("text_template"),
            tenant_id=str(tenant_uuid),
        )
        created.append({"id": str(row.id), "name": row.name})
    return {"count": len(created), "items": created}


@router.get("/templates")
def list_templates(request: Request, db: Session = Depends(get_db)):
    """Liste tous les templates d'emails"""
    tenant_uuid = _tenant_uuid(request)
    templates = db.query(EmailTemplate).filter(EmailTemplate.tenant_id == tenant_uuid).all()
    return [
        {
            "id": str(tpl.id),
            "name": tpl.name,
            "subject_template": tpl.subject_template,
            "html_template": tpl.html_template,
            "text_template": tpl.text_template,
        }
        for tpl in templates
    ]


@router.get("/templates/{name_or_id}")
def get_template(name_or_id: str, request: Request, db: Session = Depends(get_db)):
    tenant_uuid = _tenant_uuid(request)
    tpl = get_email_template(db, name_or_id=name_or_id, tenant_id=str(tenant_uuid))
    if not tpl:
        raise HTTPException(status_code=404, detail="template_not_found")
    return {
        "id": str(tpl.id),
        "name": tpl.name,
        "subject_template": tpl.subject_template,
        "html_template": tpl.html_template,
        "text_template": tpl.text_template,
    }


@router.put("/templates/{name_or_id}")
def update_template(
    name_or_id: str,
    request: Request,
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    tenant_uuid = _tenant_uuid(request)
    tpl = get_email_template(db, name_or_id=name_or_id, tenant_id=str(tenant_uuid))
    if not tpl:
        raise HTTPException(status_code=404, detail="template_not_found")
    tpl.subject_template = payload.get("subject_template") or tpl.subject_template
    tpl.html_template = payload.get("html_template") or tpl.html_template
    tpl.text_template = payload.get("text_template") or tpl.text_template
    if payload.get("name"):
        tpl.name = payload.get("name")
    db.commit()
    db.refresh(tpl)
    return {
        "id": str(tpl.id),
        "name": tpl.name,
        "subject_template": tpl.subject_template,
        "html_template": tpl.html_template,
        "text_template": tpl.text_template,
    }


@router.delete("/templates/{name_or_id}")
def delete_template(name_or_id: str, request: Request, db: Session = Depends(get_db)):
    tenant_uuid = _tenant_uuid(request)
    tpl = get_email_template(db, name_or_id=name_or_id, tenant_id=str(tenant_uuid))
    if not tpl:
        raise HTTPException(status_code=404, detail="template_not_found")
    db.delete(tpl)
    db.commit()
    return {"deleted": True, "id": str(tpl.id), "name": tpl.name}


@router.post("/templates/{name_or_id}/preview")
def preview_template(
    name_or_id: str,
    request: Request,
    context: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    tenant_uuid = _tenant_uuid(request)
    tpl = get_email_template(db, name_or_id=name_or_id, tenant_id=str(tenant_uuid))
    if not tpl:
        raise HTTPException(status_code=404, detail="template_not_found")
    try:
        rendered = render_email_template(tpl, context)
        return rendered
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=public_error_detail(
                code="template_render_error",
                exc=e,
                logger_name=__name__,
                context={"template": name_or_id},
            ),
        )


@router.post("/templates/{name_or_id}/send-test", dependencies=[Depends(require_role("manager|admin"))])
async def send_test(
    name_or_id: str,
    request: Request,
    payload: Dict[str, Any] = Body(..., example={"to_email": "contact@example.com", "context": {}}),
    db: Session = Depends(get_db),
):
    tenant_uuid = _tenant_uuid(request)
    tpl = get_email_template(db, name_or_id=name_or_id, tenant_id=str(tenant_uuid))
    if not tpl:
        raise HTTPException(status_code=404, detail="template_not_found")
    to_email: Optional[str] = payload.get("to_email")
    to_phone: Optional[str] = payload.get("to_phone")
    context: Dict[str, Any] = payload.get("context", {})

    rendered = render_email_template(tpl, context)

    results = {}

    if to_email:
        es = EmailService()
        ok = es.send_followup(to_email, rendered["subject"], rendered["html"]) if es.is_configured() else False
        results["email"] = "sent" if ok else "failed"

    if to_phone and rendered.get("text"):
        ss = SMSService()
        ok = await ss.send_sms(to_phone, rendered["text"]) if ss.is_configured() else False
        results["sms"] = "sent" if ok else "failed"

    return {"status": results}


@router.get("/recent")
def get_recent_notifications(
    request: Request,
    limit: int = Query(5, ge=1, le=20),
    db: Session = Depends(get_db),
    creds = Depends(security)
):
    tenant_uuid = _tenant_uuid(request)
    """Récupérer les notifications récentes pour le header (authentification requise)"""
    # Récupérer les notifications des dernières 24h
    time_min = datetime.now() - timedelta(days=1)
    
    # Récupérer emails et SMS séparément
    emails = (
        db.query(EmailLog)
        .filter(EmailLog.tenant_id == tenant_uuid, EmailLog.created_at >= time_min)
        .order_by(EmailLog.created_at.desc())
        .limit(limit)
        .all()
    )
    
    sms = (
        db.query(SMSLog)
        .filter(SMSLog.tenant_id == tenant_uuid, SMSLog.created_at >= time_min)
        .order_by(SMSLog.created_at.desc())
        .limit(limit)
        .all()
    )
    
    # Combiner et trier
    all_notifications = []
    for email in emails:
        item = _email_log_payload(email)
        item["subject"] = item.get("subject") or "Email"
        item["is_read"] = False
        all_notifications.append(item)
    
    for s in sms:
        item = _sms_log_payload(s)
        item["subject"] = "SMS"
        item["is_read"] = False
        all_notifications.append(item)
    
    # Trier par date décroissante
    all_notifications.sort(key=lambda x: x["created_at"], reverse=True)
    
    return {
        "items": all_notifications[:limit],
        "unread_count": len([n for n in all_notifications if n["status"] in {"pending", "failed"}])
    }


@router.get("/emails")
def list_email_logs(
    request: Request,
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    tenant_uuid = _tenant_uuid(request)
    base_query = db.query(EmailLog).filter(EmailLog.tenant_id == tenant_uuid)
    total = base_query.count()
    status_counts = _notification_status_counts(
        db.query(EmailLog.statut, func.count(EmailLog.id))
        .filter(EmailLog.tenant_id == tenant_uuid)
        .group_by(EmailLog.statut)
        .all()
    )
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = (
        db.query(EmailLog)
        .filter(EmailLog.tenant_id == tenant_uuid, EmailLog.created_at >= today_start)
        .count()
    )
    rows = (
        base_query
        .order_by(EmailLog.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    items = [_email_log_payload(e) for e in rows]
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(items)) < total,
        "status_counts": status_counts,
        "today_count": today_count,
        "direction_counts": {"outbound": total, "inbound": 0},
        "kpis": _notification_kpis(total=total, status_counts=status_counts, unit_cost=float(settings.email_unit_cost)),
    }


@router.get("/emails/{email_id}")
def get_email_log(email_id: str, request: Request, db: Session = Depends(get_db)):
    tenant_uuid = _tenant_uuid(request)
    try:
        uid = UUID(email_id)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_email_id")
    row = db.query(EmailLog).filter(EmailLog.id == uid, EmailLog.tenant_id == tenant_uuid).first()
    if not row:
        raise HTTPException(status_code=404, detail="email_not_found")
    return _email_log_payload(row)


@router.get("/sms", dependencies=[Depends(require_role("manager|admin"))])
def list_sms_logs(
    request: Request,
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    tenant_uuid = _tenant_uuid(request)
    base_query = db.query(SMSLog).filter(SMSLog.tenant_id == tenant_uuid)
    total = base_query.count()
    status_counts = _notification_status_counts(
        db.query(SMSLog.statut, func.count(SMSLog.id))
        .filter(SMSLog.tenant_id == tenant_uuid)
        .group_by(SMSLog.statut)
        .all()
    )
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = (
        db.query(SMSLog)
        .filter(SMSLog.tenant_id == tenant_uuid, SMSLog.created_at >= today_start)
        .count()
    )
    rows = (
        base_query
        .order_by(SMSLog.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    items = [_sms_log_payload(s) for s in rows]
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(items)) < total,
        "status_counts": status_counts,
        "today_count": today_count,
        "direction_counts": {"outbound": total, "inbound": 0},
        "kpis": _notification_kpis(total=total, status_counts=status_counts, unit_cost=float(settings.sms_unit_cost)),
    }


@router.get("/sms/{sms_id}", dependencies=[Depends(require_role("manager|admin"))])
def get_sms_log(sms_id: str, request: Request, db: Session = Depends(get_db)):
    tenant_uuid = _tenant_uuid(request)
    try:
        uid = UUID(sms_id)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_sms_id")
    row = db.query(SMSLog).filter(SMSLog.id == uid, SMSLog.tenant_id == tenant_uuid).first()
    if not row:
        raise HTTPException(status_code=404, detail="sms_not_found")
    return _sms_log_payload(row)


@router.post("/test-email", dependencies=[Depends(require_dev_endpoint)])
def test_email(payload: Dict[str, Any] = Body(...)):
    to_email = payload.get("to_email")
    if not to_email:
        raise HTTPException(status_code=400, detail="missing_to_email")
    subject = payload.get("subject") or "Test Email - AgentIA"
    body = payload.get("body") or payload.get("html") or "Test email"
    es = EmailService()
    if not es.is_configured():
        raise HTTPException(status_code=503, detail="email_not_configured")
    ok = es.send_followup(to_email, subject, body)
    return {"success": bool(ok)}


@router.post("/test-sms", dependencies=[Depends(require_dev_endpoint)])
async def test_sms(payload: Dict[str, Any] = Body(...)):
    to_phone = payload.get("to_phone")
    message = payload.get("message") or payload.get("body")
    if not to_phone or not message:
        raise HTTPException(status_code=400, detail="missing_to_phone_or_message")
    sms = SMSService()
    if not sms.is_configured():
        raise HTTPException(status_code=503, detail="sms_not_configured")
    ok = await sms.send_sms(to_phone, message)
    return {"success": bool(ok)}


def _map_sms_delivery_status(provider_status: Optional[str]) -> str:
    value = str(provider_status or "").strip().lower()
    if value in {"queued", "accepted", "sending"}:
        return "pending"
    if value in {"sent"}:
        return "sent"
    if value in {"delivered"}:
        return "delivered"
    if value in {"failed", "undelivered", "canceled"}:
        return "failed"
    return "pending"


def _map_email_delivery_status(provider_status: Optional[str]) -> str:
    value = str(provider_status or "").strip().lower()
    if value in {"processed", "queued", "accepted"}:
        return "pending"
    if value in {"sent"}:
        return "sent"
    if value in {"delivered", "delivery"}:
        return "delivered"
    if value in {"bounce", "bounced", "dropped", "failed", "blocked", "deferred"}:
        return "failed"
    return "pending"


@webhook_router.post("/webhooks/twilio/sms-status", include_in_schema=False)
async def twilio_sms_status_webhook(request: Request, db: Session = Depends(get_db)):
    raw_body = await request.body()
    form = await request.form()
    data = {str(k): str(v) for k, v in dict(form).items()}
    verify_webhook(
        "twilio_events",
        request=request,
        raw_body=raw_body,
        form_data=data,
        url=str(request.url),
    )
    provider_id = data.get("MessageSid") or data.get("SmsSid")
    status_value = _map_sms_delivery_status(data.get("MessageStatus") or data.get("SmsStatus"))
    error_code = data.get("ErrorCode")
    error_message = data.get("ErrorMessage")
    last_error = " ".join(part for part in [error_code, error_message] if part).strip() or None
    if provider_id:
        kb_service.mark_sms_log_status(
            db,
            provider_id=provider_id,
            statut=status_value,
            provider_name="twilio",
            recipient=data.get("To"),
            last_error=last_error,
        )
    return {"received": True, "provider_id": provider_id, "status": status_value}


@webhook_router.post("/webhooks/email/delivery", include_in_schema=False)
async def email_delivery_webhook(request: Request, db: Session = Depends(get_db)):
    raw_body = await request.body()
    content_type = str(request.headers.get("content-type") or "").lower()
    payload: dict[str, Any]
    if "application/json" in content_type:
        payload = await request.json()
        form_data = {}
    else:
        form = await request.form()
        form_data = {str(k): str(v) for k, v in dict(form).items()}
        payload = form_data

    verify_webhook(
        "email_inbound",
        request=request,
        raw_body=raw_body,
        form_data=form_data or payload,
    )

    provider_id = (
        str(payload.get("provider_id") or payload.get("message_id") or payload.get("sg_message_id") or payload.get("Message-Id") or "").strip()
        or None
    )
    dedupe_key = str(payload.get("dedupe_key") or "").strip() or None
    status_value = _map_email_delivery_status(payload.get("event") or payload.get("status"))
    last_error = str(payload.get("reason") or payload.get("error") or "").strip() or None
    if provider_id or dedupe_key:
        kb_service.mark_email_log_status(
            db,
            provider_id=provider_id,
            dedupe_key=dedupe_key,
            statut=status_value,
            provider_name=str(payload.get("provider") or payload.get("provider_name") or "").strip() or None,
            recipient=str(payload.get("email") or payload.get("recipient") or "").strip() or None,
            last_error=last_error,
        )
    return {"received": True, "provider_id": provider_id, "dedupe_key": dedupe_key, "status": status_value}


@router.get("/whatsapp")
def list_whatsapp_logs(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    tenant_uuid = _tenant_uuid(request)
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    inbound_total = (
        db.query(func.count(Message.id))
        .filter(Message.tenant_id == tenant_uuid, Message.canal == "whatsapp", Message.role == "user")
        .scalar()
        or 0
    )
    outbound_total = (
        db.query(func.count(Message.id))
        .filter(Message.tenant_id == tenant_uuid, Message.canal == "whatsapp", Message.role == "assistant")
        .scalar()
        or 0
    )
    total = int(inbound_total + outbound_total)
    today_count = (
        db.query(func.count(Message.id))
        .filter(Message.tenant_id == tenant_uuid, Message.canal == "whatsapp", Message.created_at >= today_start)
        .scalar()
        or 0
    )
    conversation_count = (
        db.query(func.count(func.distinct(Message.conversation_id)))
        .filter(Message.tenant_id == tenant_uuid, Message.canal == "whatsapp")
        .scalar()
        or 0
    )
    responded_rows = (
        db.query(
            Message.conversation_id,
            func.sum(case((Message.role == "user", 1), else_=0)).label("user_count"),
            func.sum(case((Message.role == "assistant", 1), else_=0)).label("assistant_count"),
        )
        .filter(Message.tenant_id == tenant_uuid, Message.canal == "whatsapp")
        .group_by(Message.conversation_id)
        .all()
    )
    responded_conversations = sum(
        1 for row in responded_rows if int(row.user_count or 0) > 0 and int(row.assistant_count or 0) > 0
    )

    rows = (
        db.query(Message)
        .filter(Message.tenant_id == tenant_uuid, Message.canal == "whatsapp")
        .order_by(Message.created_at.desc())
        .limit(limit)
        .all()
    )
    items = []
    for m in rows:
        direction = "inbound" if m.role == "user" else "outbound"
        status = "received" if direction == "inbound" else "sent"
        items.append({
            "id": str(m.id),
            "conversation_id": str(m.conversation_id),
            "direction": direction,
            "status": status,
            "body": m.content,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        })
    return {
        "items": items,
        "summary": {
            "total": int(total),
            "inbound": int(inbound_total),
            "outbound": int(outbound_total),
            "today_count": int(today_count),
            "conversation_count": int(conversation_count),
            "responded_conversations": int(responded_conversations),
            "response_rate": round((float(responded_conversations) / float(conversation_count)) * 100, 1)
            if conversation_count
            else 0.0,
            "delivery_rate": round((float(outbound_total) / float(total)) * 100, 1) if total else 0.0,
        },
    }
