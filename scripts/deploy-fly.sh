#!/usr/bin/env bash

set -euo pipefail

API_APP="${API_APP:-salmaai-api}"
DASHBOARD_APP="${DASHBOARD_APP:-salmaai-dashboard}"
REGION="${REGION:-cdg}"
BACKEND_CONFIG="${BACKEND_CONFIG:-fly.toml}"
FRONTEND_DIR="${FRONTEND_DIR:-front/dashboard}"
FRONTEND_CONFIG="${FRONTEND_CONFIG:-front/dashboard/fly.toml}"
ENV_FILE="${ENV_FILE:-.env}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[deploy]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
err()  { echo -e "${RED}[error]${NC} $*" >&2; }

usage() {
  cat <<EOF
Usage: $0 {setup|secrets|migrate|backend|frontend|all}

Commands:
  setup     Create Fly apps if missing
  secrets   Import selected backend secrets from ${ENV_FILE}
  migrate   Run Alembic migrations on ${API_APP}
  backend   Deploy backend
  frontend  Deploy frontend
  all       Deploy backend, then frontend

Environment overrides:
  API_APP, DASHBOARD_APP, REGION, BACKEND_CONFIG, FRONTEND_DIR, FRONTEND_CONFIG, ENV_FILE
EOF
}

check_fly() {
  if ! command -v fly >/dev/null 2>&1; then
    err "flyctl not installed. Install: curl -L https://fly.io/install.sh | sh"
    exit 1
  fi
  if ! fly auth whoami >/dev/null 2>&1; then
    err "Not logged in. Run: fly auth login"
    exit 1
  fi
}

ensure_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    err "Missing file: $path"
    exit 1
  fi
}

app_exists() {
  local app="$1"
  fly apps list --json 2>/dev/null | grep -q "\"Name\":\"${app}\""
}

require_app() {
  local app="$1"
  if ! app_exists "$app"; then
    err "Fly app not found: $app"
    err "Run '$0 setup' after adding billing on Fly, or set API_APP/DASHBOARD_APP to an existing app."
    exit 1
  fi
}

ensure_app() {
  local app="$1"
  if app_exists "$app"; then
    log "App exists: $app"
    return
  fi
  log "Creating Fly app: $app"
  fly apps create "$app"
}

cmd_setup() {
  check_fly
  ensure_file "$BACKEND_CONFIG"
  ensure_file "$FRONTEND_CONFIG"

  ensure_app "$API_APP"
  ensure_app "$DASHBOARD_APP"

  log "Setup complete"
  log "Next: $0 secrets"
  log "Then: $0 all"
}

cmd_secrets() {
  check_fly
  ensure_file "$ENV_FILE"
  require_app "$API_APP"

  local keys=(
    DATABASE_URL
    JWT_PUBLIC_KEY
    JWT_PRIVATE_KEY
    JWT_AUDIENCE
    JWT_ISSUER
    OPENAI_API_KEY
    GPT5_API_KEY
    DEEPGRAM_API_KEY
    ELEVENLABS_API_KEY
    ELEVENLABS_VOICE_ID
    TWILIO_ACCOUNT_SID
    TWILIO_AUTH_TOKEN
    TWILIO_API_KEY
    TWILIO_API_SECRET
    TWILIO_TWIML_APP_SID
    TWILIO_PHONE_NUMBER
    TWILIO_VOICE_NUMBER
    TWILIO_WHATSAPP_NUMBER
    META_API_VERSION
    META_WHATSAPP_PHONE_NUMBER_ID
    META_WHATSAPP_ACCESS_TOKEN
    META_WHATSAPP_VERIFY_TOKEN
    META_WHATSAPP_APP_SECRET
    EMAIL_PROVIDER
    MAIL_MAILER
    MAIL_HOST
    MAIL_PORT
    MAIL_USERNAME
    MAIL_PASSWORD
    MAIL_FROM_ADDRESS
    MAIL_FROM_NAME
    FROM_EMAIL
    FROM_NAME
    BREVO_API_KEY
    SENDGRID_API_KEY
    GMAIL_SMTP_USER
    GMAIL_SMTP_PASS
    GMAIL_SMTP_HOST
    GMAIL_SMTP_PORT
    GOOGLE_CREDENTIALS_JSON_BASE64
    GOOGLE_CALENDAR_ID
    PUBLIC_BASE_URL
    PUBLIC_WS_URL
    ALLOWED_ORIGINS
    DEFAULT_TENANT_ID
    REDIS_URL
    REDIS_PASSWORD
    KMS_KEY_ID
    APP_ENCRYPTION_KEY_BASE64
    WIDGET_PUBLIC_TOKEN
    EMAIL_WEBHOOK_SECRET
    MAILGUN_WEBHOOK_SIGNING_KEY
    STRIPE_SECRET_KEY
    STRIPE_WEBHOOK_SECRET
    STRIPE_PUBLISHABLE_KEY
    SENTRY_DSN
    SENTRY_ENV
    SENTRY_TRACES_SAMPLE_RATE
    SENTRY_ORG
    SENTRY_PROJECT
    SENTRY_AUTH_TOKEN
    ADMIN_ALERT_EMAIL
  )

  log "Importing backend secrets from ${ENV_FILE} into ${API_APP}"
  python3 - "$ENV_FILE" "${keys[@]}" <<'PY' | fly secrets import -a "$API_APP"
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
allowed = set(sys.argv[2:])

for raw_line in env_path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    if key not in allowed:
        continue
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    print(f"{key}={value}")
PY
  log "Secrets synced for ${API_APP}"
  warn "Non-secret runtime defaults stay in fly.toml"
}

cmd_migrate() {
  check_fly
  ensure_file "$BACKEND_CONFIG"
  require_app "$API_APP"

  log "Running migrations on ${API_APP}"
  fly console -C "python -m alembic upgrade head" -a "$API_APP"
}

cmd_backend() {
  check_fly
  ensure_file "$BACKEND_CONFIG"
  require_app "$API_APP"

  log "Deploying backend: ${API_APP}"
  fly deploy --app "$API_APP" --config "$BACKEND_CONFIG"
  log "Backend deployed: https://${API_APP}.fly.dev"
}

cmd_frontend() {
  check_fly
  ensure_file "$FRONTEND_CONFIG"
  require_app "$DASHBOARD_APP"

  log "Deploying frontend: ${DASHBOARD_APP}"
  fly deploy --app "$DASHBOARD_APP" --config "$FRONTEND_CONFIG"
  log "Frontend deployed: https://${DASHBOARD_APP}.fly.dev"
}

cmd_all() {
  cmd_backend
  cmd_frontend
  log "Deployment complete"
  log "API: https://${API_APP}.fly.dev"
  log "Dashboard: https://${DASHBOARD_APP}.fly.dev"
}

case "${1:-help}" in
  setup)    cmd_setup ;;
  secrets)  cmd_secrets ;;
  migrate)  cmd_migrate ;;
  backend)  cmd_backend ;;
  frontend) cmd_frontend ;;
  all)      cmd_all ;;
  help|-h|--help) usage ;;
  *)
    usage
    exit 1
    ;;
esac
