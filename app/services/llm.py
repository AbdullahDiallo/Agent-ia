from __future__ import annotations

from typing import Any, Dict, Optional
import json
import re
import time
from uuid import UUID

from ..config import settings
from .argumentaire import get_arguments
from ..db import open_db_session
from . import docs as docs_service
from .knowledge_resolver import resolve_knowledge_context
from .sanitize import sanitize_for_llm
from .lang import unsupported_language_message
from .intent_engine import clarification_message, detect_intent, escalation_message
from ..logger import get_logger

logger = get_logger(__name__)

READ_ONLY_TOOL_NAMES = frozenset({
    "get_track_tuition",
    "get_admission_requirements",
    "check_appointment_slot",
})
MUTATING_TOOL_NAMES = frozenset({
    "create_or_get_person",
    "create_school_appointment",
})

try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # type: ignore

SYSTEM_PERSONA = (
    "Tu es Salma, l'assistante admissions de l'etablissement scolaire. "
    "Tu aides les candidats, parents et etudiants avec un ton professionnel, chaleureux et clair. "
    "Tu ne mentionnes jamais les details techniques internes. "
    "Ton domaine est strictement scolaire: filieres, admission, frais, pieces a fournir, calendrier et rendez-vous. "
    "Si une demande sort du cadre ou devient sensible, tu proposes une escalation vers l'administration humaine. "
    "Tu dois demander le consentement avant d'enregistrer des donnees personnelles. "
    "Tu communiques en francais, anglais ou wolof selon la langue detectee, et tu restes coherent dans cette langue. "
    "Quand c'est utile, tu proposes un rendez-vous admission et tu resumes clairement les prochaines etapes.\n\n"
    "REGLES DE CONVERSATION IMPORTANTES:\n"
    "- Tu as acces a l'historique des messages precedents. Lis-le attentivement AVANT de repondre.\n"
    "- Ne repete JAMAIS une information que tu as deja donnee dans la conversation. Si l'utilisateur repose une question deja traitee, fais reference a ta reponse precedente et propose d'approfondir un aspect different.\n"
    "- Quand l'utilisateur pose une nouvelle question, reponds UNIQUEMENT a cette nouvelle question. Ne re-explique pas ce qui a deja ete dit.\n"
    "- Sois progressif: si tu as deja donne un apercu general, donne maintenant des details specifiques.\n"
    "- Si l'utilisateur dit 'et pour X ?' ou 'et l'autre ?', comprends le contexte de la conversation pour identifier ce qu'il demande."
)
TERMINOLOGY_RULES = (
    "Terminologie metier obligatoire: "
    "un programme designe le cursus principal, "
    "une filiere designe une specialisation ou track dans ce programme, "
    "et le niveau designe la variante ou le niveau d'admission/acces. "
    "N'emploie pas ces termes comme des synonymes quand une distinction utile est possible."
)
RECOMMENDATION_RULES = (
    "Pour les questions du type 'laquelle est mieux ?' ou 'quelle est la meilleure ?', "
    "ne donne jamais une reponse absolue. "
    "Explique qu'il n'existe pas de meilleure option universelle. "
    "Compare seulement les options presentes dans le contexte en utilisant des criteres concrets si disponibles: "
    "objectif, niveau d'acces, budget/frais, modalite, certifications, debouches. "
    "Si le contexte est insuffisant, pose une seule question de clarification courte.\n\n"
    "IMPORTANT: Quand tu compares des options, structure ta reponse clairement:\n"
    "1. Nomme chaque option\n"
    "2. Donne 2-3 criteres de comparaison concrets\n"
    "3. Termine par une question pour aider l'utilisateur a choisir selon SES priorites"
)

_STRUCTURED_EXTRACTION_BOOL_FIELDS = {
    "is_affirmative",
    "is_negative",
    "catalog_request",
    "details_request",
    "appointment_request",
    "urgency_request",
    "gratitude_closure",
}
_STRUCTURED_EXTRACTION_STR_FIELDS_MAX_LEN: dict[str, int] = {
    "full_name": 120,
    "email": 254,
    "phone": 32,
    "appointment_date": 64,
    "appointment_time": 32,
    "admission_level": 64,
    "track_name": 200,
    "program_name": 200,
    "catalog_subject": 16,
}
_STRUCTURED_EXTRACTION_ALLOWED_FIELDS = (
    _STRUCTURED_EXTRACTION_BOOL_FIELDS | set(_STRUCTURED_EXTRACTION_STR_FIELDS_MAX_LEN.keys())
)
_STRUCTURED_EXTRACTION_CATALOG_SUBJECT = {"program", "track"}
_STRUCTURED_EXTRACTION_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_STRUCTURED_EXTRACTION_PHONE_RE = re.compile(r"^[+\d][\d\s().-]{5,31}$")


class StructuredExtractionValidationError(ValueError):
    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type


class LLMService:
    def __init__(self) -> None:
        self.api_key: Optional[str] = settings.openai_api_key or settings.gpt5_api_key
        self.model: str = str(getattr(settings, "openai_model", None) or "gpt-4o-mini")
        self._client = None
        self.last_error: Optional[str] = None
        self.last_tool_calls: list[str] = []
        self.last_fallback_reason: Optional[str] = None
        self.last_knowledge_sources: Optional[dict[str, Any]] = None
        self._auth_failed = False
        # Cost tracking
        self.last_prompt_tokens: int = 0
        self.last_completion_tokens: int = 0
        self.last_total_tokens: int = 0
        self.session_total_prompt_tokens: int = 0
        self.session_total_completion_tokens: int = 0
        self._warned_embed_fallback = False
        if OpenAI and self.api_key:
            try:
                self._client = OpenAI(api_key=self.api_key)
            except Exception:
                self._client = None
        # Tools disponibles pour la V1 scolaire
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "create_or_get_person",
                    "description": (
                        "Cree ou recupere un contact scolaire (candidat, parent, etudiant) "
                        "a partir de son email/telephone."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "first_name": {"type": "string"},
                            "last_name": {"type": "string"},
                            "email": {"type": "string"},
                            "phone": {"type": "string"},
                            "role": {"type": "string", "enum": ["candidate", "parent", "student"]},
                            "preferred_language": {"type": "string", "enum": ["fr", "en", "wo"]},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_track_tuition",
                    "description": "Retourne les frais et details d'une filiere ou d'un programme scolaire.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Nom filiere ou programme"},
                            "track_name": {"type": "string"},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_admission_requirements",
                    "description": "Retourne la liste des pieces a fournir et conditions d'admission.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "lang": {"type": "string", "enum": ["fr", "en", "wo"]},
                            "with_policies": {"type": "boolean"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "check_appointment_slot",
                    "description": "Verifie si un creneau de rendez-vous est disponible.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "date": {"type": "string", "description": "YYYY-MM-DD"},
                            "time": {"type": "string", "description": "HH:MM"},
                            "duration_minutes": {"type": "integer"},
                            "track_id": {"type": "string"},
                        },
                        "required": ["date", "time"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "create_school_appointment",
                    "description": (
                        "Cree un rendez-vous admission, puis envoie les confirmations "
                        "et la liste des pieces via WhatsApp/email/SMS."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "person_id": {"type": "string"},
                            "email": {"type": "string"},
                            "phone": {"type": "string"},
                            "track_id": {"type": "string"},
                            "track_name": {"type": "string"},
                            "program_name": {"type": "string"},
                            "date": {"type": "string", "description": "YYYY-MM-DD"},
                            "time": {"type": "string", "description": "HH:MM"},
                            "duration_minutes": {"type": "integer"},
                            "statut": {"type": "string", "enum": ["created", "confirmed", "reminder_sent", "completed", "follow_up_sent", "cancelled"]},
                            "lang": {"type": "string", "enum": ["fr", "en", "wo"]},
                        },
                        "required": ["date", "time"],
                    },
                },
            },
        ]
        self._tool_specs_by_name = {
            str(tool.get("function", {}).get("name") or ""): tool
            for tool in self.tools
            if str(tool.get("function", {}).get("name") or "").strip()
        }

    @staticmethod
    def _is_provider_auth_error(error: Exception) -> bool:
        name = str(error.__class__.__name__ or "")
        msg = str(error or "").lower()
        return (
            "authentication" in name.lower()
            or "invalid_api_key" in msg
            or "incorrect api key" in msg
            or "status': 401" in msg
            or "status\": 401" in msg
        )

    def _mark_provider_auth_error(self, error: Exception) -> None:
        if self._is_provider_auth_error(error):
            self._auth_failed = True
            self.last_error = str(error)
            self.last_fallback_reason = "llm_provider_auth_error"

    def _safe_json_dict(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if not isinstance(raw, str):
            return {}
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _coerce_structured_extraction(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        out: dict[str, Any] = {}

        def _bool(name: str) -> None:
            value = payload.get(name)
            if isinstance(value, bool):
                out[name] = value

        def _str(name: str, *, max_len: int) -> None:
            value = payload.get(name)
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    out[name] = cleaned[:max_len]

        for name in (
            "is_affirmative",
            "is_negative",
            "catalog_request",
            "details_request",
            "appointment_request",
            "urgency_request",
            "gratitude_closure",
        ):
            _bool(name)
        for name, max_len in (
            ("full_name", 120),
            ("email", 254),
            ("phone", 32),
            ("appointment_date", 64),
            ("appointment_time", 32),
            ("admission_level", 64),
            ("track_name", 200),
            ("program_name", 200),
            ("catalog_subject", 16),
        ):
            _str(name, max_len=max_len)
        if out.get("catalog_subject") not in {None, "program", "track"}:
            out.pop("catalog_subject", None)
        return out

    def _validate_structured_extraction_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise StructuredExtractionValidationError(
                "schema_not_object",
                "Structured extraction payload must be a JSON object.",
            )

        unknown_keys = sorted(set(payload.keys()) - _STRUCTURED_EXTRACTION_ALLOWED_FIELDS)
        if unknown_keys:
            raise StructuredExtractionValidationError(
                "schema_unknown_keys",
                f"Unknown structured extraction fields: {unknown_keys}",
            )

        validated: dict[str, Any] = {}
        for name in _STRUCTURED_EXTRACTION_BOOL_FIELDS:
            if name not in payload:
                continue
            value = payload.get(name)
            if not isinstance(value, bool):
                raise StructuredExtractionValidationError(
                    "schema_type_error",
                    f"Field '{name}' must be a boolean.",
                )
            validated[name] = value

        for name, max_len in _STRUCTURED_EXTRACTION_STR_FIELDS_MAX_LEN.items():
            if name not in payload:
                continue
            value = payload.get(name)
            if not isinstance(value, str):
                raise StructuredExtractionValidationError(
                    "schema_type_error",
                    f"Field '{name}' must be a string.",
                )
            cleaned = value.strip()
            if not cleaned:
                continue
            if len(cleaned) > max_len:
                raise StructuredExtractionValidationError(
                    "schema_length_error",
                    f"Field '{name}' exceeds max length {max_len}.",
                )
            validated[name] = cleaned

        if "catalog_subject" in validated and validated["catalog_subject"] not in _STRUCTURED_EXTRACTION_CATALOG_SUBJECT:
            raise StructuredExtractionValidationError(
                "schema_enum_error",
                "Field 'catalog_subject' must be 'program' or 'track'.",
            )
        if "email" in validated and not _STRUCTURED_EXTRACTION_EMAIL_RE.match(validated["email"]):
            raise StructuredExtractionValidationError(
                "schema_email_invalid",
                "Field 'email' has an invalid format.",
            )
        if "phone" in validated and not _STRUCTURED_EXTRACTION_PHONE_RE.match(validated["phone"]):
            raise StructuredExtractionValidationError(
                "schema_phone_invalid",
                "Field 'phone' has an invalid format.",
            )
        return validated

    def _mark_structured_extraction_status(
        self,
        *,
        session_state: Optional[Dict[str, Any]],
        success: bool,
        error_type: Optional[str],
        extraction_latency_ms: int,
    ) -> None:
        if session_state is None:
            return
        session_state["structured_extraction_success"] = bool(success)
        session_state["structured_extraction_error_type"] = str(error_type or "") or None
        session_state["extraction_latency_ms"] = max(0, int(extraction_latency_ms))
        fail_count = int(session_state.get("structured_extraction_fail_count") or 0)
        if success:
            session_state["structured_extraction_fail_count"] = 0
        else:
            session_state["structured_extraction_fail_count"] = fail_count + 1

    def _log_structured_extraction_result(
        self,
        *,
        session_state: Optional[Dict[str, Any]],
        success: bool,
        error_type: Optional[str],
        extraction_latency_ms: int,
    ) -> None:
        extra_fields = {
            "channel": (session_state or {}).get("channel"),
            "model": self.model,
            "structured_extraction_success": bool(success),
            "structured_extraction_error_type": str(error_type or "") or None,
            "structured_extraction_fail_count": int((session_state or {}).get("structured_extraction_fail_count") or 0),
            "extraction_latency_ms": max(0, int(extraction_latency_ms)),
        }
        if success:
            logger.info("LLM structured extraction completed", extra={"extra_fields": extra_fields})
            return
        logger.warning("LLM structured extraction completed", extra={"extra_fields": extra_fields})

    async def extract_structured_message(
        self,
        user_text: str,
        *,
        session_state: Optional[Dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        if self._auth_failed:
            return None
        if not settings.llm_structured_extraction_enabled or not self.is_configured():
            return None
        started_at_s = time.perf_counter()
        try:
            language_instruction = self._language_instruction(session_state)
            system_prompt = (
                "Extract structured conversation signals for an admissions assistant. "
                "Return only JSON with optional keys: "
                "is_affirmative, is_negative, catalog_request, details_request, appointment_request, urgency_request, "
                "gratitude_closure, catalog_subject, "
                "full_name, email, phone, appointment_date, appointment_time, admission_level, track_name, program_name. "
                "catalog_subject must be 'program' or 'track' when present. "
                "Do not invent values. Omit unknown fields."
            )
            if language_instruction:
                system_prompt += " " + language_instruction
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": (user_text or "").strip()},
                ],
                temperature=0.0,
                max_tokens=180,
                response_format={"type": "json_object"},
            )
            if not response or not response.choices or not response.choices[0].message:
                raise StructuredExtractionValidationError(
                    "empty_model_response",
                    "No structured extraction choice returned by model.",
                )
            if getattr(response.choices[0].message, "tool_calls", None):
                raise StructuredExtractionValidationError(
                    "unexpected_tool_call",
                    "Structured extraction must not include tool calls.",
                )
            content = ""
            content = response.choices[0].message.content or ""
            payload = self._safe_json_dict(content)
            if not payload:
                raise StructuredExtractionValidationError(
                    "invalid_json_response",
                    "Structured extraction returned non-JSON content.",
                )
            validated = self._validate_structured_extraction_payload(payload)
            extraction_latency_ms = int((time.perf_counter() - started_at_s) * 1000.0)
            self._mark_structured_extraction_status(
                session_state=session_state,
                success=True,
                error_type=None,
                extraction_latency_ms=extraction_latency_ms,
            )
            self._log_structured_extraction_result(
                session_state=session_state,
                success=True,
                error_type=None,
                extraction_latency_ms=extraction_latency_ms,
            )
            return validated or None
        except Exception as e:
            self._mark_provider_auth_error(e)
            extraction_latency_ms = int((time.perf_counter() - started_at_s) * 1000.0)
            error_type = getattr(e, "error_type", None) or e.__class__.__name__
            self._mark_structured_extraction_status(
                session_state=session_state,
                success=False,
                error_type=str(error_type),
                extraction_latency_ms=extraction_latency_ms,
            )
            self._log_structured_extraction_result(
                session_state=session_state,
                success=False,
                error_type=str(error_type),
                extraction_latency_ms=extraction_latency_ms,
            )
            logger.info(
                "LLM structured extraction failed_open",
                extra={
                    "extra_fields": {
                        "error": str(e),
                        "error_type": str(error_type),
                        "channel": (session_state or {}).get("channel"),
                        "model": self.model,
                    }
                },
            )
            return None

    async def rephrase_controlled_reply(
        self,
        *,
        reply_text: str,
        session_state: Optional[Dict[str, Any]] = None,
        response_contract: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        if self._auth_failed:
            return None
        if not settings.llm_deterministic_rephrase_enabled or not self.is_configured():
            return None
        source = (reply_text or "").strip()
        if not source:
            return None
        try:
            language_instruction = self._language_instruction(session_state)
            system_prompt = (
                "Rewrite an admissions assistant response. Preserve all facts, required fields, "
                "workflow step, and next actions exactly. Do not invent or remove information. "
                "Return only the rewritten final reply."
            )
            if language_instruction:
                system_prompt += " " + language_instruction
            user_prompt = (
                "Contract JSON:\n"
                + json.dumps(response_contract or {}, ensure_ascii=False)
                + "\n\nOriginal reply:\n"
                + source
            )
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=max(120, min(700, len(source) + 180)),
            )
            content = ""
            if response and response.choices and response.choices[0].message:
                content = (response.choices[0].message.content or "").strip()
            return content or None
        except Exception as e:
            self._mark_provider_auth_error(e)
            logger.warning(
                "LLM deterministic rephrase failed",
                extra={"extra_fields": {"error": str(e), "channel": (session_state or {}).get("channel")}},
            )
            return None

    def _language_instruction(self, session_state: Optional[Dict[str, Any]]) -> str:
        lang = (session_state or {}).get("lang_detected") or (session_state or {}).get("response_language")
        if lang == "fr":
            return "Réponds uniquement en français. N'utilise pas d'autres langues."
        if lang == "en":
            return "Reply only in English. Do not use other languages."
        if lang == "wo":
            return "Tontu ci wolof rekk. Bul jëfandikoo yeneen làkk."
        return ""

    def _channel_response_instruction(self, session_state: Optional[Dict[str, Any]]) -> str:
        channel = str((session_state or {}).get("channel") or "").strip().lower()
        if channel == "whatsapp":
            return (
                "Canal: WhatsApp. Reponds de facon mobile-friendly:\n"
                "- Phrases courtes (max 2-3 lignes par bloc)\n"
                "- Utilise des emojis avec parcimonie pour la lisibilite\n"
                "- Pas de longs paragraphes\n"
                "- Si tu listes des options, utilise des puces courtes\n"
                "- Max 3-4 messages courts plutot qu'un long message"
            )
        if channel == "sms":
            return (
                "Canal: SMS. Reponds de facon ultra-concise:\n"
                "- Maximum 160 caracteres si possible\n"
                "- Va droit au but, pas de formules de politesse longues\n"
                "- Si la reponse est longue, donne l'essentiel et propose de continuer par un autre canal"
            )
        if channel == "email":
            return (
                "Canal: Email. Reponds de facon professionnelle et structuree:\n"
                "- Commence par une salutation appropriee\n"
                "- Structure avec des paragraphes courts ou des puces\n"
                "- Termine par les prochaines etapes claires\n"
                "- Ton formel mais chaleureux"
            )
        if channel == "voice":
            return (
                "Canal: Vocal. Tu parles a voix haute, comme un humain au telephone:\n"
                "- Phrases courtes et naturelles, comme dans une vraie conversation\n"
                "- JAMAIS de numerotation (pas de '1.', '2.', 'premierement', 'deuxiemement')\n"
                "- JAMAIS de puces, tirets, gras ou formatage ecrit\n"
                "- Pour lister des programmes ou options, cite-les naturellement dans une phrase fluide "
                "(ex: 'Nous avons le BTS Informatique, la Licence Pro Marketing et le Master Finance')\n"
                "- Max 2-3 phrases courtes par reponse\n"
                "- Parle comme une conseillere bienveillante, pas comme un robot qui lit une liste"
            )
        return "Reste concis, clair et directement utile. Max 3-4 phrases par reponse."

    def _conversation_slots_from_state(self, session_state: Optional[Dict[str, Any]]) -> dict[str, Any]:
        if not isinstance(session_state, dict):
            return {}
        raw_slots = session_state.get("conversation_slots")
        if isinstance(raw_slots, dict):
            return raw_slots
        raw_slots = session_state.get("slots_json")
        if isinstance(raw_slots, dict):
            return raw_slots
        return {}

    @staticmethod
    def _is_recommendation_request_text(user_text: str) -> bool:
        normalized = (user_text or "").strip().lower()
        return any(
            marker in normalized
            for marker in {
                "laquelle est mieux",
                "lequel est mieux",
                "quelle est la meilleure",
                "quel est le meilleur",
                "meilleure",
                "meilleur",
                "mieux",
                "best",
                "which is better",
                "which one is better",
                "recommend",
                "recommande",
                "conseille",
                "orienter",
            }
        )

    def _comparison_options_from_state(self, session_state: Optional[Dict[str, Any]]) -> list[dict[str, Any]]:
        slots = self._conversation_slots_from_state(session_state)
        raw_options = slots.get("last_catalog_options")
        if isinstance(raw_options, list):
            items = [item for item in raw_options if isinstance(item, dict)]
            if items:
                return items[:4]
        raw_options = slots.get("pending_track_options")
        if isinstance(raw_options, list):
            items = [item for item in raw_options if isinstance(item, dict)]
            if items:
                return items[:4]
        if str(slots.get("track_name") or "").strip():
            return [
                {
                    "track_name": str(slots.get("track_name") or "").strip(),
                    "program_name": str(slots.get("program_name") or "").strip(),
                    "access_level": str(slots.get("access_level") or slots.get("admission_level") or "").strip(),
                    "annual_fee": slots.get("annual_fee"),
                    "monthly_fee": slots.get("monthly_fee"),
                    "delivery_mode": str(slots.get("delivery_mode") or "").strip(),
                    "certifications": str(slots.get("certifications") or "").strip(),
                }
            ]
        return []

    def _comparison_context_block(self, session_state: Optional[Dict[str, Any]]) -> str:
        options = self._comparison_options_from_state(session_state)
        if not options:
            return ""
        lines: list[str] = []
        for index, item in enumerate(options[:4], start=1):
            track_name = str(item.get("track_name") or "").strip() or "Filiere"
            program_name = str(item.get("program_name") or "").strip()
            access_level = str(item.get("access_level") or "").strip()
            label_parts = [track_name]
            if program_name:
                label_parts.append(f"programme={program_name}")
            if access_level:
                label_parts.append(f"niveau={access_level}")
            details: list[str] = []
            if item.get("annual_fee") not in (None, ""):
                details.append(f"frais_annuels={item.get('annual_fee')}")
            if item.get("monthly_fee") not in (None, ""):
                details.append(f"mensualite={item.get('monthly_fee')}")
            delivery_mode = str(item.get("delivery_mode") or "").strip()
            if delivery_mode:
                details.append(f"modalite={delivery_mode}")
            certifications = str(item.get("certifications") or "").strip()
            if certifications:
                details.append(f"certifications={certifications}")
            suffix = " | " + " | ".join(details) if details else ""
            lines.append(f"{index}. {' | '.join(label_parts)}{suffix}")
        return "Options disponibles pour comparaison:\n" + "\n".join(lines)

    def _fallback_recommendation_message(self, session_state: Optional[Dict[str, Any]]) -> Optional[str]:
        options = self._comparison_options_from_state(session_state)
        if not options:
            return None
        lang = (session_state or {}).get("lang_detected") or (session_state or {}).get("response_language") or "fr"
        lines: list[str] = []
        for item in options[:3]:
            label_parts = [str(item.get("track_name") or "").strip() or "Filiere"]
            program_name = str(item.get("program_name") or "").strip()
            access_level = str(item.get("access_level") or "").strip()
            if program_name:
                label_parts.append(program_name)
            if access_level:
                label_parts.append(access_level)
            criteria: list[str] = []
            if item.get("monthly_fee") not in (None, "", 0):
                criteria.append(f"{item.get('monthly_fee')} F CFA/mois")
            delivery_mode = str(item.get("delivery_mode") or "").strip()
            if delivery_mode:
                criteria.append(delivery_mode)
            suffix = f" ({', '.join(criteria[:2])})" if criteria else ""
            lines.append(f"- {' / '.join(label_parts)}{suffix}")
        if lang == "en":
            return (
                "There is no universally best option. The right choice depends on your goal, entry level, and budget.\n"
                + "\n".join(lines)
                + "\n\nTell me your priority and I will narrow the best fit safely."
            )
        if lang == "wo":
            return (
                "Amul benn option bu gën ci yépp. Li gën a baax dafay sukkandiku ci sa objectif, niveau, ak budget.\n"
                + "\n".join(lines)
                + "\n\nWax ma sa priorité, ma jublu la ci option bi gën a méngoo."
            )
        return (
            "Il n'existe pas de meilleure option universelle. Le bon choix depend de votre objectif, de votre niveau et de votre budget.\n"
            + "\n".join(lines)
            + "\n\nDites-moi votre priorite et je vous orienterai plus precisement."
        )

    def _fallback_message(self, session_state: Optional[Dict[str, Any]] = None) -> str:
        channel = (session_state or {}).get("channel", "")
        lang = (session_state or {}).get("lang_detected")
        if str((session_state or {}).get("pending_open_intent") or "").strip() == "recommendation_request":
            recommendation_fallback = self._fallback_recommendation_message(session_state)
            if recommendation_fallback:
                return recommendation_fallback
        if lang == "unknown":
            return unsupported_language_message()
        if lang == "en":
            if channel == "email":
                return (
                    "Thanks for your email. We are experiencing a temporary technical issue. "
                    "Could you share your target program, admission level, and preferred contact? "
                    "An admissions advisor will contact you shortly."
                )
            return (
                "We are experiencing a temporary technical issue. "
                "Could you share your target program, admission level, and preferred contact? "
                "I can also propose an admissions appointment quickly."
            )
        if lang == "wo":
            if channel == "email":
                return (
                    "Jërëjëf ci sa imeel. Am na jafe-jafe bu tuuti. "
                    "Wax ma filiere bi nga bëgg, niveau bi, ak ni nu la wara jokkoo. "
                    "Ab conseiller admissions dina la waxaat."
                )
            return (
                "Am na jafe-jafe bu tuuti. "
                "Wax ma filiere bi nga bëgg, niveau bi, ak ni nu la wara jokkoo. "
                "Dinaa la jappal."
            )
        if channel == "email":
            return (
                "Merci pour votre email. Nous rencontrons un souci technique temporaire. "
                "Pouvez-vous preciser la filiere visee, le niveau d'admission et votre contact ? "
                "Un conseiller admission vous recontacte rapidement."
            )
        return (
            "Je rencontre un souci technique temporaire. "
            "Pouvez-vous preciser la filiere visee, le niveau d'admission et votre contact ? "
            "Je peux aussi proposer un rendez-vous admission rapidement."
        )

    def _mutating_tool_guard(self, session_state: Optional[Dict[str, Any]]) -> tuple[bool, str]:
        if not isinstance(session_state, dict):
            return False, "mutating_tools_disabled"
        if not bool(session_state.get("allow_mutating_tools")):
            return False, "mutating_tools_disabled"
        if str(session_state.get("tool_mutation_scope") or "").strip().lower() != "booking":
            return False, "mutating_tools_scope_missing"

        active_flow = str(
            session_state.get("conversation_active_flow")
            or session_state.get("active_flow")
            or ""
        ).strip().lower()
        if active_flow != "booking_confirm":
            return False, "mutating_tools_disallowed_flow"

        response_strategy = str(session_state.get("response_strategy") or "").strip().lower()
        if response_strategy != "deterministic_booking_confirm":
            return False, "mutating_tools_missing_deterministic_confirmation"

        if not bool(session_state.get("booking_confirmation_obtained")):
            return False, "mutating_tools_missing_confirmation"
        if not bool(session_state.get("personal_data_consent_obtained")):
            return False, "mutating_tools_missing_consent"

        slots = self._conversation_slots_from_state(session_state)
        has_contact = bool(
            str(session_state.get("person_id") or "").strip()
            or str(slots.get("person_id") or "").strip()
            or str(slots.get("email") or "").strip()
            or str(slots.get("phone") or "").strip()
        )
        if not has_contact:
            return False, "mutating_tools_missing_contact"

        has_track = bool(
            str(slots.get("track_id") or "").strip()
            or str(slots.get("track_name") or "").strip()
            or str(slots.get("program_name") or "").strip()
        )
        if not has_track:
            return False, "mutating_tools_missing_track"

        has_datetime = bool(
            str(slots.get("appointment_date") or "").strip()
            and str(slots.get("appointment_time") or "").strip()
        )
        if not has_datetime:
            return False, "mutating_tools_missing_datetime"

        return True, "allowed"

    def _resolve_tool_policy(self, session_state: Optional[Dict[str, Any]]) -> tuple[list[dict[str, Any]], set[str], dict[str, str]]:
        allowed_names = set(READ_ONLY_TOOL_NAMES)
        blocked_reasons: dict[str, str] = {}

        write_allowed, write_reason = self._mutating_tool_guard(session_state)
        if write_allowed:
            allowed_names.update(MUTATING_TOOL_NAMES)
        else:
            for tool_name in MUTATING_TOOL_NAMES:
                blocked_reasons[tool_name] = write_reason

        advertised_tools = [
            self._tool_specs_by_name[name]
            for name in self._tool_specs_by_name
            if name in allowed_names
        ]
        return advertised_tools, allowed_names, blocked_reasons

    def is_configured(self) -> bool:
        return bool(self.api_key and self._client)

    def _track_usage(self, response: Any) -> None:
        """Extract and accumulate token usage from an OpenAI response."""
        usage = getattr(response, "usage", None)
        if usage:
            self.last_prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            self.last_completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            self.last_total_tokens = self.last_prompt_tokens + self.last_completion_tokens
            self.session_total_prompt_tokens += self.last_prompt_tokens
            self.session_total_completion_tokens += self.last_completion_tokens

    def _resolve_tenant_scope(
        self,
        *,
        session_state: Optional[Dict[str, Any]] = None,
        db_session: Optional[Any] = None,
    ) -> str:
        if db_session is not None:
            tenant_id = getattr(db_session, "info", {}).get("tenant_id")
            if tenant_id:
                return str(tenant_id)
        if session_state:
            tenant_id = session_state.get("tenant_id")
            if tenant_id:
                try:
                    UUID(str(tenant_id))
                    return str(tenant_id)
                except Exception:
                    return str(settings.default_tenant_id)
        return str(settings.default_tenant_id)

    def _load_persona_blocks(
        self,
        *,
        tenant_scope: str,
        db_session: Optional[Any] = None,
    ) -> tuple[str, str]:
        extra_persona = ""
        extra_args_block = ""
        db = db_session
        close_after = False
        if db is None:
            db = open_db_session(tenant_scope)
            close_after = True
        try:
            persona_doc = docs_service.get_document_by_tag(db, "persona_core")
            if persona_doc and persona_doc.content:
                extra_persona = persona_doc.content
            args_doc = docs_service.get_document_by_tag(db, "arguments_commerciaux")
            if args_doc and args_doc.content:
                extra_args_block = args_doc.content
        except Exception:
            pass
        finally:
            if close_after:
                try:
                    db.close()
                except Exception:
                    pass
        return extra_persona, extra_args_block

    def _build_clean_context(self, session_state: Optional[Dict[str, Any]]) -> str:
        """Build a clean, structured context string instead of dumping raw JSON."""
        if not session_state:
            return ""
        parts: list[str] = []

        # 1. Channel & language
        channel = str(session_state.get("channel") or "").strip()
        lang = str(session_state.get("response_language") or session_state.get("lang_detected") or "fr")
        if channel:
            parts.append(f"Canal: {channel}")
        parts.append(f"Langue: {lang}")

        # 2. Active flow
        flow = str(session_state.get("conversation_active_flow") or session_state.get("active_flow") or "browsing_catalog")
        parts.append(f"Étape conversation: {flow}")

        # 3. Memory slots (only useful ones)
        slots = session_state.get("conversation_slots") or session_state.get("slots_json") or {}
        if isinstance(slots, dict):
            useful_keys = [
                "full_name", "first_name", "last_name", "email", "phone",
                "track_name", "program_name", "admission_level",
                "appointment_date", "appointment_time", "preferred_language",
            ]
            slot_parts = []
            for key in useful_keys:
                val = slots.get(key)
                if val not in (None, "", []):
                    slot_parts.append(f"  {key}: {val}")
            if slot_parts:
                parts.append("Informations collectées:\n" + "\n".join(slot_parts))

        # 4. Session summary (compact)
        summary = str(session_state.get("session_summary") or session_state.get("summary_memory") or "").strip()
        if summary:
            parts.append(f"Résumé session: {summary}")

        # 5. Handoff status
        if session_state.get("handoff_allowed"):
            reason = session_state.get("handoff_trigger_reason") or ""
            parts.append(f"Escalade autorisée (raison: {reason})")

        return "\n".join(parts)

    def _build_conversation_history_messages(self, session_state: Optional[Dict[str, Any]]) -> list[dict[str, str]]:
        """Extract recent_turns from session_state and format as proper multi-turn messages."""
        if not session_state:
            return []
        recent_turns = session_state.get("recent_turns")
        if not isinstance(recent_turns, list):
            return []
        history_messages: list[dict[str, str]] = []
        for turn in recent_turns:
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("role") or "").strip().lower()
            content = str(turn.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                history_messages.append({"role": role, "content": content})
        return history_messages

    async def generate_reply_with_tools(
        self,
        user_text: str,
        session_state: Optional[Dict[str, Any]] = None,
        db_session: Optional[Any] = None
    ) -> str:
        """Génère une réponse avec support des function calls.

        Args:
            user_text: Message de l'utilisateur
            session_state: État de la session
            db_session: Session de base de données pour les function calls

        Returns:
            Réponse générée par l'IA (avec exécution des functions si nécessaire)
        """
        self.last_error = None
        self.last_tool_calls = []
        self.last_fallback_reason = None
        self.last_knowledge_sources = None
        safe_state: Dict[str, Any] = dict(session_state or {})
        lang_code = (safe_state.get("lang_detected") or safe_state.get("response_language") or "fr")
        failure_count = int(safe_state.get("failure_count") or 0)
        intent_decision = detect_intent(user_text or "", lang=str(lang_code), failure_count=failure_count)
        safe_state["intent_detected"] = intent_decision.intent
        safe_state["intent_score"] = float(intent_decision.score)
        safe_state["intent_action"] = intent_decision.action
        safe_state["intent_keywords"] = intent_decision.matched_keywords
        if session_state is not None:
            session_state.update(safe_state)
        tenant_scope = self._resolve_tenant_scope(session_state=safe_state, db_session=db_session)
        safe_state.setdefault("tenant_id", tenant_scope)

        logger.info(
            "Intent decision",
            extra={
                "extra_fields": {
                    "intent": intent_decision.intent,
                    "score": intent_decision.score,
                    "action": intent_decision.action,
                    "channel": safe_state.get("channel"),
                    "lang": lang_code,
                }
            },
        )

        if intent_decision.action == "escalate_human":
            return escalation_message(str(lang_code))
        if intent_decision.action == "ask_clarification" and (len((user_text or "").strip()) < 4):
            return clarification_message(str(lang_code))

        if self._auth_failed:
            self.last_fallback_reason = "llm_provider_auth_error"
            return self._fallback_message(safe_state)
        if not self.is_configured():
            logger.warning("LLM not configured; using fallback reply", extra={"extra_fields": {"channel": safe_state.get("channel")}})
            self.last_fallback_reason = "llm_not_configured"
            return self._fallback_message(safe_state)

        try:
            from .llm_tools import execute_function_call
            advertised_tools, allowed_tool_names, blocked_tool_reasons = self._resolve_tool_policy(safe_state)

            # Construire le contexte
            safe_user_text = (user_text or "").strip()

            # --- Build clean structured context instead of raw JSON dump ---
            ctx_raw = self._build_clean_context(safe_state)

            # Récupération persona et arguments
            extra_persona, extra_args_block = self._load_persona_blocks(
                tenant_scope=tenant_scope,
                db_session=db_session,
            )

            system_prompt = SYSTEM_PERSONA
            if extra_persona:
                system_prompt += f"\n\n{extra_persona}"
            if extra_args_block:
                system_prompt += f"\n\n{extra_args_block}"
            system_prompt += f"\n\n{TERMINOLOGY_RULES}"
            channel_instruction = self._channel_response_instruction(safe_state)
            if channel_instruction:
                system_prompt += f"\n\n{channel_instruction}"
            knowledge_block = ""
            if db_session is not None:
                try:
                    knowledge_context = resolve_knowledge_context(
                        db_session,
                        user_text=(user_text or "").strip(),
                        session_state=safe_state,
                    )
                except Exception:
                    knowledge_context = None
                if knowledge_context:
                    self.last_knowledge_sources = knowledge_context.source_summary()
                    if session_state is not None:
                        session_state["knowledge_source_summary"] = self.last_knowledge_sources
                    knowledge_block = knowledge_context.to_prompt_block()
            if knowledge_block:
                system_prompt += (
                    "\n\nKnowledge resolution order:\n"
                    "1. STRUCTURED_TRUTH / AUTHORITATIVE_FACTS\n"
                    "2. CURATED_FAQ_SNIPPETS\n"
                    "3. RETRIEVAL_SUPPORT\n"
                    "- For critical facts (programs, tuition, admission requirements, policies, deadlines, calendar facts), use only STRUCTURED_TRUTH / AUTHORITATIVE_FACTS.\n"
                    "- FAQ snippets can explain or clarify, but they must never override authoritative facts.\n"
                    "- RETRIEVAL_SUPPORT is only supporting context for long-form explanations.\n"
                    "- If authoritative facts are missing for a critical question and no tool can confirm them, do not invent them: ask for clarification or offer human escalation.\n\n"
                    + knowledge_block
                )
            language_instruction = self._language_instruction(safe_state)
            if language_instruction:
                system_prompt += f"\n\n{language_instruction}"
            if self._is_recommendation_request_text(safe_user_text) or str(safe_state.get("pending_open_intent") or "") == "recommendation_request":
                system_prompt += f"\n\n{RECOMMENDATION_RULES}"
                comparison_context = self._comparison_context_block(safe_state)
                if comparison_context:
                    system_prompt += f"\n\n{comparison_context}"
            system_prompt += (
                "\n\nIntent engine (deterministic): "
                f"intent={intent_decision.intent}, score={intent_decision.score}, action={intent_decision.action}. "
                "Respect this routing decision in your response."
            )
            system_prompt += (
                "\n\nRègles outils:"
                "\n- Les outils de lecture peuvent servir à vérifier des faits."
                "\n- N'utilise jamais un outil de création ou de mise à jour sans confirmation explicite, consentement explicite,"
                " et autorisation de l'état de session."
                "\n- Si un outil d'écriture n'est pas disponible, collecte les informations manquantes ou reste dans la réponse conversationnelle."
            )

            # Premier appel avec tools
            safe_ctx = sanitize_for_llm(ctx_raw, mode="normal") if ctx_raw else ""

            # Build messages with proper multi-turn conversation history
            messages = [{"role": "system", "content": system_prompt}]

            # Inject structured context as a system message (not mixed with user text)
            if safe_ctx:
                messages.append({"role": "system", "content": f"Contexte de la conversation:\n{safe_ctx}"})

            # Inject conversation history as proper multi-turn messages
            history_messages = self._build_conversation_history_messages(safe_state)
            if history_messages:
                messages.extend(history_messages)

            # Current user message (clean, without context dump)
            messages.append({"role": "user", "content": safe_user_text})

            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=advertised_tools,
                tool_choice="auto",
                parallel_tool_calls=False,
                temperature=0.2,
                max_tokens=500
            )

            assistant_message = response.choices[0].message
            self._track_usage(response)

            # Vérifier si l'IA veut appeler des fonctions
            if assistant_message.tool_calls:
                self.last_tool_calls = [
                    str(tc.function.name or "")
                    for tc in assistant_message.tool_calls
                    if getattr(getattr(tc, "function", None), "name", None)
                ]
                # Exécuter les function calls
                messages.append(assistant_message)

                for tool_call in assistant_message.tool_calls:
                    function_name = tool_call.function.name
                    function_args = json.loads(tool_call.function.arguments)

                    # Exécuter la fonction
                    if db_session:
                        function_result = await execute_function_call(
                            db_session,
                            function_name,
                            function_args,
                            allowed_function_names=allowed_tool_names,
                        )
                    else:
                        function_result = {"success": False, "error": "Pas de session DB disponible"}
                    if function_result.get("error") == "tool_not_allowed":
                        logger.warning(
                            "llm_tool_call_blocked",
                            extra={
                                "extra_fields": {
                                    "tool_name": function_name,
                                    "reason": blocked_tool_reasons.get(function_name) or "tool_not_allowed",
                                    "channel": safe_state.get("channel"),
                                    "active_flow": safe_state.get("conversation_active_flow"),
                                    "response_strategy": safe_state.get("response_strategy"),
                                }
                            },
                        )

                    # Ajouter le résultat aux messages
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": function_name,
                        "content": json.dumps(function_result, ensure_ascii=False)
                    })

                # Deuxième appel pour obtenir la réponse finale
                # Note: parallel_tool_calls is only valid when tools are provided
                final_response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.2,
                    max_tokens=500
                )

                self._track_usage(final_response)
                return final_response.choices[0].message.content.strip()

            # Pas de function call, retourner la réponse directe
            return assistant_message.content.strip()

        except Exception as e:
            self._mark_provider_auth_error(e)
            self.last_error = str(e)
            self.last_fallback_reason = "llm_provider_error"
            logger.error(f"LLM tool-call flow failed: {e}", exc_info=True)
            return self._fallback_message(safe_state)

    async def generate_reply(self, user_text: str, session_state: Optional[Dict[str, Any]] = None) -> str:
        if self._auth_failed:
            self.last_fallback_reason = "llm_provider_auth_error"
            return self._fallback_message(session_state)
        if self.is_configured():
            try:
                ctx_raw = self._build_clean_context(session_state) if session_state else ""
                tenant_scope = self._resolve_tenant_scope(session_state=session_state, db_session=None)

                # Récupération éventuelle de blocs persona / argumentaire depuis la base documents
                extra_persona, extra_args_block = self._load_persona_blocks(
                    tenant_scope=tenant_scope,
                    db_session=None,
                )

                system_persona = SYSTEM_PERSONA
                if extra_persona:
                    system_persona = system_persona + "\n\n---\nPersona complémentaire (BDD) :\n" + extra_persona

                args = get_arguments(session_state or {})
                args_text = "".join([f"- {a}\n" for a in args]) if args else ""
                arg_system_parts = []
                if args_text:
                    arg_system_parts.append(
                        "Voici quelques arguments commerciaux que tu peux utiliser si pertinents (ne les récite pas tels quels, "
                        "mais intègre-les naturellement dans la discussion) :\n" + args_text
                    )
                if extra_args_block:
                    arg_system_parts.append(
                        "Bloc d'argumentaire métier issu de la base de connaissance (intègre-le de façon naturelle, sans le réciter tel quel) :\n"
                        + extra_args_block
                    )
                arg_system = "\n\n".join(arg_system_parts) if arg_system_parts else ""

                # Sanitization RGPD des contenus envoyés au LLM
                mode = "strict"
                safe_user_text = sanitize_for_llm(user_text, mode=mode)
                safe_ctx = sanitize_for_llm(ctx_raw, mode=mode) if ctx_raw else ""
                safe_arg_system = sanitize_for_llm(arg_system, mode=mode) if arg_system else ""
                language_instruction = self._language_instruction(session_state)
                messages = [{"role": "system", "content": system_persona}]
                if language_instruction:
                    messages.append({"role": "system", "content": language_instruction})
                if safe_ctx:
                    messages.append({"role": "system", "content": f"Contexte de la conversation:\n{safe_ctx}"})
                if safe_arg_system:
                    messages.append({"role": "system", "content": safe_arg_system})

                # Inject conversation history as proper multi-turn messages
                history_messages = self._build_conversation_history_messages(session_state)
                if history_messages:
                    messages.extend(history_messages)

                messages.append({"role": "user", "content": safe_user_text})

                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.4,
                )
                msg = resp.choices[0].message.content if resp and resp.choices else None
                if isinstance(msg, str) and msg.strip():
                    self.last_error = None
                    return msg.strip()
            except Exception as e:
                self._mark_provider_auth_error(e)
                self.last_error = str(e)
                logger.error(f"LLM reply generation failed: {e}", exc_info=True)
        else:
            logger.warning("LLM not configured; using fallback reply", extra={"extra_fields": {"channel": (session_state or {}).get("channel")}})
        return self._fallback_message(session_state)

    async def embed_text(self, text: str) -> list[float]:
        """Retourne un embedding pour un texte donné.

        V1 : utilise les embeddings OpenAI si disponibles, sinon retourne un vecteur bidon
        de petite dimension (utile uniquement pour tests locaux sans config OpenAI).
        """
        # Fallback simple si OpenAI n'est pas configuré
        if not self.is_configured() or not OpenAI:
            if not self._warned_embed_fallback:
                logger.warning("Embedding fallback active (OpenAI not configured)")
                self._warned_embed_fallback = True
            # vecteur fixe basé sur le hash du texte, pour garder un ordre déterministe
            h = abs(hash(text))
            return [float((h >> (8 * i)) & 0xFF) / 255.0 for i in range(32)]

        try:
            client = self._client
            # type: ignore[attr-defined]
            resp = client.embeddings.create(
                model="text-embedding-3-small",
                input=text,
            )
            vec = resp.data[0].embedding if resp and resp.data else None
            if isinstance(vec, list) and vec:
                return vec  # type: ignore[return-value]
        except Exception as e:
            self.last_error = str(e)

        # Dernier fallback si l'appel embeddings échoue
        h = abs(hash(text))
        return [float((h >> (8 * i)) & 0xFF) / 255.0 for i in range(32)]
