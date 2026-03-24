"""LLM cost tracking — persists usage to DB and enforces quotas."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..logger import get_logger
from ..models import LLMUsageLog

logger = get_logger(__name__)

# Pricing per 1M tokens (USD)
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "gpt-4": {"input": 30.00, "output": 60.00},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    "text-embedding-3-small": {"input": 0.02, "output": 0.0},
    "text-embedding-3-large": {"input": 0.13, "output": 0.0},
}

ELEVENLABS_PRICING: Dict[str, float] = {
    "eleven_turbo_v2_5": 0.00003,
    "eleven_multilingual_v2": 0.00003,
}

DEEPGRAM_PRICING: Dict[str, float] = {
    "nova-2": 0.0043,
    "nova": 0.0043,
}


def estimate_llm_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pricing = MODEL_PRICING.get(model, MODEL_PRICING.get("gpt-4o", {}))
    input_cost = (prompt_tokens / 1_000_000) * pricing.get("input", 0)
    output_cost = (completion_tokens / 1_000_000) * pricing.get("output", 0)
    return round(input_cost + output_cost, 6)


def estimate_tts_cost(model: str, char_count: int) -> float:
    return round(char_count * ELEVENLABS_PRICING.get(model, 0.00003), 6)


def estimate_stt_cost(model: str, duration_seconds: float) -> float:
    return round((duration_seconds / 60.0) * DEEPGRAM_PRICING.get(model, 0.0043), 6)


class LLMCostTracker:
    """Tracks and persists LLM/TTS/STT costs per tenant to the database."""

    def __init__(self, db: Session, *, tenant_id: str) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self._entries: list[Dict[str, Any]] = []

    def record_llm_call(
        self,
        *,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        channel: str = "",
        call_type: str = "generate",
        latency_ms: int = 0,
        conversation_id: Optional[str] = None,
    ) -> float:
        cost = estimate_llm_cost(model, prompt_tokens, completion_tokens)
        self._persist_log(
            model=model,
            call_type=call_type,
            channel=channel,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            conversation_id=conversation_id,
        )
        return cost

    def record_tts_call(self, *, model: str, char_count: int, latency_ms: int = 0) -> float:
        cost = estimate_tts_cost(model, char_count)
        self._persist_log(
            model=model, call_type="tts", channel="voice",
            prompt_tokens=char_count, completion_tokens=0, total_tokens=char_count,
            cost_usd=cost, latency_ms=latency_ms,
        )
        return cost

    def record_stt_call(self, *, model: str, duration_seconds: float, latency_ms: int = 0) -> float:
        cost = estimate_stt_cost(model, duration_seconds)
        self._persist_log(
            model=model, call_type="stt", channel="voice",
            prompt_tokens=0, completion_tokens=0, total_tokens=0,
            cost_usd=cost, latency_ms=latency_ms,
        )
        return cost

    def _persist_log(self, **kwargs: Any) -> None:
        try:
            from uuid import UUID as _UUID
            conv_id = kwargs.pop("conversation_id", None)
            log = LLMUsageLog(
                tenant_id=_UUID(str(self.tenant_id)),
                conversation_id=_UUID(str(conv_id)) if conv_id else None,
                **kwargs,
            )
            self.db.add(log)
            self.db.flush()
            self._entries.append(kwargs)
        except Exception as e:
            logger.warning(f"Failed to persist LLM usage log: {e}")

    def persist_to_quota(self) -> None:
        """Increment the ai_tokens quota for the current billing period."""
        from .tenant_governance import check_and_increment_quota
        total_tokens = sum(e.get("total_tokens", 0) for e in self._entries)
        if total_tokens > 0:
            try:
                check_and_increment_quota(
                    self.db, tenant_id=self.tenant_id, metric="ai_tokens", increment=total_tokens,
                )
            except Exception as e:
                logger.warning(f"Failed to persist LLM cost quota: {e}")

    @property
    def session_total_cost(self) -> float:
        return round(sum(e.get("cost_usd", 0) for e in self._entries), 6)

    @property
    def session_total_tokens(self) -> int:
        return sum(e.get("total_tokens", 0) for e in self._entries)
