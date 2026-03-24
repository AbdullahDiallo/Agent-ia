from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.services import llm as llm_module
from app.services import llm_tools as llm_tools_module
from app.services.llm import LLMService, MUTATING_TOOL_NAMES, READ_ONLY_TOOL_NAMES
from app.services.knowledge_resolver import KnowledgeContext, KnowledgeSnippet
from app.services.llm_tools import execute_function_call


class _FakeToolCall:
    def __init__(self, name: str, arguments: dict[str, object], *, tool_call_id: str = "tool_1") -> None:
        self.id = tool_call_id
        self.function = SimpleNamespace(name=name, arguments=json.dumps(arguments))


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            message = SimpleNamespace(
                content="",
                tool_calls=[_FakeToolCall("get_track_tuition", {"query": "Genie Logiciel"})],
            )
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])
        message = SimpleNamespace(content="final read-only reply", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _FakeClient:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


def _configured_service(client) -> LLMService:
    service = LLMService()
    service.api_key = "test-openai-key"
    service._client = client
    return service


def _tool_names(tools: list[dict[str, object]]) -> list[str]:
    names: list[str] = []
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) else {}
        if isinstance(function, dict):
            name = str(function.get("name") or "").strip()
            if name:
                names.append(name)
    return names


def test_tool_policy_defaults_to_read_only_tools():
    service = LLMService()

    advertised_tools, allowed_names, blocked_reasons = service._resolve_tool_policy({"channel": "chat"})

    assert set(_tool_names(advertised_tools)) == set(READ_ONLY_TOOL_NAMES)
    assert allowed_names == set(READ_ONLY_TOOL_NAMES)
    for tool_name in MUTATING_TOOL_NAMES:
        assert blocked_reasons[tool_name] == "mutating_tools_disabled"


def test_tool_policy_allows_mutating_tools_only_with_explicit_booking_guard():
    service = LLMService()

    advertised_tools, allowed_names, blocked_reasons = service._resolve_tool_policy(
        {
            "channel": "chat",
            "allow_mutating_tools": True,
            "tool_mutation_scope": "booking",
            "conversation_active_flow": "booking_confirm",
            "response_strategy": "deterministic_booking_confirm",
            "booking_confirmation_obtained": True,
            "personal_data_consent_obtained": True,
            "conversation_slots": {
                "person_id": "00000000-0000-0000-0000-00000000c001".replace("c", "0"),
                "track_name": "Genie Logiciel",
                "appointment_date": "2026-09-01",
                "appointment_time": "10:00",
            },
        }
    )

    assert set(_tool_names(advertised_tools)) == set(READ_ONLY_TOOL_NAMES | MUTATING_TOOL_NAMES)
    assert MUTATING_TOOL_NAMES.issubset(allowed_names)
    assert blocked_reasons == {}


def test_generate_reply_with_tools_advertises_read_only_tools_and_executes_allowed_tool(monkeypatch):
    fake_completions = _FakeCompletions()
    service = _configured_service(_FakeClient(fake_completions))
    captured: dict[str, object] = {}

    async def fake_execute_function_call(db, function_name, arguments, *, allowed_function_names=None):
        captured["db"] = db
        captured["function_name"] = function_name
        captured["arguments"] = arguments
        captured["allowed_function_names"] = set(allowed_function_names or set())
        return {"success": True, "items": [{"track_name": "Genie Logiciel"}]}

    monkeypatch.setattr(llm_tools_module, "execute_function_call", fake_execute_function_call)

    reply = asyncio.run(
        service.generate_reply_with_tools(
            "Quels sont les frais de Genie Logiciel ?",
            session_state={"channel": "chat", "lang_detected": "fr"},
            db_session=SimpleNamespace(info={"tenant_id": "00000000-0000-0000-0000-000000000777"}),
        )
    )

    first_call = fake_completions.calls[0]
    advertised_tool_names = _tool_names(first_call.get("tools") or [])

    assert set(advertised_tool_names) == set(READ_ONLY_TOOL_NAMES)
    assert captured["function_name"] == "get_track_tuition"
    assert captured["arguments"] == {"query": "Genie Logiciel"}
    assert set(captured["allowed_function_names"]) == set(READ_ONLY_TOOL_NAMES)
    assert not (set(captured["allowed_function_names"]) & set(MUTATING_TOOL_NAMES))
    assert reply == "final read-only reply"


def test_execute_function_call_blocks_mutating_tool_when_not_allowed():
    result = asyncio.run(
        execute_function_call(
            SimpleNamespace(),
            "create_school_appointment",
            {"date": "2026-09-01", "time": "10:00"},
            allowed_function_names=set(READ_ONLY_TOOL_NAMES),
        )
    )

    assert result == {
        "success": False,
        "error": "tool_not_allowed",
        "tool_name": "create_school_appointment",
    }


def test_generate_reply_with_tools_uses_hybrid_knowledge_resolution(monkeypatch):
    class _KnowledgeOnlyCompletions:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            message = SimpleNamespace(content="grounded reply", tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    fake_completions = _KnowledgeOnlyCompletions()
    service = _configured_service(_FakeClient(fake_completions))

    def fake_resolve_knowledge_context(_db, *, user_text, session_state=None):
        assert "tuition" in user_text.lower()
        assert (session_state or {}).get("lang_detected") == "en"
        return KnowledgeContext(
            authoritative_facts=[
                KnowledgeSnippet(
                    title="Programs and tuition",
                    content="Track: Software Engineering | Annual fee: 950000 F CFA",
                    source="school_catalog",
                    source_kind="structured",
                    authoritative=True,
                )
            ],
            faq_snippets=[
                KnowledgeSnippet(
                    title="FAQ",
                    content="Scholarship policy summary.",
                    source="knowledge_documents_faq",
                    source_kind="faq",
                )
            ],
            retrieval_snippets=[
                KnowledgeSnippet(
                    title="Handbook",
                    content="Extended curriculum context.",
                    source="knowledge_documents_retrieval",
                    source_kind="retrieval",
                )
            ],
            critical_domains=["catalog"],
        )

    monkeypatch.setattr(llm_module, "resolve_knowledge_context", fake_resolve_knowledge_context)

    reply = asyncio.run(
        service.generate_reply_with_tools(
            "What are the tuition fees for software engineering?",
            session_state={"channel": "chat", "lang_detected": "en"},
            db_session=SimpleNamespace(info={"tenant_id": "00000000-0000-0000-0000-000000000777"}),
        )
    )

    first_call = fake_completions.calls[0]
    system_prompt = first_call["messages"][0]["content"]

    assert reply == "grounded reply"
    assert "Knowledge resolution order" in system_prompt
    assert "STRUCTURED_TRUTH / AUTHORITATIVE_FACTS" in system_prompt
    assert "CURATED_FAQ_SNIPPETS" in system_prompt
    assert "RETRIEVAL_SUPPORT" in system_prompt
    assert service.last_knowledge_sources == {
        "critical_domains": ["catalog"],
        "authoritative_count": 1,
        "faq_count": 1,
        "retrieval_count": 1,
        "authoritative_sources": ["school_catalog"],
        "faq_sources": ["knowledge_documents_faq"],
        "retrieval_sources": ["knowledge_documents_retrieval"],
    }


def test_generate_reply_with_tools_adds_recommendation_and_channel_rules_to_prompt():
    class _PromptCaptureCompletions:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            message = SimpleNamespace(content="guided comparison", tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    fake_completions = _PromptCaptureCompletions()
    service = _configured_service(_FakeClient(fake_completions))

    reply = asyncio.run(
        service.generate_reply_with_tools(
            "De toutes ces filieres, laquelle est mieux ?",
            session_state={
                "channel": "whatsapp",
                "lang_detected": "fr",
                "pending_open_intent": "recommendation_request",
                "conversation_slots": {
                    "last_catalog_options": [
                        {
                            "track_name": "Genie Logiciel",
                            "program_name": "Licence Professionnelle (L3)",
                            "access_level": "L3",
                            "monthly_fee": 110000,
                            "delivery_mode": "onsite",
                        },
                        {
                            "track_name": "Cyber Securite",
                            "program_name": "Master Professionnel",
                            "access_level": "Master",
                            "monthly_fee": 160000,
                            "delivery_mode": "hybrid",
                        },
                    ]
                },
            },
            db_session=SimpleNamespace(info={"tenant_id": "00000000-0000-0000-0000-000000000777"}),
        )
    )

    system_prompt = fake_completions.calls[0]["messages"][0]["content"]

    assert reply == "guided comparison"
    assert "Terminologie metier obligatoire" in system_prompt
    assert "Canal: WhatsApp" in system_prompt
    assert "Il n'existe pas de meilleure option universelle" in system_prompt or "meilleure option universelle" in system_prompt
    assert "Options disponibles pour comparaison" in system_prompt
    assert "programme=Licence Professionnelle (L3)" in system_prompt
    assert "niveau=Master" in system_prompt


def test_generate_reply_with_tools_llm_unavailable_uses_bounded_recommendation_fallback():
    service = LLMService()
    service.api_key = None
    service._client = None

    reply = asyncio.run(
        service.generate_reply_with_tools(
            "Laquelle est mieux ?",
            session_state={
                "channel": "chat",
                "lang_detected": "fr",
                "pending_open_intent": "recommendation_request",
                "conversation_slots": {
                    "last_catalog_options": [
                        {
                            "track_name": "Genie Logiciel",
                            "program_name": "Licence Professionnelle (L3)",
                            "access_level": "L3",
                            "monthly_fee": 110000,
                        },
                        {
                            "track_name": "Cyber Securite",
                            "program_name": "Master Professionnel",
                            "access_level": "Master",
                            "monthly_fee": 160000,
                        },
                    ]
                },
            },
            db_session=None,
        )
    )

    assert service.last_fallback_reason == "llm_not_configured"
    assert "meilleure option universelle" in reply.lower()
    assert "objectif" in reply.lower()
    assert "budget" in reply.lower()
    assert "Genie Logiciel" in reply
    assert "Cyber Securite" in reply
