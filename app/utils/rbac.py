"""
Utilitaires RBAC réutilisables pour filtrage et logging
"""
from typing import Optional
from uuid import UUID
from sqlalchemy.orm import Session
from ..models import Agent, AuditEvent, User
from ..security import Principal
from ..logger import get_logger
from ..config import settings

logger = get_logger(__name__)


def get_agent_from_principal(db: Session, principal: Principal) -> Optional[Agent]:
    """
    Récupère l'objet Agent associé au principal (utilisateur connecté).
    
    Args:
        db: Session SQLAlchemy
        principal: Principal extrait du JWT
        
    Returns:
        Agent si trouvé, None sinon
    """
    try:
        # Le principal.sub contient l'ID de l'utilisateur (pas l'email)
        user_id = int(principal.sub)
        
        # Récupérer l'agent associé à cet utilisateur
        agent = db.query(Agent).filter(Agent.user_id == user_id).first()
        return agent
    except Exception as e:
        logger.error(f"Erreur lors de la récupération de l'agent: {e}")
        return None


def should_filter_by_agent(principal: Principal) -> bool:
    """
    Détermine si les données doivent être filtrées par agent_id.
    
    Les admins et managers voient tout.
    Les agents ne voient que leurs propres données.
    
    Args:
        principal: Principal extrait du JWT
        
    Returns:
        True si filtrage nécessaire, False sinon
    """
    # Admin et Manager voient tout
    if "admin" in principal.roles or "manager" in principal.roles:
        return False
    
    # Agent doit être filtré
    if "agent" in principal.roles:
        return True
    
    # Viewer voit tout (lecture seule)
    return False


def log_audit_event(
    db: Session,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    details: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    tenant_id: Optional[str] = None,
):
    """
    Enregistre un événement d'audit dans la base de données.
    
    Args:
        db: Session SQLAlchemy
        actor: Email ou identifiant de l'utilisateur
        action: Action effectuée (create, update, delete, etc.)
        resource_type: Type de ressource (person, track, rendezvous, notification, etc.)
        resource_id: ID de la ressource (optionnel)
        details: Détails supplémentaires (optionnel)
        ip_address: Adresse IP (optionnel)
        user_agent: User agent (optionnel)
        tenant_id: Tenant associé (optionnel)
    """
    try:
        resolved_tenant_id = None
        tenant_candidate = tenant_id or getattr(settings, "default_tenant_id", None)
        if tenant_candidate:
            try:
                resolved_tenant_id = UUID(str(tenant_candidate))
            except Exception:
                resolved_tenant_id = None

        audit = AuditEvent(
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            ip_address=ip_address,
            user_agent=user_agent,
            tenant_id=resolved_tenant_id,
        )
        db.add(audit)
        db.commit()
        
        logger.info(
            f"Audit: {actor} {action} {resource_type} {resource_id or ''}",
            extra={
                "extra_fields": {
                    "actor": actor,
                    "action": action,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "tenant_id": str(resolved_tenant_id) if resolved_tenant_id else None,
                }
            }
        )
    except Exception as e:
        logger.error(f"Erreur lors de l'enregistrement de l'audit: {e}")
        db.rollback()
