"""
Service d'analyse de sentiment pour les conversations
Utilise TextBlob pour l'analyse de sentiment en français et anglais
"""
import logging
from typing import Tuple, Optional
from datetime import datetime
from textblob import TextBlob
from sqlalchemy.orm import Session
from ..models import Conversation, Message

logger = logging.getLogger(__name__)


def analyze_sentiment(text: str) -> Tuple[float, str]:
    """
    Analyse le sentiment d'un texte.
    
    Args:
        text: Le texte à analyser
        
    Returns:
        Tuple (score, label) où:
        - score: float entre -1.0 (très négatif) et 1.0 (très positif)
        - label: 'positive', 'negative', ou 'neutral'
    """
    try:
        blob = TextBlob(text)
        polarity = blob.sentiment.polarity
        
        # Classifier le sentiment
        if polarity > 0.1:
            label = "positive"
        elif polarity < -0.1:
            label = "negative"
        else:
            label = "neutral"
            
        return round(polarity, 2), label
    except Exception as e:
        logger.error(f"Erreur lors de l'analyse de sentiment: {e}")
        return 0.0, "neutral"


def analyze_conversation_sentiment(db: Session, conversation_id: str) -> Optional[Tuple[float, str]]:
    """
    Analyse le sentiment d'une conversation complète en agrégeant tous les messages.
    
    Args:
        db: Session de base de données
        conversation_id: ID de la conversation
        
    Returns:
        Tuple (score, label) ou None si erreur
    """
    try:
        # Récupérer tous les messages de la conversation
        messages = db.query(Message).filter(
            Message.conversation_id == conversation_id
        ).all()
        
        if not messages:
            return None
        
        # Agréger le contenu de tous les messages
        full_text = " ".join([msg.content for msg in messages if msg.content])
        
        if not full_text.strip():
            return None
        
        # Analyser le sentiment
        score, label = analyze_sentiment(full_text)
        
        # Mettre à jour la conversation
        conversation = db.query(Conversation).filter(
            Conversation.id == conversation_id
        ).first()
        
        if conversation:
            conversation.sentiment_score = score
            conversation.sentiment_label = label
            conversation.sentiment_analyzed_at = datetime.utcnow()
            db.commit()
            
        return score, label
        
    except Exception as e:
        logger.error(f"Erreur lors de l'analyse de sentiment de la conversation {conversation_id}: {e}")
        db.rollback()
        return None


def get_conversation_sentiment_stats(db: Session) -> dict:
    """
    Récupère les statistiques de sentiment pour toutes les conversations.
    
    Returns:
        Dict avec les compteurs par label
    """
    try:
        from sqlalchemy import func
        
        stats = db.query(
            Conversation.sentiment_label,
            func.count(Conversation.id).label('count')
        ).filter(
            Conversation.sentiment_label.isnot(None)
        ).group_by(
            Conversation.sentiment_label
        ).all()
        
        result = {
            "positive": 0,
            "negative": 0,
            "neutral": 0,
            "total": 0
        }
        
        for label, count in stats:
            if label in result:
                result[label] = count
                result["total"] += count
        
        return result
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des stats de sentiment: {e}")
        return {"positive": 0, "negative": 0, "neutral": 0, "total": 0}
