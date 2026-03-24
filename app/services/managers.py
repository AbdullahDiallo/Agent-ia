"""Service CRUD pour la gestion des managers."""
from __future__ import annotations

from typing import Optional, List
from uuid import UUID

from sqlalchemy.orm import Session

from ..models import Manager, User, Role
from ..services.auth import hash_password


def create_manager(
    db: Session,
    *,
    first_name: str,
    last_name: str,
    email: str,
    phone: Optional[str] = None,
    password: str
) -> tuple[User, Manager]:
    """Crée un nouveau manager avec son utilisateur associé.
    
    Cette fonction crée d'abord un utilisateur avec le rôle 'manager',
    puis crée l'enregistrement manager lié.
    
    Args:
        db: Session de base de données
        first_name: Prénom du manager
        last_name: Nom de famille du manager
        email: Email du manager
        phone: Téléphone du manager
        password: Mot de passe pour le compte utilisateur
    
    Returns:
        Tuple (User, Manager) créés
    
    Raises:
        ValueError: Si l'email existe déjà ou si le rôle 'manager' n'existe pas
    """
    # Vérifier si l'email existe déjà
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        raise ValueError(f"Un utilisateur avec l'email {email} existe déjà")
    
    # Récupérer le rôle 'manager'
    manager_role = db.query(Role).filter(Role.name == 'manager').first()
    if not manager_role:
        raise ValueError("Le rôle 'manager' n'existe pas dans la base de données")
    
    # Créer l'utilisateur
    user = User(
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=phone,
        password_hash=hash_password(password),
        role_id=manager_role.id,
        mfa_enabled=False
    )
    db.add(user)
    db.flush()  # Pour obtenir l'ID de l'utilisateur
    
    # Créer le manager lié
    manager = Manager(
        user_id=user.id
    )
    db.add(manager)
    db.commit()
    db.refresh(user)
    db.refresh(manager)
    
    return user, manager


def get_manager(db: Session, manager_id: UUID) -> Optional[Manager]:
    """Récupère un manager par son ID.
    
    Args:
        db: Session de base de données
        manager_id: ID du manager
    
    Returns:
        Manager ou None si non trouvé
    """
    return db.get(Manager, manager_id)


def get_manager_by_user_id(db: Session, user_id: int) -> Optional[Manager]:
    """Récupère un manager par son user_id.
    
    Args:
        db: Session de base de données
        user_id: ID de l'utilisateur
    
    Returns:
        Manager ou None si non trouvé
    """
    return db.query(Manager).filter(Manager.user_id == user_id).first()


def list_managers(
    db: Session,
    *,
    limit: int = 100,
    offset: int = 0
) -> List[Manager]:
    """Liste les managers.
    
    Args:
        db: Session de base de données
        limit: Nombre maximum de managers à retourner
        offset: Offset pour la pagination
    
    Returns:
        Liste de managers
    """
    query = db.query(Manager).join(User, Manager.user_id == User.id)
    query = query.order_by(User.last_name, User.first_name)
    query = query.limit(limit).offset(offset)
    
    return query.all()


def update_manager(
    db: Session,
    manager_id: UUID,
    *,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None
) -> Optional[tuple[User, Manager]]:
    """Met à jour un manager et son utilisateur associé.
    
    Args:
        db: Session de base de données
        manager_id: ID du manager
        first_name: Nouveau prénom (optionnel)
        last_name: Nouveau nom (optionnel)
        email: Nouvel email (optionnel)
        phone: Nouveau téléphone (optionnel)
    
    Returns:
        Tuple (User, Manager) mis à jour ou None si non trouvé
    """
    manager = db.get(Manager, manager_id)
    if not manager:
        return None
    
    # Récupérer l'utilisateur associé
    user = db.get(User, manager.user_id)
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
    db.refresh(manager)
    
    return user, manager


def delete_manager(db: Session, manager_id: UUID) -> bool:
    """Supprime un manager.
    
    Args:
        db: Session de base de données
        manager_id: ID du manager
    
    Returns:
        True si supprimé, False si non trouvé
    """
    manager = db.get(Manager, manager_id)
    if not manager:
        return False
    
    db.delete(manager)
    db.commit()
    return True
