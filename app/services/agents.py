"""Service CRUD pour la gestion des agents."""
from __future__ import annotations

from typing import Optional, List
from uuid import UUID

from sqlalchemy.orm import Session

from ..models import Agent, User, Role
from ..services.auth import hash_password


def create_agent(
    db: Session,
    *,
    first_name: str,
    last_name: str,
    email: str,
    phone: Optional[str] = None,
    password: str,
    specialite: Optional[str] = None,
    max_rdv_par_jour: int = 8,
    secteur_geographique: Optional[str] = None
) -> tuple[User, Agent]:
    """Crée un nouvel agent avec son utilisateur associé.
    
    Cette fonction crée d'abord un utilisateur avec le rôle 'agent',
    puis crée l'enregistrement agent lié.
    
    Args:
        db: Session de base de données
        first_name: Prénom de l'agent
        last_name: Nom de famille de l'agent
        email: Email de l'agent
        phone: Téléphone de l'agent
        password: Mot de passe pour le compte utilisateur
        specialite: Spécialité (admissions, filières, orientation, etc.)
        max_rdv_par_jour: Nombre maximum de RDV par jour
        secteur_geographique: Secteurs géographiques (séparés par virgule)
    
    Returns:
        Tuple (User, Agent) créés
    
    Raises:
        ValueError: Si l'email existe déjà ou si le rôle 'agent' n'existe pas
    """
    # Vérifier si l'email existe déjà
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        raise ValueError(f"Un utilisateur avec l'email {email} existe déjà")
    
    # Récupérer le rôle 'agent'
    agent_role = db.query(Role).filter(Role.name == 'agent').first()
    if not agent_role:
        raise ValueError("Le rôle 'agent' n'existe pas dans la base de données")
    
    # Créer l'utilisateur
    user = User(
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=phone,
        password_hash=hash_password(password),
        role_id=agent_role.id,
        mfa_enabled=False
    )
    db.add(user)
    db.flush()  # Pour obtenir l'ID de l'utilisateur
    
    # Créer l'agent lié
    agent = Agent(
        user_id=user.id,
        specialite=specialite,
        disponible=True,
        max_rdv_par_jour=max_rdv_par_jour,
        secteur_geographique=secteur_geographique
    )
    db.add(agent)
    db.commit()
    db.refresh(user)
    db.refresh(agent)
    
    return user, agent


def get_agent(db: Session, agent_id: UUID) -> Optional[Agent]:
    """Récupère un agent par son ID.
    
    Args:
        db: Session de base de données
        agent_id: ID de l'agent
    
    Returns:
        Agent ou None si non trouvé
    """
    return db.get(Agent, agent_id)


def list_agents(
    db: Session,
    *,
    disponible_only: bool = False,
    specialite: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
) -> List[Agent]:
    """Liste les agents avec filtres optionnels.
    
    Args:
        db: Session de base de données
        disponible_only: Filtrer uniquement les agents disponibles
        specialite: Filtrer par spécialité
        limit: Nombre maximum d'agents à retourner
        offset: Offset pour la pagination
    
    Returns:
        Liste d'agents
    """
    query = db.query(Agent).join(User, Agent.user_id == User.id)
    
    if disponible_only:
        query = query.filter(Agent.disponible == True)
    
    if specialite:
        query = query.filter(Agent.specialite == specialite)
    
    query = query.order_by(User.last_name, User.first_name)
    query = query.limit(limit).offset(offset)
    
    return query.all()


def update_agent(
    db: Session,
    agent_id: UUID,
    *,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    specialite: Optional[str] = None,
    disponible: Optional[bool] = None,
    max_rdv_par_jour: Optional[int] = None,
    secteur_geographique: Optional[str] = None
) -> Optional[tuple[User, Agent]]:
    """Met à jour un agent et son utilisateur associé.
    
    Args:
        db: Session de base de données
        agent_id: ID de l'agent
        first_name: Nouveau prénom (optionnel)
        last_name: Nouveau nom (optionnel)
        email: Nouvel email (optionnel)
        phone: Nouveau téléphone (optionnel)
        specialite: Nouvelle spécialité (optionnel)
        disponible: Nouvelle disponibilité (optionnel)
        max_rdv_par_jour: Nouveau max RDV (optionnel)
        secteur_geographique: Nouveaux secteurs (optionnel)
    
    Returns:
        Tuple (User, Agent) mis à jour ou None si non trouvé
    """
    agent = db.get(Agent, agent_id)
    if not agent:
        return None
    
    # Récupérer l'utilisateur associé
    user = db.get(User, agent.user_id)
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
    
    # Mettre à jour les informations agent
    if specialite is not None:
        agent.specialite = specialite
    if disponible is not None:
        agent.disponible = disponible
    if max_rdv_par_jour is not None:
        agent.max_rdv_par_jour = max_rdv_par_jour
    if secteur_geographique is not None:
        agent.secteur_geographique = secteur_geographique
    
    db.add(user)
    db.add(agent)
    db.commit()
    db.refresh(user)
    db.refresh(agent)
    
    return user, agent


def delete_agent(db: Session, agent_id: UUID) -> bool:
    """Supprime un agent.
    
    Args:
        db: Session de base de données
        agent_id: ID de l'agent
    
    Returns:
        True si supprimé, False si non trouvé
    """
    agent = db.get(Agent, agent_id)
    if not agent:
        return False
    
    db.delete(agent)
    db.commit()
    return True


def toggle_availability(db: Session, agent_id: UUID) -> Optional[Agent]:
    """Bascule la disponibilité d'un agent.
    
    Args:
        db: Session de base de données
        agent_id: ID de l'agent
    
    Returns:
        Agent mis à jour ou None si non trouvé
    """
    agent = db.get(Agent, agent_id)
    if not agent:
        return None
    
    agent.disponible = not agent.disponible
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent
