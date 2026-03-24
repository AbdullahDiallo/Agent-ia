"""
Router pour gérer les webhooks d'enregistrement Twilio
"""
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session
from ..db import get_db
from ..services import kb as kb_service
from ..logger import get_logger
from ..security import require_role
from ..services.webhook_security import verify_webhook
import httpx

router = APIRouter(tags=["voice-recording"])
logger = get_logger(__name__)


@router.post("/voice/recording-status")
async def recording_status_callback(request: Request, db: Session = Depends(get_db)):
    """
    Webhook appelé par Twilio quand un enregistrement est terminé.
    
    Twilio envoie les paramètres suivants :
    - CallSid : ID de l'appel
    - RecordingSid : ID de l'enregistrement
    - RecordingUrl : URL de l'enregistrement
    - RecordingDuration : Durée en secondes
    - RecordingStatus : "completed", "failed", etc.
    """
    try:
        # Vérifier la signature Twilio
        raw_body = await request.body()
        form = await request.form()
        verify_webhook(
            "twilio_recording",
            request=request,
            raw_body=raw_body,
            form_data={str(k): str(v) for k, v in dict(form).items()},
            url=str(request.url),
        )
        
        # Récupérer les données de l'enregistrement
        call_sid = form.get("CallSid")
        recording_sid = form.get("RecordingSid")
        recording_url = form.get("RecordingUrl")
        recording_duration = form.get("RecordingDuration")
        recording_status = form.get("RecordingStatus")
        
        logger.info(
            f"Enregistrement reçu",
            extra={
                "extra_fields": {
                    "call_sid": call_sid,
                    "recording_sid": recording_sid,
                    "duration": recording_duration,
                    "status": recording_status
                }
            }
        )
        
        # Si l'enregistrement a réussi
        if recording_status == "completed" and recording_url:
            # Ajouter l'extension .mp3 à l'URL si nécessaire
            if not recording_url.endswith(".mp3"):
                recording_url = f"{recording_url}.mp3"
            
            # Mettre à jour la conversation avec l'URL de l'enregistrement
            from ..models import Conversation
            conv = db.query(Conversation).filter(
                Conversation.call_sid == call_sid
            ).first()
            
            if conv:
                conv.recording_sid = recording_sid
                conv.recording_url = recording_url
                conv.recording_duration = int(recording_duration) if recording_duration else None
                db.commit()
                
                logger.info(
                    f"Conversation mise à jour avec l'enregistrement",
                    extra={
                        "extra_fields": {
                            "conversation_id": str(conv.id),
                            "recording_url": recording_url
                        }
                    }
                )
            else:
                logger.warning(
                    f"Conversation non trouvée pour Call SID: {call_sid}"
                )
        
        # Retourner 200 OK pour confirmer la réception
        return Response(content="OK", status_code=200)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur traitement webhook enregistrement: {e}", exc_info=True)
        # Retourner 200 pour éviter que Twilio réessaie
        return Response(content="OK", status_code=200)


@router.get("/voice/recording/{recording_sid}", dependencies=[Depends(require_role("agent|manager|admin"))])
async def get_recording(recording_sid: str, db: Session = Depends(get_db)):
    """
    Récupère les informations d'un enregistrement.
    """
    from ..models import Conversation
    
    conv = db.query(Conversation).filter(
        Conversation.recording_sid == recording_sid
    ).first()
    
    if not conv:
        return {"error": "Enregistrement non trouvé"}, 404
    
    return {
        "id": str(conv.id),
        "call_sid": conv.call_sid,
        "recording_sid": conv.recording_sid,
        "recording_url": conv.recording_url,
        "recording_duration": conv.recording_duration,
        "created_at": conv.created_at,
    }


@router.post("/voice/download-recording/{conversation_id}", dependencies=[Depends(require_role("agent|manager|admin"))])
async def download_recording(conversation_id: str, db: Session = Depends(get_db)):
    """
    Télécharge l'enregistrement audio depuis Twilio et le sauvegarde localement.
    
    Cette fonction peut être utilisée pour :
    1. Sauvegarder les enregistrements localement (backup)
    2. Migrer vers un stockage S3
    3. Analyser l'audio (qualité, émotions, etc.)
    """
    from ..models import Conversation
    from uuid import UUID
    from ..config import settings
    import os
    
    try:
        conv = db.get(Conversation, UUID(conversation_id))
        if not conv or not conv.recording_url:
            return {"error": "Enregistrement non trouvé"}, 404
        
        # Télécharger l'audio depuis Twilio
        async with httpx.AsyncClient() as client:
            # Authentification Twilio
            auth = (settings.twilio_account_sid, settings.twilio_auth_token)
            response = await client.get(conv.recording_url, auth=auth)
            
            if response.status_code != 200:
                logger.error(f"Erreur téléchargement enregistrement: {response.status_code}")
                return {"error": "Échec du téléchargement"}, 500
            
            audio_data = response.content
        
        # Créer le dossier recordings s'il n'existe pas
        recordings_dir = "recordings"
        os.makedirs(recordings_dir, exist_ok=True)
        
        # Sauvegarder localement
        file_path = f"{recordings_dir}/{conv.call_sid}.mp3"
        with open(file_path, "wb") as f:
            f.write(audio_data)
        
        logger.info(
            f"Enregistrement téléchargé",
            extra={
                "extra_fields": {
                    "conversation_id": conversation_id,
                    "file_path": file_path,
                    "size_bytes": len(audio_data)
                }
            }
        )
        
        return {
            "success": True,
            "file_path": file_path,
            "size_bytes": len(audio_data),
            "duration_seconds": conv.recording_duration
        }
        
    except Exception as e:
        logger.error(f"Erreur téléchargement enregistrement: {e}", exc_info=True)
        return {"error": str(e)}, 500
