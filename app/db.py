from __future__ import annotations

from typing import Generator, Optional
from uuid import UUID

from fastapi import Request
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Session, with_loader_criteria

from .config import settings
from .services.tenant_context import (
    is_fail_closed_public_path,
    is_public_tenant_path,
    require_tenant_guard,
)

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, class_=Session)

class Base(DeclarativeBase):
    pass


def configure_session_tenant(db: Session, tenant_id: Optional[str], *, allow_unscoped: bool = False) -> Session:
    db.info["allow_unscoped_tenant"] = bool(allow_unscoped)
    if tenant_id:
        db.info["tenant_id"] = str(tenant_id)
    else:
        db.info.pop("tenant_id", None)
    return db


def open_db_session(tenant_id: Optional[str] = None, *, allow_unscoped: bool = False) -> Session:
    db = SessionLocal()
    return configure_session_tenant(db, tenant_id, allow_unscoped=allow_unscoped)


def _tenant_models():
    models = []
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        if hasattr(cls, "tenant_id"):
            models.append(cls)
    return models


def get_missing_tenant_columns() -> list[str]:
    """Return existing tables that are mapped as tenant-scoped but miss `tenant_id` physically."""
    try:
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
    except Exception:
        return []

    missing: set[str] = set()
    for model in _tenant_models():
        table = getattr(model, "__table__", None)
        if table is None:
            continue
        table_name = table.name
        if table_name not in existing_tables:
            continue
        try:
            column_names = {col.get("name") for col in inspector.get_columns(table_name)}
        except Exception:
            continue
        if "tenant_id" not in column_names:
            missing.add(table_name)
    return sorted(missing)


def get_missing_required_columns(required_columns: dict[str, set[str]]) -> dict[str, list[str]]:
    """Return missing physical columns for an explicit table->columns mapping."""
    try:
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
    except Exception:
        return {}

    missing: dict[str, list[str]] = {}
    for table_name, expected_columns in required_columns.items():
        if table_name not in existing_tables:
            missing[table_name] = sorted(expected_columns)
            continue
        try:
            actual_columns = {str(col.get("name")) for col in inspector.get_columns(table_name)}
        except Exception:
            continue
        absent = sorted(str(column) for column in expected_columns if column not in actual_columns)
        if absent:
            missing[table_name] = absent
    return missing


def _tenant_column_is_uuid(tenant_column) -> bool:
    type_name = tenant_column.type.__class__.__name__.lower()
    return "uuid" in type_name


@event.listens_for(Session, "do_orm_execute")
def _tenant_scope_orm_queries(execute_state):
    tenant_id = execute_state.session.info.get("tenant_id")
    if not tenant_id:
        return
    statement = execute_state.statement
    for model in _tenant_models():
        tenant_column = getattr(getattr(model, "__table__", None), "columns", {}).get("tenant_id")
        if tenant_column is None:
            continue
        typed_tenant = tenant_id
        if _tenant_column_is_uuid(tenant_column):
            try:
                typed_tenant = UUID(str(tenant_id))
            except Exception:
                # Skip UUID-typed models if the current tenant scope cannot be represented as UUID.
                continue
        statement = statement.options(
            with_loader_criteria(
                model,
                model.tenant_id == typed_tenant,
                include_aliases=True,
            )
        )
    execute_state.statement = statement


@event.listens_for(Session, "before_flush")
def _tenant_scope_before_flush(session: Session, flush_context, instances):
    tenant_id = session.info.get("tenant_id")
    if not tenant_id:
        return

    def _normalize(value):
        if value is None:
            return None
        return str(value)

    for obj in list(session.new) + list(session.dirty):
        if not hasattr(obj, "tenant_id"):
            continue
        current = getattr(obj, "tenant_id", None)
        if current is None:
            try:
                setattr(obj, "tenant_id", UUID(str(tenant_id)))
            except Exception:
                setattr(obj, "tenant_id", str(tenant_id))
            continue
        if _normalize(current) != _normalize(tenant_id):
            raise PermissionError("cross_tenant_write_forbidden")


def get_db(request: Request) -> Generator[Session, None, None]:
    tenant_id: Optional[str] = None
    allow_unscoped = True

    if getattr(settings, "enforce_tenant_scope", True):
        tenant_from_context = getattr(getattr(request, "state", None), "tenant_id", None)
        path = getattr(getattr(request, "url", None), "path", "") or ""
        if tenant_from_context:
            tenant_id = str(tenant_from_context)
            allow_unscoped = False
        elif is_public_tenant_path(path) and not is_fail_closed_public_path(path):
            tenant_id = None
            allow_unscoped = True
        else:
            tenant_id = require_tenant_guard(request)
            allow_unscoped = False

    db = open_db_session(tenant_id=tenant_id, allow_unscoped=allow_unscoped)
    try:
        yield db
    finally:
        db.close()
