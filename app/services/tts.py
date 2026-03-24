from __future__ import annotations

import asyncio
import io
import wave
from typing import AsyncIterator, Optional

from ..config import settings
from ..logger import get_logger

logger = get_logger(__name__)

ElevenLabsClient = None  # type: ignore
try:
    # API legacy (0.x)
    from elevenlabs import generate, set_api_key
except Exception:
    generate = None  # type: ignore
    set_api_key = None  # type: ignore
    try:
        # API modern (1.x)
        from elevenlabs.client import ElevenLabs as ElevenLabsClient  # type: ignore
    except Exception:
        ElevenLabsClient = None  # type: ignore


class TTSService:
    def __init__(self) -> None:
        self.api_key: Optional[str] = settings.elevenlabs_api_key
        self.voice_id: Optional[str] = settings.elevenlabs_voice_id
        self.model_id: str = settings.elevenlabs_model
        self.last_error: Optional[str] = None
        self._client = None
        if set_api_key and self.api_key:
            try:
                set_api_key(self.api_key)
            except Exception:
                pass
        if ElevenLabsClient and self.api_key:
            try:
                self._client = ElevenLabsClient(api_key=self.api_key)
            except Exception:
                self._client = None

    def is_configured(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _pcm16_to_wav(pcm16_bytes: bytes, sample_rate: int) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm16_bytes)
        return buf.getvalue()

    def _collect_audio_chunks(self, audio: object) -> Optional[bytes]:
        """Collect audio from an iterator/generator into bytes."""
        if isinstance(audio, (bytes, bytearray)):
            return bytes(audio)
        try:
            chunks = []
            for chunk in audio:  # type: ignore[union-attr]
                if isinstance(chunk, (bytes, bytearray)):
                    chunks.append(bytes(chunk))
                    continue
                data = getattr(chunk, "audio", None)
                if isinstance(data, (bytes, bytearray)):
                    chunks.append(bytes(data))
                    continue
                if isinstance(chunk, memoryview):
                    chunks.append(chunk.tobytes())
                    continue
                if isinstance(chunk, int):
                    chunks.append(bytes([chunk]))
            result = b"".join(chunks)
            if not result:
                self.last_error = "unexpected_audio_stream"
                return None
            return result
        except Exception as e:
            self.last_error = str(e)
            return None

    async def synthesize_to_wav(self, text: str) -> Optional[bytes]:
        if not self.is_configured():
            self.last_error = "not_configured"
            return None
        if generate is None and self._client is None:
            self.last_error = "sdk_unavailable"
            return None
        try:
            voice = self.voice_id or "Bella"
            audio_format = "unknown"
            audio: bytes | object
            if generate is not None:
                try:
                    audio = generate(
                        text=text,
                        voice=voice,
                        model=self.model_id,
                        output_format="pcm_16000",
                    )
                    audio_format = "pcm_16000"
                except TypeError:
                    audio = generate(text=text, voice=voice, model=self.model_id)
            else:
                tts_client = getattr(self._client, "text_to_speech", None)
                if tts_client is None or not hasattr(tts_client, "convert"):
                    self.last_error = "sdk_incompatible"
                    return None
                audio = tts_client.convert(
                    voice_id=voice,
                    text=text,
                    model_id=self.model_id,
                    output_format="pcm_16000",
                )
                audio_format = "pcm_16000"

            audio = self._collect_audio_chunks(audio)
            if audio is None:
                return None

            if audio.startswith(b"RIFF"):
                return audio
            if audio_format == "pcm_16000":
                return self._pcm16_to_wav(audio, 16000)

            self.last_error = "unsupported_audio_format"
            return None
        except Exception as e:
            self.last_error = str(e)
            return None

    async def synthesize_to_mulaw_8k(self, text: str) -> Optional[bytes]:
        """Synthesize directly to µ-law 8kHz format to avoid double resampling.

        ElevenLabs supports ulaw_8000 output format natively.
        This avoids the costly PCM16→WAV→resample→µ-law conversion chain.
        """
        if not self.is_configured():
            self.last_error = "not_configured"
            return None
        if self._client is None and generate is None:
            self.last_error = "sdk_unavailable"
            return None
        try:
            voice = self.voice_id or "Bella"

            if self._client is not None:
                tts_client = getattr(self._client, "text_to_speech", None)
                if tts_client is not None and hasattr(tts_client, "convert"):
                    try:
                        audio = tts_client.convert(
                            voice_id=voice,
                            text=text,
                            model_id=self.model_id,
                            output_format="ulaw_8000",
                        )
                        result = self._collect_audio_chunks(audio)
                        if result:
                            return result
                    except Exception as e:
                        logger.info(
                            "TTS ulaw_8000 direct synthesis failed, falling back to pcm_16000",
                            extra={"extra_fields": {"error": str(e)}},
                        )

            # Fallback: synthesize to WAV and let caller handle conversion
            self.last_error = "ulaw_direct_unavailable"
            return None
        except Exception as e:
            self.last_error = str(e)
            return None

    async def synthesize_streaming(self, text: str) -> AsyncIterator[bytes]:
        """Stream TTS audio chunks as they are generated.

        Uses ElevenLabs streaming API to yield audio chunks progressively,
        reducing time-to-first-audio significantly.
        Yields PCM16 16kHz chunks by default.
        """
        if not self.is_configured():
            self.last_error = "not_configured"
            return
        if self._client is None:
            self.last_error = "sdk_unavailable_for_streaming"
            return
        try:
            voice = self.voice_id or "Bella"
            tts_client = getattr(self._client, "text_to_speech", None)
            if tts_client is None:
                self.last_error = "sdk_incompatible"
                return

            # Try streaming with convert_as_stream (ElevenLabs SDK >=1.x)
            stream_fn = getattr(tts_client, "convert_as_stream", None)
            if stream_fn is None:
                # Fallback to non-streaming convert
                self.last_error = "streaming_not_available"
                return

            audio_stream = stream_fn(
                voice_id=voice,
                text=text,
                model_id=self.model_id,
                output_format="ulaw_8000",
            )

            for chunk in audio_stream:
                if isinstance(chunk, (bytes, bytearray)) and chunk:
                    yield bytes(chunk)
                elif hasattr(chunk, "audio"):
                    data = chunk.audio
                    if isinstance(data, (bytes, bytearray)) and data:
                        yield bytes(data)
        except Exception as e:
            self.last_error = str(e)
            logger.warning(
                "TTS streaming failed",
                extra={"extra_fields": {"error": str(e)}},
            )

    async def synthesize_streaming_mulaw_8k(self, text: str) -> AsyncIterator[bytes]:
        """Stream TTS audio directly in µ-law 8kHz format.

        This is the optimal path for Twilio voice: no resampling needed.
        """
        if not self.is_configured():
            self.last_error = "not_configured"
            return
        if self._client is None:
            self.last_error = "sdk_unavailable_for_streaming"
            return
        try:
            voice = self.voice_id or "Bella"
            tts_client = getattr(self._client, "text_to_speech", None)
            if tts_client is None:
                self.last_error = "sdk_incompatible"
                return

            stream_fn = getattr(tts_client, "convert_as_stream", None)
            if stream_fn is None:
                self.last_error = "streaming_not_available"
                return

            audio_stream = stream_fn(
                voice_id=voice,
                text=text,
                model_id=self.model_id,
                output_format="ulaw_8000",
            )

            for chunk in audio_stream:
                if isinstance(chunk, (bytes, bytearray)) and chunk:
                    yield bytes(chunk)
                elif hasattr(chunk, "audio"):
                    data = chunk.audio
                    if isinstance(data, (bytes, bytearray)) and data:
                        yield bytes(data)
        except Exception as e:
            self.last_error = str(e)
            logger.warning(
                "TTS streaming µ-law failed",
                extra={"extra_fields": {"error": str(e)}},
            )
