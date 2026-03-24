from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from uuid import UUID

from ..db import get_db
from ..models import Document
from ..security import require_role
from ..services import docs as docs_service

router = APIRouter(
    prefix="/persona",
    tags=["persona"],
    dependencies=[Depends(require_role("admin"))],
)


class PersonaUpdate(BaseModel):
    content: str
    title: Optional[str] = None


class PersonaDocPayload(BaseModel):
    title: str
    content: str
    tags: Optional[str] = None


def _serialize_doc(doc: Optional[Document]):
    if not doc:
        return None
    return {
        "id": str(doc.id),
        "title": doc.title,
        "content": doc.content,
        "tags": doc.tags,
        "created_at": doc.created_at,
    }


def _ensure_tag(existing: Optional[str], tag: str) -> str:
    if not existing:
        return tag
    tags = [t.strip() for t in existing.split(",") if t.strip()]
    if tag not in tags:
        tags.append(tag)
    return ",".join(tags)


def _upsert_by_tag(db: Session, tag: str, payload: PersonaUpdate) -> Document:
    doc = docs_service.get_document_by_tag(db, tag)
    if doc:
        doc.content = payload.content
        if payload.title:
            doc.title = payload.title
        doc.tags = _ensure_tag(doc.tags, tag)
    else:
        title = payload.title or tag.replace("_", " ").title()
        doc = docs_service.create_document(db, title=title, content=payload.content, tags=tag)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def _serialize_docs(docs: list[Document]) -> list[dict]:
    return [
        {
            "id": str(doc.id),
            "title": doc.title,
            "content": doc.content,
            "tags": doc.tags,
            "created_at": doc.created_at,
        }
        for doc in docs
    ]


@router.get("/config")
def get_persona_config(db: Session = Depends(get_db)):
    core_doc = docs_service.get_document_by_tag(db, "persona_core")
    args_doc = docs_service.get_document_by_tag(db, "persona_arguments")
    return {
        "persona_core": _serialize_doc(core_doc),
        "arguments": _serialize_doc(args_doc),
    }


@router.get("/docs")
def list_persona_docs(
    tag: str = Query("persona", min_length=2),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=10_000),
    db: Session = Depends(get_db),
):
    pattern = f"%{tag}%"
    query = db.query(Document).filter(Document.tags.ilike(pattern))
    total = query.count()
    items = query.order_by(Document.created_at.desc()).offset(offset).limit(limit).all()
    return {
        "items": _serialize_docs(items),
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(items)) < total,
    }


@router.post("/docs", dependencies=[Depends(require_role("admin"))])
def create_persona_doc(payload: PersonaDocPayload, db: Session = Depends(get_db)):
    tags = _ensure_tag(payload.tags, "persona")
    doc = docs_service.create_document(db, title=payload.title, content=payload.content, tags=tags)
    return _serialize_doc(doc)


@router.put("/docs/{doc_id}", dependencies=[Depends(require_role("admin"))])
def update_persona_doc(doc_id: UUID, payload: PersonaDocPayload, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="persona_doc_not_found")
    tags = _ensure_tag(payload.tags or doc.tags, "persona")
    doc.title = payload.title
    doc.content = payload.content
    doc.tags = tags
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return _serialize_doc(doc)


@router.delete("/docs/{doc_id}", dependencies=[Depends(require_role("admin"))])
def delete_persona_doc(doc_id: UUID, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="persona_doc_not_found")
    if not (doc.tags or "") or "persona" not in doc.tags.lower():
        raise HTTPException(status_code=400, detail="not_a_persona_document")
    db.delete(doc)
    db.commit()
    return {"deleted": True, "id": str(doc.id)}


@router.post("/core", dependencies=[Depends(require_role("admin"))])
def update_persona_core(payload: PersonaUpdate, db: Session = Depends(get_db)):
    doc = _upsert_by_tag(db, "persona_core", payload)
    return {
        "persona_core": _serialize_doc(doc),
        "arguments": _serialize_doc(docs_service.get_document_by_tag(db, "persona_arguments")),
    }


@router.post("/arguments", dependencies=[Depends(require_role("admin"))])
def update_persona_arguments(payload: PersonaUpdate, db: Session = Depends(get_db)):
    doc = _upsert_by_tag(db, "persona_arguments", payload)
    return {
        "persona_core": _serialize_doc(docs_service.get_document_by_tag(db, "persona_core")),
        "arguments": _serialize_doc(doc),
    }
