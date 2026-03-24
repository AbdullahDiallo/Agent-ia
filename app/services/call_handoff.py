from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..logger import get_logger
from ..models import Agent, User
from .agent_assignment import has_conflict

logger = get_logger(__name__)

try:
    from twilio.rest import Client as TwilioClient
except Exception:
    TwilioClient = None  # type: ignore


def _normalize_phone(value: Optional[str]) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    raw = raw.replace(" ", "")
    if not raw.startswith("+"):
        raw = f"+{raw}"
    return raw


def pick_available_agent_phones_for_live_call(
    db: Session,
    *,
    limit: int = 3,
    window_minutes: int = 30,
) -> list[str]:
    """Pick up to N human agent phones that look available right now.

    Heuristic:
    - Agent.disponible = True
    - User.phone present
    - No RendezVous conflict over [now, now+window_minutes]
    """
    limit = max(1, min(int(limit), 6))
    window_minutes = max(10, min(int(window_minutes), 120))

    now = datetime.now(timezone.utc)
    end_at = now + timedelta(minutes=window_minutes)

    rows = (
        db.query(Agent, User)
        .join(User, Agent.user_id == User.id)
        .filter(Agent.disponible == True)  # noqa: E712
        .order_by(Agent.updated_at.desc())
        .limit(50)
        .all()
    )

    out: list[str] = []
    seen: set[str] = set()
    for agent, user in rows:
        phone = _normalize_phone(getattr(user, "phone", None))
        if not phone or phone in seen:
            continue
        if has_conflict(db, agent.id, now, end_at):
            continue
        seen.add(phone)
        out.append(phone)
        if len(out) >= limit:
            break
    return out


def _handoff_twiml(*, agent_numbers: list[str], lang: str) -> str:
    lang_code = (lang or "fr").lower()
    if lang_code not in {"fr", "en", "wo"}:
        lang_code = "fr"

    if lang_code == "en":
        pre = "Please hold. I am transferring you to a human admissions advisor."
        no_agent = "No advisor is available right now. Please call back later."
        voice = "alice"
        say_lang = "en-US"
    elif lang_code == "wo":
        pre = "Maa ngi lay jëflante ak ab conseiller admissions."
        no_agent = "Amul conseiller bu libre léegi. Wuyusi ci kanam."
        voice = "alice"
        say_lang = "fr-FR"
    else:
        pre = "Veuillez patienter. Je vous transfère à un conseiller admissions."
        no_agent = "Aucun conseiller n'est disponible pour le moment. Merci de rappeler plus tard."
        voice = "alice"
        say_lang = "fr-FR"

    caller_id = _normalize_phone(getattr(settings, "twilio_voice_number", None))
    caller_id_attr = f' callerId="{caller_id}"' if caller_id else ""

    if not agent_numbers:
        return (
            "<Response>"
            f'<Say voice="{voice}" language="{say_lang}">{no_agent}</Say>'
            "<Hangup/>"
            "</Response>"
        )

    numbers_xml = "".join([f"<Number>{n}</Number>" for n in agent_numbers])
    return (
        "<Response>"
        f'<Say voice="{voice}" language="{say_lang}">{pre}</Say>'
        f'<Dial timeout="20" answerOnBridge="true"{caller_id_attr}>{numbers_xml}</Dial>'
        "<Hangup/>"
        "</Response>"
    )


def _transfer_call_sync(*, call_sid: str, twiml: str) -> bool:
    if not TwilioClient:
        logger.error("Twilio client unavailable for call handoff")
        return False
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        logger.error("Twilio credentials missing for call handoff")
        return False
    if not call_sid:
        return False
    try:
        client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)
        client.calls(call_sid).update(twiml=twiml)
        logger.info("call_handoff_initiated", extra={"extra_fields": {"call_sid": call_sid}})
        return True
    except Exception as exc:
        logger.error(
            "call_handoff_failed",
            extra={"extra_fields": {"call_sid": call_sid, "error": str(exc)}},
            exc_info=True,
        )
        return False


async def transfer_call_to_human_agents(
    db: Session,
    *,
    call_sid: str,
    lang: str,
    max_agents: int = 3,
) -> bool:
    agent_numbers = pick_available_agent_phones_for_live_call(db, limit=max_agents)
    twiml = _handoff_twiml(agent_numbers=agent_numbers, lang=lang)
    return await asyncio.to_thread(_transfer_call_sync, call_sid=call_sid, twiml=twiml)


__all__ = ["transfer_call_to_human_agents", "pick_available_agent_phones_for_live_call"]

