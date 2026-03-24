from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

import pytest
from starlette.requests import Request

from app.db import Base, engine, open_db_session
from app.models import Calendar, Conversation, EmailLog, Event, Message, Person, PersonRole, SMSLog
from app.routers.calendar import get_calendar_stats
from app.routers.knowledge_base import conversations_stats
from app.routers.notifications import list_email_logs, list_sms_logs
from app.routers.school_people import persons_stats

TENANT_A = "00000000-0000-0000-0000-000000000001"
TENANT_B = "00000000-0000-0000-0000-000000000002"


def _request_for_tenant(tenant_id: str) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/tests",
        "raw_path": b"/tests",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 5100),
        "server": ("testserver", 443),
        "scheme": "https",
        "state": {"tenant_id": tenant_id},
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


@pytest.fixture(scope="module", autouse=True)
def _setup_tables():
    Base.metadata.drop_all(
        bind=engine,
        tables=[
            PersonRole.__table__,
            Person.__table__,
            Message.__table__,
            Conversation.__table__,
            SMSLog.__table__,
            EmailLog.__table__,
            Event.__table__,
            Calendar.__table__,
        ],
        checkfirst=True,
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Calendar.__table__,
            Event.__table__,
            Person.__table__,
            PersonRole.__table__,
            Conversation.__table__,
            Message.__table__,
            EmailLog.__table__,
            SMSLog.__table__,
        ],
    )
    yield
    Base.metadata.drop_all(
        bind=engine,
        tables=[
            SMSLog.__table__,
            EmailLog.__table__,
            Message.__table__,
            Conversation.__table__,
            PersonRole.__table__,
            Person.__table__,
            Event.__table__,
            Calendar.__table__,
        ],
    )


def _reset_seed_data() -> None:
    db = open_db_session(allow_unscoped=True)
    try:
        db.query(SMSLog).delete()
        db.query(EmailLog).delete()
        db.query(Message).delete()
        db.query(Conversation).delete()
        db.query(PersonRole).delete()
        db.query(Person).delete()
        db.query(Event).delete()
        db.query(Calendar).delete()
        db.commit()
    finally:
        db.close()


def test_conversations_kpi_seed_expected():
    _reset_seed_data()
    now = datetime.now()

    db_seed = open_db_session(allow_unscoped=True)
    try:
        conv_call_a = Conversation(
            tenant_id=UUID(TENANT_A),
            canal="call",
            status="closed",
            created_at=now,
            recording_duration=180,
            recording_url="https://recording-a.mp3",
            recording_consent=True,
        )
        conv_wa_a = Conversation(
            tenant_id=UUID(TENANT_A),
            canal="whatsapp",
            status="active",
            created_at=now - timedelta(days=1),
        )
        conv_call_b = Conversation(
            tenant_id=UUID(TENANT_B),
            canal="call",
            status="closed",
            created_at=now,
            recording_duration=400,
            recording_url="https://recording-b.mp3",
            recording_consent=True,
        )
        db_seed.add_all([conv_call_a, conv_wa_a, conv_call_b])
        db_seed.flush()

        db_seed.add_all(
            [
                Message(
                    tenant_id=UUID(TENANT_A),
                    conversation_id=conv_call_a.id,
                    role="user",
                    canal="call",
                    content="bonjour",
                    created_at=now,
                ),
                Message(
                    tenant_id=UUID(TENANT_A),
                    conversation_id=conv_call_a.id,
                    role="assistant",
                    canal="call",
                    content="reponse",
                    created_at=now + timedelta(seconds=30),
                ),
                Message(
                    tenant_id=UUID(TENANT_A),
                    conversation_id=conv_wa_a.id,
                    role="user",
                    canal="whatsapp",
                    content="question",
                    created_at=now - timedelta(days=1),
                ),
                Message(
                    tenant_id=UUID(TENANT_B),
                    conversation_id=conv_call_b.id,
                    role="assistant",
                    canal="call",
                    content="should_not_leak",
                    created_at=now,
                ),
            ]
        )
        db_seed.commit()
    finally:
        db_seed.close()

    db_a = open_db_session(TENANT_A)
    try:
        call_stats = conversations_stats(canal="call", db=db_a)
        assert call_stats["total"] == 1
        assert call_stats["recording_count"] == 1
        assert call_stats["total_duration_seconds"] == 180
        assert call_stats["response_rate"] == 100.0
        assert call_stats["resolution_rate"] == 100.0
        assert call_stats["today_count"] == 1

        global_stats = conversations_stats(canal=None, db=db_a)
        assert global_stats["total"] == 2
        assert global_stats["by_channel"]["call"] == 1
        assert global_stats["by_channel"]["whatsapp"] == 1
    finally:
        db_a.close()


def test_notifications_kpi_seed_expected():
    _reset_seed_data()
    now = datetime.now()

    db_seed = open_db_session(allow_unscoped=True)
    try:
        db_seed.add_all(
            [
                EmailLog(tenant_id=UUID(TENANT_A), sujet="A1", statut="sent", created_at=now),
                EmailLog(tenant_id=UUID(TENANT_A), sujet="A2", statut="failed", created_at=now),
                EmailLog(tenant_id=UUID(TENANT_B), sujet="B1", statut="sent", created_at=now),
                SMSLog(tenant_id=UUID(TENANT_A), contenu="A1", statut="sent", created_at=now),
                SMSLog(tenant_id=UUID(TENANT_A), contenu="A2", statut="queued", created_at=now),
                SMSLog(tenant_id=UUID(TENANT_B), contenu="B1", statut="failed", created_at=now),
            ]
        )
        db_seed.commit()
    finally:
        db_seed.close()

    req_a = _request_for_tenant(TENANT_A)
    db_a = open_db_session(TENANT_A)
    try:
        emails = list_email_logs(request=req_a, limit=50, offset=0, db=db_a)
        assert emails["total"] == 2
        assert emails["status_counts"]["sent"] == 1
        assert emails["status_counts"]["failed"] == 1
        assert emails["kpis"]["delivery_rate"] == 50.0
        assert emails["kpis"]["failure_rate"] == 50.0

        sms = list_sms_logs(request=req_a, limit=50, offset=0, db=db_a)
        assert sms["total"] == 2
        assert sms["status_counts"]["sent"] == 1
        assert sms["status_counts"]["queued"] == 1
        assert sms["kpis"]["delivery_rate"] == 50.0
    finally:
        db_a.close()


def test_persons_kpi_seed_expected():
    _reset_seed_data()
    now = datetime.now()

    db_seed = open_db_session(allow_unscoped=True)
    try:
        p1 = Person(
            tenant_id=UUID(TENANT_A),
            first_name="Aminata",
            last_name="Diallo",
            email="aminata@example.com",
            status="active",
            created_at=now,
        )
        p2 = Person(
            tenant_id=UUID(TENANT_A),
            first_name="Mamadou",
            last_name="Fall",
            email="mamadou@example.com",
            status="inactive",
            created_at=now - timedelta(days=2),
        )
        p3 = Person(
            tenant_id=UUID(TENANT_A),
            first_name="Fatou",
            last_name="Ndiaye",
            email="fatou@example.com",
            status="active",
            created_at=now - timedelta(days=20),
        )
        p4 = Person(
            tenant_id=UUID(TENANT_B),
            first_name="Other",
            last_name="Tenant",
            email="other@example.com",
            status="active",
            created_at=now,
        )
        db_seed.add_all([p1, p2, p3, p4])
        db_seed.flush()

        db_seed.add_all(
                [
                PersonRole(id=1, tenant_id=UUID(TENANT_A), person_id=p1.id, role="candidate"),
                PersonRole(id=2, tenant_id=UUID(TENANT_A), person_id=p2.id, role="candidate"),
                PersonRole(id=3, tenant_id=UUID(TENANT_A), person_id=p1.id, role="student"),
                PersonRole(id=4, tenant_id=UUID(TENANT_A), person_id=p3.id, role="parent"),
                PersonRole(id=5, tenant_id=UUID(TENANT_B), person_id=p4.id, role="candidate"),
                ]
            )
        db_seed.commit()
    finally:
        db_seed.close()

    db_a = open_db_session(TENANT_A)
    try:
        stats = persons_stats(db=db_a)
        assert stats["total"] == 3
        assert stats["active"] == 2
        assert stats["inactive"] == 1
        assert stats["candidates"] == 2
        assert stats["students"] == 1
        assert stats["parents"] == 1
        assert stats["new_7d"] == 2
        assert stats["conversion_rate"] == 50.0
    finally:
        db_a.close()


def test_calendar_kpi_seed_expected():
    _reset_seed_data()
    # Use a stable daytime baseline to avoid midnight boundary flakiness.
    now = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)

    db_seed = open_db_session(allow_unscoped=True)
    try:
        cal_a = Calendar(tenant_id=UUID(TENANT_A), name="A Calendar", owner="owner-a@example.com")
        cal_b = Calendar(tenant_id=UUID(TENANT_B), name="B Calendar", owner="owner-b@example.com")
        db_seed.add_all([cal_a, cal_b])
        db_seed.flush()

        db_seed.add_all(
            [
                Event(
                    tenant_id=UUID(TENANT_A),
                    calendar_id=cal_a.id,
                    title="A confirmed today",
                    start_at=now + timedelta(hours=1),
                    end_at=now + timedelta(hours=2),
                    attendees="a@example.com;b@example.com",
                    status="confirmed",
                ),
                Event(
                    tenant_id=UUID(TENANT_A),
                    calendar_id=cal_a.id,
                    title="A pending tomorrow",
                    start_at=now + timedelta(days=1, hours=1),
                    end_at=now + timedelta(days=1, hours=2),
                    attendees="c@example.com",
                    status="pending",
                ),
                Event(
                    tenant_id=UUID(TENANT_A),
                    calendar_id=cal_a.id,
                    title="A cancelled yesterday",
                    start_at=now - timedelta(days=1, hours=1),
                    end_at=now - timedelta(days=1),
                    attendees="d@example.com",
                    status="cancelled",
                ),
                Event(
                    tenant_id=UUID(TENANT_B),
                    calendar_id=cal_b.id,
                    title="B confirmed",
                    start_at=now + timedelta(hours=1),
                    end_at=now + timedelta(hours=2),
                    attendees="other@example.com",
                    status="confirmed",
                ),
            ]
        )
        db_seed.commit()
    finally:
        db_seed.close()

    db_a = open_db_session(TENANT_A)
    try:
        stats = get_calendar_stats(db=db_a)
        assert stats["total_events"] == 3
        assert stats["confirmed_count"] == 1
        assert stats["pending_count"] == 1
        assert stats["cancelled_count"] == 1
        assert stats["today_count"] == 1
        # Depending on execution hour, the "today" event can already be in the past
        # for the rolling "week_count >= now" metric.
        assert stats["week_count"] in {1, 2}
        assert stats["total_participants"] == 4
        assert stats["attendance_rate"] == 33.3
        assert stats["cancel_rate"] == 33.3
    finally:
        db_a.close()
