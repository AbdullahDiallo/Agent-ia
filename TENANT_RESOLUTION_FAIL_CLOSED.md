# Tenant Resolution Fail-Closed (Public/Webhooks)

Date: 2026-02-06

## Principe
- Aucun fallback `default_tenant_id` sur endpoints publics/webhooks critiques.
- Le tenant est determine via table `tenant_channels`:
  - `provider` (ex: `meta_whatsapp`)
  - `provider_key`
  - `token_hash` (SHA-256 du secret partage)
  - `tenant_id`

## Entrees requises (public/webhook)
- `provider_key` (header `X-Provider-Key` ou query `provider_key`)
- `tenant_token` (header `X-Tenant-Token` ou query `tenant_token`)
- `provider`:
  - explicite (`X-Provider` / query `provider`) ou derive du path.

## Rejets (fail-closed)
- provider inconnu: `403 unknown_provider_key`
- token invalide: `403 invalid_tenant_token`
- injection cross-tenant (`tenant_id` force et different): `403 cross_tenant_injection`
- credentials manquants: `403 missing_provider_key` / `403 missing_tenant_token`

## Validation
- `tests/test_tenant_context.py`
- `tests/test_tenant_webhook_resolution_integration.py`
