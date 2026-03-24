from __future__ import annotations

from typing import Any, Dict, Optional
import json

from fastapi import APIRouter, Request, Depends, Query, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.channel_agent_pipeline import ChannelAgentPipeline
from ..services.llm import LLMService
from ..services.llm_tools import handle_create_or_get_person, handle_get_track_tuition
from ..config import settings
from ..services.whatsapp import WhatsAppService
from ..logger import get_logger
from ..services.provider_config import resolve_whatsapp_provider
from ..services.webhook_security import verify_webhook
from ..services.media_storage import download_and_store as store_media

router = APIRouter(tags=["whatsapp"])
logger = get_logger(__name__)


def _log_whatsapp_conversation(*_args, **_kwargs):
    """Backward-compatibility shim after P3 pipeline centralization."""
    return None


def _normalize_whatsapp_reply_text(reply_text: str) -> str:
    lines = [str(line or "").strip() for line in str(reply_text or "").splitlines()]
    cleaned: list[str] = []
    blank_pending = False
    for line in lines:
        if not line:
            if cleaned:
                blank_pending = True
            continue
        if blank_pending:
            cleaned.append("")
            blank_pending = False
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def _active_whatsapp_provider() -> str:
    return resolve_whatsapp_provider(settings)


def _provider_disabled_response(*, provider: str) -> Response:
    if provider == "twilio":
        return Response(content="<Response></Response>", media_type="application/xml", status_code=503)
    return Response(status_code=503)


@router.post("/whatsapp/incoming")
async def whatsapp_incoming(request: Request, db: Session = Depends(get_db)):
    """Webhook Twilio WhatsApp pour messages texte simples."""
    if _active_whatsapp_provider() != "twilio":
        return _provider_disabled_response(provider="twilio")

    raw_body = await request.body()
    form = await request.form()
    data: Dict[str, Any] = dict(form)
    verify_webhook(
        "twilio_whatsapp",
        request=request,
        raw_body=raw_body,
        form_data={k: str(v) for k, v in data.items()},
        url=str(request.url),
    )

    tenant_scope = str(getattr(db, "info", {}).get("tenant_id") or "")
    if not tenant_scope:
        raise HTTPException(status_code=403, detail="missing_tenant_scope")

    from_number: Optional[str] = data.get("From")
    body: str = str(data.get("Body") or "").strip()

    num_media_raw = data.get("NumMedia") or "0"
    try:
        num_media = int(str(num_media_raw))
    except ValueError:
        num_media = 0

    media_summaries: list[str] = []
    if num_media > 0:
        twilio_auth = (settings.twilio_account_sid, settings.twilio_auth_token) if settings.twilio_account_sid else (None, None)
        for i in range(num_media):
            url_key = f"MediaUrl{i}"
            type_key = f"MediaContentType{i}"
            url = str(data.get(url_key) or "").strip()
            mtype = str(data.get(type_key) or "").strip() or "media/unknown"
            if url:
                # Download and store the media file
                try:
                    stored = await store_media(
                        db,
                        tenant_id=tenant_scope,
                        source_url=url,
                        content_type=mtype,
                        channel="whatsapp",
                        direction="inbound",
                        auth_user=twilio_auth[0],
                        auth_password=twilio_auth[1],
                    )
                    if stored:
                        media_summaries.append(
                            f"[FICHIER REÇU: {stored['filename']} ({mtype}, {stored['size_bytes']} octets) - stocké]"
                        )
                    else:
                        media_summaries.append(f"[MEDIA {i+1}: {mtype} - non supporté ou trop volumineux]")
                except Exception:
                    media_summaries.append(f"[MEDIA {i+1}: {mtype} - erreur de téléchargement]")

    full_user_content = body
    if media_summaries:
        extra = "\n".join(media_summaries)
        full_user_content = (body + "\n" + extra).strip() if body else extra

    if not full_user_content:
        reply_text = "Je n'ai pas reçu de message lisible. Pouvez-vous reformuler votre demande scolaire ?"
    else:
        pipeline = ChannelAgentPipeline(
            db,
            llm_factory=LLMService,
            track_search_fn=handle_get_track_tuition,
            person_upsert_fn=handle_create_or_get_person,
        )
        try:
            result = await pipeline.process_inbound_text(
                channel="whatsapp",
                user_text=full_user_content,
                contact_phone=from_number,
                from_value=from_number,
                reuse_recent_by_person=True,
                thread_key=f"whatsapp:{from_number}" if from_number else None,
                conversation_resume_prefix=f"From: {from_number or 'unknown'}",
            )
            reply_text = result.reply
        except Exception:
            reply_text = "Je rencontre un souci technique temporaire. Pouvez-vous reformuler votre demande scolaire ?"

    reply_text = _normalize_whatsapp_reply_text(reply_text)
    twiml = f"""<Response><Message>{reply_text}</Message></Response>"""
    return Response(content=twiml, media_type="application/xml")


@router.get("/webhooks/meta/whatsapp")
async def meta_whatsapp_verify(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    if _active_whatsapp_provider() != "meta":
        return _provider_disabled_response(provider="meta")
    if hub_mode == "subscribe" and hub_verify_token == settings.meta_whatsapp_verify_token:
        return Response(content=hub_challenge or "", media_type="text/plain")
    return Response(status_code=403)


@router.post("/webhooks/meta/whatsapp")
async def meta_whatsapp_incoming(request: Request, db: Session = Depends(get_db)):
    if _active_whatsapp_provider() != "meta":
        return _provider_disabled_response(provider="meta")

    raw_body = await request.body()
    try:
        payload: Dict[str, Any] = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_payload")

    verify_webhook(
        "meta_whatsapp",
        request=request,
        raw_body=raw_body,
        payload=payload,
    )
    tenant_scope = str(getattr(db, "info", {}).get("tenant_id") or "")
    if not tenant_scope:
        raise HTTPException(status_code=403, detail="missing_tenant_scope")

    logger.info(
        "WhatsApp Meta webhook received",
        extra={"extra_fields": {"entry_count": len(payload.get("entry") or [])}},
    )

    from_number: Optional[str] = None
    body: str = ""

    try:
        entries = payload.get("entry") or []
        for entry in entries:
            changes = entry.get("changes") or []
            for change in changes:
                value = change.get("value") or {}
                messages = value.get("messages") or []
                if messages:
                    msg = messages[0]
                    from_number = msg.get("from")
                    body = (msg.get("text") or {}).get("body") or ""
                    break
            if body:
                break
    except Exception as exc:
        logger.error("Failed to parse WhatsApp Meta payload", extra={"extra_fields": {"error": str(exc)}})
        return {"status": "ignored", "reason": "parse_error"}

    if not body:
        return {"status": "ignored", "reason": "empty_body"}

    pipeline = ChannelAgentPipeline(
        db,
        llm_factory=LLMService,
        track_search_fn=handle_get_track_tuition,
        person_upsert_fn=handle_create_or_get_person,
    )
    try:
        result = await pipeline.process_inbound_text(
            channel="whatsapp",
            user_text=body,
            contact_phone=from_number,
            from_value=from_number,
            reuse_recent_by_person=True,
            thread_key=f"whatsapp:{from_number}" if from_number else None,
            conversation_resume_prefix=f"From: {from_number or 'unknown'}",
        )
        reply_text = result.reply
    except Exception:
        reply_text = "Je rencontre un souci technique temporaire. Pouvez-vous reformuler votre demande scolaire ?"

    reply_text = _normalize_whatsapp_reply_text(reply_text)
    wa_service = WhatsAppService()
    sent = await wa_service.send_message(from_number, reply_text)
    if not sent:
        logger.warning("WhatsApp Meta reply failed", extra={"extra_fields": {"from": from_number}})

    return {"status": "ok"}
