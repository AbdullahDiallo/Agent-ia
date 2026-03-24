"""Service CRUD pour la gestion des viewers."""
from __future__ import annotations

from typing import Optional, List
from uuid import UUID

from sqlalchemy.orm import Session

from ..models import Viewer, User, Role
from ..services.auth import hash_password


def create_viewer(
    db: Session,
    *,
    first_name: str,
    last_name: str,
    email: str,
    phone: Optional[str] = None,
    password: str
) -> tuple[User, Viewer]:
    """Crée un nouveau viewer avec son utilisateur associé.
    
    Cette fonction crée d'abord un utilisateur avec le rôle 'viewer',
    puis crée l'enregistrement viewer lié.
    
    Args:
        db: Session de base de données
        first_name: Prénom du viewer
        last_name: Nom de famille du viewer
        email: Email du viewer
        phone: Téléphone du viewer
        password: Mot de passe pour le compte utilisateur
    
    Returns:
        Tuple (User, Viewer) créés
    
    Raises:
        ValueError: Si l'email existe déjà ou si le rôle 'viewer' n'existe pas
    """
    # Vérifier si l'email existe déjà
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        raise ValueError(f"Un utilisateur avec l'email {email} existe déjà")
    
    # Récupérer le rôle 'viewer'
    viewer_role = db.query(Role).filter(Role.name == 'viewer').first()
    if not viewer_role:
        raise ValueError("Le rôle 'viewer' n'existe pas dans la base de données")
    
    # Créer l'utilisateur
    user = User(
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=phone,
        password_hash=hash_password(password),
        role_id=viewer_role.id,
        mfa_enabled=False
    )
    db.add(user)
    db.flush()  # Pour obtenir l'ID de l'utilisateur
    
    # Créer le viewer lié
    viewer = Viewer(
        user_id=user.id
    )
    db.add(viewer)
    db.commit()
    db.refresh(user)
    db.refresh(viewer)
    
    return user, viewer


def get_viewer(db: Session, viewer_id: UUID) -> Optional[Viewer]:
    """Récupère un viewer par son ID.
    
    Args:
        db: Session de base de données
        viewer_id: ID du viewer
    
    Returns:
        Viewer ou None si non trouvé
    """
    return db.get(Viewer, viewer_id)


def get_viewer_by_user_id(db: Session, user_id: int) -> Optional[Viewer]:
    """Récupère un viewer par son user_id.
    
    Args:
        db: Session de base de données
        user_id: ID de l'utilisateur
    
    Returns:
        Viewer ou None si non trouvé
    """
    return db.query(Viewer).filter(Viewer.user_id == user_id).first()


def list_viewers(
    db: Session,
    *,
    limit: int = 100,
    offset: int = 0
) -> List[Viewer]:
    """Liste les viewers.
    
    Args:
        db: Session de base de données
        limit: Nombre maximum de viewers à retourner
        offset: Offset pour la pagination
    
    Returns:
        Liste de viewers
    """
    query = db.query(Viewer).join(User, Viewer.user_id == User.id)
    query = query.order_by(User.last_name, User.first_name)
    query = query.limit(limit).offset(offset)
    
    return query.all()


def update_viewer(
    db: Session,
    viewer_id: UUID,
    *,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None
) -> Optional[tuple[User, Viewer]]:
    """Met à jour un viewer et son utilisateur associé.
    
    Args:
        db: Session de base de données
        viewer_id: ID du viewer
        first_name: Nouveau prénom (optionnel)
        last_name: Nouveau nom (optionnel)
        email: Nouvel email (optionnel)
        phone: Nouveau téléphone (optionnel)
    
    Returns:
        Tuple (User, Viewer) mis à jour ou None si non trouvé
    """
    viewer = db.get(Viewer, viewer_id)
    if not viewer:
        return None
    
    # Récupérer l'utilisateur associé
    user = db.get(User, viewer.user_id)
    if not user:
        return None
    
    # Mettre à jour les informations utilisateur
    if first_name is not None:
        user.first_name = first_name
    if last_name is not None:
        user.last_name = last_name
    if email is not None:
        user.email = email
    if phone is not None:
        user.phone = phone
    
    db.add(user)
    db.commit()
    db.refresh(user)
    db.refresh(viewer)
    
    return user, viewer


def delete_viewer(db: Session, viewer_id: UUID) -> bool:
    """Supprime un viewer.
    
    Args:
        db: Session de base de données
        viewer_id: ID du viewer
    
    Returns:
        True si supprimé, False si non trouvé
    """
    viewer = db.get(Viewer, viewer_id)
    if not viewer:
        return False
    
    db.delete(viewer)
    db.commit()
    return True
