from __future__ import annotations

import pytest

from sqlalchemy import Column, Integer, String

from app.db import Base, engine, open_db_session

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


class TenantScopedProbe(Base):
    __tablename__ = "test_tenant_scope_probe"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(64), nullable=False)
    value = Column(String(120), nullable=False)


@pytest.fixture(scope="module", autouse=True)
def _setup_probe_table():
    Base.metadata.drop_all(bind=engine, tables=[TenantScopedProbe.__table__], checkfirst=True)
    Base.metadata.create_all(bind=engine, tables=[TenantScopedProbe.__table__])
    yield
    Base.metadata.drop_all(bind=engine, tables=[TenantScopedProbe.__table__])


def _seed_probe_rows() -> None:
    db = open_db_session(allow_unscoped=True)
    try:
        db.query(TenantScopedProbe).delete()
        db.add(TenantScopedProbe(tenant_id=TENANT_A, value="alpha"))
        db.add(TenantScopedProbe(tenant_id=TENANT_B, value="bravo"))
        db.commit()
    finally:
        db.close()


def test_cross_tenant_read_isolation():
    _seed_probe_rows()

    db_a = open_db_session(TENANT_A)
    try:
        values_a = [row.value for row in db_a.query(TenantScopedProbe).order_by(TenantScopedProbe.id.asc()).all()]
        assert values_a == ["alpha"]
    finally:
        db_a.close()

    db_b = open_db_session(TENANT_B)
    try:
        values_b = [row.value for row in db_b.query(TenantScopedProbe).order_by(TenantScopedProbe.id.asc()).all()]
        assert values_b == ["bravo"]
    finally:
        db_b.close()


def test_cross_tenant_write_denied():
    db = open_db_session(TENANT_A)
    try:
        db.add(TenantScopedProbe(tenant_id=TENANT_B, value="forbidden"))
        with pytest.raises(PermissionError):
            db.commit()
        db.rollback()
    finally:
        db.close()
