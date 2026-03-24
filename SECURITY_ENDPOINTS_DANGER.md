# Endpoints Sensibles (Guardés)

## Garde-fou central
- `app/security.py` : `require_dev_endpoint`
  - Bloque si `ENABLE_DEV_ENDPOINTS=false`
  - Autorise uniquement rôle `admin`

## Endpoints protégés par le garde-fou
- `POST /auth/test-email`
- `POST /notifications/test-email`
- `POST /notifications/test-sms`
- `POST /seed/create-users`
- `POST /seed/create-user`
- `POST /school/catalog/seed`
- `POST /school/admission/seed`

## Endpoints sensibles sous RBAC renforcé
- `POST /notifications/templates/{name_or_id}/send-test` (manager/admin)

## Politique recommandée
- Production: `ENABLE_DEV_ENDPOINTS=false`
- Pré-prod/dev: activer temporairement si besoin et journaliser les usages
