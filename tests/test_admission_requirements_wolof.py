from __future__ import annotations

import pytest

from app.db import Base, engine, open_db_session
from app.models import SchoolAdmissionPolicy, SchoolAdmissionRequirement
from app.services.admission_requirements import seed_default_admission_rules


@pytest.fixture(scope="module", autouse=True)
def _setup_admission_tables():
    Base.metadata.drop_all(
        bind=engine,
        tables=[
            SchoolAdmissionPolicy.__table__,
            SchoolAdmissionRequirement.__table__,
        ],
        checkfirst=True,
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[
            SchoolAdmissionRequirement.__table__,
            SchoolAdmissionPolicy.__table__,
        ],
    )
    yield
    Base.metadata.drop_all(
        bind=engine,
        tables=[
            SchoolAdmissionPolicy.__table__,
            SchoolAdmissionRequirement.__table__,
        ],
        checkfirst=True,
    )


def test_seed_admission_rules_have_wolof_equivalents():
    db = open_db_session(allow_unscoped=True)
    try:
        seed_default_admission_rules(db)
        requirements = db.query(SchoolAdmissionRequirement).filter(SchoolAdmissionRequirement.is_active == True).all()
        policies = db.query(SchoolAdmissionPolicy).filter(SchoolAdmissionPolicy.is_active == True).all()
    finally:
        db.close()

    assert requirements, "no admission requirements seeded"
    assert policies, "no admission policies seeded"

    for row in requirements:
        assert (row.title_wo or "").strip(), f"missing Wolof title for requirement {row.code}"
        assert (row.details_wo or "").strip(), f"missing Wolof details for requirement {row.code}"

    for row in policies:
        assert (row.text_wo or "").strip(), f"missing Wolof text for policy {row.code}"
