from __future__ import annotations

import os
from typing import Dict, List
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Role, Permission, RolePermission, User
from app.services.auth import hash_password

PERMISSIONS: List[str] = [
    "manage_users",
    "view_dashboard",
    "manage_notifications",
    "manage_calendar",
    "view_calendar",
    "manage_kb",
    "view_kb",
    "manage_admissions",
    "send_messages",
]

ROLE_PERMS: Dict[str, List[str]] = {
    "admin": PERMISSIONS,
    "manager": [
        "view_dashboard",
        "manage_notifications",
        "manage_calendar",
        "view_calendar",
        "manage_kb",
        "view_kb",
        "send_messages",
    ],
    "agent": [
        "view_calendar",
        "view_kb",
        "send_messages",
    ],
}


def upsert_role(db: Session, name: str) -> Role:
    r = db.query(Role).filter(Role.name == name).first()
    if r:
        return r
    r = Role(name=name)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def upsert_permission(db: Session, name: str) -> Permission:
    p = db.query(Permission).filter(Permission.name == name).first()
    if p:
        return p
    p = Permission(name=name)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def ensure_role_permissions(db: Session, role: Role, perms: List[Permission]) -> None:
    existing = db.query(RolePermission).filter(RolePermission.role_id == role.id).all()
    existing_ids = {(rp.permission_id) for rp in existing}
    for p in perms:
        if p.id not in existing_ids:
            db.add(RolePermission(role_id=role.id, permission_id=p.id))
    db.commit()


def upsert_admin_user(db: Session, email: str, password: str, role: Role) -> User:
    user = db.query(User).filter(User.email == email.lower()).first()
    if user:
        return user
    user = User(email=email.lower(), password_hash=hash_password(password), role_id=role.id, first_name="Admin", last_name="User")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def main():
    admin_email = os.getenv("ADMIN_EMAIL", "diabdullah113@gmail.com")
    admin_password = os.getenv("ADMIN_PASSWORD", "ChangeMe123!")

    db = SessionLocal()
    try:
        role_objs = {}
        for role_name in ("admin", "manager", "agent"):
            role_objs[role_name] = upsert_role(db, role_name)
        perm_objs = {}
        for p in PERMISSIONS:
            perm_objs[p] = upsert_permission(db, p)
        # Map role->perms
        for rn, perm_names in ROLE_PERMS.items():
            perms_list = [perm_objs[name] for name in perm_names]
            ensure_role_permissions(db, role_objs[rn], perms_list)
        # Admin user
        upsert_admin_user(db, admin_email, admin_password, role_objs["admin"])
        print("Seed completed: roles, permissions, admin user")
    finally:
        db.close()


if __name__ == "__main__":
    main()
