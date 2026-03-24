from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

from app.services.llm import LLMService


class _FakeCompletions:
    def __init__(self, contents: list[str]) -> None:
        self._contents = contents
        self._index = 0

    def create(self, **_kwargs):
        if self._index >= len(self._contents):
            raise RuntimeError("no_more_mock_responses")
        content = self._contents[self._index]
        self._index += 1
        message = SimpleNamespace(content=content, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _FakeClient:
    def __init__(self, contents: list[str]) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(contents))


def _service_with_responses(contents: list[str]) -> LLMService:
    service = LLMService()
    service.api_key = "test-openai-key"
    service._client = _FakeClient(contents)
    return service


def test_structured_extraction_contract_20_inputs_min_95_percent_valid_and_no_silent_fail(caplog):
    representative_inputs = [
        "Oui",
        "Non",
        "Montrez-moi les programmes disponibles",
        "Je veux les details de Genie Logiciel",
        "Je veux prendre un rendez-vous",
        "C'est urgent",
        "Merci beaucoup",
        "Je m'appelle Abdoulaye Diallo",
        "Mon email est abdoulaye@example.com",
        "Mon numero est +221776625059",
        "Rendez-vous le 28/02/2026",
        "A 15h",
        "Niveau Bac+2",
        "Genie Logiciel",
        "Licence Pro",
        "Catalogue des filieres",
        "Programmes s'il vous plait",
        "Can we schedule an admission appointment?",
        "I need details about tuition",
        "Waa ngi ci rendez-vous",
    ]
    model_outputs = [
        json.dumps({"is_affirmative": True}),
        json.dumps({"is_negative": True}),
        json.dumps({"catalog_request": True, "catalog_subject": "program"}),
        json.dumps({"details_request": True, "track_name": "Genie Logiciel"}),
        json.dumps({"appointment_request": True}),
        json.dumps({"urgency_request": True}),
        json.dumps({"gratitude_closure": True}),
        json.dumps({"full_name": "Abdoulaye Diallo"}),
        json.dumps({"email": "abdoulaye@example.com"}),
        json.dumps({"phone": "+221776625059"}),
        json.dumps({"appointment_date": "2026-02-28"}),
        json.dumps({"appointment_time": "15:00"}),
        json.dumps({"admission_level": "Bac+2"}),
        json.dumps({"track_name": "Genie Logiciel"}),
        json.dumps({"program_name": "Licence Pro"}),
        json.dumps({"catalog_request": True, "catalog_subject": "track"}),
        json.dumps({"catalog_request": True, "catalog_subject": "program"}),
        json.dumps({"appointment_request": True, "catalog_request": False}),
        json.dumps({"details_request": True, "program_name": "Data Science"}),
        json.dumps({"unknown_field": "invalid_schema"}),  # expected failure (1/20)
    ]

    service = _service_with_responses(model_outputs)
    caplog.set_level(logging.INFO, logger="agentia.app.services.llm")

    valid_count = 0
    for text in representative_inputs:
        session_state = {"channel": "call", "structured_extraction_fail_count": 0}
        result = asyncio.run(service.extract_structured_message(text, session_state=session_state))
        if isinstance(result, dict) and bool(result):
            valid_count += 1

    assert valid_count >= 19  # 95% minimum over 20 representative inputs

    extraction_logs = [r for r in caplog.records if r.getMessage() == "LLM structured extraction completed"]
    assert len(extraction_logs) == 20
    for record in extraction_logs:
        fields = getattr(record, "extra_fields", {}) or {}
        assert "structured_extraction_success" in fields
        assert "structured_extraction_error_type" in fields
        assert "extraction_latency_ms" in fields

    failure_logs = [
        r for r in extraction_logs if not bool((getattr(r, "extra_fields", {}) or {}).get("structured_extraction_success"))
    ]
    assert len(failure_logs) == 1
    failure_fields = getattr(failure_logs[0], "extra_fields", {}) or {}
    assert failure_fields.get("structured_extraction_error_type") == "schema_unknown_keys"


def test_structured_extraction_failure_increments_fail_count_and_logs_error(caplog):
    service = _service_with_responses([json.dumps({"unexpected": "field"})])
    caplog.set_level(logging.INFO, logger="agentia.app.services.llm")

    session_state = {
        "channel": "chat",
        "structured_extraction_fail_count": 2,
    }
    result = asyncio.run(service.extract_structured_message("test", session_state=session_state))

    assert result is None
    assert session_state["structured_extraction_fail_count"] == 3
    assert session_state["structured_extraction_success"] is False
    assert session_state["structured_extraction_error_type"] == "schema_unknown_keys"
    assert isinstance(session_state["extraction_latency_ms"], int)

    extraction_logs = [r for r in caplog.records if r.getMessage() == "LLM structured extraction completed"]
    assert extraction_logs
    last_fields = getattr(extraction_logs[-1], "extra_fields", {}) or {}
    assert last_fields.get("structured_extraction_success") is False
    assert last_fields.get("structured_extraction_error_type") == "schema_unknown_keys"
