from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from uuid import UUID, uuid4

from sqlalchemy import inspect, text

from app.db import Base, engine, open_db_session
from app.models import (
    BillingPlan,
    Calendar,
    Conversation,
    Event,
    Message,
    Person,
    RendezVous,
    SchoolAdmissionPolicy,
    SchoolAdmissionRequirement,
    SchoolDepartment,
    SchoolProgram,
    SchoolTrack,
    Tenant,
)
from app.services import channel_agent_pipeline as pipeline_module
from app.services.channel_agent_pipeline import ChannelAgentPipeline
from app.services import llm_tools as llm_tools_module


TENANT_ID = "00000000-0000-0000-0000-00000000p301".replace("p", "0")
PERSON_ID = "00000000-0000-0000-0000-00000000p302".replace("p", "0")


class FakeLLMService:
    def __init__(self):
        self.last_error = None
        self.last_fallback_reason = None
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.model = "fake-model"

    async def extract_structured_message(self, body, session_state=None):  # pragma: no cover - explicit no-op
        return None

    async def rephrase_controlled_reply(self, **kwargs):  # pragma: no cover - explicit no-op
        return None

    async def generate_reply_with_tools(self, body, session_state, db_session):
        return f"LLM:{body}"


def _ensure_tables() -> None:
    Base.metadata.create_all(
        bind=engine,
        tables=[
            BillingPlan.__table__, Tenant.__table__,
            Person.__table__,
            SchoolAdmissionRequirement.__table__,
            SchoolAdmissionPolicy.__table__,
            SchoolDepartment.__table__,
            SchoolProgram.__table__,
            SchoolTrack.__table__,
            RendezVous.__table__,
            Calendar.__table__,
            Event.__table__,
            Conversation.__table__,
            Message.__table__,
        ],
        checkfirst=True,
    )
    db = open_db_session(allow_unscoped=True)
    try:
        inspector = inspect(db.bind)
        columns = {c["name"] for c in inspector.get_columns("conversations")} if "conversations" in inspector.get_table_names() else set()
        if "conversation_state" not in columns:
            db.execute(text("ALTER TABLE conversations ADD COLUMN conversation_state TEXT"))
            db.commit()
    finally:
        db.close()


def _seed_tenant_and_person() -> None:
    _ensure_tables()
    db = open_db_session(allow_unscoped=True)
    try:
        tenant_uuid = UUID(TENANT_ID)
        person_uuid = UUID(PERSON_ID)
        if not db.get(Tenant, tenant_uuid):
            db.add(Tenant(id=tenant_uuid, slug="tenant-p3-pipeline", name="Tenant P3 Pipeline", is_active=True))
            db.flush()
        db.query(Message).filter(Message.tenant_id == tenant_uuid).delete()
        db.query(RendezVous).filter(RendezVous.tenant_id == tenant_uuid).delete()
        db.query(SchoolTrack).filter(SchoolTrack.tenant_id == tenant_uuid).delete()
        db.query(SchoolProgram).filter(SchoolProgram.tenant_id == tenant_uuid).delete()
        db.query(SchoolDepartment).filter(SchoolDepartment.tenant_id == tenant_uuid).delete()
        db.query(SchoolAdmissionRequirement).filter(SchoolAdmissionRequirement.tenant_id == tenant_uuid).delete()
        db.query(SchoolAdmissionPolicy).filter(SchoolAdmissionPolicy.tenant_id == tenant_uuid).delete()
        db.query(Conversation).filter(Conversation.tenant_id == tenant_uuid).delete()
        existing_person = db.get(Person, person_uuid)
        if existing_person is None:
            db.add(
                Person(
                    id=person_uuid,
                    tenant_id=tenant_uuid,
                    first_name="Abdoulaye",
                    last_name="Diallo",
                    email="abdoulaye.pipeline@example.com",
                    phone="+221770000001",
                    preferred_language="fr",
                )
            )
        db.commit()
    finally:
        db.close()


def _seed_school_track(*, tenant_id: str, track_name: str = "Data Science & Intelligence Artificielle"):
    db = open_db_session(tenant_id=tenant_id)
    try:
        dept = SchoolDepartment(name="Informatique")
        db.add(dept)
        db.flush()
        program = SchoolProgram(
            department_id=dept.id,
            name="Data Science & Intelligence Artificielle",
            delivery_mode="onsite",
            access_level="Bac +3",
            is_active=True,
        )
        db.add(program)
        db.flush()
        track = SchoolTrack(
            program_id=program.id,
            name=track_name,
            annual_fee=1600000,
            registration_fee=250000,
            monthly_fee=150000,
            certifications="HUAWEI, AWS, CISCO",
            is_active=True,
        )
        db.add(track)
        db.commit()
        db.refresh(track)
        db.refresh(program)
        return str(track.id), track.name, program.name
    finally:
        db.close()


def _pipeline(db):
    return ChannelAgentPipeline(
        db,
        llm_factory=FakeLLMService,
        track_search_fn=lambda *_args, **_kwargs: {"success": False, "error": "track_not_found"},
        person_upsert_fn=lambda *_args, **_kwargs: {"success": True, "person_id": PERSON_ID},
    )


def test_pipeline_reuses_recent_conversation_with_same_person_and_thread_key():
    _seed_tenant_and_person()

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        pipeline = _pipeline(db)
        first = asyncio.run(
            pipeline.process_inbound_text(
                channel="whatsapp",
                user_text="Bonjour",
                person_id=PERSON_ID,
                from_value="+221770000001",
                thread_key="whatsapp:+221770000001",
                reuse_recent_by_person=True,
            )
        )
        second = asyncio.run(
            pipeline.process_inbound_text(
                channel="whatsapp",
                user_text="Pouvez-vous m'aider ?",
                person_id=PERSON_ID,
                from_value="+221770000001",
                thread_key="whatsapp:+221770000001",
                reuse_recent_by_person=True,
            )
        )

        assert first.conversation_id == second.conversation_id
        conversation = db.get(Conversation, UUID(first.conversation_id))
        state = json.loads(conversation.conversation_state or "{}")
        slots = state.get("slots_json") or {}
        assert slots.get("thread_key") == "whatsapp:+221770000001"
        assert state.get("response_strategy")
        messages = db.query(Message).filter(Message.conversation_id == UUID(first.conversation_id)).all()
        assert len(messages) == 4
    finally:
        db.close()


def test_pipeline_thread_key_mismatch_creates_new_conversation_for_same_person_channel():
    _seed_tenant_and_person()

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        pipeline = _pipeline(db)
        first = asyncio.run(
            pipeline.process_inbound_text(
                channel="email",
                user_text="Bonjour",
                person_id=PERSON_ID,
                from_value="abdoulaye.pipeline@example.com",
                thread_key="email:abdoulaye.pipeline@example.com:admission data science",
                reuse_recent_by_person=True,
            )
        )
        second = asyncio.run(
            pipeline.process_inbound_text(
                channel="email",
                user_text="Nouveau sujet",
                person_id=PERSON_ID,
                from_value="abdoulaye.pipeline@example.com",
                thread_key="email:abdoulaye.pipeline@example.com:admission genie logiciel",
                reuse_recent_by_person=True,
            )
        )
        third = asyncio.run(
            pipeline.process_inbound_text(
                channel="email",
                user_text="Relance sur le meme sujet",
                person_id=PERSON_ID,
                from_value="abdoulaye.pipeline@example.com",
                thread_key="email:abdoulaye.pipeline@example.com:admission genie logiciel",
                reuse_recent_by_person=True,
            )
        )

        assert first.conversation_id != second.conversation_id
        assert second.conversation_id == third.conversation_id
        conversations = (
            db.query(Conversation)
            .filter(Conversation.tenant_id == UUID(TENANT_ID), Conversation.canal == "email")
            .all()
        )
        assert len(conversations) == 2
    finally:
        db.close()


def test_pipeline_reuses_conversation_by_call_sid_for_voice_turns():
    _seed_tenant_and_person()

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        pipeline = _pipeline(db)
        first = asyncio.run(
            pipeline.process_inbound_text(
                channel="call",
                user_text="Bonjour",
                call_sid="CA123456789",
                from_value="CA123456789",
                recording_consent=True,
                reuse_recent_by_person=False,
                conversation_resume_prefix="Call stream test",
            )
        )
        second = asyncio.run(
            pipeline.process_inbound_text(
                channel="call",
                user_text="Je veux des informations",
                call_sid="CA123456789",
                from_value="CA123456789",
                recording_consent=True,
                reuse_recent_by_person=False,
                conversation_resume_prefix="Call stream test",
            )
        )

        assert first.conversation_id == second.conversation_id
        conversation = db.get(Conversation, UUID(first.conversation_id))
        assert conversation is not None
        assert conversation.canal == "call"
        assert conversation.call_sid == "CA123456789"
        assert conversation.recording_consent is True
    finally:
        db.close()


def test_pipeline_persists_summary_memory_and_recent_turn_context():
    _seed_tenant_and_person()

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        pipeline = _pipeline(db)
        first = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="Bonjour, je cherche des informations",
                contact_email="abdoulaye.pipeline@example.com",
                contact_phone="+221 77 000 00 01",
                contact_name="Abdoulaye Diallo",
                reuse_recent_by_person=True,
            )
        )
        second = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="Pouvez-vous me rappeler les frais ?",
                contact_email="abdoulaye.pipeline@example.com",
                contact_phone="+221770000001",
                contact_name="Abdoulaye Diallo",
                reuse_recent_by_person=True,
            )
        )

        assert first.conversation_id == second.conversation_id
        conversation = db.get(Conversation, UUID(first.conversation_id))
        assert conversation is not None
        state = json.loads(conversation.conversation_state or "{}")
        slots = state.get("slots_json") or {}
        assert state.get("participant_key") == f"person:{PERSON_ID}"
        assert state.get("channel_thread_key") == f"chat:person:{PERSON_ID}"
        assert isinstance(state.get("summary_memory"), str)
        assert "Abdoulaye Diallo" in state.get("summary_memory")
        assert slots.get("full_name") == "Abdoulaye Diallo"
        assert slots.get("first_name") == "Abdoulaye"
        assert slots.get("last_name") == "Diallo"
        assert slots.get("email") == "abdoulaye.pipeline@example.com"
        assert slots.get("phone") == "+221770000001"
        assert slots.get("preferred_language") == "fr"
        assert second.llm_state.get("session_summary") == state.get("summary_memory")
        assert second.llm_state.get("participant_key") == f"person:{PERSON_ID}"
        assert second.llm_state.get("channel_thread_key") == f"chat:person:{PERSON_ID}"
        recent_turns = second.llm_state.get("recent_turns") or []
        assert len(recent_turns) >= 2
        assert recent_turns[0]["role"] == "user"
        assert "Bonjour" in recent_turns[0]["content"]
    finally:
        db.close()


def test_pipeline_suppresses_manual_non_voice_conversations_before_orchestrator(monkeypatch):
    _seed_tenant_and_person()

    class FailingOrchestrator:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("orchestrator should not run for locked non-voice conversations")

    def failing_llm_factory():
        raise AssertionError("llm factory should not run for locked non-voice conversations")

    monkeypatch.setattr(pipeline_module, "ConversationOrchestrator", FailingOrchestrator)

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        conv = Conversation(
            tenant_id=UUID(TENANT_ID),
            person_id=UUID(PERSON_ID),
            canal="chat",
            resume="Locked chat",
            conversation_state=json.dumps({"language_locked": "en", "active_flow": "booking_collect_contact"}),
            status="active",
            mode="manual",
            assigned_to=42,
        )
        db.add(conv)
        db.commit()
        db.refresh(conv)

        pipeline = ChannelAgentPipeline(
            db,
            llm_factory=failing_llm_factory,
            track_search_fn=lambda *_args, **_kwargs: {"success": False, "error": "track_not_found"},
            person_upsert_fn=lambda *_args, **_kwargs: {"success": True, "person_id": PERSON_ID},
        )
        result = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="Can you update me?",
                conversation_id=str(conv.id),
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )

        db.expire_all()
        locked = db.get(Conversation, conv.id)
        messages = db.query(Message).filter(Message.conversation_id == conv.id).order_by(Message.created_at.asc()).all()
    finally:
        db.close()

    assert result.response_strategy == "deterministic_manual_lock"
    assert result.lang == "en"
    assert "human advisor" in (result.reply or "").lower()
    assert locked is not None
    assert locked.mode == "manual"
    assert locked.status == "active"
    assert locked.assigned_to == 42
    assert len(messages) == 3
    assert [msg.role for msg in messages] == ["user", "assistant", "system"]
    assert "verrouillage manuel" in (messages[-1].content or "").lower()


def test_pipeline_keeps_pending_review_conversation_in_staff_queue(monkeypatch):
    _seed_tenant_and_person()

    class FailingOrchestrator:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("orchestrator should not run for pending_review conversations")

    def failing_llm_factory():
        raise AssertionError("llm factory should not run for pending_review conversations")

    monkeypatch.setattr(pipeline_module, "ConversationOrchestrator", FailingOrchestrator)

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        conv = Conversation(
            tenant_id=UUID(TENANT_ID),
            person_id=UUID(PERSON_ID),
            canal="email",
            resume="Pending review email",
            conversation_state=json.dumps({"language_locked": "fr"}),
            status="pending_review",
            mode="auto",
            requires_validation=True,
        )
        db.add(conv)
        db.commit()
        db.refresh(conv)

        pipeline = ChannelAgentPipeline(
            db,
            llm_factory=failing_llm_factory,
            track_search_fn=lambda *_args, **_kwargs: {"success": False, "error": "track_not_found"},
            person_upsert_fn=lambda *_args, **_kwargs: {"success": True, "person_id": PERSON_ID},
        )
        result = asyncio.run(
            pipeline.process_inbound_text(
                channel="email",
                user_text="Je relance ma demande d'admission",
                conversation_id=str(conv.id),
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
                thread_key="email:abdoulaye.pipeline@example.com:admission",
            )
        )

        db.expire_all()
        locked = db.get(Conversation, conv.id)
        state = json.loads(locked.conversation_state or "{}")
        messages = db.query(Message).filter(Message.conversation_id == conv.id).order_by(Message.created_at.asc()).all()
    finally:
        db.close()

    assert result.response_strategy == "deterministic_manual_lock"
    assert "équipe admissions" in (result.reply or "").lower()
    assert locked is not None
    assert locked.status == "pending_review"
    assert locked.requires_validation is True
    assert state.get("response_strategy") == "deterministic_manual_lock"
    assert state.get("handoff_trigger_reason") == "pending_review_lock"
    assert (state.get("slots_json") or {}).get("thread_key") == "email:abdoulaye.pipeline@example.com:admission"
    assert any(msg.role == "system" and "pending_review" in (msg.content or "") for msg in messages)


def test_pipeline_unlocked_chat_flow_still_uses_existing_llm_path():
    _seed_tenant_and_person()

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        pipeline = _pipeline(db)
        result = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="Bonjour",
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
    finally:
        db.close()

    assert result.response_strategy == "llm"
    assert result.reply == "LLM:Bonjour"


def test_pipeline_does_not_apply_non_voice_lock_guard_to_call_channel():
    _seed_tenant_and_person()

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        conv = Conversation(
            tenant_id=UUID(TENANT_ID),
            person_id=UUID(PERSON_ID),
            canal="call",
            resume="Locked call",
            conversation_state=json.dumps({"language_locked": "fr"}),
            status="active",
            mode="manual",
            call_sid="CA-MANUAL-LOCK-001",
            recording_consent=True,
        )
        db.add(conv)
        db.commit()
        db.refresh(conv)

        pipeline = _pipeline(db)
        result = asyncio.run(
            pipeline.process_inbound_text(
                channel="call",
                user_text="Bonjour",
                conversation_id=str(conv.id),
                call_sid="CA-MANUAL-LOCK-001",
                reuse_recent_by_person=False,
                recording_consent=True,
            )
        )
    finally:
        db.close()

    assert result.response_strategy == "llm"
    assert result.reply == "LLM:Bonjour"


def test_voice_progressive_fallback_then_kb_answer_without_handoff():
    _seed_tenant_and_person()

    def fake_track_search(_db, arguments):
        query = str((arguments or {}).get("query") or "").lower()
        if "genie logiciel" in query:
            return {
                "success": True,
                "items": [
                    {
                        "track_id": "t1",
                        "track_name": "Genie Logiciel",
                        "program_name": "Licence Pro",
                        "annual_fee": 1150000,
                        "registration_fee": 250000,
                        "monthly_fee": 100000,
                        "access_level": "Bac +2",
                        "delivery_mode": "onsite",
                        "certifications": "CCNA",
                    }
                ],
            }
        return {"success": False, "error": "track_not_found"}

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        pipeline = ChannelAgentPipeline(
            db,
            llm_factory=FakeLLMService,
            track_search_fn=fake_track_search,
            person_upsert_fn=lambda *_args, **_kwargs: {"success": True, "person_id": PERSON_ID},
        )

        t1 = asyncio.run(
            pipeline.process_inbound_text(
                channel="call",
                user_text="Je suis Salam Benali",
                call_sid="CA-PROGRESSIVE-001",
                reuse_recent_by_person=False,
                recording_consent=True,
            )
        )
        t2 = asyncio.run(
            pipeline.process_inbound_text(
                channel="call",
                user_text="%%% ???",
                conversation_id=t1.conversation_id,
                call_sid="CA-PROGRESSIVE-001",
                reuse_recent_by_person=False,
                recording_consent=True,
            )
        )
        t3 = asyncio.run(
            pipeline.process_inbound_text(
                channel="call",
                user_text="frais genie logiciel",
                conversation_id=t1.conversation_id,
                call_sid="CA-PROGRESSIVE-001",
                reuse_recent_by_person=False,
                recording_consent=True,
            )
        )

        assert t2.response_strategy == "fallback_clarify"
        assert "pas bien compris" in (t2.reply or "").lower()
        assert t2.conversation_state.get("failure_count") == 1
        assert t2.conversation_state.get("handoff_allowed") is False

        assert t3.response_strategy == "deterministic_track_details"
        assert "frais annuels" in (t3.reply or "").lower()
        assert t3.conversation_state.get("failure_count") == 0
        assert t3.conversation_state.get("clarification_success") is True
        assert t3.conversation_state.get("handoff_allowed") is False

        conv = db.get(Conversation, UUID(t1.conversation_id))
        assert conv is not None
        state = json.loads(conv.conversation_state or "{}")
        assert state.get("failure_count") == 0
        assert state.get("handoff_allowed") is False
    finally:
        db.close()


def test_pipeline_logs_turn_observability_fields_on_deterministic_flow(monkeypatch):
    _seed_tenant_and_person()
    captured: list[dict] = []

    class DeterministicLLMStub:
        async def generate_reply_with_tools(self, body, session_state, db_session):  # pragma: no cover - should not run
            raise AssertionError("LLM generation should not run on deterministic track details path")

    def fake_track_search(_db, arguments):
        query = str((arguments or {}).get("query") or "").lower()
        if "genie logiciel" in query:
            return {
                "success": True,
                "items": [
                    {
                        "track_id": "t1",
                        "track_name": "Genie Logiciel",
                        "program_name": "Licence Pro",
                        "annual_fee": 1150000,
                        "registration_fee": 250000,
                        "monthly_fee": 100000,
                        "access_level": "Bac +2",
                        "delivery_mode": "onsite",
                        "certifications": "CCNA",
                    }
                ],
            }
        return {"success": False, "error": "track_not_found"}

    def fake_info(message, *args, **kwargs):
        if message == "agent_turn_processed":
            captured.append(dict((kwargs or {}).get("extra", {}).get("extra_fields", {})))

    monkeypatch.setattr(pipeline_module.logger, "info", fake_info)

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        pipeline = ChannelAgentPipeline(
            db,
            llm_factory=DeterministicLLMStub,
            track_search_fn=fake_track_search,
            person_upsert_fn=lambda *_args, **_kwargs: {"success": True, "person_id": PERSON_ID},
        )
        asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="Genie logiciel",
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
    finally:
        db.close()

    assert captured, "expected agent_turn_processed log"
    event = captured[-1]
    for key in (
        "response_strategy",
        "flow_state",
        "state_enter",
        "state_exit",
        "slots_filled",
        "slots_missing",
        "llm_called",
        "tool_calls",
        "fallback_reason",
        "failure_count",
        "fallback_stage",
        "clarification_success",
        "short_text_flag",
        "session_ttl_expired",
        "reset_reason",
        "language_locked",
        "channel",
        "handoff_trigger",
        "handoff_trigger_reason",
    ):
        assert key in event
    assert event["channel"] == "chat"
    assert event["response_strategy"].startswith("deterministic")
    assert event["flow_state"] == "track_selected"
    assert event["state_enter"] == "browsing_catalog"
    assert event["state_exit"] == "track_selected"
    assert "track_name" in event["slots_filled"]
    assert event["llm_called"] is False
    assert event["tool_calls"] == 0
    assert event["short_text_flag"] is False
    assert event["session_ttl_expired"] is False


def test_pipeline_logs_llm_fallback_reason_and_tool_calls(monkeypatch):
    _seed_tenant_and_person()
    captured_info: list[dict] = []
    captured_warnings: list[tuple[str, dict]] = []

    class FallbackLLMStub:
        def __init__(self):
            self.last_error = None
            self.last_fallback_reason = None
            self.last_tool_calls = []

        async def generate_reply_with_tools(self, body, session_state, db_session):
            self.last_tool_calls = ["get_track_tuition"]
            self.last_error = "provider boom"
            self.last_fallback_reason = "llm_provider_error"
            return "fallback provider reply"

    def fake_info(message, *args, **kwargs):
        if message == "agent_turn_processed":
            captured_info.append(dict((kwargs or {}).get("extra", {}).get("extra_fields", {})))

    def fake_warning(message, *args, **kwargs):
        captured_warnings.append((str(message), dict((kwargs or {}).get("extra", {}).get("extra_fields", {}))))

    monkeypatch.setattr(pipeline_module.logger, "info", fake_info)
    monkeypatch.setattr(pipeline_module.logger, "warning", fake_warning)

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        pipeline = ChannelAgentPipeline(
            db,
            llm_factory=FallbackLLMStub,
            track_search_fn=lambda *_args, **_kwargs: {"success": False, "error": "track_not_found"},
            person_upsert_fn=lambda *_args, **_kwargs: {"success": True, "person_id": PERSON_ID},
        )
        asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="Bonjour",
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
    finally:
        db.close()

    assert any(msg == "channel_llm_provider_error_fallback_used" for msg, _ in captured_warnings)
    assert captured_info, "expected agent_turn_processed log"
    event = captured_info[-1]
    assert event["response_strategy"] == "fallback_contextual"
    assert event["response_strategy_category"] == "fallback"
    assert event["llm_called"] is True
    assert event["llm_generate_called"] is True
    assert event["tool_calls"] == 1
    assert event["tool_call_names"] == ["get_track_tuition"]
    assert event["fallback_used"] is True
    assert event["fallback_reason"] == "llm_provider_error"


def test_pipeline_short_text_inputs_keep_language_and_avoid_handoff(monkeypatch):
    _seed_tenant_and_person()
    captured: list[dict] = []

    def fake_info(message, *args, **kwargs):
        if message == "agent_turn_processed":
            captured.append(dict((kwargs or {}).get("extra", {}).get("extra_fields", {})))

    monkeypatch.setattr(pipeline_module.logger, "info", fake_info)

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        pipeline = ChannelAgentPipeline(
            db,
            llm_factory=FakeLLMService,
            track_search_fn=lambda *_args, **_kwargs: {"success": False, "error": "track_not_found"},
            person_upsert_fn=lambda *_args, **_kwargs: {"success": True, "person_id": PERSON_ID},
        )
        conversation_id = None
        for text in ("ok", "oui", "d'accord", "frais"):
            turn = asyncio.run(
                pipeline.process_inbound_text(
                    channel="chat",
                    user_text=text,
                    person_id=PERSON_ID,
                    conversation_id=conversation_id,
                    reuse_recent_by_person=False,
                )
            )
            conversation_id = turn.conversation_id
            assert turn.lang == "fr"
            assert turn.response_strategy != "fallback_handoff"
            assert turn.conversation_state.get("handoff_allowed") is False
            assert turn.conversation_state.get("short_text_flag") is True
    finally:
        db.close()

    assert len(captured) >= 4
    for event in captured[-4:]:
        assert event.get("short_text_flag") is True
        assert event.get("lang_detected") == "fr"
        assert event.get("handoff_trigger") is not True


def test_pipeline_chat_session_ttl_expiration_applies_controlled_reset(monkeypatch):
    _seed_tenant_and_person()
    captured: list[dict] = []

    class DeterministicLLMStub:
        async def generate_reply_with_tools(self, body, session_state, db_session):  # pragma: no cover - should not run
            raise AssertionError("LLM should not run for deterministic catalog turn")

    def fake_track_search(_db, arguments):
        query = str((arguments or {}).get("query") or "").lower()
        if "programme" in query or "programmes" in query or "program" in query:
            return {
                "success": True,
                "items": [
                    {
                        "track_id": "t1",
                        "track_name": "Genie Logiciel",
                        "program_name": "Licence Pro",
                        "annual_fee": 1150000,
                        "registration_fee": 250000,
                        "monthly_fee": 100000,
                        "access_level": "Bac +2",
                        "delivery_mode": "onsite",
                        "certifications": "CCNA",
                    }
                ],
            }
        if "genie logiciel" in query:
            return {
                "success": True,
                "items": [
                    {
                        "track_id": "t1",
                        "track_name": "Genie Logiciel",
                        "program_name": "Licence Pro",
                        "annual_fee": 1150000,
                        "registration_fee": 250000,
                        "monthly_fee": 100000,
                        "access_level": "Bac +2",
                        "delivery_mode": "onsite",
                        "certifications": "CCNA",
                    }
                ],
            }
        return {"success": False, "error": "track_not_found"}

    def fake_info(message, *args, **kwargs):
        if message == "agent_turn_processed":
            captured.append(dict((kwargs or {}).get("extra", {}).get("extra_fields", {})))

    monkeypatch.setattr(pipeline_module.logger, "info", fake_info)
    monkeypatch.setattr(pipeline_module.settings, "chat_session_ttl_sec", 60, raising=False)

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        pipeline = ChannelAgentPipeline(
            db,
            llm_factory=DeterministicLLMStub,
            track_search_fn=fake_track_search,
            person_upsert_fn=lambda *_args, **_kwargs: {"success": True, "person_id": PERSON_ID},
        )
        first = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="Genie Logiciel",
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
                thread_key="chat:ttl-expired",
            )
        )
        conv = db.get(Conversation, UUID(first.conversation_id))
        assert conv is not None
        state_payload = json.loads(conv.conversation_state or "{}")
        state_payload["active_flow"] = "booking_collect_datetime"
        state_payload["slots_json"] = {
            "thread_key": "chat:ttl-expired",
            "track_name": "Genie Logiciel",
            "program_name": "Licence Pro",
            "email": "abdoulaye@example.com",
        }
        conv.conversation_state = json.dumps(state_payload)
        old_ts = datetime.now(timezone.utc) - timedelta(hours=10)
        db.query(Message).filter(Message.conversation_id == conv.id).update(
            {Message.created_at: old_ts},
            synchronize_session=False,
        )
        db.add(conv)
        db.commit()

        second = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="quels sont vos programmes disponibles ?",
                conversation_id=first.conversation_id,
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
                thread_key="chat:ttl-expired",
            )
        )
    finally:
        db.close()

    assert second.response_strategy == "deterministic_catalog"
    assert second.conversation_state.get("session_ttl_expired") is True
    assert second.conversation_state.get("reset_reason") == "session_ttl_expired"
    assert second.conversation_state.get("active_flow") == "browsing_catalog"
    slots = second.conversation_state.get("slots_json") or {}
    assert slots.get("thread_key") == "chat:ttl-expired"
    assert "email" not in slots
    assert captured, "expected agent_turn_processed log"
    event = captured[-1]
    assert event.get("state_enter") == "booking_collect_datetime"
    assert event.get("state_exit") == "browsing_catalog"
    assert event.get("session_ttl_expired") is True
    assert event.get("reset_reason") == "session_ttl_expired"


def test_pipeline_flow_escape_resets_current_flow_without_handoff():
    _seed_tenant_and_person()

    def fake_track_search(_db, arguments):
        query = str((arguments or {}).get("query") or "").lower()
        if "genie logiciel" in query:
            return {
                "success": True,
                "items": [
                    {
                        "track_id": "t1",
                        "track_name": "Genie Logiciel",
                        "program_name": "Licence Pro",
                        "annual_fee": 1150000,
                        "registration_fee": 250000,
                        "monthly_fee": 100000,
                        "access_level": "Bac +2",
                        "delivery_mode": "onsite",
                        "certifications": "CCNA",
                    }
                ],
            }
        return {"success": False, "error": "track_not_found"}

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        pipeline = ChannelAgentPipeline(
            db,
            llm_factory=FakeLLMService,
            track_search_fn=fake_track_search,
            person_upsert_fn=lambda *_args, **_kwargs: {"success": True, "person_id": PERSON_ID},
        )
        t1 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="Genie Logiciel",
                person_id=PERSON_ID,
                thread_key="chat:flow-escape",
                reuse_recent_by_person=False,
            )
        )
        t2 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="oui",
                conversation_id=t1.conversation_id,
                person_id=PERSON_ID,
                thread_key="chat:flow-escape",
                reuse_recent_by_person=False,
            )
        )
        t3 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="annuler",
                conversation_id=t1.conversation_id,
                person_id=PERSON_ID,
                thread_key="chat:flow-escape",
                reuse_recent_by_person=False,
            )
        )
    finally:
        db.close()

    assert t2.response_strategy == "deterministic_booking_collect_contact"
    assert t3.response_strategy == "deterministic_flow_escape"
    assert t3.conversation_state.get("active_flow") == "browsing_catalog"
    assert t3.conversation_state.get("reset_reason") == "flow_escape_cancel"
    assert t3.conversation_state.get("handoff_allowed") is False
    slots = t3.conversation_state.get("slots_json") or {}
    assert slots.get("thread_key") == "chat:flow-escape"
    assert slots.get("person_id") == PERSON_ID
    assert slots.get("preferred_language") == "fr"
    assert "track_name" not in slots


def test_pipeline_persists_appointment_on_booking_confirmation_and_allows_catalog_after_submit(monkeypatch):
    _seed_tenant_and_person()
    track_id, track_name, program_name = _seed_school_track(tenant_id=TENANT_ID)

    async def fake_send_preferred_notification(**kwargs):
        return {"channel": "email", "sent": True}

    def fake_track_search(_db, arguments):
        query = str((arguments or {}).get("query") or "").lower()
        normalized = (
            query.replace("é", "e")
            .replace("è", "e")
            .replace("ê", "e")
            .replace("à", "a")
            .replace("î", "i")
            .replace("ï", "i")
        )
        if "data science" in normalized:
            return {
                "success": True,
                "items": [
                    {
                        "track_id": track_id,
                        "track_name": track_name,
                        "program_name": program_name,
                        "annual_fee": 1600000,
                        "registration_fee": 250000,
                        "monthly_fee": 150000,
                        "access_level": "Bac +3",
                        "delivery_mode": "onsite",
                        "certifications": "HUAWEI, AWS, CISCO",
                    }
                ],
            }
        if "programme" in normalized or "programmes" in normalized or "filiere" in normalized or "filiere" in normalized:
            items = []
            for i in range(12):
                items.append(
                    {
                        "track_id": f"t{i+1}",
                        "track_name": f"Track {i+1}",
                        "program_name": f"Programme {i+1}",
                        "annual_fee": 1000000 + i * 10000,
                        "registration_fee": 250000,
                        "monthly_fee": 90000,
                        "access_level": "Bac",
                        "delivery_mode": "onsite",
                        "certifications": None,
                    }
                )
            return {"success": True, "items": items}
        return {"success": False, "error": "track_not_found"}

    monkeypatch.setattr(llm_tools_module, "send_preferred_notification", fake_send_preferred_notification)

    def fake_slot_check(_db, _args):
        return {"success": True, "available": True, "conflicts": 0, "available_agents_count": 1}

    async def fake_create_appointment(_db, _args):
        return {
            "success": True,
            "appointment_id": str(uuid4()),
            "status": "pending",
            "person_id": PERSON_ID,
            "agent_id": "00000000-0000-0000-0000-000000000999",
            "agent_name": "Agent Admission Test",
            "notifications": {"channel": "email", "sent": True},
        }

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        pipeline = ChannelAgentPipeline(
            db,
            llm_factory=FakeLLMService,
            track_search_fn=fake_track_search,
            person_upsert_fn=lambda *_args, **_kwargs: {"success": True, "person_id": PERSON_ID},
            appointment_slot_check_fn=fake_slot_check,
            appointment_create_fn=fake_create_appointment,
        )
        t1 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="Data Science & Intelligence Artificielle",
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
        t2 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="oui",
                conversation_id=t1.conversation_id,
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
        t3 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="Diallo Abdoulaye, diabdullah112@gmail.com, le 28 fevrier 2099 à 15h",
                conversation_id=t1.conversation_id,
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
        t4 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="oui",
                conversation_id=t1.conversation_id,
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )

        assert t2.response_strategy == "deterministic_booking_collect_contact"
        assert t3.response_strategy == "deterministic_booking_confirm"
        assert t4.response_strategy == "deterministic_booking_submitted_persisted"
        assert t4.active_flow == "browsing_catalog"
        assert "ref:" in t4.reply

        rdvs = db.query(RendezVous).filter(RendezVous.tenant_id == UUID(TENANT_ID)).all()
        assert len(rdvs) == 0  # creation is stubbed in this pipeline test

        conv = db.get(Conversation, UUID(t4.conversation_id))
        state = json.loads(conv.conversation_state or "{}")
        slots = state.get("slots_json") or {}
        assert slots.get("appointment_id")
        assert slots.get("appointment_status") == "pending"
        assert slots.get("assigned_agent_id") == "00000000-0000-0000-0000-000000000999"
        assert slots.get("assigned_agent_name") == "Agent Admission Test"
        assert slots.get("notification_channel") == "email"
        assert slots.get("notification_sent") is True
        assert state.get("active_flow") == "browsing_catalog"
        assert state.get("appointment_locked") is True

        t5 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="puis je recevoir un email pour confirmer ?",
                conversation_id=t1.conversation_id,
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
        assert t5.response_strategy == "deterministic_booking_post_submit_followup"
        assert "email" in t5.reply.lower()
        assert "recapitulatif" not in t5.reply.lower()
        assert t5.conversation_state.get("appointment_locked") is True

        t6 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="quels sont vos programmes disponibles ?",
                conversation_id=t1.conversation_id,
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
        assert t6.response_strategy == "deterministic_catalog"
        assert "Programme 12" in t6.reply
        assert "Track 12" not in t6.reply
        assert "recapitulatif" not in t6.reply.lower()
        assert t6.conversation_state.get("appointment_locked") is True
    finally:
        db.close()


def test_pipeline_post_booking_closing_message_does_not_replay_recap():
    _seed_tenant_and_person()
    track_id, track_name, program_name = _seed_school_track(tenant_id=TENANT_ID)

    def fake_track_search(_db, arguments):
        query = str((arguments or {}).get("query") or "").lower()
        if "data science" in query:
            return {
                "success": True,
                "items": [
                    {
                        "track_id": track_id,
                        "track_name": track_name,
                        "program_name": program_name,
                        "annual_fee": 1600000,
                        "registration_fee": 250000,
                        "monthly_fee": 150000,
                        "access_level": "Bac +3",
                        "delivery_mode": "onsite",
                        "certifications": "HUAWEI, AWS, CISCO",
                    }
                ],
            }
        return {"success": False, "error": "track_not_found"}

    def fake_slot_check(_db, _args):
        return {"success": True, "available": True, "conflicts": 0, "available_agents_count": 1}

    async def fake_create_appointment(_db, _args):
        return {
            "success": True,
            "appointment_id": str(uuid4()),
            "status": "pending",
            "person_id": PERSON_ID,
            "agent_id": "00000000-0000-0000-0000-000000000999",
            "agent_name": "Agent Admission Test",
            "notifications": {"channel": "email", "sent": True},
        }

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        pipeline = ChannelAgentPipeline(
            db,
            llm_factory=FakeLLMService,
            track_search_fn=fake_track_search,
            person_upsert_fn=lambda *_args, **_kwargs: {"success": True, "person_id": PERSON_ID},
            appointment_slot_check_fn=fake_slot_check,
            appointment_create_fn=fake_create_appointment,
        )
        t1 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="Data Science & Intelligence Artificielle",
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
        t2 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="oui",
                conversation_id=t1.conversation_id,
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
        t3 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="Diallo Abdoulaye, diabdullah112@gmail.com, le 28 fevrier 2099 à 15h",
                conversation_id=t1.conversation_id,
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
        t4 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="oui",
                conversation_id=t1.conversation_id,
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
        t5 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="ok sebon alors merci",
                conversation_id=t1.conversation_id,
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
    finally:
        db.close()

    assert t2.response_strategy == "deterministic_booking_collect_contact"
    assert t3.response_strategy == "deterministic_booking_confirm"
    assert t4.response_strategy == "deterministic_booking_submitted_persisted"
    assert t4.active_flow == "browsing_catalog"
    assert t4.conversation_state.get("appointment_locked") is True
    assert t5.response_strategy == "deterministic_gratitude_after_booking"
    assert t5.active_flow == "browsing_catalog"
    assert t5.conversation_state.get("appointment_locked") is True
    assert "recapitulatif" not in (t5.reply or "").lower()
    assert "ref:" not in (t5.reply or "").lower()


def test_pipeline_llm_not_configured_fallback_after_booking_stays_safe(monkeypatch):
    _seed_tenant_and_person()
    captured_info: list[dict] = []
    captured_warnings: list[tuple[str, dict]] = []
    track_id, track_name, program_name = _seed_school_track(tenant_id=TENANT_ID)

    class MissingLLMStub:
        def __init__(self):
            self.last_error = None
            self.last_fallback_reason = None
            self.last_tool_calls = []

        async def generate_reply_with_tools(self, body, session_state, db_session):
            self.last_fallback_reason = "llm_not_configured"
            return "fallback missing llm"

    def fake_track_search(_db, arguments):
        query = str((arguments or {}).get("query") or "").lower()
        if "data science" in query:
            return {
                "success": True,
                "items": [
                    {
                        "track_id": track_id,
                        "track_name": track_name,
                        "program_name": program_name,
                        "annual_fee": 1600000,
                        "registration_fee": 250000,
                        "monthly_fee": 150000,
                        "access_level": "Bac +3",
                        "delivery_mode": "onsite",
                        "certifications": "HUAWEI, AWS, CISCO",
                    }
                ],
            }
        if "programme" in query or "programmes" in query or "filiere" in query or "filieres" in query:
            return {
                "success": True,
                "items": [
                    {
                        "track_id": "lp-l3",
                        "track_name": "Genie Logiciel",
                        "program_name": "Licence Professionnelle (L3)",
                        "annual_fee": 1450000,
                        "registration_fee": 250000,
                        "monthly_fee": 110000,
                        "access_level": "L3",
                        "delivery_mode": "onsite",
                        "certifications": "",
                    },
                    {
                        "track_id": "master-ia",
                        "track_name": "Intelligence Artificielle",
                        "program_name": "Master Professionnel",
                        "annual_fee": 1900000,
                        "registration_fee": 300000,
                        "monthly_fee": 160000,
                        "access_level": "Master",
                        "delivery_mode": "hybrid",
                        "certifications": "",
                    },
                ],
            }
        return {"success": False, "error": "track_not_found"}

    def fake_slot_check(_db, _args):
        return {"success": True, "available": True, "conflicts": 0, "available_agents_count": 1}

    async def fake_create_appointment(_db, _args):
        return {
            "success": True,
            "appointment_id": str(uuid4()),
            "status": "pending",
            "person_id": PERSON_ID,
            "agent_id": "00000000-0000-0000-0000-000000000999",
            "agent_name": "Agent Admission Test",
            "notifications": {"channel": "email", "sent": True},
        }

    def fake_info(message, *args, **kwargs):
        if message == "agent_turn_processed":
            captured_info.append(dict((kwargs or {}).get("extra", {}).get("extra_fields", {})))

    def fake_warning(message, *args, **kwargs):
        captured_warnings.append((str(message), dict((kwargs or {}).get("extra", {}).get("extra_fields", {}))))

    monkeypatch.setattr(pipeline_module.logger, "info", fake_info)
    monkeypatch.setattr(pipeline_module.logger, "warning", fake_warning)

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        pipeline = ChannelAgentPipeline(
            db,
            llm_factory=MissingLLMStub,
            track_search_fn=fake_track_search,
            person_upsert_fn=lambda *_args, **_kwargs: {"success": True, "person_id": PERSON_ID},
            appointment_slot_check_fn=fake_slot_check,
            appointment_create_fn=fake_create_appointment,
        )
        t1 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="Data Science & Intelligence Artificielle",
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
        t2 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="oui",
                conversation_id=t1.conversation_id,
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
        t3 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="Diallo Abdoulaye, diabdullah112@gmail.com, le 28 fevrier 2099 à 15h",
                conversation_id=t1.conversation_id,
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
        t4 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="oui",
                conversation_id=t1.conversation_id,
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
        t5 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="de toutes ces filieres, laquelle est mieux ?",
                conversation_id=t1.conversation_id,
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
    finally:
        db.close()

    assert t2.response_strategy == "deterministic_booking_collect_contact"
    assert t3.response_strategy == "deterministic_booking_confirm"
    assert t4.response_strategy == "deterministic_booking_submitted_persisted"
    assert t4.active_flow == "browsing_catalog"
    assert t4.conversation_state.get("appointment_locked") is True
    assert t5.response_strategy == "fallback_contextual"
    assert t5.active_flow == "browsing_catalog"
    assert t5.conversation_state.get("appointment_locked") is True
    assert "souci technique temporaire" in (t5.reply or "").lower()
    assert "demande de rendez-vous reste enregistree" in (t5.reply or "").lower() or "demande de rendez-vous reste enregistrée" in (t5.reply or "").lower()
    assert "recapitulatif" not in (t5.reply or "").lower()
    assert "ref:" not in (t5.reply or "").lower()
    assert "frais annuels" not in (t5.reply or "").lower()
    assert "data science" not in (t5.reply or "").lower()
    assert any(msg == "channel_llm_provider_error_fallback_used" for msg, _ in captured_warnings)
    assert captured_info, "expected agent_turn_processed log"
    event = captured_info[-1]
    assert event["response_strategy"] == "fallback_contextual"
    assert event["fallback_used"] is True
    assert event["fallback_reason"] == "llm_not_configured"
    assert event["state_exit"] == "browsing_catalog"


def test_pipeline_llm_not_configured_recommendation_fallback_stays_safe(monkeypatch):
    _seed_tenant_and_person()

    class MissingLLMStub:
        def __init__(self):
            self.last_error = None
            self.last_fallback_reason = None
            self.last_tool_calls = []

        async def generate_reply_with_tools(self, body, session_state, db_session):
            self.last_fallback_reason = "llm_not_configured"
            return "fallback missing llm"

    def fake_track_search(_db, arguments):
        query = str((arguments or {}).get("query") or "").lower()
        if "filiere" in query or "filieres" in query:
            return {
                "success": True,
                "items": [
                    {
                        "track_id": "gl-l3",
                        "track_name": "Genie Logiciel",
                        "program_name": "Licence Professionnelle (L3)",
                        "annual_fee": 1450000,
                        "registration_fee": 250000,
                        "monthly_fee": 110000,
                        "access_level": "L3",
                        "delivery_mode": "onsite",
                        "certifications": "",
                    },
                    {
                        "track_id": "cs-master",
                        "track_name": "Cyber Securite",
                        "program_name": "Master Professionnel",
                        "annual_fee": 1900000,
                        "registration_fee": 300000,
                        "monthly_fee": 160000,
                        "access_level": "Master",
                        "delivery_mode": "hybrid",
                        "certifications": "",
                    },
                ],
            }
        return {"success": False, "error": "track_not_found"}

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        pipeline = ChannelAgentPipeline(
            db,
            llm_factory=MissingLLMStub,
            track_search_fn=fake_track_search,
            person_upsert_fn=lambda *_args, **_kwargs: {"success": True, "person_id": PERSON_ID},
        )
        first = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="Quelles sont vos filieres disponibles ?",
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
        second = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="De toutes ces filieres, laquelle est mieux ?",
                conversation_id=first.conversation_id,
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
    finally:
        db.close()

    assert first.response_strategy == "deterministic_catalog"
    assert second.response_strategy == "fallback_contextual"
    assert "souci technique temporaire" in (second.reply or "").lower()
    assert "meilleure option universelle" not in (second.reply or "").lower()
    assert "le meilleur" not in (second.reply or "").lower()


def test_pipeline_casual_closure_does_not_fill_booking_name_slot():
    _seed_tenant_and_person()

    def fake_track_search(_db, arguments):
        query = str((arguments or {}).get("query") or "").lower()
        if "genie logiciel" in query:
            return {
                "success": True,
                "items": [
                    {
                        "track_id": "t1",
                        "track_name": "Genie Logiciel",
                        "program_name": "Licence Professionnelle",
                        "annual_fee": 1330000,
                        "registration_fee": 250000,
                        "monthly_fee": 100000,
                        "access_level": "Bac +2",
                        "delivery_mode": "onsite",
                        "certifications": "",
                    }
                ],
            }
        return {"success": False, "error": "track_not_found"}

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        pipeline = ChannelAgentPipeline(
            db,
            llm_factory=FakeLLMService,
            track_search_fn=fake_track_search,
            person_upsert_fn=lambda *_args, **_kwargs: {"success": True, "person_id": PERSON_ID},
        )
        t1 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="Genie Logiciel",
                person_id=PERSON_ID,
                thread_key="chat:casual-closure-name",
                reuse_recent_by_person=False,
            )
        )
        t2 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="oui",
                conversation_id=t1.conversation_id,
                person_id=PERSON_ID,
                thread_key="chat:casual-closure-name",
                reuse_recent_by_person=False,
            )
        )
        t3 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="ok sebon alors merci",
                conversation_id=t1.conversation_id,
                person_id=PERSON_ID,
                thread_key="chat:casual-closure-name",
                reuse_recent_by_person=False,
            )
        )
    finally:
        db.close()

    assert t2.response_strategy == "deterministic_booking_collect_contact"
    assert t3.response_strategy == "deterministic_booking_collect_contact"
    slots = t3.conversation_state.get("slots_json") or {}
    assert slots.get("full_name") in (None, "")
    assert "recapitulatif" not in (t3.reply or "").lower()


def test_pipeline_returns_datetime_prompt_when_no_agent_available_for_slot():
    _seed_tenant_and_person()

    def fake_track_search(_db, arguments):
        query = str((arguments or {}).get("query") or "").lower()
        if "genie logiciel" in query:
            return {
                "success": True,
                "items": [
                    {
                        "track_id": "t1",
                        "track_name": "Genie Logiciel",
                        "program_name": "Licence Professionnelle",
                        "annual_fee": 1330000,
                        "registration_fee": 250000,
                        "monthly_fee": 100000,
                        "access_level": "Bac +2",
                        "delivery_mode": "onsite",
                        "certifications": "",
                    }
                ],
            }
        return {"success": False, "error": "track_not_found"}

    def slot_check_no_agent(_db, _args):
        return {"success": True, "available": False, "conflicts": 0, "reason": "no_agent_available"}

    async def create_should_not_run(_db, _args):  # pragma: no cover - protected by slot precheck
        raise AssertionError("create_school_appointment should not run when no agent is available")

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        pipeline = ChannelAgentPipeline(
            db,
            llm_factory=FakeLLMService,
            track_search_fn=fake_track_search,
            person_upsert_fn=lambda *_args, **_kwargs: {"success": True, "person_id": PERSON_ID},
            appointment_slot_check_fn=slot_check_no_agent,
            appointment_create_fn=create_should_not_run,
        )
        t1 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="Genie Logiciel",
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
        t2 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="oui",
                conversation_id=t1.conversation_id,
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
        t3 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="Diallo Abdoulaye, diabdullah112@gmail.com, 28/02/2099 a 15h",
                conversation_id=t1.conversation_id,
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
        t4 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="oui",
                conversation_id=t1.conversation_id,
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )

        assert t2.response_strategy == "deterministic_booking_collect_contact"
        assert t3.response_strategy == "deterministic_booking_confirm"
        assert t4.response_strategy == "deterministic_booking_no_agent_available"
        assert "agent" in t4.reply.lower()
        assert t4.active_flow == "booking_collect_datetime"

        t5 = asyncio.run(
            pipeline.process_inbound_text(
                channel="chat",
                user_text="ok sans problemes, mais dis moi de tout les programmes que tu m'a propose lequel est mieux ?",
                conversation_id=t1.conversation_id,
                person_id=PERSON_ID,
                reuse_recent_by_person=False,
            )
        )
        assert t5.response_strategy == "llm"
        assert t5.active_flow == "browsing_catalog"
        assert "aucun agent admission" not in t5.reply.lower()
        assert "recapitulatif" not in t5.reply.lower()
    finally:
        db.close()
