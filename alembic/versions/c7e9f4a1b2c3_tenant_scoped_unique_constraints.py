"""tenant-scoped unique constraints for SaaS readiness

Revision ID: c7e9f4a1b2c3
Revises: b1f6e2c4d9ab
Create Date: 2026-02-06 22:30:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c7e9f4a1b2c3"
down_revision = "b1f6e2c4d9ab"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _unique_exists(table_name: str, constraint_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    names = {row.get("name") for row in _inspector().get_unique_constraints(table_name)}
    return constraint_name in names


def _drop_unique_if_exists(table_name: str, constraint_name: str) -> None:
    if _unique_exists(table_name, constraint_name):
        op.drop_constraint(constraint_name, table_name, type_="unique")


def _create_unique_if_missing(table_name: str, constraint_name: str, columns: list[str]) -> None:
    if _table_exists(table_name) and not _unique_exists(table_name, constraint_name):
        op.create_unique_constraint(constraint_name, table_name, columns)


def upgrade() -> None:
    # Drop legacy/global unique constraints when present.
    _drop_unique_if_exists("email_templates", "email_templates_name_key")
    _drop_unique_if_exists("school_departments", "school_departments_name_key")
    _drop_unique_if_exists("school_admission_requirements", "school_admission_requirements_code_key")
    _drop_unique_if_exists("school_admission_policies", "school_admission_policies_code_key")
    _drop_unique_if_exists("users", "users_email_key")

    # Ensure tenant-scoped uniqueness for SaaS multi-tenant model.
    _create_unique_if_missing(
        "email_templates",
        "uq_email_templates_tenant_name",
        ["tenant_id", "name"],
    )
    _create_unique_if_missing(
        "school_departments",
        "uq_school_departments_tenant_name",
        ["tenant_id", "name"],
    )
    _create_unique_if_missing(
        "school_admission_requirements",
        "uq_school_admission_requirements_tenant_code",
        ["tenant_id", "code"],
    )
    _create_unique_if_missing(
        "school_admission_policies",
        "uq_school_admission_policies_tenant_code",
        ["tenant_id", "code"],
    )
    _create_unique_if_missing(
        "users",
        "uq_users_tenant_email",
        ["tenant_id", "email"],
    )


def downgrade() -> None:
    _drop_unique_if_exists("email_templates", "uq_email_templates_tenant_name")
    _drop_unique_if_exists("school_departments", "uq_school_departments_tenant_name")
    _drop_unique_if_exists("school_admission_requirements", "uq_school_admission_requirements_tenant_code")
    _drop_unique_if_exists("school_admission_policies", "uq_school_admission_policies_tenant_code")
    _drop_unique_if_exists("users", "uq_users_tenant_email")

    _create_unique_if_missing("email_templates", "email_templates_name_key", ["name"])
    _create_unique_if_missing("school_departments", "school_departments_name_key", ["name"])
    _create_unique_if_missing("school_admission_requirements", "school_admission_requirements_code_key", ["code"])
    _create_unique_if_missing("school_admission_policies", "school_admission_policies_code_key", ["code"])
    _create_unique_if_missing("users", "users_email_key", ["email"])
