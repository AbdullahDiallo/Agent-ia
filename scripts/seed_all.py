from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.db import open_db_session
from app.models import EmailTemplate, Person, PersonRole, SchoolDepartment, SchoolProgram, SchoolTrack, Tenant
from app.services import kb as kb_service
from app.services.internal_calendar import create_calendar, create_event

try:
    from scripts.seed_auth import main as seed_auth_main
except Exception:
    seed_auth_main = None


def _get_or_create_school_catalog(db):
    department = db.query(SchoolDepartment).filter(SchoolDepartment.name == "Informatique").first()
    if not department:
        department = SchoolDepartment(name="Informatique", code="INFO", description="Departement informatique")
        db.add(department)
        db.commit()
        db.refresh(department)

    program = db.query(SchoolProgram).filter(
        SchoolProgram.department_id == department.id,
        SchoolProgram.name == "Licence Professionnelle",
    ).first()
    if not program:
        program = SchoolProgram(
            department_id=department.id,
            name="Licence Professionnelle",
            description="Programme professionnalisant",
            delivery_mode="onsite",
            access_level="Bac +2",
            is_active=True,
        )
        db.add(program)
        db.commit()
        db.refresh(program)

    track = db.query(SchoolTrack).filter(
        SchoolTrack.program_id == program.id,
        SchoolTrack.name == "Genie Logiciel",
    ).first()
    if not track:
        track = SchoolTrack(
            program_id=program.id,
            name="Genie Logiciel",
            annual_fee=950000,
            registration_fee=100000,
            monthly_fee=85000,
            certifications="Python, DevOps",
            options="Cloud, IA",
            is_active=True,
        )
        db.add(track)
        db.commit()
        db.refresh(track)

    return department, program, track


def _get_or_create_person(db):
    person = db.query(Person).filter(Person.email == "alice.school@example.com").first()
    if not person:
        person = Person(
            first_name="Alice",
            last_name="Ndiaye",
            email="alice.school@example.com",
            phone="+221770000111",
            preferred_language="fr",
            status="active",
            notes="Seed school candidate",
        )
        db.add(person)
        db.commit()
        db.refresh(person)

    role = db.query(PersonRole).filter(
        PersonRole.person_id == person.id,
        PersonRole.role == "candidate",
    ).first()
    if not role:
        db.add(PersonRole(person_id=person.id, role="candidate"))
        db.commit()

    return person


def _upsert_template(db):
    tpl = db.query(EmailTemplate).filter(EmailTemplate.name == "rdv_confirmation").first()
    if tpl:
        return tpl
    tpl = EmailTemplate(
        name="rdv_confirmation",
        subject_template="Confirmation rendez-vous admission - {{ event.title }}",
        html_template=(
            "<p>Bonjour {{ person_name or 'candidat' }},</p>"
            "<p>Votre rendez-vous admission est confirme le {{ event.start_at }}.</p>"
        ),
        text_template="Rendez-vous admission confirme: {{ event.start_at }}",
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


def _ensure_default_tenant(db):
    tenant_uuid = UUID(str(settings.default_tenant_id))
    tenant = db.get(Tenant, tenant_uuid)
    if tenant:
        return tenant
    tenant = Tenant(id=tenant_uuid, slug="default", name="Default Tenant", is_active=True)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


def main() -> None:
    db = open_db_session(str(settings.default_tenant_id))
    try:
        if seed_auth_main:
            seed_auth_main()

        _ensure_default_tenant(db)
        _, _, track = _get_or_create_school_catalog(db)
        person = _get_or_create_person(db)

        conversation = kb_service.create_conversation(
            db,
            person_id=str(person.id),
            resume="Demande informations admission",
            canal="chat",
            intention="admission",
        )

        now = datetime.now(timezone.utc)
        calendar = create_calendar(db, name="Admissions", owner="admin", timezone="Africa/Dakar")
        event = create_event(
            db,
            calendar_id=str(calendar.id),
            title="Entretien admission",
            start_at=now + timedelta(days=1, hours=10),
            end_at=now + timedelta(days=1, hours=11),
            resource_key="admission:desk",
            attendees=person.email,
            description="Entretien orientation",
            status="confirmed",
        )

        rdv = kb_service.create_rendezvous(
            db,
            person_id=person.id,
            track_id=track.id,
            start_at=event.start_at,
            end_at=event.end_at,
            agent="Admission Desk",
            statut="confirmed",
            event_id=str(event.id),
        )

        _upsert_template(db)

        kb_service.create_email_log(
            db,
            person_id=str(person.id),
            sujet="Confirmation entretien admission",
            statut="sent",
            provider_id="seed",
        )
        kb_service.create_sms_log(
            db,
            person_id=str(person.id),
            contenu="Votre entretien admission est confirme.",
            statut="sent",
            provider_id="seed",
        )

        print(
            {
                "seed": "ok",
                "person_id": str(person.id),
                "track_id": str(track.id),
                "conversation_id": str(conversation.id),
                "rendezvous_id": str(rdv.id),
            }
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
