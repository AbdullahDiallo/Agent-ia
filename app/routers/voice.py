from fastapi import APIRouter, WebSocket, Request, HTTPException, WebSocketDisconnect
from fastapi.responses import Response
from ..config import settings
import asyncio
import audioop
import json
import base64
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional
from xml.sax.saxutils import escape as xml_escape
from sqlalchemy.orm import Session
from ..db import open_db_session
from ..services.channel_agent_pipeline import ChannelAgentPipeline
from ..services.stt import STTService
from ..services.tts import TTSService
from ..services.lang import detect_language
from ..services.security_controls import get_emergency_state
from ..services.llm_tools import handle_create_or_get_person, handle_get_track_tuition
from ..services.call_handoff import transfer_call_to_human_agents
from ..audio import wav_to_mulaw8k_frames, frames_to_base64_payloads
from ..logger import get_logger
import time
import jwt
import uuid
from contextlib import suppress
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant
from ..security import verify_jwt
from ..services.webhook_security import verify_webhook
try:
    from twilio.rest import Client as TwilioRestClient
    from twilio.base.exceptions import TwilioRestException
except Exception:
    TwilioRestClient = None  # type: ignore
    TwilioRestException = Exception  # type: ignore

router = APIRouter(tags=["voice"])
logger = get_logger(__name__)

_GREETING_ONLY_RE = re.compile(
    r"^\s*(bonjour|bonsoir|salut|allo|all[oô]|hello|hi|hey|coucou)\s*[!.?]?\s*$",
    re.IGNORECASE,
)
_GREETING_TOKENS = {
    "bonjour", "bonsoir", "salut", "allo", "alloô", "hello", "hi", "hey", "coucou"
}

_STT_MIN_AUDIO_DURATION_MS = 500
_STT_SILENCE_THRESHOLD_MS = 500
_STT_SPEECH_TIMEOUT_MS = 7000
_STT_PRE_SPEECH_TIMEOUT_MS = 3500
_STT_SILENCE_RMS_THRESHOLD = 90
_STT_STREAMING_ENDPOINTING_MS = 350
_STT_DYNAMIC_NOISE_MARGIN_RMS = 65
_STT_SPEECH_START_MIN_CHUNKS = 2
_STT_STREAM_RECOVERY_MAX_RETRIES = 2
_STT_STREAM_RECOVERY_COOLDOWN_S = 1.5
_EMPTY_TRANSCRIPT_REPROMPT_STREAK = 3
_EMPTY_REPROMPT_COOLDOWN_S = 8.0
_EMPTY_REPROMPT_MAX_PER_CALL = 2
_VOICE_FRAME_PACING_S = 0.020

_VOICE_WELCOME_PROMPT = (
    "Bonjour, je suis Salma, comment puis-je vous aider ?"
)
_VOICE_GREETING_PROMPTS = {
    "fr": "Je vous ecoute. Vous pouvez demander un programme, les frais, l'admission ou un rendez-vous.",
    "en": "I'm listening. You can ask about programs, tuition, admission, or an appointment.",
    "wo": "Mangi deglu. Mën nga laaj ci programmes, frais, admission walla rendez-vous.",
}
_VOICE_EMPTY_REPROMPTS = {
    "fr": "Je vous entends mal. Parlez plus pres du micro, puis faites une courte pause.",
    "en": "I could not hear you clearly. Please speak closer to the microphone, then pause briefly.",
    "wo": "Duma la dégg bu baax. Waxal gannaaw mikro bi te nga taxaw tuuti.",
}
_VOICE_TECHNICAL_FALLBACKS = {
    "fr": "Je rencontre un souci technique temporaire. Pouvez-vous reformuler votre demande ?",
    "en": "I'm having a temporary technical issue. Could you rephrase your request?",
    "wo": "Am na jafe-jafe bu tuuti. Mën nga waxaat sa laaj?",
}
_VOICE_TRANSFER_REASONS = {
    "fr": "Je vais vous transferer vers un conseiller admissions humain pour mieux vous aider.",
    "en": "I will transfer you to a human admissions advisor for better assistance.",
    "wo": "Dinaa la jox benn conseiller admissions ngir mu gën la dimbali.",
}


def _normalize_voice_lang(value: Optional[str], *, fallback: str = "fr") -> str:
    lang = str(value or "").strip().lower()
    if lang in {"fr", "en", "wo"}:
        return lang
    return fallback


def _detect_voice_lang(text: str, *, fallback: str = "fr") -> str:
    try:
        detected = detect_language(text or "")
    except Exception:
        detected = "unknown"
    return _normalize_voice_lang(detected, fallback=fallback)


def _voice_prompt(prompt_map: dict[str, str], *, lang: str) -> str:
    return str(prompt_map.get(_normalize_voice_lang(lang)) or prompt_map["fr"])


def _twilio_say_language(lang: str) -> str:
    normalized = _normalize_voice_lang(lang)
    if normalized == "en":
        return "en-US"
    return "fr-FR"


def _stt_language_for_voice(lang: str) -> str:
    normalized = _normalize_voice_lang(lang)
    if normalized == "en":
        return "en"
    return "fr"


def _voice_reply_budget(response_strategy: str) -> tuple[int, int]:
    normalized = str(response_strategy or "").strip().lower()
    if normalized.startswith("deterministic_catalog"):
        return 200, 2
    if normalized.startswith("fallback"):
        return 200, 2
    if normalized.startswith("deterministic_booking"):
        return 250, 3
    if normalized.startswith("deterministic_track_details"):
        return 220, 3
    return 250, 3


def _compact_voice_reply(reply_text: str, *, max_chars: int = 200, max_sentences: int = 2) -> str:
    raw = str(reply_text or "").strip()
    if not raw:
        return ""
    normalized = raw.replace("\n", " ").replace("•", ", ").replace(";", ", ").replace(" - ", ", ")
    # Strip numbered list prefixes (e.g. "1. ", "2) ", "- ") to sound natural
    normalized = re.sub(r"(?:^|\s)(\d+[\.\)]\s*)", " ", normalized)
    normalized = re.sub(r"(?:^|\s)[-–—]\s+", " ", normalized)
    # Strip markdown bold/italic
    normalized = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", normalized)
    # Strip "Frais annuels :" / "Frais d'inscription :" label prefixes for spoken flow
    normalized = re.sub(r"Frais\s+(?:annuels|d'inscription|inscription)\s*:\s*", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"Mensualite\s*:\s*", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"Modalite\s*:\s*", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"Certifications\s*:\s*", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    # Remove long parenthetical asides (not natural in speech)
    normalized = re.sub(r"\(([^)]{40,})\)", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", normalized) if part.strip()]
    compact = " ".join(sentences[:max(1, max_sentences)]).strip() if sentences else normalized
    if len(compact) > max_chars:
        compact = compact[: max_chars - 3].rstrip(" ,;:.") + "..."
    elif len(compact) < len(normalized):
        compact = compact.rstrip(". ") + "."
    return compact


@dataclass
class BufferedTurnAccumulator:
    min_audio_duration_ms: int = _STT_MIN_AUDIO_DURATION_MS
    silence_threshold_ms: int = _STT_SILENCE_THRESHOLD_MS
    speech_timeout_ms: int = _STT_SPEECH_TIMEOUT_MS
    pre_speech_timeout_ms: int = _STT_PRE_SPEECH_TIMEOUT_MS
    silence_rms_threshold: int = _STT_SILENCE_RMS_THRESHOLD
    dynamic_noise_margin_rms: int = _STT_DYNAMIC_NOISE_MARGIN_RMS
    speech_start_min_chunks: int = _STT_SPEECH_START_MIN_CHUNKS
    sample_rate: int = 8000

    def __post_init__(self) -> None:
        self.audio_buf = bytearray()
        self.audio_bytes_buffered = 0
        self.turn_started_at_s: Optional[float] = None
        self.last_voice_at_s: Optional[float] = None
        self.voice_detected = False
        self._noise_floor_rms = 0.0
        self._speech_chunk_streak = 0

    def reset(self) -> None:
        self.audio_buf.clear()
        self.audio_bytes_buffered = 0
        self.turn_started_at_s = None
        self.last_voice_at_s = None
        self.voice_detected = False
        self._noise_floor_rms = 0.0
        self._speech_chunk_streak = 0

    def has_audio(self) -> bool:
        return self.audio_bytes_buffered > 0

    def audio_duration_ms(self) -> int:
        if self.audio_bytes_buffered <= 0:
            return 0
        return int((self.audio_bytes_buffered / float(self.sample_rate)) * 1000.0)

    @staticmethod
    def _chunk_rms(chunk: bytes) -> int:
        if not chunk:
            return 0
        try:
            pcm16 = audioop.ulaw2lin(chunk, 2)
            return int(audioop.rms(pcm16, 2))
        except Exception:
            return 0

    def is_speech_chunk(self, chunk: bytes) -> bool:
        rms = self._chunk_rms(chunk)
        if rms <= 0:
            self._speech_chunk_streak = 0
            return False
        if not self.voice_detected:
            if rms < self.silence_rms_threshold:
                if self._noise_floor_rms <= 0.0:
                    self._noise_floor_rms = float(rms)
                else:
                    self._noise_floor_rms = (self._noise_floor_rms * 0.85) + (float(rms) * 0.15)
            dynamic_threshold = max(
                self.silence_rms_threshold,
                int(self._noise_floor_rms) + self.dynamic_noise_margin_rms,
            )
            is_speech = rms >= dynamic_threshold
        else:
            hold_threshold = max(60, min(self.silence_rms_threshold, int(self._noise_floor_rms) + 45))
            is_speech = rms >= hold_threshold
        if is_speech:
            self._speech_chunk_streak += 1
        else:
            self._speech_chunk_streak = 0
        return is_speech

    def peek_speech_chunk(self, chunk: bytes) -> bool:
        rms = self._chunk_rms(chunk)
        if rms <= 0:
            return False
        if not self.voice_detected:
            candidate_noise_floor = self._noise_floor_rms
            if rms < self.silence_rms_threshold:
                if candidate_noise_floor <= 0.0:
                    candidate_noise_floor = float(rms)
                else:
                    candidate_noise_floor = (candidate_noise_floor * 0.85) + (float(rms) * 0.15)
            dynamic_threshold = max(
                self.silence_rms_threshold,
                int(candidate_noise_floor) + self.dynamic_noise_margin_rms,
            )
            return rms >= dynamic_threshold
        hold_threshold = max(60, min(self.silence_rms_threshold, int(self._noise_floor_rms) + 45))
        return rms >= hold_threshold

    def ingest(self, chunk: bytes, *, now_s: Optional[float] = None) -> Optional[str]:
        now = float(now_s if now_s is not None else time.perf_counter())
        if self.turn_started_at_s is None:
            self.turn_started_at_s = now
        self.audio_buf.extend(chunk)
        self.audio_bytes_buffered += len(chunk)

        if self.is_speech_chunk(chunk):
            if self.voice_detected or self._speech_chunk_streak >= self.speech_start_min_chunks:
                self.voice_detected = True
                self.last_voice_at_s = now

        if self.voice_detected:
            if self.audio_duration_ms() >= self.speech_timeout_ms:
                return "speech_timeout"
            if self.last_voice_at_s is not None:
                silence_ms = int((now - self.last_voice_at_s) * 1000.0)
                if silence_ms >= self.silence_threshold_ms and self.audio_duration_ms() >= self.min_audio_duration_ms:
                    return "silence_threshold"
            return None

        if self.audio_duration_ms() >= self.pre_speech_timeout_ms:
            return "speech_timeout_no_voice"
        return None

@router.get("/voice/token")
async def get_voice_token(request: Request):
    """
    Génère un token Twilio pour permettre les appels vocaux depuis le navigateur.
    Utilisé par le ChatWidget pour initialiser le Twilio Device.
    """
    tenant_scope = str(getattr(request.state, "tenant_id", "") or "")

    # If the tenant_context_middleware already resolved a tenant (via
    # X-Widget-Session JWT or legacy provider_key/tenant_token query params),
    # skip all additional auth checks — the request is already authenticated.
    if tenant_scope:
        pass  # authenticated via middleware — proceed
    elif settings.widget_public_token:
        token_header = request.headers.get("X-Widget-Token")
        if token_header != settings.widget_public_token:
            raise HTTPException(status_code=401, detail="invalid_widget_token")
    else:
        # If no widget token is configured and there is no fail-closed tenant context,
        # require authenticated user (dashboard/internal usage).
        token = request.cookies.get("access_token")
        if not token:
            auth = request.headers.get("authorization") or request.headers.get("Authorization")
            if auth and auth.lower().startswith("bearer "):
                token = auth.split(" ", 1)[1].strip()
        if not token:
            raise HTTPException(status_code=401, detail="auth_required")
        verify_jwt(token)

    if not settings.twilio_account_sid or not settings.twilio_api_key or not settings.twilio_api_secret:
        return {"error": "Twilio credentials not configured"}, 500

    # Créer une identité unique pour le client web
    identity = f"web-client-{uuid.uuid4()}"

    # Créer le token d'accès
    token = AccessToken(
        settings.twilio_account_sid,
        settings.twilio_api_key,
        settings.twilio_api_secret,
        identity=identity,
        ttl=3600  # Token valide 1 heure
    )

    # Ajouter les permissions Voice
    voice_grant = VoiceGrant(
        outgoing_application_sid=settings.twilio_twiml_app_sid,
        incoming_allow=True
    )
    token.add_grant(voice_grant)

    return {
        "token": token.to_jwt(),
        "identity": identity
    }

@router.post("/voice/outbound")
async def voice_outbound(request: Request):
    """
    TwiML pour les appels sortants depuis le navigateur web.
    Connecte l'appel au WebSocket pour interaction avec l'IA.
    """
    raw_body = await request.body()
    form = await request.form()
    verify_webhook(
        "twilio_voice",
        request=request,
        raw_body=raw_body,
        form_data={str(k): str(v) for k, v in dict(form).items()},
        url=str(request.url),
    )
    to = form.get("To")
    logger.info(
        "Twilio voice outbound webhook received",
        extra={
            "extra_fields": {
                "call_sid": str(form.get("CallSid") or ""),
                "to": str(to or ""),
                "tenant_id": str(getattr(request.state, "tenant_id", "") or ""),
                "path": str(request.url.path),
                "xf_proto": str(request.headers.get("x-forwarded-proto") or ""),
                "xf_host": str(request.headers.get("x-forwarded-host") or ""),
            }
        },
    )

    # Si "To" = "agent-ia", connecter au WebSocket de l'IA
    if to == "agent-ia":
        ws_url = (str(settings.public_ws_url).rstrip("/")) if settings.public_ws_url else ""
        if not ws_url:
            twiml = """
<Response>
  <Say language=\"fr-FR\">Le service admissions est indisponible pour le moment.</Say>
</Response>
""".strip()
            return Response(content=twiml, media_type="application/xml")

        # Générer un token pour le WebSocket
        now = int(time.time())
        tenant_scope = str(getattr(request.state, "tenant_id", None) or "")
        if not tenant_scope:
            raise HTTPException(status_code=403, detail="missing_tenant_scope")
        call_sid = form.get("CallSid")
        payload = {
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": now,
            "exp": now + 60,
            "typ": "ws",
            "call_sid": call_sid,
            "tenant_id": tenant_scope,
        }
        token = jwt.encode(payload, settings.jwt_private_key, algorithm="RS256")
        # Twilio Media Streams n'accepte pas les query params dans <Stream url>.
        # On passe donc le JWT dans le path WebSocket (base64url-safe).
        stream_url = f"{ws_url}/media/stream/{token}"
        logger.info(
            "Twilio voice outbound stream URL generated",
            extra={
                "extra_fields": {
                    "call_sid": str(call_sid or ""),
                    "tenant_id": tenant_scope,
                    "ws_base": ws_url,
                    "stream_path_prefix": "/media/stream/",
                    "jwt_len": len(str(token or "")),
                }
            },
        )

        twiml = f"""
<Response>
  <Connect>
    <Stream url=\"{stream_url}\"/>
  </Connect>
</Response>
""".strip()
    else:
        # Appel vers un numéro réel (si nécessaire)
        twiml = f"""
<Response>
  <Dial>{to}</Dial>
</Response>
""".strip()

    return Response(content=twiml, media_type="application/xml")

@router.post("/voice/incoming")
async def voice_incoming(request: Request):
    raw_body = await request.body()
    form = await request.form()
    verify_webhook(
        "twilio_voice",
        request=request,
        raw_body=raw_body,
        form_data={str(k): str(v) for k, v in dict(form).items()},
        url=str(request.url),
    )

    # Récupérer le Call SID de Twilio
    call_sid = form.get("CallSid")

    ws_url = (str(settings.public_ws_url).rstrip("/")) if settings.public_ws_url else ""
    if not ws_url:
        twiml = """
<Response>
  <Say language="fr-FR">Le service admissions est indisponible pour le moment. Veuillez rappeler plus tard.</Say>
</Response>
""".strip()
        return Response(content=twiml, media_type="application/xml")

    # Generate short-lived WS token (60s) to protect the websocket endpoint
    now = int(time.time())
    tenant_scope = str(getattr(request.state, "tenant_id", None) or "")
    if not tenant_scope:
        raise HTTPException(status_code=403, detail="missing_tenant_scope")
    payload = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + 60,
        "typ": "ws",
        "call_sid": call_sid,  # Inclure le Call SID dans le token
        "tenant_id": tenant_scope,
    }
    token = jwt.encode(payload, settings.jwt_private_key, algorithm="RS256")
    # Twilio Media Streams n'accepte pas les query params dans <Stream url>.
    stream_url = f"{ws_url}/media/stream/{token}"

    # TwiML pour streaming audio vers l'IA
    twiml = f"""
<Response>
  <Say language="fr-FR">Cet appel peut etre enregistre.</Say>
  <Connect>
    <Stream url="{stream_url}"/>
  </Connect>
</Response>
""".strip()
    return Response(content=twiml, media_type="application/xml")

@router.websocket("/media/stream")
@router.websocket("/media/stream/{token}")
async def media_stream(ws: WebSocket, token: Optional[str] = None):
    if get_emergency_state().get("enabled"):
        await ws.close()
        return
    # Verify WS token from query params
    token = token or (ws.query_params.get("token") if hasattr(ws, "query_params") else None)
    if not token:
        logger.warning(
            "Twilio media stream rejected: missing token",
            extra={
                "extra_fields": {
                    "path": str(getattr(getattr(ws, "url", None), "path", "") or ""),
                    "query": str(getattr(getattr(ws, "url", None), "query", "") or ""),
                }
            },
        )
        await ws.close()
        return
    call_sid = None
    tenant_scope = None
    suppress_welcome = False
    try:
        payload = jwt.decode(
            token,
            settings.jwt_public_key,
            algorithms=["RS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
        if payload.get("typ") != "ws":
            await ws.close()
            return
        call_sid = payload.get("call_sid")  # Récupérer le Call SID du token
        tenant_scope = payload.get("tenant_id")
        suppress_welcome = bool(payload.get("suppress_welcome"))
        if not tenant_scope:
            logger.warning(
                "Twilio media stream rejected: missing tenant in token",
                extra={"extra_fields": {"call_sid": str(call_sid or "")}},
            )
            await ws.close()
            return
    except Exception as exc:
        logger.warning(
            "Twilio media stream rejected: invalid token",
            extra={
                "extra_fields": {
                    "error": exc.__class__.__name__,
                    "token_len": len(str(token or "")),
                }
            },
        )
        await ws.close()
        return
    await ws.accept()
    logger.info(
        "Twilio media stream accepted",
        extra={
            "extra_fields": {
                "call_sid": str(call_sid or ""),
                "tenant_id": str(tenant_scope or ""),
            }
        },
    )
    # Minimal session state
    stream_sid: Optional[str] = None
    buffered_turn = BufferedTurnAccumulator()
    current_voice_lang = "fr"
    def _build_stt_service(lang: str) -> STTService:
        try:
            return STTService(
                language=_stt_language_for_voice(lang),
                endpointing_ms=_STT_STREAMING_ENDPOINTING_MS,
            )
        except TypeError:
            fallback_stt = STTService()
            if hasattr(fallback_stt, "set_language"):
                with suppress(Exception):
                    fallback_stt.set_language(_stt_language_for_voice(lang))
            return fallback_stt

    stt = _build_stt_service(current_voice_lang)
    stt_streaming = False
    tts = TTSService()
    stt_mode = "streaming" if stt_streaming else "buffered"
    assistant_speaking_until_s = 0.0
    assistant_playback_task: Optional[asyncio.Task] = None
    assistant_playback_transport = ""
    assistant_playback_id: Optional[int] = None
    assistant_playback_seq = 0
    assistant_barge_in_count = 0
    assistant_interrupt_effective_count = 0
    welcome_delivered = False
    send_lock = asyncio.Lock()
    # DB session pour logger la conversation d'appel
    db: Session = open_db_session(str(tenant_scope))
    pipeline = ChannelAgentPipeline(
        db,
        track_search_fn=handle_get_track_tuition,
        person_upsert_fn=handle_create_or_get_person,
    )
    current_conversation_id: Optional[str] = None
    voice_turn_count = 0
    empty_transcript_streak = 0
    empty_reprompt_count = 0
    last_empty_reprompt_at_s = 0.0
    ws_disconnected = False
    twilio_redirect_unavailable = False
    session_started_at_s: Optional[float] = None
    first_transcript_at_s: Optional[float] = None
    first_transcript_latency_ms: Optional[int] = None
    first_audio_at_s: Optional[float] = None
    first_audio_latency_ms: Optional[int] = None
    stt_stream_downgrade_count = 0
    stt_stream_recovery_attempts = 0
    stt_stream_recovery_successes = 0
    stt_stream_disabled_until_s = 0.0
    stt_buffered_fallback_count = 0
    twilio_say_fallback_count = 0

    def _is_ws_closed_runtime_error(exc: RuntimeError) -> bool:
        msg = str(exc).lower()
        return (
            "close message has been sent" in msg
            or "disconnect message has been received" in msg
        )

    async def _safe_ws_send_text(payload: str, *, event_name: str) -> bool:
        nonlocal ws_disconnected
        if ws_disconnected:
            return False
        try:
            async with send_lock:
                await ws.send_text(payload)
            return True
        except WebSocketDisconnect:
            ws_disconnected = True
            return False
        except RuntimeError as exc:
            if _is_ws_closed_runtime_error(exc):
                ws_disconnected = True
                logger.info(
                    "Twilio websocket send skipped after close",
                    extra={
                        "extra_fields": {
                            "call_sid": str(call_sid or ""),
                            "stream_sid": str(stream_sid or ""),
                            "event_name": event_name,
                        }
                    },
                )
                return False
            raise

    async def _safe_ws_send_bytes(payload: bytes, *, event_name: str) -> bool:
        nonlocal ws_disconnected
        if ws_disconnected:
            return False
        try:
            async with send_lock:
                await ws.send_bytes(payload)
            return True
        except WebSocketDisconnect:
            ws_disconnected = True
            return False
        except RuntimeError as exc:
            if _is_ws_closed_runtime_error(exc):
                ws_disconnected = True
                logger.info(
                    "Twilio websocket send skipped after close",
                    extra={
                        "extra_fields": {
                            "call_sid": str(call_sid or ""),
                            "stream_sid": str(stream_sid or ""),
                            "event_name": event_name,
                        }
                    },
                )
                return False
            raise

    async def _cancel_assistant_playback(*, reason: str) -> bool:
        nonlocal assistant_playback_task, assistant_speaking_until_s, assistant_playback_transport, assistant_playback_id
        task = assistant_playback_task
        assistant_speaking_until_s = 0.0
        if task is None or task.done():
            assistant_playback_task = None
            return False
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
        assistant_playback_task = None

        # Send Twilio clear message to stop buffered audio in transit
        if stream_sid and not ws_disconnected:
            try:
                await _safe_ws_send_text(
                    json.dumps({"event": "clear", "streamSid": stream_sid}),
                    event_name="clear_on_barge_in",
                )
            except Exception:
                pass

        logger.info(
            "Twilio assistant playback cancelled",
            extra={
                "extra_fields": {
                    "call_sid": str(call_sid or ""),
                    "stream_sid": str(stream_sid or ""),
                    "reason": reason,
                    "playback_transport": assistant_playback_transport or None,
                    "playback_id": assistant_playback_id,
                }
            },
        )
        assistant_playback_transport = ""
        assistant_playback_id = None
        return True

    async def _handle_user_speech_during_playback(*, speech_detected: bool, now_s: float, detection_mode: str) -> bool:
        nonlocal assistant_barge_in_count, assistant_interrupt_effective_count
        if not speech_detected:
            return False
        if assistant_playback_task is None and now_s >= assistant_speaking_until_s:
            return False
        playback_transport = assistant_playback_transport or ""
        assistant_barge_in_count += 1
        interruption_effective = await _cancel_assistant_playback(reason="barge_in")
        if interruption_effective:
            assistant_interrupt_effective_count += 1
        limitation = ""
        if not interruption_effective:
            if playback_transport == "twilio_say":
                limitation = "twilio_say_uninterruptible"
            elif playback_transport == "media_stream":
                limitation = "downstream_buffered_audio"
            else:
                limitation = "playback_already_buffered"
        logger.info(
            "Twilio voice barge-in detected",
            extra={
                "extra_fields": {
                    "call_sid": str(call_sid or ""),
                    "stream_sid": str(stream_sid or ""),
                    "stt_mode": stt_mode,
                    "vad_end_reason": "barge_in",
                    "barge_in_count": int(assistant_barge_in_count),
                    "barge_in_effective": bool(interruption_effective),
                    "barge_in_effective_count": int(assistant_interrupt_effective_count),
                    "playback_transport": playback_transport or None,
                    "detection_mode": detection_mode,
                    "limitation": limitation or None,
                }
            },
        )
        return interruption_effective

    async def _start_streaming_session(*, reason: str, recovery: bool = False) -> bool:
        nonlocal stt, stt_streaming, stt_mode, stt_stream_recovery_attempts, stt_stream_recovery_successes, stt_stream_disabled_until_s
        if recovery:
            if stt_stream_recovery_attempts >= _STT_STREAM_RECOVERY_MAX_RETRIES:
                return False
            stt_stream_recovery_attempts += 1
        with suppress(Exception):
            await stt.close()
        stt = _build_stt_service(current_voice_lang)
        try:
            stt_streaming = await stt.start_session()
        except Exception:
            stt_streaming = False
        stt_mode = "streaming" if stt_streaming else "buffered"
        if stt_streaming:
            stt_stream_disabled_until_s = 0.0
            if recovery:
                stt_stream_recovery_successes += 1
                logger.info(
                    "Twilio STT streaming recovered",
                    extra={
                        "extra_fields": {
                            "call_sid": str(call_sid or ""),
                            "stream_sid": str(stream_sid or ""),
                            "recovery_attempts": int(stt_stream_recovery_attempts),
                            "recovery_successes": int(stt_stream_recovery_successes),
                            "reason": reason,
                        }
                    },
                )
            return True
        if recovery:
            stt_stream_disabled_until_s = time.perf_counter() + _STT_STREAM_RECOVERY_COOLDOWN_S
            logger.warning(
                "Twilio STT streaming recovery failed",
                extra={
                    "extra_fields": {
                        "call_sid": str(call_sid or ""),
                        "stream_sid": str(stream_sid or ""),
                        "recovery_attempts": int(stt_stream_recovery_attempts),
                        "reason": reason,
                        "retry_after_s": _STT_STREAM_RECOVERY_COOLDOWN_S,
                    }
                },
            )
        return False

    async def _twilio_say_and_resume(reply_text: str, *, lang: str) -> bool:
        nonlocal twilio_redirect_unavailable
        if twilio_redirect_unavailable:
            return False
        if not call_sid:
            return False
        if not TwilioRestClient:
            return False
        if not settings.twilio_account_sid or not settings.twilio_auth_token:
            return False
        ws_url = (str(settings.public_ws_url).rstrip("/")) if settings.public_ws_url else ""
        if not ws_url:
            return False
        try:
            now = int(time.time())
            payload = {
                "iss": settings.jwt_issuer,
                "aud": settings.jwt_audience,
                "iat": now,
                "exp": now + 60,
                "typ": "ws",
                "call_sid": call_sid,
                "tenant_id": tenant_scope,
                "suppress_welcome": True,
            }
            ws_token = jwt.encode(payload, settings.jwt_private_key, algorithm="RS256")
            stream_url = f"{ws_url}/media/stream/{ws_token}"
            twiml = (
                "<Response>"
                f'<Say voice="alice" language="{xml_escape(_twilio_say_language(lang))}">{xml_escape(str(reply_text or ""))}</Say>'
                "<Connect>"
                f'<Stream url="{xml_escape(stream_url)}"/>'
                "</Connect>"
                "</Response>"
            )

            def _update() -> None:
                client = TwilioRestClient(settings.twilio_account_sid, settings.twilio_auth_token)  # type: ignore[misc]
                client.calls(str(call_sid)).update(twiml=twiml)

            await asyncio.to_thread(_update)
            logger.info(
                "Twilio TTS fallback via <Say> applied",
                extra={
                    "extra_fields": {
                        "call_sid": str(call_sid or ""),
                        "stream_sid": str(stream_sid or ""),
                        "reply_len": len(str(reply_text or "")),
                    }
                },
            )
            return True
        except TwilioRestException as exc:
            if str(getattr(exc, "code", "")) == "21220":
                twilio_redirect_unavailable = True
                logger.info(
                    "Twilio TTS fallback skipped: call no longer in progress",
                    extra={
                        "extra_fields": {
                            "call_sid": str(call_sid or ""),
                            "stream_sid": str(stream_sid or ""),
                            "twilio_error_code": getattr(exc, "code", None),
                        }
                    },
                )
                return False
            logger.warning(
                "Twilio TTS fallback via <Say> failed",
                extra={
                    "extra_fields": {
                        "call_sid": str(call_sid or ""),
                        "stream_sid": str(stream_sid or ""),
                        "error": str(exc),
                        "twilio_error_code": getattr(exc, "code", None),
                    }
                },
                exc_info=True,
            )
            return False
        except Exception as exc:
            logger.warning(
                "Twilio TTS fallback via <Say> failed",
                extra={
                    "extra_fields": {
                        "call_sid": str(call_sid or ""),
                        "stream_sid": str(stream_sid or ""),
                        "error": str(exc),
                    }
                },
                exc_info=True,
            )
            return False

    async def _send_tts_reply(reply_text: str, *, lang: str) -> dict[str, Any]:
        nonlocal assistant_speaking_until_s, ws_disconnected, assistant_playback_task
        nonlocal assistant_playback_transport, assistant_playback_id, assistant_playback_seq
        nonlocal first_audio_at_s, first_audio_latency_ms, twilio_say_fallback_count
        await _cancel_assistant_playback(reason="new_reply")
        tts_started_at_s = time.perf_counter()

        # --- Try direct µ-law 8kHz synthesis first (avoids double resampling) ---
        mulaw_bytes = await tts.synthesize_to_mulaw_8k(reply_text)
        if mulaw_bytes and not ws_disconnected:
            tts_synth_ms = int((time.perf_counter() - tts_started_at_s) * 1000.0)
            # Split µ-law bytes into 20ms frames (8000 samples/s * 0.020s = 160 bytes/frame)
            frame_size = 160
            mulaw_frames = [
                mulaw_bytes[i:i + frame_size]
                for i in range(0, len(mulaw_bytes), frame_size)
                if mulaw_bytes[i:i + frame_size]
            ]
            payloads = frames_to_base64_payloads(mulaw_frames)
            wav_bytes = None  # skip WAV path
        else:
            # Fallback to WAV synthesis + conversion
            wav_bytes = await tts.synthesize_to_wav(reply_text)
            tts_synth_ms = int((time.perf_counter() - tts_started_at_s) * 1000.0)
            payloads = None

        if payloads and not ws_disconnected:
            # Direct µ-law path - payloads already ready
            pass
        elif wav_bytes and not ws_disconnected:
            # WAV fallback path
            payloads = frames_to_base64_payloads(wav_to_mulaw8k_frames(wav_bytes, frame_ms=20))
        else:
            payloads = None

        if payloads and not ws_disconnected:
            try:
                if not payloads:
                    raise ValueError("empty_tts_payloads")
                estimated_ms = max(300, len(payloads) * 20)
                assistant_speaking_until_s = time.perf_counter() + (estimated_ms / 1000.0)
                assistant_playback_transport = "media_stream"
                assistant_playback_seq += 1
                playback_id = assistant_playback_seq
                assistant_playback_id = playback_id
                first_frame_started_at_s = time.perf_counter()
                first_frame_ok = await _safe_ws_send_text(
                    json.dumps({
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {"payload": payloads[0]},
                    }),
                    event_name="media",
                )
                if not first_frame_ok:
                    ws_disconnected = True
                    assistant_playback_transport = ""
                    assistant_playback_id = None
                    return {
                        "stream_replaced": False,
                        "tts_transport": "disconnected",
                        "tts_synth_ms": max(0, tts_synth_ms),
                        "tts_frames": 0,
                        "tts_estimated_playback_ms": 0,
                        "tts_first_audio_send_ms": None,
                        "first_audio_at_s": None,
                    }
                first_frame_sent_at_s = time.perf_counter()
                tts_first_audio_send_ms = int((first_frame_sent_at_s - tts_started_at_s) * 1000.0)
                if first_audio_at_s is None:
                    first_audio_at_s = first_frame_sent_at_s
                    if session_started_at_s is not None:
                        first_audio_latency_ms = int((first_audio_at_s - session_started_at_s) * 1000.0)
                    logger.info(
                        "Twilio first audio sent",
                        extra={
                            "extra_fields": {
                                "call_sid": str(call_sid or ""),
                                "stream_sid": str(stream_sid or ""),
                                "playback_id": playback_id,
                                "tts_transport": "media_stream",
                                "tts_synth_ms": max(0, tts_synth_ms),
                                "tts_first_audio_send_ms": max(0, tts_first_audio_send_ms),
                                "first_audio_latency_ms": first_audio_latency_ms,
                            }
                        },
                    )

                async def _stream_payloads() -> None:
                    nonlocal ws_disconnected, assistant_playback_task, assistant_playback_transport, assistant_playback_id
                    cancelled = False
                    try:
                        for idx, payload_chunk in enumerate(payloads[1:], start=1):
                            ok = await _safe_ws_send_text(
                                json.dumps({
                                    "event": "media",
                                    "streamSid": stream_sid,
                                    "media": {"payload": payload_chunk},
                                }),
                                event_name="media",
                            )
                            if not ok:
                                ws_disconnected = True
                                break
                            if idx + 1 < len(payloads):
                                await asyncio.sleep(_VOICE_FRAME_PACING_S)
                    except asyncio.CancelledError:
                        cancelled = True
                        raise
                    except WebSocketDisconnect:
                        ws_disconnected = True
                        logger.info(
                            "Twilio media websocket disconnected during TTS send",
                            extra={
                                "extra_fields": {
                                    "call_sid": str(call_sid or ""),
                                    "stream_sid": str(stream_sid or ""),
                                }
                            },
                        )
                    except Exception:
                        logger.exception(
                            "Twilio TTS streaming failed",
                            extra={"extra_fields": {"call_sid": str(call_sid or ""), "stream_sid": str(stream_sid or "")}},
                        )
                        ws_disconnected = True
                    finally:
                        assistant_playback_task = None
                        if not cancelled:
                            logger.info(
                                "Twilio assistant playback completed",
                                extra={
                                    "extra_fields": {
                                        "call_sid": str(call_sid or ""),
                                        "stream_sid": str(stream_sid or ""),
                                        "playback_id": playback_id,
                                        "tts_transport": "media_stream",
                                        "frames_sent": len(payloads),
                                        "first_frame_send_ms": int((first_frame_sent_at_s - first_frame_started_at_s) * 1000.0),
                                    }
                                },
                            )
                        if assistant_playback_id == playback_id:
                            assistant_playback_transport = ""
                            assistant_playback_id = None

                if len(payloads) > 1:
                    assistant_playback_task = asyncio.create_task(_stream_payloads())
                else:
                    assistant_playback_task = None
                    assistant_playback_transport = ""
                    assistant_playback_id = None
                return {
                    "stream_replaced": False,
                    "tts_transport": "media_stream",
                    "tts_synth_ms": max(0, tts_synth_ms),
                    "tts_frames": len(payloads),
                    "tts_estimated_playback_ms": estimated_ms,
                    "tts_first_audio_send_ms": max(0, tts_first_audio_send_ms),
                    "first_audio_at_s": first_frame_sent_at_s,
                }
            except WebSocketDisconnect:
                ws_disconnected = True
                logger.info(
                    "Twilio media websocket disconnected during TTS send",
                    extra={
                        "extra_fields": {
                            "call_sid": str(call_sid or ""),
                            "stream_sid": str(stream_sid or ""),
                        }
                    },
                )
            except Exception:
                logger.exception(
                    "Twilio TTS streaming failed",
                    extra={"extra_fields": {"call_sid": str(call_sid or ""), "stream_sid": str(stream_sid or "")}},
                )
                ws_disconnected = True
        tts_last_error = str(getattr(tts, "last_error", None) or "")
        logger.warning(
            f"Twilio TTS returned no audio (tts_last_error={tts_last_error or 'unknown'})",
            extra={
                "extra_fields": {
                    "call_sid": str(call_sid or ""),
                    "stream_sid": str(stream_sid or ""),
                    "reply_len": len(str(reply_text or "")),
                    "tts_last_error": tts_last_error,
                }
            },
        )
        if await _twilio_say_and_resume(reply_text, lang=lang):
            estimated_ms = max(600, min(12000, int(len(str(reply_text or "")) * 55)))
            assistant_speaking_until_s = time.perf_counter() + (estimated_ms / 1000.0)
            assistant_playback_transport = "twilio_say"
            assistant_playback_seq += 1
            assistant_playback_id = assistant_playback_seq
            twilio_say_fallback_count += 1
            return {
                "stream_replaced": True,
                "tts_transport": "twilio_say",
                "tts_synth_ms": max(0, tts_synth_ms),
                "tts_frames": 0,
                "tts_estimated_playback_ms": estimated_ms,
                "tts_first_audio_send_ms": None,
                "first_audio_at_s": None,
            }
        if ws_disconnected:
            return {
                "stream_replaced": False,
                "tts_transport": "disconnected",
                "tts_synth_ms": max(0, tts_synth_ms),
                "tts_frames": 0,
                "tts_estimated_playback_ms": 0,
                "tts_first_audio_send_ms": None,
                "first_audio_at_s": None,
            }
        try:
            await _safe_ws_send_text(
                json.dumps({
                    "event": "mark",
                    "streamSid": stream_sid,
                    "mark": {"name": "reply-ready"},
                }),
                event_name="reply-ready",
            )
        except WebSocketDisconnect:
            ws_disconnected = True
        except Exception:
            pass
        return {
            "stream_replaced": False,
            "tts_transport": "mark_only",
            "tts_synth_ms": max(0, tts_synth_ms),
            "tts_frames": 0,
            "tts_estimated_playback_ms": 0,
            "tts_first_audio_send_ms": None,
            "first_audio_at_s": None,
        }

    async def _flush_buffered_turn(*, vad_end_reason: str) -> bool:
        nonlocal stt_mode, empty_transcript_streak, empty_reprompt_count, last_empty_reprompt_at_s
        if not buffered_turn.has_audio():
            return False

        audio_duration_ms = buffered_turn.audio_duration_ms()
        turn_started_at_s = buffered_turn.turn_started_at_s or time.perf_counter()
        payload = bytes(buffered_turn.audio_buf)
        speech_detected = bool(buffered_turn.voice_detected)
        buffered_turn.reset()

        if audio_duration_ms < _STT_MIN_AUDIO_DURATION_MS and not speech_detected:
            logger.info(
                "Twilio buffered segment ignored",
                extra={
                    "extra_fields": {
                        "call_sid": str(call_sid or ""),
                        "stream_sid": str(stream_sid or ""),
                        "stt_mode": "buffered",
                        "transcript_length": 0,
                        "stt_confidence": None,
                        "turn_latency_ms": int((time.perf_counter() - turn_started_at_s) * 1000.0),
                        "vad_end_reason": "min_audio_not_reached",
                    }
                },
            )
            return False

        stt_mode = "buffered"
        stt_meta = {"text": None, "confidence": None}
        try:
            if hasattr(stt, "set_language"):
                stt.set_language(_stt_language_for_voice(current_voice_lang))
            stt_meta = await stt.transcribe_prerecorded_with_meta(payload, mimetype="audio/ulaw;rate=8000")
        except Exception:
            stt_meta = {"text": None, "confidence": None}

        transcript = str(stt_meta.get("text") or "").strip()
        transcript_length = len(transcript)
        confidence_raw = stt_meta.get("confidence")
        try:
            stt_confidence = round(float(confidence_raw), 4) if confidence_raw is not None else None
        except Exception:
            stt_confidence = None
        turn_latency_ms = int((time.perf_counter() - turn_started_at_s) * 1000.0)

        logger.info(
            "Twilio buffered transcript processed",
            extra={
                "extra_fields": {
                    "call_sid": str(call_sid or ""),
                    "stream_sid": str(stream_sid or ""),
                    "stt_mode": "buffered",
                    "stt_confidence": stt_confidence,
                    "transcript_length": transcript_length,
                    "turn_latency_ms": max(0, turn_latency_ms),
                    "vad_end_reason": str(vad_end_reason),
                }
            },
        )

        if not transcript:
            reprompt_eligible = bool(
                speech_detected
                and vad_end_reason not in {"speech_timeout_no_voice", "stream_stop"}
            )
            if reprompt_eligible:
                empty_transcript_streak += 1
            else:
                empty_transcript_streak = 0
            now_s = time.perf_counter()
            if (
                reprompt_eligible
                and empty_reprompt_count < _EMPTY_REPROMPT_MAX_PER_CALL
                and empty_transcript_streak >= _EMPTY_TRANSCRIPT_REPROMPT_STREAK
                and (now_s - last_empty_reprompt_at_s) >= _EMPTY_REPROMPT_COOLDOWN_S
            ):
                last_empty_reprompt_at_s = now_s
                empty_transcript_streak = 0
                empty_reprompt_count += 1
                await _send_tts_reply(
                    _voice_prompt(_VOICE_EMPTY_REPROMPTS, lang=current_voice_lang),
                    lang=current_voice_lang,
                )
            return False
        empty_transcript_streak = 0
        handoff_after_reply = await _process_voice_turn(
            transcript,
            stt_mode="buffered",
            stt_latency_ms=max(0, turn_latency_ms),
            vad_end_reason=vad_end_reason,
            transcript_length=transcript_length,
            stt_confidence=stt_confidence,
        )
        return bool(handoff_after_reply)

    async def _process_voice_turn(
        user_text: str,
        *,
        stt_mode: str,
        stt_latency_ms: int,
        vad_end_reason: str,
        transcript_length: Optional[int] = None,
        stt_confidence: Optional[float] = None,
    ) -> bool:
        nonlocal current_conversation_id, voice_turn_count, current_voice_lang
        nonlocal first_transcript_at_s, first_transcript_latency_ms
        segment_started_at_s = time.perf_counter()
        if first_transcript_at_s is None:
            first_transcript_at_s = segment_started_at_s
            if session_started_at_s is not None:
                first_transcript_latency_ms = int((first_transcript_at_s - session_started_at_s) * 1000.0)
            logger.info(
                "Twilio first transcript finalized",
                extra={
                    "extra_fields": {
                        "call_sid": str(call_sid or ""),
                        "stream_sid": str(stream_sid or ""),
                        "stt_mode": stt_mode,
                        "first_transcript_latency_ms": first_transcript_latency_ms,
                    }
                },
            )
        voice_turn_count += 1
        normalized_user_text = str(user_text or "").strip()
        current_voice_lang = _detect_voice_lang(normalized_user_text, fallback=current_voice_lang)
        if hasattr(stt, "set_language"):
            stt.set_language(_stt_language_for_voice(current_voice_lang))
        cleaned = re.sub(r"[^\wÀ-ÿ'\- ]+", " ", normalized_user_text, flags=re.UNICODE)
        words = [w for w in cleaned.lower().split() if w]
        first_word = words[0] if words else ""
        is_short_greeting = bool(first_word in _GREETING_TOKENS and len(words) <= 4)
        is_greeting_only = bool(_GREETING_ONLY_RE.match(normalized_user_text)) or is_short_greeting
        response_strategy = "voice_greeting_prompt"
        handoff_after_reply = False
        fallback_stage = ""
        handoff_trigger_reason = ""
        agent_started_at_s = time.perf_counter()
        if is_greeting_only:
            logger.info(
                "Twilio greeting shortcut applied",
                extra={
                    "extra_fields": {
                        "call_sid": str(call_sid or ""),
                        "stream_sid": str(stream_sid or ""),
                        "turn_count": voice_turn_count,
                        "stt_text": normalized_user_text[:120],
                    }
                },
            )
            reply = _voice_prompt(_VOICE_GREETING_PROMPTS, lang=current_voice_lang)
        else:
            try:
                result = await pipeline.process_inbound_text(
                    channel="call",
                    user_text=user_text,
                    conversation_id=current_conversation_id,
                    reuse_recent_by_person=False,
                    call_sid=call_sid,
                    recording_consent=True,
                    thread_key=f"call:{call_sid}" if call_sid else (f"call-stream:{stream_sid}" if stream_sid else None),
                    from_value=call_sid or stream_sid,
                    conversation_resume_prefix=f"Call stream {stream_sid or ''}",
                )
                current_conversation_id = result.conversation_id
                current_voice_lang = _normalize_voice_lang(getattr(result, "lang", None), fallback=current_voice_lang)
                if hasattr(stt, "set_language"):
                    stt.set_language(_stt_language_for_voice(current_voice_lang))
                response_strategy = str(getattr(result, "response_strategy", "") or "")
                max_reply_chars, max_reply_sentences = _voice_reply_budget(response_strategy)
                reply = _compact_voice_reply(
                    str(result.reply or ""),
                    max_chars=max_reply_chars,
                    max_sentences=max_reply_sentences,
                )
                logger.info(
                    "Twilio voice turn processed",
                    extra={
                        "extra_fields": {
                            "call_sid": str(call_sid or ""),
                            "stream_sid": str(stream_sid or ""),
                            "user_text_len": len(str(user_text or "")),
                            "reply_len": len(str(reply or "")),
                            "response_strategy": response_strategy,
                            "voice_lang": current_voice_lang,
                        }
                    },
                )
                fallback_stage = str((result.conversation_state or {}).get("fallback_stage") or "")
                handoff_trigger_reason = str((result.conversation_state or {}).get("handoff_trigger_reason") or "")
                raw_handoff_after_reply = bool(
                    result.response_strategy == "fallback_handoff"
                    or bool((result.conversation_state or {}).get("handoff_allowed"))
                )
                handoff_after_reply = raw_handoff_after_reply
                if handoff_after_reply and voice_turn_count <= 1:
                    logger.info(
                        "Twilio handoff suppressed for early/greeting turn",
                        extra={
                            "extra_fields": {
                                "call_sid": str(call_sid or ""),
                                "stream_sid": str(stream_sid or ""),
                                "turn_count": voice_turn_count,
                                "is_greeting_only": is_greeting_only,
                                "response_strategy": str(getattr(result, "response_strategy", "") or ""),
                                "fallback_stage": fallback_stage,
                                "handoff_trigger_reason": handoff_trigger_reason,
                            }
                        },
                    )
                    handoff_after_reply = False
            except Exception:
                response_strategy = "voice_technical_fallback"
                reply = _voice_prompt(_VOICE_TECHNICAL_FALLBACKS, lang=current_voice_lang)
                handoff_after_reply = False
        agent_latency_ms = int((time.perf_counter() - agent_started_at_s) * 1000.0)
        if handoff_after_reply and call_sid:
            transfer_reason = _voice_prompt(_VOICE_TRANSFER_REASONS, lang=current_voice_lang)
            playback = await _send_tts_reply(transfer_reason, lang=current_voice_lang)
            if playback.get("stream_replaced"):
                return True
            try:
                await transfer_call_to_human_agents(db, call_sid=str(call_sid), lang=str(current_voice_lang))
            except Exception:
                pass
            return True
        playback = await _send_tts_reply(reply, lang=current_voice_lang)
        total_latency_ms = int((time.perf_counter() - segment_started_at_s) * 1000.0)
        first_audio_send_ms = playback.get("tts_first_audio_send_ms")
        segment_time_to_first_audio_ms: Optional[int] = None
        playback_first_audio_at_s = playback.get("first_audio_at_s")
        if isinstance(playback_first_audio_at_s, (int, float)):
            segment_time_to_first_audio_ms = int((float(playback_first_audio_at_s) - segment_started_at_s) * 1000.0)
        logger.info(
            "Twilio voice segment completed",
            extra={
                "extra_fields": {
                    "call_sid": str(call_sid or ""),
                    "stream_sid": str(stream_sid or ""),
                    "stt_mode": stt_mode,
                    "voice_lang": current_voice_lang,
                    "stt_latency_ms": max(0, int(stt_latency_ms)),
                    "agent_latency_ms": max(0, agent_latency_ms),
                    "tts_latency_ms": max(0, int(playback.get("tts_synth_ms") or 0)),
                    "tts_first_audio_send_ms": first_audio_send_ms,
                    "segment_time_to_first_audio_ms": segment_time_to_first_audio_ms,
                    "segment_total_latency_ms": max(0, total_latency_ms),
                    "tts_transport": str(playback.get("tts_transport") or ""),
                    "tts_frames": int(playback.get("tts_frames") or 0),
                    "tts_estimated_playback_ms": int(playback.get("tts_estimated_playback_ms") or 0),
                    "response_strategy": response_strategy,
                    "fallback_stage": fallback_stage,
                    "handoff_trigger_reason": handoff_trigger_reason,
                    "vad_end_reason": str(vad_end_reason),
                    "transcript_length": int(transcript_length if transcript_length is not None else len(normalized_user_text)),
                    "stt_confidence": stt_confidence,
                    "is_greeting_only": is_greeting_only,
                    "stream_replaced": bool(playback.get("stream_replaced")),
                    "first_transcript_latency_ms": first_transcript_latency_ms,
                    "first_audio_latency_ms": first_audio_latency_ms,
                    "barge_in_count": int(assistant_barge_in_count),
                    "barge_in_effective_count": int(assistant_interrupt_effective_count),
                    "stt_stream_downgrade_count": int(stt_stream_downgrade_count),
                    "stt_stream_recovery_attempts": int(stt_stream_recovery_attempts),
                    "stt_stream_recovery_successes": int(stt_stream_recovery_successes),
                    "stt_buffered_fallback_count": int(stt_buffered_fallback_count),
                }
            },
        )
        return bool(handoff_after_reply or bool(playback.get("stream_replaced")))

    try:
        while True:
            try:
                msg = await ws.receive()
            except RuntimeError as exc:
                if 'disconnect message has been received' in str(exc):
                    logger.info(
                        "Twilio media stream receive after disconnect ignored",
                        extra={
                            "extra_fields": {
                                "call_sid": str(call_sid or ""),
                                "stream_sid": str(stream_sid or ""),
                            }
                        },
                    )
                    break
                raise
            if "text" in msg and msg["text"] is not None:
                try:
                    event = json.loads(msg["text"])  # Twilio Media Streams JSON messages
                except Exception:
                    event = {"event": "unknown"}

                etype = event.get("event")
                if etype == "start":
                    stream_sid = event.get("start", {}).get("streamSid")
                    session_started_at_s = time.perf_counter()
                    logger.info(
                        "Twilio media stream started",
                        extra={
                            "extra_fields": {
                                "call_sid": str(call_sid or ""),
                                "stream_sid": str(stream_sid or ""),
                            }
                        },
                    )
                    stt_streaming = await _start_streaming_session(reason="session_start")
                    if not stt_streaming:
                        stt_buffered_fallback_count += 1
                        logger.warning(
                            "Twilio voice session starting in buffered mode",
                            extra={
                                "extra_fields": {
                                    "call_sid": str(call_sid or ""),
                                    "stream_sid": str(stream_sid or ""),
                                }
                            },
                        )
                    sent = await _safe_ws_send_text(
                        json.dumps({
                            "event": "mark",
                            "streamSid": stream_sid,
                            "mark": {"name": "session-started"},
                        }),
                        event_name="session-started",
                    )
                    if not sent:
                        break
                    if not suppress_welcome and not welcome_delivered:
                        welcome_delivered = True
                        playback = await _send_tts_reply(_VOICE_WELCOME_PROMPT, lang=current_voice_lang)
                        if playback.get("stream_replaced"):
                            break
                    continue

                if etype == "media":
                    payload = event.get("media", {}).get("payload")
                    if isinstance(payload, str):
                        try:
                            chunk = base64.b64decode(payload)
                            now_s = time.perf_counter()
                            if stt_streaming:
                                stt_mode = "streaming"
                                speech_during_playback = buffered_turn.peek_speech_chunk(chunk)
                                if speech_during_playback:
                                    await _handle_user_speech_during_playback(
                                        speech_detected=True,
                                        now_s=now_s,
                                        detection_mode="streaming",
                                    )
                                end_reason = buffered_turn.ingest(chunk, now_s=now_s)
                                user_text = await stt.process_audio_chunk(chunk)
                                if stt.stream_failed:
                                    stt_stream_downgrade_count += 1
                                    stt_buffered_fallback_count += 1
                                    logger.warning(
                                        "Twilio STT streaming failed; switching to buffered transcription",
                                        extra={"extra_fields": {"call_sid": str(call_sid or ""), "stream_sid": str(stream_sid or "")}},
                                    )
                                    stt_streaming = False
                                    stt_mode = "buffered"
                                    if end_reason:
                                        handoff_after_reply = await _flush_buffered_turn(vad_end_reason=end_reason)
                                        if handoff_after_reply:
                                            await ws.close()
                                            break
                                        if not ws_disconnected:
                                            await _start_streaming_session(reason="stream_send_failure", recovery=True)
                                elif user_text:
                                    streaming_turn_started_at_s = buffered_turn.turn_started_at_s or now_s
                                    buffered_turn.reset()
                                    streaming_stt_latency_ms = int((time.perf_counter() - streaming_turn_started_at_s) * 1000.0)
                                    logger.info(
                                        "Twilio STT streaming transcript received",
                                        extra={
                                            "extra_fields": {
                                                "call_sid": str(call_sid or ""),
                                                "stream_sid": str(stream_sid or ""),
                                                "stt_mode": "streaming",
                                                "stt_confidence": None,
                                                "transcript_length": len(str(user_text or "")),
                                                "turn_latency_ms": max(0, streaming_stt_latency_ms),
                                                "vad_end_reason": "streaming_final",
                                            }
                                        },
                                    )
                                    handoff_after_reply = await _process_voice_turn(
                                        user_text,
                                        stt_mode="streaming",
                                        stt_latency_ms=max(0, streaming_stt_latency_ms),
                                        vad_end_reason="streaming_final",
                                        transcript_length=len(str(user_text or "")),
                                        stt_confidence=None,
                                    )
                                    if handoff_after_reply:
                                        await ws.close()
                                        break
                                elif end_reason in {"speech_timeout", "silence_threshold"}:
                                    stt_stream_downgrade_count += 1
                                    stt_buffered_fallback_count += 1
                                    logger.warning(
                                        "Twilio STT streaming stalled; switching to buffered transcription",
                                        extra={
                                            "extra_fields": {
                                                "call_sid": str(call_sid or ""),
                                                "stream_sid": str(stream_sid or ""),
                                                "vad_end_reason": str(end_reason),
                                            }
                                        },
                                    )
                                    stt_streaming = False
                                    stt_mode = "buffered"
                                    handoff_after_reply = await _flush_buffered_turn(vad_end_reason=end_reason)
                                    if handoff_after_reply:
                                        await ws.close()
                                        break
                                    if not ws_disconnected:
                                        await _start_streaming_session(reason=f"stream_stall:{end_reason}", recovery=True)
                                elif end_reason == "speech_timeout_no_voice":
                                    # Silent segment while streaming: drop buffered bytes to avoid stale accumulation.
                                    buffered_turn.reset()
                            else:
                                stt_mode = "buffered"
                                speech_during_playback = buffered_turn.peek_speech_chunk(chunk)
                                if speech_during_playback:
                                    await _handle_user_speech_during_playback(
                                        speech_detected=True,
                                        now_s=now_s,
                                        detection_mode="buffered",
                                    )
                                end_reason = buffered_turn.ingest(chunk, now_s=now_s)
                                if end_reason:
                                    handoff_after_reply = await _flush_buffered_turn(vad_end_reason=end_reason)
                                    if handoff_after_reply:
                                        await ws.close()
                                        break
                        except Exception:
                            pass
                    continue

                if etype == "stop":
                    try:
                        await _cancel_assistant_playback(reason="stream_stop")
                        handoff_after_reply = await _flush_buffered_turn(vad_end_reason="stream_stop")
                        if handoff_after_reply:
                            await ws.close()
                            break
                    except Exception:
                        pass
                    await _safe_ws_send_text(
                        json.dumps({
                            "event": "mark",
                            "streamSid": stream_sid,
                            "mark": {"name": "session-stopped"},
                        }),
                        event_name="session-stopped",
                    )
                    try:
                        await stt.close()
                    except Exception:
                        pass
                    try:
                        await ws.close()
                    except Exception:
                        pass
                    break

                # Unknown or keepalive: respond with a small heartbeat to prevent idle timeout
                sent = await _safe_ws_send_text("ok", event_name="heartbeat")
                if not sent:
                    break
                continue

            if "bytes" in msg and msg["bytes"] is not None:
                # Binary messages are not expected for Twilio JSON events; keep connection alive
                sent = await _safe_ws_send_bytes(b"\x00", event_name="binary-heartbeat")
                if not sent:
                    break
    except WebSocketDisconnect:
        logger.info(
            "Twilio media stream client disconnected",
            extra={
                "extra_fields": {
                    "call_sid": str(call_sid or ""),
                    "stream_sid": str(stream_sid or ""),
                }
            },
        )
    except Exception as exc:
        logger.exception(
            "Twilio media stream loop failed",
            extra={
                "extra_fields": {
                    "call_sid": str(call_sid or ""),
                    "stream_sid": str(stream_sid or ""),
                    "error": exc.__class__.__name__,
                }
            },
        )
        try:
            await ws.close()
        except Exception:
            pass
    finally:
        logger.info(
            "Twilio voice session ended",
            extra={
                "extra_fields": {
                    "call_sid": str(call_sid or ""),
                    "stream_sid": str(stream_sid or ""),
                    "voice_turn_count": int(voice_turn_count),
                    "first_transcript_latency_ms": first_transcript_latency_ms,
                    "first_audio_latency_ms": first_audio_latency_ms,
                    "barge_in_count": int(assistant_barge_in_count),
                    "barge_in_effective_count": int(assistant_interrupt_effective_count),
                    "stt_stream_downgrade_count": int(stt_stream_downgrade_count),
                    "stt_stream_recovery_attempts": int(stt_stream_recovery_attempts),
                    "stt_stream_recovery_successes": int(stt_stream_recovery_successes),
                    "stt_buffered_fallback_count": int(stt_buffered_fallback_count),
                    "twilio_say_fallback_count": int(twilio_say_fallback_count),
                }
            },
        )
        try:
            await _cancel_assistant_playback(reason="session_end")
        except Exception:
            pass
        try:
            await stt.close()
        except Exception:
            pass
        try:
            db.close()
        except Exception:
            pass
