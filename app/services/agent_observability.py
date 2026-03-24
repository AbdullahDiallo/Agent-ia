from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


@dataclass
class AgentTurnEvent:
    timestamp: datetime
    tenant_id: Optional[str]
    channel: str
    conversation_id: Optional[str]
    response_strategy: str
    response_strategy_category: str
    flow_state: str
    slots_filled: list[str]
    slots_missing: list[str]
    llm_called: bool
    tool_calls: int
    fallback_reason: Optional[str]
    language_locked: Optional[str]
    handoff_trigger: Optional[str]
    raw: dict[str, Any]


def _parse_iso_utc(value: str) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iter_candidate_log_files(log_path: Path, *, include_rotated: bool) -> list[Path]:
    if not include_rotated:
        return [log_path] if log_path.exists() else []
    candidates = [log_path]
    for idx in range(1, 10):
        rotated = log_path.with_name(f"{log_path.name}.{idx}")
        if rotated.exists():
            candidates.append(rotated)
    existing = [p for p in candidates if p.exists() and p.is_file()]
    return sorted(existing, key=lambda p: p.stat().st_mtime)


def load_agent_turn_events_from_logs(
    *,
    log_path: str | Path = "logs/agentia.log",
    tenant_id: Optional[str] = None,
    channel: Optional[str] = None,
    since: Optional[datetime] = None,
    include_rotated: bool = True,
    max_events: int = 20000,
) -> list[AgentTurnEvent]:
    target = Path(log_path)
    since_utc = since.astimezone(timezone.utc) if since and since.tzinfo else (since.replace(tzinfo=timezone.utc) if since else None)
    wanted_tenant = str(tenant_id) if tenant_id else None
    wanted_channel = str(channel or "").strip().lower() or None

    out: list[AgentTurnEvent] = []
    for file_path in _iter_candidate_log_files(target, include_rotated=include_rotated):
        try:
            with file_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or not line.startswith("{"):
                        continue
                    try:
                        payload = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    if str(payload.get("message") or "") != "agent_turn_processed":
                        continue
                    ts = _parse_iso_utc(str(payload.get("timestamp") or ""))
                    if ts is None:
                        continue
                    if since_utc and ts < since_utc:
                        continue
                    event_tenant = str(payload.get("tenant_id") or "") or None
                    if wanted_tenant and event_tenant != wanted_tenant:
                        continue
                    event_channel = str(payload.get("channel") or "").strip().lower() or "unknown"
                    if wanted_channel and event_channel != wanted_channel:
                        continue
                    event = AgentTurnEvent(
                        timestamp=ts,
                        tenant_id=event_tenant,
                        channel=event_channel,
                        conversation_id=(str(payload.get("conversation_id") or "").strip() or None),
                        response_strategy=str(payload.get("response_strategy") or ""),
                        response_strategy_category=str(payload.get("response_strategy_category") or ""),
                        flow_state=str(payload.get("flow_state") or ""),
                        slots_filled=_coerce_str_list(payload.get("slots_filled")),
                        slots_missing=_coerce_str_list(payload.get("slots_missing")),
                        llm_called=bool(payload.get("llm_called")),
                        tool_calls=_coerce_int(payload.get("tool_calls")),
                        fallback_reason=(str(payload.get("fallback_reason") or "").strip() or None),
                        language_locked=(str(payload.get("language_locked") or "").strip() or None),
                        handoff_trigger=(str(payload.get("handoff_trigger") or "").strip() or None),
                        raw=payload,
                    )
                    out.append(event)
                    if len(out) > max_events:
                        out = out[-max_events:]
        except Exception:
            continue
    out.sort(key=lambda e: (e.timestamp, e.conversation_id or "", e.channel))
    return out


def _coerce_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _coerce_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except Exception:
        return 0


def compute_agent_observability_kpis(events: Iterable[AgentTurnEvent]) -> dict[str, Any]:
    rows = list(events)
    total_turns = len(rows)
    by_channel_total: dict[str, int] = defaultdict(int)
    by_channel_provider_errors: dict[str, int] = defaultdict(int)
    fallback_turns = 0
    llm_turns = 0
    tool_call_turns = 0
    handoff_turns = 0
    repeated_prompt_turns = 0
    repeated_prompt_checks = 0

    conv_events: dict[str, list[AgentTurnEvent]] = defaultdict(list)

    for ev in rows:
        by_channel_total[ev.channel] += 1
        if ev.llm_called:
            llm_turns += 1
        if ev.tool_calls > 0:
            tool_call_turns += 1
        if ev.fallback_reason:
            fallback_turns += 1
        if ev.fallback_reason == "llm_provider_error":
            by_channel_provider_errors[ev.channel] += 1
        if ev.handoff_trigger:
            handoff_turns += 1
        if ev.conversation_id:
            conv_events[ev.conversation_id].append(ev)

    booking_started_conversations = 0
    booking_completed_conversations = 0
    booking_turns_to_submit: list[int] = []
    conversations_with_handoff = 0
    conversations_with_language_flip = 0

    for conv_id, events_for_conv in conv_events.items():
        events_for_conv.sort(key=lambda e: e.timestamp)
        seen_booking = False
        first_booking_idx: Optional[int] = None
        submitted_idx: Optional[int] = None
        last_collect_signature: Optional[tuple[str, tuple[str, ...]]] = None
        handoff_in_conv = False

        languages: list[str] = []
        for idx, ev in enumerate(events_for_conv):
            if ev.handoff_trigger:
                handoff_in_conv = True
            if ev.language_locked in {"fr", "en", "wo"}:
                if not languages or languages[-1] != ev.language_locked:
                    languages.append(ev.language_locked)

            if ev.flow_state in {
                "booking_collect_contact",
                "booking_collect_datetime",
                "booking_confirm",
                "booking_submitted",
            }:
                seen_booking = True
                if first_booking_idx is None:
                    first_booking_idx = idx
            if ev.flow_state == "booking_submitted" and submitted_idx is None:
                submitted_idx = idx

            if ev.flow_state in {"booking_collect_contact", "booking_collect_datetime"}:
                repeated_prompt_checks += 1
                signature = (
                    ev.flow_state,
                    tuple(sorted(ev.slots_missing)),
                )
                if last_collect_signature is not None and signature == last_collect_signature and ev.slots_missing:
                    repeated_prompt_turns += 1
                last_collect_signature = signature
            elif ev.flow_state not in {"booking_confirm", "booking_submitted"}:
                last_collect_signature = None

        if seen_booking:
            booking_started_conversations += 1
        if submitted_idx is not None:
            booking_completed_conversations += 1
            start_idx = first_booking_idx if first_booking_idx is not None else 0
            booking_turns_to_submit.append(max(1, (submitted_idx - start_idx + 1)))
        if handoff_in_conv:
            conversations_with_handoff += 1
        if len(set(languages)) > 1:
            conversations_with_language_flip += 1

    total_conversations = len(conv_events)

    def _rate(num: int, den: int) -> float:
        if den <= 0:
            return 0.0
        return round((num / den) * 100.0, 2)

    provider_error_rate_by_channel = {
        ch: {
            "errors": int(by_channel_provider_errors.get(ch, 0)),
            "total_turns": int(total),
            "rate": _rate(int(by_channel_provider_errors.get(ch, 0)), int(total)),
        }
        for ch, total in sorted(by_channel_total.items())
    }

    return {
        "summary": {
            "total_turns": total_turns,
            "total_conversations": total_conversations,
            "channels": dict(sorted(by_channel_total.items())),
        },
        "instrumentation": {
            "llm_turns": llm_turns,
            "llm_turn_rate": _rate(llm_turns, total_turns),
            "tool_call_turns": tool_call_turns,
            "tool_call_turn_rate": _rate(tool_call_turns, total_turns),
        },
        "kpis": {
            "repeat_question_rate": _rate(repeated_prompt_turns, repeated_prompt_checks),
            "repeat_question_events": repeated_prompt_turns,
            "fallback_rate": _rate(fallback_turns, total_turns),
            "fallback_turns": fallback_turns,
            "undesired_language_change_rate": _rate(conversations_with_language_flip, total_conversations),
            "language_flip_conversations": conversations_with_language_flip,
            "booking_completion_rate": _rate(booking_completed_conversations, booking_started_conversations),
            "booking_started_conversations": booking_started_conversations,
            "booking_completed_conversations": booking_completed_conversations,
            "avg_turns_to_booking_submission": round(sum(booking_turns_to_submit) / len(booking_turns_to_submit), 2)
            if booking_turns_to_submit
            else 0.0,
            "handoff_rate": _rate(conversations_with_handoff, total_conversations),
            "handoff_conversations": conversations_with_handoff,
            "provider_error_rate_by_channel": provider_error_rate_by_channel,
        },
    }


def build_agent_observability_report(
    *,
    tenant_id: Optional[str],
    channel: Optional[str] = None,
    hours: int = 24,
    include_rotated: bool = True,
    log_path: str | Path = "logs/agentia.log",
    max_events: int = 20000,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    hours = max(1, min(int(hours), 24 * 30))
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    since = current - timedelta(hours=hours)

    events = load_agent_turn_events_from_logs(
        log_path=log_path,
        tenant_id=tenant_id,
        channel=channel,
        since=since,
        include_rotated=include_rotated,
        max_events=max_events,
    )
    report = compute_agent_observability_kpis(events)
    report["window"] = {
        "hours": hours,
        "since": since.isoformat(),
        "until": current.isoformat(),
        "channel": channel,
        "tenant_id": tenant_id,
    }
    report["events_scanned"] = len(events)
    report["source"] = {
        "log_path": str(log_path),
        "include_rotated": bool(include_rotated),
        "max_events": int(max_events),
    }
    return report


__all__ = [
    "AgentTurnEvent",
    "load_agent_turn_events_from_logs",
    "compute_agent_observability_kpis",
    "build_agent_observability_report",
]
