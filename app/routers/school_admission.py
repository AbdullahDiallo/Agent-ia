from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import SchoolAdmissionPolicy, SchoolAdmissionRequirement
from ..security import require_dev_endpoint, require_role
from ..services.admission_requirements import (
    format_requirements_for_channel,
    seed_default_admission_rules,
)

router = APIRouter(
    prefix="/school/admission",
    tags=["school-admission"],
    dependencies=[Depends(require_role("agent|viewer|manager|admin"))],
)


class AdmissionRequirementCreate(BaseModel):
    code: str = Field(..., min_length=2, max_length=80)
    title_fr: str = Field(..., min_length=2, max_length=255)
    title_en: Optional[str] = Field(default=None, max_length=255)
    title_wo: Optional[str] = Field(default=None, max_length=255)
    details_fr: Optional[str] = None
    details_en: Optional[str] = None
    details_wo: Optional[str] = None
    sort_order: int = Field(default=0, ge=0)
    is_required: bool = True
    is_active: bool = True


class AdmissionRequirementUpdate(BaseModel):
    code: Optional[str] = Field(default=None, min_length=2, max_length=80)
    title_fr: Optional[str] = Field(default=None, min_length=2, max_length=255)
    title_en: Optional[str] = Field(default=None, max_length=255)
    title_wo: Optional[str] = Field(default=None, max_length=255)
    details_fr: Optional[str] = None
    details_en: Optional[str] = None
    details_wo: Optional[str] = None
    sort_order: Optional[int] = Field(default=None, ge=0)
    is_required: Optional[bool] = None
    is_active: Optional[bool] = None


class AdmissionPolicyCreate(BaseModel):
    code: str = Field(..., min_length=2, max_length=80)
    text_fr: str = Field(..., min_length=2)
    text_en: Optional[str] = None
    text_wo: Optional[str] = None
    sort_order: int = Field(default=0, ge=0)
    is_active: bool = True


class AdmissionPolicyUpdate(BaseModel):
    code: Optional[str] = Field(default=None, min_length=2, max_length=80)
    text_fr: Optional[str] = Field(default=None, min_length=2)
    text_en: Optional[str] = None
    text_wo: Optional[str] = None
    sort_order: Optional[int] = Field(default=None, ge=0)
    is_active: Optional[bool] = None


def _serialize_requirement(row: SchoolAdmissionRequirement) -> dict:
    return {
        "id": str(row.id),
        "code": row.code,
        "title_fr": row.title_fr,
        "title_en": row.title_en,
        "title_wo": row.title_wo,
        "details_fr": row.details_fr,
        "details_en": row.details_en,
        "details_wo": row.details_wo,
        "sort_order": row.sort_order,
        "is_required": row.is_required,
        "is_active": row.is_active,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _serialize_policy(row: SchoolAdmissionPolicy) -> dict:
    return {
        "id": str(row.id),
        "code": row.code,
        "text_fr": row.text_fr,
        "text_en": row.text_en,
        "text_wo": row.text_wo,
        "sort_order": row.sort_order,
        "is_active": row.is_active,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.get("/requirements")
def list_requirements(
    q: Optional[str] = Query(None),
    active_only: bool = Query(True),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=10_000),
    db: Session = Depends(get_db),
):
    query = db.query(SchoolAdmissionRequirement)
    if active_only:
        query = query.filter(SchoolAdmissionRequirement.is_active == True)
    if q:
        pattern = f"%{q}%"
        query = query.filter(
            (SchoolAdmissionRequirement.code.ilike(pattern))
            | (SchoolAdmissionRequirement.title_fr.ilike(pattern))
            | (SchoolAdmissionRequirement.title_en.ilike(pattern))
            | (SchoolAdmissionRequirement.title_wo.ilike(pattern))
            | (SchoolAdmissionRequirement.details_fr.ilike(pattern))
        )
    total = query.count()
    rows = (
        query.order_by(SchoolAdmissionRequirement.sort_order.asc(), SchoolAdmissionRequirement.created_at.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "items": [_serialize_requirement(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(rows)) < total,
    }


@router.get("/policies")
def list_policies(
    q: Optional[str] = Query(None),
    active_only: bool = Query(True),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=10_000),
    db: Session = Depends(get_db),
):
    query = db.query(SchoolAdmissionPolicy)
    if active_only:
        query = query.filter(SchoolAdmissionPolicy.is_active == True)
    if q:
        pattern = f"%{q}%"
        query = query.filter(
            (SchoolAdmissionPolicy.code.ilike(pattern))
            | (SchoolAdmissionPolicy.text_fr.ilike(pattern))
            | (SchoolAdmissionPolicy.text_en.ilike(pattern))
            | (SchoolAdmissionPolicy.text_wo.ilike(pattern))
        )
    total = query.count()
    rows = (
        query.order_by(SchoolAdmissionPolicy.sort_order.asc(), SchoolAdmissionPolicy.created_at.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "items": [_serialize_policy(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(rows)) < total,
    }


@router.get("/requirements/text")
def admission_requirements_text(
    lang: str = Query("fr", pattern="^(fr|en|wo)$"),
    with_policies: bool = Query(True),
    channel: str = Query("chat", pattern="^(chat|email|whatsapp|sms)$"),
    db: Session = Depends(get_db),
):
    bullet_prefix = "- "
    if channel == "whatsapp":
        bullet_prefix = "• "
    if channel == "sms":
        bullet_prefix = "- "
    text = format_requirements_for_channel(
        db,
        lang=lang,
        with_policies=with_policies,
        bullet_prefix=bullet_prefix,
    )
    return {
        "lang": lang,
        "channel": channel,
        "with_policies": with_policies,
        "text": text,
    }


@router.post("/seed", dependencies=[Depends(require_dev_endpoint)])
def seed_admission_rules(db: Session = Depends(get_db)):
    seeded = seed_default_admission_rules(db)
    total_requirements = db.query(func.count(SchoolAdmissionRequirement.id)).scalar() or 0
    total_policies = db.query(func.count(SchoolAdmissionPolicy.id)).scalar() or 0
    return {
        "seeded": True,
        "updated": seeded,
        "totals": {
            "requirements": int(total_requirements),
            "policies": int(total_policies),
        },
    }


@router.post("/requirements", dependencies=[Depends(require_role("manager|admin"))])
def create_requirement(payload: AdmissionRequirementCreate, db: Session = Depends(get_db)):
    code = payload.code.strip().lower()
    existing = db.query(SchoolAdmissionRequirement).filter(SchoolAdmissionRequirement.code == code).first()
    if existing:
        raise HTTPException(status_code=409, detail="requirement_code_exists")
    row = SchoolAdmissionRequirement(
        code=code,
        title_fr=payload.title_fr.strip(),
        title_en=payload.title_en.strip() if payload.title_en else None,
        title_wo=payload.title_wo.strip() if payload.title_wo else None,
        details_fr=payload.details_fr,
        details_en=payload.details_en,
        details_wo=payload.details_wo,
        sort_order=payload.sort_order,
        is_required=payload.is_required,
        is_active=payload.is_active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _serialize_requirement(row)


@router.put("/requirements/{requirement_id}", dependencies=[Depends(require_role("manager|admin"))])
def update_requirement(requirement_id: UUID, payload: AdmissionRequirementUpdate, db: Session = Depends(get_db)):
    row = db.get(SchoolAdmissionRequirement, requirement_id)
    if not row:
        raise HTTPException(status_code=404, detail="requirement_not_found")
    if payload.code is not None:
        code = payload.code.strip().lower()
        duplicate = (
            db.query(SchoolAdmissionRequirement)
            .filter(SchoolAdmissionRequirement.code == code, SchoolAdmissionRequirement.id != row.id)
            .first()
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="requirement_code_exists")
        row.code = code
    if payload.title_fr is not None:
        row.title_fr = payload.title_fr.strip()
    if payload.title_en is not None:
        row.title_en = payload.title_en.strip() if payload.title_en else None
    if payload.title_wo is not None:
        row.title_wo = payload.title_wo.strip() if payload.title_wo else None
    if payload.details_fr is not None:
        row.details_fr = payload.details_fr
    if payload.details_en is not None:
        row.details_en = payload.details_en
    if payload.details_wo is not None:
        row.details_wo = payload.details_wo
    if payload.sort_order is not None:
        row.sort_order = payload.sort_order
    if payload.is_required is not None:
        row.is_required = payload.is_required
    if payload.is_active is not None:
        row.is_active = payload.is_active
    db.add(row)
    db.commit()
    db.refresh(row)
    return _serialize_requirement(row)


@router.delete("/requirements/{requirement_id}", dependencies=[Depends(require_role("manager|admin"))])
def delete_requirement(requirement_id: UUID, db: Session = Depends(get_db)):
    row = db.get(SchoolAdmissionRequirement, requirement_id)
    if not row:
        raise HTTPException(status_code=404, detail="requirement_not_found")
    db.delete(row)
    db.commit()
    return {"deleted": True, "id": str(requirement_id)}


@router.post("/policies", dependencies=[Depends(require_role("manager|admin"))])
def create_policy(payload: AdmissionPolicyCreate, db: Session = Depends(get_db)):
    code = payload.code.strip().lower()
    existing = db.query(SchoolAdmissionPolicy).filter(SchoolAdmissionPolicy.code == code).first()
    if existing:
        raise HTTPException(status_code=409, detail="policy_code_exists")
    row = SchoolAdmissionPolicy(
        code=code,
        text_fr=payload.text_fr.strip(),
        text_en=payload.text_en,
        text_wo=payload.text_wo,
        sort_order=payload.sort_order,
        is_active=payload.is_active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _serialize_policy(row)


@router.put("/policies/{policy_id}", dependencies=[Depends(require_role("manager|admin"))])
def update_policy(policy_id: UUID, payload: AdmissionPolicyUpdate, db: Session = Depends(get_db)):
    row = db.get(SchoolAdmissionPolicy, policy_id)
    if not row:
        raise HTTPException(status_code=404, detail="policy_not_found")
    if payload.code is not None:
        code = payload.code.strip().lower()
        duplicate = (
            db.query(SchoolAdmissionPolicy)
            .filter(SchoolAdmissionPolicy.code == code, SchoolAdmissionPolicy.id != row.id)
            .first()
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="policy_code_exists")
        row.code = code
    if payload.text_fr is not None:
        row.text_fr = payload.text_fr.strip()
    if payload.text_en is not None:
        row.text_en = payload.text_en
    if payload.text_wo is not None:
        row.text_wo = payload.text_wo
    if payload.sort_order is not None:
        row.sort_order = payload.sort_order
    if payload.is_active is not None:
        row.is_active = payload.is_active
    db.add(row)
    db.commit()
    db.refresh(row)
    return _serialize_policy(row)


@router.delete("/policies/{policy_id}", dependencies=[Depends(require_role("manager|admin"))])
def delete_policy(policy_id: UUID, db: Session = Depends(get_db)):
    row = db.get(SchoolAdmissionPolicy, policy_id)
    if not row:
        raise HTTPException(status_code=404, detail="policy_not_found")
    db.delete(row)
    db.commit()
    return {"deleted": True, "id": str(policy_id)}
