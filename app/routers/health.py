"""
Health check endpoint pour monitoring
"""
from fastapi import APIRouter
from sqlalchemy import text
from ..db import SessionLocal
from ..redis_client import get_redis

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    """Health check endpoint pour vérifier l'état de l'application."""
    status = {
        "status": "healthy",
        "database": "unknown",
        "redis": "unknown"
    }
    
    # Check database
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        status["database"] = "healthy"
    except Exception as e:
        status["database"] = f"unhealthy: {str(e)[:100]}"
        status["status"] = "degraded"
    
    # Check Redis
    try:
        r = get_redis()
        r.ping()
        status["redis"] = "healthy"
    except Exception as e:
        status["redis"] = f"unhealthy: {str(e)[:100]}"
        status["status"] = "degraded"
    
    return status


@router.get("/health/ready")
async def readiness_check():
    """Readiness check pour Kubernetes."""
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        return {"ready": True}
    except Exception:
        return {"ready": False}, 503


@router.get("/health/live")
async def liveness_check():
    """Liveness check pour Kubernetes."""
    return {"alive": True}
