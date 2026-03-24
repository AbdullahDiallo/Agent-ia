from fastapi import FastAPI
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
import os
from .config import settings
from .middleware import pii_safe_logging_middleware, rate_limit_middleware, exception_logging_middleware, security_control_middleware, security_headers_middleware
from .error_handlers import (
    AppException, app_exception_handler, validation_exception_handler,
    sqlalchemy_exception_handler, http_exception_handler
)
from fastapi import HTTPException
from .routers.voice import router as voice_router
from .routers.voice_recording import router as voice_recording_router
from .routers.manual_intervention import router as manual_intervention_router
from .routers.events import router as events_router
from .routers.chat import router as chat_router
from .routers.gdpr import router as gdpr_router
from .routers.knowledge_base import router as knowledge_base_router
from .routers.calendar import router as calendar_router
from .routers.notifications import router as notifications_router, webhook_router as notification_webhook_router
from .routers.dashboard import router as dashboard_router
from .routers.auth import router as auth_router
from .routers.whatsapp import router as whatsapp_router
from .routers.sms import router as sms_router
from .routers.rag import router as rag_router
from .routers.health import router as health_router
from .routers.email_handler import router as email_handler_router
from .routers.ai_features import router as ai_router
from .routers.seed import router as seed_router
from .routers.users import router as users_router
from .routers.agents import router as agents_router
from .routers.persona import router as persona_router
from .routers.monitoring import router as monitoring_router
from .routers.school_catalog import router as school_catalog_router
from .routers.school_admission import router as school_admission_router
from .routers.school_people import router as school_people_router
from .routers.billing import router as billing_router
from .routers.widget_auth import router as widget_auth_router
from .db import engine, get_missing_required_columns, get_missing_tenant_columns
from sqlalchemy import text
from .scheduler import start_scheduler, stop_scheduler
from .services.provider_config import (
    get_email_provider_status,
    get_whatsapp_provider_status,
    validate_provider_configuration,
)
from .services.tenant_context import tenant_context_middleware
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    missing_tenant_columns = get_missing_tenant_columns()
    if missing_tenant_columns:
        missing_csv = ", ".join(missing_tenant_columns)
        raise RuntimeError(
            "Tenant schema mismatch: missing `tenant_id` column on tables "
            f"[{missing_csv}]. Apply DB migrations before starting the app "
            "(example: `venv/bin/alembic upgrade head`)."
        )
    missing_required_columns = get_missing_required_columns(
        {
            "events": {"rendezvous_id"},
            "emails_logs": {
                "dedupe_key",
                "recipient",
                "provider_name",
                "direction",
                "last_error",
                "sent_at",
                "delivered_at",
                "failed_at",
            },
            "sms_logs": {
                "dedupe_key",
                "recipient",
                "provider_name",
                "direction",
                "last_error",
                "sent_at",
                "delivered_at",
                "failed_at",
            },
        }
    )
    if missing_required_columns:
        details = ", ".join(
            f"{table}[{', '.join(columns)}]" for table, columns in sorted(missing_required_columns.items())
        )
        raise RuntimeError(
            "Schema mismatch: missing required columns for the rendezvous/notifications hardening rollout: "
            f"{details}. Apply DB migrations before starting the app "
            "(example: `venv/bin/alembic upgrade head`)."
        )
    validate_provider_configuration(settings)
    # Startup
    start_scheduler()
    yield
    # Shutdown
    stop_scheduler()

app = FastAPI(
    default_response_class=ORJSONResponse,
    title="Salma School Assistant API",
    version="0.1.0",
    lifespan=lifespan
)

# Init Sentry (backend) if configured
if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_env or settings.env,
        integrations=[FastApiIntegration()],
        traces_sample_rate=settings.sentry_traces_sample_rate,
    )

# Enregistrer les gestionnaires d'erreurs
app.add_exception_handler(AppException, app_exception_handler)
app.add_exception_handler(ValidationError, validation_exception_handler)
app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)

# Configuration CORS pour cookies httpOnly
env_lower = (settings.env or "").lower()

# En dev, utiliser localhost par défaut si ALLOWED_ORIGINS n'est pas défini
if env_lower not in ("prod", "production"):
    if not settings.allowed_origins or settings.allowed_origins == "*":
        origins = ["http://localhost:5173", "http://localhost:3000", "http://localhost:5174"]
    else:
        origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
    allow_credentials = True
else:
    # En production, exiger des origins explicites
    if not settings.allowed_origins or settings.allowed_origins == "*":
        raise RuntimeError("ALLOWED_ORIGINS must be set to explicit origins in production")
    origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
    allow_credentials = True

# Request middleware order (execution order = reverse of registration):
# outermost exception catcher -> request logging -> rate limit -> security -> tenant context
app.middleware("http")(tenant_context_middleware)
app.middleware("http")(security_control_middleware)
app.middleware("http")(rate_limit_middleware)
app.middleware("http")(security_headers_middleware)
app.middleware("http")(pii_safe_logging_middleware)
app.middleware("http")(exception_logging_middleware)

# Keep CORS outermost so even early 4xx/5xx responses include CORS headers.
_cors_allowed_headers = ["Content-Type", "Authorization", "X-Widget-Token", "X-Widget-Session", "X-Requested-With", "Accept"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=allow_credentials,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=_cors_allowed_headers,
    expose_headers=["Set-Cookie", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-Tenant-Id"],
)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(email_handler_router)
app.include_router(knowledge_base_router)
app.include_router(calendar_router)
app.include_router(dashboard_router)
app.include_router(notifications_router)
app.include_router(notification_webhook_router)
app.include_router(chat_router)
app.include_router(events_router)
app.include_router(voice_router)
app.include_router(voice_recording_router)
app.include_router(manual_intervention_router)
app.include_router(whatsapp_router)
app.include_router(sms_router)
app.include_router(rag_router)
app.include_router(ai_router)
if settings.enable_dev_endpoints:
    app.include_router(seed_router)
app.include_router(users_router)
app.include_router(agents_router)
app.include_router(persona_router)
app.include_router(monitoring_router)
app.include_router(school_catalog_router)
app.include_router(school_admission_router)
app.include_router(school_people_router)
app.include_router(billing_router)
app.include_router(widget_auth_router)

# Monter le dossier uploads pour servir les fichiers statiques
if not os.path.exists("uploads"):
    os.makedirs("uploads")
if not os.path.exists("uploads/avatars"):
    os.makedirs("uploads/avatars")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

@app.get("/health")
def health():
    return {"status": "ok", "env": settings.env}

@app.get("/health/ready")
def health_ready():
    checks: dict[str, object] = {"env": settings.env}
    # Verify critical env variables are present
    required_envs = [
        ("DATABASE_URL", bool(settings.database_url)),
        ("JWT_PUBLIC_KEY", bool(settings.jwt_public_key)),
        ("JWT_PRIVATE_KEY", bool(settings.jwt_private_key)),
        ("JWT_AUDIENCE", bool(settings.jwt_audience)),
        ("JWT_ISSUER", bool(settings.jwt_issuer)),
    ]
    env_ok = all(v for _, v in required_envs)
    checks["env_vars"] = {k: v for k, v in required_envs}
    # DB ping
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    checks["database"] = db_ok
    try:
        provider_report = {
            "whatsapp": get_whatsapp_provider_status(settings),
            "email": get_email_provider_status(settings),
        }
        checks["providers"] = {
            key: {
                "provider": value["provider"],
                "configured": value["configured"],
                "ignored_credentials": value["ignored_credentials"],
            }
            for key, value in provider_report.items()
        }
    except Exception as exc:
        checks["providers"] = {"status": "invalid", "detail": str(exc)}
    status_val = "ready" if (env_ok and db_ok) else "degraded"
    if isinstance(checks.get("providers"), dict) and checks["providers"].get("status") == "invalid":
        status_val = "degraded"
    return {"status": status_val, **checks}
