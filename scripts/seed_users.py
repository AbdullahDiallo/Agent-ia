"""
Script pour créer des utilisateurs de test avec différents rôles
Permet de tester le système de contrôle d'accès basé sur les rôles (RBAC)
"""
import sys
import os
from pathlib import Path

# Ajouter la racine du projet au PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session
from app.db import engine, SessionLocal
from app.models import User, Role, Permission
from app.services.auth import hash_password
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_roles_and_permissions(db: Session):
    """Créer les rôles et permissions de base"""
    
    # Définir les permissions
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
            logger.info(f"✓ Permission créée: {perm_name}")
        permissions[perm_name] = perm
    
    db.commit()
    
    # Définir les rôles avec leurs permissions
    roles_data = {
        "admin": {
            "permissions": list(permissions.keys())  # Toutes les permissions
        },
        "manager": {
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
            "permissions": [
                "view_dashboard", "view_persons", "edit_persons",
                "view_conversations", "manage_conversations",
                "view_tracks",
                "view_calendar", "manage_calendar",
                "send_emails", "send_sms"
            ]
        },
        "viewer": {
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
            logger.info(f"✓ Rôle créé: {role_name}")
        
        # Assigner les permissions au rôle
        role.permissions = []
        for perm_name in role_info["permissions"]:
            if perm_name in permissions:
                role.permissions.append(permissions[perm_name])
        
        roles[role_name] = role
    
    db.commit()
    return roles


def create_test_users(db: Session, roles: dict):
    """Créer des utilisateurs de test avec différents rôles"""
    
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
        # Vérifier si l'utilisateur existe déjà
        existing_user = db.query(User).filter(User.email == user_data["email"]).first()
        if existing_user:
            logger.info(f"⚠ Utilisateur existe déjà: {user_data['email']}")
            continue
        
        # Créer l'utilisateur
        user = User(
            email=user_data["email"],
            password_hash=hash_password(user_data["password"]),
            first_name=user_data["first_name"],
            last_name=user_data["last_name"],
            phone=user_data["phone"],
            role_id=roles[user_data["role"]].id,
            mfa_enabled=False  # Désactiver MFA pour les tests
        )
        
        db.add(user)
        created_users.append({
            "email": user_data["email"],
            "password": user_data["password"],
            "role": user_data["role"]
        })
        logger.info(f"✓ Utilisateur créé: {user_data['email']} (rôle: {user_data['role']})")
    
    db.commit()
    return created_users


def main():
    """Fonction principale"""
    logger.info("=" * 60)
    logger.info("🌱 Création des utilisateurs de test (seeds)")
    logger.info("=" * 60)
    
    db = SessionLocal()
    try:
        # Créer les rôles et permissions
        logger.info("\n📋 Création des rôles et permissions...")
        roles = create_roles_and_permissions(db)
        
        # Créer les utilisateurs de test
        logger.info("\n👥 Création des utilisateurs de test...")
        users = create_test_users(db, roles)
        
        # Afficher le récapitulatif
        logger.info("\n" + "=" * 60)
        logger.info("✅ Seeds créés avec succès!")
        logger.info("=" * 60)
        logger.info("\n📝 Utilisateurs de test créés:\n")
        
        for user in users:
            logger.info(f"  • {user['email']}")
            logger.info("    Password: [REDACTED - see seed config]")
            logger.info(f"    Rôle: {user['role']}")
            logger.info("")
        
        logger.info("💡 Utilisez ces identifiants pour tester le contrôle d'accès")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"❌ Erreur lors de la création des seeds: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
