from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import SchoolDepartment, SchoolProgram, SchoolTrack
from ..security import require_dev_endpoint, require_role
from ..services.admission_requirements import seed_default_admission_rules

router = APIRouter(
    prefix="/school",
    tags=["school"],
    dependencies=[Depends(require_role("agent|viewer|manager|admin"))],
)


class SchoolProgramCreate(BaseModel):
    department_id: UUID
    name: str = Field(..., min_length=2, max_length=120)
    description: Optional[str] = None
    delivery_mode: str = Field(default="onsite", pattern="^(onsite|elearning|hybrid)$")
    access_level: Optional[str] = Field(default=None, max_length=120)
    is_active: bool = True
    track_ids: list[UUID] = Field(default_factory=list)


class SchoolProgramUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    description: Optional[str] = None
    delivery_mode: Optional[str] = Field(default=None, pattern="^(onsite|elearning|hybrid)$")
    access_level: Optional[str] = Field(default=None, max_length=120)
    is_active: Optional[bool] = None
    track_ids: Optional[list[UUID]] = None


class SchoolTrackCreate(BaseModel):
    program_id: UUID
    name: str = Field(..., min_length=2, max_length=200)
    annual_fee: float = Field(..., ge=0)
    registration_fee: float = Field(..., ge=0)
    monthly_fee: float = Field(..., ge=0)
    certifications: Optional[str] = None
    options: Optional[str] = None
    is_active: bool = True


class SchoolTrackUpdate(BaseModel):
    program_id: Optional[UUID] = None
    name: Optional[str] = Field(default=None, min_length=2, max_length=200)
    annual_fee: Optional[float] = Field(default=None, ge=0)
    registration_fee: Optional[float] = Field(default=None, ge=0)
    monthly_fee: Optional[float] = Field(default=None, ge=0)
    certifications: Optional[str] = None
    options: Optional[str] = None
    is_active: Optional[bool] = None


class SchoolDepartmentCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    code: Optional[str] = Field(default=None, max_length=40)
    description: Optional[str] = None


class SchoolDepartmentUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    code: Optional[str] = Field(default=None, max_length=40)
    description: Optional[str] = None


def _track_payload(track: SchoolTrack) -> dict:
    return {
        "id": str(track.id),
        "name": track.name,
        "annual_fee": float(track.annual_fee),
        "registration_fee": float(track.registration_fee),
        "monthly_fee": float(track.monthly_fee),
        "certifications": track.certifications,
        "options": track.options,
        "is_active": track.is_active,
    }


def _program_payload(program: SchoolProgram) -> dict:
    return {
        "id": str(program.id),
        "name": program.name,
        "description": program.description,
        "delivery_mode": program.delivery_mode,
        "access_level": program.access_level,
        "is_active": program.is_active,
    }


def _department_payload(department: SchoolDepartment, programs_count: int = 0, tracks_count: int = 0) -> dict:
    return {
        "id": str(department.id),
        "name": department.name,
        "code": department.code,
        "description": department.description,
        "programs_count": programs_count,
        "tracks_count": tracks_count,
    }


def _attach_tracks_to_program(db: Session, program_id: UUID, track_ids: list[UUID]) -> int:
    unique_ids = list(dict.fromkeys(track_ids))
    if not unique_ids:
        return 0
    tracks = db.query(SchoolTrack).filter(SchoolTrack.id.in_(unique_ids)).all()
    found_ids = {track.id for track in tracks}
    missing = [str(track_id) for track_id in unique_ids if track_id not in found_ids]
    if missing:
        raise HTTPException(status_code=404, detail={"track_not_found": missing})
    for track in tracks:
        track.program_id = program_id
        db.add(track)
    return len(tracks)


@router.get("/departments")
def list_departments(
    q: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0, le=10_000),
    db: Session = Depends(get_db),
):
    query = db.query(SchoolDepartment)
    if q:
        pattern = f"%{q}%"
        query = query.filter(
            (SchoolDepartment.name.ilike(pattern))
            | (SchoolDepartment.code.ilike(pattern))
            | (SchoolDepartment.description.ilike(pattern))
        )

    total = query.count()
    departments = query.order_by(SchoolDepartment.name.asc()).offset(offset).limit(limit).all()

    result = []
    for department in departments:
        programs_count = (
            db.query(func.count(SchoolProgram.id))
            .filter(SchoolProgram.department_id == department.id)
            .scalar()
            or 0
        )
        tracks_count = (
            db.query(func.count(SchoolTrack.id))
            .join(SchoolProgram, SchoolTrack.program_id == SchoolProgram.id)
            .filter(SchoolProgram.department_id == department.id)
            .scalar()
            or 0
        )
        result.append(_department_payload(department, int(programs_count), int(tracks_count)))

    return {
        "items": result,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(result)) < total,
    }


@router.post("/departments", dependencies=[Depends(require_role("manager|admin"))])
def create_department(payload: SchoolDepartmentCreate, db: Session = Depends(get_db)):
    normalized_name = payload.name.strip()
    if not normalized_name:
        raise HTTPException(status_code=400, detail="department_name_required")
    existing = (
        db.query(SchoolDepartment)
        .filter(func.lower(SchoolDepartment.name) == normalized_name.lower())
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="department_already_exists")
    department = SchoolDepartment(
        name=normalized_name,
        code=payload.code.strip().upper() if payload.code else None,
        description=payload.description,
    )
    db.add(department)
    db.commit()
    db.refresh(department)
    return _department_payload(department, programs_count=0, tracks_count=0)


@router.put("/departments/{department_id}", dependencies=[Depends(require_role("manager|admin"))])
def update_department(department_id: UUID, payload: SchoolDepartmentUpdate, db: Session = Depends(get_db)):
    department = db.get(SchoolDepartment, department_id)
    if not department:
        raise HTTPException(status_code=404, detail="department_not_found")

    if payload.name is not None:
        normalized_name = payload.name.strip()
        if not normalized_name:
            raise HTTPException(status_code=400, detail="department_name_required")
        duplicate = (
            db.query(SchoolDepartment)
            .filter(
                func.lower(SchoolDepartment.name) == normalized_name.lower(),
                SchoolDepartment.id != department.id,
            )
            .first()
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="department_name_conflict")
        department.name = normalized_name
    if payload.code is not None:
        department.code = payload.code.strip().upper() if payload.code else None
    if payload.description is not None:
        department.description = payload.description

    db.add(department)
    db.commit()
    db.refresh(department)

    programs_count = (
        db.query(func.count(SchoolProgram.id))
        .filter(SchoolProgram.department_id == department.id)
        .scalar()
        or 0
    )
    tracks_count = (
        db.query(func.count(SchoolTrack.id))
        .join(SchoolProgram, SchoolTrack.program_id == SchoolProgram.id)
        .filter(SchoolProgram.department_id == department.id)
        .scalar()
        or 0
    )
    return _department_payload(department, int(programs_count), int(tracks_count))


@router.delete("/departments/{department_id}", dependencies=[Depends(require_role("manager|admin"))])
def delete_department(department_id: UUID, db: Session = Depends(get_db)):
    department = db.get(SchoolDepartment, department_id)
    if not department:
        raise HTTPException(status_code=404, detail="department_not_found")
    program_count = (
        db.query(func.count(SchoolProgram.id))
        .filter(SchoolProgram.department_id == department.id)
        .scalar()
        or 0
    )
    if program_count > 0:
        raise HTTPException(status_code=409, detail="department_has_programs")
    db.delete(department)
    db.commit()
    return {"deleted": True, "id": str(department_id)}


@router.get("/tracks")
def list_tracks(
    department: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    delivery_mode: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0, le=10_000),
    db: Session = Depends(get_db),
):
    query = (
        db.query(SchoolTrack, SchoolProgram, SchoolDepartment)
        .join(SchoolProgram, SchoolTrack.program_id == SchoolProgram.id)
        .join(SchoolDepartment, SchoolProgram.department_id == SchoolDepartment.id)
    )

    if department:
        query = query.filter(SchoolDepartment.name.ilike(f"%{department}%"))
    if level:
        query = query.filter(SchoolProgram.name.ilike(f"%{level}%"))
    if delivery_mode:
        query = query.filter(SchoolProgram.delivery_mode == delivery_mode)
    if q:
        pattern = f"%{q}%"
        query = query.filter(
            (SchoolTrack.name.ilike(pattern))
            | (SchoolTrack.certifications.ilike(pattern))
            | (SchoolTrack.options.ilike(pattern))
            | (SchoolProgram.name.ilike(pattern))
        )

    total = query.count()
    items = (
        query.order_by(SchoolDepartment.name.asc(), SchoolProgram.name.asc(), SchoolTrack.name.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    result = []
    for track, program, dept in items:
        payload = _track_payload(track)
        payload["program"] = _program_payload(program)
        payload["department"] = {"id": str(dept.id), "name": dept.name, "code": dept.code}
        result.append(payload)

    return {
        "items": result,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(result)) < total,
    }


@router.get("/programs")
def list_programs(
    department: Optional[str] = Query(None),
    delivery_mode: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0, le=10_000),
    db: Session = Depends(get_db),
):
    query = db.query(SchoolProgram, SchoolDepartment).join(
        SchoolDepartment, SchoolProgram.department_id == SchoolDepartment.id
    )

    if department:
        query = query.filter(SchoolDepartment.name.ilike(f"%{department}%"))
    if delivery_mode:
        query = query.filter(SchoolProgram.delivery_mode == delivery_mode)
    if q:
        pattern = f"%{q}%"
        query = query.filter(SchoolProgram.name.ilike(pattern))

    total = query.count()
    items = (
        query.order_by(SchoolDepartment.name.asc(), SchoolProgram.name.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    result = []
    for program, dept in items:
        tracks_count = (
            db.query(SchoolTrack)
            .filter(SchoolTrack.program_id == program.id, SchoolTrack.is_active == True)
            .count()
        )
        payload = _program_payload(program)
        payload["department"] = {"id": str(dept.id), "name": dept.name, "code": dept.code}
        payload["tracks_count"] = tracks_count
        result.append(payload)

    return {
        "items": result,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(result)) < total,
    }


@router.get("/programs/{program_id}")
def get_program_details(program_id: UUID, db: Session = Depends(get_db)):
    program = db.get(SchoolProgram, program_id)
    if not program:
        raise HTTPException(status_code=404, detail="program_not_found")
    department = db.get(SchoolDepartment, program.department_id)
    tracks = (
        db.query(SchoolTrack)
        .filter(SchoolTrack.program_id == program.id)
        .order_by(SchoolTrack.name.asc())
        .all()
    )
    return {
        **_program_payload(program),
        "department": {
            "id": str(department.id) if department else None,
            "name": department.name if department else None,
            "code": department.code if department else None,
        },
        "tracks_count": len(tracks),
        "tracks": [_track_payload(track) for track in tracks],
    }


@router.post("/programs", dependencies=[Depends(require_role("manager|admin"))])
def create_program(payload: SchoolProgramCreate, db: Session = Depends(get_db)):
    department = db.get(SchoolDepartment, payload.department_id)
    if not department:
        raise HTTPException(status_code=404, detail="department_not_found")
    existing = (
        db.query(SchoolProgram)
        .filter(
            SchoolProgram.department_id == payload.department_id,
            SchoolProgram.name == payload.name.strip(),
            SchoolProgram.delivery_mode == payload.delivery_mode,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="program_already_exists")
    program = SchoolProgram(
        department_id=payload.department_id,
        name=payload.name.strip(),
        description=payload.description,
        delivery_mode=payload.delivery_mode,
        access_level=payload.access_level,
        is_active=payload.is_active,
    )
    db.add(program)
    db.flush()
    attached_tracks = _attach_tracks_to_program(db, program.id, payload.track_ids)
    db.commit()
    db.refresh(program)
    return {
        **_program_payload(program),
        "department": {"id": str(department.id), "name": department.name, "code": department.code},
        "tracks_count": attached_tracks,
    }


@router.put("/programs/{program_id}", dependencies=[Depends(require_role("manager|admin"))])
def update_program(program_id: UUID, payload: SchoolProgramUpdate, db: Session = Depends(get_db)):
    program = db.get(SchoolProgram, program_id)
    if not program:
        raise HTTPException(status_code=404, detail="program_not_found")
    if payload.name is not None:
        program.name = payload.name.strip()
    if payload.description is not None:
        program.description = payload.description
    if payload.delivery_mode is not None:
        program.delivery_mode = payload.delivery_mode
    if payload.access_level is not None:
        program.access_level = payload.access_level
    if payload.is_active is not None:
        program.is_active = payload.is_active
    if payload.track_ids is not None:
        _attach_tracks_to_program(db, program.id, payload.track_ids)
    db.add(program)
    db.commit()
    db.refresh(program)
    tracks_count = (
        db.query(func.count(SchoolTrack.id))
        .filter(SchoolTrack.program_id == program.id)
        .scalar()
        or 0
    )
    return {**_program_payload(program), "tracks_count": int(tracks_count)}


@router.post("/tracks", dependencies=[Depends(require_role("manager|admin"))])
def create_track(payload: SchoolTrackCreate, db: Session = Depends(get_db)):
    program = db.get(SchoolProgram, payload.program_id)
    if not program:
        raise HTTPException(status_code=404, detail="program_not_found")
    existing = (
        db.query(SchoolTrack)
        .filter(SchoolTrack.program_id == payload.program_id, SchoolTrack.name == payload.name.strip())
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="track_already_exists")
    track = SchoolTrack(
        program_id=payload.program_id,
        name=payload.name.strip(),
        annual_fee=payload.annual_fee,
        registration_fee=payload.registration_fee,
        monthly_fee=payload.monthly_fee,
        certifications=payload.certifications,
        options=payload.options,
        is_active=payload.is_active,
    )
    db.add(track)
    db.commit()
    db.refresh(track)
    return _track_payload(track)


@router.put("/tracks/{track_id}", dependencies=[Depends(require_role("manager|admin"))])
def update_track(track_id: UUID, payload: SchoolTrackUpdate, db: Session = Depends(get_db)):
    track = db.get(SchoolTrack, track_id)
    if not track:
        raise HTTPException(status_code=404, detail="track_not_found")
    if payload.program_id is not None:
        program = db.get(SchoolProgram, payload.program_id)
        if not program:
            raise HTTPException(status_code=404, detail="program_not_found")
        track.program_id = payload.program_id
    if payload.name is not None:
        track.name = payload.name.strip()
    if payload.annual_fee is not None:
        track.annual_fee = payload.annual_fee
    if payload.registration_fee is not None:
        track.registration_fee = payload.registration_fee
    if payload.monthly_fee is not None:
        track.monthly_fee = payload.monthly_fee
    if payload.certifications is not None:
        track.certifications = payload.certifications
    if payload.options is not None:
        track.options = payload.options
    if payload.is_active is not None:
        track.is_active = payload.is_active
    db.add(track)
    db.commit()
    db.refresh(track)
    return _track_payload(track)


@router.delete("/tracks/{track_id}", dependencies=[Depends(require_role("manager|admin"))])
def delete_track(track_id: UUID, db: Session = Depends(get_db)):
    track = db.get(SchoolTrack, track_id)
    if not track:
        raise HTTPException(status_code=404, detail="track_not_found")
    db.delete(track)
    db.commit()
    return {"deleted": True, "id": str(track_id)}


@router.delete("/programs/{program_id}", dependencies=[Depends(require_role("manager|admin"))])
def delete_program(program_id: UUID, db: Session = Depends(get_db)):
    program = db.get(SchoolProgram, program_id)
    if not program:
        raise HTTPException(status_code=404, detail="program_not_found")
    track_count = db.query(SchoolTrack).filter(SchoolTrack.program_id == program_id).count()
    if track_count > 0:
        raise HTTPException(status_code=409, detail="program_has_tracks")
    db.delete(program)
    db.commit()
    return {"deleted": True, "id": str(program_id)}


@router.get("/catalog")
def get_catalog(db: Session = Depends(get_db)):
    departments = db.query(SchoolDepartment).order_by(SchoolDepartment.name.asc()).all()
    data = []
    for dept in departments:
        programs = (
            db.query(SchoolProgram)
            .filter(SchoolProgram.department_id == dept.id, SchoolProgram.is_active == True)
            .order_by(SchoolProgram.name.asc())
            .all()
        )

        serialized_programs = []
        for program in programs:
            tracks = (
                db.query(SchoolTrack)
                .filter(SchoolTrack.program_id == program.id, SchoolTrack.is_active == True)
                .order_by(SchoolTrack.name.asc())
                .all()
            )
            serialized_programs.append(
                {
                    **_program_payload(program),
                    "tracks": [_track_payload(track) for track in tracks],
                }
            )

        data.append(
            {
                "id": str(dept.id),
                "name": dept.name,
                "code": dept.code,
                "description": dept.description,
                "programs": serialized_programs,
            }
        )
    return {"departments": data}


def _upsert_department(db: Session, name: str, code: str, description: str) -> SchoolDepartment:
    dept = db.query(SchoolDepartment).filter(SchoolDepartment.name == name).first()
    if not dept:
        dept = SchoolDepartment(name=name, code=code, description=description)
    else:
        dept.code = code
        dept.description = description
    db.add(dept)
    db.flush()
    return dept


def _upsert_program(
    db: Session,
    department_id,
    name: str,
    delivery_mode: str = "onsite",
    access_level: Optional[str] = None,
    description: Optional[str] = None,
) -> SchoolProgram:
    query = db.query(SchoolProgram).filter(
        SchoolProgram.department_id == department_id,
        SchoolProgram.name == name,
        SchoolProgram.delivery_mode == delivery_mode,
    )
    if access_level:
        query = query.filter(SchoolProgram.access_level == access_level)
    else:
        query = query.filter(SchoolProgram.access_level.is_(None))
    program = query.first()

    if not program:
        program = SchoolProgram(
            department_id=department_id,
            name=name,
            delivery_mode=delivery_mode,
            access_level=access_level,
            description=description,
            is_active=True,
        )
    else:
        program.access_level = access_level
        program.description = description
        program.is_active = True

    db.add(program)
    db.flush()
    return program


def _upsert_track(
    db: Session,
    program_id,
    name: str,
    annual_fee: int,
    registration_fee: int,
    monthly_fee: int,
    certifications: Optional[str] = None,
    options: Optional[str] = None,
):
    track = (
        db.query(SchoolTrack)
        .filter(SchoolTrack.program_id == program_id, SchoolTrack.name == name)
        .first()
    )
    if not track:
        track = SchoolTrack(
            program_id=program_id,
            name=name,
            annual_fee=annual_fee,
            registration_fee=registration_fee,
            monthly_fee=monthly_fee,
            certifications=certifications,
            options=options,
        )
    else:
        track.annual_fee = annual_fee
        track.registration_fee = registration_fee
        track.monthly_fee = monthly_fee
        track.certifications = certifications
        track.options = options
        track.is_active = True
    db.add(track)


@router.post("/catalog/seed", dependencies=[Depends(require_dev_endpoint)])
def seed_catalog(db: Session = Depends(get_db)):
    dept_rs = _upsert_department(
        db,
        name="Departement Reseaux et Systemes",
        code="DRS",
        description="Formations reseaux, telecoms, cybersecurite et systemes.",
    )
    dept_gi = _upsert_department(
        db,
        name="Departement Genie Informatique",
        code="DGI",
        description="Formations genie logiciel, data et applications.",
    )

    prog_rs_l12 = _upsert_program(db, dept_rs.id, "Licence (L1, L2)")
    prog_rs_l3 = _upsert_program(db, dept_rs.id, "Licence Professionnelle (L3)")
    prog_rs_master = _upsert_program(db, dept_rs.id, "Master Professionnel")
    prog_rs_l3_el = _upsert_program(db, dept_rs.id, "Licence Professionnelle", delivery_mode="elearning")
    prog_rs_master_el = _upsert_program(db, dept_rs.id, "Master Professionnel", delivery_mode="elearning")

    _upsert_track(db, prog_rs_l12.id, "Reseaux Informatiques", 890000, 250000, 80000, "CISCO CCNA 1 & 2")
    _upsert_track(db, prog_rs_l12.id, "Reseaux Telecommunications", 890000, 250000, 80000, "CISCO CCNA 1 & 2")
    _upsert_track(db, prog_rs_l12.id, "Systemes Embarques & IoT", 890000, 250000, 80000, "CISCO CCNA 1 & 2")
    _upsert_track(db, prog_rs_l12.id, "Cyber Securite", 890000, 250000, 80000, "CISCO CCNA 1 & 2")

    _upsert_track(db, prog_rs_l3.id, "Reseaux Informatiques", 1150000, 250000, 100000, "CCNA, DEVNET, HUAWEI, AWS")
    _upsert_track(db, prog_rs_l3.id, "Reseaux Telecommunications", 1150000, 250000, 100000, "CCNA, DEVNET, HUAWEI, AWS")
    _upsert_track(db, prog_rs_l3.id, "Systemes Embarques & IoT", 1150000, 250000, 100000, "CCNA, DEVNET, HUAWEI, AWS")
    _upsert_track(db, prog_rs_l3.id, "Cyber Securite", 1150000, 250000, 100000, "CCNA, DEVNET, HUAWEI, AWS")

    _upsert_track(db, prog_rs_master.id, "Reseaux et Systemes Informatiques", 1330000, 250000, 120000, "CCNP, ENARSI, ENCOR, HUAWEI, AWS")
    _upsert_track(db, prog_rs_master.id, "Reseaux Telecommunications", 1330000, 250000, 120000, "CCNP, ENARSI, ENCOR, HUAWEI, AWS")
    _upsert_track(db, prog_rs_master.id, "Virtualisation et Cloud Computing", 1330000, 250000, 120000, "CCNP, ENARSI, ENCOR, HUAWEI, AWS")
    _upsert_track(
        db,
        prog_rs_master.id,
        "Securite des Systemes d Information et Monetique (SSIM)",
        1330000,
        250000,
        120000,
        "CCNP, ENARSI, ENCOR, HUAWEI, AWS",
        options="SSI, MTS",
    )

    _upsert_track(db, prog_rs_l3_el.id, "Reseaux Informatiques", 1330000, 250000, 100000)
    _upsert_track(db, prog_rs_l3_el.id, "Genie Logiciel", 1330000, 250000, 100000)
    _upsert_track(db, prog_rs_l3_el.id, "Finance et Comptabilite", 1330000, 250000, 100000)
    _upsert_track(db, prog_rs_master_el.id, "Reseaux et Systemes Informatiques", 1330000, 250000, 100000)
    _upsert_track(db, prog_rs_master_el.id, "Genie Logiciel", 1330000, 250000, 100000)
    _upsert_track(db, prog_rs_master_el.id, "Finance", 1330000, 250000, 100000)

    prog_gi_l12 = _upsert_program(db, dept_gi.id, "Licence (L1, L2)")
    prog_gi_l3 = _upsert_program(db, dept_gi.id, "Licence Professionnelle (L3)")
    prog_gi_gl = _upsert_program(db, dept_gi.id, "Genie Logiciel")
    prog_gi_diti = _upsert_program(
        db,
        dept_gi.id,
        "Diplome d Ingenieur en Techniques Informatiques (DITI) - BAC +5",
        access_level="Bac +2, BTS ou Licence",
    )
    prog_gi_master = _upsert_program(db, dept_gi.id, "Master Professionnel")
    prog_gi_dsai = _upsert_program(db, dept_gi.id, "Data Science & Intelligence Artificielle")

    _upsert_track(db, prog_gi_l12.id, "Infographie / Multimedia", 890000, 250000, 80000)
    _upsert_track(db, prog_gi_l12.id, "Geomatique et Developpement d Applications", 890000, 250000, 80000)
    _upsert_track(db, prog_gi_l12.id, "Informatique Appliquee a la Gestion des Entreprises", 890000, 250000, 80000)
    _upsert_track(db, prog_gi_l12.id, "Marketing Digital", 890000, 250000, 80000)

    _upsert_track(db, prog_gi_l3.id, "Multimedia", 1150000, 250000, 100000)
    _upsert_track(db, prog_gi_l3.id, "Geomatique et Developpement d Applications", 1150000, 250000, 100000)
    _upsert_track(db, prog_gi_l3.id, "Informatique Appliquee a la Gestion des Entreprises", 1150000, 250000, 100000)
    _upsert_track(db, prog_gi_l3.id, "Genie Logiciel", 1150000, 250000, 100000)

    _upsert_track(db, prog_gi_gl.id, "Data Science & Big Data Technology", 1050000, 250000, 100000, certifications="ORACLE")
    _upsert_track(
        db,
        prog_gi_diti.id,
        "Diplome d Ingenieur en Techniques Informatiques",
        1150000,
        250000,
        100000,
        certifications="CCNA, CCNP, DEVNET, ORACLE",
    )
    _upsert_track(db, prog_gi_master.id, "Genie Logiciel", 1330000, 250000, 120000)
    _upsert_track(
        db,
        prog_gi_master.id,
        "Informatique Appliquee a la Gestion des Entreprises",
        1330000,
        250000,
        120000,
        options="Developpement et Management des ERP, Big Data Management & Analytics",
    )
    _upsert_track(
        db,
        prog_gi_dsai.id,
        "Data Science & Intelligence Artificielle",
        1600000,
        250000,
        150000,
        certifications="HUAWEI, AWS, CISCO",
    )

    db.commit()
    admission_seed = seed_default_admission_rules(db)

    total_departments = db.query(SchoolDepartment).count()
    total_programs = db.query(SchoolProgram).count()
    total_tracks = db.query(SchoolTrack).count()
    return {
        "seeded": True,
        "departments": total_departments,
        "programs": total_programs,
        "tracks": total_tracks,
        "admission": admission_seed,
    }
