"""
Endpoints pour les fonctionnalités IA de l'agent
- Analyse de sentiment
- Résumés de conversations
"""
import logging
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..security import get_principal
from ..services.sentiment import (
    analyze_conversation_sentiment,
    get_conversation_sentiment_stats,
)
from ..services.conversation_summary import (
    generate_conversation_summary,
    update_all_conversation_summaries,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ai", tags=["ai"])


class SentimentAnalysisResponse(BaseModel):
    conversation_id: str
    sentiment_score: float
    sentiment_label: str
    success: bool


@router.post("/sentiment/analyze/{conversation_id}", response_model=SentimentAnalysisResponse)
def analyze_sentiment(
    conversation_id: str,
    db: Session = Depends(get_db),
    _principal=Depends(get_principal),
):
    """Analyse le sentiment d'une conversation spécifique."""
    try:
        conversation_id = str(UUID(conversation_id))
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_conversation_id")

    result = analyze_conversation_sentiment(db, conversation_id)
    if not result:
        raise HTTPException(status_code=404, detail="Conversation not found or no messages")

    score, label = result
    return SentimentAnalysisResponse(
        conversation_id=conversation_id,
        sentiment_score=score,
        sentiment_label=label,
        success=True,
    )


@router.get("/sentiment/stats")
def get_sentiment_stats(db: Session = Depends(get_db), _principal=Depends(get_principal)):
    """Récupère les statistiques de sentiment pour toutes les conversations."""
    return get_conversation_sentiment_stats(db)


class ConversationSummaryResponse(BaseModel):
    conversation_id: str
    summary: str
    success: bool


@router.post("/conversations/summarize/{conversation_id}", response_model=ConversationSummaryResponse)
def summarize_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    _principal=Depends(get_principal),
):
    """Génère un résumé automatique d'une conversation."""
    try:
        conversation_id = str(UUID(conversation_id))
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_conversation_id")

    summary = generate_conversation_summary(db, conversation_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Conversation not found or no messages")

    return ConversationSummaryResponse(
        conversation_id=conversation_id,
        summary=summary,
        success=True,
    )


@router.post("/conversations/summarize-all")
def summarize_all_conversations(db: Session = Depends(get_db), _principal=Depends(get_principal)):
    """Génère des résumés pour toutes les conversations qui n'en ont pas."""
    count = update_all_conversation_summaries(db)
    return {
        "success": True,
        "conversations_updated": count,
        "message": f"{count} conversations résumées",
    }
