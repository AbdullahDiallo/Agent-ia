"""
Endpoint pour créer les utilisateurs de test (seeds)
Accessible uniquement en développement
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import User, Role, Permission, RolePermission
from ..services.auth import hash_password
from ..config import settings
from ..security import require_dev_endpoint
from ..utils.http_errors import public_error_detail
from pydantic import BaseModel, EmailStr
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/seed", tags=["seed"], dependencies=[Depends(require_dev_endpoint)])


class SeedUserRequest(BaseModel):
    email: EmailStr
    password: str
    role: str = "admin"
    first_name: str = "Admin"
    last_name: str = "School"
    phone: str = "+221770000000"


@router.post("/create-users", dependencies=[Depends(require_dev_endpoint)])
def create_seed_users(db: Session = Depends(get_db)):
    """
    Créer les utilisateurs de test avec différents rôles.
    ⚠️ À utiliser uniquement en développement !
    """
    # Bloquer en production
    if settings.env and settings.env.lower() in ("prod", "production"):
        raise HTTPException(status_code=403, detail="Seeds non disponibles en production")

    try:
        # 1. Créer les permissions
        permissions_data = [
            "view_dashboard",
            "view_persons",
            "edit_persons",
            "delete_persons",
            "view_conversations",
            "manage_conversations",
            "view_tracks",
            "edit_tracks",
            "view_calendar",
            "manage_calendar",
            "send_emails",
            "send_sms",
            "view_analytics",
            "manage_users",
            "manage_settings",
            "manage_ai_features",
        ]

        permissions = {}
        for perm_name in permissions_data:
            perm = db.query(Permission).filter(Permission.name == perm_name).first()
            if not perm:
                perm = Permission(name=perm_name)
                db.add(perm)
            permissions[perm_name] = perm

        db.commit()

        # 2. Créer les rôles avec leurs permissions
        roles_data = {
            "admin": {
                "description": "Administrateur - Accès complet",
                "permissions": list(permissions.keys())
            },
            "manager": {
                "description": "Manager - Gestion des équipes et analytics",
                "permissions": [
                    "view_dashboard", "view_persons", "edit_persons",
                    "view_conversations", "manage_conversations",
                    "view_tracks", "edit_tracks",
                    "view_calendar", "manage_calendar",
                    "send_emails", "send_sms",
                    "view_analytics", "manage_ai_features"
                ]
            },
            "agent": {
                "description": "Agent - Gestion des personnes et conversations",
                "permissions": [
                    "view_dashboard", "view_persons", "edit_persons",
                    "view_conversations", "manage_conversations",
                    "view_tracks",
                    "view_calendar", "manage_calendar",
                    "send_emails", "send_sms"
                ]
            },
            "viewer": {
                "description": "Visualiseur - Lecture seule",
                "permissions": [
                    "view_dashboard", "view_persons",
                    "view_conversations", "view_tracks",
                    "view_calendar", "view_analytics"
                ]
            }
        }

        roles = {}
        for role_name, role_info in roles_data.items():
            role = db.query(Role).filter(Role.name == role_name).first()
            if not role:
                role = Role(name=role_name)
                db.add(role)
                db.flush()

            # Réinitialiser les permissions existantes
            db.query(RolePermission).filter(RolePermission.role_id == role.id).delete()
            # Assigner les permissions via la table de liaison
            for perm_name in role_info["permissions"]:
                perm = permissions.get(perm_name)
                if perm:
                    db.add(RolePermission(role_id=role.id, permission_id=perm.id))

            roles[role_name] = role

        db.commit()

        # 3. Créer les utilisateurs de test
        users_data = [
            {
                "email": "admin@aelixoria.com",
                "password": "Admin123!",
                "first_name": "Admin",
                "last_name": "Aelixoria AI",
                "role": "admin",
                "phone": "+33612345678"
            },
             {
            "email": "manager@example.com",
            "password": "Manager123!",
            "first_name": "Marie",
            "last_name": "Dupont",
            "role": "manager",
            "phone": "+33612345679"
        },
        {
            "email": "agent1@example.com",
            "password": "Agent123!",
            "first_name": "Jean",
            "last_name": "Martin",
            "role": "agent",
            "phone": "+33612345680"
        },
        {
            "email": "agent2@aelixoria.com",
            "password": "Agent123!",
            "first_name": "Sophie",
            "last_name": "Bernard",
            "role": "agent",
            "phone": "+33612345681"
        },
            {
                "email": "viewer@aelixoria.com",
                "password": "Viewer123!",
                "first_name": "Pierre",
                "last_name": "Durand",
                "role": "viewer",
                "phone": "+33612345682"
            }
        ]

        created_users = []
        for user_data in users_data:
            existing_user = db.query(User).filter(User.email == user_data["email"]).first()
            if existing_user:
                continue

            user = User(
                email=user_data["email"],
                password_hash=hash_password(user_data["password"]),
                first_name=user_data["first_name"],
                last_name=user_data["last_name"],
                phone=user_data["phone"],
                role_id=roles[user_data["role"]].id,
                mfa_enabled=False
            )

            db.add(user)
            created_users.append({
                "email": user_data["email"],
                "role": user_data["role"]
            })

        db.commit()

        return {
            "success": True,
            "message": f"{len(created_users)} utilisateurs créés",
            "users": created_users,
            "credentials": "See seed data configuration. Passwords should be changed after first login."
        }

    except Exception as e:
        logger.error(f"Erreur lors de la création des seeds: {e}")
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=public_error_detail(code="seed_create_error", exc=e, logger_name=__name__),
        )


@router.post("/create-user", dependencies=[Depends(require_dev_endpoint)])
def create_seed_user(payload: SeedUserRequest, db: Session = Depends(get_db)):
    """
    Créer (ou mettre à jour) un utilisateur de seed ciblé.
    ⚠️ Disponible uniquement en développement.
    """
    if settings.env and settings.env.lower() in ("prod", "production"):
        raise HTTPException(status_code=403, detail="Seeds non disponibles en production")

    role_name = payload.role.strip().lower()
    if role_name not in {"admin", "manager", "agent", "viewer"}:
        raise HTTPException(status_code=400, detail="role_invalide")

    role = db.query(Role).filter(Role.name == role_name).first()
    if not role:
        role = Role(name=role_name)
        db.add(role)
        db.commit()
        db.refresh(role)

    user = db.query(User).filter(User.email == payload.email.lower()).first()
    if user:
        user.password_hash = hash_password(payload.password)
        user.first_name = payload.first_name
        user.last_name = payload.last_name
        user.phone = payload.phone
        user.role_id = role.id
        action = "updated"
    else:
        user = User(
            email=payload.email.lower(),
            password_hash=hash_password(payload.password),
            first_name=payload.first_name,
            last_name=payload.last_name,
            phone=payload.phone,
            role_id=role.id,
            mfa_enabled=False,
        )
        db.add(user)
        action = "created"

    db.commit()
    db.refresh(user)

    return {
        "success": True,
        "action": action,
        "user": {
            "id": int(user.id),
            "email": user.email,
            "role": role_name,
        },
        "credentials": {
            "email": user.email,
            "password": payload.password,
        },
    }
