"""Router pour gérer les emails entrants et permettre à l'IA de répondre."""
from __future__ import annotations

import html
from typing import Optional
import re
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.channel_agent_pipeline import ChannelAgentPipeline
from ..services.llm import LLMService
from ..services.llm_tools import handle_create_or_get_person, handle_get_track_tuition
from ..services import kb as kb_service
from ..services.email import EmailService
from ..logger import get_logger
from ..services.webhook_security import verify_webhook
from ..services.media_storage import store_raw_bytes as store_media_bytes
from ..services.attachment_reader import extract_text_from_file, format_attachment_content_for_llm

router = APIRouter(tags=["email"])
logger = get_logger(__name__)


def _normalize_subject_thread_key(subject: str) -> str:
    value = (subject or "").strip().lower()
    value = re.sub(r"^(re|fw|fwd)\s*:\s*", "", value)
    value = re.sub(r"\s+", " ", value)
    return value[:200]


def _extract_message_ids(value: str) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    matches = re.findall(r"<([^>]+)>", raw)
    if not matches:
        matches = [segment.strip() for segment in re.split(r"[\s,]+", raw) if segment.strip()]
    seen: set[str] = set()
    normalized: list[str] = []
    for item in matches:
        token = item.strip().strip("<>").lower()
        if token and token not in seen:
            seen.add(token)
            normalized.append(token[:240])
    return normalized


def _header_value(data: dict[str, object], *names: str) -> str:
    for name in names:
        value = data.get(name)
        if value:
            return str(value).strip()
    raw_headers = str(data.get("headers") or data.get("message-headers") or "").strip()
    if not raw_headers:
        return ""
    for line in raw_headers.splitlines():
        if ":" not in line:
            continue
        header_name, header_value = line.split(":", 1)
        if header_name.strip().lower() in {name.lower() for name in names}:
            return header_value.strip()
    return ""


def _build_email_thread_key(*, clean_email: str, subject: str, data: dict[str, object]) -> str:
    references = _extract_message_ids(_header_value(data, "References", "references"))
    in_reply_to = _extract_message_ids(_header_value(data, "In-Reply-To", "in-reply-to", "in_reply_to"))
    message_ids = _extract_message_ids(_header_value(data, "Message-ID", "message-id", "message_id"))
    thread_marker = (
        (references[0] if references else None)
        or (in_reply_to[0] if in_reply_to else None)
        or (message_ids[0] if message_ids else None)
        or _normalize_subject_thread_key(subject)
        or clean_email
    )
    return f"email:{clean_email}:{thread_marker}"


def _normalize_email_reply_text(reply_text: str) -> str:
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


def _render_email_reply_html(reply_text: str) -> str:
    normalized = _normalize_email_reply_text(reply_text)
    paragraphs = [block.strip() for block in re.split(r"\n\s*\n", normalized) if block.strip()]
    rendered_blocks: list[str] = []
    for block in paragraphs:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if lines and all(re.match(r"^(?:[-*]\s+|\d+\.\s+)", line) for line in lines):
            rendered_blocks.append(
                "<ul>"
                + "".join(
                    f"<li>{html.escape(re.sub(r'^(?:[-*]\s+|\d+\.\s+)', '', line).strip())}</li>"
                    for line in lines
                )
                + "</ul>"
            )
            continue
        rendered_blocks.append(
            "".join(f"<p>{html.escape(line)}</p>" for line in lines)
        )

    return (
        "<html>"
        "<body style=\"font-family: Arial, sans-serif; line-height: 1.6; color: #333;\">"
        + "".join(rendered_blocks)
        + (
            "<br><p style=\"color: #666; font-size: 0.9em;\">---<br>"
            "Salma - Admissions<br>"
            "Aelixoria AI / Etablissement scolaire"
            "</p>"
        )
        + "</body></html>"
    )


@router.post("/email/incoming")
async def email_incoming(request: Request, db: Session = Depends(get_db)):
    """Webhook pour emails entrants (SendGrid, Mailgun, etc.)."""
    try:
        raw_body = await request.body()
        form = await request.form()
        data = dict(form)
        verify_webhook(
            "email_inbound",
            request=request,
            raw_body=raw_body,
            form_data={str(k): str(v) for k, v in data.items()},
        )

        tenant_scope = str(getattr(db, "info", {}).get("tenant_id") or "")
        if not tenant_scope:
            raise HTTPException(status_code=403, detail="missing_tenant_scope")
        from_email = (data.get("from") or "").strip()
        subject = (data.get("subject") or "").strip()
        text_body = (data.get("text") or "").strip()
        html_body = (data.get("html") or "").strip()

        email_match = re.search(r"<(.+?)>", from_email)
        clean_email = (email_match.group(1) if email_match else from_email) or ""

        message_content = text_body if text_body else html_body
        if not message_content or not clean_email:
            logger.warning("Email reçu sans contenu ou expéditeur")
            return Response(content="OK", status_code=200)

        # --- Handle email attachments ---
        attachment_count = int(data.get("attachment-count") or data.get("attachments") or 0)
        attachment_summaries: list[str] = []
        if attachment_count > 0:
            form_data = await request.form()
            for i in range(1, attachment_count + 1):
                att_file = form_data.get(f"attachment-{i}") or form_data.get(f"attachment{i}")
                if att_file and hasattr(att_file, "read"):
                    try:
                        att_content = await att_file.read()
                        att_filename = getattr(att_file, "filename", None) or f"attachment_{i}"
                        att_ctype = getattr(att_file, "content_type", None) or "application/octet-stream"
                        stored = store_media_bytes(
                            db,
                            tenant_id=tenant_scope,
                            content=att_content,
                            content_type=att_ctype,
                            channel="email",
                            direction="inbound",
                            original_filename=att_filename,
                        )
                        if stored:
                            extracted = extract_text_from_file(stored.get("storage_path", ""), att_ctype)
                            summary = format_attachment_content_for_llm(att_filename, att_ctype, extracted)
                            attachment_summaries.append(summary)
                    except Exception as exc:
                        logger.warning(f"Email attachment storage failed: {exc}")
        if attachment_summaries:
            message_content += "\n\n" + "\n".join(attachment_summaries)

        logger.info(
            "Email reçu",
            extra={
                "extra_fields": {
                    "from": clean_email, "subject": subject,
                    "length": len(message_content), "attachments": len(attachment_summaries),
                }
            },
        )

        pipeline = ChannelAgentPipeline(
            db,
            llm_factory=LLMService,
            track_search_fn=handle_get_track_tuition,
            person_upsert_fn=handle_create_or_get_person,
        )
        person_id = None
        try:
            result = await pipeline.process_inbound_text(
                channel="email",
                user_text=message_content,
                llm_user_text=f"Sujet: {subject}\n\n{message_content}",
                user_message_content_for_log=f"Sujet: {subject}\n\n{message_content}",
                contact_email=clean_email,
                from_value=clean_email,
                reuse_recent_by_person=True,
                thread_key=_build_email_thread_key(clean_email=clean_email, subject=subject, data=data),
                conversation_resume_prefix=f"From: {clean_email} | Subject: {subject}",
                llm_context_extra={"subject": subject, "from_email": clean_email},
            )
            reply_text = result.reply
            person_id = result.person_id
        except Exception as exc:
            logger.error("Erreur pipeline email entrant", extra={"extra_fields": {"error": str(exc)}})
            reply_text = "Je rencontre un souci technique temporaire. Merci de reformuler votre demande (filiere, niveau, contact)."

        try:
            email_service = EmailService()
            reply_subject = f"Re: {subject}" if subject and not subject.lower().startswith("re:") else (subject or "Réponse admissions")
            normalized_reply_text = _normalize_email_reply_text(reply_text)
            reply_html = _render_email_reply_html(normalized_reply_text)
            sent = await email_service.send_email(
                to_email=clean_email,
                subject=reply_subject,
                html_body=reply_html,
                text_body=normalized_reply_text,
            )
            kb_service.create_email_log(
                db,
                person_id=person_id,
                sujet=reply_subject,
                statut="sent" if sent else "failed",
                provider_id="auto_reply",
            )
        except Exception as exc:
            logger.error("Erreur envoi réponse email", extra={"extra_fields": {"error": str(exc)}}, exc_info=True)

        return Response(content="OK", status_code=200)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Erreur traitement email entrant", extra={"extra_fields": {"error": str(exc)}}, exc_info=True)
        return Response(content="OK", status_code=200)
