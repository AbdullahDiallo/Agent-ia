from __future__ import annotations

import uuid

import pytest

from app.db import Base, engine, open_db_session
from app.models import (
    BillingPlan,
    DEFAULT_TENANT_UUID,
    Document,
    SchoolAdmissionPolicy,
    SchoolAdmissionRequirement,
    SchoolDepartment,
    SchoolProgram,
    SchoolTrack,
    Tenant,
)
from app.services.admission_requirements import seed_default_admission_rules
from app.services.docs import create_document
from app.services.knowledge_resolver import resolve_knowledge_context


@pytest.fixture(scope="module", autouse=True)
def _setup_knowledge_tables():
    Base.metadata.create_all(
        bind=engine,
        tables=[
            BillingPlan.__table__, Tenant.__table__,
            Document.__table__,
            SchoolDepartment.__table__,
            SchoolProgram.__table__,
            SchoolTrack.__table__,
            SchoolAdmissionRequirement.__table__,
            SchoolAdmissionPolicy.__table__,
        ],
        checkfirst=True,
    )
    yield


def _ensure_default_tenant(db) -> None:
    tenant = db.get(Tenant, DEFAULT_TENANT_UUID)
    if tenant is None:
        db.add(
            Tenant(
                id=DEFAULT_TENANT_UUID,
                slug="default",
                name="Default Tenant",
                is_active=True,
            )
        )
        db.commit()


def _seed_catalog(db, suffix: str) -> str:
    department = SchoolDepartment(
        tenant_id=DEFAULT_TENANT_UUID,
        name=f"Informatique {suffix}",
        code=f"INFO-{suffix}",
        description="Departement test",
    )
    db.add(department)
    db.flush()

    program = SchoolProgram(
        tenant_id=DEFAULT_TENANT_UUID,
        department_id=department.id,
        name=f"Licence Professionnelle {suffix}",
        description="Programme test",
        delivery_mode="onsite",
        access_level="Bac +2",
        is_active=True,
    )
    db.add(program)
    db.flush()

    track = SchoolTrack(
        tenant_id=DEFAULT_TENANT_UUID,
        program_id=program.id,
        name=f"Genie Logiciel {suffix}",
        annual_fee=950000,
        registration_fee=100000,
        monthly_fee=85000,
        certifications="Python, DevOps",
        options="Cloud, IA",
        is_active=True,
    )
    db.add(track)
    db.commit()
    return track.name


def test_resolver_prefers_structured_catalog_facts_over_faq_docs():
    suffix = uuid.uuid4().hex[:8]
    db = open_db_session(allow_unscoped=True)
    try:
        _ensure_default_tenant(db)
        track_name = _seed_catalog(db, suffix)
        create_document(
            db,
            title=f"Ancien tarif {suffix}",
            content=f"Ancien montant pour {track_name}: 999999 F CFA.",
            tags="faq,lang:fr",
        )

        context = resolve_knowledge_context(
            db,
            user_text=f"Quels sont les frais de {track_name} ?",
            session_state={"lang_detected": "fr"},
        )
    finally:
        db.close()

    authoritative_text = "\n".join(item.content for item in context.authoritative_facts)
    faq_text = "\n".join(item.content for item in context.faq_snippets)

    assert "catalog" in context.critical_domains
    assert "950000" in authoritative_text
    assert "999999" not in authoritative_text
    assert "999999" in faq_text


def test_resolver_uses_structured_requirements_and_deadline_policies():
    db = open_db_session(allow_unscoped=True)
    try:
        _ensure_default_tenant(db)
        seed_default_admission_rules(db)
        context = resolve_knowledge_context(
            db,
            user_text="Quels documents faut-il fournir et quelles sont les dates limites ?",
            session_state={"lang_detected": "fr"},
        )
    finally:
        db.close()

    authoritative_text = "\n".join(item.content for item in context.authoritative_facts)

    assert "requirements" in context.critical_domains
    assert "calendar" in context.critical_domains
    assert "Photos d'identite" in authoritative_text
    assert "05" in authoritative_text


def test_resolver_prefers_language_specific_faq_and_longform_support():
    suffix = uuid.uuid4().hex[:8]
    db = open_db_session(allow_unscoped=True)
    try:
        _ensure_default_tenant(db)
        create_document(
            db,
            title=f"Application process overview {suffix}",
            content=f"English FAQ answer {suffix} with application process details.",
            tags="faq,lang:en",
        )
        create_document(
            db,
            title=f"Processus admission {suffix}",
            content=f"Reponse FAQ francaise {suffix}.",
            tags="faq,lang:fr",
        )
        create_document(
            db,
            title=f"Admissions handbook {suffix}",
            content=f"Long-form English handbook {suffix} for the application process and orientation flow.",
            tags="retrieval,longform,lang:en",
        )

        context = resolve_knowledge_context(
            db,
            user_text=f"Explain the application process and orientation flow {suffix}",
            session_state={"lang_detected": "en"},
        )
    finally:
        db.close()

    assert context.faq_snippets
    assert "English FAQ answer" in context.faq_snippets[0].content
    assert context.retrieval_snippets
    assert "Long-form English handbook" in context.retrieval_snippets[0].content
