from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..logger import get_logger
from ..services import kb as kb_service
from ..services.channel_agent_pipeline import ChannelAgentPipeline
from ..services.conversation_orchestrator import _looks_like_name_only_message  # exported for regression tests
from ..services.llm import LLMService  # exported for monkeypatch-compatible tests
from ..services.llm_tools import handle_create_or_get_person, handle_get_track_tuition

router = APIRouter(prefix="/chat", tags=["chat"])
logger = get_logger(__name__)


class ChatMessageRequest(BaseModel):
    """Requête pour envoyer un message au chatbot."""

    message: str
    session_id: Optional[str] = None
    client_phone: Optional[str] = None
    client_email: Optional[str] = None
    client_name: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    session_id: Optional[str] = None


def _as_uuid(value: Optional[str]) -> Optional[UUID]:
    if not value:
        return None
    try:
        return UUID(str(value))
    except Exception:
        return None


def require_widget_token(
    request: Request,
    x_widget_token: Optional[str] = Header(None, alias="X-Widget-Token"),
    x_widget_session: Optional[str] = Header(None, alias="X-Widget-Session"),
):
    # If the tenant_context_middleware already resolved a tenant (via
    # X-Widget-Session JWT or legacy provider_key/tenant_token query params),
    # skip the WIDGET_PUBLIC_TOKEN check — the request is already authenticated.
    tenant_scope = getattr(getattr(request, "state", None), "tenant_id", None)
    if tenant_scope:
        return
    if x_widget_session:
        return
    if settings.widget_public_token and x_widget_token != settings.widget_public_token:
        raise HTTPException(status_code=403, detail="invalid_widget_token")


@router.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_widget_token)])
async def chat_endpoint(payload: ChatMessageRequest, db: Session = Depends(get_db)) -> ChatResponse:
    requested_session = _as_uuid(payload.session_id)
    requested_session_id = str(requested_session) if requested_session else None

    tenant_scope = str(getattr(db, "info", {}).get("tenant_id") or "")
    if not tenant_scope:
        raise HTTPException(status_code=403, detail="missing_tenant_scope")

    # Preserve explicit session-id semantics for chat widget while delegating the full
    # conversation core to the shared multi-channel pipeline (P3).
    if requested_session:
        existing = kb_service.get_conversation(db, requested_session)
        if existing is not None:
            existing_tenant = str(getattr(existing, "tenant_id", "") or "")
            if existing_tenant and existing_tenant != tenant_scope:
                raise HTTPException(status_code=403, detail="cross_tenant_conversation_forbidden")

    pipeline = ChannelAgentPipeline(
        db,
        llm_factory=LLMService,  # monkeypatch-friendly in tests
        track_search_fn=handle_get_track_tuition,
        person_upsert_fn=handle_create_or_get_person,
    )
    reuse_recent_by_person = bool(not requested_session_id and (payload.client_phone or payload.client_email))
    try:
        result = await pipeline.process_inbound_text(
            channel="chat",
            user_text=payload.message,
            conversation_id=requested_session_id,
            contact_phone=payload.client_phone,
            contact_email=payload.client_email,
            contact_name=payload.client_name,
            reuse_recent_by_person=reuse_recent_by_person,
            conversation_resume_prefix="Chat",
        )
    except Exception as exc:
        logger.warning(
            "chat_conversation_persist_failed",
            extra={"extra_fields": {"error": str(exc), "tenant_id": tenant_scope}},
        )
        raise

    return ChatResponse(reply=result.reply, session_id=result.conversation_id or requested_session_id)


@router.post("/message", response_model=ChatResponse, dependencies=[Depends(require_widget_token)])
async def chat_message_endpoint(payload: ChatMessageRequest, db: Session = Depends(get_db)) -> ChatResponse:
    return await chat_endpoint(payload, db)


__all__ = [
    "router",
    "chat_endpoint",
    "chat_message_endpoint",
    "ChatMessageRequest",
    "ChatResponse",
    "_looks_like_name_only_message",
    "LLMService",
]
