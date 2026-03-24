from __future__ import annotations

import uuid

import pytest

from app.db import Base, engine, open_db_session
from app.models import DEFAULT_TENANT_UUID, SchoolDepartment, SchoolProgram, SchoolTrack, BillingPlan, Tenant
from app.services.llm_tools import handle_get_track_tuition


@pytest.fixture(scope="module", autouse=True)
def _setup_track_tables():
    Base.metadata.create_all(
        bind=engine,
        tables=[
            BillingPlan.__table__, Tenant.__table__,
            SchoolDepartment.__table__,
            SchoolProgram.__table__,
            SchoolTrack.__table__,
        ],
        checkfirst=True,
    )
    yield


def _seed_catalog() -> tuple[str, str]:
    db = open_db_session(allow_unscoped=True)
    suffix = uuid.uuid4().hex[:8]
    try:
        tenant = db.get(Tenant, DEFAULT_TENANT_UUID)
        if tenant is None:
            tenant = Tenant(
                id=DEFAULT_TENANT_UUID,
                slug="default",
                name="Default Tenant",
                is_active=True,
            )
            db.add(tenant)
            db.flush()

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

        track_main = SchoolTrack(
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
        track_secondary = SchoolTrack(
            tenant_id=DEFAULT_TENANT_UUID,
            program_id=program.id,
            name=f"Data Science {suffix}",
            annual_fee=980000,
            registration_fee=120000,
            monthly_fee=90000,
            certifications="ML, BI",
            options="MLOps",
            is_active=True,
        )
        db.add(track_main)
        db.add(track_secondary)
        db.commit()
        return track_main.name, track_secondary.name
    finally:
        db.close()


def test_get_track_tuition_matches_accented_query():
    track_name, _ = _seed_catalog()
    query = track_name.replace("Genie", "Génie")
    db = open_db_session(allow_unscoped=True)
    try:
        result = handle_get_track_tuition(db, {"query": query})
    finally:
        db.close()

    assert result.get("success") is True
    items = result.get("items") or []
    assert any(track_name == item.get("track_name") for item in items)


def test_get_track_tuition_lists_catalog_for_generic_program_query():
    _seed_catalog()
    db = open_db_session(allow_unscoped=True)
    try:
        result = handle_get_track_tuition(db, {"query": "quels sont vos programmes disponibles"})
    finally:
        db.close()

    assert result.get("success") is True
    items = result.get("items") or []
    assert len(items) > 0
    assert all(str(item.get("track_name") or "").strip() for item in items)


def test_get_track_tuition_lists_catalog_for_generic_wolof_query():
    _seed_catalog()
    db = open_db_session(allow_unscoped=True)
    try:
        result = handle_get_track_tuition(db, {"query": "yan program yi am?"})
    finally:
        db.close()

    assert result.get("success") is True
    items = result.get("items") or []
    assert len(items) > 0
    assert all(str(item.get("track_name") or "").strip() for item in items)
