"""
Endpoints pour la gestion des utilisateurs (Admin uniquement)
CRUD complet: Create, Read, Update, Delete
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from typing import List
from ..db import get_db
from ..models import User, Role, Agent, Manager, Viewer
from ..security import get_principal, Principal
from ..services.auth import hash_password
from pydantic import BaseModel, EmailStr
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["users"])

_ROLE_ORDER = ("admin", "manager", "agent", "viewer")
_ROLE_DESCRIPTIONS = {
    "admin": "Administration complete",
    "manager": "Supervision et gestion",
    "agent": "Traitement des conversations et admissions",
    "viewer": "Lecture seule",
}


# ============= SCHEMAS =============

class UserResponse(BaseModel):
    id: int
    email: str
    first_name: str
    last_name: str
    phone: str
    role: dict
    mfa_enabled: bool
    created_at: str

    class Config:
        from_attributes = True


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str
    phone: str
    role_id: int
    mfa_enabled: bool = False


class UserUpdate(BaseModel):
    first_name: str
    last_name: str
    phone: str
    role_id: int
    mfa_enabled: bool
    password: str | None = None


class RoleResponse(BaseModel):
    id: int
    name: str
    description: str | None = None

    class Config:
        from_attributes = True


def _ensure_core_roles(db: Session) -> None:
    existing_names = {
        str(name)
        for (name,) in db.query(Role.name).all()
        if isinstance(name, str) and name.strip()
    }
    missing = [role_name for role_name in _ROLE_ORDER if role_name not in existing_names]
    if not missing:
        return
    for role_name in missing:
        db.add(Role(name=role_name))
    db.commit()


def _serialize_role(role: Role) -> dict:
    return {
        "id": int(role.id),
        "name": str(role.name),
        "description": _ROLE_DESCRIPTIONS.get(str(role.name)),
    }


# ============= ENDPOINTS =============

@router.get("/users", response_model=List[UserResponse])
def get_all_users(
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal)
):
    """
    Récupérer tous les utilisateurs (Admin uniquement)
    """
    # Vérifier que l'utilisateur est admin
    if "admin" not in principal.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux administrateurs"
        )
    
    # Charger tous les utilisateurs
    users = db.query(User).all()
    
    result = []
    for user in users:
        role_data = None
        if user.role_id:
            role = db.get(Role, user.role_id)
            if role:
                role_data = {
                    "id": role.id,
                    "name": role.name,
                }
        
        result.append({
            "id": user.id,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            # Toujours retourner une chaîne pour satisfaire le schéma de réponse
            "phone": user.phone or "",
            "role": role_data or {"id": 0, "name": "unknown"},
            "mfa_enabled": user.mfa_enabled,
            "created_at": user.created_at.isoformat()
        })
    
    return result


@router.get("/roles", response_model=List[RoleResponse])
def get_all_roles(
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal)
):
    """
    Récupérer tous les rôles disponibles (Admin uniquement)
    """
    if "admin" not in principal.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux administrateurs"
        )
    
    _ensure_core_roles(db)
    roles = db.query(Role).all()
    order_index = {name: idx for idx, name in enumerate(_ROLE_ORDER)}
    roles = sorted(roles, key=lambda r: (order_index.get(str(r.name), 99), str(r.name)))
    return [_serialize_role(role) for role in roles]


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    user_data: UserCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal)
):
    """
    Créer un nouvel utilisateur (Admin uniquement)
    """
    if "admin" not in principal.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux administrateurs"
        )
    
    # Vérifier si l'email existe déjà
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Un utilisateur avec cet email existe déjà"
        )
    
    # Vérifier que le rôle existe
    role = db.query(Role).filter(Role.id == user_data.role_id).first()
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rôle introuvable"
        )
    
    # Créer l'utilisateur
    new_user = User(
        email=user_data.email,
        password_hash=hash_password(user_data.password),
        first_name=user_data.first_name,
        last_name=user_data.last_name,
        phone=user_data.phone,
        role_id=user_data.role_id,
        mfa_enabled=user_data.mfa_enabled
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Charger le rôle pour déterminer le type (agent / manager / viewer)
    role = db.get(Role, new_user.role_id)

    # Créer automatiquement l'entrée liée dans agents / managers / viewers
    if role is not None:
        if role.name == "agent":
            # Ne créer qu'un seul agent par utilisateur
            existing = db.query(Agent).filter(Agent.user_id == new_user.id).first()
            if not existing:
                db.add(Agent(user_id=new_user.id))
                db.commit()
        elif role.name == "manager":
            existing = db.query(Manager).filter(Manager.user_id == new_user.id).first()
            if not existing:
                db.add(Manager(user_id=new_user.id))
                db.commit()
        elif role.name == "viewer":
            existing = db.query(Viewer).filter(Viewer.user_id == new_user.id).first()
            if not existing:
                db.add(Viewer(user_id=new_user.id))
                db.commit()

    logger.info(f"Utilisateur créé: {new_user.email} par {principal.sub}")
    
    return {
        "id": new_user.id,
        "email": new_user.email,
        "first_name": new_user.first_name,
        "last_name": new_user.last_name,
        # Normaliser en chaîne vide si None
        "phone": new_user.phone or "",
        "role": {
            "id": role.id,
            "name": role.name,
        } if role else {"id": 0, "name": "unknown"},
        "mfa_enabled": new_user.mfa_enabled,
        "created_at": new_user.created_at.isoformat()
    }


@router.put("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    user_data: UserUpdate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal)
):
    """
    Modifier un utilisateur (Admin uniquement)
    """
    if "admin" not in principal.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux administrateurs"
        )
    
    # Récupérer l'utilisateur
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur introuvable"
        )
    
    # Vérifier que le rôle existe
    role = db.query(Role).filter(Role.id == user_data.role_id).first()
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rôle introuvable"
        )
    
    # Mettre à jour les champs
    user.first_name = user_data.first_name
    user.last_name = user_data.last_name
    user.phone = user_data.phone
    user.role_id = user_data.role_id
    user.mfa_enabled = user_data.mfa_enabled
    
    # Mettre à jour le mot de passe si fourni
    if user_data.password:
        user.password_hash = hash_password(user_data.password)
    
    db.commit()
    db.refresh(user)
    
    logger.info(f"Utilisateur modifié: {user.email} par {principal.sub}")
    
    # Charger le rôle
    role = db.get(Role, user.role_id)
    
    return {
        "id": user.id,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        # Normaliser en chaîne vide si None
        "phone": user.phone or "",
        "role": {
            "id": role.id,
            "name": role.name,
        } if role else {"id": 0, "name": "unknown"},
        "mfa_enabled": user.mfa_enabled,
        "created_at": user.created_at.isoformat()
    }


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal)
):
    """
    Supprimer un utilisateur (Admin uniquement)
    """
    if "admin" not in principal.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux administrateurs"
        )
    
    # Récupérer l'utilisateur
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur introuvable"
        )
    
    # Empêcher la suppression de son propre compte
    if str(user.id) == str(principal.sub):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vous ne pouvez pas supprimer votre propre compte"
        )
    
    logger.info(f"Utilisateur supprimé: {user.email} par {principal.sub}")
    
    db.delete(user)
    db.commit()
    
    return None
