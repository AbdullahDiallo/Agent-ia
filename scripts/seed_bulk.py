from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.db import open_db_session
from app.models import Person, PersonRole, SchoolDepartment, SchoolProgram, SchoolTrack, Tenant
from app.services import kb as kb_service
from uuid import UUID

FIRST_NAMES = ["Awa", "Moussa", "Fatou", "Ibrahima", "Mariama", "Omar", "Astou", "Cheikh"]
LAST_NAMES = ["Diop", "Ndiaye", "Fall", "Sow", "Ba", "Diallo", "Gueye", "Seck"]
TRACK_NAMES = ["Genie Logiciel", "Data Science", "Cybersecurite", "Marketing Digital"]


def _ensure_catalog(db, target_tracks: int) -> list[SchoolTrack]:
    department = db.query(SchoolDepartment).filter(SchoolDepartment.name == "Informatique").first()
    if not department:
        department = SchoolDepartment(name="Informatique", code="INFO", description="Departement informatique")
        db.add(department)
        db.commit()
        db.refresh(department)

    program = db.query(SchoolProgram).filter(
        SchoolProgram.department_id == department.id,
        SchoolProgram.name == "Cycle Professionnel",
    ).first()
    if not program:
        program = SchoolProgram(
            department_id=department.id,
            name="Cycle Professionnel",
            description="Parcours professionnalisants",
            delivery_mode="hybrid",
            access_level="Bac",
            is_active=True,
        )
        db.add(program)
        db.commit()
        db.refresh(program)

    existing = db.query(SchoolTrack).filter(SchoolTrack.program_id == program.id).all()
    existing_names = {track.name for track in existing}
    idx = 0
    while len(existing) < target_tracks:
        name = TRACK_NAMES[idx % len(TRACK_NAMES)]
        idx += 1
        if name in existing_names:
            name = f"{name} {idx}"
        track = SchoolTrack(
            program_id=program.id,
            name=name,
            annual_fee=850000 + idx * 25000,
            registration_fee=100000,
            monthly_fee=80000 + idx * 3000,
            certifications="Certification interne",
            options="Projet fil rouge",
            is_active=True,
        )
        db.add(track)
        db.commit()
        db.refresh(track)
        existing.append(track)
        existing_names.add(track.name)

    return existing


def _ensure_persons(db, target_persons: int) -> list[Person]:
    persons = db.query(Person).all()
    counter = len(persons)
    while len(persons) < target_persons:
        counter += 1
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        email = f"seed.person.{counter}@example.com"
        person = Person(
            first_name=first,
            last_name=last,
            email=email,
            phone=f"+22177{counter:06d}"[:12],
            preferred_language=random.choice(["fr", "en", "wo"]),
            status="active",
            notes="Seed bulk",
        )
        db.add(person)
        db.commit()
        db.refresh(person)
        persons.append(person)

        db.add(PersonRole(person_id=person.id, role="candidate"))
        db.commit()

    return persons


def _ensure_default_tenant(db) -> Tenant:
    tenant_uuid = UUID(str(settings.default_tenant_id))
    tenant = db.get(Tenant, tenant_uuid)
    if tenant:
        return tenant
    tenant = Tenant(id=tenant_uuid, slug="default", name="Default Tenant", is_active=True)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


def _ensure_conversations(db, persons: list[Person], target: int) -> int:
    current = db.query(Person).count()
    created = 0
    for idx in range(target):
        person = persons[idx % len(persons)]
        kb_service.create_conversation(
            db,
            person_id=str(person.id),
            resume=f"Conversation seed {idx + 1}",
            canal=random.choice(["chat", "whatsapp", "email", "call"]),
            intention=random.choice(["admission", "documents_requis", "tarifs", "prise_rdv"]),
        )
        created += 1
    return current + created


def _ensure_rendezvous(db, persons: list[Person], tracks: list[SchoolTrack], target: int) -> int:
    bulk_base = datetime(2030, 1, 1, 8, 0, tzinfo=timezone.utc)
    for idx in range(target):
        person = persons[idx % len(persons)]
        track = tracks[idx % len(tracks)]
        person_slot = idx // max(1, len(persons))
        start_at = bulk_base + timedelta(
            days=person_slot,
            hours=(person_slot % 6),
            minutes=(person_slot // 20) * 90,
        )
        end_at = start_at + timedelta(hours=1)
        kb_service.create_rendezvous(
            db,
            person_id=person.id,
            track_id=track.id,
            start_at=start_at,
            end_at=end_at,
            agent="Admission Desk",
            statut=random.choice(["created", "confirmed", "reminder_sent"]),
        )
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed bulk school data")
    parser.add_argument("--persons", type=int, default=20)
    parser.add_argument("--tracks", type=int, default=8)
    parser.add_argument("--conversations", type=int, default=25)
    parser.add_argument("--rendezvous", type=int, default=25)
    args = parser.parse_args()

    db = open_db_session(str(settings.default_tenant_id))
    try:
        _ensure_default_tenant(db)
        tracks = _ensure_catalog(db, target_tracks=max(1, args.tracks))
        persons = _ensure_persons(db, target_persons=max(1, args.persons))
        total_conversations = _ensure_conversations(db, persons, target=max(0, args.conversations))
        total_rendezvous = _ensure_rendezvous(db, persons, tracks, target=max(0, args.rendezvous))

        print(
            {
                "seed_bulk": "ok",
                "persons": len(persons),
                "tracks": len(tracks),
                "conversations_created": total_conversations,
                "rendezvous_created": total_rendezvous,
            }
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
