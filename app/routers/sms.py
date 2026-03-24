from __future__ import annotations

from typing import Any, Dict, Optional
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.channel_agent_pipeline import ChannelAgentPipeline
from ..services.llm import LLMService
from ..services.llm_tools import handle_create_or_get_person, handle_get_track_tuition
from ..services.webhook_security import verify_webhook

router = APIRouter(tags=["sms"])


@router.post("/sms/incoming")
async def sms_incoming(request: Request, db: Session = Depends(get_db)):
    """Webhook Twilio SMS pour messages texte simples."""
    raw_body = await request.body()
    form = await request.form()
    data: Dict[str, Any] = dict(form)

    verify_webhook(
        "twilio_sms",
        request=request,
        raw_body=raw_body,
        form_data={k: str(v) for k, v in data.items()},
        url=str(request.url),
    )

    from_number: Optional[str] = data.get("From")
    body: str = str(data.get("Body") or "").strip()

    tenant_scope = str(getattr(db, "info", {}).get("tenant_id") or "")
    if not tenant_scope:
        raise HTTPException(status_code=403, detail="missing_tenant_scope")

    if not body:
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
                channel="sms",
                user_text=body,
                contact_phone=from_number,
                from_value=from_number,
                reuse_recent_by_person=True,
                thread_key=f"sms:{from_number}" if from_number else None,
                conversation_resume_prefix=f"From: {from_number or 'unknown'}",
            )
            reply_text = result.reply
        except Exception:
            reply_text = "Je rencontre un souci technique temporaire. Pouvez-vous reformuler votre demande scolaire ?"

    twiml = f"""<Response><Message>{xml_escape(reply_text)}</Message></Response>"""
    return Response(content=twiml, media_type="application/xml")
