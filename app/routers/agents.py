"""API Router pour la gestion des agents."""
from __future__ import annotations

from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import AgentCreate, AgentUpdate, AgentResponse, PaginatedResponse
from ..services import agents as agent_service
from ..services.agent_assignment import (
    get_agent_workload,
    get_agent_availability_slots,
    find_available_agents
)
from ..security import require_role
from ..utils.http_errors import public_error_detail
from datetime import datetime

router = APIRouter(prefix="/agents", tags=["agents"], dependencies=[Depends(require_role("agent|viewer|manager|admin"))])


@router.post("", response_model=AgentResponse, dependencies=[Depends(require_role("admin"))])
def create_agent(payload: AgentCreate, db: Session = Depends(get_db)):
    """Crée un nouvel agent.
    
    Nécessite le rôle admin.
    """
    try:
        user, agent = agent_service.create_agent(
            db,
            first_name=payload.first_name,
            last_name=payload.last_name,
            email=payload.email,
            phone=payload.phone,
            password=payload.password,
            specialite=payload.specialite,
            max_rdv_par_jour=payload.max_rdv_par_jour,
            secteur_geographique=payload.secteur_geographique
        )
        return {
            "id": agent.id,
            "user_id": user.id,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "phone": user.phone,
            "specialite": agent.specialite,
            "disponible": agent.disponible,
            "max_rdv_par_jour": agent.max_rdv_par_jour,
            "secteur_geographique": agent.secteur_geographique,
            "created_at": agent.created_at,
            "updated_at": agent.updated_at
        }
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=public_error_detail(code="invalid_agent_payload", exc=e, logger_name=__name__),
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=public_error_detail(code="agent_create_error", exc=e, logger_name=__name__),
        )


@router.get("", response_model=PaginatedResponse[AgentResponse])
def list_agents(
    disponible_only: bool = Query(False, description="Filtrer uniquement les agents disponibles"),
    specialite: Optional[str] = Query(None, description="Filtrer par spécialité"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """Liste tous les agents avec filtres optionnels.
    
    Nécessite le rôle viewer minimum.
    """
    from ..models import Agent, User
    agents = agent_service.list_agents(
        db,
        disponible_only=disponible_only,
        specialite=specialite,
        limit=limit,
        offset=offset
    )
    
    # Compter le total
    query = db.query(Agent)
    if disponible_only:
        query = query.filter(Agent.disponible == True)
    if specialite:
        query = query.filter(Agent.specialite == specialite)
    total = query.count()
    
    # Formater les réponses avec les données utilisateur
    items = []
    for agent in agents:
        user = db.get(User, agent.user_id)
        if user:
            items.append({
                "id": agent.id,
                "user_id": user.id,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "email": user.email,
                "phone": user.phone,
                "specialite": agent.specialite,
                "disponible": agent.disponible,
                "max_rdv_par_jour": agent.max_rdv_par_jour,
                "secteur_geographique": agent.secteur_geographique,
                "created_at": agent.created_at,
                "updated_at": agent.updated_at
            })
    
    return PaginatedResponse.create(
        items=items,
        total=total,
        limit=limit,
        offset=offset
    )


@router.get("/{agent_id}", response_model=AgentResponse, dependencies=[Depends(require_role("viewer"))])
def get_agent(agent_id: UUID, db: Session = Depends(get_db)):
    """Récupère un agent par son ID.
    
    Nécessite le rôle viewer minimum.
    """
    from ..models import User
    agent = agent_service.get_agent(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent non trouvé")
    
    user = db.get(User, agent.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur associé non trouvé")
    
    return {
        "id": agent.id,
        "user_id": user.id,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "phone": user.phone,
        "specialite": agent.specialite,
        "disponible": agent.disponible,
        "max_rdv_par_jour": agent.max_rdv_par_jour,
        "secteur_geographique": agent.secteur_geographique,
        "created_at": agent.created_at,
        "updated_at": agent.updated_at
    }


@router.patch("/{agent_id}", response_model=AgentResponse, dependencies=[Depends(require_role("manager"))])
def update_agent(agent_id: UUID, payload: AgentUpdate, db: Session = Depends(get_db)):
    """Met à jour un agent.
    
    Nécessite le rôle manager.
    """
    result = agent_service.update_agent(
        db,
        agent_id,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email,
        phone=payload.phone,
        specialite=payload.specialite,
        disponible=payload.disponible,
        max_rdv_par_jour=payload.max_rdv_par_jour,
        secteur_geographique=payload.secteur_geographique
    )
    
    if not result:
        raise HTTPException(status_code=404, detail="Agent non trouvé")
    
    user, agent = result
    return {
        "id": agent.id,
        "user_id": user.id,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "phone": user.phone,
        "specialite": agent.specialite,
        "disponible": agent.disponible,
        "max_rdv_par_jour": agent.max_rdv_par_jour,
        "secteur_geographique": agent.secteur_geographique,
        "created_at": agent.created_at,
        "updated_at": agent.updated_at
    }


@router.delete("/{agent_id}", dependencies=[Depends(require_role("admin"))])
def delete_agent(agent_id: UUID, db: Session = Depends(get_db)):
    """Supprime un agent.
    
    Nécessite le rôle admin.
    """
    success = agent_service.delete_agent(db, agent_id)
    if not success:
        raise HTTPException(status_code=404, detail="Agent non trouvé")
    
    return {"success": True, "message": "Agent supprimé avec succès"}


@router.post("/{agent_id}/toggle-availability", response_model=AgentResponse, dependencies=[Depends(require_role("manager"))])
def toggle_availability(agent_id: UUID, db: Session = Depends(get_db)):
    """Bascule la disponibilité d'un agent (disponible <-> indisponible).
    
    Nécessite le rôle manager.
    """
    agent = agent_service.toggle_availability(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent non trouvé")
    
    return agent


@router.get("/{agent_id}/workload", dependencies=[Depends(require_role("viewer"))])
def get_workload(
    agent_id: UUID,
    date: datetime = Query(..., description="Date au format ISO (YYYY-MM-DD)"),
    db: Session = Depends(get_db)
):
    """Récupère la charge de travail d'un agent pour une journée donnée.
    
    Retourne le nombre de rendez-vous confirmés ou en attente.
    
    Nécessite le rôle viewer minimum.
    """
    from ..models import User
    agent = agent_service.get_agent(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent non trouvé")
    
    user = db.get(User, agent.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur associé non trouvé")
    
    workload = get_agent_workload(db, agent_id, date)
    
    return {
        "agent_id": str(agent_id),
        "agent_name": f"{user.first_name} {user.last_name}",
        "date": date.date().isoformat(),
        "rdv_count": workload,
        "max_rdv_par_jour": agent.max_rdv_par_jour,
        "capacity_percentage": round((workload / agent.max_rdv_par_jour) * 100, 2) if agent.max_rdv_par_jour > 0 else 0
    }


@router.get("/{agent_id}/availability", dependencies=[Depends(require_role("viewer"))])
def get_availability(
    agent_id: UUID,
    date: datetime = Query(..., description="Date au format ISO (YYYY-MM-DD)"),
    slot_duration: int = Query(60, ge=15, le=240, description="Durée des créneaux en minutes"),
    db: Session = Depends(get_db)
):
    """Récupère les créneaux disponibles d'un agent pour une journée.
    
    Retourne une liste de créneaux libres.
    
    Nécessite le rôle viewer minimum.
    """
    from ..models import User
    agent = agent_service.get_agent(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent non trouvé")
    
    user = db.get(User, agent.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur associé non trouvé")
    
    if not agent.disponible:
        return {
            "agent_id": str(agent_id),
            "agent_name": f"{user.first_name} {user.last_name}",
            "date": date.date().isoformat(),
            "available": False,
            "slots": []
        }
    
    slots = get_agent_availability_slots(db, agent_id, date, slot_duration)
    
    return {
        "agent_id": str(agent_id),
        "agent_name": f"{user.first_name} {user.last_name}",
        "date": date.date().isoformat(),
        "available": True,
        "slot_duration_minutes": slot_duration,
        "slots": [
            {
                "start": start.isoformat(),
                "end": end.isoformat()
            }
            for start, end in slots
        ],
        "total_slots": len(slots)
    }


@router.get("/available/for-slot", dependencies=[Depends(require_role("viewer"))])
def find_available_for_slot(
    start_at: datetime = Query(..., description="Début du créneau (ISO format)"),
    end_at: datetime = Query(..., description="Fin du créneau (ISO format)"),
    track_id: Optional[UUID] = Query(None, description="ID de la filiere (optionnel)"),
    db: Session = Depends(get_db)
):
    """Trouve tous les agents disponibles pour un créneau donné.
    
    Retourne une liste d'agents triés par score (meilleur en premier).
    
    Nécessite le rôle viewer minimum.
    """
    if start_at >= end_at:
        raise HTTPException(status_code=400, detail="start_at doit être avant end_at")
    
    from ..models import User
    available = find_available_agents(db, start_at, end_at, track_id)
    
    return {
        "start_at": start_at.isoformat(),
        "end_at": end_at.isoformat(),
        "track_id": str(track_id) if track_id else None,
        "available_agents": [
            {
                "agent": {
                    "id": str(agent.id),
                    "nom": f"{db.get(User, agent.user_id).first_name} {db.get(User, agent.user_id).last_name}" if db.get(User, agent.user_id) else "Unknown",
                    "email": db.get(User, agent.user_id).email if db.get(User, agent.user_id) else None,
                    "specialite": agent.specialite,
                    "secteur_geographique": agent.secteur_geographique
                },
                "score": score,
                "recommended": idx == 0
            }
            for idx, (agent, score) in enumerate(available)
        ],
        "total_available": len(available)
    }
