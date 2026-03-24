"""
Router pour la gestion manuelle des conversations
Permet aux agents humains de prendre le contrôle et d'intervenir
"""
from fastapi import APIRouter, Depends, HTTPException, Form
from sqlalchemy.orm import Session
from typing import Optional
from uuid import UUID
from datetime import datetime
from ..db import get_db
from ..security import require_role, get_principal, Principal
from ..models import Conversation, Message
from ..services import kb as kb_service
from ..services.email import EmailService
from ..logger import get_logger
from ..utils.http_errors import public_error_detail

router = APIRouter(tags=["manual-intervention"])
logger = get_logger(__name__)


@router.post("/conversations/{conversation_id}/take-control")
async def take_control(
    conversation_id: UUID,
    user_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
    _=Depends(require_role("agent|manager|admin"))
):
    """
    Prendre le contrôle manuel d'une conversation.
    Passe la conversation en mode 'manual' et assigne l'agent.
    """
    conv = db.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation non trouvée")
    
    # Mettre à jour la conversation
    acting_user_id = int(principal.sub)
    conv.mode = "manual"
    conv.assigned_to = acting_user_id
    conv.last_human_interaction = datetime.utcnow()
    conv.status = "active"
    
    db.commit()
    db.refresh(conv)
    
    logger.info(
        f"Prise de contrôle de la conversation {conversation_id}",
        extra={"extra_fields": {"user_id": acting_user_id, "conversation_id": str(conversation_id)}}
    )
    
    return {
        "success": True,
        "message": "Contrôle pris avec succès",
        "conversation": {
            "id": str(conv.id),
            "mode": conv.mode,
            "assigned_to": conv.assigned_to,
            "status": conv.status
        }
    }


@router.post("/conversations/{conversation_id}/release-control")
async def release_control(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    _=Depends(require_role("agent|manager|admin"))
):
    """
    Relâcher le contrôle manuel d'une conversation.
    Repasse la conversation en mode 'auto'.
    """
    conv = db.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation non trouvée")
    
    # Mettre à jour la conversation
    conv.mode = "auto"
    conv.assigned_to = None
    
    db.commit()
    db.refresh(conv)
    
    logger.info(
        f"Relâchement du contrôle de la conversation {conversation_id}",
        extra={"extra_fields": {"conversation_id": str(conversation_id)}}
    )
    
    return {
        "success": True,
        "message": "Contrôle relâché, l'IA reprend la main",
        "conversation": {
            "id": str(conv.id),
            "mode": conv.mode,
            "assigned_to": conv.assigned_to
        }
    }


@router.post("/conversations/{conversation_id}/close")
async def close_conversation(
    conversation_id: UUID,
    reason: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    _=Depends(require_role("agent|manager|admin"))
):
    """
    Fermer une conversation.
    """
    conv = db.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation non trouvée")
    
    conv.status = "closed"
    
    # Ajouter un message système si une raison est fournie
    if reason:
        kb_service.create_message(
            db,
            conversation_id=str(conv.id),
            role="system",
            canal=conv.canal,
            content=f"Conversation fermée. Raison: {reason}"
        )
    
    db.commit()
    db.refresh(conv)
    
    logger.info(
        f"Fermeture de la conversation {conversation_id}",
        extra={"extra_fields": {"conversation_id": str(conversation_id), "reason": reason}}
    )
    
    return {
        "success": True,
        "message": "Conversation fermée",
        "conversation": {
            "id": str(conv.id),
            "status": conv.status
        }
    }


@router.post("/conversations/{conversation_id}/reopen")
async def reopen_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    _=Depends(require_role("agent|manager|admin"))
):
    """
    Réouvrir une conversation fermée.
    """
    conv = db.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation non trouvée")
    
    conv.status = "active"
    
    # Ajouter un message système
    kb_service.create_message(
        db,
        conversation_id=str(conv.id),
        role="system",
        canal=conv.canal,
        content="Conversation réouverte par un agent"
    )
    
    db.commit()
    db.refresh(conv)
    
    logger.info(
        f"Réouverture de la conversation {conversation_id}",
        extra={"extra_fields": {"conversation_id": str(conversation_id)}}
    )
    
    return {
        "success": True,
        "message": "Conversation réouverte",
        "conversation": {
            "id": str(conv.id),
            "status": conv.status
        }
    }


@router.post("/email/send-manual")
async def send_manual_email(
    to_email: str = Form(...),
    subject: str = Form(...),
    html_body: str = Form(...),
    text_body: Optional[str] = Form(None),
    conversation_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
    _=Depends(require_role("agent|manager|admin"))
):
    """
    Envoyer un email manuellement.
    L'agent peut composer et envoyer un email personnalisé.
    """
    try:
        email_service = EmailService()
        
        # Envoyer l'email
        sent = await email_service.send_email(
            to_email=to_email,
            subject=subject,
            html_body=html_body,
            text_body=text_body or html_body
        )
        
        if not sent:
            raise HTTPException(status_code=500, detail="Échec de l'envoi de l'email")
        
        # Logger le message dans la conversation si fournie
        if conversation_id:
            conv = db.get(Conversation, UUID(conversation_id))
            if conv:
                conv.last_human_interaction = datetime.utcnow()
                
                kb_service.create_message(
                    db,
                    conversation_id=conversation_id,
                    role="assistant",
                    canal="email",
                    content=f"Sujet: {subject}\n\n{text_body or html_body}"
                )
                
                db.commit()
        
        logger.info(
            f"Email manuel envoyé",
            extra={"extra_fields": {
                "to_email": to_email,
                "subject": subject,
                "user_id": int(principal.sub),
                "conversation_id": conversation_id
            }}
        )
        
        return {
            "success": True,
            "message": "Email envoyé avec succès",
            "to_email": to_email,
            "subject": subject
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur envoi email manuel: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=public_error_detail(code="manual_email_send_error", exc=e, logger_name=__name__),
        )


@router.post("/whatsapp/send-manual")
async def send_manual_whatsapp(
    to_number: str = Form(...),
    message: str = Form(...),
    conversation_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
    _=Depends(require_role("agent|manager|admin"))
):
    """
    Envoyer un message WhatsApp manuellement.
    """
    try:
        from ..services.whatsapp import WhatsAppService
        
        whatsapp_service = WhatsAppService()
        
        # Envoyer le message
        sent = await whatsapp_service.send_message(
            to_number=to_number,
            message=message
        )
        
        # Service non configuré ou erreur côté provider
        if not sent:
            raise HTTPException(
                status_code=400,
                detail="Échec de l'envoi du message WhatsApp (service non configuré ou erreur provider)"
            )
        
        # Logger le message dans la conversation si fournie
        if conversation_id:
            conv = db.get(Conversation, UUID(conversation_id))
            if conv:
                conv.last_human_interaction = datetime.utcnow()
                
                kb_service.create_message(
                    db,
                    conversation_id=conversation_id,
                    role="assistant",
                    canal="whatsapp",
                    content=message
                )
                
                db.commit()
        
        logger.info(
            f"Message WhatsApp manuel envoyé",
            extra={"extra_fields": {
                "to_number": to_number,
                "user_id": int(principal.sub),
                "conversation_id": conversation_id
            }}
        )
        
        return {
            "success": True,
            "message": "Message WhatsApp envoyé avec succès",
            "to_number": to_number
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur envoi WhatsApp manuel: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=public_error_detail(code="manual_whatsapp_send_error", exc=e, logger_name=__name__),
        )


@router.get("/conversations/pending-review")
async def get_pending_review_conversations(
    db: Session = Depends(get_db),
    _=Depends(require_role("agent|manager|admin"))
):
    """
    Récupérer les conversations nécessitant une validation.
    """
    conversations = db.query(Conversation).filter(
        Conversation.requires_validation == True,
        Conversation.status == "pending_review"
    ).order_by(Conversation.created_at.desc()).limit(50).all()
    
    return {
        "items": [
            {
                "id": str(conv.id),
                "person_id": str(conv.person_id) if conv.person_id else None,
                "canal": conv.canal,
                "resume": conv.resume,
                "created_at": conv.created_at,
                "assigned_to": conv.assigned_to
            }
            for conv in conversations
        ],
        "total": len(conversations)
    }


@router.get("/conversations/assigned-to-me")
async def get_my_conversations(
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
    _=Depends(require_role("agent|manager|admin"))
):
    """
    Récupérer les conversations assignées à l'agent connecté.
    """
    acting_user_id = int(principal.sub)
    conversations = db.query(Conversation).filter(
        Conversation.assigned_to == acting_user_id,
        Conversation.status == "active"
    ).order_by(Conversation.last_human_interaction.desc()).limit(50).all()
    
    return {
        "items": [
            {
                "id": str(conv.id),
                "person_id": str(conv.person_id) if conv.person_id else None,
                "canal": conv.canal,
                "resume": conv.resume,
                "mode": conv.mode,
                "created_at": conv.created_at,
                "last_human_interaction": conv.last_human_interaction
            }
            for conv in conversations
        ],
        "total": len(conversations)
    }
