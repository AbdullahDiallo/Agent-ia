from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from uuid import UUID

from app.db import Base, engine, open_db_session
from app.models import (
    BillingPlan,
    Agent,
    Calendar,
    EmailLog,
    Event,
    OutboxEvent,
    Person,
    RendezVous,
    SchoolAdmissionPolicy,
    SchoolAdmissionRequirement,
    SchoolDepartment,
    SchoolProgram,
    SchoolTrack,
    SMSLog,
    Tenant,
    User,
)
from app.services import kb as kb_service
from app.services import llm_tools as llm_tools_module
from app.services import notification_dispatch as notification_dispatch_module
from app.services import outbox as outbox_module
from app.services.llm_tools import handle_create_school_appointment
from app.services.outbox import (
    EVENT_APPOINTMENT_CALENDAR_SYNC,
    EVENT_APPOINTMENT_CRM_SYNC,
    EVENT_APPOINTMENT_NOTIFICATION_EMAIL,
    EVENT_APPOINTMENT_NOTIFICATION_SMS,
    EVENT_APPOINTMENT_STAFF_NOTIFICATION,
    EVENT_NOTIFICATION_PREFERRED,
    enqueue_event,
    process_outbox_batch,
    process_staff_notification_event,
)


TENANT_ID = "00000000-0000-0000-0000-00000000a401".replace("a", "0")


def _ensure_tables() -> None:
    Base.metadata.create_all(
        bind=engine,
        tables=[
            BillingPlan.__table__, Tenant.__table__,
            User.__table__,
            Agent.__table__,
            Person.__table__,
            SchoolAdmissionRequirement.__table__,
            SchoolAdmissionPolicy.__table__,
            SchoolDepartment.__table__,
            SchoolProgram.__table__,
            SchoolTrack.__table__,
            RendezVous.__table__,
            Calendar.__table__,
            Event.__table__,
            EmailLog.__table__,
            SMSLog.__table__,
            OutboxEvent.__table__,
        ],
        checkfirst=True,
    )


def _seed_base_data(*, with_agents: int = 1) -> dict[str, str]:
    _ensure_tables()
    db = open_db_session(tenant_id=TENANT_ID)
    try:
        tenant_uuid = UUID(TENANT_ID)
        if not db.get(Tenant, tenant_uuid):
            db.add(Tenant(id=tenant_uuid, slug="tenant-rdv-assignment", name="Tenant RDV Assignment", is_active=True))
            db.flush()

        db.query(RendezVous).filter(RendezVous.tenant_id == tenant_uuid).delete()
        db.query(OutboxEvent).filter(OutboxEvent.tenant_id == tenant_uuid).delete()
        db.query(EmailLog).filter(EmailLog.tenant_id == tenant_uuid).delete()
        db.query(SMSLog).filter(SMSLog.tenant_id == tenant_uuid).delete()
        db.query(SchoolTrack).filter(SchoolTrack.tenant_id == tenant_uuid).delete()
        db.query(SchoolProgram).filter(SchoolProgram.tenant_id == tenant_uuid).delete()
        db.query(SchoolDepartment).filter(SchoolDepartment.tenant_id == tenant_uuid).delete()
        db.query(SchoolAdmissionRequirement).filter(SchoolAdmissionRequirement.tenant_id == tenant_uuid).delete()
        db.query(SchoolAdmissionPolicy).filter(SchoolAdmissionPolicy.tenant_id == tenant_uuid).delete()
        db.query(Agent).filter(Agent.tenant_id == tenant_uuid).delete()
        db.query(User).filter(User.tenant_id == tenant_uuid).delete()
        db.query(Person).filter(Person.tenant_id == tenant_uuid).delete()
        db.commit()

        person1 = Person(
            tenant_id=tenant_uuid,
            first_name="Abdoulaye",
            last_name="Diallo",
            email="abdoulaye.rdv1@example.com",
            phone="+221770001001",
            preferred_language="fr",
        )
        person2 = Person(
            tenant_id=tenant_uuid,
            first_name="Awa",
            last_name="Ndiaye",
            email="awa.rdv2@example.com",
            phone="+221770001002",
            preferred_language="fr",
        )
        db.add_all([person1, person2])
        db.flush()

        dept = SchoolDepartment(tenant_id=tenant_uuid, name="Informatique", code="INFO", description="Dept test")
        db.add(dept)
        db.flush()
        program = SchoolProgram(
            tenant_id=tenant_uuid,
            department_id=dept.id,
            name="Licence Professionnelle",
            description="Programme test",
            delivery_mode="onsite",
            access_level="Bac +2",
            is_active=True,
        )
        db.add(program)
        db.flush()
        track = SchoolTrack(
            tenant_id=tenant_uuid,
            program_id=program.id,
            name="Genie Logiciel",
            annual_fee=1330000,
            registration_fee=250000,
            monthly_fee=100000,
            certifications="",
            options="",
            is_active=True,
        )
        db.add(track)
        db.flush()

        agent_ids: list[str] = []
        for idx in range(with_agents):
            user = User(
                id=1000 + idx,
                tenant_id=tenant_uuid,
                first_name=f"Agent{idx+1}",
                last_name="Admission",
                email=f"agent{idx+1}@example.com",
                password_hash="x",
            )
            db.add(user)
            db.flush()
            agent = Agent(
                tenant_id=tenant_uuid,
                user_id=user.id,
                specialite="admission",
                disponible=True,
                max_rdv_par_jour=8,
            )
            db.add(agent)
            db.flush()
            agent_ids.append(str(agent.id))

        db.commit()
        return {
            "person1_id": str(person1.id),
            "person2_id": str(person2.id),
            "track_id": str(track.id),
            "track_name": track.name,
            "program_name": program.name,
            "agent_ids": agent_ids,
        }
    finally:
        db.close()


async def _fake_notifications(*args, **kwargs):
    return {"sent": True, "channel": "email"}


def _future_slot(days: int = 30, hour: int = 15) -> tuple[str, str]:
    dt = datetime.now() + timedelta(days=days)
    dt = dt.replace(hour=hour, minute=0, second=0, microsecond=0)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


def test_create_school_appointment_assigns_available_agent(monkeypatch):
    seeded = _seed_base_data(with_agents=1)
    monkeypatch.setattr(llm_tools_module, "_send_school_rdv_notifications", _fake_notifications)
    date_str, time_str = _future_slot(days=60, hour=16)

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        result = asyncio.run(
            handle_create_school_appointment(
                db,
                {
                    "person_id": seeded["person1_id"],
                    "track_id": seeded["track_id"],
                    "date": date_str,
                    "time": time_str,
                    "duration_minutes": 45,
                    "statut": "pending",
                    "lang": "fr",
                },
            )
        )
        assert result["success"] is True
        assert result["status"] == "created"
        assert result.get("agent_id") == seeded["agent_ids"][0]
        assert result.get("agent_name")

        rdv = db.query(RendezVous).filter(RendezVous.tenant_id == UUID(TENANT_ID)).one()
        assert rdv.agent_id is not None
        assert rdv.statut == "created"
    finally:
        db.close()


def test_create_school_appointment_rejects_when_no_agent_available(monkeypatch):
    seeded = _seed_base_data(with_agents=0)
    monkeypatch.setattr(llm_tools_module, "_send_school_rdv_notifications", _fake_notifications)
    date_str, time_str = _future_slot(days=61, hour=11)

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        result = asyncio.run(
            handle_create_school_appointment(
                db,
                {
                    "person_id": seeded["person1_id"],
                    "track_id": seeded["track_id"],
                    "date": date_str,
                    "time": time_str,
                    "duration_minutes": 45,
                    "statut": "pending",
                    "lang": "fr",
                },
            )
        )
        assert result == {"success": False, "error": "no_agent_available"}
        assert db.query(RendezVous).filter(RendezVous.tenant_id == UUID(TENANT_ID)).count() == 0
    finally:
        db.close()


def test_create_school_appointment_assigns_different_agents_for_same_slot(monkeypatch):
    seeded = _seed_base_data(with_agents=2)
    monkeypatch.setattr(llm_tools_module, "_send_school_rdv_notifications", _fake_notifications)
    date_str, time_str = _future_slot(days=62, hour=10)

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        first = asyncio.run(
            handle_create_school_appointment(
                db,
                {
                    "person_id": seeded["person1_id"],
                    "track_id": seeded["track_id"],
                    "date": date_str,
                    "time": time_str,
                    "duration_minutes": 45,
                    "statut": "pending",
                    "lang": "fr",
                },
            )
        )
        second = asyncio.run(
            handle_create_school_appointment(
                db,
                {
                    "person_id": seeded["person2_id"],
                    "track_id": seeded["track_id"],
                    "date": date_str,
                    "time": time_str,
                    "duration_minutes": 45,
                    "statut": "pending",
                    "lang": "fr",
                },
            )
        )

        assert first["success"] is True
        assert second["success"] is True
        assert first["agent_id"] != second["agent_id"]
        rdvs = db.query(RendezVous).filter(RendezVous.tenant_id == UUID(TENANT_ID)).all()
        assert len(rdvs) == 2
        assert all(r.agent_id is not None for r in rdvs)
    finally:
        db.close()


def test_kb_create_rendezvous_assigns_agent_when_required():
    seeded = _seed_base_data(with_agents=1)
    date_str, time_str = _future_slot(days=63, hour=14)
    start_at = datetime.fromisoformat(f"{date_str}T{time_str}:00")
    end_at = start_at + timedelta(minutes=45)

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        rdv = kb_service.create_rendezvous(
            db,
            person_id=UUID(seeded["person1_id"]),
            track_id=UUID(seeded["track_id"]),
            start_at=start_at,
            end_at=end_at,
            require_assigned_agent=True,
        )
        assert rdv.agent_id is not None
        assert rdv.agent
    finally:
        db.close()


def test_kb_create_rendezvous_rejects_when_no_agent_available_if_required():
    seeded = _seed_base_data(with_agents=0)
    date_str, time_str = _future_slot(days=64, hour=9)
    start_at = datetime.fromisoformat(f"{date_str}T{time_str}:00")
    end_at = start_at + timedelta(minutes=45)

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        try:
            kb_service.create_rendezvous(
                db,
                person_id=UUID(seeded["person1_id"]),
                track_id=UUID(seeded["track_id"]),
                start_at=start_at,
                end_at=end_at,
                require_assigned_agent=True,
            )
            raise AssertionError("Expected ValueError no_agent_available")
        except ValueError as exc:
            assert str(exc) == "no_agent_available"
        assert db.query(RendezVous).filter(RendezVous.tenant_id == UUID(TENANT_ID)).count() == 0
    finally:
        db.close()


def test_create_school_appointment_enqueues_outbox_side_effects_and_returns_queued_notification():
    seeded = _seed_base_data(with_agents=1)
    date_str, time_str = _future_slot(days=65, hour=15)

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        result = asyncio.run(
            handle_create_school_appointment(
                db,
                {
                    "person_id": seeded["person1_id"],
                    "track_id": seeded["track_id"],
                    "date": date_str,
                    "time": time_str,
                    "duration_minutes": 45,
                    "statut": "pending",
                    "lang": "fr",
                },
            )
        )
        appointment_id = result.get("appointment_id")
        events = (
            db.query(OutboxEvent)
            .filter(OutboxEvent.tenant_id == UUID(TENANT_ID), OutboxEvent.aggregate_id == str(appointment_id))
            .order_by(OutboxEvent.created_at.asc())
            .all()
        )
    finally:
        db.close()

    assert result["success"] is True
    assert result["notifications"] == {
        "email": {"status": "pending"},
        "sms": {"status": "pending"},
        "queued": True,
        "reason": "queued_via_outbox",
    }
    assert appointment_id
    assert {row.event_type for row in events} == {
        EVENT_APPOINTMENT_NOTIFICATION_EMAIL,
        EVENT_APPOINTMENT_NOTIFICATION_SMS,
        EVENT_APPOINTMENT_STAFF_NOTIFICATION,
        EVENT_APPOINTMENT_CALENDAR_SYNC,
        EVENT_APPOINTMENT_CRM_SYNC,
    }
    assert all(row.status == "pending" for row in events)

    payloads = {row.event_type: json.loads(row.payload or "{}") for row in events}
    email_payload = payloads[EVENT_APPOINTMENT_NOTIFICATION_EMAIL]
    sms_payload = payloads[EVENT_APPOINTMENT_NOTIFICATION_SMS]
    staff_payload = payloads[EVENT_APPOINTMENT_STAFF_NOTIFICATION]
    assert email_payload["person_id"] == seeded["person1_id"]
    assert email_payload["recipient"] == "abdoulaye.rdv1@example.com"
    assert sms_payload["recipient"] == "+221770001001"
    assert staff_payload["recipient_scope"] == "staff"
    assert staff_payload["assigned_agent_email"] == "agent1@example.com"
    assert staff_payload["staff_recipient_emails"] == ["agent1@example.com"]
    assert staff_payload["person_email"] == "abdoulaye.rdv1@example.com"


def test_booking_persists_even_if_outbox_worker_fails_later(monkeypatch):
    seeded = _seed_base_data(with_agents=1)
    date_str, time_str = _future_slot(days=66, hour=13)

    class FailingEmailService:
        provider = "fake"

        async def send_email_result(self, *_args, **_kwargs):
            from app.services.email import EmailSendResult
            return EmailSendResult(ok=False, provider="fake", error="provider_down")

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        result = asyncio.run(
            handle_create_school_appointment(
                db,
                {
                    "person_id": seeded["person1_id"],
                    "track_id": seeded["track_id"],
                    "date": date_str,
                    "time": time_str,
                    "duration_minutes": 45,
                    "statut": "pending",
                    "lang": "fr",
                },
            )
        )
        monkeypatch.setattr(outbox_module, "EmailService", lambda: FailingEmailService())
        stats = asyncio.run(process_outbox_batch(db, limit=1))
        rdv = db.get(RendezVous, UUID(str(result["appointment_id"])))
        failed_event = (
            db.query(OutboxEvent)
            .filter(
                OutboxEvent.tenant_id == UUID(TENANT_ID),
                OutboxEvent.aggregate_id == str(result["appointment_id"]),
                OutboxEvent.event_type == EVENT_APPOINTMENT_NOTIFICATION_EMAIL,
            )
            .one()
        )
    finally:
        db.close()

    assert result["success"] is True
    assert stats == {"processed": 1, "sent": 0, "failed": 1}
    assert rdv is not None
    assert failed_event.status == "failed"
    assert "provider_send_failed" in str(failed_event.last_error or "")


def test_outbox_duplicate_notification_event_is_not_marked_failed(monkeypatch):
    seeded = _seed_base_data(with_agents=1)
    dedupe_key = "dup-rdv-001"
    call_counter = {"count": 0}

    async def fake_send_preferred_notification(**_kwargs):
        call_counter["count"] += 1
        if call_counter["count"] == 1:
            return {"channel": "email", "sent": True}
        return {"channel": None, "sent": False, "reason": "already_sent"}

    monkeypatch.setattr(outbox_module, "send_preferred_notification", fake_send_preferred_notification)

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        enqueue_event(
            db,
            tenant_id=TENANT_ID,
            event_type=EVENT_NOTIFICATION_PREFERRED,
            aggregate_type="appointment",
            aggregate_id="apt-1",
            payload={
                "person_id": seeded["person1_id"],
                "dedupe_key": dedupe_key,
                "email_subject": "subject",
                "email_html": "<p>body</p>",
                "email_text": "body",
                "sms_text": "body",
                "wa_text": "body",
            },
        )
        enqueue_event(
            db,
            tenant_id=TENANT_ID,
            event_type=EVENT_NOTIFICATION_PREFERRED,
            aggregate_type="appointment",
            aggregate_id="apt-1",
            payload={
                "person_id": seeded["person1_id"],
                "dedupe_key": dedupe_key,
                "email_subject": "subject",
                "email_html": "<p>body</p>",
                "email_text": "body",
                "sms_text": "body",
                "wa_text": "body",
            },
        )
        stats = asyncio.run(process_outbox_batch(db, limit=2))
        rows = (
            db.query(OutboxEvent)
            .filter(OutboxEvent.tenant_id == UUID(TENANT_ID), OutboxEvent.event_type == EVENT_NOTIFICATION_PREFERRED)
            .order_by(OutboxEvent.created_at.asc())
            .all()
        )
    finally:
        db.close()

    assert stats == {"processed": 2, "sent": 2, "failed": 0}
    assert [row.status for row in rows] == ["sent", "sent"]


def test_outbox_applicant_notification_uses_applicant_email_only(monkeypatch):
    seeded = _seed_base_data(with_agents=1)
    captured: list[dict[str, str | None]] = []

    async def fake_send_preferred_notification(**kwargs):
        captured.append(
            {
                "person_id": str(kwargs["person"].id),
                "target_email": kwargs.get("target_email"),
                "target_phone": kwargs.get("target_phone"),
                "assigned_agent_email": kwargs.get("assigned_agent_email"),
            }
        )
        return {"channel": "email", "sent": True}

    monkeypatch.setattr(outbox_module, "send_preferred_notification", fake_send_preferred_notification)

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        enqueue_event(
            db,
            tenant_id=TENANT_ID,
            event_type=EVENT_NOTIFICATION_PREFERRED,
            aggregate_type="appointment",
            aggregate_id="apt-recipient-1",
            payload={
                "recipient_scope": "applicant",
                "person_id": seeded["person1_id"],
                "applicant_person_id": seeded["person1_id"],
                "applicant_email": "abdoulaye.rdv1@example.com",
                "applicant_phone": "+221770001001",
                "assigned_agent_email": "agent1@example.com",
                "dedupe_key": "recipient-separation-1",
                "email_subject": "subject",
                "email_html": "<p>body</p>",
                "email_text": "body",
                "sms_text": "body",
                "wa_text": "body",
            },
        )
        stats = asyncio.run(process_outbox_batch(db, limit=1))
    finally:
        db.close()

    assert stats == {"processed": 1, "sent": 1, "failed": 0}
    assert captured == [
        {
            "person_id": seeded["person1_id"],
            "target_email": "abdoulaye.rdv1@example.com",
            "target_phone": "+221770001001",
            "assigned_agent_email": "agent1@example.com",
        }
    ]


def test_outbox_staff_notification_targets_assigned_agent_only(monkeypatch):
    seeded = _seed_base_data(with_agents=1)
    date_str, time_str = _future_slot(days=67, hour=12)

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        result = asyncio.run(
            handle_create_school_appointment(
                db,
                {
                    "person_id": seeded["person1_id"],
                    "track_id": seeded["track_id"],
                    "date": date_str,
                    "time": time_str,
                    "duration_minutes": 45,
                    "statut": "pending",
                    "lang": "fr",
                },
            )
        )
        staff_event = (
            db.query(OutboxEvent)
            .filter(
                OutboxEvent.tenant_id == UUID(TENANT_ID),
                OutboxEvent.aggregate_id == str(result["appointment_id"]),
                OutboxEvent.event_type == EVENT_APPOINTMENT_STAFF_NOTIFICATION,
            )
            .one()
        )
    finally:
        db.close()

    sent_to: list[str] = []

    class FakeEmailService:
        provider = "fake"

        def is_configured(self) -> bool:
            return True

        async def send_email(self, to_email: str, subject: str, html_body: str, text_body: str | None = None) -> bool:
            sent_to.append(to_email)
            return True

    monkeypatch.setattr(outbox_module, "EmailService", lambda: FakeEmailService())
    monkeypatch.setattr(outbox_module.settings, "admin_alert_email", None)
    monkeypatch.setattr(outbox_module.kb_service, "create_email_log", lambda *args, **kwargs: None)

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        processed = asyncio.run(process_staff_notification_event(db, staff_event))
    finally:
        db.close()

    assert processed is True
    assert sent_to == ["agent1@example.com"]


def test_booking_confirmation_dispatches_to_applicant_only_and_logs_resolution(monkeypatch, caplog):
    seeded = _seed_base_data(with_agents=1)
    date_str, time_str = _future_slot(days=68, hour=11)
    captured: list[str] = []

    class FakeEmailService:
        provider = "fake"

        async def send_email_result(self, to_email: str, *_args, **_kwargs):
            from app.services.email import EmailSendResult
            captured.append(to_email)
            return EmailSendResult(ok=True, provider="fake", provider_id="msg-1")

    monkeypatch.setattr(outbox_module, "EmailService", lambda: FakeEmailService())

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        result = asyncio.run(
            handle_create_school_appointment(
                db,
                {
                    "person_id": seeded["person1_id"],
                    "track_id": seeded["track_id"],
                    "date": date_str,
                    "time": time_str,
                    "duration_minutes": 45,
                    "statut": "pending",
                    "lang": "fr",
                },
            )
        )
        stats = asyncio.run(process_outbox_batch(db, limit=1))
    finally:
        db.close()

    assert result["success"] is True
    assert stats == {"processed": 1, "sent": 1, "failed": 0}
    assert captured == ["abdoulaye.rdv1@example.com"]


def test_outbox_missing_applicant_email_does_not_redirect_confirmation_to_agent(monkeypatch, caplog):
    seeded = _seed_base_data(with_agents=1)
    db = open_db_session(tenant_id=TENANT_ID)
    try:
        result = asyncio.run(
            handle_create_school_appointment(
                db,
                {
                    "person_id": seeded["person1_id"],
                    "track_id": seeded["track_id"],
                    "date": _future_slot(days=69, hour=14)[0],
                    "time": _future_slot(days=69, hour=14)[1],
                    "duration_minutes": 45,
                    "statut": "pending",
                    "lang": "fr",
                },
            )
        )
        appointment_id = str(result["appointment_id"])
        event_row = (
            db.query(OutboxEvent)
            .filter(
                OutboxEvent.tenant_id == UUID(TENANT_ID),
                OutboxEvent.aggregate_id == appointment_id,
                OutboxEvent.event_type == EVENT_APPOINTMENT_NOTIFICATION_EMAIL,
            )
            .one()
        )
        payload = json.loads(event_row.payload or "{}")
        payload["recipient"] = None
        event_row.payload = json.dumps(payload)
        person = db.get(Person, UUID(seeded["person1_id"]))
        assert person is not None
        person.email = "agent1@example.com"
        person.phone = None
        db.add(person)
        db.add(event_row)
        db.commit()
    finally:
        db.close()

    class NeverSendEmailService:
        provider = "fake"

    monkeypatch.setattr(outbox_module, "EmailService", lambda: NeverSendEmailService())

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        stats = asyncio.run(process_outbox_batch(db, limit=1))
        row = (
            db.query(OutboxEvent)
            .filter(
                OutboxEvent.tenant_id == UUID(TENANT_ID),
                OutboxEvent.aggregate_id == appointment_id,
                OutboxEvent.event_type == EVENT_APPOINTMENT_NOTIFICATION_EMAIL,
            )
            .one()
        )
        email_log = kb_service.get_email_log_by_dedupe_key(db, dedupe_key=payload["dedupe_key"])
    finally:
        db.close()

    assert stats == {"processed": 1, "sent": 1, "failed": 0}
    assert row.status == "sent"
    assert email_log is not None
    assert email_log.statut == "failed"
    assert email_log.last_error == "missing_email_recipient"
