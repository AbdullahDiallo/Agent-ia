"""Service d'assignation automatique des agents aux rendez-vous."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from uuid import UUID

from sqlalchemy.orm import Session

from ..models import Agent, RendezVous, User
from ..logger import get_logger

logger = get_logger(__name__)
ACTIVE_ASSIGN_STATUSES = ["created", "confirmed", "reminder_sent", "pending"]


def _agent_display_name(db: Session, agent: Agent) -> str:
    user = db.get(User, agent.user_id)
    if user:
        full = f"{(user.first_name or '').strip()} {(user.last_name or '').strip()}".strip()
        if full:
            return full
        if user.email:
            return user.email
    return str(agent.id)


def has_conflict(db: Session, agent_id: UUID, start_at: datetime, end_at: datetime, exclude_rdv_id: Optional[UUID] = None) -> bool:
    """Vérifie si un agent a déjà un rendez-vous sur ce créneau.

    Args:
        db: Session de base de données
        agent_id: ID de l'agent
        start_at: Début du créneau à vérifier
        end_at: Fin du créneau à vérifier
        exclude_rdv_id: ID du RDV à exclure (pour les modifications)

    Returns:
        True si conflit détecté, False sinon
    """
    query = db.query(RendezVous).filter(
        RendezVous.agent_id == agent_id,
        RendezVous.statut.in_(ACTIVE_ASSIGN_STATUSES),
        # Détection de chevauchement : (start1 < end2) AND (end1 > start2)
        RendezVous.start_at < end_at,
        RendezVous.end_at > start_at
    )

    if exclude_rdv_id:
        query = query.filter(RendezVous.id != exclude_rdv_id)

    conflicts = query.count()
    return conflicts > 0


def get_agent_workload(db: Session, agent_id: UUID, date: datetime) -> int:
    """Calcule le nombre de RDV d'un agent pour une journée donnée.

    Args:
        db: Session de base de données
        agent_id: ID de l'agent
        date: Date à vérifier

    Returns:
        Nombre de RDV confirmés ou en attente
    """
    start_of_day = date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day + timedelta(days=1)

    count = db.query(RendezVous).filter(
        RendezVous.agent_id == agent_id,
        RendezVous.statut.in_(ACTIVE_ASSIGN_STATUSES),
        RendezVous.start_at >= start_of_day,
        RendezVous.start_at < end_of_day
    ).count()

    return count


def calculate_agent_score(db: Session, agent: Agent, rdv_start: datetime, rdv_end: datetime) -> float:
    """Calcule un score pour un agent basé sur plusieurs critères.

    Plus le score est élevé, plus l'agent est adapté pour ce RDV.

    Critères:
    - Charge de travail (moins de RDV = meilleur score)
    - Disponibilite

    Args:
        db: Session de base de données
        agent: Agent à évaluer
        rdv_start: Début du RDV
        rdv_end: Fin du RDV
    Returns:
        Score de 0 à 100
    """
    score = 100.0

    # 1. Pénalité pour charge de travail (max -40 points)
    workload = get_agent_workload(db, agent.id, rdv_start)
    if workload >= agent.max_rdv_par_jour:
        return 0.0  # Agent à capacité maximale

    workload_penalty = (workload / agent.max_rdv_par_jour) * 40
    score -= workload_penalty

    # Bonus pour disponibilité (+10 points)
    if agent.disponible:
        score += 10

    return max(0.0, score)


def find_available_agents(db: Session, start_at: datetime, end_at: datetime, track_id: Optional[UUID] = None) -> List[Tuple[Agent, float]]:
    """Trouve tous les agents disponibles pour un créneau donné et les classe par score.

    Args:
        db: Session de base de données
        start_at: Début du créneau
        end_at: Fin du créneau
        track_id: ID de la filiere (optionnel, pour affiner le scoring)

    Returns:
        Liste de tuples (agent, score) triée par score décroissant
    """
    # Récupérer tous les agents actifs
    agents = db.query(Agent).filter(
        Agent.disponible == True
    ).all()

    if not agents:
        logger.warning("No active agents found in database")
        return []

    # Filtrer les agents sans conflit et calculer leur score
    available_with_score: List[Tuple[Agent, float]] = []

    for agent in agents:
        # Vérifier les conflits
        if has_conflict(db, agent.id, start_at, end_at):
            logger.debug(
                "Agent has conflict",
                extra={"extra_fields": {"agent_id": str(agent.id)}}
            )
            continue

        # Calculer le score
        score = calculate_agent_score(db, agent, start_at, end_at)

        if score > 0:
            available_with_score.append((agent, score))

    # Trier par score décroissant
    available_with_score.sort(key=lambda x: x[1], reverse=True)

    logger.info(
        f"Found {len(available_with_score)} available agents",
        extra={
            "extra_fields": {
                "start_at": start_at.isoformat(),
                "end_at": end_at.isoformat(),
                "available_count": len(available_with_score)
            }
        }
    )

    return available_with_score


def assign_agent_automatically(db: Session, rdv: RendezVous) -> Optional[Agent]:
    """Assigne automatiquement le meilleur agent disponible à un rendez-vous.

    Args:
        db: Session de base de données
        rdv: Rendez-vous à assigner

    Returns:
        Agent assigné ou None si aucun agent disponible
    """
    available = find_available_agents(db, rdv.start_at, rdv.end_at, rdv.track_id)

    if not available:
        logger.warning(
            "No available agent for appointment",
            extra={
                "extra_fields": {
                    "rdv_id": str(rdv.id),
                    "start_at": rdv.start_at.isoformat()
                }
            }
        )
        return None

    # Prendre le meilleur agent (score le plus élevé)
    best_agent, score = available[0]

    # Assigner l'agent
    rdv.agent_id = best_agent.id
    rdv.agent = _agent_display_name(db, best_agent)
    db.add(rdv)
    db.commit()
    db.refresh(rdv)

    logger.info(
        "Agent assigned to appointment",
        extra={
            "extra_fields": {
                "rdv_id": str(rdv.id),
                "agent_id": str(best_agent.id),
                "agent_name": rdv.agent,
                "score": score
            }
        }
    )

    return best_agent


def reassign_agent_if_needed(db: Session, rdv: RendezVous) -> Optional[Agent]:
    """Réassigne un agent si le RDV a été modifié et qu'il y a maintenant un conflit.

    Args:
        db: Session de base de données
        rdv: Rendez-vous à vérifier

    Returns:
        Nouvel agent assigné ou None si pas de réassignation nécessaire
    """
    if not rdv.agent_id:
        # Pas d'agent assigné, essayer d'en assigner un
        return assign_agent_automatically(db, rdv)

    # Vérifier si l'agent actuel a un conflit
    if has_conflict(db, rdv.agent_id, rdv.start_at, rdv.end_at, exclude_rdv_id=rdv.id):
        logger.warning(
            f"Conflict detected for agent, reassigning",
            extra={
                "extra_fields": {
                    "rdv_id": str(rdv.id),
                    "agent_id": str(rdv.agent_id)
                }
            }
        )

        # Réassigner automatiquement
        rdv.agent_id = None
        rdv.agent = None
        return assign_agent_automatically(db, rdv)

    # Pas de conflit, garder l'agent actuel
    return None


def get_agent_availability_slots(db: Session, agent_id: UUID, date: datetime, slot_duration_minutes: int = 60) -> List[Tuple[datetime, datetime]]:
    """Retourne les créneaux disponibles d'un agent pour une journée.

    Args:
        db: Session de base de données
        agent_id: ID de l'agent
        date: Date à vérifier
        slot_duration_minutes: Durée des créneaux en minutes

    Returns:
        Liste de tuples (start, end) représentant les créneaux libres
    """
    agent = db.get(Agent, agent_id)
    if not agent or not agent.disponible:
        return []

    # Définir les heures de travail (9h-18h par défaut)
    start_of_day = date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_of_day = date.replace(hour=18, minute=0, second=0, microsecond=0)

    # Récupérer tous les RDV de l'agent pour cette journée
    rdvs = db.query(RendezVous).filter(
        RendezVous.agent_id == agent_id,
        RendezVous.statut.in_(ACTIVE_ASSIGN_STATUSES),
        RendezVous.start_at >= start_of_day,
        RendezVous.start_at < end_of_day + timedelta(days=1)
    ).order_by(RendezVous.start_at).all()

    # Calculer les créneaux libres
    free_slots: List[Tuple[datetime, datetime]] = []
    current_time = start_of_day

    for rdv in rdvs:
        # Si il y a un gap avant ce RDV
        if current_time < rdv.start_at:
            # Créer des slots de la durée spécifiée
            slot_start = current_time
            while slot_start + timedelta(minutes=slot_duration_minutes) <= rdv.start_at:
                slot_end = slot_start + timedelta(minutes=slot_duration_minutes)
                free_slots.append((slot_start, slot_end))
                slot_start = slot_end

        # Avancer après ce RDV
        current_time = max(current_time, rdv.end_at)

    # Ajouter les créneaux après le dernier RDV
    if current_time < end_of_day:
        slot_start = current_time
        while slot_start + timedelta(minutes=slot_duration_minutes) <= end_of_day:
            slot_end = slot_start + timedelta(minutes=slot_duration_minutes)
            free_slots.append((slot_start, slot_end))
            slot_start = slot_end

    return free_slots
