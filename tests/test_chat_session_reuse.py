from __future__ import annotations

import hashlib
import json
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.config import settings
from app.db import Base, engine, open_db_session
from app.models import Conversation, Message, Person, Tenant, BillingPlan, TenantChannel
from app.routers import chat as chat_router
from app.services.tenant_context import tenant_context_middleware


TENANT_ID = "00000000-0000-0000-0000-0000000000fd"
PERSON_ID = "00000000-0000-0000-0000-0000000000fe"
PROVIDER_KEY = "chat-session-reuse-key"
TENANT_TOKEN = "chat-session-reuse-token"


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _seed_chat_channel() -> None:
    Base.metadata.create_all(
        bind=engine,
        tables=[
            BillingPlan.__table__, Tenant.__table__,
            TenantChannel.__table__,
            Person.__table__,
            Conversation.__table__,
            Message.__table__,
        ],
        checkfirst=True,
    )
    db = open_db_session(allow_unscoped=True)
    try:
        inspector = inspect(db.bind)
        conversation_columns = {c["name"] for c in inspector.get_columns("conversations")} if "conversations" in inspector.get_table_names() else set()
        if "conversation_state" not in conversation_columns:
            db.execute(text("ALTER TABLE conversations ADD COLUMN conversation_state TEXT"))
            db.commit()
        tenant_uuid = UUID(TENANT_ID)
        if not db.get(Tenant, tenant_uuid):
            db.add(Tenant(id=tenant_uuid, slug="tenant-chat-reuse", name="Tenant Chat Reuse", is_active=True))
            db.flush()
        db.query(TenantChannel).filter(
            TenantChannel.provider == "chat_widget",
            TenantChannel.provider_key == PROVIDER_KEY,
        ).delete()
        db.query(Message).filter(Message.tenant_id == tenant_uuid).delete()
        db.query(Conversation).filter(Conversation.tenant_id == tenant_uuid).delete()
        db.query(Person).filter(Person.tenant_id == tenant_uuid).delete()
        db.add(
            TenantChannel(
                tenant_id=tenant_uuid,
                provider="chat_widget",
                provider_key=PROVIDER_KEY,
                token_hash=_hash_token(TENANT_TOKEN),
                is_active=True,
            )
        )
        db.add(
            Person(
                id=UUID(PERSON_ID),
                tenant_id=tenant_uuid,
                first_name="Abdoulaye",
                last_name="Diallo",
                email="abdoulaye.chat@example.com",
                phone="+221770000099",
                preferred_language="fr",
            )
        )
        db.commit()
    finally:
        db.close()


def test_chat_reuses_same_conversation_when_session_id_is_provided(monkeypatch):
    _seed_chat_channel()

    class FakeLLMService:
        def __init__(self):
            self.last_error = None
            self.last_fallback_reason = None
            self.last_prompt_tokens = 0
            self.last_completion_tokens = 0
            self.model = "fake-model"

        async def extract_structured_message(self, body, session_state=None):
            return None

        async def rephrase_controlled_reply(self, **kwargs):
            return None

        async def generate_reply_with_tools(self, body, session_state, db_session):
            return f"Echo: {body}"

    monkeypatch.setattr(settings, "widget_public_token", None, raising=False)
    monkeypatch.setattr(chat_router, "LLMService", FakeLLMService)

    app = FastAPI()
    app.middleware("http")(tenant_context_middleware)
    app.include_router(chat_router.router)

    params = {"provider_key": PROVIDER_KEY, "tenant_token": TENANT_TOKEN}
    with TestClient(app) as client:
        first = client.post("/chat/chat", params=params, json={"message": "Bonjour"})
        assert first.status_code == 200
        first_session = first.json().get("session_id")
        assert isinstance(first_session, str) and first_session

        second = client.post(
            "/chat/chat",
            params=params,
            json={"message": "Hello again", "session_id": first_session},
        )
        assert second.status_code == 200
        assert second.json().get("session_id") == first_session

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        conversations = db.query(Conversation).filter(Conversation.tenant_id == UUID(TENANT_ID)).all()
        assert len(conversations) == 1
        state = json.loads(conversations[0].conversation_state or "{}")
        assert "active_flow" in state
        assert "language_locked" in state
        assert "slots_json" in state
        assert "response_strategy" in state
        messages = db.query(Message).filter(Message.conversation_id == conversations[0].id).all()
        assert len(messages) == 4
    finally:
        db.close()


def test_chat_reuses_known_contact_without_explicit_session_id(monkeypatch):
    _seed_chat_channel()

    class FakeLLMService:
        def __init__(self):
            self.last_error = None
            self.last_fallback_reason = None
            self.last_prompt_tokens = 0
            self.last_completion_tokens = 0
            self.model = "fake-model"

        async def extract_structured_message(self, body, session_state=None):
            return None

        async def rephrase_controlled_reply(self, **kwargs):
            return None

        async def generate_reply_with_tools(self, body, session_state, db_session):
            return f"Echo: {body}"

    def fake_person_upsert(_db, _payload):
        return {"success": True, "person_id": PERSON_ID}

    monkeypatch.setattr(settings, "widget_public_token", None, raising=False)
    monkeypatch.setattr(chat_router, "LLMService", FakeLLMService)
    monkeypatch.setattr(chat_router, "handle_create_or_get_person", fake_person_upsert)

    app = FastAPI()
    app.middleware("http")(tenant_context_middleware)
    app.include_router(chat_router.router)

    params = {"provider_key": PROVIDER_KEY, "tenant_token": TENANT_TOKEN}
    with TestClient(app) as client:
        first = client.post(
            "/chat/chat",
            params=params,
            json={
                "message": "Bonjour",
                "client_email": "abdoulaye.chat@example.com",
                "client_name": "Abdoulaye Diallo",
            },
        )
        assert first.status_code == 200
        first_session = first.json().get("session_id")
        assert isinstance(first_session, str) and first_session

        second = client.post(
            "/chat/chat",
            params=params,
            json={
                "message": "Je veux plus de details",
                "client_email": "abdoulaye.chat@example.com",
                "client_name": "Abdoulaye Diallo",
            },
        )
        assert second.status_code == 200
        assert second.json().get("session_id") == first_session

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        conversations = db.query(Conversation).filter(Conversation.tenant_id == UUID(TENANT_ID)).all()
        assert len(conversations) == 1
        state = json.loads(conversations[0].conversation_state or "{}")
        assert state.get("participant_key") == f"person:{PERSON_ID}"
        assert state.get("channel_thread_key") == f"chat:person:{PERSON_ID}"
    finally:
        db.close()


def test_chat_returns_fallback_reply_when_llm_provider_fails(monkeypatch):
    _seed_chat_channel()

    class FakeLLMService:
        def __init__(self):
            self.last_error = "invalid_api_key"
            self.last_fallback_reason = "llm_provider_error"
            self.last_prompt_tokens = 0
            self.last_completion_tokens = 0
            self.model = "fake-model"

        async def extract_structured_message(self, body, session_state=None):
            return None

        async def rephrase_controlled_reply(self, **kwargs):
            return None

        async def generate_reply_with_tools(self, body, session_state, db_session):
            return "temporary fallback"

    monkeypatch.setattr(settings, "widget_public_token", None, raising=False)
    monkeypatch.setattr(chat_router, "LLMService", FakeLLMService)

    app = FastAPI()
    app.middleware("http")(tenant_context_middleware)
    app.include_router(chat_router.router)

    params = {"provider_key": PROVIDER_KEY, "tenant_token": TENANT_TOKEN}
    with TestClient(app) as client:
        res = client.post("/chat/chat", params=params, json={"message": "Bonjour"})

    assert res.status_code == 200
    reply = str(res.json().get("reply") or "").lower()
    assert "souci technique temporaire" in reply
    assert "filiere" in reply or "programme" in reply


def test_chat_lists_catalog_from_db_without_calling_llm(monkeypatch):
    _seed_chat_channel()
    seeded_track = "Genie Logiciel"

    class FailingLLMService:
        async def generate_reply_with_tools(self, body, session_state, db_session):  # pragma: no cover - should not run
            raise AssertionError("LLM should not be called for catalog listing requests")

    def fake_get_track_tuition(_db, arguments):
        query = str((arguments or {}).get("query") or "").lower()
        query = (
            query.replace("é", "e")
            .replace("è", "e")
            .replace("ê", "e")
            .replace("à", "a")
            .replace("î", "i")
            .replace("ï", "i")
        )
        if "filiere" in query or "programme" in query or "program" in query:
            return {
                "success": True,
                "items": [
                    {
                        "track_id": "t1",
                        "track_name": seeded_track,
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

    monkeypatch.setattr(settings, "widget_public_token", None, raising=False)
    monkeypatch.setattr(chat_router, "LLMService", FailingLLMService)
    monkeypatch.setattr(chat_router, "handle_get_track_tuition", fake_get_track_tuition)

    app = FastAPI()
    app.middleware("http")(tenant_context_middleware)
    app.include_router(chat_router.router)

    params = {"provider_key": PROVIDER_KEY, "tenant_token": TENANT_TOKEN}
    with TestClient(app) as client:
        res = client.post("/chat/chat", params=params, json={"message": "quels sont les filières disponibles ?"})

    assert res.status_code == 200
    reply = str(res.json().get("reply") or "")
    assert seeded_track in reply


def test_chat_reuses_track_context_for_details_and_rdv(monkeypatch):
    _seed_chat_channel()
    seeded_track = "Genie Logiciel"

    class FailingLLMService:
        async def generate_reply_with_tools(self, body, session_state, db_session):  # pragma: no cover - should not run
            raise AssertionError("LLM should not be called for track details context flow")

    def fake_get_track_tuition(_db, arguments):
        query = str((arguments or {}).get("query") or "").lower()
        if "genie logiciel" in query:
            return {
                "success": True,
                "items": [
                    {
                        "track_id": "t1",
                        "track_name": seeded_track,
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

    monkeypatch.setattr(settings, "widget_public_token", None, raising=False)
    monkeypatch.setattr(chat_router, "LLMService", FailingLLMService)
    monkeypatch.setattr(chat_router, "handle_get_track_tuition", fake_get_track_tuition)

    app = FastAPI()
    app.middleware("http")(tenant_context_middleware)
    app.include_router(chat_router.router)

    params = {"provider_key": PROVIDER_KEY, "tenant_token": TENANT_TOKEN}
    with TestClient(app) as client:
        first = client.post("/chat/chat", params=params, json={"message": seeded_track})
        assert first.status_code == 200
        session_id = first.json().get("session_id")
        assert isinstance(session_id, str) and session_id

        second = client.post(
            "/chat/chat",
            params=params,
            json={"message": "les details de la filiere et un rendez-vous", "session_id": session_id},
        )

    assert second.status_code == 200
    reply = str(second.json().get("reply") or "").lower()
    assert "genie logiciel" in reply
    assert "rendez-vous" in reply or "rendez vous" in reply


def test_chat_short_yes_uses_session_language_and_advances_rdv_flow(monkeypatch):
    _seed_chat_channel()
    seeded_track = "Informatique Appliquee a la Gestion des Entreprises"

    class FailingLLMService:
        async def generate_reply_with_tools(self, body, session_state, db_session):  # pragma: no cover - should not run
            raise AssertionError("LLM should not be called for short yes in deterministic booking flow")

    def fake_get_track_tuition(_db, arguments):
        query = str((arguments or {}).get("query") or "").lower()
        if "informatique appliquee a la gestion des entreprises" in query:
            return {
                "success": True,
                "items": [
                    {
                        "track_id": "t-iage",
                        "track_name": seeded_track,
                        "program_name": "Licence (L1, L2)",
                        "annual_fee": 890000,
                        "registration_fee": 250000,
                        "monthly_fee": 80000,
                        "access_level": "Bac",
                        "delivery_mode": "onsite",
                        "certifications": "",
                    }
                ],
            }
        return {"success": False, "error": "track_not_found"}

    monkeypatch.setattr(settings, "widget_public_token", None, raising=False)
    monkeypatch.setattr(chat_router, "LLMService", FailingLLMService)
    monkeypatch.setattr(chat_router, "handle_get_track_tuition", fake_get_track_tuition)

    app = FastAPI()
    app.middleware("http")(tenant_context_middleware)
    app.include_router(chat_router.router)

    params = {"provider_key": PROVIDER_KEY, "tenant_token": TENANT_TOKEN}
    with TestClient(app) as client:
        first = client.post("/chat/chat", params=params, json={"message": seeded_track})
        assert first.status_code == 200
        session_id = first.json().get("session_id")
        assert session_id

        second = client.post("/chat/chat", params=params, json={"message": "oui", "session_id": session_id})

    assert second.status_code == 200
    reply = str(second.json().get("reply") or "").lower()
    assert "rendez-vous" in reply or "rendez vous" in reply
    assert "nom" in reply and ("telephone" in reply or "email" in reply)
    assert "je ne comprends pas votre langue" not in reply


def test_chat_contact_message_in_booking_flow_does_not_repeat_track_details(monkeypatch):
    _seed_chat_channel()
    seeded_track = "Informatique Appliquee a la Gestion des Entreprises"

    class FailingLLMService:
        async def generate_reply_with_tools(self, body, session_state, db_session):  # pragma: no cover - should not run
            raise AssertionError("LLM should not be called for deterministic contact collection step")

    def fake_get_track_tuition(_db, arguments):
        query = str((arguments or {}).get("query") or "").lower()
        if "informatique appliquee a la gestion des entreprises" in query:
            return {
                "success": True,
                "items": [
                    {
                        "track_id": "t-iage",
                        "track_name": seeded_track,
                        "program_name": "Licence (L1, L2)",
                        "annual_fee": 890000,
                        "registration_fee": 250000,
                        "monthly_fee": 80000,
                        "access_level": "Bac",
                        "delivery_mode": "onsite",
                        "certifications": "",
                    }
                ],
            }
        return {"success": False, "error": "track_not_found"}

    monkeypatch.setattr(settings, "widget_public_token", None, raising=False)
    monkeypatch.setattr(chat_router, "LLMService", FailingLLMService)
    monkeypatch.setattr(chat_router, "handle_get_track_tuition", fake_get_track_tuition)

    app = FastAPI()
    app.middleware("http")(tenant_context_middleware)
    app.include_router(chat_router.router)

    params = {"provider_key": PROVIDER_KEY, "tenant_token": TENANT_TOKEN}
    with TestClient(app) as client:
        first = client.post("/chat/chat", params=params, json={"message": seeded_track})
        assert first.status_code == 200
        session_id = first.json().get("session_id")
        assert session_id

        yes_step = client.post(
            "/chat/chat",
            params=params,
            json={"message": "oui reserve un rendez-vous", "session_id": session_id},
        )
        assert yes_step.status_code == 200

        contact_step = client.post(
            "/chat/chat",
            params=params,
            json={
                "message": (
                    "Je suis intéressé par le programme Informatique Appliquee a la Gestion des Entreprises "
                    "et voici mes information Abdoulaye DIALLO diabdullah113@gmail.com comme mail "
                    "et +221776625059 comme numero"
                ),
                "session_id": session_id,
            },
        )

    assert contact_step.status_code == 200
    reply = str(contact_step.json().get("reply") or "").lower()
    assert "date" in reply and ("heure" in reply or "waxtu" in reply)
    assert "frais annuels" not in reply


def test_chat_affirmative_phrase_with_urgency_advances_booking_flow(monkeypatch):
    _seed_chat_channel()
    seeded_track = "Data Science & Intelligence Artificielle"

    class FailingLLMService:
        async def generate_reply_with_tools(self, body, session_state, db_session):  # pragma: no cover - should not run
            raise AssertionError("LLM should not be called for affirmative+urgency booking step")

    def fake_get_track_tuition(_db, arguments):
        query = str((arguments or {}).get("query") or "").lower()
        if "data science" in query:
            return {
                "success": True,
                "items": [
                    {
                        "track_id": "t-ds",
                        "track_name": seeded_track,
                        "program_name": "Data Science & Intelligence Artificielle",
                        "annual_fee": 1600000,
                        "registration_fee": 250000,
                        "monthly_fee": 150000,
                        "access_level": "Bac +2",
                        "delivery_mode": "onsite",
                        "certifications": "AWS",
                    }
                ],
            }
        return {"success": False, "error": "track_not_found"}

    monkeypatch.setattr(settings, "widget_public_token", None, raising=False)
    monkeypatch.setattr(chat_router, "LLMService", FailingLLMService)
    monkeypatch.setattr(chat_router, "handle_get_track_tuition", fake_get_track_tuition)

    app = FastAPI()
    app.middleware("http")(tenant_context_middleware)
    app.include_router(chat_router.router)

    params = {"provider_key": PROVIDER_KEY, "tenant_token": TENANT_TOKEN}
    with TestClient(app) as client:
        first = client.post("/chat/chat", params=params, json={"message": f"Je suis interesse par {seeded_track}"})
        assert first.status_code == 200
        session_id = first.json().get("session_id")
        assert session_id
        second = client.post(
            "/chat/chat",
            params=params,
            json={"message": "oui et le plus tot possible", "session_id": session_id},
        )

    assert second.status_code == 200
    reply = str(second.json().get("reply") or "").lower()
    assert "rendez-vous" in reply or "rendez vous" in reply
    assert "nom" in reply
    assert "telephone" in reply or "email" in reply
    assert "souci technique temporaire" not in reply


def test_chat_contact_and_datetime_same_message_goes_to_confirm_not_repeat(monkeypatch):
    _seed_chat_channel()
    seeded_track = "Data Science & Intelligence Artificielle"

    class FailingLLMService:
        async def generate_reply_with_tools(self, body, session_state, db_session):  # pragma: no cover - should not run
            raise AssertionError("LLM should not be called when booking payload contains contact+datetime")

    def fake_get_track_tuition(_db, arguments):
        query = str((arguments or {}).get("query") or "").lower()
        if "data science" in query:
            return {
                "success": True,
                "items": [
                    {
                        "track_id": "t-ds",
                        "track_name": seeded_track,
                        "program_name": "Data Science & Intelligence Artificielle",
                        "annual_fee": 1600000,
                        "registration_fee": 250000,
                        "monthly_fee": 150000,
                        "access_level": "Bac +2",
                        "delivery_mode": "onsite",
                        "certifications": "AWS",
                    }
                ],
            }
        return {"success": False, "error": "track_not_found"}

    monkeypatch.setattr(settings, "widget_public_token", None, raising=False)
    monkeypatch.setattr(chat_router, "LLMService", FailingLLMService)
    monkeypatch.setattr(chat_router, "handle_get_track_tuition", fake_get_track_tuition)

    app = FastAPI()
    app.middleware("http")(tenant_context_middleware)
    app.include_router(chat_router.router)
    params = {"provider_key": PROVIDER_KEY, "tenant_token": TENANT_TOKEN}

    with TestClient(app) as client:
        first = client.post("/chat/chat", params=params, json={"message": f"Je suis interesse par {seeded_track}"})
        session_id = first.json().get("session_id")
        assert session_id
        client.post("/chat/chat", params=params, json={"message": "oui", "session_id": session_id})
        payload_step = client.post(
            "/chat/chat",
            params=params,
            json={
                "message": "DIALLO Abdoulaye, 776625059, diabdullah113@gmail.com, le 28 février 2026 a 15h",
                "session_id": session_id,
            },
        )

    assert payload_step.status_code == 200
    reply = str(payload_step.json().get("reply") or "").lower()
    assert "recap" in reply or "recapitulatif" in reply or "summary" in reply
    assert "confirmer" in reply or "confirm" in reply
    assert "date et l'heure" not in reply


def test_chat_date_only_message_in_booking_flow_uses_locked_language(monkeypatch):
    _seed_chat_channel()
    seeded_track = "Data Science & Intelligence Artificielle"

    class FailingLLMService:
        async def generate_reply_with_tools(self, body, session_state, db_session):  # pragma: no cover - should not run
            raise AssertionError("LLM should not be called for date-only booking follow-up")

    def fake_get_track_tuition(_db, arguments):
        query = str((arguments or {}).get("query") or "").lower()
        if "data science" in query:
            return {
                "success": True,
                "items": [
                    {
                        "track_id": "t-ds",
                        "track_name": seeded_track,
                        "program_name": "Data Science & Intelligence Artificielle",
                        "annual_fee": 1600000,
                        "registration_fee": 250000,
                        "monthly_fee": 150000,
                        "access_level": "Bac +2",
                        "delivery_mode": "onsite",
                        "certifications": "AWS",
                    }
                ],
            }
        return {"success": False, "error": "track_not_found"}

    monkeypatch.setattr(settings, "widget_public_token", None, raising=False)
    monkeypatch.setattr(chat_router, "LLMService", FailingLLMService)
    monkeypatch.setattr(chat_router, "handle_get_track_tuition", fake_get_track_tuition)

    app = FastAPI()
    app.middleware("http")(tenant_context_middleware)
    app.include_router(chat_router.router)
    params = {"provider_key": PROVIDER_KEY, "tenant_token": TENANT_TOKEN}

    with TestClient(app) as client:
        first = client.post("/chat/chat", params=params, json={"message": f"Je suis interesse par {seeded_track}"})
        session_id = first.json().get("session_id")
        assert session_id
        client.post("/chat/chat", params=params, json={"message": "oui", "session_id": session_id})
        client.post(
            "/chat/chat",
            params=params,
            json={"message": "DIALLO Abdoulaye diabdullah113@gmail.com +221776625059", "session_id": session_id},
        )
        date_step = client.post("/chat/chat", params=params, json={"message": "28/02/2026 à 15h", "session_id": session_id})

    assert date_step.status_code == 200
    reply = str(date_step.json().get("reply") or "").lower()
    assert "sorry, i don't understand your language" not in reply
    assert "we are experiencing a temporary technical issue" not in reply
    assert "confirmer" in reply or "confirm" in reply


def test_chat_persists_agent_state_keys_and_flow(monkeypatch):
    _seed_chat_channel()
    seeded_track = "Informatique Appliquee a la Gestion des Entreprises"

    class FailingLLMService:
        async def generate_reply_with_tools(self, body, session_state, db_session):  # pragma: no cover - should not run
            raise AssertionError("LLM should not be called for deterministic state persistence test")

    def fake_get_track_tuition(_db, arguments):
        query = str((arguments or {}).get("query") or "").lower()
        if "informatique appliquee" in query:
            return {
                "success": True,
                "items": [
                    {
                        "track_id": "t-iage",
                        "track_name": seeded_track,
                        "program_name": "Licence (L1, L2)",
                        "annual_fee": 890000,
                        "registration_fee": 250000,
                        "monthly_fee": 80000,
                        "access_level": "Bac",
                        "delivery_mode": "onsite",
                        "certifications": "",
                    }
                ],
            }
        return {"success": False, "error": "track_not_found"}

    monkeypatch.setattr(settings, "widget_public_token", None, raising=False)
    monkeypatch.setattr(chat_router, "LLMService", FailingLLMService)
    monkeypatch.setattr(chat_router, "handle_get_track_tuition", fake_get_track_tuition)

    app = FastAPI()
    app.middleware("http")(tenant_context_middleware)
    app.include_router(chat_router.router)
    params = {"provider_key": PROVIDER_KEY, "tenant_token": TENANT_TOKEN}
    with TestClient(app) as client:
        first = client.post("/chat/chat", params=params, json={"message": seeded_track})
        session_id = first.json().get("session_id")
        assert session_id
        client.post("/chat/chat", params=params, json={"message": "oui", "session_id": session_id})
        client.post(
            "/chat/chat",
            params=params,
            json={"message": "Abdoulaye Diallo diabdullah113@gmail.com +221776625059", "session_id": session_id},
        )

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        conv = db.get(Conversation, UUID(session_id))
        assert conv is not None
        payload = json.loads(conv.conversation_state or "{}")
        assert payload["language_locked"] == "fr"
        assert payload["active_flow"] in {"booking_collect_datetime", "booking_confirm"}
        assert isinstance(payload.get("slots_json"), dict)
        assert payload["slots_json"].get("track_name") == seeded_track
        assert payload["response_strategy"].startswith("deterministic_")
    finally:
        db.close()


def test_name_heuristic_does_not_treat_confusion_message_as_name():
    assert chat_router._looks_like_name_only_message("tu raconte quoi ?") is False


def test_chat_structured_extraction_enriches_booking_without_controlling_transitions(monkeypatch):
    _seed_chat_channel()
    seeded_track = "Genie Logiciel"

    class FakeP2LLMService:
        def __init__(self):
            self.last_error = None
            self.last_fallback_reason = None
            self.last_prompt_tokens = 0
            self.last_completion_tokens = 0
            self.model = "fake-model"

        def is_configured(self):
            return True

        async def extract_structured_message(self, body, session_state=None):
            text = str(body or "").lower()
            if "samedi prochain" in text:
                return {
                    "appointment_date": "2026-02-28",
                    "appointment_time": "15:00",
                    "full_name": "Abdoulaye Diallo",
                    "email": "diabdullah113@gmail.com",
                    "phone": "+221776625059",
                }
            return None

        async def rephrase_controlled_reply(self, **kwargs):
            return None

        async def generate_reply_with_tools(self, body, session_state, db_session):  # pragma: no cover - should not run
            raise AssertionError("LLM free-form generation should not be called in deterministic booking flow")

    def fake_get_track_tuition(_db, arguments):
        query = str((arguments or {}).get("query") or "").lower()
        if "genie logiciel" in query:
            return {
                "success": True,
                "items": [
                    {
                        "track_id": "t1",
                        "track_name": seeded_track,
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

    monkeypatch.setattr(settings, "widget_public_token", None, raising=False)
    monkeypatch.setattr(settings, "llm_structured_extraction_enabled", True, raising=False)
    monkeypatch.setattr(settings, "llm_deterministic_rephrase_enabled", False, raising=False)
    monkeypatch.setattr(chat_router, "LLMService", FakeP2LLMService)
    monkeypatch.setattr(chat_router, "handle_get_track_tuition", fake_get_track_tuition)

    app = FastAPI()
    app.middleware("http")(tenant_context_middleware)
    app.include_router(chat_router.router)

    params = {"provider_key": PROVIDER_KEY, "tenant_token": TENANT_TOKEN}
    with TestClient(app) as client:
        first = client.post("/chat/chat", params=params, json={"message": "Genie Logiciel"})
        session_id = first.json().get("session_id")
        assert session_id
        client.post("/chat/chat", params=params, json={"message": "oui", "session_id": session_id})
        final_step = client.post(
            "/chat/chat",
            params=params,
            json={
                "message": "Abdoulaye Diallo diabdullah113@gmail.com +221776625059 samedi prochain a 15h",
                "session_id": session_id,
            },
        )

    assert final_step.status_code == 200
    reply = str(final_step.json().get("reply") or "").lower()
    assert "recap" in reply or "recapitulatif" in reply or "summary" in reply
    assert "confirmer" in reply or "confirm" in reply


def test_chat_deterministic_reply_can_use_controlled_rephrase_layer(monkeypatch):
    _seed_chat_channel()

    class FakeP2LLMService:
        def __init__(self):
            self.last_error = None
            self.last_fallback_reason = None
            self.last_prompt_tokens = 0
            self.last_completion_tokens = 0
            self.model = "fake-model"

        def is_configured(self):
            return True

        async def extract_structured_message(self, body, session_state=None):
            return None

        async def rephrase_controlled_reply(self, **kwargs):
            return "Réponse reformulée contrôlée"

        async def generate_reply_with_tools(self, body, session_state, db_session):  # pragma: no cover - should not run
            raise AssertionError("Free-form LLM generation should not run for deterministic catalog query")

    def fake_get_track_tuition(_db, arguments):
        query = str((arguments or {}).get("query") or "").lower()
        if "filiere" in query or "programme" in query or "program" in query:
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

    monkeypatch.setattr(settings, "widget_public_token", None, raising=False)
    monkeypatch.setattr(settings, "llm_structured_extraction_enabled", True, raising=False)
    monkeypatch.setattr(settings, "llm_deterministic_rephrase_enabled", True, raising=False)
    monkeypatch.setattr(chat_router, "LLMService", FakeP2LLMService)
    monkeypatch.setattr(chat_router, "handle_get_track_tuition", fake_get_track_tuition)

    app = FastAPI()
    app.middleware("http")(tenant_context_middleware)
    app.include_router(chat_router.router)
    params = {"provider_key": PROVIDER_KEY, "tenant_token": TENANT_TOKEN}

    with TestClient(app) as client:
        res = client.post("/chat/chat", params=params, json={"message": "quelles filieres avez-vous ?"})

    assert res.status_code == 200
    assert res.json().get("reply") == "Réponse reformulée contrôlée"
