#!/usr/bin/env bash
# ============================================
# EXEMPLE — Commande fly secrets set complète
# ============================================
# Ce fichier est un EXEMPLE. Ne le committez JAMAIS avec de vraies valeurs.
# Copiez-le, remplissez vos valeurs, et exécutez-le une seule fois.
#
# Usage:
#   cp scripts/example-fly-secrets.sh scripts/my-secrets.sh
#   nano scripts/my-secrets.sh   # remplir vos vraies valeurs
#   chmod +x scripts/my-secrets.sh
#   ./scripts/my-secrets.sh

fly secrets set -a agentia-api \
  DATABASE_URL="postgresql+psycopg://postgres:Diallo972182@db.awlztayaxmwuudadaxfy.supabase.co:5432/postgres" \
  \
  JWT_PUBLIC_KEY="-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA2x5F
kR3rYLOHNpZ6xJGGGST8LzOQnVBN3Y6bFOPCGRMaWxahXspn
qR7KzGpAtiSdPTaFJk2bNFLzDhRVpUGRsHEjKL0B5pkHMRQFd
8GxV0YBzHNpkSdFjKLpR8TxVz5GNpqMFjHNpkSdFjKLpR8TxV
z5GNpqMFjHNpkSdFjKLpR8TxVz5GNpqMFjHNpkSdFjKLpR8Tx
Vz5GNpqMFjHNpkSdFjKLpR8TxVz5GNpqMFjHNpkSdFjKLpR8T
QIDAQAB
-----END PUBLIC KEY-----" \
  \
  JWT_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBA
QDbHkWRHetgs4c2lnrEkYYZJPwvM5CdUE3djpsU48IZExpbFqF
eymepHsrMakC2JJ09NoUmTZs0UvMOFFWlQZGwcSMovQHmmQcxF
AV3wbFXRgHMc2mRJ0WMoulHxPFXPkY2mowWMc2mRJ0WMoulHxP
FXPkY2mowWMc2mRJ0WMoulHxPFXPkY2mowWMc2mRJ0WMoulHxP
FXPkY2mowWMc2mRJ0WMoulHxPFXPkY2mowWMc2mRJ0WMoulHxP
... (beaucoup plus de lignes) ...
FXPkY2mowWMc2mRJ0WMoulHxPFXPkY2mo=
-----END PRIVATE KEY-----" \
  \
  JWT_AUDIENCE="voice-agent" \
  JWT_ISSUER="https://agentia-api.fly.dev" \
  \
  OPENAI_API_KEY="sk-proj-abc123def456ghi789..." \
  DEEPGRAM_API_KEY="a1b2c3d4e5f6a1b2c3d4e5f6..." \
  ELEVENLABS_API_KEY="sk_abc123def456ghi789jkl..." \
  ELEVENLABS_VOICE_ID="21m00Tcm4TlvDq8ikWAM" \
  \
  TWILIO_ACCOUNT_SID="AC6e6fcb20143e1f70c8fa531e7bb403a4" \
  TWILIO_AUTH_TOKEN="abc123def456ghi789jkl012mno345pq" \
  TWILIO_API_KEY="SKabc123def456ghi789jkl012mno345" \
  TWILIO_API_SECRET="abc123def456ghi789jkl012mno345pq" \
  TWILIO_TWIML_APP_SID="APdaf3bfdc1873e5784fe51594b7fd63a1" \
  TWILIO_PHONE_NUMBER="+221781234567" \
  TWILIO_VOICE_NUMBER="+221781234567" \
  \
  PUBLIC_BASE_URL="https://agentia-api.fly.dev" \
  PUBLIC_WS_URL="wss://agentia-api.fly.dev" \
  ALLOWED_ORIGINS="https://agentia-dashboard.fly.dev" \
  DEFAULT_TENANT_ID="00000000-0000-0000-0000-000000000001" \
  RATE_LIMIT_WINDOW_SEC="60" \
  RATE_LIMIT_MAX_REQ="120"

echo ""
echo "✅ Secrets set. Vérifiez avec : fly secrets list -a agentia-api"
