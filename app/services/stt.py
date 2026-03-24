from __future__ import annotations

from typing import Any, Optional
import asyncio
import json
import io
import os
import ssl
import wave
import audioop
from contextlib import suppress
from urllib.parse import urlencode

from ..config import settings
from ..logger import get_logger

logger = get_logger(__name__)

try:
    # Deepgram SDK (prerecorded)
    from deepgram import DeepgramClient, PrerecordedOptions
except Exception:
    DeepgramClient = None  # type: ignore
    PrerecordedOptions = None  # type: ignore

try:
    import websockets
    from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
except Exception:
    websockets = None  # type: ignore
    ConnectionClosedError = Exception  # type: ignore
    ConnectionClosedOK = Exception  # type: ignore

try:
    import certifi
except Exception:
    certifi = None  # type: ignore


_DEEPGRAM_WS_BASE = "wss://api.deepgram.com/v1/listen"
_DEEPGRAM_KEEPALIVE_INTERVAL_S = 4.0


class STTService:
    """Service de transcription Speech-to-Text via Deepgram.

    - Streaming temps réel via WebSocket Deepgram.
    - Fallback buffered via transcription prerecorded Deepgram.
    """

    def __init__(self, *, language: Optional[str] = None, endpointing_ms: Optional[int] = None) -> None:
        self.provider = "deepgram"
        self.api_key: Optional[str] = settings.deepgram_api_key
        self.language: str = str(language or settings.stt_language or "fr").strip() or "fr"
        self.endpointing_ms: int = max(200, int(endpointing_ms or 300))
        self._ssl_context: Optional[ssl.SSLContext] = self._build_ssl_context()
        self._ws: Optional[Any] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._queue: Optional[asyncio.Queue] = None
        self._stream_failed = False
        self._warned_not_configured = False
        self._warned_streaming_unavailable = False

    @staticmethod
    def _resolve_ca_bundle_path() -> Optional[str]:
        env_path = str(os.getenv("SSL_CERT_FILE") or os.getenv("REQUESTS_CA_BUNDLE") or "").strip()
        if env_path:
            return env_path
        if certifi is None:
            return None
        try:
            return str(certifi.where() or "").strip() or None
        except Exception:
            return None

    @classmethod
    def _build_ssl_context(cls) -> Optional[ssl.SSLContext]:
        ca_bundle = cls._resolve_ca_bundle_path()
        if not ca_bundle:
            return None
        # Keep TLS trust configuration consistent across websocket and SDK HTTP calls.
        if not os.getenv("SSL_CERT_FILE"):
            os.environ["SSL_CERT_FILE"] = ca_bundle
        if not os.getenv("REQUESTS_CA_BUNDLE"):
            os.environ["REQUESTS_CA_BUNDLE"] = ca_bundle
        try:
            return ssl.create_default_context(cafile=ca_bundle)
        except Exception as exc:
            logger.warning(
                "STT SSL context creation failed; using system trust store",
                extra={"extra_fields": {"error": str(exc)}},
            )
            return None

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def set_language(self, language: Optional[str]) -> None:
        normalized = str(language or "").strip().lower()
        if normalized in {"fr", "en"}:
            self.language = normalized

    async def start_session(self) -> bool:
        """Initialise une session de streaming Deepgram."""
        if not self.is_configured():
            return False
        if self._ws is not None:
            return True
        if websockets is None:
            if not self._warned_streaming_unavailable:
                logger.warning("STT streaming unavailable: websockets dependency missing; falling back to buffered transcription")
                self._warned_streaming_unavailable = True
            self._stream_failed = True
            return False

        self._stream_failed = False
        self._queue = asyncio.Queue()

        params = {
            "model": settings.deepgram_model,
            "language": self.language,
            "punctuate": "true",
            "smart_format": "true",
            "encoding": "mulaw",
            "sample_rate": "8000",
            "channels": "1",
            "interim_results": "true",
            "endpointing": str(self.endpointing_ms),
        }
        uri = f"{_DEEPGRAM_WS_BASE}?{urlencode(params)}"
        headers = {"Authorization": f"Token {self.api_key}"}
        connect_kwargs = {
            "open_timeout": 10,
            "close_timeout": 3,
            "ping_interval": 20,
            "ping_timeout": 20,
            "max_queue": 32,
        }
        if self._ssl_context is not None:
            connect_kwargs["ssl"] = self._ssl_context

        try:
            try:
                # websockets>=14
                self._ws = await websockets.connect(uri, additional_headers=headers, **connect_kwargs)
            except TypeError:
                # websockets<=13
                self._ws = await websockets.connect(uri, extra_headers=headers, **connect_kwargs)
            self._recv_task = asyncio.create_task(self._recv_loop())
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())
            logger.info(
                "STT streaming session started",
                extra={
                    "extra_fields": {
                        "provider": self.provider,
                        "model": settings.deepgram_model,
                        "language": self.language,
                        "endpointing_ms": self.endpointing_ms,
                    }
                },
            )
            return True
        except Exception as exc:
            logger.warning(
                "STT streaming init failed; falling back to buffered transcription",
                extra={"extra_fields": {"error": str(exc), "model": settings.deepgram_model}},
            )
            await self.close()
            self._stream_failed = True
            return False

    @staticmethod
    def _mulaw8k_to_wav_bytes(mulaw_bytes: bytes, sample_rate: int = 8000) -> bytes:
        pcm16 = audioop.ulaw2lin(mulaw_bytes, 2)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm16)
        return buf.getvalue()

    async def process_audio_chunk(self, chunk: bytes) -> Optional[str]:
        """Envoie un chunk audio et retourne une transcription finale si disponible."""
        if self._ws is None:
            ok = await self.start_session()
            if not ok:
                self._stream_failed = True
                return None
        if self._ws is None:
            self._stream_failed = True
            return None

        try:
            await self._ws.send(chunk)
        except Exception as exc:
            logger.warning("STT streaming send failed", extra={"extra_fields": {"error": str(exc)}})
            self._stream_failed = True
            return None

        if not self._queue:
            return None

        final_text: Optional[str] = None
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if bool(item.get("is_final")):
                candidate = str(item.get("text") or "").strip()
                if candidate:
                    final_text = candidate
        return final_text

    async def close(self) -> None:
        """Ferme proprement la session de streaming."""
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            with suppress(Exception):
                await self._keepalive_task
        if self._recv_task is not None:
            self._recv_task.cancel()
            with suppress(Exception):
                await self._recv_task
        if self._ws is not None:
            with suppress(Exception):
                await self._ws.send(json.dumps({"type": "CloseStream"}))
            with suppress(Exception):
                await self._ws.close()
        self._ws = None
        self._recv_task = None
        self._keepalive_task = None
        self._queue = None
        self._stream_failed = False

    @property
    def stream_failed(self) -> bool:
        return self._stream_failed

    async def _keepalive_loop(self) -> None:
        if self._ws is None:
            return
        try:
            while self._ws is not None:
                await asyncio.sleep(_DEEPGRAM_KEEPALIVE_INTERVAL_S)
                if self._ws is None:
                    break
                await self._ws.send(json.dumps({"type": "KeepAlive"}))
        except asyncio.CancelledError:
            return
        except Exception:
            return

    async def _recv_loop(self) -> None:
        if self._ws is None or self._queue is None:
            return
        try:
            while self._ws is not None:
                raw_msg = await self._ws.recv()
                if not isinstance(raw_msg, str):
                    continue
                try:
                    data = json.loads(raw_msg)
                except Exception:
                    continue
                if data.get("type") != "Results":
                    continue
                channel = data.get("channel") or {}
                alternatives = channel.get("alternatives") or []
                transcript = ""
                if alternatives and isinstance(alternatives[0], dict):
                    transcript = str(alternatives[0].get("transcript") or "").strip()
                is_final = bool(data.get("is_final") or data.get("speech_final"))
                if transcript or is_final:
                    await self._queue.put({"text": transcript, "is_final": is_final})
        except asyncio.CancelledError:
            return
        except ConnectionClosedOK:
            return
        except ConnectionClosedError as exc:
            self._stream_failed = True
            logger.warning("STT streaming connection closed", extra={"extra_fields": {"error": str(exc)}})
        except Exception as exc:
            self._stream_failed = True
            logger.warning("STT streaming receive loop failed", extra={"extra_fields": {"error": str(exc)}})

    async def transcribe_prerecorded(self, data: bytes, mimetype: str = "audio/wav") -> Optional[str]:
        meta = await self.transcribe_prerecorded_with_meta(data=data, mimetype=mimetype)
        text = meta.get("text")
        return str(text).strip() if isinstance(text, str) and text.strip() else None

    async def transcribe_prerecorded_with_meta(self, data: bytes, mimetype: str = "audio/wav") -> dict[str, Any]:
        """Transcription buffered avec métadonnées STT (texte + confiance)."""
        if not self.is_configured() or DeepgramClient is None or PrerecordedOptions is None:
            if not self._warned_not_configured:
                logger.warning("STT not configured or SDK missing; transcription skipped")
                self._warned_not_configured = True
            return {"text": None, "confidence": None}
        try:
            dg = DeepgramClient(api_key=self.api_key)
            payload = data
            effective_mimetype = mimetype
            lowered_mimetype = (mimetype or "").lower()
            if "ulaw" in lowered_mimetype or "mulaw" in lowered_mimetype:
                payload = self._mulaw8k_to_wav_bytes(data, sample_rate=8000)
                effective_mimetype = "audio/wav"
            options = PrerecordedOptions(
                model=settings.deepgram_model,
                smart_format=True,
                punctuate=True,
                language=self.language,
            )
            source = {"buffer": payload, "mimetype": effective_mimetype}
            res = dg.listen.prerecorded.v("1").transcribe_file(source, options)
            try:
                obj = res.to_dict() if hasattr(res, "to_dict") else res
                channels = obj.get("results", {}).get("channels", [])
                if not channels:
                    return {"text": None, "confidence": None}
                alts = channels[0].get("alternatives", [])
                if not alts:
                    return {"text": None, "confidence": None}
                top = alts[0] if isinstance(alts[0], dict) else {}
                transcript = top.get("transcript") if isinstance(top, dict) else None
                confidence_raw = top.get("confidence") if isinstance(top, dict) else None
                confidence: Optional[float]
                try:
                    confidence = float(confidence_raw) if confidence_raw is not None else None
                except Exception:
                    confidence = None
                clean_text = str(transcript or "").strip() or None
                return {"text": clean_text, "confidence": confidence}
            except Exception:
                return {"text": None, "confidence": None}
        except Exception as e:
            logger.error(f"STT transcription failed: {e}", exc_info=True)
            return {"text": None, "confidence": None}
