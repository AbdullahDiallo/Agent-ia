"""Gestionnaires d'erreurs centralisés pour l'application."""
from typing import Optional, Dict, Any
from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from pydantic import ValidationError
from .logger import get_logger

logger = get_logger(__name__)


class AppException(Exception):
    """Exception de base pour l'application."""
    def __init__(self, message: str, status_code: int = 500, details: Optional[Dict[str, Any]] = None):
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(self.message)


class PersonNotFoundException(AppException):
    """Personne non trouvée."""
    def __init__(self, person_id: str):
        super().__init__(
            message=f"Person {person_id} not found",
            status_code=404,
            details={"person_id": person_id}
        )


class ConversationNotFoundException(AppException):
    """Conversation non trouvée."""
    def __init__(self, conversation_id: str):
        super().__init__(
            message=f"Conversation {conversation_id} not found",
            status_code=404,
            details={"conversation_id": conversation_id}
        )


class InvalidDataException(AppException):
    """Données invalides."""
    def __init__(self, message: str, field: Optional[str] = None):
        details = {"field": field} if field else {}
        super().__init__(
            message=message,
            status_code=400,
            details=details
        )


class DatabaseException(AppException):
    """Erreur de base de données."""
    def __init__(self, message: str, original_error: Optional[Exception] = None):
        details = {"original_error": str(original_error)} if original_error else {}
        super().__init__(
            message=message,
            status_code=500,
            details=details
        )


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """Gestionnaire pour les exceptions applicatives."""
    logger.error(
        f"Application exception: {exc.message}",
        extra={
            "extra_fields": {
                "path": request.url.path,
                "status_code": exc.status_code,
                "details": exc.details
            }
        }
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.message,
            "type": exc.__class__.__name__,
            **exc.details
        }
    )


async def validation_exception_handler(request: Request, exc: ValidationError) -> JSONResponse:
    """Gestionnaire pour les erreurs de validation Pydantic."""
    logger.warning(
        "Validation error",
        extra={
            "extra_fields": {
                "path": request.url.path,
                "errors": exc.errors()
            }
        }
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "Validation error",
            "errors": exc.errors()
        }
    )


async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """Gestionnaire pour les erreurs SQLAlchemy."""
    logger.error(
        "Database error",
        extra={
            "extra_fields": {
                "path": request.url.path,
                "error": str(exc)
            }
        },
        exc_info=True
    )
    
    # Erreur d'intégrité (contrainte unique, etc.)
    if isinstance(exc, IntegrityError):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": "Database integrity error",
                "type": "IntegrityError"
            }
        )
    
    # Autres erreurs DB
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Database error occurred",
            "type": "DatabaseError"
        }
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Gestionnaire pour les HTTPException FastAPI."""
    logger.warning(
        f"HTTP exception: {exc.detail}",
        extra={
            "extra_fields": {
                "path": request.url.path,
                "status_code": exc.status_code
            }
        }
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )


def safe_execute(func, *args, error_message: str = "Operation failed", **kwargs):
    """Exécute une fonction avec gestion d'erreur robuste.
    
    Args:
        func: Fonction à exécuter
        *args: Arguments positionnels
        error_message: Message d'erreur par défaut
        **kwargs: Arguments nommés
    
    Returns:
        Résultat de la fonction ou None en cas d'erreur
    
    Raises:
        AppException: En cas d'erreur
    """
    try:
        return func(*args, **kwargs)
    except SQLAlchemyError as e:
        logger.error(
            f"Database error in safe_execute: {error_message}",
            extra={"extra_fields": {"error": str(e)}},
            exc_info=True
        )
        raise DatabaseException(error_message, e)
    except ValidationError as e:
        logger.error(
            f"Validation error in safe_execute: {error_message}",
            extra={"extra_fields": {"errors": e.errors()}},
            exc_info=True
        )
        raise InvalidDataException(error_message)
    except Exception as e:
        logger.error(
            f"Unexpected error in safe_execute: {error_message}",
            extra={"extra_fields": {"error": str(e)}},
            exc_info=True
        )
        raise AppException(error_message, details={"error": str(e)})
