# Configuration Complete des Variables d'Environnement

Ce document explique comment obtenir et configurer toutes les variables de `.env.example`.

## 1) Installation de base

1. Copier le template backend:
```bash
cp .env.example .env
```
2. Pour le frontend dashboard, creer `front/dashboard/.env` et y copier la section `VITE_*`.

## 2) Variables locales (a generer vous-meme)

### JWT (`JWT_PRIVATE_KEY`, `JWT_PUBLIC_KEY`)
Generer une paire RSA:
```bash
openssl genpkey -algorithm RSA -out jwt_private.pem -pkeyopt rsa_keygen_bits:2048
openssl rsa -in jwt_private.pem -pubout -out jwt_public.pem
```
Puis convertir en une ligne (`\n` echappes) et coller dans `.env`:
```bash
awk 'NF {sub(/\r/, ""); printf "%s\\n",$0;}' jwt_private.pem
awk 'NF {sub(/\r/, ""); printf "%s\\n",$0;}' jwt_public.pem
```

### Cle de chiffrement (`APP_ENCRYPTION_KEY_BASE64`)
```bash
openssl rand -base64 32
```

### Parametres applicatifs
- `ENV`: `dev`, `staging`, `prod`
- `LOG_LEVEL`: `INFO` (ou `DEBUG` en local)
- `APP_TIMEZONE`: ex `UTC`, `Europe/Paris`
- `ACCESS_TOKEN_TTL`, `REFRESH_TOKEN_TTL`: durees en secondes

## 3) Base de donnees et Redis

### PostgreSQL (`DATABASE_URL`)
Format:
```text
postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME
```

### Redis (`REDIS_URL`, `REDIS_PASSWORD`, `REDIS_SSL`)
Format:
```text
redis://127.0.0.1:6379/0
```
Si Redis indisponible:
- `AUTH_FAIL_CLOSED=false` en dev
- `AUTH_FAIL_CLOSED=true` en prod stricte

## 4) Twilio (Voice, SMS, WhatsApp Twilio)

Creer un compte Twilio: https://www.twilio.com/console

### Variables a recuperer
- `TWILIO_ACCOUNT_SID`: Console > Account Info
- `TWILIO_AUTH_TOKEN`: Console > Account Info
- `TWILIO_PHONE_NUMBER`: Phone Numbers > Active Numbers (SMS)
- `TWILIO_VOICE_NUMBER`: numero voix (peut etre le meme)
- `TWILIO_WHATSAPP_NUMBER`: numero WhatsApp Twilio (sandbox ou prod)
- `TWILIO_REGION`: ex `ie1` (optionnel)

### Twilio Voice SDK (web calls)
- `TWILIO_API_KEY`: Console > API Keys & Tokens > Create API Key
- `TWILIO_API_SECRET`: affiche lors de la creation
- `TWILIO_TWIML_APP_SID`: Voice > TwiML Apps > Create

### Provider flags
- `SMS_PROVIDER=twilio` pour SMS via Twilio
- `WHATSAPP_PROVIDER=twilio` pour WhatsApp via Twilio

## 5) WhatsApp Cloud API Meta

Creer une app Meta: https://developers.facebook.com/

### Etapes
1. Créer une app type "Business".
2. Ajouter le produit WhatsApp.
3. Lier un Business Account et un numero.
4. Configurer le webhook de votre backend.

### Variables
- `META_API_VERSION`: ex `v20.0`
- `META_WHATSAPP_PHONE_NUMBER_ID`: panneau WhatsApp API Setup
- `META_WHATSAPP_ACCESS_TOKEN`: token system user (preferable long-lived)
- `META_WHATSAPP_VERIFY_TOKEN`: valeur libre (doit matcher backend/webhook)
- `META_WHATSAPP_APP_SECRET`: App > Settings > Basic > App Secret

Pour utiliser Meta: `WHATSAPP_PROVIDER=meta`.

## 6) STT / TTS / LLM

### Deepgram (STT)
Console: https://console.deepgram.com/
- `DEEPGRAM_API_KEY`: API Keys > Create
- `DEEPGRAM_MODEL`: ex `nova-2`
- `STT_LANGUAGE`: ex `fr`, `en`, `wo`, ou `multi`

### ElevenLabs (TTS)
Console: https://elevenlabs.io/
- `ELEVENLABS_API_KEY`: Profile > API Key
- `ELEVENLABS_VOICE_ID`: Voices > copier l'ID de la voix
- `ELEVENLABS_MODEL`: ex `eleven_multilingual_v2`

### OpenAI/GPT
- `OPENAI_API_KEY`: https://platform.openai.com/api-keys
- `GPT5_API_KEY`: optionnel (si vous separez la cle GPT5)

## 7) Email (Brevo, SendGrid, Gmail/SMTP)

### Variables communes
- `EMAIL_PROVIDER`: `brevo`, `sendgrid`, `gmail`, `smtp`
- `MAIL_MAILER`: aligner avec le provider
- `FROM_EMAIL`, `FROM_NAME`, `MAIL_FROM_ADDRESS`, `MAIL_FROM_NAME`
- `MAIL_HOST`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`

### Brevo
Console: https://app.brevo.com/
- `BREVO_API_KEY`: SMTP & API > API Keys

### SendGrid
Console: https://app.sendgrid.com/
- `SENDGRID_API_KEY`: Settings > API Keys

### Gmail SMTP
- `GMAIL_SMTP_USER`: adresse Gmail
- `GMAIL_SMTP_PASS`: App Password (pas le mot de passe principal)
- `GMAIL_SMTP_HOST=smtp.gmail.com`
- `GMAIL_SMTP_PORT=587`

## 8) Webhooks entrants (email/secure)

- `WEBHOOK_REPLAY_TTL_SEC`: fenetre anti-replay
- `WEBHOOK_FAIL_CLOSED`: `true` recommande en prod
- `EMAIL_WEBHOOK_SECRET`: secret de signature provider email
- `EMAIL_WEBHOOK_SIGNATURE_HEADER`: header utilise par provider
- `EMAIL_WEBHOOK_IP_ALLOWLIST`: liste IP autorisees (CSV)
- `MAILGUN_WEBHOOK_SIGNING_KEY`: seulement si Mailgun inbound

## 9) Google Calendar

1. Google Cloud Console > creer projet.
2. Activer Google Calendar API.
3. Créer un Service Account.
4. Télécharger le JSON credentials.
5. Base64 du fichier JSON:
```bash
base64 -i service-account.json | tr -d '\n'
```
6. Mettre le resultat dans `GOOGLE_CREDENTIALS_JSON_BASE64`.
7. `GOOGLE_CALENDAR_ID`: ID de calendrier partage avec le service account.

## 10) Chiffrement/KMS

- `KMS_KEY_ID`: optionnel si vous utilisez AWS/GCP/Azure KMS.
- `ALLOW_EPHEMERAL_ENCRYPTION_KEY=false` en prod.

## 11) CORS, tenant, et endpoints publics

- `ALLOWED_ORIGINS`: origines frontend autorisees (CSV)
- `DEFAULT_TENANT_ID`, `ENFORCE_TENANT_SCOPE`
- `TENANT_PUBLIC_PATHS`, `TENANT_FAIL_CLOSED_PUBLIC_PATHS`
- `WIDGET_PUBLIC_TOKEN`: optionnel pour widget public

Important:
- Les credentials publics `provider_key` / `tenant_token` ne viennent pas du `.env`.
- Ils sont en base (`tenant_channels`) et transmis par la config du widget.

## 12) Auth hardening / monitoring / KPI

### Auth hardening
- `AUTH_RATE_LIMIT_WINDOW_SEC`
- `AUTH_RATE_LIMIT_IP_MAX`
- `AUTH_RATE_LIMIT_IDENTIFIER_MAX`
- `AUTH_LOCK_THRESHOLD`
- `AUTH_LOCK_BASE_SEC`
- `AUTH_LOCK_MAX_SEC`
- `AUTH_JITTER_MIN_MS`
- `AUTH_JITTER_MAX_MS`
- `AUTH_SECURITY_FAIL_CLOSED`

### Monitoring (Sentry)
- `SENTRY_DSN`
- `SENTRY_ENV`
- `SENTRY_TRACES_SAMPLE_RATE`
- `SENTRY_ORG`
- `SENTRY_PROJECT`
- `SENTRY_AUTH_TOKEN`
- `ADMIN_ALERT_EMAIL`
- `ALERT_FAILED_LOGINS_THRESHOLD`
- `ALERT_FAILED_LOGINS_WINDOW_MIN`
- `ALERT_DEDUPE_TTL_SEC`

### KPI / Outbox
- `SMS_UNIT_COST`
- `EMAIL_UNIT_COST`
- `OUTBOX_MODE_ENABLED`
- `OUTBOX_BATCH_SIZE`
- `OUTBOX_RETRY_BASE_SEC`

## 13) Frontend dashboard (`front/dashboard/.env`)

- `VITE_API_BASE`: URL API backend (ex `http://localhost:8000`)
- `VITE_API_TOKEN`: token API optionnel
- `VITE_DISABLE_OTP`: `true/false`
- `VITE_WIDGET_TOKEN`: token widget optionnel
- `VITE_SENTRY_DSN`: DSN Sentry frontend
- `VITE_ENV`: `development`, `staging`, `production`

## 14) Variables optionnelles tests/scripts

- `E2E_BASE_URL`: URL de test Playwright
- `ADMIN_EMAIL`, `ADMIN_PASSWORD`: utilisees par scripts de seed

## 15) Verification rapide

1. Remplir `.env`.
2. Lancer API:
```bash
uvicorn app.main:app --reload
```
3. Verifier qu'il n'y a pas d'erreurs de config au demarrage.
4. Tester login, chat, voice token, webhook selon les providers actifs.
