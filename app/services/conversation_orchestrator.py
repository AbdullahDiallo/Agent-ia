from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal, Optional

from sqlalchemy.orm import Session

from .lang import detect_language
from .llm_tools import handle_get_track_tuition


LangCode = Literal["fr", "en", "wo", "unknown"]
FlowName = Literal[
    "browsing_catalog",
    "track_selected",
    "booking_collect_contact",
    "booking_collect_datetime",
    "booking_confirm",
    "booking_submitted",
]
CATALOG_REPLY_LIMIT = 25


EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")
PHONE_RE = re.compile(r"(?:(?:\+|00)\d{6,15}|\b\d{8,15}\b)")
DATE_NUMERIC_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b")
DATE_TEXTUAL_RE = re.compile(
    r"\b(?:le\s+)?\d{1,2}\s+"
    r"(?:janvier|fevrier|février|mars|avril|mai|juin|juillet|aout|août|septembre|"
    r"octobre|novembre|decembre|décembre)\b(?:\s+\d{2,4})?",
    re.IGNORECASE,
)
TIME_RE = re.compile(r"\b\d{1,2}(?::\d{2})?\s*h(?:\s*\d{1,2})?\b|\b\d{1,2}:\d{2}\b", re.IGNORECASE)
NAME_AFTER_INTRO_RE = re.compile(
    r"\b(?:je m appelle|je suis|my name is|i am|maa ngi tudd)\s+([A-Za-zÀ-ÿ'’ -]{3,60})",
    re.IGNORECASE,
)
CAPITALIZED_NAME_RE = re.compile(r"\b[A-ZÀ-Ý][A-Za-zÀ-ÿ'’-]{1,}(?:\s+[A-ZÀ-Ý][A-Za-zÀ-ÿ'’-]{1,}){1,3}\b")
ADMISSION_LEVEL_RE = re.compile(r"\b(?:l[1-5]|m[12]|bac\s*\+?\s*\d+|bac)\b", re.IGNORECASE)


def _normalize_text(value: str) -> str:
    lowered = (value or "").lower().replace("’", "'")
    normalized = unicodedata.normalize("NFKD", lowered)
    ascii_like = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    ascii_like = re.sub(r"[^a-z0-9\s]", " ", ascii_like)
    return " ".join(ascii_like.split())


def _to_float(value: object) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _format_cfa(value: object) -> str:
    amount = int(round(_to_float(value)))
    return f"{amount:,}".replace(",", " ")


def _looks_like_level_label(value: str) -> bool:
    normalized = _normalize_text(value)
    if not normalized:
        return False
    if re.fullmatch(r"(?:l|m)\s*[1-5](?:\s*[,-]\s*(?:l|m)?\s*[1-5])*", normalized):
        return True
    if re.fullmatch(r"bac\s*\+?\s*\d+", normalized):
        return True
    return any(
        marker in normalized
        for marker in {
            "licence",
            "master",
            "ingenieur",
            "bachelor",
            "l1",
            "l2",
            "l3",
            "l4",
            "l5",
            "m1",
            "m2",
        }
    )


def _is_affirmative_message(normalized_message: str) -> bool:
    tokens = set((normalized_message or "").split())
    positives = {"oui", "ouais", "yes", "ok", "okay", "waaw", "waw"}
    negatives = {"non", "no", "nop", "deedet"}
    # Mixed yes/no payloads like "non oui" or "oui non" are treated as ambiguous.
    if tokens & positives and tokens & negatives:
        return False
    compact = normalized_message.replace(" ", "")
    if normalized_message in {
        "oui",
        "ouais",
        "yes",
        "ok",
        "okay",
        "d accord",
        "waaw",
        "waw",
    } or compact in {"daccord", "oks", "okok"}:
        return True
    token_list = normalized_message.split()
    if token_list and token_list[0] in positives:
        return True
    return any(
        marker in normalized_message
        for marker in {
            "vas y",
            "allez y",
            "go ahead",
            "reserve un rendez vous",
            "reserver un rendez vous",
            "prendre un rendez vous",
            "confirme",
            "confirm",
        }
    )


def _is_negative_message(normalized_message: str) -> bool:
    tokens = set((normalized_message or "").split())
    positives = {"oui", "ouais", "yes", "ok", "okay", "waaw", "waw"}
    negatives = {"non", "no", "nop", "deedet"}
    if tokens & positives and tokens & negatives:
        return False
    if normalized_message in negatives:
        return True
    return normalized_message.startswith("non ")


def _extract_contact_signals(raw_message: str) -> tuple[Optional[str], Optional[str]]:
    text = raw_message or ""
    email_match = EMAIL_RE.search(text)
    phone_match = PHONE_RE.search(text)
    email = email_match.group(0) if email_match else None
    phone = phone_match.group(0) if phone_match else None
    if phone:
        phone = phone.replace(" ", "")
    return email, phone


def _contains_contact_info(raw_message: str) -> bool:
    email, phone = _extract_contact_signals(raw_message)
    return bool(email or phone)


def _extract_datetime_signals(raw_message: str) -> tuple[Optional[str], Optional[str]]:
    text = raw_message or ""
    normalized = _normalize_text(text)
    date_match = DATE_NUMERIC_RE.search(text) or DATE_TEXTUAL_RE.search(text) or DATE_TEXTUAL_RE.search(normalized)
    time_match = TIME_RE.search(text) or TIME_RE.search(normalized)
    date_value = date_match.group(0) if date_match else None
    time_value = time_match.group(0) if time_match else None
    return date_value, time_value


def _contains_datetime_info(raw_message: str) -> bool:
    date_value, time_value = _extract_datetime_signals(raw_message)
    return bool(date_value and time_value)


def _looks_like_name_only_message(raw_message: str) -> bool:
    normalized = _normalize_text(raw_message)
    if not normalized:
        return False
    if (
        _is_affirmative_message(normalized)
        or _is_negative_message(normalized)
        or _is_gratitude_or_closure_request(normalized)
    ):
        return False
    if "?" in (raw_message or ""):
        return False
    if _contains_contact_info(raw_message) or _contains_datetime_info(raw_message):
        return False
    tokens = normalized.split()
    if len(tokens) < 2 or len(tokens) > 4:
        return False
    blocked = {
        "oui",
        "non",
        "rendez",
        "vous",
        "rdv",
        "filiere",
        "programme",
        "detail",
        "details",
        "info",
        "infos",
        "mail",
        "numero",
        "telephone",
        "phone",
        "tu",
        "quoi",
        "raconte",
        "comment",
        "pourquoi",
        "quand",
        "precise",
        "preciser",
    }
    if any(t in blocked for t in tokens):
        return False
    return all(t.isalpha() and len(t) >= 2 for t in tokens)


def _extract_name(raw_message: str) -> Optional[str]:
    text = (raw_message or "").strip()
    if not text:
        return None
    if _looks_like_name_only_message(text):
        return " ".join(part for part in text.replace(",", " ").split() if part).strip()
    intro = NAME_AFTER_INTRO_RE.search(text)
    if intro:
        candidate = re.split(r"\b(?:email|mail|telephone|t[eé]l[eé]phone|phone|numero|num[eé]ro|le\s+\d{1,2}[/-])\b", intro.group(1), 1, flags=re.IGNORECASE)[0]
        candidate = " ".join(candidate.replace(",", " ").split()).strip(" -")
        if candidate and len(candidate.split()) <= 5:
            return candidate
    if (_contains_contact_info(text) or _contains_datetime_info(text)) and CAPITALIZED_NAME_RE.search(text):
        return CAPITALIZED_NAME_RE.search(text).group(0).strip()
    return None


def _extract_admission_level(raw_message: str) -> Optional[str]:
    match = ADMISSION_LEVEL_RE.search(raw_message or "")
    return match.group(0).strip() if match else None


def _is_catalog_request(normalized_message: str) -> bool:
    # Questions de type "quels programmes me conseilles-tu ?" doivent rester
    # ouvertes pour le LLM (recommandation/orientation), pas repasser par
    # un simple listing déterministe.
    advise_markers = {
        "conseilles",
        "me conseilles",
        "tu me conseilles",
        "conseiller",
        "recommande",
        "me recommandes",
        "orienter",
        "orientation",
        "que me proposes tu",
        "que me proposes-tu",
        "que proposes tu",
        "meilleur",
        "meilleure",
        "mieux",
        "best",
        "which is better",
        "which one is better",
    }
    if any(m in normalized_message for m in advise_markers):
        return False
    markers = {
        "filiere",
        "filieres",
        "filliere",
        "fillieres",
        "programme",
        "programmes",
        "program",
        "programs",
        "track",
        "tracks",
        "disponible",
        "disponibles",
        "available",
        "catalogue",
        "catalog",
        "program yi",
        "filiere yi",
        "yan program",
        "yan filiere",
        "am na program",
        "wone ma filiere",
    }
    return any(marker in normalized_message for marker in markers)


def _catalog_subject(normalized_message: str) -> Optional[str]:
    has_track = any(marker in normalized_message for marker in {"filiere", "filieres", "track", "tracks"})
    has_program = any(marker in normalized_message for marker in {"programme", "programmes", "program", "programs"})
    if has_program and has_track:
        return "grouped"
    if has_program and not has_track:
        return "program"
    if has_track and not has_program:
        return "track"
    return None


def _is_details_request(normalized_message: str) -> bool:
    markers = {
        "detail",
        "details",
        "infos",
        "information",
        "informations",
        "plus",
        "renseignement",
        "tuition",
        "frais",
        "cout",
        "prix",
    }
    return any(marker in normalized_message for marker in markers)


def _is_recommendation_request(normalized_message: str) -> bool:
    markers = {
        "conseille",
        "conseilles",
        "conseiller",
        "recommande",
        "recommandes",
        "recommandation",
        "orienter",
        "orientation",
        "meilleur",
        "meilleure",
        "mieux",
        "best",
        "which is better",
        "which one is better",
        "quel est le meilleur",
        "quelle est la meilleure",
        "lequel est mieux",
        "laquelle est mieux",
        "which program is best",
        "what is the best program",
    }
    return any(marker in normalized_message for marker in markers)


def _is_appointment_request(normalized_message: str) -> bool:
    markers = {
        "rendez vous",
        "rdv",
        "appointment",
        "meeting",
        "creneau",
        "slot",
        "disponibilite",
        "disponibilites",
    }
    return any(marker in normalized_message for marker in markers)


def _is_booking_restart_request(normalized_message: str) -> bool:
    markers = {
        "prendre un autre rendez vous",
        "prendre un autre rdv",
        "un autre rendez vous",
        "un autre rdv",
        "nouveau rendez vous",
        "nouveau rdv",
        "autre rendez vous",
        "autre rdv",
        "modifier mon rendez vous",
        "modifier mon rdv",
        "changer mon rendez vous",
        "changer mon rdv",
        "decaler mon rendez vous",
        "reprogrammer mon rendez vous",
        "book another appointment",
        "another appointment",
        "new appointment",
        "reschedule my appointment",
        "modify my appointment",
        "beneen rendez vous",
        "beneen rdv",
    }
    return any(marker in normalized_message for marker in markers)


def _is_confirmation_followup_request(normalized_message: str) -> bool:
    markers = {
        "confirmation",
        "confirmer",
        "confirm",
        "confirme",
        "email",
        "mail",
        "recevoir",
        "recois",
        "recoit",
        "puis je recevoir",
        "can i receive",
        "send me a confirmation",
    }
    return any(marker in normalized_message for marker in markers)


def _is_gratitude_or_closure_request(normalized_message: str) -> bool:
    return any(
        marker in normalized_message
        for marker in {
            "merci",
            "merci beaucoup",
            "ok merci",
            "d accord merci",
            "thanks",
            "thank you",
            "ok thanks",
            "great thanks",
            "jerejef",
            "dieureudieuf",
            "c est bon",
            "cest bon",
            "sebon",
            "a plus",
            "a bientot",
            "au revoir",
            "bye",
            "goodbye",
            "see you",
        }
    )


def _is_urgency_request(normalized_message: str) -> bool:
    return any(
        marker in normalized_message
        for marker in {
            "plus tot possible",
            "plutot possible",
            "au plus vite",
            "rapidement",
            "urgent",
            "asap",
        }
    )


def _is_human_request(normalized_message: str) -> bool:
    return any(
        marker in normalized_message
        for marker in {
            "parler a un humain",
            "parler a un agent",
            "parler a un conseiller",
            "transfere moi",
            "transfer me",
            "human agent",
            "human advisor",
            "agent humain",
            "conseiller humain",
            "un humain",
            "a human",
        }
    )


def _default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "language_locked": None,
        "active_flow": "browsing_catalog",
        "participant_key": None,
        "channel_thread_key": None,
        "summary_memory": None,
        "slots_json": {},
        "response_strategy": "init",
        "failure_count": 0,
        "structured_extraction_fail_count": 0,
        "fallback_stage": None,
        "clarification_success": False,
        "handoff_allowed": False,
        "handoff_trigger_reason": None,
        "appointment_locked": False,
        "short_text_flag": False,
        "session_ttl_expired": False,
        "reset_reason": None,
    }


def parse_conversation_state(raw: Optional[str]) -> dict[str, Any]:
    state = _default_state()
    if not raw:
        return state
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            state.update(payload)
    except Exception:
        return state
    if not isinstance(state.get("slots_json"), dict):
        state["slots_json"] = {}
    if state.get("active_flow") not in {
        "browsing_catalog",
        "track_selected",
        "booking_collect_contact",
        "booking_collect_datetime",
        "booking_confirm",
        "booking_submitted",
    }:
        state["active_flow"] = "browsing_catalog"
    state["participant_key"] = str(state.get("participant_key") or "").strip() or None
    state["channel_thread_key"] = str(state.get("channel_thread_key") or "").strip() or None
    summary_memory = str(state.get("summary_memory") or "").strip()
    state["summary_memory"] = summary_memory[:500] if summary_memory else None
    state["structured_extraction_fail_count"] = max(0, int(state.get("structured_extraction_fail_count") or 0))
    slots = state.get("slots_json") if isinstance(state.get("slots_json"), dict) else {}
    state["appointment_locked"] = bool(state.get("appointment_locked")) or bool(
        str(slots.get("appointment_id") or "").strip()
        or str(slots.get("appointment_status") or "").strip()
    )
    state["short_text_flag"] = bool(state.get("short_text_flag"))
    state["session_ttl_expired"] = bool(state.get("session_ttl_expired"))
    state["reset_reason"] = str(state.get("reset_reason") or "") or None
    return state


def dump_conversation_state(state: dict[str, Any]) -> str:
    serializable = {
        "version": int(state.get("version") or 1),
        "language_locked": state.get("language_locked"),
        "active_flow": state.get("active_flow") or "browsing_catalog",
        "participant_key": str(state.get("participant_key") or "").strip() or None,
        "channel_thread_key": str(state.get("channel_thread_key") or "").strip() or None,
        "summary_memory": str(state.get("summary_memory") or "").strip()[:500] or None,
        "slots_json": state.get("slots_json") if isinstance(state.get("slots_json"), dict) else {},
        "response_strategy": state.get("response_strategy"),
        "failure_count": max(0, int(state.get("failure_count") or 0)),
        "structured_extraction_fail_count": max(0, int(state.get("structured_extraction_fail_count") or 0)),
        "fallback_stage": state.get("fallback_stage"),
        "clarification_success": bool(state.get("clarification_success")),
        "handoff_allowed": bool(state.get("handoff_allowed")),
        "handoff_trigger_reason": state.get("handoff_trigger_reason"),
        "appointment_locked": bool(state.get("appointment_locked")),
        "short_text_flag": bool(state.get("short_text_flag")),
        "session_ttl_expired": bool(state.get("session_ttl_expired")),
        "reset_reason": state.get("reset_reason"),
    }
    return json.dumps(serializable, ensure_ascii=True, separators=(",", ":"))


@dataclass
class OrchestratorTurn:
    reply: Optional[str]
    state: dict[str, Any]
    lang: LangCode
    response_strategy: str
    use_llm: bool = False
    inferred_intent: Optional[str] = None


class ConversationOrchestrator:
    def __init__(self, db: Session, *, track_search_fn=handle_get_track_tuition):
        self.db = db
        self._track_search_fn = track_search_fn

    def process_message(
        self,
        *,
        message: str,
        history_user_messages: list[str],
        state: Optional[dict[str, Any]],
        llm_entities: Optional[dict[str, Any]] = None,
    ) -> OrchestratorTurn:
        conv_state = parse_conversation_state(dump_conversation_state(state or _default_state()))
        conv_state["reset_reason"] = None
        normalized = _normalize_text(message)
        short_text_flag = self._is_short_text_message(message)
        conv_state["short_text_flag"] = short_text_flag
        lang = self._resolve_language(
            message=message,
            normalized=normalized,
            history_user_messages=history_user_messages,
            state=conv_state,
        )
        conv_state["language_locked"] = lang if lang != "unknown" else conv_state.get("language_locked")

        flow_escape_reason = self._detect_flow_escape_reason(normalized)
        if flow_escape_reason:
            self._apply_controlled_partial_reset(conv_state, reason=flow_escape_reason)
            reply = self._render_flow_escape_reply(lang=lang, reason=flow_escape_reason)
            return self._reply(
                conv_state,
                "deterministic_flow_escape",
                reply,
                lang,
                intent="flow_escape",
            )

        if _is_human_request(normalized):
            fallback_lang = self._fallback_reply_language(
                lang=lang,
                state=conv_state,
                history_user_messages=history_user_messages,
            )
            return self._reply_progressive_fallback(
                conv_state,
                lang=fallback_lang,
                intent="human_request",
                reason="human_request",
            )

        if not normalized.strip():
            fallback_lang = self._fallback_reply_language(
                lang=lang,
                state=conv_state,
                history_user_messages=history_user_messages,
            )
            return self._reply_progressive_fallback(
                conv_state,
                lang=fallback_lang,
                intent="clarification_needed",
                reason="empty_or_noise_input",
            )

        if lang == "unknown":
            fallback_lang = self._fallback_reply_language(
                lang=lang,
                state=conv_state,
                history_user_messages=history_user_messages,
            )
            return self._reply_progressive_fallback(
                conv_state,
                lang=fallback_lang,
                intent="clarification_needed",
                reason="understanding_failed",
            )

        entities = self._extract_entities(message)
        entities = self._merge_llm_entities(entities, llm_entities)
        if bool(entities.get("booking_restart_request")):
            self._prepare_booking_restart(conv_state)
        direct_items = self._dedupe_track_items(self._query_track_items(message), limit=CATALOG_REPLY_LIMIT)
        if not direct_items and isinstance(entities.get("track_name"), str):
            direct_items = self._dedupe_track_items(self._query_track_items(str(entities["track_name"])), limit=8)
        if not direct_items and isinstance(entities.get("program_name"), str):
            direct_items = self._dedupe_track_items(self._query_track_items(str(entities["program_name"])), limit=8)
        history_track = self._find_history_track_item(history_user_messages)
        selected_track = self._resolve_pending_track_candidate(state=conv_state, normalized=normalized)
        selected_from_pending = selected_track is not None
        _selected_from_history = False
        if not selected_track:
            selected_track = self._resolve_selected_track(
                message=message,
                direct_items=direct_items,
                history_track=history_track,
                allow_history_fallback=not bool(
                    entities.get("catalog_request")
                    or entities.get("recommendation_request")
                    or entities.get("gratitude_closure")
                ),
            )
            selected_from_pending = False
            # Detect if the selection came from history fallback (not explicit in message)
            if selected_track and selected_track is history_track:
                explicit = [item for item in direct_items if self._message_mentions_track(item, message)]
                if not explicit and len(direct_items) != 1:
                    _selected_from_history = True
        if selected_track:
            # Don't overwrite existing track slots with a history fallback —
            # this prevents "connaitre les frais" from resetting the current
            # track to a previously discussed one.
            existing_track = str(self._current_track_slots(conv_state).get("track_name") or "").strip()
            if _selected_from_history and existing_track:
                pass  # keep current slots
            else:
                self._apply_track_slot(conv_state, selected_track)

        current_flow = str(conv_state.get("active_flow") or "browsing_catalog")
        if current_flow not in {
            "browsing_catalog",
            "track_selected",
            "booking_collect_contact",
            "booking_collect_datetime",
            "booking_confirm",
            "booking_submitted",
        }:
            current_flow = "browsing_catalog"
            conv_state["active_flow"] = current_flow

        if current_flow == "booking_submitted":
            post_submit = self._handle_post_booking_submitted_followup(
                state=conv_state,
                lang=lang,
                normalized=normalized,
                entities=entities,
                direct_items=direct_items,
            )
            if post_submit is not None:
                return post_submit

        if self._should_interrupt_booking_flow_for_topic_switch(
            state=conv_state,
            entities=entities,
            direct_items=direct_items,
        ):
            if entities.get("catalog_subject") == "program" or entities.get("catalog_request") or entities.get("recommendation_request"):
                conv_state["active_flow"] = "browsing_catalog"
            elif str(self._current_track_slots(conv_state).get("track_name") or "").strip():
                conv_state["active_flow"] = "track_selected"
            else:
                conv_state["active_flow"] = "browsing_catalog"

        if selected_from_pending:
            conv_state["active_flow"] = "track_selected"
            return self._reply(
                conv_state,
                "deterministic_track_details",
                self._render_track_details_reply(selected_track, lang=lang, ask_rdv=False),
                lang,
                intent="track_details",
            )

        if self._should_enter_booking_flow(conv_state, normalized, entities):
            return self._handle_booking_flow(
                state=conv_state,
                lang=lang,
                message=message,
                normalized=normalized,
                entities=entities,
                direct_items=direct_items,
            )

        # Contextual follow-up questions should go to LLM for proper reasoning
        if self._is_contextual_followup(normalized, history_user_messages):
            conv_state["pending_open_intent"] = "contextual_followup"
            return self._reply(conv_state, "llm_pending", None, lang, use_llm=True, intent="contextual_followup")

        deterministic = self._handle_catalog_and_track(
            state=conv_state,
            lang=lang,
            message=message,
            normalized=normalized,
            direct_items=direct_items,
            entities=entities,
        )
        if deterministic is not None:
            return deterministic

        # No deterministic route: keep state but delegate response to LLM.
        llm_intent = "recommendation_request" if entities.get("recommendation_request") else "open_query"
        return self._reply(conv_state, "llm_pending", None, lang, use_llm=True, intent=llm_intent)

    def contextual_fallback_reply(self, *, state: dict[str, Any], lang: LangCode) -> str:
        flow = str(state.get("active_flow") or "browsing_catalog")
        slots = state.get("slots_json") if isinstance(state.get("slots_json"), dict) else {}
        missing_name, missing_contact, missing_datetime = self._missing_booking_fields(slots)

        if flow in {"booking_collect_contact", "booking_collect_datetime", "booking_confirm"}:
            base = self._render_booking_missing_fields_prompt(
                lang=lang,
                missing_name=missing_name,
                missing_contact=missing_contact,
                missing_datetime=missing_datetime,
            )
            if lang == "en":
                return "We are experiencing a temporary technical issue. " + base
            if lang == "wo":
                return "Amna jafe-jafe technique bu gàtt. " + base
            return "Je rencontre un souci technique temporaire. " + base

        if str(state.get("pending_open_intent") or "").strip() == "recommendation_request":
            recommendation_reply = self._render_recommendation_fallback_reply(state=state, lang=lang)
            if recommendation_reply:
                return recommendation_reply

        if flow == "track_selected":
            if lang == "en":
                return "We are experiencing a temporary technical issue. I can still help with track details, tuition, or booking an admissions appointment."
            if lang == "wo":
                return "Amna jafe-jafe technique bu gàtt. Mën naa la dimbali ci xibaaru filiere bi walla rendez-vous admission."
            return "Je rencontre un souci technique temporaire. Je peux quand meme vous aider sur les details de la filiere, les frais ou un rendez-vous admission."

        if self._has_recorded_booking(state):
            if lang == "en":
                return "We are experiencing a temporary technical issue. Your appointment request remains recorded. You can ask a new question about programs, tuition, or admission."
            if lang == "wo":
                return "Amna jafe-jafe technique bu gàtt. Sa demande rendez-vous bi am na ba noppi. Mën nga laaj beneen laaj ci programmes, frais, walla admission."
            return "Je rencontre un souci technique temporaire. Votre demande de rendez-vous reste enregistrée. Vous pouvez poser une nouvelle question sur les programmes, les frais ou l'admission."

        if lang == "en":
            return "We are experiencing a temporary technical issue. Could you share your target program, admission level, and preferred contact? I can also propose an admissions appointment quickly."
        if lang == "wo":
            return "Amna jafe-jafe technique bu gàtt. Wax ma program/filiere bi nga bëgg, niveau bi ak sa kontak. Mën naa la jox rendez-vous admission bu gaaw."
        return "Je rencontre un souci technique temporaire. Pouvez-vous preciser la filiere visee, le niveau d'admission et votre contact ? Je peux aussi proposer un rendez-vous admission rapidement."

    def normalize_state_for_provider_fallback(self, state: Optional[dict[str, Any]]) -> dict[str, Any]:
        conv_state = parse_conversation_state(dump_conversation_state(state or _default_state()))
        flow = str(conv_state.get("active_flow") or "browsing_catalog")
        if self._is_appointment_locked(conv_state) or flow not in {"booking_collect_contact", "booking_collect_datetime", "booking_confirm"}:
            conv_state["active_flow"] = "browsing_catalog"
        conv_state["handoff_allowed"] = False
        conv_state["handoff_trigger_reason"] = None
        return conv_state

    def _fallback_reply_language(
        self,
        *,
        lang: LangCode,
        state: dict[str, Any],
        history_user_messages: list[str],
    ) -> LangCode:
        if lang in {"fr", "en", "wo"}:
            return lang
        locked = state.get("language_locked")
        if locked in {"fr", "en", "wo"}:
            return locked  # type: ignore[return-value]
        history_lang = self._infer_language_from_history(history_user_messages)
        if history_lang in {"fr", "en", "wo"}:
            return history_lang
        return "fr"

    def _reply_progressive_fallback(
        self,
        state: dict[str, Any],
        *,
        lang: LangCode,
        intent: str,
        reason: str,
    ) -> OrchestratorTurn:
        current_count = max(0, int(state.get("failure_count") or 0))

        if intent == "human_request":
            state["failure_count"] = max(1, current_count)
            state["fallback_stage"] = "handoff"
            state["handoff_allowed"] = True
            state["handoff_trigger_reason"] = "human_request"
            reply = self._render_progressive_fallback_reply(stage="handoff", lang=lang, human_request=True)
            return self._reply(state, "fallback_handoff", reply, lang, intent="human_request")

        next_count = min(3, current_count + 1)
        state["failure_count"] = next_count
        if next_count == 1:
            stage = "clarify"
            strategy = "fallback_clarify"
        elif next_count == 2:
            stage = "guided"
            strategy = "fallback_guided"
        else:
            stage = "handoff"
            strategy = "fallback_handoff"

        state["fallback_stage"] = stage
        state["handoff_allowed"] = stage == "handoff"
        state["handoff_trigger_reason"] = "failure_count_threshold" if stage == "handoff" else reason
        reply = self._render_progressive_fallback_reply(stage=stage, lang=lang, human_request=False)
        return self._reply(state, strategy, reply, lang, intent=intent)

    def _render_progressive_fallback_reply(
        self,
        *,
        stage: str,
        lang: LangCode,
        human_request: bool,
    ) -> str:
        if lang == "en":
            if human_request or stage == "handoff":
                return "I will transfer you to a human admissions advisor now."
            if stage == "guided":
                return "Please say one keyword: tuition, admission, appointment, or programs."
            return "I did not understand well. Are you asking about tuition, schedule, or admission?"
        if lang == "wo":
            if human_request or stage == "handoff":
                return "Dinaa la jëflante ak ab conseiller admissions bu nit léegi."
            if stage == "guided":
                return "Waxal benn baat rekk: frais, admission, rendez-vous walla programmes."
            return "Ma déggul bu baax. Ndax yaa ngi laaj ci frais, horaires walla inscription?"
        if human_request or stage == "handoff":
            return "Je vais vous transférer vers un conseiller admissions humain maintenant."
        if stage == "guided":
            return "Dites simplement : frais, admission, rendez-vous ou programmes."
        return "Je n'ai pas bien compris. Parlez-vous des frais, des horaires ou d'une inscription ?"

    def _resolve_language(
        self,
        *,
        message: str,
        normalized: str,
        history_user_messages: list[str],
        state: dict[str, Any],
    ) -> LangCode:
        locked = state.get("language_locked")
        if self._is_short_text_message(message):
            if locked in {"fr", "en", "wo"}:
                return locked  # type: ignore[return-value]
            history_lang = self._infer_language_from_history(history_user_messages)
            if history_lang in {"fr", "en", "wo"}:
                return history_lang
            return "fr"

        if locked in {"fr", "en", "wo"} and self._is_low_signal_language_message(message, normalized):
            return locked  # type: ignore[return-value]

        guessed = detect_language(message)
        if locked in {"fr", "en", "wo"} and guessed == "unknown":
            return locked  # type: ignore[return-value]

        if guessed == "unknown":
            history_lang = self._infer_language_from_history(history_user_messages)
            if history_lang and (
                self._is_low_signal_language_message(message, normalized)
                or self._is_booking_context_message(normalized)
            ):
                return history_lang  # type: ignore[return-value]
            return "unknown"

        if locked in {"fr", "en", "wo"} and self._is_booking_context_message(normalized):
            # Avoid flip-flopping in booking workflow on short mixed/date messages.
            return locked  # type: ignore[return-value]
        return guessed

    def _merge_llm_entities(self, regex_entities: dict[str, Any], llm_entities: Optional[dict[str, Any]]) -> dict[str, Any]:
        merged = dict(regex_entities)
        if not isinstance(llm_entities, dict):
            return merged
        # Booleans: only enrich when local heuristics are false/absent.
        for key in (
            "is_affirmative",
            "is_negative",
            "catalog_request",
            "recommendation_request",
            "details_request",
            "appointment_request",
            "urgency_request",
            "gratitude_closure",
            "booking_restart_request",
        ):
            if not bool(merged.get(key)) and isinstance(llm_entities.get(key), bool):
                merged[key] = llm_entities[key]
        # Strings/entities: local regex wins when present; LLM fills gaps.
        for key in (
            "full_name",
            "email",
            "phone",
            "appointment_date",
            "appointment_time",
            "admission_level",
            "track_name",
            "program_name",
            "catalog_subject",
        ):
            current = str(merged.get(key) or "").strip()
            candidate = llm_entities.get(key)
            if not current and isinstance(candidate, str) and candidate.strip():
                if key == "catalog_subject" and candidate.strip() not in {"program", "track", "grouped"}:
                    continue
                merged[key] = candidate.strip()
        return merged

    def _is_low_signal_language_message(self, raw_message: str, normalized: str) -> bool:
        token_count = len(normalized.split())
        return (
            token_count <= 8
            and (
                _is_affirmative_message(normalized)
                or _is_negative_message(normalized)
                or _looks_like_name_only_message(raw_message)
                or _contains_contact_info(raw_message)
                or _contains_datetime_info(raw_message)
            )
        )

    def _is_short_text_message(self, raw_message: str) -> bool:
        return len(str(raw_message or "").strip()) < 12

    @staticmethod
    def _is_contextual_followup(normalized: str, history_user_messages: list[str]) -> bool:
        """Detect if the message is a contextual follow-up that needs LLM reasoning.

        These are messages that reference previous conversation context and cannot
        be handled by deterministic keyword matching alone.
        """
        if not history_user_messages:
            return False
        contextual_markers = {
            "et pour", "et l autre", "et l'autre", "l autre option", "l'autre option",
            "et celui", "et celle", "et les autres", "compare", "comparer",
            "la difference", "la différence", "difference entre", "différence entre",
            "lequel", "laquelle", "lesquels", "lesquelles",
            "c est quoi", "c'est quoi", "qu est ce", "qu'est-ce",
            "tu m as dit", "tu m'as dit", "comme tu as dit", "tu as mentionne",
            "en fait", "finalement", "plutot", "plutôt", "au lieu de",
            "par rapport a", "par rapport à", "versus", "vs",
            "et si", "mais si", "sinon",
            "plus de details", "plus de détails", "approfondir", "developper", "développer",
            "explique moi", "explique-moi", "peux tu preciser", "peux-tu préciser",
            "en resume", "en résumé", "recapitule", "récapitule",
            "what about", "and the other", "compared to", "tell me more",
            "can you explain", "what is the difference", "which one",
        }
        return any(marker in normalized for marker in contextual_markers)

    def _detect_flow_escape_reason(self, normalized: str) -> Optional[str]:
        if not normalized:
            return None
        if normalized in {"menu", "menu principal", "main menu"}:
            return "flow_escape_menu"
        if normalized in {"annuler", "annule", "cancel"}:
            return "flow_escape_cancel"
        if normalized in {"nouvelle question", "new question", "autre question"}:
            return "flow_escape_new_question"
        return None

    def apply_controlled_partial_reset(self, state: Optional[dict[str, Any]], *, reason: str) -> dict[str, Any]:
        conv_state = parse_conversation_state(dump_conversation_state(state or _default_state()))
        self._apply_controlled_partial_reset(conv_state, reason=reason)
        return conv_state

    def _apply_controlled_partial_reset(self, state: dict[str, Any], *, reason: str) -> None:
        slots = state.get("slots_json") if isinstance(state.get("slots_json"), dict) else {}
        thread_key = str(slots.get("thread_key") or "").strip()
        state["slots_json"] = {"thread_key": thread_key} if thread_key else {}
        state["active_flow"] = "browsing_catalog"
        state["failure_count"] = 0
        state["fallback_stage"] = None
        state["clarification_success"] = False
        state["handoff_allowed"] = False
        state["handoff_trigger_reason"] = None
        state["reset_reason"] = reason

    def _render_flow_escape_reply(self, *, lang: LangCode, reason: str) -> str:
        if lang == "en":
            if reason == "flow_escape_menu":
                return "Main menu restored. You can ask about programs, tuition, admission, or appointments."
            if reason == "flow_escape_cancel":
                return "Done, current flow cancelled. Ask a new question any time."
            return "Understood. Starting a new question flow now."
        if lang == "wo":
            if reason == "flow_escape_menu":
                return "Menu bi dellusi na. Mën nga laaj ci programmes, frais, admission walla rendez-vous."
            if reason == "flow_escape_cancel":
                return "Baax na, ma taxawalul flow bi. Mën nga laaj beneen laaj saa su ne."
            return "Degg naa. Dinaa tambali beneen laaj léegi."
        if reason == "flow_escape_menu":
            return "Menu réinitialisé. Vous pouvez demander les programmes, les frais, l'admission ou un rendez-vous."
        if reason == "flow_escape_cancel":
            return "C'est noté, j'annule le flux en cours. Vous pouvez poser une nouvelle question."
        return "D'accord, je passe sur une nouvelle question."

    def _is_booking_context_message(self, normalized: str) -> bool:
        return _is_appointment_request(normalized) or _is_affirmative_message(normalized) or _is_negative_message(normalized)

    def _infer_language_from_history(self, history_user_messages: list[str]) -> Optional[LangCode]:
        for text in reversed(history_user_messages):
            normalized = _normalize_text(text)
            low_signal_payload = self._is_low_signal_language_message(text, normalized) and not any(
                marker in normalized
                for marker in {"filiere", "programme", "program", "admission", "rendez vous", "rdv", "frais", "detail", "details"}
            )
            if low_signal_payload:
                continue
            guessed = detect_language(text)
            if guessed != "unknown":
                return guessed
        return None

    def _extract_entities(self, raw_message: str) -> dict[str, Any]:
        normalized = _normalize_text(raw_message)
        email, phone = _extract_contact_signals(raw_message)
        date_value, time_value = _extract_datetime_signals(raw_message)
        return {
            "normalized": normalized,
            "is_affirmative": _is_affirmative_message(normalized),
            "is_negative": _is_negative_message(normalized),
            "gratitude_closure": _is_gratitude_or_closure_request(normalized),
            "catalog_request": _is_catalog_request(normalized),
            "recommendation_request": _is_recommendation_request(normalized),
            "catalog_subject": _catalog_subject(normalized),
            "details_request": _is_details_request(normalized),
            "appointment_request": _is_appointment_request(normalized),
            "booking_restart_request": _is_booking_restart_request(normalized),
            "urgency_request": _is_urgency_request(normalized),
            "email": email,
            "phone": phone,
            "full_name": _extract_name(raw_message),
            "appointment_date": date_value,
            "appointment_time": time_value,
            "admission_level": _extract_admission_level(raw_message),
            "raw_message": raw_message,
        }

    def _query_track_items(self, query_text: str) -> list[dict[str, Any]]:
        result = self._track_search_fn(self.db, {"query": query_text})
        if not result.get("success"):
            return []
        raw_items = result.get("items") or []
        return [item for item in raw_items if isinstance(item, dict)]

    def _dedupe_track_items(self, items: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            key = str(item.get("track_id") or f"{item.get('track_name')}::{item.get('program_name')}")
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
            if len(deduped) >= limit:
                break
        return deduped

    def _message_mentions_track(self, item: dict[str, Any], message: str) -> bool:
        track_name = _normalize_text(str(item.get("track_name") or ""))
        return bool(track_name and track_name in _normalize_text(message))

    def _find_history_track_item(self, user_messages: list[str]) -> Optional[dict[str, Any]]:
        for text in reversed(user_messages):
            if not (text or "").strip():
                continue
            items = self._dedupe_track_items(self._query_track_items(text), limit=4)
            if not items:
                continue
            explicit = [item for item in items if self._message_mentions_track(item, text)]
            if explicit:
                return explicit[0]
            if len(items) == 1:
                return items[0]
        return None

    def _resolve_selected_track(
        self,
        *,
        message: str,
        direct_items: list[dict[str, Any]],
        history_track: Optional[dict[str, Any]],
        allow_history_fallback: bool = True,
    ) -> Optional[dict[str, Any]]:
        explicit = [item for item in direct_items if self._message_mentions_track(item, message)]
        if explicit:
            return explicit[0]
        if len(direct_items) == 1:
            return direct_items[0]
        if allow_history_fallback:
            return history_track
        return None

    def _apply_track_slot(self, state: dict[str, Any], item: dict[str, Any]) -> None:
        slots = state.setdefault("slots_json", {})
        if not isinstance(slots, dict):
            slots = {}
            state["slots_json"] = slots
        slots["track_id"] = str(item.get("track_id") or slots.get("track_id") or "")
        slots["track_name"] = str(item.get("track_name") or slots.get("track_name") or "").strip() or slots.get("track_name")
        program_name = str(item.get("program_name") or "").strip()
        if program_name:
            slots["program_name"] = program_name
        slots.pop("pending_track_options", None)
        for field in (
            "annual_fee",
            "registration_fee",
            "monthly_fee",
            "access_level",
            "delivery_mode",
            "certifications",
        ):
            if item.get(field) is not None:
                slots[field] = item.get(field)

    def _should_enter_booking_flow(self, state: dict[str, Any], normalized: str, entities: dict[str, Any]) -> bool:
        flow = str(state.get("active_flow") or "browsing_catalog")
        if flow in {"booking_collect_contact", "booking_collect_datetime", "booking_confirm"}:
            return True
        if self._is_appointment_locked(state) and not bool(entities.get("booking_restart_request")):
            return False
        if entities.get("gratitude_closure") or _is_confirmation_followup_request(normalized):
            return False
        if entities.get("details_request") or entities.get("catalog_request") or entities.get("recommendation_request"):
            # If the user asks for details + appointment in the same turn, answer details first,
            # then transition into booking on the next turn/confirmation.
            return False
        if entities.get("appointment_request"):
            return bool(self._current_track_slots(state).get("track_name"))
        if entities.get("is_affirmative"):
            return bool(self._current_track_slots(state).get("track_name"))
        return False

    def _should_interrupt_booking_flow_for_topic_switch(
        self,
        *,
        state: dict[str, Any],
        entities: dict[str, Any],
        direct_items: list[dict[str, Any]],
    ) -> bool:
        flow = str(state.get("active_flow") or "browsing_catalog")
        if flow not in {"booking_collect_contact", "booking_collect_datetime", "booking_confirm"}:
            return False
        if entities.get("appointment_request"):
            return False
        if entities.get("full_name") or entities.get("email") or entities.get("phone"):
            return False
        if entities.get("appointment_date") or entities.get("appointment_time"):
            return False
        if entities.get("is_negative"):
            return False
        explicit_non_booking_intent = bool(
            entities.get("catalog_request")
            or entities.get("details_request")
            or entities.get("recommendation_request")
            or direct_items
        )
        if not explicit_non_booking_intent:
            return False
        # "oui"/"ok" alone should continue booking, but mixed messages like
        # "ok ... quel est le meilleur programme ?" should switch topic.
        return True

    def _current_track_slots(self, state: dict[str, Any]) -> dict[str, Any]:
        slots = state.get("slots_json")
        if not isinstance(slots, dict):
            return {}
        return slots

    def _merge_slots_from_entities(self, state: dict[str, Any], entities: dict[str, Any]) -> None:
        slots = state.setdefault("slots_json", {})
        if not isinstance(slots, dict):
            slots = {}
            state["slots_json"] = slots

        for key in ("email", "phone", "appointment_date", "appointment_time", "admission_level"):
            value = entities.get(key)
            if value:
                slots[key] = str(value).strip()
        full_name = entities.get("full_name")
        if full_name:
            slots["full_name"] = str(full_name).strip()
        if entities.get("urgency_request"):
            slots["urgency"] = "asap"
        if entities.get("gratitude_closure"):
            slots["last_user_intent"] = "gratitude_closure"

    def _missing_booking_fields(self, slots: dict[str, Any]) -> tuple[bool, bool, bool]:
        missing_name = not bool(str(slots.get("full_name") or "").strip())
        has_contact = bool(str(slots.get("email") or "").strip() or str(slots.get("phone") or "").strip())
        missing_contact = not has_contact
        missing_datetime = not bool(str(slots.get("appointment_date") or "").strip() and str(slots.get("appointment_time") or "").strip())
        return missing_name, missing_contact, missing_datetime

    def _render_booking_missing_fields_prompt(
        self,
        *,
        lang: LangCode,
        missing_name: bool,
        missing_contact: bool,
        missing_datetime: bool,
    ) -> str:
        if lang == "en":
            fields = []
            if missing_name:
                fields.append("your full name")
            if missing_contact:
                fields.append("your phone or email")
            if missing_datetime:
                fields.append("your preferred date and time")
            ask = self._join_list(fields, lang)
            return f"Great. To continue the admissions appointment booking, please send {ask}."
        if lang == "wo":
            fields = []
            if missing_name:
                fields.append("sa tur bu mat")
            if missing_contact:
                fields.append("sa téléphone walla email")
            if missing_datetime:
                fields.append("bés ak waxtu bi nga bëgg")
            ask = self._join_list(fields, lang)
            return f"Baax na. Ngir ma kontine rendez-vous admission bi, yónnee ma {ask}."

        fields = []
        if missing_name:
            fields.append("votre nom complet")
        if missing_contact:
            fields.append("votre telephone ou email")
        if missing_datetime:
            fields.append("la date et l'heure souhaitees")
        ask = self._join_list(fields, lang)
        return f"Parfait. Pour continuer la reservation du rendez-vous admission, envoyez-moi {ask}."

    def _join_list(self, values: list[str], lang: LangCode) -> str:
        if not values:
            return ""
        if len(values) == 1:
            return values[0]
        if len(values) == 2:
            sep = " and " if lang == "en" else " ak " if lang == "wo" else " et "
            return sep.join(values)
        if lang == "en":
            return ", ".join(values[:-1]) + ", and " + values[-1]
        if lang == "wo":
            return ", ".join(values[:-1]) + " ak " + values[-1]
        return ", ".join(values[:-1]) + " et " + values[-1]

    def _has_recorded_booking(self, state: dict[str, Any]) -> bool:
        slots = self._current_track_slots(state)
        return bool(
            str(slots.get("appointment_id") or "").strip()
            or str(slots.get("appointment_status") or "").strip()
        )

    def _is_appointment_locked(self, state: dict[str, Any]) -> bool:
        return bool(state.get("appointment_locked"))

    def _prepare_booking_restart(self, state: dict[str, Any]) -> None:
        state["appointment_locked"] = False
        slots = state.setdefault("slots_json", {})
        if not isinstance(slots, dict):
            slots = {}
            state["slots_json"] = slots
        for key in (
            "appointment_date",
            "appointment_time",
            "appointment_date_iso",
            "appointment_time_iso",
            "requested_appointment_date",
            "requested_appointment_time",
        ):
            slots.pop(key, None)

    def _is_post_booking_acknowledgement(self, *, normalized: str, entities: dict[str, Any]) -> bool:
        if entities.get("gratitude_closure"):
            return True
        if not entities.get("is_affirmative"):
            return False
        if any(
            entities.get(key)
            for key in ("catalog_request", "details_request", "appointment_request", "recommendation_request")
        ):
            return False
        return len((normalized or "").split()) <= 4

    def _program_display_parts(self, *, program_name: str, access_level: str) -> tuple[str, Optional[str]]:
        base_name = str(program_name or "").strip()
        level_labels: list[str] = []

        match = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", base_name)
        if match:
            candidate_base = match.group(1).strip()
            candidate_level = match.group(2).strip()
            if _looks_like_level_label(candidate_level):
                base_name = candidate_base or base_name
                if candidate_level:
                    level_labels.append(candidate_level)

        access_level_clean = str(access_level or "").strip()
        if access_level_clean:
            access_level_key = _normalize_text(access_level_clean)
            if all(_normalize_text(label) != access_level_key for label in level_labels):
                level_labels.append(access_level_clean)

        clean_levels: list[str] = []
        seen_levels: set[str] = set()
        for label in level_labels:
            key = _normalize_text(label)
            if not key or key in seen_levels:
                continue
            seen_levels.add(key)
            clean_levels.append(label)

        return base_name, ", ".join(clean_levels) if clean_levels else None

    def _format_track_label(
        self,
        *,
        track_name: str,
        program_name: str,
        access_level: str,
        lang: LangCode,
    ) -> str:
        track_label = str(track_name or "").strip() or ("Track" if lang == "en" else "Filiere")
        program_label, level_label = self._program_display_parts(program_name=program_name, access_level=access_level)
        descriptors: list[str] = []
        if program_label and _normalize_text(program_label) != _normalize_text(track_label):
            descriptors.append(program_label)
        if level_label:
            if lang == "en":
                descriptors.append(f"Level {level_label}")
            elif lang == "wo":
                descriptors.append(f"Niveau {level_label}")
            else:
                descriptors.append(f"Niveau {level_label}")
        return track_label + (f" - {' | '.join(descriptors)}" if descriptors else "")

    def _format_program_catalog_label(self, *, program_name: str, access_level: str, lang: LangCode) -> str:
        label = str(program_name or "").strip()
        if label:
            return label
        access_level_clean = str(access_level or "").strip()
        if access_level_clean:
            if lang == "en":
                return f"Program ({access_level_clean})"
            if lang == "wo":
                return f"Programme ({access_level_clean})"
            return f"Programme ({access_level_clean})"
        return "Program" if lang == "en" else "Programme"

    def _unique_program_catalog_labels(self, items: list[dict[str, Any]], *, lang: LangCode) -> list[str]:
        labels: list[str] = []
        seen: set[str] = set()
        for item in items:
            label = self._format_program_catalog_label(
                program_name=str(item.get("program_name") or "").strip() or str(item.get("track_name") or "Programme"),
                access_level=str(item.get("access_level") or ""),
                lang=lang,
            )
            key = _normalize_text(label)
            if not key or key in seen:
                continue
            seen.add(key)
            labels.append(label)
        return labels

    def _unique_track_catalog_labels(self, items: list[dict[str, Any]]) -> list[str]:
        labels: list[str] = []
        seen: set[str] = set()
        for item in items:
            label = str(item.get("track_name") or "").strip() or "Filiere"
            key = _normalize_text(label)
            if not key or key in seen:
                continue
            seen.add(key)
            labels.append(label)
        return labels

    def _group_catalog_by_program(self, items: list[dict[str, Any]], *, lang: LangCode) -> list[tuple[str, list[str]]]:
        groups: dict[str, dict[str, Any]] = {}
        ordered_keys: list[str] = []
        for item in items:
            program_label = self._format_program_catalog_label(
                program_name=str(item.get("program_name") or "").strip() or str(item.get("track_name") or "Programme"),
                access_level=str(item.get("access_level") or ""),
                lang=lang,
            )
            program_key = _normalize_text(program_label)
            if not program_key:
                continue
            if program_key not in groups:
                groups[program_key] = {"program": program_label, "tracks": [], "track_keys": set()}
                ordered_keys.append(program_key)
            track_label = str(item.get("track_name") or "").strip()
            track_key = _normalize_text(track_label)
            if not track_key or track_key in groups[program_key]["track_keys"]:
                continue
            groups[program_key]["track_keys"].add(track_key)
            groups[program_key]["tracks"].append(track_label)

        return [
            (str(groups[key]["program"]), list(groups[key]["tracks"]))
            for key in ordered_keys
        ]

    def _render_booking_confirm_summary(self, *, state: dict[str, Any], lang: LangCode) -> str:
        slots = self._current_track_slots(state)
        track_label = self._format_track_label(
            track_name=str(slots.get("track_name") or "cette filiere"),
            program_name=str(slots.get("program_name") or ""),
            access_level=str(slots.get("access_level") or slots.get("admission_level") or ""),
            lang=lang,
        )
        name = str(slots.get("full_name") or "").strip()
        email = str(slots.get("email") or "").strip()
        phone = str(slots.get("phone") or "").strip()
        date_value = str(slots.get("appointment_date") or "").strip()
        time_value = str(slots.get("appointment_time") or "").strip()
        level = str(slots.get("admission_level") or "").strip()

        if lang == "en":
            lines = [
                f"Great, here is the admissions appointment request summary for {track_label}:",
                f"- Name: {name}",
            ]
            if email:
                lines.append(f"- Email: {email}")
            if phone:
                lines.append(f"- Phone: {phone}")
            lines.append(f"- Preferred slot: {date_value} at {time_value}")
            if level:
                lines.append(f"- Admission level: {level}")
            lines.append("Reply \"yes\" to confirm the request, or send a correction.")
            return "\n".join(lines)

        if lang == "wo":
            lines = [
                f"Baax na, nii la ma dégg ci demande rendez-vous admission bi ({track_label}):",
                f"- Tur: {name}",
            ]
            if email:
                lines.append(f"- Email: {email}")
            if phone:
                lines.append(f"- Téléphone: {phone}")
            lines.append(f"- Bés/waxtu: {date_value} / {time_value}")
            if level:
                lines.append(f"- Niveau: {level}")
            lines.append("Tontul \"waaw\" ngir confirmé, walla yokk/coppite xibaar.")
            return "\n".join(lines)

        lines = [
            f"Parfait, voici le recapitulatif de votre demande de rendez-vous admission pour {track_label} :",
            f"- Nom : {name}",
        ]
        if email:
            lines.append(f"- Email : {email}")
        if phone:
            lines.append(f"- Telephone : {phone}")
        lines.append(f"- Creneau souhaite : {date_value} a {time_value}")
        if level:
            lines.append(f"- Niveau d'admission : {level}")
        lines.append("Repondez \"oui\" pour confirmer la demande, ou envoyez une correction.")
        return "\n".join(lines)

    def _render_booking_submitted_reply(self, *, lang: LangCode) -> str:
        if lang == "en":
            return "Your admissions appointment request has been recorded. The admissions team will confirm the final slot shortly."
        if lang == "wo":
            return "Demande rendez-vous admission bi am na. Equipe admission bi dina la confirmé waxtu bu mujj bi ci lu gaaw."
        return "Votre demande de rendez-vous admission est bien enregistree. Le service admission vous confirmera le creneau final rapidement."

    def _render_gratitude_closure_reply(self, *, lang: LangCode, after_booking: bool = False) -> str:
        if after_booking:
            if lang == "en":
                return "You're welcome. If you have another question about programs, tuition, or admission, I can help."
            if lang == "wo":
                return "Jërëjëf. Su la amee beneen laaj ci programmes, frais walla admission, mën naa la dimbali."
            return "Avec plaisir. Si vous avez une autre question sur les programmes, les frais ou l'admission, je peux vous aider."
        if lang == "en":
            return "You're welcome. I can also help with tuition, admission requirements, or another program/track."
        if lang == "wo":
            return "Jërëjëf. Mën naa la dimbali it ci frais yi, conditions admission yi walla beneen program/filiere."
        return "Avec plaisir. Je peux aussi vous aider sur les frais, les conditions d'admission, ou une autre filiere / un autre programme."

    def _render_track_decline_booking_reply(self, *, lang: LangCode) -> str:
        if lang == "en":
            return "Understood, no appointment booking. I can still share more details about this program/track or show other options."
        if lang == "wo":
            return "Degg naa, du rendez-vous. Waaye mën naa la joxaat yeneen xibaar ci program/filiere bii walla ma wone la yeneen tànn."
        return "D'accord, pas de rendez-vous. Je peux quand meme vous donner plus de details sur ce programme / cette filiere ou vous montrer d'autres options."

    def _render_track_disambiguation_reply(self, *, candidates: list[dict[str, Any]], lang: LangCode) -> str:
        lines: list[str] = []
        for idx, item in enumerate(candidates[:5], start=1):
            label = self._format_track_label(
                track_name=str(item.get("track_name") or "Filiere"),
                program_name=str(item.get("program_name") or ""),
                access_level=str(item.get("access_level") or ""),
                lang=lang,
            )
            delivery_mode = str(item.get("delivery_mode") or "").strip()
            suffix = f" | {delivery_mode}" if delivery_mode else ""
            lines.append(f"{idx}. {label}{suffix}")
        if lang == "en":
            return (
                "I found multiple matches for that name. Please specify the program/level you want:\n"
                + "\n".join(lines)
                + "\n\nExample: reply with the number or the program/level you want."
            )
        if lang == "wo":
            return (
                "Am naa ay tànn yu bari ci tur woowu. Wax ma program walla niveau bi nga bëgg:\n"
                + "\n".join(lines)
                + "\n\nMisaal: tontul ak nimero bi walla program/niveau bi nga bëgg."
            )
        return (
            "J'ai trouve plusieurs options pour ce nom. Precisez le programme / niveau souhaite :\n"
            + "\n".join(lines)
            + "\n\nExemple : repondez avec le numero ou le programme / niveau souhaite."
        )

    def _render_post_booking_confirmation_reply(self, *, state: dict[str, Any], lang: LangCode) -> str:
        slots = self._current_track_slots(state)
        appointment_id = str(slots.get("appointment_id") or "").strip()
        notification_channel = str(slots.get("notification_channel") or "").strip()
        notification_sent = bool(slots.get("notification_sent"))
        notification_queued = bool(slots.get("notification_queued"))
        email = str(slots.get("email") or "").strip()

        if lang == "en":
            if notification_sent and notification_channel == "email":
                return "Yes. A confirmation email has been sent for your admissions appointment request."
            if notification_sent and notification_channel:
                return f"Your appointment request is recorded. A confirmation was sent via {notification_channel}. The admissions team can also follow up by email if needed."
            if notification_queued:
                return "Your appointment request is recorded. A confirmation is queued and should be sent shortly."
            if email and appointment_id:
                return "Your appointment request is recorded. The admissions team will confirm the slot and may contact you by email shortly."
            return "Your appointment request is recorded. The admissions team will confirm the final slot shortly."

        if lang == "wo":
            if notification_sent and notification_channel == "email":
                return "Waaw. Yónnee nañu email confirmation ngir sa demande rendez-vous admission."
            if notification_sent and notification_channel:
                return f"Sa demande rendez-vous am na. Confirmation bi dem na ci {notification_channel}. Equipe admission bi mën na la topp ci email itam."
            if notification_queued:
                return "Sa demande rendez-vous am na. Confirmation bi ngi ci waajal te dina ñëw ci lu gaaw."
            if email and appointment_id:
                return "Sa demande rendez-vous am na. Equipe admission bi dina la confirmé waxtu bi te mën nañu la jokkoo ci email."
            return "Sa demande rendez-vous am na. Equipe admission bi dina la confirmé waxtu bu mujj bi."

        if notification_sent and notification_channel == "email":
            return "Oui. Un email de confirmation a ete envoye pour votre demande de rendez-vous admission."
        if notification_sent and notification_channel:
            return (
                f"Votre demande de rendez-vous est bien enregistree. Une confirmation a ete envoyee via {notification_channel}. "
                "Le service admission peut aussi vous recontacter selon vos coordonnees."
            )
        if notification_queued:
            return "Votre demande de rendez-vous est bien enregistree. Une confirmation est en file d'envoi et vous sera envoyee rapidement."
        if email and appointment_id:
            return "Votre demande de rendez-vous est bien enregistree. Le service admission vous confirmera le creneau et peut vous recontacter par email."
        return "Votre demande de rendez-vous est bien enregistree. Le service admission vous confirmera le creneau final rapidement."

    def _render_booking_cancel_reply(self, *, lang: LangCode) -> str:
        if lang == "en":
            return "Understood. I stopped the appointment booking flow. I can still help with tuition, requirements, or track details."
        if lang == "wo":
            return "Degg naa. Ma taxawalul booking bi. Mën naa la dimbali ci frais yi, conditions yi walla xibaaru filiere bi."
        return "D'accord. J'arrete la reservation pour le moment. Je peux toujours vous aider sur les frais, les conditions d'admission ou les details de la filiere."

    def _render_choose_track_before_booking(self, *, lang: LangCode) -> str:
        if lang == "en":
            return "I can help schedule an admissions appointment. First, tell me which track/program interests you."
        if lang == "wo":
            return "Mën naa la defal rendez-vous admission. Waaye jëkk wax ma filiere/program bi nga bëgg."
        return "Je peux vous aider a reserver un rendez-vous admission. Dites-moi d'abord la filiere / le programme qui vous interesse."

    def _handle_post_booking_submitted_followup(
        self,
        *,
        state: dict[str, Any],
        lang: LangCode,
        normalized: str,
        entities: dict[str, Any],
        direct_items: list[dict[str, Any]],
    ) -> Optional[OrchestratorTurn]:
        if self._is_post_booking_acknowledgement(normalized=normalized, entities=entities):
            state["active_flow"] = "browsing_catalog"
            return self._reply(
                state,
                "deterministic_gratitude_after_booking",
                self._render_gratitude_closure_reply(lang=lang, after_booking=True),
                lang,
                intent="gratitude_closure",
            )
        # Let explicit catalog/details/new track requests flow through normal handlers.
        if entities.get("catalog_request") or entities.get("details_request") or direct_items:
            state["active_flow"] = "browsing_catalog"
            return None
        if _is_confirmation_followup_request(normalized):
            state["active_flow"] = "browsing_catalog"
            return self._reply(
                state,
                "deterministic_booking_post_submit_followup",
                self._render_post_booking_confirmation_reply(state=state, lang=lang),
                lang,
                intent="booking_post_submit_followup",
            )
        state["active_flow"] = "browsing_catalog"
        return None

    def _handle_booking_flow(
        self,
        *,
        state: dict[str, Any],
        lang: LangCode,
        message: str,
        normalized: str,
        entities: dict[str, Any],
        direct_items: list[dict[str, Any]],
    ) -> OrchestratorTurn:
        self._merge_slots_from_entities(state, entities)
        slots = self._current_track_slots(state)

        if not str(slots.get("track_name") or "").strip():
            state["active_flow"] = "browsing_catalog"
            return self._reply(state, "deterministic_booking_need_track", self._render_choose_track_before_booking(lang=lang), lang, intent="booking_need_track")

        if entities.get("is_negative"):
            state["active_flow"] = "track_selected"
            return self._reply(state, "deterministic_booking_cancel", self._render_booking_cancel_reply(lang=lang), lang, intent="booking_cancel")

        if str(state.get("active_flow")) == "booking_confirm" and entities.get("is_affirmative"):
            state["active_flow"] = "booking_submitted"
            return self._reply(state, "deterministic_booking_submitted", self._render_booking_submitted_reply(lang=lang), lang, intent="booking_submit")

        missing_name, missing_contact, missing_datetime = self._missing_booking_fields(slots)
        if missing_name or missing_contact:
            state["active_flow"] = "booking_collect_contact"
            prompt = self._render_booking_missing_fields_prompt(
                lang=lang,
                missing_name=missing_name,
                missing_contact=missing_contact,
                missing_datetime=missing_datetime,
            )
            if entities.get("urgency_request") and missing_datetime and lang == "fr":
                prompt += " Si vous voulez un creneau le plus tot possible, indiquez aussi vos plages de disponibilite."
            elif entities.get("urgency_request") and missing_datetime and lang == "en":
                prompt += " If you want the earliest slot, share your available time range too."
            elif entities.get("urgency_request") and missing_datetime and lang == "wo":
                prompt += " Su nga bëgg gaaw, wax ma waxtuy disponibilité yi nga am."
            return self._reply(state, "deterministic_booking_collect_contact", prompt, lang, intent="booking_collect_contact")

        if missing_datetime:
            state["active_flow"] = "booking_collect_datetime"
            prompt = self._render_booking_missing_fields_prompt(
                lang=lang,
                missing_name=False,
                missing_contact=False,
                missing_datetime=True,
            )
            if entities.get("urgency_request") and lang == "fr":
                prompt += " Si vous voulez un creneau le plus tot possible, indiquez aussi vos plages de disponibilite."
            elif entities.get("urgency_request") and lang == "en":
                prompt += " If you want the earliest slot, share your available time range too."
            elif entities.get("urgency_request") and lang == "wo":
                prompt += " Su nga bëgg gaaw, wax ma waxtuy disponibilité yi nga am."
            return self._reply(state, "deterministic_booking_collect_datetime", prompt, lang, intent="booking_collect_datetime")

        state["active_flow"] = "booking_confirm"
        return self._reply(state, "deterministic_booking_confirm", self._render_booking_confirm_summary(state=state, lang=lang), lang, intent="booking_confirm")

    def _handle_catalog_and_track(
        self,
        *,
        state: dict[str, Any],
        lang: LangCode,
        message: str,
        normalized: str,
        direct_items: list[dict[str, Any]],
        entities: dict[str, Any],
    ) -> Optional[OrchestratorTurn]:
        catalog_request = bool(entities.get("catalog_request"))
        details_request = bool(entities.get("details_request"))
        appointment_request = bool(entities.get("appointment_request"))
        catalog_subject = str(entities.get("catalog_subject") or "track")
        selected_track = self._current_track_slots(state)
        has_selected_track = bool(str(selected_track.get("track_name") or "").strip())
        has_recorded_booking = self._has_recorded_booking(state)

        if has_recorded_booking and self._is_post_booking_acknowledgement(normalized=normalized, entities=entities):
            state["active_flow"] = "browsing_catalog"
            return self._reply(
                state,
                "deterministic_gratitude_after_booking",
                self._render_gratitude_closure_reply(lang=lang, after_booking=True),
                lang,
                intent="gratitude_closure",
            )

        if has_recorded_booking and _is_confirmation_followup_request(normalized):
            state["active_flow"] = "browsing_catalog"
            return self._reply(
                state,
                "deterministic_booking_post_submit_followup",
                self._render_post_booking_confirmation_reply(state=state, lang=lang),
                lang,
                intent="booking_post_submit_followup",
            )

        if entities.get("gratitude_closure") and not (catalog_request or details_request or appointment_request):
            return self._reply(
                state,
                "deterministic_gratitude",
                self._render_gratitude_closure_reply(lang=lang, after_booking=False),
                lang,
                intent="gratitude_closure",
            )

        if entities.get("recommendation_request"):
            if direct_items:
                self._set_last_catalog_context(state, direct_items, subject=catalog_subject)
            elif has_selected_track:
                self._set_last_catalog_context(
                    state,
                    [
                        {
                            "track_id": selected_track.get("track_id"),
                            "track_name": selected_track.get("track_name"),
                            "program_name": selected_track.get("program_name"),
                            "access_level": selected_track.get("access_level"),
                            "delivery_mode": selected_track.get("delivery_mode"),
                            "annual_fee": selected_track.get("annual_fee"),
                            "registration_fee": selected_track.get("registration_fee"),
                            "monthly_fee": selected_track.get("monthly_fee"),
                            "certifications": selected_track.get("certifications"),
                        }
                    ],
                    subject="track",
                )
            state["active_flow"] = "browsing_catalog"
            return None

        if has_selected_track and entities.get("is_negative") and not (catalog_request or details_request or appointment_request):
            state["active_flow"] = "track_selected"
            return self._reply(
                state,
                "deterministic_track_decline_booking",
                self._render_track_decline_booking_reply(lang=lang),
                lang,
                intent="booking_declined",
            )

        if catalog_request and not details_request and not appointment_request:
            if direct_items:
                self._set_last_catalog_context(state, direct_items, subject=catalog_subject)
                state["active_flow"] = "browsing_catalog"
                return self._reply(
                    state,
                    "deterministic_catalog",
                    self._render_catalog_reply(direct_items, lang, subject=catalog_subject),
                    lang,
                    intent="catalog",
                )
            return None

        if details_request or appointment_request:
            explicit = [item for item in direct_items if self._message_mentions_track(item, message)]
            if explicit:
                explicit = self._narrow_track_candidates(explicit, normalized)
            if len(explicit) > 1:
                self._set_last_catalog_context(state, explicit, subject="track")
                self._set_pending_track_options(state, explicit)
                state["active_flow"] = "browsing_catalog"
                return self._reply(
                    state,
                    "deterministic_track_disambiguation",
                    self._render_track_disambiguation_reply(candidates=explicit, lang=lang),
                    lang,
                    intent="track_disambiguation",
                )
            if explicit:
                self._apply_track_slot(state, explicit[0])
                state["active_flow"] = "track_selected"
                return self._reply(
                    state,
                    "deterministic_track_details",
                    self._render_track_details_reply(explicit[0], lang=lang, ask_rdv=appointment_request),
                    lang,
                    intent="track_details",
                )
            if has_selected_track:
                state["active_flow"] = "track_selected"
                pseudo_item = {
                    "track_id": selected_track.get("track_id"),
                    "track_name": selected_track.get("track_name"),
                    "program_name": selected_track.get("program_name"),
                    "annual_fee": selected_track.get("annual_fee"),
                    "registration_fee": selected_track.get("registration_fee"),
                    "monthly_fee": selected_track.get("monthly_fee"),
                    "access_level": selected_track.get("access_level"),
                    "delivery_mode": selected_track.get("delivery_mode"),
                    "certifications": selected_track.get("certifications"),
                }
                if pseudo_item.get("annual_fee") is not None:
                    return self._reply(
                        state,
                        "deterministic_track_details",
                        self._render_track_details_reply(pseudo_item, lang=lang, ask_rdv=appointment_request or details_request),
                        lang,
                        intent="track_details",
                    )
            if len(direct_items) == 1:
                self._apply_track_slot(state, direct_items[0])
                state["active_flow"] = "track_selected"
                return self._reply(
                    state,
                    "deterministic_track_details",
                    self._render_track_details_reply(direct_items[0], lang=lang, ask_rdv=appointment_request),
                    lang,
                    intent="track_details",
                )
            if direct_items:
                self._set_last_catalog_context(state, direct_items, subject=catalog_subject)
                state["active_flow"] = "browsing_catalog"
                return self._reply(
                    state,
                    "deterministic_catalog",
                    self._render_catalog_reply(direct_items, lang, subject=catalog_subject),
                    lang,
                    intent="catalog",
                )
            return None

        if direct_items:
            explicit = [item for item in direct_items if self._message_mentions_track(item, message)]
            if explicit:
                explicit = self._narrow_track_candidates(explicit, normalized)
            if len(explicit) > 1:
                self._set_last_catalog_context(state, explicit, subject="track")
                self._set_pending_track_options(state, explicit)
                state["active_flow"] = "browsing_catalog"
                return self._reply(
                    state,
                    "deterministic_track_disambiguation",
                    self._render_track_disambiguation_reply(candidates=explicit, lang=lang),
                    lang,
                    intent="track_disambiguation",
                )
            if explicit:
                self._apply_track_slot(state, explicit[0])
                state["active_flow"] = "track_selected"
                return self._reply(
                    state,
                    "deterministic_track_details",
                    self._render_track_details_reply(explicit[0], lang=lang, ask_rdv=False),
                    lang,
                    intent="track_selected",
                )
            if len(direct_items) == 1:
                self._apply_track_slot(state, direct_items[0])
                state["active_flow"] = "track_selected"
                return self._reply(
                    state,
                    "deterministic_track_details",
                    self._render_track_details_reply(direct_items[0], lang=lang, ask_rdv=False),
                    lang,
                    intent="track_selected",
                )

        return None

    def _render_catalog_reply(self, items: list[dict[str, Any]], lang: LangCode, *, subject: str = "track") -> str:
        rows = self._dedupe_track_items(items, limit=CATALOG_REPLY_LIMIT)
        lines: list[str] = []
        mode = subject if subject in {"program", "track", "grouped"} else "track"
        if mode == "program":
            programs = self._unique_program_catalog_labels(rows, lang=lang)
            for index, program_label in enumerate(programs, start=1):
                lines.append(f"{index}. {program_label}")
        elif mode == "grouped":
            grouped_rows = self._group_catalog_by_program(rows, lang=lang)
            for index, (program_label, track_labels) in enumerate(grouped_rows, start=1):
                if track_labels:
                    lines.append(f"{index}. {program_label} : {', '.join(track_labels)}")
                else:
                    lines.append(f"{index}. {program_label}")
        else:
            track_labels = self._unique_track_catalog_labels(rows)
            for index, label in enumerate(track_labels, start=1):
                lines.append(f"{index}. {label}")

        if lang == "en":
            if mode == "program":
                header = "Here are the programs currently available:"
                footer = "Tell me which program interests you and I will share the details. I can also schedule an admissions appointment."
            elif mode == "grouped":
                header = "Here are the programs with their tracks currently available:"
                footer = "Tell me the program or track you want, and I will share the details. I can also schedule an admissions appointment."
            else:
                header = "Here are the tracks currently available:"
                footer = "Some tracks may exist in multiple programs or levels. Tell me which track you want and I will clarify the right option."
            return (
                header
                + "\n"
                + "\n".join(lines)
                + "\n\n"
                + footer
            )
        if lang == "wo":
            if mode == "program":
                header = "Yii la programmes yi am léegi:"
                footer = "Wax ma programme bi nga bëgg, dinaa la leeral xibaar yi. Mën naa la defal rendez-vous admission itam."
            elif mode == "grouped":
                header = "Yii la programmes yi ak filieres yi ci biir seen kurus yi:"
                footer = "Wax ma programme walla filiere bi nga bëgg, dinaa la leeral xibaar yi. Mën naa la defal rendez-vous admission itam."
            else:
                header = "Yii la filieres yi am léegi:"
                footer = "Am na ay filiere yu mën a am ci bari programmes walla niveaux. Wax ma filiere bi nga bëgg, dinaa la leeral option bi baax."
            return (
                header
                + "\n"
                + "\n".join(lines)
                + "\n\n"
                + footer
            )
        if mode == "program":
            header = "Voici les programmes disponibles actuellement :"
            footer = "Dites-moi le programme qui vous interesse et je vous donne les details. Je peux aussi organiser un rendez-vous admission."
        elif mode == "grouped":
            header = "Voici les programmes avec leurs filieres disponibles actuellement :"
            footer = "Dites-moi le programme ou la filiere qui vous interesse et je vous donne les details. Je peux aussi organiser un rendez-vous admission."
        else:
            header = "Voici les filieres disponibles actuellement :"
            footer = "Certaines filieres existent dans plusieurs programmes ou niveaux. Dites-moi la filiere qui vous interesse et je vous precise la bonne option."
        return (
            header
            + "\n"
            + "\n".join(lines)
            + "\n\n"
            + footer
        )

    def _render_track_details_reply(self, item: dict[str, Any], *, lang: LangCode, ask_rdv: bool) -> str:
        track_label = self._format_track_label(
            track_name=str(item.get("track_name") or "Filiere"),
            program_name=str(item.get("program_name") or ""),
            access_level=str(item.get("access_level") or ""),
            lang=lang,
        )
        annual = _format_cfa(item.get("annual_fee"))
        registration = _format_cfa(item.get("registration_fee"))
        monthly = _format_cfa(item.get("monthly_fee"))
        access_level = str(item.get("access_level") or "").strip()
        delivery_mode = str(item.get("delivery_mode") or "").strip()
        certifications = str(item.get("certifications") or "").strip()

        delivery_mode_display = {
            "onsite": {"fr": "presentiel", "en": "onsite", "wo": "presentiel"},
            "elearning": {"fr": "e-learning", "en": "e-learning", "wo": "e-learning"},
            "hybrid": {"fr": "hybride", "en": "hybrid", "wo": "hybride"},
        }.get(delivery_mode.lower() if delivery_mode else "", {}).get(lang, delivery_mode)

        if lang == "en":
            details = [
                f"Here are the details for the program/track {track_label}:",
                f"- Annual tuition: {annual} F CFA",
                f"- Enrollment fee: {registration} F CFA",
                f"- Monthly payment: {monthly} F CFA",
            ]
            if access_level:
                details.append(f"- Entry level: {access_level}")
            if delivery_mode_display:
                details.append(f"- Delivery mode: {delivery_mode_display}")
            if certifications:
                details.append(f"- Certifications: {certifications}")
            details.append(
                "If you want, I can book your admissions appointment now. Share your name, phone/email, and preferred date/time."
                if ask_rdv
                else "Would you like me to schedule an admissions appointment as well?"
            )
            return "\n".join(details)

        if lang == "wo":
            details = [
                f"Xibaar yi ci program/filiere {track_label}:",
                f"- Frais annuel: {annual} F CFA",
                f"- Droit d'inscription: {registration} F CFA",
                f"- Mensualite: {monthly} F CFA",
            ]
            if access_level:
                details.append(f"- Niveau d'accès: {access_level}")
            if delivery_mode_display:
                details.append(f"- Modalite: {delivery_mode_display}")
            if certifications:
                details.append(f"- Certifications: {certifications}")
            details.append(
                "Mën naa la defal rendez-vous admission léegi. Yónnee ma sa tur, téléphone/email ak bés ak waxtu bi nga bëgg."
                if ask_rdv
                else "Ndax bëgg nga ma defal la rendez-vous admission itam?"
            )
            return "\n".join(details)

        details = [
            f"Voici les details pour le programme / la filiere {track_label} :",
            f"- Frais annuels : {annual} F CFA",
            f"- Frais d'inscription : {registration} F CFA",
            f"- Mensualite : {monthly} F CFA",
        ]
        if access_level:
            details.append(f"- Niveau d'acces : {access_level}")
        if delivery_mode_display:
            details.append(f"- Modalite : {delivery_mode_display}")
        if certifications:
            details.append(f"- Certifications : {certifications}")
        details.append(
            "Je peux vous reserver un rendez-vous admission maintenant. Envoyez votre nom, telephone/email et vos disponibilites (date + heure)."
            if ask_rdv
            else "Souhaitez-vous aussi que je reserve un rendez-vous admission ?"
        )
        return "\n".join(details)

    def _set_pending_track_options(self, state: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
        slots = state.setdefault("slots_json", {})
        if not isinstance(slots, dict):
            slots = {}
            state["slots_json"] = slots
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in candidates:
            track_id = str(item.get("track_id") or "").strip()
            if not track_id or track_id in seen:
                continue
            seen.add(track_id)
            normalized.append(
                {
                    "track_id": track_id,
                    "track_name": item.get("track_name"),
                    "program_name": item.get("program_name"),
                    "access_level": item.get("access_level"),
                    "delivery_mode": item.get("delivery_mode"),
                    "annual_fee": item.get("annual_fee"),
                    "registration_fee": item.get("registration_fee"),
                    "monthly_fee": item.get("monthly_fee"),
                    "certifications": item.get("certifications"),
                }
            )
            if len(normalized) >= 8:
                break
        if normalized:
            slots["pending_track_options"] = normalized
        else:
            slots.pop("pending_track_options", None)

    def _set_last_catalog_context(self, state: dict[str, Any], items: list[dict[str, Any]], *, subject: str) -> None:
        slots = state.setdefault("slots_json", {})
        if not isinstance(slots, dict):
            slots = {}
            state["slots_json"] = slots
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            track_name = str(item.get("track_name") or "").strip()
            program_name = str(item.get("program_name") or "").strip()
            access_level = str(item.get("access_level") or "").strip()
            identity = _normalize_text(" | ".join(part for part in (track_name, program_name, access_level) if part))
            if not identity or identity in seen:
                continue
            seen.add(identity)
            normalized.append(
                {
                    "track_id": str(item.get("track_id") or "").strip() or None,
                    "track_name": track_name or None,
                    "program_name": program_name or None,
                    "access_level": access_level or None,
                    "delivery_mode": str(item.get("delivery_mode") or "").strip() or None,
                    "annual_fee": item.get("annual_fee"),
                    "registration_fee": item.get("registration_fee"),
                    "monthly_fee": item.get("monthly_fee"),
                    "certifications": str(item.get("certifications") or "").strip() or None,
                }
            )
            if len(normalized) >= 8:
                break
        if normalized:
            slots["last_catalog_options"] = normalized
            slots["last_catalog_subject"] = subject if subject in {"program", "track", "grouped"} else "track"
        else:
            slots.pop("last_catalog_options", None)
            slots.pop("last_catalog_subject", None)

    def _catalog_options_from_state(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        slots = state.get("slots_json")
        if not isinstance(slots, dict):
            return []
        raw_options = slots.get("last_catalog_options")
        if isinstance(raw_options, list):
            items = [item for item in raw_options if isinstance(item, dict)]
            if items:
                return items
        raw_pending = slots.get("pending_track_options")
        if isinstance(raw_pending, list):
            items = [item for item in raw_pending if isinstance(item, dict)]
            if items:
                return items
        if str(slots.get("track_name") or "").strip():
            return [
                {
                    "track_id": str(slots.get("track_id") or "").strip() or None,
                    "track_name": str(slots.get("track_name") or "").strip() or None,
                    "program_name": str(slots.get("program_name") or "").strip() or None,
                    "access_level": str(slots.get("access_level") or slots.get("admission_level") or "").strip() or None,
                    "delivery_mode": str(slots.get("delivery_mode") or "").strip() or None,
                    "annual_fee": slots.get("annual_fee"),
                    "registration_fee": slots.get("registration_fee"),
                    "monthly_fee": slots.get("monthly_fee"),
                    "certifications": str(slots.get("certifications") or "").strip() or None,
                }
            ]
        return []

    def _render_recommendation_fallback_reply(self, *, state: dict[str, Any], lang: LangCode) -> Optional[str]:
        options = self._catalog_options_from_state(state)
        if not options:
            return None
        compact_lines: list[str] = []
        for item in options[:3]:
            label = self._format_track_label(
                track_name=str(item.get("track_name") or "Filiere"),
                program_name=str(item.get("program_name") or ""),
                access_level=str(item.get("access_level") or ""),
                lang=lang,
            )
            criteria: list[str] = []
            access_level = str(item.get("access_level") or "").strip()
            if access_level:
                if lang == "en":
                    criteria.append(f"entry level {access_level}")
                elif lang == "wo":
                    criteria.append(f"niveau {access_level}")
                else:
                    criteria.append(f"niveau {access_level}")
            monthly_fee = item.get("monthly_fee")
            if monthly_fee not in (None, "", 0):
                if lang == "en":
                    criteria.append(f"monthly payment {_format_cfa(monthly_fee)} F CFA")
                elif lang == "wo":
                    criteria.append(f"mensualite {_format_cfa(monthly_fee)} F CFA")
                else:
                    criteria.append(f"mensualite {_format_cfa(monthly_fee)} F CFA")
            delivery_mode = str(item.get("delivery_mode") or "").strip()
            if delivery_mode:
                if lang == "en":
                    criteria.append(f"mode {delivery_mode}")
                else:
                    criteria.append(f"modalite {delivery_mode}")
            certifications = str(item.get("certifications") or "").strip()
            if certifications:
                criteria.append(certifications)
            suffix = f": {', '.join(criteria[:3])}" if criteria else ""
            compact_lines.append(f"- {label}{suffix}")
        if not compact_lines:
            return None
        if lang == "en":
            return (
                "I cannot say one option is absolutely the best. The right choice depends on your goal, entry level, and budget.\n"
                + "\n".join(compact_lines)
                + "\n\nTell me your priority (fast employment, specialization, budget, or current level) and I will narrow the best fit."
            )
        if lang == "wo":
            return (
                "Manuma wax ne benn option mo gën ci yépp. Li gën a baax dafay sukkandiku ci sa objectif, sa niveau, ak sa budget.\n"
                + "\n".join(compact_lines)
                + "\n\nWax ma li gën a am solo ci yaw (liggéey bu gaaw, spécialisation, budget, walla niveau bi nga nekk) ma jublu la ci option bi gën a méngoo."
            )
        return (
            "Je ne peux pas dire qu'une option est la meilleure en absolu. Le bon choix depend surtout de votre objectif, de votre niveau d'acces et de votre budget.\n"
            + "\n".join(compact_lines)
            + "\n\nDites-moi votre priorite (emploi rapide, specialisation, budget ou niveau actuel) et je vous orienterai vers l'option la plus adaptee."
        )

    def _resolve_pending_track_candidate(self, *, state: dict[str, Any], normalized: str) -> Optional[dict[str, Any]]:
        slots = state.get("slots_json")
        if not isinstance(slots, dict):
            return None
        raw_options = slots.get("pending_track_options")
        if not isinstance(raw_options, list) or not raw_options:
            return None
        candidates = [item for item in raw_options if isinstance(item, dict)]
        if not candidates:
            slots.pop("pending_track_options", None)
            return None
        narrowed = self._narrow_track_candidates(candidates, normalized)
        if len(narrowed) == 1:
            slots.pop("pending_track_options", None)
            return narrowed[0]
        return None

    def _narrow_track_candidates(self, candidates: list[dict[str, Any]], normalized: str) -> list[dict[str, Any]]:
        if len(candidates) <= 1:
            return candidates
        narrowed = list(candidates)
        for field in ("program_name", "access_level", "delivery_mode"):
            exact = []
            partial = []
            for item in narrowed:
                field_value = _normalize_text(str(item.get(field) or ""))
                if not field_value:
                    continue
                if field_value == normalized:
                    exact.append(item)
                elif field_value in normalized:
                    partial.append(item)
            if len(exact) == 1:
                return exact
            if exact:
                narrowed = exact
                continue
            if len(partial) == 1:
                return partial
            if partial:
                narrowed = partial
        return narrowed

    def _reply(
        self,
        state: dict[str, Any],
        strategy: str,
        reply: Optional[str],
        lang: LangCode,
        *,
        use_llm: bool = False,
        intent: Optional[str] = None,
    ) -> OrchestratorTurn:
        is_progressive_fallback = str(strategy).startswith("fallback_")
        current_failures = max(0, int(state.get("failure_count") or 0))
        if is_progressive_fallback:
            state["clarification_success"] = False
            state["handoff_allowed"] = bool(state.get("fallback_stage") == "handoff")
        else:
            state["clarification_success"] = current_failures > 0
            state["failure_count"] = 0
            state["fallback_stage"] = None
            state["handoff_allowed"] = False
            state["handoff_trigger_reason"] = None
        state["response_strategy"] = strategy
        if use_llm:
            state["pending_open_intent"] = str(intent or "open_query")
        else:
            state.pop("pending_open_intent", None)
        return OrchestratorTurn(
            reply=reply,
            state=state,
            lang=lang,
            response_strategy=strategy,
            use_llm=use_llm,
            inferred_intent=intent,
        )


__all__ = [
    "ConversationOrchestrator",
    "OrchestratorTurn",
    "parse_conversation_state",
    "dump_conversation_state",
    "_looks_like_name_only_message",
]
