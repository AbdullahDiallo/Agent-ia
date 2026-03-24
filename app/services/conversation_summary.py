"""
Service de génération de résumés automatiques des conversations
"""
import logging
from typing import Optional
from datetime import datetime
from sqlalchemy.orm import Session
from ..models import Conversation, Message

logger = logging.getLogger(__name__)


def generate_conversation_summary(db: Session, conversation_id: str) -> Optional[str]:
    """
    Génère un résumé automatique d'une conversation.
    Pour l'instant, utilise une approche simple basée sur l'extraction des points clés.
    Peut être amélioré avec un modèle LLM plus tard.
    
    Args:
        db: Session de base de données
        conversation_id: ID de la conversation
        
    Returns:
        Résumé de la conversation ou None si erreur
    """
    try:
        # Récupérer la conversation et ses messages
        conversation = db.query(Conversation).filter(
            Conversation.id == conversation_id
        ).first()
        
        if not conversation:
            return None
        
        messages = db.query(Message).filter(
            Message.conversation_id == conversation_id
        ).order_by(Message.created_at).all()
        
        if not messages:
            return "Aucun message dans cette conversation."
        
        # Extraire les informations clés
        total_messages = len(messages)
        user_messages = [m for m in messages if m.role == "user"]
        assistant_messages = [m for m in messages if m.role == "assistant"]
        
        # Construire le résumé
        summary_parts = []
        
        # En-tête
        summary_parts.append(f"Conversation de {total_messages} messages")
        
        if conversation.canal:
            summary_parts.append(f"via {conversation.canal}")
        
        # Intention si disponible
        if conversation.intention:
            summary_parts.append(f"\nIntention: {conversation.intention}")
        
        # Sentiment si disponible
        if conversation.sentiment_label:
            summary_parts.append(f"\nSentiment: {conversation.sentiment_label}")
            if conversation.sentiment_score:
                summary_parts.append(f"(score: {conversation.sentiment_score})")
        
        # Premiers échanges (max 2)
        if user_messages:
            summary_parts.append("\n\nPremiers échanges:")
            for i, msg in enumerate(user_messages[:2]):
                content_preview = msg.content[:100] + "..." if len(msg.content) > 100 else msg.content
                summary_parts.append(f"\n- Personne: {content_preview}")
                
                # Réponse de l'assistant si disponible
                if i < len(assistant_messages):
                    resp_preview = assistant_messages[i].content[:100] + "..." if len(assistant_messages[i].content) > 100 else assistant_messages[i].content
                    summary_parts.append(f"\n- Agent: {resp_preview}")
        
        # Statut
        if conversation.status:
            summary_parts.append(f"\n\nStatut: {conversation.status}")
        
        summary = " ".join(summary_parts)
        
        # Mettre à jour la conversation
        conversation.resume = summary
        db.commit()
        
        return summary
        
    except Exception as e:
        logger.error(f"Erreur lors de la génération du résumé de la conversation {conversation_id}: {e}")
        db.rollback()
        return None


def update_all_conversation_summaries(db: Session) -> int:
    """
    Met à jour les résumés de toutes les conversations qui n'en ont pas.
    
    Returns:
        Nombre de conversations mises à jour
    """
    try:
        # Récupérer les conversations sans résumé
        conversations = db.query(Conversation).filter(
            Conversation.resume.is_(None)
        ).all()
        
        count = 0
        for conv in conversations:
            if generate_conversation_summary(db, str(conv.id)):
                count += 1
        
        return count
        
    except Exception as e:
        logger.error(f"Erreur lors de la mise à jour des résumés: {e}")
        return 0
