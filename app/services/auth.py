from __future__ import annotations

import os
import time
import hmac
import hashlib
import secrets
from typing import Optional, Tuple, List
import logging

import jwt
from fastapi import HTTPException
from argon2 import PasswordHasher
from sqlalchemy.orm import Session

from ..config import settings
from ..models import User, Role, Permission, RolePermission
from ..redis_client import get_redis
from .email import EmailService

# Utiliser un namespace explicite pour apparaître dans logs/agentia.log
logger = logging.getLogger("agentia.app.services.auth")

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def get_current_user_id(token: str) -> int:
    """
    Extrait l'ID utilisateur du token JWT.
    
    Args:
        token: Le token JWT
        
    Returns:
        L'ID de l'utilisateur
        
    Raises:
        HTTPException: Si le token est invalide
    """
    try:
        payload = jwt.decode(
            token, 
            settings.jwt_public_key, 
            algorithms=["RS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer
        )
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="invalid_token")
        return int(user_id)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token_expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="invalid_token")


def verify_password(hash_value: str, password: str) -> bool:
    try:
        return _ph.verify(hash_value, password)
    except Exception as e:
        # Log unexpected errors for debugging
        from ..logger import get_logger
        logger = get_logger(__name__)
        logger.warning(f"Password verification error: {type(e).__name__}")
        return False


def _user_permissions(db: Session, role_id: Optional[int]) -> List[str]:
    if not role_id:
        return []
    q = (
        db.query(Permission.name)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .filter(RolePermission.role_id == role_id)
    )
    return [n for (n,) in q.all()]


def _issue_jwt(
    sub: str,
    email: str,
    roles: List[str],
    permissions: List[str],
    tenant_id: str,
    token_version: int,
    ttl: int,
    token_type: str,
) -> Tuple[str, str]:
    now = int(time.time())
    exp = now + int(ttl)
    jti = secrets.token_hex(16)
    payload = {
        "sub": str(sub),
        "email": email,
        "roles": roles,
        "permissions": permissions,
        "tenant_id": tenant_id,
        "tv": int(token_version),
        "aud": settings.jwt_audience,
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": exp,
        "jti": jti,
        "typ": token_type,
    }
    token = jwt.encode(payload, settings.jwt_private_key, algorithm="RS256")
    return token, jti


def _store_blacklist(jti: str, ttl: int) -> None:
    r = get_redis()
    r.setex(f"bl:{jti}", ttl, b"1")


def _otp_keys(user_id: int) -> Tuple[str, str, str]:
    return f"otp:{user_id}", f"attempts:{user_id}", f"otpblock:{user_id}"


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def generate_and_send_otp(user: User) -> bool:
    logger.info(f"Generating OTP for user", extra={"user_id": user.id, "email": user.email})
    
    if settings.disable_otp:
        logger.info("OTP disabled in settings, skipping")
        return True
    
    code = f"{secrets.randbelow(1000000):06d}"
    logger.info(f"OTP code generated", extra={"user_id": user.id, "code_length": len(code)})
    
    r = get_redis()
    key, attempts_key, block_key = _otp_keys(int(user.id))
    
    if r.get(block_key):
        logger.warning(f"User blocked from receiving OTP", extra={"user_id": user.id})
        return False
    
    # OTP valide pendant 1 minute (60 secondes)
    r.setex(key, 60, _sha256(code).encode())
    r.delete(attempts_key)
    logger.info(f"OTP stored in Redis with 1min TTL", extra={"user_id": user.id})
    
    # Utiliser le template professionnel otp_verification
    from ..db import open_db_session
    from ..services.templates import get_email_template, render_email_template
    
    db_session = open_db_session(str(getattr(user, "tenant_id", None) or settings.default_tenant_id))
    try:
        tpl = get_email_template(db_session, name_or_id="otp_verification")
        if tpl:
            context = {
                "user_name": user.email.split('@')[0].capitalize(),
                "otp_code": code,
                "company_name": "Aelixoria AI",
                "company_phone": "+33 1 23 45 67 89",
            }
            rendered = render_email_template(tpl, context)
            subject = rendered.get("subject", "Votre code OTP - AgentIA")
            html = rendered.get("html", f"<p>Votre code: {code}</p>")
        else:
            # Fallback si template non trouvé
            subject = "Votre code OTP - AgentIA"
            html = f"<p>Votre code: <strong>{code}</strong></p>"
    finally:
        db_session.close()
    
    es = EmailService()
    logger.info(f"Email service initialized", extra={
        "provider": es.provider,
        "from_email": es.from_email,
        "is_configured": es.is_configured()
    })
    
    if es.is_configured():
        
        logger.info(f"Attempting to send OTP email", extra={
            "to_email": user.email,
            "provider": es.provider
        })
        
        success = bool(es.send_followup(user.email, subject, html))
        
        if success:
            logger.info(f"OTP email sent successfully", extra={
                "user_id": user.id,
                "email": user.email,
                "provider": es.provider
            })
        else:
            logger.error(f"Failed to send OTP email", extra={
                "user_id": user.id,
                "email": user.email,
                "provider": es.provider
            })
        
        return success
    else:
        logger.warning(f"Email service not configured, OTP not sent", extra={"user_id": user.id})
        return False


def verify_otp_and_issue_tokens(db: Session, user: User, otp: str) -> Tuple[str, str]:
    tenant_id = str(getattr(user, "tenant_id", None) or settings.default_tenant_id)
    token_version = int(getattr(user, "token_version", 0) or 0)
    if settings.disable_otp:
        role_name = None
        if user.role_id:
            role = db.get(Role, user.role_id)
            role_name = role.name if role else None
        roles = [role_name] if role_name else []
        perms = _user_permissions(db, user.role_id)
        access, _ = _issue_jwt(str(user.id), user.email, roles, perms, tenant_id, token_version, settings.access_token_ttl, "access")
        refresh, _ = _issue_jwt(str(user.id), user.email, roles, perms, tenant_id, token_version, settings.refresh_token_ttl, "refresh")
        return access, refresh
    r = get_redis()
    key, attempts_key, block_key = _otp_keys(int(user.id))
    if r.get(block_key):
        raise ValueError("otp_blocked")
    hv = r.get(key)
    if not hv:
        raise ValueError("otp_expired")
    attempts = int(r.get(attempts_key) or b"0")
    if attempts >= 3:
        r.setex(block_key, 600, b"1")
        r.delete(key)
        r.delete(attempts_key)
        raise ValueError("too_many_attempts")
    if not hmac.compare_digest(hv.decode(), _sha256(otp)):
        r.incr(attempts_key)
        raise ValueError("invalid_otp")
    r.delete(key)
    r.delete(attempts_key)
    role_name = None
    if user.role_id:
        role = db.get(Role, user.role_id)
        role_name = role.name if role else None
    roles = [role_name] if role_name else []
    perms = _user_permissions(db, user.role_id)
    access, access_jti = _issue_jwt(
        str(user.id),
        user.email,
        roles,
        perms,
        tenant_id,
        token_version,
        settings.access_token_ttl,
        "access",
    )
    refresh, refresh_jti = _issue_jwt(
        str(user.id),
        user.email,
        roles,
        perms,
        tenant_id,
        token_version,
        settings.refresh_token_ttl,
        "refresh",
    )
    return access, refresh


def rotate_refresh_and_issue_access(refresh_token: str) -> Tuple[str, str]:
    try:
        payload = jwt.decode(
            refresh_token,
            settings.jwt_public_key,
            algorithms=["RS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
        )
    except Exception:
        raise ValueError("invalid_token")
    if payload.get("typ") != "refresh":
        raise ValueError("invalid_token_type")
    sub = payload.get("sub")
    tenant_id = payload.get("tenant_id") or settings.default_tenant_id
    if not sub:
        raise ValueError("invalid_refresh_token")
    try:
        user_id = int(str(sub))
    except Exception:
        raise ValueError("invalid_refresh_token")

    from ..db import open_db_session

    db = open_db_session(allow_unscoped=True)
    try:
        user = db.get(User, user_id)
        if not user:
            raise ValueError("invalid_refresh_token")
        if str(getattr(user, "tenant_id", "")) != str(tenant_id):
            raise ValueError("invalid_refresh_token")
        claim_token_version = int(payload.get("tv", -1))
        current_token_version = int(getattr(user, "token_version", 0) or 0)
        if claim_token_version != current_token_version:
            raise ValueError("token_revoked")
    finally:
        db.close()

    jti = payload.get("jti")
    if jti:
        r = get_redis()
        if r.exists(f"bl:{jti}"):
            raise ValueError("token_revoked")
        exp = int(payload.get("exp", int(time.time())))
        ttl = max(1, exp - int(time.time()))
        _store_blacklist(jti, ttl)
    email = payload.get("email")
    roles = payload.get("roles") or []
    perms = payload.get("permissions") or []
    token_version = int(payload.get("tv", 0))
    access, _ = _issue_jwt(sub, email, roles, perms, tenant_id, token_version, settings.access_token_ttl, "access")
    new_refresh, _ = _issue_jwt(sub, email, roles, perms, tenant_id, token_version, settings.refresh_token_ttl, "refresh")
    return access, new_refresh


def blacklist_token_jti(token: str) -> None:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_public_key,
            algorithms=["RS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
        )
    except Exception:
        return
    jti = payload.get("jti")
    if not jti:
        return
    exp = int(payload.get("exp", int(time.time())))
    ttl = max(1, exp - int(time.time()))
    _store_blacklist(jti, ttl)


def bump_user_token_version(db: Session, user_id: int, tenant_id: str | None = None) -> int:
    user = db.get(User, int(user_id))
    if not user:
        raise ValueError("user_not_found")
    if tenant_id and str(getattr(user, "tenant_id", "")) != str(tenant_id):
        raise ValueError("invalid_tenant_scope")
    next_version = int(getattr(user, "token_version", 0) or 0) + 1
    user.token_version = next_version
    db.add(user)
    db.commit()
    return next_version
