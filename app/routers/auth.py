from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Body, UploadFile, File, Response, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import User, Role, LoginAttempt
from ..services.auth import (
    verify_password,
    generate_and_send_otp,
    verify_otp_and_issue_tokens,
    rotate_refresh_and_issue_access,
    blacklist_token_jti,
    hash_password,
    get_current_user_id,
    bump_user_token_version,
)
from ..security import (
    security,
    require_dev_endpoint,
    enforce_allowed_origin,
    get_auth_cookie_settings,
)
from ..services.email import EmailService
from ..services.auth_security import (
    apply_login_jitter,
    check_login_allowed,
    register_login_failure,
    register_login_success,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    cookie_kwargs = get_auth_cookie_settings()
    response.set_cookie(
        key="access_token",
        value=access_token,
        max_age=int(settings.access_token_ttl),
        **cookie_kwargs,
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        max_age=int(settings.refresh_token_ttl),
        **cookie_kwargs,
    )


def _clear_auth_cookies(response: Response) -> None:
    cookie_kwargs = get_auth_cookie_settings()
    response.delete_cookie(key="access_token", **cookie_kwargs)
    response.delete_cookie(key="refresh_token", **cookie_kwargs)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    status: str
    user_id: int
    message: str


def _log_login_attempt(
    db: Session,
    *,
    email: str,
    ip_address: Optional[str],
    user_agent: str,
    success: bool,
    failure_reason: Optional[str],
) -> None:
    try:
        attempt = LoginAttempt(
            email=email,
            ip_address=ip_address,
            user_agent=user_agent,
            success=success,
            failure_reason=failure_reason,
        )
        db.add(attempt)
        db.commit()
    except Exception:
        db.rollback()


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    start_ts = time.perf_counter()
    email = payload.email.lower()
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent", "")[:500]
    failure_reason = "invalid_credentials"

    try:
        decision = check_login_allowed(ip_address, email)
        if not decision.allowed:
            failure_reason = decision.reason or "rate_limited"
            register_login_failure(ip_address, email)
            _log_login_attempt(
                db,
                email=email,
                ip_address=ip_address,
                user_agent=user_agent,
                success=False,
                failure_reason=failure_reason,
            )
            headers = {}
            if decision.retry_after:
                headers["Retry-After"] = str(decision.retry_after)
            raise HTTPException(status_code=429, detail="too_many_attempts", headers=headers or None)

        user = db.query(User).filter(User.email == email).first()
        if not user or not verify_password(user.password_hash, payload.password):
            register_login_failure(ip_address, email)
            _log_login_attempt(
                db,
                email=email,
                ip_address=ip_address,
                user_agent=user_agent,
                success=False,
                failure_reason="invalid_credentials",
            )
            raise HTTPException(status_code=401, detail="invalid_credentials")

        ok = generate_and_send_otp(user)
        if not ok:
            _log_login_attempt(
                db,
                email=email,
                ip_address=ip_address,
                user_agent=user_agent,
                success=False,
                failure_reason="otp_send_failed",
            )
            raise HTTPException(status_code=503, detail="otp_send_failed_or_not_configured")

        register_login_success(email)
        _log_login_attempt(
            db,
            email=email,
            ip_address=ip_address,
            user_agent=user_agent,
            success=True,
            failure_reason=None,
        )
        return LoginResponse(
            status="OTP_REQUIRED",
            user_id=int(user.id),
            message="Un code à 6 chiffres vous a été envoyé par email. Valide pendant 1 minute.",
        )
    except HTTPException as exc:
        if exc.status_code == 401:
            # Uniform message to prevent account enumeration.
            raise HTTPException(status_code=401, detail="invalid_credentials")
        raise
    finally:
        await apply_login_jitter(start_ts)


class VerifyOtpRequest(BaseModel):
    user_id: int
    otp: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


@router.post("/verify-otp", response_model=TokenResponse)
def verify_otp(payload: VerifyOtpRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    enforce_allowed_origin(request, require_for_all_unsafe=True)
    user = db.get(User, payload.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user_not_found")
    try:
        access, refresh = verify_otp_and_issue_tokens(db, user, payload.otp)
        # 15 min by default as configured
        from ..config import settings
        
        _set_auth_cookies(response, access, refresh)
        
        return TokenResponse(access_token="set_in_cookie", refresh_token="set_in_cookie", expires_in=int(settings.access_token_ttl))
    except ValueError as e:
        code = str(e)
        if code in ("otp_expired", "invalid_otp", "too_many_attempts", "otp_blocked"):
            raise HTTPException(status_code=401, detail=code)
        raise HTTPException(status_code=400, detail="otp_error")


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/refresh", response_model=TokenResponse)
def refresh(request: Request, response: Response):
    enforce_allowed_origin(request, require_for_all_unsafe=True)
    # Lire le refresh_token depuis le cookie
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=401, detail="no_refresh_token")
    
    try:
        access, new_refresh = rotate_refresh_and_issue_access(refresh_token)
        from ..config import settings
        
        _set_auth_cookies(response, access, new_refresh)
        
        return TokenResponse(access_token="set_in_cookie", refresh_token="set_in_cookie", expires_in=int(settings.access_token_ttl))
    except ValueError as e:
        code = str(e).strip().lower()
        if code in {"invalid_refresh_token", "token_revoked", "token_expired", "invalid_token_type"}:
            raise HTTPException(status_code=401, detail=code)
        raise HTTPException(status_code=401, detail="invalid_refresh_token")


@router.post("/logout")
def logout(request: Request, response: Response, creds = Depends(security), db: Session = Depends(get_db)):
    token = creds.credentials
    tenant_scope = str(getattr(request.state, "tenant_id", "") or "")
    user_id: Optional[int] = None
    try:
        user_id = get_current_user_id(token)
    except Exception:
        user_id = None
    if user_id is not None:
        try:
            bump_user_token_version(db, user_id, tenant_scope or None)
        except Exception:
            db.rollback()
    try:
        blacklist_token_jti(token)
    except Exception:
        pass

    refresh_token = request.cookies.get("refresh_token")
    if refresh_token:
        try:
            blacklist_token_jti(refresh_token)
        except Exception:
            pass

    _clear_auth_cookies(response)

    return {"logout": True}


@router.post("/logout-all")
def logout_all(request: Request, response: Response, creds = Depends(security), db: Session = Depends(get_db)):
    token = creds.credentials
    tenant_scope = str(getattr(request.state, "tenant_id", "") or "")
    try:
        user_id = get_current_user_id(token)
        bump_user_token_version(db, user_id, tenant_scope or None)
    except Exception:
        db.rollback()
    try:
        blacklist_token_jti(token)
    except Exception:
        pass

    _clear_auth_cookies(response)
    return {"logout_all": True}


class ProfileResponse(BaseModel):
    id: int
    tenant_id: Optional[str]
    email: str
    first_name: Optional[str]
    last_name: Optional[str]
    nom: Optional[str]  # Sera rempli avec first_name pour compatibilité frontend
    prenom: Optional[str]  # Sera rempli avec last_name pour compatibilité frontend
    telephone: Optional[str]  # Sera rempli avec phone pour compatibilité frontend
    role: Optional[str]
    roles: list[str] = []  # Liste des rôles pour le frontend
    avatar_url: Optional[str]
    created_at: datetime
    last_login: Optional[datetime]


@router.get("/profile", response_model=ProfileResponse)
def get_profile(creds = Depends(security), db: Session = Depends(get_db)):
    """Récupérer le profil de l'utilisateur connecté"""
    user_id = get_current_user_id(creds.credentials)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user_not_found")
    
    role_name = None
    if user.role_id:
        role = db.get(Role, user.role_id)
        role_name = role.name if role else None
    
    return ProfileResponse(
        id=int(user.id),
        tenant_id=str(getattr(user, "tenant_id", None)) if getattr(user, "tenant_id", None) else None,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        nom=user.first_name,  # Utiliser first_name de la DB
        prenom=user.last_name,  # Utiliser last_name de la DB
        telephone=user.phone,  # Utiliser phone de la DB
        role=role_name,
        roles=[role_name] if role_name else [],
        avatar_url=user.avatar_url,
        created_at=user.created_at,
        last_login=user.last_login
    )


class UpdateProfileRequest(BaseModel):
    nom: Optional[str] = None
    prenom: Optional[str] = None
    telephone: Optional[str] = None


@router.put("/profile")
def update_profile(payload: UpdateProfileRequest, creds = Depends(security), db: Session = Depends(get_db)):
    """Mettre à jour le profil de l'utilisateur connecté"""
    user_id = get_current_user_id(creds.credentials)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user_not_found")
    
    if payload.nom is not None:
        user.first_name = payload.nom  # Sauvegarder dans first_name de la DB
    if payload.prenom is not None:
        user.last_name = payload.prenom  # Sauvegarder dans last_name de la DB
    if payload.telephone is not None:
        user.phone = payload.telephone  # Sauvegarder dans phone de la DB
    
    db.commit()
    db.refresh(user)
    
    return {"success": True, "message": "Profil mis à jour avec succès"}


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/change-password")
def change_password(payload: ChangePasswordRequest, creds = Depends(security), db: Session = Depends(get_db)):
    """Changer le mot de passe de l'utilisateur connecté"""
    user_id = get_current_user_id(creds.credentials)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user_not_found")
    
    # Vérifier le mot de passe actuel
    if not verify_password(user.password_hash, payload.current_password):
        raise HTTPException(status_code=401, detail="invalid_current_password")
    
    # Hasher et sauvegarder le nouveau mot de passe
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    
    return {"success": True, "message": "Mot de passe changé avec succès"}


@router.post("/upload-avatar")
async def upload_avatar(file: UploadFile = File(...), creds = Depends(security), db: Session = Depends(get_db)):
    """Upload de la photo de profil de l'utilisateur"""
    user_id = get_current_user_id(creds.credentials)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user_not_found")
    
    # Vérifier le type de fichier
    allowed_types = ["image/jpeg", "image/png", "image/jpg", "image/webp"]
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="invalid_file_type")
    
    # Vérifier la taille (max 5MB)
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="file_too_large")
    
    # Créer le dossier uploads s'il n'existe pas
    upload_dir = "uploads/avatars"
    os.makedirs(upload_dir, exist_ok=True)
    
    # Générer un nom de fichier unique
    file_extension = file.filename.split(".")[-1] if "." in file.filename else "jpg"
    filename = f"{user_id}_{uuid.uuid4().hex}.{file_extension}"
    file_path = os.path.join(upload_dir, filename)
    
    # Sauvegarder le fichier
    with open(file_path, "wb") as f:
        f.write(content)
    
    # Supprimer l'ancien avatar si existant
    if user.avatar_url:
        # L'avatar_url est stocké comme /uploads/avatars/filename
        # Convertir en chemin système pour suppression
        old_file_path = user.avatar_url.lstrip('/')
        if os.path.exists(old_file_path):
            try:
                os.remove(old_file_path)
            except Exception:
                pass
    
    # Mettre à jour l'URL de l'avatar (stocker le chemin relatif web)
    avatar_url = f"/uploads/avatars/{filename}"
    user.avatar_url = avatar_url
    db.commit()
    
    return {
        "success": True,
        "message": "Avatar uploadé avec succès",
        "avatar_url": avatar_url
    }


class TestEmailRequest(BaseModel):
    to_email: EmailStr
    subject: str = "Test Email - AgentIA"
    message: str = "Ceci est un email de test envoyé après connexion."


@router.post("/test-email", dependencies=[Depends(require_dev_endpoint)])
def test_email(payload: TestEmailRequest, creds = Depends(security), db: Session = Depends(get_db)):
    """Endpoint de test pour envoyer un email avec logs détaillés.
    
    Nécessite une authentification. Permet de tester l'envoi d'emails
    et de vérifier les logs pour diagnostiquer les problèmes.
    """
    logger.info(f"Test email requested by authenticated user", extra={
        "to_email": payload.to_email,
        "subject": payload.subject
    })
    
    try:
        # Initialiser le service email
        email_service = EmailService()
        
        logger.info(f"Email service initialized", extra={
            "provider": email_service.provider,
            "from_email": email_service.from_email,
            "from_name": email_service.from_name,
            "is_configured": email_service.is_configured()
        })
        
        # Vérifier la configuration
        if not email_service.is_configured():
            logger.error("Email service not configured")
            raise HTTPException(
                status_code=503,
                detail="Email service not configured. Check EMAIL_PROVIDER and the selected provider credentials."
            )
        
        # Construire le HTML
        html_content = f"""
        <html>
            <body>
                <h1>{payload.subject}</h1>
                <p>{payload.message}</p>
                <hr>
                <p style="color: #666; font-size: 12px;">
                    Envoyé depuis AgentIA - Test après connexion<br>
                    Provider: {email_service.provider}<br>
                    From: {email_service.from_name} &lt;{email_service.from_email}&gt;
                </p>
            </body>
        </html>
        """
        
        logger.info(f"Attempting to send email via {email_service.provider}")
        
        # Envoyer l'email
        success = email_service.send_followup(
            to_email=payload.to_email,
            subject=payload.subject,
            html_content=html_content
        )
        
        if success:
            logger.info(f"Email sent successfully to {payload.to_email}", extra={
                "provider": email_service.provider,
                "to_email": payload.to_email,
                "subject": payload.subject
            })
            return {
                "success": True,
                "message": f"Email sent successfully to {payload.to_email}",
                "provider": email_service.provider,
                "from": f"{email_service.from_name} <{email_service.from_email}>"
            }
        else:
            logger.error(f"Failed to send email to {payload.to_email}", extra={
                "provider": email_service.provider,
                "to_email": payload.to_email
            })
            raise HTTPException(
                status_code=500,
                detail="Failed to send email. Check logs for details."
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error sending test email: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )
