from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.db import Base, engine, open_db_session
from app.models import (
    Agent,
    BillingPlan,
    Calendar,
    EmailLog,
    Event,
    OutboxEvent,
    Person,
    RendezVous,
    SchoolDepartment,
    SchoolProgram,
    SchoolTrack,
    SMSLog,
    Tenant,
    User,
)
from app.routers.school_people import (
    SchoolAppointmentCreate,
    SchoolAppointmentUpdate,
    create_school_appointment,
    delete_school_appointment,
    get_school_appointment,
    list_school_appointments,
    update_school_appointment,
)
from app.security import Principal


TENANT_ID = "00000000-0000-0000-0000-00000000f501".replace("f", "0")


def _ensure_tables() -> None:
    Base.metadata.create_all(
        bind=engine,
        tables=[
            BillingPlan.__table__,
            Tenant.__table__,
            User.__table__,
            Agent.__table__,
            Person.__table__,
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


def _seed_base_data() -> dict[str, str]:
    _ensure_tables()
    db = open_db_session(tenant_id=TENANT_ID)
    try:
        tenant_uuid = UUID(TENANT_ID)
        if not db.get(Tenant, tenant_uuid):
            db.add(Tenant(id=tenant_uuid, slug="tenant-school-rdv", name="Tenant School RDV", is_active=True))
            db.flush()

        for model in (OutboxEvent, EmailLog, SMSLog, Event, RendezVous, SchoolTrack, SchoolProgram, SchoolDepartment, Agent, User, Person):
            db.query(model).filter(model.tenant_id == tenant_uuid).delete()
        db.commit()

        dept = SchoolDepartment(tenant_id=tenant_uuid, name="Admissions", code="ADM", description="dept")
        db.add(dept)
        db.flush()
        program = SchoolProgram(
            tenant_id=tenant_uuid,
            department_id=dept.id,
            name="Bachelor",
            description="program",
            delivery_mode="onsite",
            access_level="Bac",
            is_active=True,
        )
        db.add(program)
        db.flush()
        track = SchoolTrack(
            tenant_id=tenant_uuid,
            program_id=program.id,
            name="Data Science",
            annual_fee=1000000,
            registration_fee=150000,
            monthly_fee=90000,
            certifications="",
            options="",
            is_active=True,
        )
        db.add(track)
        db.flush()

        person_1 = Person(
            tenant_id=tenant_uuid,
            first_name="Aminata",
            last_name="Fall",
            email="aminata@example.com",
            phone="+221770010001",
            preferred_language="fr",
        )
        person_2 = Person(
            tenant_id=tenant_uuid,
            first_name="Cheikh",
            last_name="Ndiaye",
            email="cheikh@example.com",
            phone="+221770010002",
            preferred_language="fr",
        )
        person_3 = Person(
            tenant_id=tenant_uuid,
            first_name="Mame",
            last_name="Ba",
            email=None,
            phone="+221770010003",
            preferred_language="fr",
        )
        db.add_all([person_1, person_2, person_3])
        db.flush()

        agents: list[Agent] = []
        for idx in range(2):
            user = User(
                id=2500 + idx,
                tenant_id=tenant_uuid,
                first_name=f"Agent{idx + 1}",
                last_name="School",
                email=f"agent{idx + 1}@example.com",
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
            agents.append(agent)

        db.commit()
        return {
            "person_1_id": str(person_1.id),
            "person_2_id": str(person_2.id),
            "person_3_id": str(person_3.id),
            "track_id": str(track.id),
            "agent_1_id": str(agents[0].id),
            "agent_2_id": str(agents[1].id),
            "agent_1_user_id": str(agents[0].user_id),
            "agent_2_user_id": str(agents[1].user_id),
        }
    finally:
        db.close()


def _future_window(days: int, hour: int) -> tuple[datetime, datetime]:
    start = datetime.now(timezone.utc).replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=days)
    end = start + timedelta(minutes=45)
    return start, end


def test_manual_create_syncs_event_and_enqueues_notifications():
    seeded = _seed_base_data()
    start_at, end_at = _future_window(20, 10)

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        result = asyncio.run(
            create_school_appointment(
                SchoolAppointmentCreate(
                    person_id=UUID(seeded["person_1_id"]),
                    track_id=UUID(seeded["track_id"]),
                    agent_id=UUID(seeded["agent_1_id"]),
                    start_at=start_at,
                    end_at=end_at,
                    statut="created",
                ),
                db=db,
            )
        )
        rdv = db.get(RendezVous, UUID(result["id"]))
        event = db.query(Event).filter(Event.rendezvous_id == rdv.id).one()
        outbox_events = (
            db.query(OutboxEvent)
            .filter(OutboxEvent.aggregate_id == result["id"])
            .order_by(OutboxEvent.created_at.asc())
            .all()
        )
    finally:
        db.close()

    assert result["agent_id"] == seeded["agent_1_id"]
    assert result["notifications"] == {
        "email": {"status": "pending"},
        "sms": {"status": "pending"},
    }
    assert rdv is not None
    assert event is not None
    assert event.status == "confirmed"
    assert {row.event_type for row in outbox_events} == {
        "appointment.notification.email",
        "appointment.notification.sms",
    }


def test_manual_update_rejects_overlapping_slot_for_same_agent():
    seeded = _seed_base_data()
    start_at, end_at = _future_window(21, 11)
    overlap_start = start_at + timedelta(minutes=15)
    overlap_end = overlap_start + timedelta(minutes=45)

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        first = asyncio.run(
            create_school_appointment(
                SchoolAppointmentCreate(
                    person_id=UUID(seeded["person_1_id"]),
                    track_id=UUID(seeded["track_id"]),
                    agent_id=UUID(seeded["agent_1_id"]),
                    start_at=start_at,
                    end_at=end_at,
                    statut="created",
                ),
                db=db,
            )
        )
        second = asyncio.run(
            create_school_appointment(
                SchoolAppointmentCreate(
                    person_id=UUID(seeded["person_2_id"]),
                    track_id=UUID(seeded["track_id"]),
                    agent_id=UUID(seeded["agent_2_id"]),
                    start_at=start_at + timedelta(hours=2),
                    end_at=end_at + timedelta(hours=2),
                    statut="created",
                ),
                db=db,
            )
        )

        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                update_school_appointment(
                    UUID(second["id"]),
                    SchoolAppointmentUpdate(
                        start_at=overlap_start,
                        end_at=overlap_end,
                        agent_id=UUID(seeded["agent_1_id"]),
                    ),
                    db=db,
                )
            )
    finally:
        db.close()

    assert first["id"] != second["id"]
    assert exc.value.status_code == 409


def test_manual_delete_soft_deletes_and_cancels_internal_event():
    seeded = _seed_base_data()
    start_at, end_at = _future_window(22, 14)

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        created = asyncio.run(
            create_school_appointment(
                SchoolAppointmentCreate(
                    person_id=UUID(seeded["person_1_id"]),
                    track_id=UUID(seeded["track_id"]),
                    agent_id=UUID(seeded["agent_1_id"]),
                    start_at=start_at,
                    end_at=end_at,
                    statut="created",
                ),
                db=db,
            )
        )
        deleted = asyncio.run(delete_school_appointment(UUID(created["id"]), db=db))
        rdv = db.get(RendezVous, UUID(created["id"]))
        event = db.query(Event).filter(Event.rendezvous_id == rdv.id).one()
    finally:
        db.close()

    assert deleted["deleted"] is True
    assert rdv.deleted_at is not None
    assert rdv.statut == "cancelled"
    assert event.status == "cancelled"
    assert deleted["notifications"]["email"]["status"] == "pending"


def test_manual_create_marks_email_failed_when_candidate_has_no_email():
    seeded = _seed_base_data()
    start_at, end_at = _future_window(23, 9)

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        result = asyncio.run(
            create_school_appointment(
                SchoolAppointmentCreate(
                    person_id=UUID(seeded["person_3_id"]),
                    track_id=UUID(seeded["track_id"]),
                    agent_id=UUID(seeded["agent_1_id"]),
                    start_at=start_at,
                    end_at=end_at,
                    statut="created",
                ),
                db=db,
            )
        )
        email_logs = db.query(EmailLog).order_by(EmailLog.created_at.desc()).all()
    finally:
        db.close()

    assert result["notifications"]["email"] == {"status": "failed", "reason": "missing_email_recipient"}
    assert result["notifications"]["sms"] == {"status": "pending"}
    assert email_logs[0].statut == "failed"
    assert email_logs[0].last_error == "missing_email_recipient"


def test_manual_rbac_limits_agent_visibility_to_own_appointments():
    seeded = _seed_base_data()
    first_start, first_end = _future_window(24, 10)
    second_start, second_end = _future_window(24, 12)

    db = open_db_session(tenant_id=TENANT_ID)
    try:
        first = asyncio.run(
            create_school_appointment(
                SchoolAppointmentCreate(
                    person_id=UUID(seeded["person_1_id"]),
                    track_id=UUID(seeded["track_id"]),
                    agent_id=UUID(seeded["agent_1_id"]),
                    start_at=first_start,
                    end_at=first_end,
                    statut="created",
                ),
                db=db,
            )
        )
        second = asyncio.run(
            create_school_appointment(
                SchoolAppointmentCreate(
                    person_id=UUID(seeded["person_2_id"]),
                    track_id=UUID(seeded["track_id"]),
                    agent_id=UUID(seeded["agent_2_id"]),
                    start_at=second_start,
                    end_at=second_end,
                    statut="created",
                ),
                db=db,
            )
        )

        agent_principal = Principal(sub=seeded["agent_1_user_id"], roles=["agent"], tenant_id=TENANT_ID)
        admin_principal = Principal(sub="1", roles=["admin"], tenant_id=TENANT_ID)

        agent_view = list_school_appointments(
            person_id=None,
            status=None,
            include_deleted=False,
            limit=100,
            offset=0,
            db=db,
            principal=agent_principal,
        )
        admin_view = list_school_appointments(
            person_id=None,
            status=None,
            include_deleted=False,
            limit=100,
            offset=0,
            db=db,
            principal=admin_principal,
        )
        own_rdv = get_school_appointment(UUID(first["id"]), db=db, principal=agent_principal)
        with pytest.raises(HTTPException) as exc:
            get_school_appointment(UUID(second["id"]), db=db, principal=agent_principal)
    finally:
        db.close()

    assert [item["id"] for item in agent_view["items"]] == [first["id"]]
    assert len(admin_view["items"]) == 2
    assert own_rdv["id"] == first["id"]
    assert exc.value.status_code == 403
