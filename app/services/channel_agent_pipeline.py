from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import time
from typing import Any, Callable, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from ..config import settings
from ..logger import get_logger
from . import kb as kb_service
from .conversation_orchestrator import (
    ConversationOrchestrator,
    dump_conversation_state,
    parse_conversation_state,
)
from .llm import LLMService
from .llm_tools import (
    handle_check_appointment_slot,
    handle_create_or_get_person,
    handle_create_school_appointment,
    handle_get_track_tuition,
)

logger = get_logger(__name__)

_OBS_SLOT_IGNORE_KEYS = {"thread_key"}
_OBS_BOOKING_FLOWS = {"booking_collect_contact", "booking_collect_datetime", "booking_confirm", "booking_submitted"}
_FR_MONTHS = {
    "janvier": 1,
    "fevrier": 2,
    "février": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "août": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
    "décembre": 12,
}
_DATE_NUMERIC_SLOT_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b")
_DATE_TEXT_SLOT_RE = re.compile(
    r"\b(?:le\s+)?(\d{1,2})\s+([A-Za-zÀ-ÿ]+)(?:\s+(\d{2,4}))?\b",
    re.IGNORECASE,
)
_TIME_SLOT_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*h(?:\s*(\d{1,2}))?\b|\b(\d{1,2}):(\d{2})\b", re.IGNORECASE)


def _as_uuid(value: Optional[str]) -> Optional[UUID]:
    if not value:
        return None
    try:
        return UUID(str(value))
    except Exception:
        return None


def _history_user_messages(messages) -> list[str]:
    return [
        str(msg.content or "").strip()
        for msg in messages
        if str(getattr(msg, "role", "")).lower() == "user" and str(msg.content or "").strip()
    ][-10:]


def _recent_turns(messages, limit: int = 12) -> list[dict[str, str]]:
    turns: list[dict[str, str]] = []
    for msg in messages:
        role = str(getattr(msg, "role", "")).strip().lower()
        content = str(getattr(msg, "content", "") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        if len(content) > 600:
            content = content[:597] + "..."
        turns.append({"role": role, "content": content})
    return turns[-max(1, limit):]


def _normalize_email(value: Optional[str]) -> Optional[str]:
    email = str(value or "").strip().lower()
    return email or None


def _normalize_phone(value: Optional[str]) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    digits = re.sub(r"[^\d+]", "", raw)
    if digits.startswith("00"):
        digits = f"+{digits[2:]}"
    if digits.startswith("+"):
        prefix = "+"
        body = re.sub(r"\D", "", digits[1:])
        return f"{prefix}{body}" if body else None
    body = re.sub(r"\D", "", digits)
    return body or None


def _clean_name(value: Optional[str]) -> Optional[str]:
    cleaned = " ".join(str(value or "").replace(",", " ").split()).strip(" -")
    return cleaned or None


def _truncate_text(value: str, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _participant_key(
    *,
    person_id: Optional[str],
    contact_email: Optional[str],
    contact_phone: Optional[str],
    from_value: Optional[str],
) -> Optional[str]:
    person_uuid = str(person_id or "").strip()
    if person_uuid:
        return f"person:{person_uuid}"
    normalized_email = _normalize_email(contact_email)
    if normalized_email:
        return f"email:{normalized_email}"
    normalized_phone = _normalize_phone(contact_phone)
    if normalized_phone:
        return f"phone:{normalized_phone}"
    fallback_email = _normalize_email(from_value)
    if fallback_email and "@" in fallback_email:
        return f"email:{fallback_email}"
    fallback_phone = _normalize_phone(from_value)
    if fallback_phone:
        return f"phone:{fallback_phone}"
    return None


def _default_thread_key(
    *,
    channel: str,
    participant_key: Optional[str],
    from_value: Optional[str],
    contact_email: Optional[str],
    contact_phone: Optional[str],
) -> Optional[str]:
    normalized_channel = str(channel or "").strip().lower()
    if normalized_channel == "chat" and participant_key:
        return f"chat:{participant_key}"
    if normalized_channel == "email":
        normalized_email = _normalize_email(contact_email) or _normalize_email(from_value)
        if normalized_email:
            return f"email:{normalized_email}"
    if normalized_channel == "whatsapp":
        normalized_phone = _normalize_phone(contact_phone) or _normalize_phone(from_value)
        if normalized_phone:
            return f"whatsapp:{normalized_phone}"
    if normalized_channel == "sms":
        normalized_phone = _normalize_phone(contact_phone) or _normalize_phone(from_value)
        if normalized_phone:
            return f"sms:{normalized_phone}"
    return None


def _memory_slots_from_state(state: dict[str, Any]) -> dict[str, Any]:
    slots = state.get("slots_json") if isinstance(state.get("slots_json"), dict) else {}
    selected: dict[str, Any] = {}
    for key in (
        "person_id",
        "full_name",
        "first_name",
        "last_name",
        "email",
        "phone",
        "track_id",
        "track_name",
        "program_name",
        "appointment_date",
        "appointment_time",
        "admission_level",
        "preferred_language",
        "appointment_intent",
        "handoff_status",
    ):
        value = slots.get(key)
        if value not in (None, "", []):
            selected[key] = value
    if bool(state.get("appointment_locked")):
        selected["appointment_locked"] = True
    return selected


def _build_summary_memory(state: dict[str, Any], recent_turns: Optional[list] = None) -> Optional[str]:
    slots = _memory_slots_from_state(state)
    parts: list[str] = []
    full_name = str(slots.get("full_name") or "").strip()
    if not full_name:
        first_name = str(slots.get("first_name") or "").strip()
        last_name = str(slots.get("last_name") or "").strip()
        full_name = " ".join(part for part in (first_name, last_name) if part).strip()
    if full_name:
        parts.append(f"Contact: {full_name}")
    email = str(slots.get("email") or "").strip()
    phone = str(slots.get("phone") or "").strip()
    if email or phone:
        contact_bits = [bit for bit in (email, phone) if bit]
        parts.append(f"Coordonnées: {', '.join(contact_bits)}")
    program = str(slots.get("track_name") or slots.get("program_name") or "").strip()
    if program:
        parts.append(f"Programme discuté: {program}")
    date_value = str(slots.get("appointment_date") or "").strip()
    time_value = str(slots.get("appointment_time") or "").strip()
    if date_value or time_value:
        when = " ".join(bit for bit in (date_value, time_value) if bit).strip()
        if when:
            parts.append(f"RDV: {when}")
    active_flow = str(state.get("active_flow") or "").strip()
    if active_flow and active_flow != "browsing_catalog":
        parts.append(f"Étape: {active_flow}")

    # Build a summary of topics already discussed from recent turns
    if recent_turns:
        topics_discussed: list[str] = []
        for turn in recent_turns:
            if not isinstance(turn, dict):
                continue
            if turn.get("role") == "user":
                content = str(turn.get("content") or "").strip()
                if content and len(content) > 5:
                    # Extract a short summary of the user's question
                    short = content[:80].rstrip() + ("..." if len(content) > 80 else "")
                    topics_discussed.append(short)
        if topics_discussed:
            parts.append(f"Sujets abordés: {'; '.join(topics_discussed[-4:])}")

    summary = " | ".join(parts).strip()
    return _truncate_text(summary, limit=500) if summary else None


def _match_thread_key(conv, thread_key: Optional[str]) -> bool:
    if not thread_key:
        return True
    state = parse_conversation_state(str(getattr(conv, "conversation_state", "") or ""))
    stored_thread_key = str(state.get("channel_thread_key") or "").strip()
    if stored_thread_key:
        return stored_thread_key == str(thread_key)
    slots = state.get("slots_json") if isinstance(state.get("slots_json"), dict) else {}
    return str(slots.get("thread_key") or "") == str(thread_key)


@dataclass
class ChannelTurnResult:
    reply: str
    lang: str
    person_id: Optional[str]
    conversation_id: Optional[str]
    conversation: Any
    llm_state: dict[str, Any]
    response_strategy: str
    active_flow: str
    conversation_state: dict[str, Any]


class ChannelAgentPipeline:
    def __init__(
        self,
        db: Session,
        *,
        llm_factory: Callable[[], Any] = LLMService,
        track_search_fn=handle_get_track_tuition,
        person_upsert_fn=handle_create_or_get_person,
        appointment_slot_check_fn=handle_check_appointment_slot,
        appointment_create_fn=handle_create_school_appointment,
    ) -> None:
        self.db = db
        self._llm_factory = llm_factory
        self._track_search_fn = track_search_fn
        self._person_upsert_fn = person_upsert_fn
        self._appointment_slot_check_fn = appointment_slot_check_fn
        self._appointment_create_fn = appointment_create_fn

    def _apply_memory_state(
        self,
        *,
        state: dict[str, Any],
        participant_key: Optional[str],
        thread_key: Optional[str],
        person_id: Optional[str],
        contact_email: Optional[str],
        contact_phone: Optional[str],
        contact_name: Optional[str],
        preferred_language: Optional[str] = None,
    ) -> None:
        slots = state.setdefault("slots_json", {})
        if not isinstance(slots, dict):
            slots = {}
            state["slots_json"] = slots

        if participant_key:
            state["participant_key"] = participant_key
        if thread_key:
            state["channel_thread_key"] = thread_key
            slots.setdefault("thread_key", thread_key)
        if person_id:
            slots["person_id"] = str(person_id)

        normalized_email = _normalize_email(contact_email)
        if normalized_email:
            slots["email"] = normalized_email
        normalized_phone = _normalize_phone(contact_phone)
        if normalized_phone:
            slots["phone"] = normalized_phone

        cleaned_name = _clean_name(contact_name)
        existing_full_name = _clean_name(slots.get("full_name"))
        if cleaned_name:
            slots["full_name"] = cleaned_name
        elif existing_full_name:
            slots["full_name"] = existing_full_name

        full_name = _clean_name(slots.get("full_name"))
        first_name, last_name = _split_name(full_name)
        if first_name:
            slots["first_name"] = first_name
        if last_name:
            slots["last_name"] = last_name

        lang = str(preferred_language or state.get("language_locked") or "").strip().lower()
        if lang in {"fr", "en", "wo"}:
            slots["preferred_language"] = lang

        active_flow = str(state.get("active_flow") or "").strip().lower()
        if active_flow.startswith("booking"):
            slots["appointment_intent"] = "requested"
        elif str(slots.get("appointment_intent") or "").strip() == "requested" and active_flow == "browsing_catalog":
            slots.pop("appointment_intent", None)

        if bool(state.get("manual_lock_suppressed")):
            slots["handoff_status"] = "manual_lock"
        elif bool(state.get("handoff_allowed")):
            slots["handoff_status"] = "pending_review"

        state["summary_memory"] = _build_summary_memory(state)

    async def process_inbound_text(
        self,
        *,
        channel: str,
        user_text: str,
        conversation_id: Optional[str] = None,
        person_id: Optional[str] = None,
        contact_phone: Optional[str] = None,
        contact_email: Optional[str] = None,
        contact_name: Optional[str] = None,
        thread_key: Optional[str] = None,
        from_value: Optional[str] = None,
        reuse_recent_by_person: bool = True,
        call_sid: Optional[str] = None,
        recording_consent: Optional[bool] = None,
        conversation_resume_prefix: Optional[str] = None,
        user_message_content_for_log: Optional[str] = None,
        llm_user_text: Optional[str] = None,
        llm_context_extra: Optional[dict[str, Any]] = None,
    ) -> ChannelTurnResult:
        started_at = time.perf_counter()
        tenant_scope = str(getattr(self.db, "info", {}).get("tenant_id") or "")
        if not tenant_scope:
            raise PermissionError("missing_tenant_scope")

        # --- Channel enforcement: check if tenant's plan allows this channel ---
        try:
            from .billing import is_channel_allowed
            if not is_channel_allowed(self.db, tenant_scope, channel):
                logger.warning(
                    "Channel blocked by plan",
                    extra={"extra_fields": {"tenant_id": tenant_scope, "channel": channel}},
                )
                return ChannelTurnResult(
                    reply="Ce canal n'est pas disponible dans votre forfait actuel. Contactez l'administration pour mettre à niveau.",
                    lang="fr", person_id=person_id, conversation_id=conversation_id,
                    conversation=None, llm_state={}, response_strategy="plan_channel_blocked",
                    active_flow="browsing_catalog", conversation_state={},
                )
        except Exception:
            pass  # Fail-open: if billing check fails, allow the message

        # --- Initialize cost tracker for this turn ---
        from .llm_cost_tracker import LLMCostTracker
        cost_tracker = LLMCostTracker(self.db, tenant_id=tenant_scope)

        incoming_text = str(user_text or "").strip()
        log_user_text = str(user_message_content_for_log or incoming_text)
        llm_input_text = str(llm_user_text or incoming_text)

        resolved_person_id = person_id
        if (contact_phone or contact_email) and not resolved_person_id:
            language_hint = None
            try:
                language_hint = detect_language_safe(incoming_text)
            except Exception:
                language_hint = None
            first_name, last_name = _split_name(contact_name)
            res = self._person_upsert_fn(
                self.db,
                {
                    "first_name": first_name or "Contact",
                    "last_name": last_name,
                    "phone": contact_phone,
                    "email": contact_email,
                    "role": "candidate",
                    "preferred_language": language_hint or "fr",
                },
            )
            if res.get("success"):
                resolved_person_id = str(res.get("person_id"))

        participant_key = _participant_key(
            person_id=resolved_person_id,
            contact_email=contact_email,
            contact_phone=contact_phone,
            from_value=from_value,
        )
        effective_thread_key = thread_key or _default_thread_key(
            channel=channel,
            participant_key=participant_key,
            from_value=from_value,
            contact_email=contact_email,
            contact_phone=contact_phone,
        )

        conv = self._resolve_conversation(
            channel=channel,
            conversation_id=conversation_id,
            person_id=resolved_person_id,
            thread_key=effective_thread_key,
            reuse_recent_by_person=reuse_recent_by_person,
            call_sid=call_sid,
        )
        messages_for_conversation: list[Any] = []
        history_user_messages: list[str] = []
        recent_turn_memory: list[dict[str, str]] = []
        if conv is not None:
            messages_for_conversation = kb_service.list_messages_for_conversation(self.db, conv.id)
            history_user_messages = _history_user_messages(messages_for_conversation)
            recent_turn_memory = _recent_turns(messages_for_conversation)

        state = parse_conversation_state(str(getattr(conv, "conversation_state", "") or "") if conv is not None else None)
        state_enter = str(state.get("active_flow") or "browsing_catalog")
        state.setdefault("active_flow", state_enter)
        state["session_ttl_expired"] = False
        state["reset_reason"] = None

        self._apply_memory_state(
            state=state,
            participant_key=participant_key or str(state.get("participant_key") or "").strip() or None,
            thread_key=effective_thread_key or str(state.get("channel_thread_key") or "").strip() or None,
            person_id=resolved_person_id or str((state.get("slots_json") or {}).get("person_id") or "").strip() or None,
            contact_email=contact_email or str((state.get("slots_json") or {}).get("email") or "").strip() or None,
            contact_phone=contact_phone or str((state.get("slots_json") or {}).get("phone") or "").strip() or None,
            contact_name=contact_name or str((state.get("slots_json") or {}).get("full_name") or "").strip() or None,
        )

        if conv is not None and self._should_suppress_locked_conversation(channel=channel, conv=conv):
            locked_reason = self._locked_conversation_reason(conv=conv)
            lang = self._resolve_locked_conversation_language(state=state, incoming_text=incoming_text)
            self._apply_memory_state(
                state=state,
                participant_key=participant_key or str(state.get("participant_key") or "").strip() or None,
                thread_key=effective_thread_key or str(state.get("channel_thread_key") or "").strip() or None,
                person_id=resolved_person_id or str((state.get("slots_json") or {}).get("person_id") or "").strip() or None,
                contact_email=contact_email or str((state.get("slots_json") or {}).get("email") or "").strip() or None,
                contact_phone=contact_phone or str((state.get("slots_json") or {}).get("phone") or "").strip() or None,
                contact_name=contact_name or str((state.get("slots_json") or {}).get("full_name") or "").strip() or None,
                preferred_language=lang,
            )
            reply = self._locked_conversation_reply(lang=lang, reason=locked_reason)
            state["response_strategy"] = "deterministic_manual_lock"
            state["handoff_allowed"] = True
            state["handoff_trigger_reason"] = locked_reason
            state["manual_lock_suppressed"] = True
            self._apply_memory_state(
                state=state,
                participant_key=participant_key or str(state.get("participant_key") or "").strip() or None,
                thread_key=effective_thread_key or str(state.get("channel_thread_key") or "").strip() or None,
                person_id=resolved_person_id or str((state.get("slots_json") or {}).get("person_id") or "").strip() or None,
                contact_email=contact_email or str((state.get("slots_json") or {}).get("email") or "").strip() or None,
                contact_phone=contact_phone or str((state.get("slots_json") or {}).get("phone") or "").strip() or None,
                contact_name=contact_name or str((state.get("slots_json") or {}).get("full_name") or "").strip() or None,
                preferred_language=lang,
            )
            resolved_person_id = resolved_person_id or (str(getattr(conv, "person_id", "") or "") or None)
            llm_state = {
                "channel": channel,
                "from": from_value,
                "lang_detected": lang,
                "response_language": lang,
                "person_id": resolved_person_id,
                "tenant_id": tenant_scope,
                "recent_user_messages": (history_user_messages + [incoming_text])[-4:],
                "recent_turns": recent_turn_memory,
                "conversation_active_flow": state.get("active_flow"),
                "conversation_slots": state.get("slots_json") if isinstance(state.get("slots_json"), dict) else {},
                "memory_slots": _memory_slots_from_state(state),
                "session_summary": state.get("summary_memory"),
                "participant_key": state.get("participant_key"),
                "channel_thread_key": state.get("channel_thread_key"),
                "response_strategy": "deterministic_manual_lock",
                "failure_count": int(state.get("failure_count") or 0),
                "fallback_stage": state.get("fallback_stage"),
                "clarification_success": bool(state.get("clarification_success")),
                "handoff_allowed": True,
                "handoff_trigger_reason": locked_reason,
                "structured_extraction_fail_count": int(state.get("structured_extraction_fail_count") or 0),
                "structured_extraction_error_type": None,
                "state_enter": state_enter,
                "state_exit": state_enter,
                "short_text_flag": bool(state.get("short_text_flag")),
                "session_ttl_expired": False,
                "reset_reason": state.get("reset_reason"),
            }
            if llm_context_extra:
                llm_state.update({k: v for k, v in llm_context_extra.items() if v is not None})

            conv = self._persist_locked_conversation_turn(
                conv=conv,
                channel=channel,
                user_text=log_user_text,
                reply=reply,
                person_id=resolved_person_id,
                state=state,
                call_sid=call_sid,
                recording_consent=recording_consent,
                resume_prefix=conversation_resume_prefix,
                locked_reason=locked_reason,
            )

            self._log_turn_observability(
                channel=channel,
                tenant_id=tenant_scope,
                conversation_id=str(conv.id),
                person_id=resolved_person_id,
                state=state,
                lang=lang,
                response_strategy="deterministic_manual_lock",
                llm_state=llm_state,
                llm_extract_called=False,
                llm_extract_applied=False,
                llm_generate_called=False,
                llm_rephrase_called=False,
                llm_rephrase_applied=False,
                llm_last_error="",
                llm_fallback_reason="",
                llm_tool_calls=[],
                state_enter=state_enter,
                state_exit=state_enter,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
            )

            return ChannelTurnResult(
                reply=reply,
                lang=lang,
                person_id=resolved_person_id,
                conversation_id=str(conv.id),
                conversation=conv,
                llm_state=llm_state,
                response_strategy="deterministic_manual_lock",
                active_flow=state_enter,
                conversation_state=state,
            )

        llm = self._llm_factory()
        orchestrator = ConversationOrchestrator(self.db, track_search_fn=self._track_search_fn)

        if conv is not None and self._is_session_ttl_expired(
            channel=channel,
            conv=conv,
            messages=messages_for_conversation,
        ):
            state = orchestrator.apply_controlled_partial_reset(state, reason="session_ttl_expired")
            state["session_ttl_expired"] = True
            state["reset_reason"] = "session_ttl_expired"

        structured_entities = None
        llm_extract_called = False
        llm_extract_applied = False
        structured_extraction_error_type: Optional[str] = None
        structured_extraction_fail_count = int(state.get("structured_extraction_fail_count") or 0)
        extractor = getattr(llm, "extract_structured_message", None)
        if callable(extractor):
            llm_extract_called = True
            extraction_session_state = {
                "channel": channel,
                "tenant_id": tenant_scope,
                "lang_detected": state.get("language_locked"),
                "response_language": state.get("language_locked"),
                "active_flow": state.get("active_flow"),
                "structured_extraction_fail_count": structured_extraction_fail_count,
            }
            try:
                structured_entities = await extractor(
                    llm_input_text,
                    session_state=extraction_session_state,
                )
            except Exception as exc:
                structured_extraction_error_type = exc.__class__.__name__
                structured_entities = None
                logger.warning(
                    "channel_structured_extraction_call_failed",
                    extra={
                        "extra_fields": {
                            "channel": channel,
                            "tenant_id": tenant_scope,
                            "error_type": structured_extraction_error_type,
                            "error": str(exc),
                        }
                    },
                )
            structured_extraction_fail_count = int(extraction_session_state.get("structured_extraction_fail_count") or 0)
            structured_extraction_error_type = (
                str(extraction_session_state.get("structured_extraction_error_type") or "").strip()
                or structured_extraction_error_type
            )
            state["structured_extraction_fail_count"] = structured_extraction_fail_count
            llm_extract_applied = isinstance(structured_entities, dict) and bool(structured_entities)
            if not llm_extract_applied and structured_extraction_fail_count > 0:
                logger.warning(
                    "channel_structured_extraction_fallback_used",
                    extra={
                        "extra_fields": {
                            "channel": channel,
                            "tenant_id": tenant_scope,
                            "active_flow": state.get("active_flow"),
                            "structured_extraction_fail_count": structured_extraction_fail_count,
                            "structured_extraction_error_type": structured_extraction_error_type,
                        }
                    },
                )

        turn = orchestrator.process_message(
            message=incoming_text,
            history_user_messages=history_user_messages,
            state=state,
            llm_entities=structured_entities,
        )
        if bool(state.get("session_ttl_expired")):
            turn.state["session_ttl_expired"] = True
            if not str(turn.state.get("reset_reason") or "").strip():
                turn.state["reset_reason"] = "session_ttl_expired"
        state_exit = str(turn.state.get("active_flow") or "browsing_catalog")
        lang = turn.lang if turn.lang in {"fr", "en", "wo"} else "fr"
        reply = turn.reply or ""

        if not turn.use_llm and str(turn.state.get("response_strategy") or turn.response_strategy) == "deterministic_booking_submitted":
            booking_side_effect = await self._finalize_booking_submission(
                state=turn.state,
                lang=lang,
                person_id=resolved_person_id,
            )
            if booking_side_effect.get("person_id"):
                resolved_person_id = str(booking_side_effect["person_id"])
            if isinstance(booking_side_effect.get("reply"), str) and booking_side_effect["reply"].strip():
                reply = str(booking_side_effect["reply"]).strip()
            turn.response_strategy = str(turn.state.get("response_strategy") or turn.response_strategy)
            state_exit = str(turn.state.get("active_flow") or "browsing_catalog")

        self._apply_memory_state(
            state=turn.state,
            participant_key=participant_key or str(turn.state.get("participant_key") or "").strip() or None,
            thread_key=effective_thread_key or str(turn.state.get("channel_thread_key") or "").strip() or None,
            person_id=resolved_person_id or str((turn.state.get("slots_json") or {}).get("person_id") or "").strip() or None,
            contact_email=contact_email or str((turn.state.get("slots_json") or {}).get("email") or "").strip() or None,
            contact_phone=contact_phone or str((turn.state.get("slots_json") or {}).get("phone") or "").strip() or None,
            contact_name=contact_name or str((turn.state.get("slots_json") or {}).get("full_name") or "").strip() or None,
            preferred_language=lang,
        )
        llm_state = {
            "channel": channel,
            "from": from_value,
            "lang_detected": lang,
            "response_language": lang,
            "person_id": resolved_person_id,
            "tenant_id": tenant_scope,
            "recent_user_messages": history_user_messages[-4:],
            "recent_turns": recent_turn_memory,
            "conversation_active_flow": turn.state.get("active_flow"),
            "conversation_slots": turn.state.get("slots_json") if isinstance(turn.state.get("slots_json"), dict) else {},
            "memory_slots": _memory_slots_from_state(turn.state),
            "session_summary": turn.state.get("summary_memory"),
            "participant_key": turn.state.get("participant_key"),
            "channel_thread_key": turn.state.get("channel_thread_key"),
            "response_strategy": turn.response_strategy,
            "failure_count": int(turn.state.get("failure_count") or 0),
            "fallback_stage": turn.state.get("fallback_stage"),
            "clarification_success": bool(turn.state.get("clarification_success")),
            "handoff_allowed": bool(turn.state.get("handoff_allowed")),
            "handoff_trigger_reason": turn.state.get("handoff_trigger_reason"),
            "structured_extraction_fail_count": int(turn.state.get("structured_extraction_fail_count") or 0),
            "structured_extraction_error_type": structured_extraction_error_type,
            "state_enter": state_enter,
            "state_exit": state_exit,
            "short_text_flag": bool(turn.state.get("short_text_flag")),
            "session_ttl_expired": bool(turn.state.get("session_ttl_expired")),
            "reset_reason": turn.state.get("reset_reason"),
            "fallback_used": False,
        }
        if llm_context_extra:
            llm_state.update({k: v for k, v in llm_context_extra.items() if v is not None})

        llm_generate_called = False
        llm_rephrase_called = False
        llm_rephrase_applied = False
        if turn.use_llm:
            llm_generate_called = True
            reply = await llm.generate_reply_with_tools(
                llm_input_text,
                session_state=llm_state,
                db_session=self.db,
            )
            # Track LLM cost
            prompt_tokens = int(getattr(llm, "last_prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(llm, "last_completion_tokens", 0) or 0)
            if prompt_tokens > 0 or completion_tokens > 0:
                cost_tracker.record_llm_call(
                    model=llm.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    channel=channel,
                    call_type="generate",
                    conversation_id=str(getattr(conv, "id", "")) if conv else None,
                )
            llm_fallback_reason = str(getattr(llm, "last_fallback_reason", "") or "")
            if getattr(llm, "last_error", None) or llm_fallback_reason:
                turn.state = orchestrator.normalize_state_for_provider_fallback(turn.state)
                state_exit = str(turn.state.get("active_flow") or "browsing_catalog")
                reply = orchestrator.contextual_fallback_reply(state=turn.state, lang=lang)
                turn.state["response_strategy"] = "fallback_contextual"
                llm_state["conversation_active_flow"] = turn.state.get("active_flow")
                llm_state["conversation_slots"] = turn.state.get("slots_json") if isinstance(turn.state.get("slots_json"), dict) else {}
                llm_state["memory_slots"] = _memory_slots_from_state(turn.state)
                llm_state["response_strategy"] = "fallback_contextual"
                llm_state["handoff_allowed"] = bool(turn.state.get("handoff_allowed"))
                llm_state["handoff_trigger_reason"] = turn.state.get("handoff_trigger_reason")
                llm_state["state_exit"] = state_exit
                llm_state["fallback_used"] = True
                logger.warning(
                    "channel_llm_provider_error_fallback_used",
                    extra={
                        "extra_fields": {
                            "channel": channel,
                            "tenant_id": tenant_scope,
                            "error": str(getattr(llm, "last_error", "")),
                            "fallback_reason": llm_fallback_reason or "llm_provider_error",
                            "active_flow": turn.state.get("active_flow"),
                        }
                    },
                )
            else:
                turn.state["response_strategy"] = "llm"
                llm_state["response_strategy"] = "llm"
        else:
            rephraser = getattr(llm, "rephrase_controlled_reply", None)
            if callable(rephraser):
                llm_rephrase_called = True
                try:
                    rephrased = await rephraser(
                        reply_text=reply,
                        session_state=llm_state,
                        response_contract={
                            "response_strategy": turn.response_strategy,
                            "active_flow": turn.state.get("active_flow"),
                            "lang": lang,
                            "slots": turn.state.get("slots_json") if isinstance(turn.state.get("slots_json"), dict) else {},
                        },
                    )
                    if isinstance(rephrased, str) and rephrased.strip():
                        reply = rephrased.strip()
                        llm_rephrase_applied = True
                except Exception:
                    pass

        conv = self._persist_turn(
            conv=conv,
            channel=channel,
            user_text=log_user_text,
            reply=reply,
            person_id=resolved_person_id,
            intent=turn.inferred_intent or llm_state.get("intent_detected"),
            state=turn.state,
            response_strategy=str(turn.state.get("response_strategy") or turn.response_strategy),
            llm_state=llm_state,
            call_sid=call_sid,
            recording_consent=recording_consent,
            resume_prefix=conversation_resume_prefix,
        )

        self._log_turn_observability(
            channel=channel,
            tenant_id=tenant_scope,
            conversation_id=str(conv.id) if conv is not None else None,
            person_id=resolved_person_id,
            state=turn.state,
            lang=lang,
            response_strategy=str(turn.state.get("response_strategy") or turn.response_strategy),
            llm_state=llm_state,
            llm_extract_called=llm_extract_called,
            llm_extract_applied=llm_extract_applied,
            llm_generate_called=llm_generate_called,
            llm_rephrase_called=llm_rephrase_called,
            llm_rephrase_applied=llm_rephrase_applied,
            llm_last_error=str(getattr(llm, "last_error", "") or ""),
            llm_fallback_reason=str(getattr(llm, "last_fallback_reason", "") or ""),
            llm_tool_calls=list(getattr(llm, "last_tool_calls", []) or []),
            state_enter=state_enter,
            state_exit=state_exit,
            duration_ms=int((time.perf_counter() - started_at) * 1000),
        )

        # Persist LLM costs to quota
        try:
            cost_tracker.persist_to_quota()
        except Exception:
            pass

        return ChannelTurnResult(
            reply=reply,
            lang=lang,
            person_id=resolved_person_id,
            conversation_id=str(conv.id) if conv is not None else conversation_id,
            conversation=conv,
            llm_state=llm_state,
            response_strategy=str(turn.state.get("response_strategy") or turn.response_strategy),
            active_flow=str(turn.state.get("active_flow") or ""),
            conversation_state=turn.state,
        )

    async def _finalize_booking_submission(
        self,
        *,
        state: dict[str, Any],
        lang: str,
        person_id: Optional[str],
    ) -> dict[str, Any]:
        slots = state.setdefault("slots_json", {})
        if not isinstance(slots, dict):
            slots = {}
            state["slots_json"] = slots

        existing_appointment_id = str(slots.get("appointment_id") or "").strip()
        if existing_appointment_id:
            state["active_flow"] = "browsing_catalog"
            state["appointment_locked"] = True
            state["response_strategy"] = "deterministic_booking_submitted_existing"
            return {"reply": self._render_booking_created_reply(state=state, lang=lang), "person_id": person_id}

        date_iso = _normalize_booking_date(slots.get("appointment_date"))
        time_iso = _normalize_booking_time(slots.get("appointment_time"))
        if not date_iso or not time_iso:
            state["active_flow"] = "booking_collect_datetime"
            state["response_strategy"] = "deterministic_booking_collect_datetime_invalid"
            return {"reply": self._render_booking_datetime_invalid_reply(lang=lang), "person_id": person_id}

        resolved_person_id = person_id or str(slots.get("person_id") or "").strip() or None
        if not resolved_person_id:
            full_name = str(slots.get("full_name") or "").strip()
            email = str(slots.get("email") or "").strip() or None
            phone = str(slots.get("phone") or "").strip() or None
            if email or phone:
                first_name, last_name = _split_name(full_name)
                res = self._person_upsert_fn(
                    self.db,
                    {
                        "first_name": first_name or "Contact",
                        "last_name": last_name,
                        "email": email,
                        "phone": phone,
                        "role": "candidate",
                        "preferred_language": lang if lang in {"fr", "en", "wo"} else "fr",
                    },
                )
                if res.get("success"):
                    resolved_person_id = str(res.get("person_id"))
        if resolved_person_id:
            slots["person_id"] = resolved_person_id

        slot_check = self._appointment_slot_check_fn(
            self.db,
            {
                "date": date_iso,
                "time": time_iso,
                "duration_minutes": 45,
            },
        )
        if not slot_check.get("success"):
            state["response_strategy"] = "fallback_contextual"
            return {"reply": ConversationOrchestrator(self.db, track_search_fn=self._track_search_fn).contextual_fallback_reply(state=state, lang=lang), "person_id": resolved_person_id}
        if not bool(slot_check.get("available")):
            state["active_flow"] = "booking_collect_datetime"
            reason = str(slot_check.get("reason") or "")
            if reason == "no_agent_available":
                state["response_strategy"] = "deterministic_booking_no_agent_available"
                return {
                    "reply": self._render_booking_no_agent_available_reply(lang=lang),
                    "person_id": resolved_person_id,
                }
            state["response_strategy"] = "deterministic_booking_slot_conflict"
            slots["requested_appointment_date"] = str(slots.get("appointment_date") or "")
            slots["requested_appointment_time"] = str(slots.get("appointment_time") or "")
            slots.pop("appointment_id", None)
            return {
                "reply": self._render_booking_slot_conflict_reply(lang=lang, conflicts=int(slot_check.get("conflicts") or 0)),
                "person_id": resolved_person_id,
            }

        create_args = {
            "person_id": resolved_person_id,
            "email": str(slots.get("email") or "").strip() or None,
            "phone": str(slots.get("phone") or "").strip() or None,
            "track_id": str(slots.get("track_id") or "").strip() or None,
            "track_name": str(slots.get("track_name") or "").strip() or None,
            "program_name": str(slots.get("program_name") or "").strip() or None,
            "date": date_iso,
            "time": time_iso,
            "duration_minutes": 45,
            "statut": "pending",
            "lang": lang if lang in {"fr", "en", "wo"} else "fr",
        }
        create_result = await self._appointment_create_fn(self.db, create_args)
        if not create_result.get("success"):
            err = str(create_result.get("error") or "")
            if err in {"slot_conflict", "person_slot_conflict"}:
                state["active_flow"] = "booking_collect_datetime"
                state["response_strategy"] = "deterministic_booking_slot_conflict"
                return {
                    "reply": self._render_booking_slot_conflict_reply(lang=lang, conflicts=1),
                    "person_id": resolved_person_id,
                }
            if err == "no_agent_available":
                state["active_flow"] = "booking_collect_datetime"
                state["response_strategy"] = "deterministic_booking_no_agent_available"
                return {
                    "reply": self._render_booking_no_agent_available_reply(lang=lang),
                    "person_id": resolved_person_id,
                }
            if err in {"appointment_must_be_in_future", "invalid_datetime_format_use_yyyy_mm_dd_and_hh_mm", "date_and_time_required"}:
                state["active_flow"] = "booking_collect_datetime"
                state["response_strategy"] = "deterministic_booking_collect_datetime_invalid"
                return {"reply": self._render_booking_datetime_invalid_reply(lang=lang), "person_id": resolved_person_id}
            # Fail closed in wording: do not claim the appointment was created.
            state["active_flow"] = "booking_confirm"
            state["response_strategy"] = "fallback_contextual"
            return {
                "reply": self._render_booking_create_failed_reply(lang=lang),
                "person_id": resolved_person_id,
            }

        slots["appointment_id"] = str(create_result.get("appointment_id") or "")
        slots["appointment_status"] = str(create_result.get("status") or "created")
        slots["appointment_date_iso"] = date_iso
        slots["appointment_time_iso"] = time_iso
        if create_result.get("agent_id"):
            slots["assigned_agent_id"] = str(create_result.get("agent_id"))
        if create_result.get("agent_name"):
            slots["assigned_agent_name"] = str(create_result.get("agent_name"))
        notifications = create_result.get("notifications") if isinstance(create_result.get("notifications"), dict) else {}
        if notifications:
            slots["notification_channel"] = notifications.get("channel")
            slots["notification_sent"] = bool(notifications.get("sent"))
            slots["notification_queued"] = bool(notifications.get("queued"))
            if notifications.get("reason"):
                slots["notification_reason"] = str(notifications.get("reason"))
        slots.pop("requested_appointment_date", None)
        slots.pop("requested_appointment_time", None)
        state["active_flow"] = "browsing_catalog"
        state["appointment_locked"] = True
        state["response_strategy"] = "deterministic_booking_submitted_persisted"
        return {"reply": self._render_booking_created_reply(state=state, lang=lang), "person_id": resolved_person_id}

    def _render_booking_created_reply(self, *, state: dict[str, Any], lang: str) -> str:
        slots = state.get("slots_json") if isinstance(state.get("slots_json"), dict) else {}
        notification_channel = str(slots.get("notification_channel") or "").strip()
        notification_sent = bool(slots.get("notification_sent"))
        notification_queued = bool(slots.get("notification_queued"))
        appointment_id = str(slots.get("appointment_id") or "").strip()
        assigned_agent_name = str(slots.get("assigned_agent_name") or "").strip()
        ref_suffix = f" (ref: {appointment_id[:8]})" if appointment_id else ""
        assignment_note_en = f" An admissions agent has been assigned ({assigned_agent_name})." if assigned_agent_name else ""
        assignment_note_wo = f" Agent admissions bu njëkk bi ñu ko jox na ko ({assigned_agent_name})." if assigned_agent_name else ""
        assignment_note_fr = f" Un agent admission a ete assigne ({assigned_agent_name})." if assigned_agent_name else ""

        if lang == "en":
            if notification_sent and notification_channel:
                return f"Your admissions appointment request has been recorded{ref_suffix}.{assignment_note_en} A confirmation was sent via {notification_channel}. The admissions team will confirm the final slot shortly.".replace("..", ".")
            if notification_queued:
                return f"Your admissions appointment request has been recorded{ref_suffix}.{assignment_note_en} A confirmation is queued and will be sent shortly.".replace("..", ".")
            return f"Your admissions appointment request has been recorded{ref_suffix}.{assignment_note_en} The admissions team will confirm the final slot shortly.".replace("..", ".")
        if lang == "wo":
            if notification_sent and notification_channel:
                return f"Demande rendez-vous admission bi am na{ref_suffix}.{assignment_note_wo} Confirmation bi dem na ci {notification_channel}. Equipe admission bi dina la confirmé waxtu bu mujj bi ci lu gaaw.".replace("..", ".")
            if notification_queued:
                return f"Demande rendez-vous admission bi am na{ref_suffix}.{assignment_note_wo} Confirmation bi ngi ci waajal te dina ñëw ci lu gaaw.".replace("..", ".")
            return f"Demande rendez-vous admission bi am na{ref_suffix}.{assignment_note_wo} Equipe admission bi dina la confirmé waxtu bu mujj bi ci lu gaaw.".replace("..", ".")
        if notification_sent and notification_channel:
            return (
                f"Votre demande de rendez-vous admission est bien enregistree{ref_suffix}.{assignment_note_fr} "
                f"Une confirmation a ete envoyee via {notification_channel}. Le service admission vous confirmera le creneau final rapidement."
            ).replace("..", ".")
        if notification_queued:
            return (
                f"Votre demande de rendez-vous admission est bien enregistree{ref_suffix}.{assignment_note_fr} "
                "Une confirmation est en file d'envoi et vous sera envoyee rapidement."
            ).replace("..", ".")
        return f"Votre demande de rendez-vous admission est bien enregistree{ref_suffix}.{assignment_note_fr} Le service admission vous confirmera le creneau final rapidement.".replace("..", ".")

    def _render_booking_slot_conflict_reply(self, *, lang: str, conflicts: int) -> str:
        if lang == "en":
            return "The requested slot is no longer available. Please send another preferred date and time for the admissions appointment."
        if lang == "wo":
            return "Waxtu bi nga laajoon dootu am. Yónnee ma beneen bés ak waxtu ngir rendez-vous admission bi."
        return "Le creneau demande n'est plus disponible. Merci d'envoyer une autre date et heure souhaitees pour le rendez-vous admission."

    def _render_booking_no_agent_available_reply(self, *, lang: str) -> str:
        if lang == "en":
            return "No admissions agent is available on the requested slot right now. Please send another preferred date and time, and I will check again."
        if lang == "wo":
            return "Amul agent admissions bu libre ci waxtu bi nga laaj. Yónnee ma beneen bés ak waxtu, dinaa seetaat."
        return "Aucun agent admission n'est disponible sur le creneau demande pour le moment. Merci d'envoyer une autre date et heure souhaitees."

    def _render_booking_datetime_invalid_reply(self, *, lang: str) -> str:
        if lang == "en":
            return "I could not validate the requested slot format. Please send a date and time like 28/02/2026 at 15:00."
        if lang == "wo":
            return "Ma manul woon xam bés ak waxtu bi ci format bi. Yónnee ma ko ni 28/02/2026 ak 15h."
        return "Je n'ai pas pu valider le format du creneau. Merci d'envoyer une date et une heure comme 28/02/2026 a 15h."

    def _render_booking_create_failed_reply(self, *, lang: str) -> str:
        if lang == "en":
            return "I could not finalize the appointment request right now. Please resend your preferred date/time or let me know if you want another slot."
        if lang == "wo":
            return "Ma manul woon yokk rendez-vous bi léegi. Yónnee maaat bés ak waxtu bi nga bëgg, walla beneen creneau."
        return "Je n'ai pas pu finaliser la demande de rendez-vous pour le moment. Merci de renvoyer votre date/heure souhaitees ou de proposer un autre creneau."

    def _log_turn_observability(
        self,
        *,
        channel: str,
        tenant_id: str,
        conversation_id: Optional[str],
        person_id: Optional[str],
        state: dict[str, Any],
        lang: str,
        response_strategy: str,
        llm_state: dict[str, Any],
        llm_extract_called: bool,
        llm_extract_applied: bool,
        llm_generate_called: bool,
        llm_rephrase_called: bool,
        llm_rephrase_applied: bool,
        llm_last_error: str,
        llm_fallback_reason: str,
        llm_tool_calls: list[str],
        state_enter: str,
        state_exit: str,
        duration_ms: int,
    ) -> None:
        slots = state.get("slots_json") if isinstance(state.get("slots_json"), dict) else {}
        flow_state = str(state.get("active_flow") or "browsing_catalog")
        language_locked = str(state.get("language_locked") or "") or None
        slots_filled = sorted(
            key
            for key, value in slots.items()
            if key not in _OBS_SLOT_IGNORE_KEYS and _slot_value_present(value)
        )
        slots_missing = _infer_missing_slots_for_observability(flow_state=flow_state, slots=slots)
        llm_called = bool(llm_extract_called or llm_generate_called or llm_rephrase_called)
        fallback_reason = (
            llm_fallback_reason
            or _fallback_reason_from_strategy(response_strategy)
            or ("llm_provider_error" if llm_last_error and response_strategy.startswith("fallback") else None)
        )
        fallback_used = bool(llm_state.get("fallback_used")) or bool(fallback_reason) or response_strategy.startswith("fallback")
        failure_count = max(0, int(state.get("failure_count") or 0))
        fallback_stage = str(state.get("fallback_stage") or "") or None
        clarification_success = bool(state.get("clarification_success"))
        handoff_trigger_reason = str(state.get("handoff_trigger_reason") or "") or None
        short_text_flag = bool(state.get("short_text_flag"))
        session_ttl_expired = bool(state.get("session_ttl_expired"))
        reset_reason = str(state.get("reset_reason") or "") or None
        handoff_trigger = _handoff_trigger_from_context(
            response_strategy=response_strategy,
            llm_state=llm_state,
        )
        strategy_category = _response_strategy_category(
            response_strategy=response_strategy,
            llm_rephrase_applied=llm_rephrase_applied,
            llm_extract_applied=llm_extract_applied,
        )
        logger.info(
            "agent_turn_processed",
            extra={
                "extra_fields": {
                    "channel": channel,
                    "tenant_id": tenant_id,
                    "conversation_id": conversation_id,
                    "person_id": person_id,
                    "response_strategy": response_strategy,
                    "response_strategy_category": strategy_category,
                    "flow_state": flow_state,
                    "state_enter": state_enter,
                    "state_exit": state_exit,
                    "language_locked": language_locked,
                    "lang_detected": lang,
                    "slots_filled": slots_filled,
                    "slots_missing": slots_missing,
                    "llm_called": llm_called,
                    "llm_extract_called": llm_extract_called,
                    "llm_extract_applied": llm_extract_applied,
                    "llm_generate_called": llm_generate_called,
                    "llm_rephrase_called": llm_rephrase_called,
                    "llm_rephrase_applied": llm_rephrase_applied,
                    "tool_calls": len(llm_tool_calls),
                    "tool_call_names": llm_tool_calls,
                    "fallback_used": fallback_used,
                    "fallback_reason": fallback_reason,
                    "failure_count": failure_count,
                    "fallback_stage": fallback_stage,
                    "clarification_success": clarification_success,
                    "short_text_flag": short_text_flag,
                    "session_ttl_expired": session_ttl_expired,
                    "reset_reason": reset_reason,
                    "handoff_trigger": handoff_trigger,
                    "handoff_trigger_reason": handoff_trigger_reason,
                    "duration_ms": max(0, int(duration_ms)),
                }
            },
        )

    def _resolve_conversation(
        self,
        *,
        channel: str,
        conversation_id: Optional[str],
        person_id: Optional[str],
        thread_key: Optional[str],
        reuse_recent_by_person: bool,
        call_sid: Optional[str],
    ):
        conv = None
        if conversation_id:
            conv = kb_service.get_conversation(self.db, _as_uuid(conversation_id))
            if conv is not None:
                return conv
        if channel == "call" and call_sid:
            conv = kb_service.find_latest_conversation_by_call_sid(self.db, call_sid=call_sid)
            if conv is not None:
                return conv
        if reuse_recent_by_person and person_id:
            recent = kb_service.list_recent_conversations_for_person(self.db, person_id=person_id, canal=channel, limit=8)
            for candidate in recent:
                if _match_thread_key(candidate, thread_key):
                    return candidate
        return None

    def _persist_turn(
        self,
        *,
        conv,
        channel: str,
        user_text: str,
        reply: str,
        person_id: Optional[str],
        intent: Optional[str],
        state: dict[str, Any],
        response_strategy: str,
        llm_state: dict[str, Any],
        call_sid: Optional[str],
        recording_consent: Optional[bool],
        resume_prefix: Optional[str],
    ):
        snippet_prefix = (resume_prefix or channel.capitalize()).strip()
        snippet = f"{snippet_prefix} | User: {user_text} | Reply: {reply}"
        if len(snippet) > 500:
            snippet = snippet[:497] + "..."

        serialized_state = dump_conversation_state(state)
        needs_handoff = bool(
            response_strategy == "fallback_handoff"
            or llm_state.get("handoff_allowed")
        )
        if conv is not None:
            if person_id and not conv.person_id:
                conv.person_id = _as_uuid(person_id)
            conv.resume = snippet
            conv.intention = intent or conv.intention
            conv.conversation_state = serialized_state
            if needs_handoff:
                conv.status = "pending_review"
                conv.requires_validation = True
                if conv.mode == "auto":
                    conv.mode = "manual"
            if call_sid and not getattr(conv, "call_sid", None):
                conv.call_sid = call_sid
            if recording_consent is not None:
                conv.recording_consent = bool(recording_consent)
            self.db.add(conv)
            self.db.commit()
            self.db.refresh(conv)
        else:
            conv = kb_service.create_conversation(
                self.db,
                person_id=person_id,
                resume=snippet,
                canal=channel,
                intention=intent,
                conversation_state=serialized_state,
                call_sid=call_sid,
                recording_consent=bool(recording_consent) if recording_consent is not None else None,
            )
            if needs_handoff:
                # Reload and flag the conversation for manual follow-up.
                conv.status = "pending_review"
                conv.requires_validation = True
                if conv.mode == "auto":
                    conv.mode = "manual"
                self.db.add(conv)
                self.db.commit()
                self.db.refresh(conv)

        kb_service.create_message(
            self.db,
            conversation_id=str(conv.id),
            role="user",
            canal=channel,
            content=user_text,
        )
        kb_service.create_message(
            self.db,
            conversation_id=str(conv.id),
            role="assistant",
            canal=channel,
            content=reply,
        )
        return conv

    def _should_suppress_locked_conversation(self, *, channel: str, conv) -> bool:
        normalized_channel = str(channel or "").strip().lower()
        if normalized_channel in {"call", "voice"}:
            return False
        mode = str(getattr(conv, "mode", "") or "").strip().lower()
        status = str(getattr(conv, "status", "") or "").strip().lower()
        return mode == "manual" or status == "pending_review"

    def _locked_conversation_reason(self, *, conv) -> str:
        status = str(getattr(conv, "status", "") or "").strip().lower()
        if status == "pending_review":
            return "pending_review_lock"
        return "manual_mode_lock"

    def _resolve_locked_conversation_language(self, *, state: dict[str, Any], incoming_text: str) -> str:
        slots = state.get("slots_json") if isinstance(state.get("slots_json"), dict) else {}
        candidates = [
            state.get("language_locked"),
            slots.get("preferred_language") if isinstance(slots, dict) else None,
            detect_language_safe(incoming_text),
        ]
        for candidate in candidates:
            lang = str(candidate or "").strip().lower()
            if lang in {"fr", "en", "wo"}:
                return lang
        return "fr"

    def _locked_conversation_reply(self, *, lang: str, reason: str) -> str:
        messages = {
            "fr": {
                "manual_mode_lock": "Un conseiller humain gère déjà cette conversation. Votre message a été transmis et vous recevrez une réponse rapidement.",
                "pending_review_lock": "Votre message a bien été transmis à notre équipe admissions pour suivi. Un conseiller vous répondra rapidement.",
            },
            "en": {
                "manual_mode_lock": "A human advisor is already handling this conversation. Your message has been forwarded and you will receive a reply shortly.",
                "pending_review_lock": "Your message has been forwarded to our admissions team for follow-up. A human advisor will reply shortly.",
            },
            "wo": {
                "manual_mode_lock": "Nit ku ci saytujang mi ngi topp waxtaan wi. Yonnee nanu sa bataaxal, dinaanu la tontu ci gaaw.",
                "pending_review_lock": "Yonnee nanu sa bataaxal ci ekipu admissions bi ngir toppatoo. Dinaanu la tontu ci gaaw.",
            },
        }
        lang_messages = messages.get(lang) or messages["fr"]
        return str(lang_messages.get(reason) or lang_messages["manual_mode_lock"])

    def _locked_conversation_system_note(self, *, reason: str) -> str:
        if reason == "pending_review_lock":
            return "Nouveau message reçu pendant pending_review. Réponse automatique IA supprimée, suivi humain requis."
        return "Nouveau message reçu pendant verrouillage manuel. Réponse automatique IA supprimée."

    def _persist_locked_conversation_turn(
        self,
        *,
        conv,
        channel: str,
        user_text: str,
        reply: str,
        person_id: Optional[str],
        state: dict[str, Any],
        call_sid: Optional[str],
        recording_consent: Optional[bool],
        resume_prefix: Optional[str],
        locked_reason: str,
    ):
        snippet_prefix = (resume_prefix or channel.capitalize()).strip()
        snippet = f"{snippet_prefix} | User: {user_text} | Reply: {reply}"
        if len(snippet) > 500:
            snippet = snippet[:497] + "..."

        if person_id and not conv.person_id:
            conv.person_id = _as_uuid(person_id)
        conv.resume = snippet
        conv.conversation_state = dump_conversation_state(state)
        if str(getattr(conv, "status", "") or "").strip().lower() == "pending_review":
            conv.requires_validation = True
        if call_sid and not getattr(conv, "call_sid", None):
            conv.call_sid = call_sid
        if recording_consent is not None:
            conv.recording_consent = bool(recording_consent)
        self.db.add(conv)
        self.db.commit()
        self.db.refresh(conv)

        kb_service.create_message(
            self.db,
            conversation_id=str(conv.id),
            role="user",
            canal=channel,
            content=user_text,
        )
        kb_service.create_message(
            self.db,
            conversation_id=str(conv.id),
            role="assistant",
            canal=channel,
            content=reply,
        )
        kb_service.create_message(
            self.db,
            conversation_id=str(conv.id),
            role="system",
            canal=channel,
            content=self._locked_conversation_system_note(reason=locked_reason),
        )
        return conv

    def _channel_session_ttl_seconds(self, channel: str) -> int:
        normalized = str(channel or "").strip().lower()
        if normalized in {"call", "voice"}:
            return max(0, int(settings.voice_session_ttl_sec or 0))
        if normalized == "chat":
            return max(0, int(settings.chat_session_ttl_sec or 0))
        if normalized == "email":
            return max(0, int(settings.email_session_ttl_sec or 0))
        if normalized == "sms":
            return max(0, int(settings.sms_session_ttl_sec or 0))
        if normalized == "whatsapp":
            return max(0, int(settings.whatsapp_session_ttl_sec or 0))
        return max(0, int(settings.default_session_ttl_sec or 0))

    def _resolve_last_activity_at(self, *, conv, messages: list[Any]) -> Optional[datetime]:
        if messages:
            msg_ts = getattr(messages[-1], "created_at", None)
            if isinstance(msg_ts, datetime):
                return msg_ts
        conv_ts = getattr(conv, "created_at", None)
        if isinstance(conv_ts, datetime):
            return conv_ts
        return None

    def _is_session_ttl_expired(self, *, channel: str, conv, messages: list[Any]) -> bool:
        ttl_seconds = self._channel_session_ttl_seconds(channel)
        if ttl_seconds <= 0:
            return False
        last_activity_at = self._resolve_last_activity_at(conv=conv, messages=messages)
        if last_activity_at is None:
            return False
        now = datetime.now(last_activity_at.tzinfo) if last_activity_at.tzinfo else datetime.now(timezone.utc).replace(tzinfo=None)
        elapsed = (now - last_activity_at).total_seconds()
        return elapsed >= float(ttl_seconds)


def _split_name(raw_name: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    name = str(raw_name or "").strip()
    if not name:
        return None, None
    parts = name.split()
    if not parts:
        return None, None
    first = parts[0]
    last = " ".join(parts[1:]) if len(parts) > 1 else None
    return first, last


def _normalize_booking_date(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    m_num = _DATE_NUMERIC_SLOT_RE.search(raw)
    if m_num:
        day = int(m_num.group(1))
        month = int(m_num.group(2))
        year_raw = m_num.group(3)
        year = int(year_raw) if year_raw else datetime.now().year
        if year < 100:
            year += 2000
        try:
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            return None

    m_txt = _DATE_TEXT_SLOT_RE.search(raw)
    if not m_txt:
        # Retry on accent-normalized lower text for "février" / "décembre" variants
        lowered = (
            raw.lower()
            .replace("é", "e")
            .replace("è", "e")
            .replace("ê", "e")
            .replace("à", "a")
            .replace("â", "a")
            .replace("ô", "o")
            .replace("û", "u")
            .replace("ù", "u")
            .replace("î", "i")
            .replace("ï", "i")
            .replace("ç", "c")
        )
        m_txt = _DATE_TEXT_SLOT_RE.search(lowered)
    if not m_txt:
        return None
    day = int(m_txt.group(1))
    month_name = str(m_txt.group(2) or "").strip().lower()
    month_name = (
        month_name.replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("à", "a")
        .replace("â", "a")
        .replace("ô", "o")
        .replace("û", "u")
        .replace("ù", "u")
        .replace("î", "i")
        .replace("ï", "i")
        .replace("ç", "c")
    )
    month = _FR_MONTHS.get(month_name)
    if not month:
        return None
    year_raw = m_txt.group(3)
    year = int(year_raw) if year_raw else datetime.now().year
    if year < 100:
        year += 2000
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _normalize_booking_time(value: Any) -> Optional[str]:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    m = _TIME_SLOT_RE.search(raw)
    if not m:
        return None

    hour: Optional[int] = None
    minute: int = 0
    if m.group(4) is not None and m.group(5) is not None:
        hour = int(m.group(4))
        minute = int(m.group(5))
    else:
        hour = int(m.group(1))
        if m.group(2) is not None:
            minute = int(m.group(2))
        elif m.group(3) is not None:
            minute = int(m.group(3))
    if hour is None:
        return None
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def detect_language_safe(text: str) -> Optional[str]:
    # Local helper to avoid importing lang in a hot path on module import cycles.
    try:
        from .lang import detect_language

        lang = detect_language(text or "")
        return None if lang == "unknown" else lang
    except Exception:
        return None


def _slot_value_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _infer_missing_slots_for_observability(*, flow_state: str, slots: dict[str, Any]) -> list[str]:
    if flow_state not in _OBS_BOOKING_FLOWS:
        return []
    missing: list[str] = []
    if not _slot_value_present(slots.get("track_name")):
        missing.append("track_name")
    if not _slot_value_present(slots.get("full_name")):
        missing.append("full_name")
    has_contact = _slot_value_present(slots.get("email")) or _slot_value_present(slots.get("phone"))
    if not has_contact:
        missing.append("contact")
    if not _slot_value_present(slots.get("appointment_date")):
        missing.append("appointment_date")
    if not _slot_value_present(slots.get("appointment_time")):
        missing.append("appointment_time")
    return missing


def _fallback_reason_from_strategy(response_strategy: str) -> Optional[str]:
    strategy = str(response_strategy or "")
    if strategy == "unsupported_language":
        return "unsupported_language"
    if strategy.startswith("fallback_"):
        return strategy
    return None


def _handoff_trigger_from_context(*, response_strategy: str, llm_state: dict[str, Any]) -> Optional[str]:
    if response_strategy == "fallback_handoff" or bool(llm_state.get("handoff_allowed")):
        reason = str(llm_state.get("handoff_trigger_reason") or "")
        return reason or "fallback_handoff"
    return None


def _response_strategy_category(*, response_strategy: str, llm_rephrase_applied: bool, llm_extract_applied: bool) -> str:
    strategy = str(response_strategy or "")
    if strategy.startswith("fallback") or strategy == "unsupported_language":
        return "fallback"
    if strategy == "llm":
        return "llm"
    if llm_rephrase_applied:
        return "llm_rephrase"
    if llm_extract_applied:
        return "llm_extract"
    if strategy.startswith("deterministic"):
        return "deterministic"
    return strategy or "unknown"


__all__ = ["ChannelAgentPipeline", "ChannelTurnResult"]
