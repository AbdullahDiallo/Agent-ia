# Tenant Scoping Checklist (Fail-Closed)

## Enforcement controls
- `app/services/tenant_context.py`
  - Middleware sets `request.state.tenant_id` from JWT.
  - If `ENFORCE_TENANT_SCOPE=true`, any non-public path without tenant is rejected with `403 missing_tenant_scope`.
- `app/db.py`
  - `get_db(request)` enforces tenant guard by default.
  - Session-level automatic query filtering via `with_loader_criteria` on all models with `tenant_id`.
  - `before_flush` blocks cross-tenant writes (`PermissionError: cross_tenant_write_forbidden`).
- `app/services/kb.py`
  - Business writes derive tenant from scoped DB session (`db.info.tenant_id`) in fail-closed mode.
  - Cross-tenant references (person/conversation mismatch) raise `cross_tenant_reference_forbidden`.
- `app/services/llm.py`
  - Persona/argument docs loading resolves tenant from scoped DB session (or validated tenant in session state).
- `app/routers/voice.py`
  - WS token now carries `tenant_id`; media stream session opens DB with token tenant scope.

## Explicit public allowlist
- Source: `TENANT_PUBLIC_PATHS` (default in `app/config.py`, documented in `.env.example`).
- Current allowlist includes:
  - `/health`, `/health/ready`, `/health/live`
  - `/auth/login`, `/auth/verify-otp`, `/auth/refresh`
  - `/chat/chat`, `/chat/message`
  - `/sms/incoming`, `/whatsapp/incoming`, `/webhooks/meta/whatsapp`
  - `/email/incoming`, `/voice/token`, `/voice/outbound`, `/voice/incoming`, `/voice/recording-status`, `/events/call-status`
  - `/school/public`, `/school/contact-requests`
  - `/uploads`

## Router coverage status
- `app/routers/auth.py`: Scoped via middleware + `get_db`; public auth bootstrap endpoints explicitly allowlisted.
- `app/routers/users.py`: Scoped via middleware + `get_db`.
- `app/routers/agents.py`: Scoped via middleware + `get_db`.
- `app/routers/dashboard.py`: Scoped via middleware + `get_db`.
- `app/routers/knowledge_base.py`: Scoped via middleware + `get_db`.
- `app/routers/manual_intervention.py`: Scoped via middleware + `get_db`.
- `app/routers/notifications.py`: Scoped via middleware + `get_db`.
- `app/routers/calendar.py`: Scoped via middleware + `get_db` (background helper uses tenant-bound session).
- `app/routers/school_people.py`: Scoped via middleware + `get_db`.
- `app/routers/school_catalog.py`: Scoped via middleware + `get_db`.
- `app/routers/school_admission.py`: Scoped via middleware + `get_db`.
- `app/routers/persona.py`: Scoped via middleware + `get_db`.
- `app/routers/rag.py`: Scoped via middleware + `get_db`.
- `app/routers/monitoring.py`: Scoped via middleware + `get_db`; health probe uses `open_db_session(allow_unscoped=True)` for `SELECT 1` only.
- `app/routers/ai_features.py`: Scoped via middleware + `get_db`.
- `app/routers/voice_recording.py`: Public webhook path allowlisted, DB operations still tenant-scoped through `get_db`.
- `app/routers/sms.py`, `app/routers/whatsapp.py`, `app/routers/email_handler.py`, `app/routers/events.py`, `app/routers/voice.py`, `app/routers/chat.py`: public ingestion/widget paths allowlisted; persistence uses tenant-scoped DB sessions.
- `app/routers/health.py`: explicitly public, no tenant business data exposure.

## Test proof
- `tests/test_tenant_context.py`
  - private route without tenant -> `403 missing_tenant_scope`
  - representative private route set (`/dashboard/*`, `/calendar/*`, `/notifications/*`, `/kb/*`, `/school/*`, `/users`, `/agents`, `/ai/*`, `/monitoring/*`) -> fail-closed `403`
  - allowlisted route -> default tenant applied
  - token tenant overrides allowlisted default
  - invalid token -> `401 invalid_token`
- `tests/test_tenant_scoping.py`
  - cross-tenant read isolation (`tenant-a` cannot read `tenant-b`)
  - cross-tenant write denied (`cross_tenant_write_forbidden`)
- `tests/test_webhook_security.py`
  - signed webhook accepted, invalid signatures rejected
  - anti-replay enforced
  - tenant injection via payload/header ignored (trusted tenant only from request context)

## Validation command
- `.venv312/bin/python -m pytest -q --cov=app --cov-report=term-missing --cov-report=xml --cov-fail-under=10`
- Last execution (2026-02-06): `36 passed`, coverage `27.86%`.
