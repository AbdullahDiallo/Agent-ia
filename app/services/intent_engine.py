from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

from ..logger import get_logger

logger = get_logger(__name__)

INTENTS_FILE = Path(__file__).resolve().parents[1] / "intents.yaml"


@dataclass
class IntentDecision:
    intent: str
    score: float
    action: str
    matched_keywords: List[str]


@lru_cache(maxsize=1)
def load_intent_matrix() -> Dict[str, Any]:
    raw = INTENTS_FILE.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except Exception as exc:
        logger.error("Invalid intents matrix format", extra={"extra_fields": {"error": str(exc)}})
        return {
            "version": 1,
            "default_intent": "general_information",
            "intents": [],
            "sensitive_keywords": {},
            "escalate_on_failures": 2,
        }


def _lang_code(lang: str | None) -> str:
    value = (lang or "fr").strip().lower()
    if value in {"fr", "en", "wo"}:
        return value
    return "fr"


def _normalize_for_matching(text: str) -> str:
    """Normalize text for intent matching: lowercase, remove accents, collapse whitespace."""
    lowered = (text or "").lower().strip()
    normalized = unicodedata.normalize("NFKD", lowered)
    ascii_like = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(ascii_like.split())


def _collect_keywords(intent_row: Dict[str, Any], lang: str) -> List[str]:
    keywords = intent_row.get("keywords") or {}
    lang_keywords = keywords.get(lang) or []
    fallback_keywords = keywords.get("*") or []
    merged = [str(x).strip().lower() for x in [*lang_keywords, *fallback_keywords] if str(x).strip()]
    # Preserve order and uniqueness.
    return list(dict.fromkeys(merged))


def _compute_intent_score(
    normalized_text: str,
    keywords: List[str],
) -> tuple[float, List[str]]:
    """Compute a weighted intent score.

    Improvements over simple ratio:
    - Multi-word keywords get higher weight
    - Keywords appearing early in the text get a bonus
    - Exact phrase matches get a bonus
    """
    if not keywords or not normalized_text:
        return 0.0, []

    matched: List[str] = []
    total_weight = 0.0
    matched_weight = 0.0

    text_words = set(normalized_text.split())
    text_len = len(normalized_text)

    for kw in keywords:
        kw_normalized = _normalize_for_matching(kw)
        kw_word_count = len(kw_normalized.split())
        # Multi-word keywords are more specific, so they get higher weight
        base_weight = 1.0 + (kw_word_count - 1) * 0.5
        total_weight += base_weight

        if kw_normalized in normalized_text:
            matched.append(kw)
            weight = base_weight

            # Bonus for early appearance (first third of text)
            pos = normalized_text.find(kw_normalized)
            if pos >= 0 and pos < text_len / 3:
                weight *= 1.2

            # Bonus for exact word boundary match (not substring)
            if kw_word_count == 1 and kw_normalized in text_words:
                weight *= 1.1

            matched_weight += weight

    if total_weight <= 0:
        return 0.0, matched

    score = round(matched_weight / total_weight, 3)
    return score, matched


def detect_intent(
    text: str,
    *,
    lang: str | None,
    failure_count: int = 0,
) -> IntentDecision:
    payload = load_intent_matrix()
    normalized = _normalize_for_matching(text)
    lang_code = _lang_code(lang)
    default_intent = str(payload.get("default_intent") or "general_information")
    default_action = "respond"  # Changed from ask_clarification to respond
    best = IntentDecision(intent=default_intent, score=0.0, action=default_action, matched_keywords=[])

    for row in payload.get("intents") or []:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        keywords = _collect_keywords(row, lang_code)
        if not keywords:
            continue

        score, matched = _compute_intent_score(normalized, keywords)

        if not matched:
            continue
        if score > best.score:
            best = IntentDecision(
                intent=name,
                score=score,
                action=str(row.get("action") or "respond"),
                matched_keywords=matched,
            )

    sensitive_keywords = [
        str(x).strip().lower()
        for x in ((payload.get("sensitive_keywords") or {}).get(lang_code) or [])
        if str(x).strip()
    ]
    is_sensitive = any(k in normalized for k in sensitive_keywords)
    failure_threshold = int(payload.get("escalate_on_failures") or 2)

    if is_sensitive or int(failure_count) >= failure_threshold:
        best.action = "escalate_human"
        if best.score == 0:
            best.intent = "escalade_humaine"

    # For low-confidence matches, prefer to let LLM handle it
    if best.score > 0 and best.score < 0.15 and best.action != "escalate_human":
        best.action = "respond"  # Let LLM handle low-confidence intents

    return best


def escalation_message(lang: str | None) -> str:
    code = _lang_code(lang)
    if code == "en":
        return (
            "I am transferring your request to a human admissions advisor now. "
            "You will be contacted shortly."
        )
    if code == "wo":
        return (
            "Dinaa jox sa laaj bi ab conseiller admissions bu nit. "
            "Dinañu la woo ci ni mu gën a gaaw."
        )
    return (
        "Je transfère votre demande à un conseiller admissions humain. "
        "Vous serez recontacté rapidement."
    )


def clarification_message(lang: str | None) -> str:
    code = _lang_code(lang)
    if code == "en":
        return "Could you clarify your request (program, level, and preferred schedule)?"
    if code == "wo":
        return "Mën nga leeral sa laaj (filière, niveau ak waxtu bi nga bëgg)?"
    return "Pouvez-vous préciser votre demande (filière, niveau et créneau souhaité) ?"
