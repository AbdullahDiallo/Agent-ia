from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.llm import LLMService
from ..services import rag as rag_service
from ..security import require_role

router = APIRouter(
    tags=["rag"],
    prefix="/rag",
    dependencies=[Depends(require_role("agent|manager|admin"))],
)


class RagAnswerRequest(BaseModel):
  question: str
  top_k: int = 5


class RagAnswerResponse(BaseModel):
  answer: str
  docs_used: list[dict]


@router.post("/answer", response_model=RagAnswerResponse)
async def rag_answer(payload: RagAnswerRequest, db: Session = Depends(get_db)):
  ranked_docs = await rag_service.retrieve_documents(db, payload.question, top_k=payload.top_k)

  docs_context = "\n\n".join([
    f"[DOC {i+1}] {d.title}\n{d.content}" for i, d in enumerate(ranked_docs)
  ])

  svc = LLMService()
  answer = await svc.generate_reply(
    payload.question,
    session_state={
      "channel": "rag",
      "docs_context": docs_context,
    },
  )

  return RagAnswerResponse(
    answer=answer,
    docs_used=[
      {"id": d.id, "title": d.title, "tags": d.tags, "score": d.score} for d in ranked_docs
    ],
  )
