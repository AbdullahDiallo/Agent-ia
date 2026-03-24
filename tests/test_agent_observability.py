from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.services.agent_observability import (
    build_agent_observability_report,
    compute_agent_observability_kpis,
    load_agent_turn_events_from_logs,
)


TENANT_A = "00000000-0000-0000-0000-0000000000aa"
TENANT_B = "00000000-0000-0000-0000-0000000000bb"


def _event(
    *,
    ts: str,
    tenant_id: str,
    channel: str,
    conversation_id: str,
    response_strategy: str,
    response_strategy_category: str,
    flow_state: str,
    slots_filled: list[str] | None = None,
    slots_missing: list[str] | None = None,
    llm_called: bool = False,
    tool_calls: int = 0,
    fallback_reason: str | None = None,
    language_locked: str | None = "fr",
    handoff_trigger: str | None = None,
) -> dict:
    return {
        "timestamp": ts,
        "level": "INFO",
        "logger": "agentia.app.services.channel_agent_pipeline",
        "message": "agent_turn_processed",
        "tenant_id": tenant_id,
        "channel": channel,
        "conversation_id": conversation_id,
        "response_strategy": response_strategy,
        "response_strategy_category": response_strategy_category,
        "flow_state": flow_state,
        "slots_filled": slots_filled or [],
        "slots_missing": slots_missing or [],
        "llm_called": llm_called,
        "tool_calls": tool_calls,
        "tool_call_names": ["get_track_tuition"] if tool_calls else [],
        "fallback_reason": fallback_reason,
        "language_locked": language_locked,
        "handoff_trigger": handoff_trigger,
        "duration_ms": 42,
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    path.write_text(payload + "\n", encoding="utf-8")


def test_load_agent_turn_events_filters_tenant_channel_and_ignores_invalid_lines(tmp_path):
    log_file = tmp_path / "agentia.log"
    rows = [
        {"message": "not_me", "timestamp": "2026-02-23T10:00:00Z"},
        _event(
            ts="2026-02-23T10:01:00Z",
            tenant_id=TENANT_A,
            channel="chat",
            conversation_id="conv-a1",
            response_strategy="deterministic_catalog",
            response_strategy_category="deterministic",
            flow_state="browsing_catalog",
        ),
        _event(
            ts="2026-02-23T10:02:00Z",
            tenant_id=TENANT_B,
            channel="whatsapp",
            conversation_id="conv-b1",
            response_strategy="llm",
            response_strategy_category="llm",
            flow_state="track_selected",
            llm_called=True,
        ),
    ]
    log_file.write_text("invalid-line\n", encoding="utf-8")
    with log_file.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")

    events = load_agent_turn_events_from_logs(log_path=log_file, tenant_id=TENANT_A, channel="chat", include_rotated=False)
    assert len(events) == 1
    assert events[0].tenant_id == TENANT_A
    assert events[0].channel == "chat"
    assert events[0].conversation_id == "conv-a1"


def test_compute_agent_observability_kpis_covers_booking_repeat_fallback_language_flip_and_provider_errors(tmp_path):
    events = load_agent_turn_events_from_logs(
        log_path=_build_synthetic_log_for_compute(tmp_path),
        tenant_id=TENANT_A,
        include_rotated=False,
    )
    report = compute_agent_observability_kpis(events)
    kpis = report["kpis"]

    assert report["summary"]["total_turns"] == 8
    assert report["summary"]["channels"]["chat"] == 6
    assert report["summary"]["channels"]["whatsapp"] == 2
    assert kpis["repeat_question_events"] == 1
    assert kpis["repeat_question_rate"] > 0
    assert kpis["fallback_turns"] == 1
    assert kpis["fallback_rate"] > 0
    assert kpis["language_flip_conversations"] == 1
    assert kpis["booking_started_conversations"] == 1
    assert kpis["booking_completed_conversations"] == 1
    assert kpis["booking_completion_rate"] == 100.0
    assert kpis["avg_turns_to_booking_submission"] == 4.0
    assert kpis["handoff_conversations"] == 1
    assert kpis["provider_error_rate_by_channel"]["whatsapp"]["errors"] == 1
    assert kpis["provider_error_rate_by_channel"]["whatsapp"]["rate"] == 50.0


def test_build_agent_observability_report_applies_window_and_metadata(tmp_path):
    log_file = tmp_path / "agentia.log"
    rows = [
        _event(
            ts="2026-02-23T08:00:00Z",
            tenant_id=TENANT_A,
            channel="chat",
            conversation_id="conv-1",
            response_strategy="deterministic_catalog",
            response_strategy_category="deterministic",
            flow_state="browsing_catalog",
        ),
        _event(
            ts="2026-02-23T11:30:00Z",
            tenant_id=TENANT_A,
            channel="chat",
            conversation_id="conv-2",
            response_strategy="llm",
            response_strategy_category="llm",
            flow_state="track_selected",
            llm_called=True,
            tool_calls=1,
        ),
    ]
    _write_jsonl(log_file, rows)

    now = datetime(2026, 2, 23, 12, 0, tzinfo=timezone.utc)
    report = build_agent_observability_report(
        tenant_id=TENANT_A,
        channel="chat",
        hours=2,
        include_rotated=False,
        log_path=log_file,
        now=now,
    )
    assert report["events_scanned"] == 1
    assert report["window"]["hours"] == 2
    assert report["window"]["tenant_id"] == TENANT_A
    assert report["window"]["channel"] == "chat"
    assert report["source"]["log_path"] == str(log_file)
    assert report["summary"]["total_turns"] == 1


def _build_synthetic_log_for_compute(tmp_path: Path) -> Path:
    path = tmp_path / "agentia.log"
    rows = [
        _event(
            ts="2026-02-23T10:00:00Z",
            tenant_id=TENANT_A,
            channel="chat",
            conversation_id="conv-booking",
            response_strategy="deterministic_booking_collect_contact",
            response_strategy_category="deterministic",
            flow_state="booking_collect_contact",
            slots_filled=["track_name"],
            slots_missing=["full_name", "contact", "appointment_date", "appointment_time"],
            language_locked="fr",
        ),
        _event(
            ts="2026-02-23T10:01:00Z",
            tenant_id=TENANT_A,
            channel="chat",
            conversation_id="conv-booking",
            response_strategy="deterministic_booking_collect_contact",
            response_strategy_category="deterministic",
            flow_state="booking_collect_contact",
            slots_filled=["track_name"],
            slots_missing=["full_name", "contact", "appointment_date", "appointment_time"],
            language_locked="fr",
        ),
        _event(
            ts="2026-02-23T10:02:00Z",
            tenant_id=TENANT_A,
            channel="chat",
            conversation_id="conv-booking",
            response_strategy="deterministic_booking_collect_datetime",
            response_strategy_category="deterministic",
            flow_state="booking_collect_datetime",
            slots_filled=["track_name", "full_name", "phone"],
            slots_missing=["appointment_date", "appointment_time"],
            language_locked="fr",
        ),
        _event(
            ts="2026-02-23T10:03:00Z",
            tenant_id=TENANT_A,
            channel="chat",
            conversation_id="conv-booking",
            response_strategy="deterministic_booking_submitted",
            response_strategy_category="deterministic",
            flow_state="booking_submitted",
            slots_filled=["track_name", "full_name", "phone", "appointment_date", "appointment_time"],
            slots_missing=[],
            language_locked="en",
        ),
        _event(
            ts="2026-02-23T10:04:00Z",
            tenant_id=TENANT_A,
            channel="whatsapp",
            conversation_id="conv-wa",
            response_strategy="fallback_contextual",
            response_strategy_category="fallback",
            flow_state="track_selected",
            llm_called=True,
            tool_calls=1,
            fallback_reason="llm_provider_error",
            handoff_trigger="intent_engine_escalate",
            language_locked="fr",
        ),
        _event(
            ts="2026-02-23T10:05:00Z",
            tenant_id=TENANT_A,
            channel="whatsapp",
            conversation_id="conv-wa",
            response_strategy="deterministic_track_details",
            response_strategy_category="deterministic",
            flow_state="track_selected",
            language_locked="fr",
        ),
        _event(
            ts="2026-02-23T10:06:00Z",
            tenant_id=TENANT_A,
            channel="chat",
            conversation_id="conv-open",
            response_strategy="llm",
            response_strategy_category="llm",
            flow_state="browsing_catalog",
            llm_called=True,
            tool_calls=0,
            language_locked="fr",
        ),
        _event(
            ts="2026-02-23T10:07:00Z",
            tenant_id=TENANT_A,
            channel="chat",
            conversation_id="conv-open-2",
            response_strategy="deterministic_catalog",
            response_strategy_category="deterministic",
            flow_state="browsing_catalog",
            language_locked="fr",
        ),
        _event(
            ts="2026-02-23T10:08:00Z",
            tenant_id=TENANT_B,
            channel="chat",
            conversation_id="conv-foreign",
            response_strategy="deterministic_catalog",
            response_strategy_category="deterministic",
            flow_state="browsing_catalog",
            language_locked="fr",
        ),
    ]
    _write_jsonl(path, rows)
    return path
