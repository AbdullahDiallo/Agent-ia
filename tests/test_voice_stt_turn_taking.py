from __future__ import annotations

import audioop
import base64
import io
import json
import logging
import struct
import wave
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.routers import voice as voice_module
from app.routers.voice import router as voice_router


def _mulaw_speech_chunk(*, amplitude: int = 7000, samples: int = 160) -> bytes:
    pcm = b"".join(struct.pack("<h", amplitude) for _ in range(samples))
    return audioop.lin2ulaw(pcm, 2)


def _mulaw_silence_chunk(*, samples: int = 160) -> bytes:
    pcm = b"".join(struct.pack("<h", 0) for _ in range(samples))
    return audioop.lin2ulaw(pcm, 2)


def _silent_wav(*, duration_ms: int = 60, sample_rate: int = 8000) -> bytes:
    frame_count = int(sample_rate * (duration_ms / 1000.0))
    pcm = (b"\x00\x00" * frame_count)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


class _FakeDBSession:
    def close(self) -> None:
        return None


class _FakeSTTService:
    transcribe_calls = 0

    def __init__(self) -> None:
        self._stream_failed = False

    @property
    def stream_failed(self) -> bool:
        return self._stream_failed

    async def start_session(self) -> bool:
        return False

    async def close(self) -> None:
        return None

    async def process_audio_chunk(self, _chunk: bytes):  # pragma: no cover - explicit no-op
        return None

    async def transcribe_prerecorded_with_meta(self, data: bytes, mimetype: str = "audio/wav") -> dict:
        _ = mimetype
        _FakeSTTService.transcribe_calls += 1
        assert len(data) > 0
        return {
            "text": "Je veux connaitre les frais de scolarite",
            "confidence": 0.91,
        }


class _FakeTTSService:
    def __init__(self) -> None:
        self.last_error = None

    async def synthesize_to_mulaw_8k(self, _text: str):
        return None  # force fallback to WAV path

    async def synthesize_to_wav(self, _text: str):
        return _silent_wav()


class _LongTTSService:
    def __init__(self) -> None:
        self.last_error = None

    async def synthesize_to_mulaw_8k(self, _text: str):
        return None

    async def synthesize_to_wav(self, _text: str):
        return _silent_wav(duration_ms=1500)


class _CapturingTTSService:
    spoken_texts: list[str] = []

    def __init__(self) -> None:
        self.last_error = None

    async def synthesize_to_mulaw_8k(self, text: str):
        return None

    async def synthesize_to_wav(self, text: str):
        _CapturingTTSService.spoken_texts.append(str(text or ""))
        return _silent_wav()


class _FakeEmptySTTService:
    transcribe_calls = 0

    def __init__(self) -> None:
        self._stream_failed = False

    @property
    def stream_failed(self) -> bool:
        return self._stream_failed

    async def start_session(self) -> bool:
        return False

    async def close(self) -> None:
        return None

    async def process_audio_chunk(self, _chunk: bytes):  # pragma: no cover - explicit no-op
        return None

    async def transcribe_prerecorded_with_meta(self, data: bytes, mimetype: str = "audio/wav") -> dict:
        _ = data
        _ = mimetype
        _FakeEmptySTTService.transcribe_calls += 1
        return {"text": None, "confidence": 0.0}


class _FakeStreamingNoFinalSTTService:
    transcribe_calls = 0

    def __init__(self) -> None:
        self._stream_failed = False

    @property
    def stream_failed(self) -> bool:
        return self._stream_failed

    async def start_session(self) -> bool:
        return True

    async def close(self) -> None:
        return None

    async def process_audio_chunk(self, _chunk: bytes):
        return None

    async def transcribe_prerecorded_with_meta(self, data: bytes, mimetype: str = "audio/wav") -> dict:
        _ = data
        _ = mimetype
        _FakeStreamingNoFinalSTTService.transcribe_calls += 1
        return {
            "text": "Je veux connaitre les frais de scolarite",
            "confidence": 0.88,
        }


class _GreetingSTTService:
    transcribe_calls = 0

    def __init__(self) -> None:
        self._stream_failed = False

    @property
    def stream_failed(self) -> bool:
        return self._stream_failed

    async def start_session(self) -> bool:
        return False

    async def close(self) -> None:
        return None

    async def process_audio_chunk(self, _chunk: bytes):
        return None

    async def transcribe_prerecorded_with_meta(self, data: bytes, mimetype: str = "audio/wav") -> dict:
        _ = data
        _ = mimetype
        _GreetingSTTService.transcribe_calls += 1
        return {"text": "hello", "confidence": 0.94}


class _BufferedInterruptSTTService:
    transcribe_calls = 0

    def __init__(self) -> None:
        self._stream_failed = False

    @property
    def stream_failed(self) -> bool:
        return self._stream_failed

    async def start_session(self) -> bool:
        return False

    async def close(self) -> None:
        return None

    async def process_audio_chunk(self, _chunk: bytes):
        return None

    async def transcribe_prerecorded_with_meta(self, data: bytes, mimetype: str = "audio/wav") -> dict:
        _ = data
        _ = mimetype
        _BufferedInterruptSTTService.transcribe_calls += 1
        if _BufferedInterruptSTTService.transcribe_calls == 1:
            return {"text": "Quels sont les frais ?", "confidence": 0.95}
        return {"text": "bonjour", "confidence": 0.94}


class _StreamingInterruptSTTService:
    def __init__(self) -> None:
        self._stream_failed = False
        self._chunk_count = 0
        self._returned_first = False
        self._returned_second = False

    @property
    def stream_failed(self) -> bool:
        return self._stream_failed

    async def start_session(self) -> bool:
        return True

    async def close(self) -> None:
        return None

    async def process_audio_chunk(self, _chunk: bytes):
        self._chunk_count += 1
        if not self._returned_first and self._chunk_count >= 12:
            self._returned_first = True
            return "Quels sont les frais ?"
        if self._returned_first and not self._returned_second and self._chunk_count >= 20:
            self._returned_second = True
            return "bonjour"
        return None

    async def transcribe_prerecorded_with_meta(self, data: bytes, mimetype: str = "audio/wav") -> dict:
        _ = data
        _ = mimetype
        return {"text": None, "confidence": None}


class _RecoveringStreamingSTTService:
    start_session_calls = 0
    transcribe_calls = 0

    def __init__(self) -> None:
        self._stream_failed = False

    @property
    def stream_failed(self) -> bool:
        return self._stream_failed

    async def start_session(self) -> bool:
        _RecoveringStreamingSTTService.start_session_calls += 1
        return True

    async def close(self) -> None:
        return None

    async def process_audio_chunk(self, _chunk: bytes):
        return None

    async def transcribe_prerecorded_with_meta(self, data: bytes, mimetype: str = "audio/wav") -> dict:
        _ = data
        _ = mimetype
        _RecoveringStreamingSTTService.transcribe_calls += 1
        return {
            "text": "Je veux connaitre les frais de scolarite",
            "confidence": 0.89,
        }


class _FakePipeline:
    captured_user_texts: list[str] = []
    captured_strategies: list[str] = []

    def __init__(self, *_args, **_kwargs) -> None:
        return None

    async def process_inbound_text(self, **kwargs):
        user_text = str(kwargs.get("user_text") or "")
        _FakePipeline.captured_user_texts.append(user_text)
        strategy = "kb_answer" if "frais" in user_text.lower() else "fallback_guided"
        _FakePipeline.captured_strategies.append(strategy)
        return SimpleNamespace(
            conversation_id="00000000-0000-0000-0000-00000000pr10",
            reply="Les frais dependent du programme.",
            response_strategy=strategy,
            conversation_state={"fallback_stage": "", "handoff_trigger_reason": "", "handoff_allowed": False},
            lang="fr",
        )


class _GreetingPipelineShouldNotRun:
    def __init__(self, *_args, **_kwargs) -> None:
        return None

    async def process_inbound_text(self, **kwargs):  # pragma: no cover - should not run
        raise AssertionError(f"Pipeline should not run for greeting shortcut: {kwargs}")


def test_voice_buffered_stt_turn_taking_logs_metrics_and_keeps_intent(monkeypatch, caplog):
    _FakePipeline.captured_user_texts = []
    _FakePipeline.captured_strategies = []
    _FakeSTTService.transcribe_calls = 0

    monkeypatch.setattr(voice_module.jwt, "decode", lambda *_a, **_k: {
        "typ": "ws",
        "call_sid": "CA_PR1_TEST",
        "tenant_id": "00000000-0000-0000-0000-000000000001",
    })
    monkeypatch.setattr(voice_module, "open_db_session", lambda *_a, **_k: _FakeDBSession())
    monkeypatch.setattr(voice_module, "STTService", _FakeSTTService)
    monkeypatch.setattr(voice_module, "TTSService", _FakeTTSService)
    monkeypatch.setattr(voice_module, "ChannelAgentPipeline", _FakePipeline)
    monkeypatch.setattr(settings, "twilio_account_sid", None, raising=False)
    monkeypatch.setattr(settings, "twilio_auth_token", None, raising=False)

    app = FastAPI()
    app.include_router(voice_router)
    caplog.set_level(logging.INFO, logger="app.routers.voice")

    speech_chunk = _mulaw_speech_chunk()
    speech_payload = base64.b64encode(speech_chunk).decode("utf-8")

    with TestClient(app) as client:
        with client.websocket_connect("/media/stream/fake-token") as websocket:
            websocket.send_text(json.dumps({"event": "start", "start": {"streamSid": "MZ_PR1_TEST"}}))
            # 350 * 20ms = 7000ms -> triggers speech_timeout in buffered mode.
            for _ in range(350):
                websocket.send_text(json.dumps({"event": "media", "media": {"payload": speech_payload}}))
            websocket.send_text(json.dumps({"event": "stop"}))

    assert _FakeSTTService.transcribe_calls >= 1
    assert _FakePipeline.captured_user_texts
    assert any("frais" in txt.lower() for txt in _FakePipeline.captured_user_texts)
    assert any(strategy == "kb_answer" for strategy in _FakePipeline.captured_strategies)

    stt_records = [rec for rec in caplog.records if rec.getMessage() == "Twilio buffered transcript processed"]
    assert stt_records
    stt_fields = getattr(stt_records[-1], "extra_fields", {}) or {}
    assert stt_fields.get("stt_mode") == "buffered"
    assert stt_fields.get("stt_confidence") == 0.91
    assert int(stt_fields.get("transcript_length") or 0) > 10
    assert stt_fields.get("turn_latency_ms") is not None
    assert stt_fields.get("vad_end_reason") == "speech_timeout"

    segment_records = [rec for rec in caplog.records if rec.getMessage() == "Twilio voice segment completed"]
    assert segment_records
    segment_fields = getattr(segment_records[-1], "extra_fields", {}) or {}
    assert segment_fields.get("response_strategy") == "kb_answer"
    assert segment_fields.get("stt_latency_ms") is not None
    assert segment_fields.get("agent_latency_ms") is not None
    assert segment_fields.get("tts_latency_ms") is not None
    assert segment_fields.get("tts_first_audio_send_ms") is not None
    assert segment_fields.get("segment_time_to_first_audio_ms") is not None
    assert segment_fields.get("segment_total_latency_ms") is not None
    assert segment_fields.get("first_transcript_latency_ms") is not None
    assert segment_fields.get("first_audio_latency_ms") is not None

    assert any(rec.getMessage() == "Twilio voice barge-in detected" for rec in caplog.records)


def test_voice_silence_does_not_trigger_looping_empty_transcript_reprompts(monkeypatch):
    _FakeEmptySTTService.transcribe_calls = 0
    _CapturingTTSService.spoken_texts = []

    monkeypatch.setattr(voice_module.jwt, "decode", lambda *_a, **_k: {
        "typ": "ws",
        "call_sid": "CA_PR1_SILENT",
        "tenant_id": "00000000-0000-0000-0000-000000000001",
    })
    monkeypatch.setattr(voice_module, "open_db_session", lambda *_a, **_k: _FakeDBSession())
    monkeypatch.setattr(voice_module, "STTService", _FakeEmptySTTService)
    monkeypatch.setattr(voice_module, "TTSService", _CapturingTTSService)
    monkeypatch.setattr(voice_module, "ChannelAgentPipeline", _FakePipeline)
    monkeypatch.setattr(settings, "twilio_account_sid", None, raising=False)
    monkeypatch.setattr(settings, "twilio_auth_token", None, raising=False)

    app = FastAPI()
    app.include_router(voice_router)

    silent_chunk = _mulaw_silence_chunk()
    silent_payload = base64.b64encode(silent_chunk).decode("utf-8")

    with TestClient(app) as client:
        with client.websocket_connect("/media/stream/fake-token") as websocket:
            websocket.send_text(json.dumps({"event": "start", "start": {"streamSid": "MZ_PR1_SILENT"}}))
            # 660 * 20ms = 13.2s of pure silence, enough for multiple buffered flushes.
            for _ in range(660):
                websocket.send_text(json.dumps({"event": "media", "media": {"payload": silent_payload}}))
            websocket.send_text(json.dumps({"event": "stop"}))

    assert _FakeEmptySTTService.transcribe_calls >= 3
    assert any("Vous pouvez parler en francais" in txt for txt in _CapturingTTSService.spoken_texts)
    assert not any("Je vous entends mal. Parlez plus près du micro" in txt for txt in _CapturingTTSService.spoken_texts)


def test_voice_streaming_stall_falls_back_to_buffered_turn(monkeypatch):
    _FakePipeline.captured_user_texts = []
    _FakePipeline.captured_strategies = []
    _FakeStreamingNoFinalSTTService.transcribe_calls = 0

    monkeypatch.setattr(voice_module.jwt, "decode", lambda *_a, **_k: {
        "typ": "ws",
        "call_sid": "CA_PR1_STALL",
        "tenant_id": "00000000-0000-0000-0000-000000000001",
    })
    monkeypatch.setattr(voice_module, "open_db_session", lambda *_a, **_k: _FakeDBSession())
    monkeypatch.setattr(voice_module, "STTService", _FakeStreamingNoFinalSTTService)
    monkeypatch.setattr(voice_module, "TTSService", _FakeTTSService)
    monkeypatch.setattr(voice_module, "ChannelAgentPipeline", _FakePipeline)
    monkeypatch.setattr(settings, "twilio_account_sid", None, raising=False)
    monkeypatch.setattr(settings, "twilio_auth_token", None, raising=False)

    app = FastAPI()
    app.include_router(voice_router)

    speech_chunk = _mulaw_speech_chunk()
    speech_payload = base64.b64encode(speech_chunk).decode("utf-8")

    with TestClient(app) as client:
        with client.websocket_connect("/media/stream/fake-token") as websocket:
            websocket.send_text(json.dumps({"event": "start", "start": {"streamSid": "MZ_PR1_STALL"}}))
            # Streaming provides no final transcript; 350 * 20ms triggers buffered fallback via speech_timeout.
            for _ in range(350):
                websocket.send_text(json.dumps({"event": "media", "media": {"payload": speech_payload}}))
            websocket.send_text(json.dumps({"event": "stop"}))

    assert _FakeStreamingNoFinalSTTService.transcribe_calls >= 1
    assert any("frais" in txt.lower() for txt in _FakePipeline.captured_user_texts)


def test_voice_greeting_shortcut_is_language_aware_without_repeating_full_intro(monkeypatch):
    _GreetingSTTService.transcribe_calls = 0
    _CapturingTTSService.spoken_texts = []

    monkeypatch.setattr(voice_module.jwt, "decode", lambda *_a, **_k: {
        "typ": "ws",
        "call_sid": "CA_PR1_GREET",
        "tenant_id": "00000000-0000-0000-0000-000000000001",
    })
    monkeypatch.setattr(voice_module, "open_db_session", lambda *_a, **_k: _FakeDBSession())
    monkeypatch.setattr(voice_module, "STTService", _GreetingSTTService)
    monkeypatch.setattr(voice_module, "TTSService", _CapturingTTSService)
    monkeypatch.setattr(voice_module, "ChannelAgentPipeline", _GreetingPipelineShouldNotRun)
    monkeypatch.setattr(settings, "twilio_account_sid", None, raising=False)
    monkeypatch.setattr(settings, "twilio_auth_token", None, raising=False)

    app = FastAPI()
    app.include_router(voice_router)

    speech_chunk = _mulaw_speech_chunk()
    speech_payload = base64.b64encode(speech_chunk).decode("utf-8")

    with TestClient(app) as client:
        with client.websocket_connect("/media/stream/fake-token") as websocket:
            websocket.send_text(json.dumps({"event": "start", "start": {"streamSid": "MZ_PR1_GREET"}}))
            for _ in range(350):
                websocket.send_text(json.dumps({"event": "media", "media": {"payload": speech_payload}}))
            websocket.send_text(json.dumps({"event": "stop"}))

    assert _GreetingSTTService.transcribe_calls >= 1
    assert len(_CapturingTTSService.spoken_texts) >= 2
    assert "Salma" in _CapturingTTSService.spoken_texts[0]
    assert "I'm listening" in _CapturingTTSService.spoken_texts[1]
    assert all(
        ("Salma" not in text) for text in _CapturingTTSService.spoken_texts[1:]
    )


def test_voice_start_token_can_suppress_session_welcome(monkeypatch):
    _CapturingTTSService.spoken_texts = []

    monkeypatch.setattr(voice_module.jwt, "decode", lambda *_a, **_k: {
        "typ": "ws",
        "call_sid": "CA_PR1_NOWELCOME",
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "suppress_welcome": True,
    })
    monkeypatch.setattr(voice_module, "open_db_session", lambda *_a, **_k: _FakeDBSession())
    monkeypatch.setattr(voice_module, "STTService", _FakeEmptySTTService)
    monkeypatch.setattr(voice_module, "TTSService", _CapturingTTSService)
    monkeypatch.setattr(voice_module, "ChannelAgentPipeline", _FakePipeline)
    monkeypatch.setattr(settings, "twilio_account_sid", None, raising=False)
    monkeypatch.setattr(settings, "twilio_auth_token", None, raising=False)

    app = FastAPI()
    app.include_router(voice_router)

    with TestClient(app) as client:
        with client.websocket_connect("/media/stream/fake-token") as websocket:
            websocket.send_text(json.dumps({"event": "start", "start": {"streamSid": "MZ_PR1_NOWELCOME"}}))
            websocket.send_text(json.dumps({"event": "stop"}))

    assert _CapturingTTSService.spoken_texts == []


def test_voice_buffered_barge_in_cancels_active_playback_without_replay(monkeypatch, caplog):
    _BufferedInterruptSTTService.transcribe_calls = 0
    _FakePipeline.captured_user_texts = []
    _FakePipeline.captured_strategies = []

    monkeypatch.setattr(voice_module.jwt, "decode", lambda *_a, **_k: {
        "typ": "ws",
        "call_sid": "CA_PR1_BUFFERED_BARGE",
        "tenant_id": "00000000-0000-0000-0000-000000000001",
    })
    monkeypatch.setattr(voice_module, "open_db_session", lambda *_a, **_k: _FakeDBSession())
    monkeypatch.setattr(voice_module, "STTService", _BufferedInterruptSTTService)
    monkeypatch.setattr(voice_module, "TTSService", _LongTTSService)
    monkeypatch.setattr(voice_module, "ChannelAgentPipeline", _FakePipeline)
    monkeypatch.setattr(settings, "twilio_account_sid", None, raising=False)
    monkeypatch.setattr(settings, "twilio_auth_token", None, raising=False)

    app = FastAPI()
    app.include_router(voice_router)
    caplog.set_level(logging.INFO, logger="app.routers.voice")

    speech_payload = base64.b64encode(_mulaw_speech_chunk()).decode("utf-8")
    silent_payload = base64.b64encode(_mulaw_silence_chunk()).decode("utf-8")

    with TestClient(app) as client:
        with client.websocket_connect("/media/stream/fake-token") as websocket:
            websocket.send_text(json.dumps({"event": "start", "start": {"streamSid": "MZ_PR1_BUFFERED_BARGE"}}))
            for _ in range(50):
                websocket.send_text(json.dumps({"event": "media", "media": {"payload": speech_payload}}))
            for _ in range(50):
                websocket.send_text(json.dumps({"event": "media", "media": {"payload": silent_payload}}))
            for _ in range(50):
                websocket.send_text(json.dumps({"event": "media", "media": {"payload": speech_payload}}))
            for _ in range(50):
                websocket.send_text(json.dumps({"event": "media", "media": {"payload": silent_payload}}))
            websocket.send_text(json.dumps({"event": "stop"}))

    barge_records = [rec for rec in caplog.records if rec.getMessage() == "Twilio voice barge-in detected"]
    assert barge_records
    barge_fields = getattr(barge_records[-1], "extra_fields", {}) or {}
    assert barge_fields.get("detection_mode") == "buffered"
    assert barge_fields.get("barge_in_effective") is True

    cancel_records = [rec for rec in caplog.records if rec.getMessage() == "Twilio assistant playback cancelled"]
    assert cancel_records
    cancelled_ids = {
        (getattr(rec, "extra_fields", {}) or {}).get("playback_id")
        for rec in cancel_records
        if (getattr(rec, "extra_fields", {}) or {}).get("reason") == "barge_in"
    }
    assert cancelled_ids

    completed_ids = {
        (getattr(rec, "extra_fields", {}) or {}).get("playback_id")
        for rec in caplog.records
        if rec.getMessage() == "Twilio assistant playback completed"
    }
    assert cancelled_ids.isdisjoint(completed_ids)


def test_voice_streaming_barge_in_cancels_active_playback(monkeypatch, caplog):
    _FakePipeline.captured_user_texts = []
    _FakePipeline.captured_strategies = []

    monkeypatch.setattr(voice_module.jwt, "decode", lambda *_a, **_k: {
        "typ": "ws",
        "call_sid": "CA_PR1_STREAM_BARGE",
        "tenant_id": "00000000-0000-0000-0000-000000000001",
    })
    monkeypatch.setattr(voice_module, "open_db_session", lambda *_a, **_k: _FakeDBSession())
    monkeypatch.setattr(voice_module, "STTService", _StreamingInterruptSTTService)
    monkeypatch.setattr(voice_module, "TTSService", _LongTTSService)
    monkeypatch.setattr(voice_module, "ChannelAgentPipeline", _FakePipeline)
    monkeypatch.setattr(settings, "twilio_account_sid", None, raising=False)
    monkeypatch.setattr(settings, "twilio_auth_token", None, raising=False)

    app = FastAPI()
    app.include_router(voice_router)
    caplog.set_level(logging.INFO, logger="app.routers.voice")

    speech_payload = base64.b64encode(_mulaw_speech_chunk()).decode("utf-8")

    with TestClient(app) as client:
        with client.websocket_connect("/media/stream/fake-token") as websocket:
            websocket.send_text(json.dumps({"event": "start", "start": {"streamSid": "MZ_PR1_STREAM_BARGE"}}))
            for _ in range(24):
                websocket.send_text(json.dumps({"event": "media", "media": {"payload": speech_payload}}))
            websocket.send_text(json.dumps({"event": "stop"}))

    barge_records = [rec for rec in caplog.records if rec.getMessage() == "Twilio voice barge-in detected"]
    assert barge_records
    barge_fields = getattr(barge_records[-1], "extra_fields", {}) or {}
    assert barge_fields.get("detection_mode") == "streaming"
    assert barge_fields.get("barge_in_effective") is True

    cancel_records = [rec for rec in caplog.records if rec.getMessage() == "Twilio assistant playback cancelled"]
    assert any((getattr(rec, "extra_fields", {}) or {}).get("reason") == "barge_in" for rec in cancel_records)


def test_voice_streaming_stall_attempts_recovery_after_buffered_fallback(monkeypatch, caplog):
    _RecoveringStreamingSTTService.start_session_calls = 0
    _RecoveringStreamingSTTService.transcribe_calls = 0
    _FakePipeline.captured_user_texts = []
    _FakePipeline.captured_strategies = []

    monkeypatch.setattr(voice_module.jwt, "decode", lambda *_a, **_k: {
        "typ": "ws",
        "call_sid": "CA_PR1_RECOVER",
        "tenant_id": "00000000-0000-0000-0000-000000000001",
    })
    monkeypatch.setattr(voice_module, "open_db_session", lambda *_a, **_k: _FakeDBSession())
    monkeypatch.setattr(voice_module, "STTService", _RecoveringStreamingSTTService)
    monkeypatch.setattr(voice_module, "TTSService", _FakeTTSService)
    monkeypatch.setattr(voice_module, "ChannelAgentPipeline", _FakePipeline)
    monkeypatch.setattr(settings, "twilio_account_sid", None, raising=False)
    monkeypatch.setattr(settings, "twilio_auth_token", None, raising=False)

    app = FastAPI()
    app.include_router(voice_router)
    caplog.set_level(logging.INFO, logger="app.routers.voice")

    speech_payload = base64.b64encode(_mulaw_speech_chunk()).decode("utf-8")

    with TestClient(app) as client:
        with client.websocket_connect("/media/stream/fake-token") as websocket:
            websocket.send_text(json.dumps({"event": "start", "start": {"streamSid": "MZ_PR1_RECOVER"}}))
            for _ in range(350):
                websocket.send_text(json.dumps({"event": "media", "media": {"payload": speech_payload}}))
            websocket.send_text(json.dumps({"event": "stop"}))

    assert _RecoveringStreamingSTTService.transcribe_calls >= 1
    assert _RecoveringStreamingSTTService.start_session_calls >= 2
    assert any(rec.getMessage() == "Twilio STT streaming recovered" for rec in caplog.records)

    session_records = [rec for rec in caplog.records if rec.getMessage() == "Twilio voice session ended"]
    assert session_records
    session_fields = getattr(session_records[-1], "extra_fields", {}) or {}
    assert int(session_fields.get("stt_stream_downgrade_count") or 0) >= 1
    assert int(session_fields.get("stt_stream_recovery_successes") or 0) >= 1


def test_voice_entry_twiml_avoids_connection_filler(monkeypatch):
    monkeypatch.setattr(voice_module, "verify_webhook", lambda *_a, **_k: None)
    monkeypatch.setattr(settings, "public_ws_url", "wss://voice.example.com", raising=False)
    monkeypatch.setattr(voice_module.jwt, "encode", lambda *_a, **_k: "fake-stream-token")

    app = FastAPI()

    @app.middleware("http")
    async def _inject_tenant(request, call_next):
        request.state.tenant_id = "00000000-0000-0000-0000-000000000001"
        return await call_next(request)

    app.include_router(voice_router)

    with TestClient(app) as client:
        inbound = client.post("/voice/incoming", data={"CallSid": "CA_TWIML_IN"})
        outbound = client.post("/voice/outbound", data={"CallSid": "CA_TWIML_OUT", "To": "agent-ia"})

    assert inbound.status_code == 200
    assert "Cet appel peut etre enregistre." in inbound.text
    assert "Connexion au service admissions en cours" not in inbound.text

    assert outbound.status_code == 200
    assert "Connexion au service admissions en cours" not in outbound.text
