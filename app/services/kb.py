from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from datetime import datetime, timezone

from sqlalchemy import and_, or_

from ..config import settings
from ..models import Conversation, EmailLog, Message, Person, RendezVous, SMSLog
from ..services.agent_assignment import _agent_display_name, find_available_agents


def _as_uuid(value: Optional[str]) -> Optional[UUID]:
    if not value:
        return None
    try:
        return UUID(str(value))
    except Exception:
        return None


def _session_tenant_uuid(db: Session) -> UUID:
    tenant_scope = getattr(db, "info", {}).get("tenant_id")
    if tenant_scope:
        try:
            return UUID(str(tenant_scope))
        except Exception as exc:
            raise PermissionError("invalid_tenant_scope") from exc
    if getattr(db, "info", {}).get("allow_unscoped_tenant"):
        return UUID(str(settings.default_tenant_id))
    raise PermissionError("missing_tenant_scope")


def _assert_same_tenant(scope_tenant: UUID, record_tenant: Optional[UUID]) -> None:
    if record_tenant and UUID(str(record_tenant)) != UUID(str(scope_tenant)):
        raise PermissionError("cross_tenant_reference_forbidden")


def create_conversation(
    db: Session,
    *,
    person_id: Optional[str],
    resume: Optional[str],
    canal: Optional[str],
    intention: Optional[str],
    conversation_state: Optional[str] = None,
    call_sid: Optional[str] = None,
    recording_sid: Optional[str] = None,
    recording_url: Optional[str] = None,
    recording_duration: Optional[int] = None,
    recording_consent: Optional[bool] = None,
) -> Conversation:
    tenant_id = _session_tenant_uuid(db)
    person_uuid = _as_uuid(person_id)
    if person_uuid:
        person = db.get(Person, person_uuid)
        if person and getattr(person, "tenant_id", None):
            _assert_same_tenant(tenant_id, person.tenant_id)
            tenant_id = person.tenant_id
    conv = Conversation(
        tenant_id=tenant_id,
        person_id=_as_uuid(person_id),
        resume=resume,
        canal=canal,
        intention=intention,
        conversation_state=conversation_state,
        call_sid=call_sid,
        recording_sid=recording_sid,
        recording_url=recording_url,
        recording_duration=recording_duration,
        recording_consent=recording_consent if recording_consent is not None else False,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def get_conversation(db: Session, conv_id) -> Optional[Conversation]:
    return db.get(Conversation, conv_id)


def find_latest_conversation_for_person(
    db: Session,
    *,
    person_id: str | UUID,
    canal: str,
    statuses: tuple[str, ...] = ("active",),
) -> Optional[Conversation]:
    person_uuid = _as_uuid(str(person_id))
    if person_uuid is None:
        return None
    query = db.query(Conversation).filter(
        Conversation.person_id == person_uuid,
        Conversation.canal == canal,
    )
    if statuses:
        query = query.filter(Conversation.status.in_(list(statuses)))
    return query.order_by(Conversation.created_at.desc()).first()


def list_recent_conversations_for_person(
    db: Session,
    *,
    person_id: str | UUID,
    canal: str,
    limit: int = 10,
) -> list[Conversation]:
    person_uuid = _as_uuid(str(person_id))
    if person_uuid is None:
        return []
    return (
        db.query(Conversation)
        .filter(
            Conversation.person_id == person_uuid,
            Conversation.canal == canal,
        )
        .order_by(Conversation.created_at.desc())
        .limit(max(1, min(limit, 50)))
        .all()
    )


def find_latest_conversation_by_call_sid(db: Session, *, call_sid: str) -> Optional[Conversation]:
    if not call_sid:
        return None
    return (
        db.query(Conversation)
        .filter(Conversation.call_sid == call_sid)
        .order_by(Conversation.created_at.desc())
        .first()
    )


def create_message(
    db: Session,
    *,
    conversation_id: str,
    role: str,
    canal: Optional[str],
    content: str,
) -> Message:
    tenant_id = _session_tenant_uuid(db)
    conversation_uuid = _as_uuid(conversation_id)
    if conversation_uuid is None:
        raise ValueError("invalid_conversation_id")
    conv = db.get(Conversation, conversation_uuid)
    if conv and getattr(conv, "tenant_id", None):
        _assert_same_tenant(tenant_id, conv.tenant_id)
        tenant_id = conv.tenant_id
    msg = Message(
        tenant_id=tenant_id,
        conversation_id=conversation_uuid,
        role=role,
        canal=canal,
        content=content,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def list_messages_for_conversation(db: Session, conversation_id: str) -> list[Message]:
    return (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .all()
    )


def create_email_log(
    db: Session,
    *,
    person_id: Optional[str] = None,
    sujet: Optional[str],
    statut: str = "pending",
    dedupe_key: Optional[str] = None,
    recipient: Optional[str] = None,
    provider_name: Optional[str] = None,
    provider_id: Optional[str] = None,
    direction: str = "outbound",
    last_error: Optional[str] = None,
) -> EmailLog:
    tenant_id = _session_tenant_uuid(db)
    person_uuid = _as_uuid(person_id)
    if person_uuid:
        person = db.get(Person, person_uuid)
        if person and getattr(person, "tenant_id", None):
            _assert_same_tenant(tenant_id, person.tenant_id)
            tenant_id = person.tenant_id
    log = EmailLog(
        tenant_id=tenant_id,
        person_id=_as_uuid(person_id),
        sujet=sujet,
        statut=statut,
        dedupe_key=dedupe_key,
        recipient=recipient,
        provider_name=provider_name,
        provider_id=provider_id,
        direction=direction or "outbound",
        last_error=last_error,
        sent_at=(datetime.now(timezone.utc) if statut == "sent" else None),
        delivered_at=(datetime.now(timezone.utc) if statut == "delivered" else None),
        failed_at=(datetime.now(timezone.utc) if statut == "failed" else None),
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def create_sms_log(
    db: Session,
    *,
    person_id: Optional[str] = None,
    contenu: Optional[str] = None,
    statut: str = "pending",
    dedupe_key: Optional[str] = None,
    recipient: Optional[str] = None,
    provider_name: Optional[str] = None,
    provider_id: Optional[str] = None,
    direction: str = "outbound",
    last_error: Optional[str] = None,
) -> SMSLog:
    tenant_id = _session_tenant_uuid(db)
    person_uuid = _as_uuid(person_id)
    if person_uuid:
        person = db.get(Person, person_uuid)
        if person and getattr(person, "tenant_id", None):
            _assert_same_tenant(tenant_id, person.tenant_id)
            tenant_id = person.tenant_id
    log = SMSLog(
        tenant_id=tenant_id,
        person_id=_as_uuid(person_id),
        contenu=contenu,
        statut=statut,
        dedupe_key=dedupe_key,
        recipient=recipient,
        provider_name=provider_name,
        provider_id=provider_id,
        direction=direction or "outbound",
        last_error=last_error,
        sent_at=(datetime.now(timezone.utc) if statut == "sent" else None),
        delivered_at=(datetime.now(timezone.utc) if statut == "delivered" else None),
        failed_at=(datetime.now(timezone.utc) if statut == "failed" else None),
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def email_log_exists(db: Session, *, dedupe_key: str) -> bool:
    if not dedupe_key:
        return False
    q = db.query(EmailLog.id).filter(
        EmailLog.dedupe_key == dedupe_key,
        EmailLog.statut.in_(["sent", "delivered"]),
    )
    return q.first() is not None


def sms_log_exists(db: Session, *, dedupe_key: str) -> bool:
    if not dedupe_key:
        return False
    q = db.query(SMSLog.id).filter(
        SMSLog.dedupe_key == dedupe_key,
        SMSLog.statut.in_(["sent", "delivered"]),
    )
    return q.first() is not None


def get_email_log_by_dedupe_key(db: Session, *, dedupe_key: str) -> Optional[EmailLog]:
    if not dedupe_key:
        return None
    return db.query(EmailLog).filter(EmailLog.dedupe_key == dedupe_key).order_by(EmailLog.created_at.desc()).first()


def get_sms_log_by_dedupe_key(db: Session, *, dedupe_key: str) -> Optional[SMSLog]:
    if not dedupe_key:
        return None
    return db.query(SMSLog).filter(SMSLog.dedupe_key == dedupe_key).order_by(SMSLog.created_at.desc()).first()


def upsert_email_log_pending(
    db: Session,
    *,
    dedupe_key: str,
    person_id: Optional[str] = None,
    sujet: Optional[str] = None,
    recipient: Optional[str] = None,
    provider_name: Optional[str] = None,
) -> EmailLog:
    existing = get_email_log_by_dedupe_key(db, dedupe_key=dedupe_key)
    if existing:
        if sujet is not None:
            existing.sujet = sujet
        if recipient is not None:
            existing.recipient = recipient
        if provider_name is not None:
            existing.provider_name = provider_name
        if existing.statut not in {"sent", "delivered"}:
            existing.statut = "pending"
            existing.last_error = None
            existing.failed_at = None
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing
    return create_email_log(
        db,
        person_id=person_id,
        sujet=sujet,
        statut="pending",
        dedupe_key=dedupe_key,
        recipient=recipient,
        provider_name=provider_name,
    )


def upsert_sms_log_pending(
    db: Session,
    *,
    dedupe_key: str,
    person_id: Optional[str] = None,
    contenu: Optional[str] = None,
    recipient: Optional[str] = None,
    provider_name: Optional[str] = None,
) -> SMSLog:
    existing = get_sms_log_by_dedupe_key(db, dedupe_key=dedupe_key)
    if existing:
        if contenu is not None:
            existing.contenu = contenu
        if recipient is not None:
            existing.recipient = recipient
        if provider_name is not None:
            existing.provider_name = provider_name
        if existing.statut not in {"sent", "delivered"}:
            existing.statut = "pending"
            existing.last_error = None
            existing.failed_at = None
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing
    return create_sms_log(
        db,
        person_id=person_id,
        contenu=contenu,
        statut="pending",
        dedupe_key=dedupe_key,
        recipient=recipient,
        provider_name=provider_name,
    )


def mark_email_log_status(
    db: Session,
    *,
    dedupe_key: Optional[str] = None,
    provider_id: Optional[str] = None,
    statut: str,
    provider_name: Optional[str] = None,
    recipient: Optional[str] = None,
    last_error: Optional[str] = None,
) -> Optional[EmailLog]:
    query = db.query(EmailLog)
    if provider_id:
        query = query.filter(EmailLog.provider_id == provider_id)
    elif dedupe_key:
        query = query.filter(EmailLog.dedupe_key == dedupe_key)
    else:
        return None
    log = query.order_by(EmailLog.created_at.desc()).first()
    if not log:
        return None
    log.statut = statut
    if provider_id:
        log.provider_id = provider_id
    if provider_name is not None:
        log.provider_name = provider_name
    if recipient is not None:
        log.recipient = recipient
    log.last_error = last_error
    now = datetime.now(timezone.utc)
    if statut == "sent":
        log.sent_at = now
        log.failed_at = None
    elif statut == "delivered":
        log.delivered_at = now
        if log.sent_at is None:
            log.sent_at = now
        log.failed_at = None
    elif statut == "failed":
        log.failed_at = now
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def mark_sms_log_status(
    db: Session,
    *,
    dedupe_key: Optional[str] = None,
    provider_id: Optional[str] = None,
    statut: str,
    provider_name: Optional[str] = None,
    recipient: Optional[str] = None,
    last_error: Optional[str] = None,
) -> Optional[SMSLog]:
    query = db.query(SMSLog)
    if provider_id:
        query = query.filter(SMSLog.provider_id == provider_id)
    elif dedupe_key:
        query = query.filter(SMSLog.dedupe_key == dedupe_key)
    else:
        return None
    log = query.order_by(SMSLog.created_at.desc()).first()
    if not log:
        return None
    log.statut = statut
    if provider_id:
        log.provider_id = provider_id
    if provider_name is not None:
        log.provider_name = provider_name
    if recipient is not None:
        log.recipient = recipient
    log.last_error = last_error
    now = datetime.now(timezone.utc)
    if statut == "sent":
        log.sent_at = now
        log.failed_at = None
    elif statut == "delivered":
        log.delivered_at = now
        if log.sent_at is None:
            log.sent_at = now
        log.failed_at = None
    elif statut == "failed":
        log.failed_at = now
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def has_appointment_conflict(
    db: Session,
    *,
    person_id: Optional[UUID] = None,
    agent_id: Optional[UUID] = None,
    start_at: datetime,
    end_at: datetime,
    exclude_rdv_id: Optional[UUID] = None,
) -> Optional[RendezVous]:
    """Check for overlapping active appointments for the same person or agent."""
    if not person_id and not agent_id:
        return None

    overlap = or_(
        and_(RendezVous.start_at >= start_at, RendezVous.start_at < end_at),
        and_(RendezVous.end_at > start_at, RendezVous.end_at <= end_at),
        and_(RendezVous.start_at <= start_at, RendezVous.end_at >= end_at),
    )

    q = db.query(RendezVous).filter(
        overlap,
        RendezVous.statut.notin_(["cancelled"]),
        RendezVous.deleted_at.is_(None),
    )
    if exclude_rdv_id:
        q = q.filter(RendezVous.id != exclude_rdv_id)

    # Check person conflict
    if person_id:
        conflict = q.filter(RendezVous.person_id == person_id).first()
        if conflict:
            return conflict

    # Check agent conflict
    if agent_id:
        conflict = q.filter(RendezVous.agent_id == agent_id).first()
        if conflict:
            return conflict

    return None


def create_rendezvous(
    db: Session,
    *,
    person_id: Optional[UUID] = None,
    track_id: Optional[UUID] = None,
    start_at,
    end_at,
    agent: Optional[str] = None,
    statut: str = "created",
    event_id: Optional[str] = None,
    require_assigned_agent: bool = False,
) -> RendezVous:
    normalized_status = (statut or "created").strip().lower()
    if normalized_status == "pending":
        normalized_status = "created"
    if not normalized_status:
        normalized_status = "created"

    tenant_id = _session_tenant_uuid(db)
    if person_id:
        person = db.get(Person, person_id)
        if person and getattr(person, "tenant_id", None):
            _assert_same_tenant(tenant_id, person.tenant_id)
            tenant_id = person.tenant_id

    assigned_agent = None
    if require_assigned_agent:
        available_agents = find_available_agents(db, start_at, end_at, track_id)
        if not available_agents:
            raise ValueError("no_agent_available")
        assigned_agent, _score = available_agents[0]
        agent = _agent_display_name(db, assigned_agent)

    rdv = RendezVous(
        tenant_id=tenant_id,
        person_id=person_id,
        track_id=track_id,
        agent_id=assigned_agent.id if assigned_agent else None,
        start_at=start_at,
        end_at=end_at,
        agent=agent,
        statut=normalized_status,
        event_id=event_id,
    )
    db.add(rdv)
    db.commit()
    db.refresh(rdv)
    return rdv
