from __future__ import annotations

from sqlalchemy import UniqueConstraint

from app.models import (
    EmailTemplate,
    SchoolAdmissionPolicy,
    SchoolAdmissionRequirement,
    SchoolDepartment,
    User,
)


def _unique_constraint_names(model) -> set[str]:
    return {
        constraint.name
        for constraint in model.__table__.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.name
    }


def test_saas_tenant_scoped_unique_constraints_present():
    assert "uq_email_templates_tenant_name" in _unique_constraint_names(EmailTemplate)
    assert "uq_school_departments_tenant_name" in _unique_constraint_names(SchoolDepartment)
    assert "uq_school_admission_requirements_tenant_code" in _unique_constraint_names(SchoolAdmissionRequirement)
    assert "uq_school_admission_policies_tenant_code" in _unique_constraint_names(SchoolAdmissionPolicy)
    assert "uq_users_tenant_email" in _unique_constraint_names(User)
