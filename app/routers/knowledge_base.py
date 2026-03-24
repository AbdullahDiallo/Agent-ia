from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Body
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..db import get_db
from ..models import Conversation, Message, Document
from ..security import require_role, get_principal, Principal
from ..utils.rbac import get_agent_from_principal, should_filter_by_agent
from ..services import kb as kb_service
from ..services import docs as docs_service
from ..logger import get_logger

logger = get_logger(__name__)

router = APIRouter(
    prefix="/kb",
    tags=["knowledge_base"],
    dependencies=[Depends(require_role("agent|viewer|manager|admin"))],
)


@router.get("/conversations")
def list_conversations(
    canal: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    query = db.query(Conversation)
    if canal:
        query = query.filter(Conversation.canal == canal)
    if should_filter_by_agent(principal):
        agent = get_agent_from_principal(db, principal)
        if agent:
            query = query.filter(Conversation.assigned_to == agent.user_id)

    total = query.count()
    items = query.order_by(Conversation.created_at.desc()).offset(offset).limit(limit).all()
    resp = [
        {
            "id": str(c.id),
            "person_id": str(c.person_id) if c.person_id else None,
            "resume": c.resume,
            "canal": c.canal,
            "intention": c.intention,
            "created_at": c.created_at,
            "call_sid": c.call_sid,
            "recording_sid": c.recording_sid,
            "recording_url": c.recording_url,
            "recording_duration": c.recording_duration,
            "recording_consent": c.recording_consent,
        }
        for c in items
    ]
    return {
        "items": resp,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(resp)) < total,
    }


def _format_duration_label(seconds: Optional[float]) -> str:
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


@router.get("/conversations/stats")
def conversations_stats(
    canal: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)

    query = db.query(Conversation)
    if canal:
        query = query.filter(Conversation.canal == canal)

    total = query.count()
    by_channel_rows = query.with_entities(Conversation.canal, func.count(Conversation.id)).group_by(Conversation.canal).all()
    by_channel = {key: int(value) for key, value in by_channel_rows if key}
    conv_ids = [row[0] for row in query.with_entities(Conversation.id).all()]
    if not conv_ids:
        return {
            "total": 0,
            "response_rate": 0,
            "avg_wait_time": "0s",
            "today_count": 0,
            "week_count": 0,
            "avg_duration": "0s",
            "avg_duration_seconds": 0,
            "total_duration_seconds": 0,
            "recording_count": 0,
            "recording_rate": 0,
            "consent_count": 0,
            "by_channel": by_channel,
            "satisfaction": 0,
            "resolution_rate": 0,
        }

    responded_count = (
        db.query(func.count(func.distinct(Message.conversation_id)))
        .filter(Message.conversation_id.in_(conv_ids), Message.role == "assistant")
        .scalar()
        or 0
    )
    response_rate = round((responded_count / total) * 100, 1) if total > 0 else 0

    user_first_rows = (
        db.query(Message.conversation_id, func.min(Message.created_at))
        .filter(Message.conversation_id.in_(conv_ids), Message.role == "user")
        .group_by(Message.conversation_id)
        .all()
    )
    assistant_first_rows = (
        db.query(Message.conversation_id, func.min(Message.created_at))
        .filter(Message.conversation_id.in_(conv_ids), Message.role == "assistant")
        .group_by(Message.conversation_id)
        .all()
    )
    user_first = {cid: ts for cid, ts in user_first_rows}
    assistant_first = {cid: ts for cid, ts in assistant_first_rows}
    wait_times = []
    for cid, user_ts in user_first.items():
        assistant_ts = assistant_first.get(cid)
        if assistant_ts and assistant_ts >= user_ts:
            wait_times.append((assistant_ts - user_ts).total_seconds())
    avg_wait_seconds = (sum(wait_times) / len(wait_times)) if wait_times else 0.0
    avg_wait_time = _format_duration_label(avg_wait_seconds)

    avg_recording_duration = (
        db.query(func.avg(Conversation.recording_duration))
        .filter(Conversation.id.in_(conv_ids), Conversation.recording_duration.isnot(None))
        .scalar()
        or 0
    )
    total_recording_duration = (
        db.query(func.sum(Conversation.recording_duration))
        .filter(Conversation.id.in_(conv_ids), Conversation.recording_duration.isnot(None))
        .scalar()
        or 0
    )
    recording_count = (
        db.query(func.count(Conversation.id))
        .filter(Conversation.id.in_(conv_ids), Conversation.recording_url.isnot(None))
        .scalar()
        or 0
    )
    consent_count = (
        db.query(func.count(Conversation.id))
        .filter(Conversation.id.in_(conv_ids), Conversation.recording_consent.is_(True))
        .scalar()
        or 0
    )
    today_count = (
        db.query(func.count(Conversation.id))
        .filter(Conversation.id.in_(conv_ids), Conversation.created_at >= today_start)
        .scalar()
        or 0
    )
    week_count = (
        db.query(func.count(Conversation.id))
        .filter(Conversation.id.in_(conv_ids), Conversation.created_at >= week_start)
        .scalar()
        or 0
    )

    sentiment_avg = (
        db.query(func.avg(Conversation.sentiment_score))
        .filter(Conversation.id.in_(conv_ids))
        .scalar()
    )
    if sentiment_avg is None:
        satisfaction = 0
    else:
        clamped = max(min(float(sentiment_avg), 1.0), -1.0)
        satisfaction = round(((clamped + 1) / 2) * 5, 1)

    resolved_count = (
        db.query(func.count(Conversation.id))
        .filter(Conversation.id.in_(conv_ids), Conversation.status == "closed")
        .scalar()
        or 0
    )
    resolution_rate = round((resolved_count / total) * 100, 1) if total > 0 else 0

    return {
        "total": total,
        "response_rate": response_rate,
        "avg_wait_time": avg_wait_time,
        "today_count": int(today_count),
        "week_count": int(week_count),
        "avg_duration": _format_duration_label(float(avg_recording_duration or 0)),
        "avg_duration_seconds": round(float(avg_recording_duration or 0), 2),
        "total_duration_seconds": int(total_recording_duration or 0),
        "recording_count": int(recording_count),
        "recording_rate": round((float(recording_count) / float(total)) * 100, 1) if total else 0.0,
        "consent_count": int(consent_count),
        "by_channel": by_channel,
        "satisfaction": satisfaction,
        "resolution_rate": resolution_rate,
    }


@router.get("/conversations/{conv_id}")
def get_conversation(conv_id: UUID, db: Session = Depends(get_db)):
    conv = kb_service.get_conversation(db, conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="conversation_not_found")
    return {
        "id": str(conv.id),
        "person_id": str(conv.person_id) if conv.person_id else None,
        "resume": conv.resume,
        "canal": conv.canal,
        "intention": conv.intention,
        "created_at": conv.created_at,
        "call_sid": conv.call_sid,
        "recording_sid": conv.recording_sid,
        "recording_url": conv.recording_url,
        "recording_duration": conv.recording_duration,
        "recording_consent": conv.recording_consent,
        "status": conv.status,
        "assigned_to": conv.assigned_to,
        "mode": conv.mode,
    }


@router.get("/conversations/{conv_id}/messages")
def list_conversation_messages(conv_id: UUID, db: Session = Depends(get_db)):
    conv = kb_service.get_conversation(db, conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="conversation_not_found")
    msgs = kb_service.list_messages_for_conversation(db, str(conv.id))
    return [
        {
            "id": str(m.id),
            "conversation_id": str(m.conversation_id),
            "role": m.role,
            "canal": m.canal,
            "content": m.content,
            "created_at": m.created_at,
        }
        for m in msgs
    ]


@router.post("/docs", dependencies=[Depends(require_role("manager|admin"))])
def create_doc(payload: dict = Body(...), db: Session = Depends(get_db)):
    title = (payload.get("title") or "").strip()
    content = (payload.get("content") or "").strip()
    tags = payload.get("tags")
    if not title or not content:
        raise HTTPException(status_code=400, detail="title_and_content_required")
    doc = docs_service.create_document(db, title=title, content=content, tags=tags)
    return {
        "id": str(doc.id),
        "title": doc.title,
        "content": doc.content,
        "tags": doc.tags,
        "created_at": doc.created_at,
    }


@router.get("/docs", dependencies=[Depends(require_role("manager|admin"))])
def list_docs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10_000),
    db: Session = Depends(get_db),
):
    query = db.query(Document)
    total = query.count()
    items = (
        query.order_by(Document.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "items": [
            {
                "id": str(doc.id),
                "title": doc.title,
                "content": doc.content,
                "tags": doc.tags,
                "created_at": doc.created_at,
            }
            for doc in items
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(items)) < total,
    }


@router.get("/docs/{doc_id}", dependencies=[Depends(require_role("manager|admin"))])
def get_doc(doc_id: UUID, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="doc_not_found")
    return {
        "id": str(doc.id),
        "title": doc.title,
        "content": doc.content,
        "tags": doc.tags,
        "created_at": doc.created_at,
    }


@router.put("/docs/{doc_id}", dependencies=[Depends(require_role("manager|admin"))])
def update_doc(doc_id: UUID, payload: dict = Body(...), db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="doc_not_found")
    title = payload.get("title")
    content = payload.get("content")
    tags = payload.get("tags")
    if title is not None:
        doc.title = title
    if content is not None:
        doc.content = content
    if tags is not None:
        doc.tags = tags
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return {
        "id": str(doc.id),
        "title": doc.title,
        "content": doc.content,
        "tags": doc.tags,
        "created_at": doc.created_at,
    }


@router.delete("/docs/{doc_id}", dependencies=[Depends(require_role("manager|admin"))])
def delete_doc(doc_id: UUID, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="doc_not_found")
    db.delete(doc)
    db.commit()
    return {"deleted": True, "id": str(doc_id)}


@router.get("/docs/search", dependencies=[Depends(require_role("manager|admin"))])
def search_docs(
    query: str = Query(..., min_length=2),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    items = docs_service.search_documents(db, query=query, limit=limit)
    return [
        {
            "id": str(doc.id),
            "title": doc.title,
            "content": doc.content,
            "tags": doc.tags,
            "created_at": doc.created_at,
        }
        for doc in items
    ]


@router.post("/docs/import", dependencies=[Depends(require_role("manager|admin"))])
async def import_docs(
    file: UploadFile = File(...),
    title: Optional[str] = Query(None),
    tags: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    content_bytes = await file.read()
    try:
        content = content_bytes.decode("utf-8")
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_file_encoding")
    doc_title = (title or file.filename or "Document").strip()
    if not doc_title or not content.strip():
        raise HTTPException(status_code=400, detail="title_and_content_required")
    doc = docs_service.create_document(db, title=doc_title, content=content.strip(), tags=tags)
    return {
        "id": str(doc.id),
        "title": doc.title,
        "content": doc.content,
        "tags": doc.tags,
        "created_at": doc.created_at,
    }
