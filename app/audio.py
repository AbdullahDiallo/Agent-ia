from __future__ import annotations

import io
import base64
import wave
from typing import List

import numpy as np
from scipy.signal import resample_poly
import audioop


TARGET_SR = 8000


def _read_wav_mono_pcm16(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    buf = io.BytesIO(wav_bytes)
    with wave.open(buf, "rb") as wf:
        sr = wf.getframerate()
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        n_frames = wf.getnframes()
        pcm = wf.readframes(n_frames)
    if sampwidth != 2:
        raise ValueError("expected 16-bit PCM WAV input")
    arr = np.frombuffer(pcm, dtype=np.int16)
    if n_channels == 2:
        arr = arr.reshape(-1, 2).mean(axis=1).astype(np.int16)
    return arr, sr


def _resample_to_8k_pcm16(x: np.ndarray, sr: int) -> bytes:
    if sr == TARGET_SR:
        pcm16 = x.astype(np.int16)
    else:
        # resample using polyphase filtering for quality
        gcd = np.gcd(sr, TARGET_SR)
        up = TARGET_SR // gcd
        down = sr // gcd
        y = resample_poly(x.astype(np.float32), up, down)
        y = np.clip(y, -32768, 32767)
        pcm16 = y.astype(np.int16)
    return pcm16.tobytes()


def _pcm16_to_mulaw_bytes(pcm16_le_bytes: bytes) -> bytes:
    return audioop.lin2ulaw(pcm16_le_bytes, 2)


def wav_to_mulaw8k_frames(wav_bytes: bytes, frame_ms: int = 20) -> List[bytes]:
    if not wav_bytes or len(wav_bytes) < 44:  # WAV header is 44 bytes minimum
        return []
    pcm16_arr, sr = _read_wav_mono_pcm16(wav_bytes)
    pcm16_le = _resample_to_8k_pcm16(pcm16_arr, sr)
    mulaw = _pcm16_to_mulaw_bytes(pcm16_le)
    samples_per_frame = int(TARGET_SR * frame_ms / 1000)
    frames: List[bytes] = []
    for i in range(0, len(mulaw), samples_per_frame):
        chunk = mulaw[i : i + samples_per_frame]
        if chunk:
            frames.append(chunk)
    return frames


def frames_to_base64_payloads(frames: List[bytes]) -> List[str]:
    return [base64.b64encode(f).decode() for f in frames]
