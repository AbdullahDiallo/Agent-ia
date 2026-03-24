from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AnyUrl, Field
from typing import Optional

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

    env: str = Field(default="dev", alias="ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    public_base_url: AnyUrl | None = Field(default=None, alias="PUBLIC_BASE_URL")
    public_ws_url: AnyUrl | None = Field(default=None, alias="PUBLIC_WS_URL")

    # SMS Configuration
    sms_provider: str = Field(default="twilio", alias="SMS_PROVIDER")  # "twilio" ou "orange"

    # Twilio
    twilio_account_sid: Optional[str] = Field(default=None, alias="TWILIO_ACCOUNT_SID")
    twilio_auth_token: Optional[str] = Field(default=None, alias="TWILIO_AUTH_TOKEN")
    twilio_phone_number: Optional[str] = Field(default=None, alias="TWILIO_PHONE_NUMBER")
    twilio_voice_number: Optional[str] = Field(default=None, alias="TWILIO_VOICE_NUMBER")
    twilio_region: Optional[str] = Field(default=None, alias="TWILIO_REGION")
    # Twilio Voice SDK (pour appels depuis le web)
    twilio_api_key: Optional[str] = Field(default=None, alias="TWILIO_API_KEY")
    twilio_api_secret: Optional[str] = Field(default=None, alias="TWILIO_API_SECRET")
    twilio_twiml_app_sid: Optional[str] = Field(default=None, alias="TWILIO_TWIML_APP_SID")

    # Meta WhatsApp Cloud API
    whatsapp_provider: str = Field(default="meta", alias="WHATSAPP_PROVIDER")  # "meta" ou "twilio"
    meta_whatsapp_phone_number_id: Optional[str] = Field(default=None, alias="META_WHATSAPP_PHONE_NUMBER_ID")
    meta_whatsapp_access_token: Optional[str] = Field(default=None, alias="META_WHATSAPP_ACCESS_TOKEN")
    meta_whatsapp_verify_token: Optional[str] = Field(default=None, alias="META_WHATSAPP_VERIFY_TOKEN")
    meta_whatsapp_app_secret: Optional[str] = Field(default=None, alias="META_WHATSAPP_APP_SECRET")
    meta_api_version: str = Field(default="v20.0", alias="META_API_VERSION")
    twilio_whatsapp_number: Optional[str] = Field(default=None, alias="TWILIO_WHATSAPP_NUMBER")

    # Orange SMS API
    orange_sms_client_id: Optional[str] = Field(default=None, alias="ORANGE_SMS_CLIENT_ID")
    orange_sms_client_secret: Optional[str] = Field(default=None, alias="ORANGE_SMS_CLIENT_SECRET")
    orange_sms_sender_number: Optional[str] = Field(default=None, alias="ORANGE_SMS_SENDER_NUMBER")  # Format: +225XXXXXXXXX

    deepgram_api_key: str | None = Field(default=None, alias="DEEPGRAM_API_KEY")
    deepgram_model: str = Field(default="nova-2", alias="DEEPGRAM_MODEL")
    stt_language: str = Field(default="fr", alias="STT_LANGUAGE")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o", alias="OPENAI_MODEL")
    gpt5_api_key: str | None = Field(default=None, alias="GPT5_API_KEY")
    elevenlabs_api_key: str | None = Field(default=None, alias="ELEVENLABS_API_KEY")
    elevenlabs_voice_id: str | None = Field(default=None, alias="ELEVENLABS_VOICE_ID")
    elevenlabs_model: str = Field(default="eleven_turbo_v2_5", alias="ELEVENLABS_MODEL")

    # Email Configuration
    email_provider: str = Field(default="brevo", alias="EMAIL_PROVIDER")  # "brevo", "sendgrid", "gmail" ou "smtp"
    from_email: str | None = Field(default=None, alias="FROM_EMAIL")
    from_name: str | None = Field(default="AgentIA", alias="FROM_NAME")

    # Laravel-style mail configuration (aliases)
    mail_mailer: str | None = Field(default=None, alias="MAIL_MAILER")
    mail_host: str | None = Field(default=None, alias="MAIL_HOST")
    mail_port: int | None = Field(default=None, alias="MAIL_PORT")
    mail_username: str | None = Field(default=None, alias="MAIL_USERNAME")
    mail_password: str | None = Field(default=None, alias="MAIL_PASSWORD")
    mail_from_address: str | None = Field(default=None, alias="MAIL_FROM_ADDRESS")
    mail_from_name: str | None = Field(default=None, alias="MAIL_FROM_NAME")

    # Brevo (Sendinblue)
    brevo_api_key: str | None = Field(default=None, alias="BREVO_API_KEY")

    # SendGrid (legacy)
    sendgrid_api_key: str | None = Field(default=None, alias="SENDGRID_API_KEY")

    # Gmail SMTP
    gmail_smtp_user: str | None = Field(default=None, alias="GMAIL_SMTP_USER")
    gmail_smtp_pass: str | None = Field(default=None, alias="GMAIL_SMTP_PASS")
    gmail_smtp_host: str = Field(default="smtp.gmail.com", alias="GMAIL_SMTP_HOST")
    gmail_smtp_port: int = Field(default=587, alias="GMAIL_SMTP_PORT")

    database_url: str = Field(alias="DATABASE_URL")

    kms_key_id: str | None = Field(default=None, alias="KMS_KEY_ID")
    app_encryption_key_base64: str | None = Field(default=None, alias="APP_ENCRYPTION_KEY_BASE64")
    allow_ephemeral_encryption_key: bool = Field(default=False, alias="ALLOW_EPHEMERAL_ENCRYPTION_KEY")

    google_credentials_json_base64: str | None = Field(default=None, alias="GOOGLE_CREDENTIALS_JSON_BASE64")
    google_calendar_id: str | None = Field(default=None, alias="GOOGLE_CALENDAR_ID")

    app_timezone: str = Field(default="UTC", alias="APP_TIMEZONE")

    jwt_public_key: str = Field(alias="JWT_PUBLIC_KEY")
    jwt_private_key: str = Field(alias="JWT_PRIVATE_KEY")
    jwt_audience: str = Field(alias="JWT_AUDIENCE")
    jwt_issuer: str = Field(alias="JWT_ISSUER")

    # CORS
    allowed_origins: str | None = Field(default=None, alias="ALLOWED_ORIGINS")  # comma-separated
    auth_cookie_samesite: str = Field(default="auto", alias="AUTH_COOKIE_SAMESITE")
    auth_cookie_secure: bool | None = Field(default=None, alias="AUTH_COOKIE_SECURE")

    # Rate limiting
    rate_limit_window_sec: int = Field(default=60, alias="RATE_LIMIT_WINDOW_SEC")
    rate_limit_max_req: int = Field(default=60, alias="RATE_LIMIT_MAX_REQ")

    # Redis (OTP, token blacklist, and auth behavior)
    redis_url: str | None = Field(default=None, alias="REDIS_URL")
    redis_password: str | None = Field(default=None, alias="REDIS_PASSWORD")
    redis_ssl: bool = Field(default=False, alias="REDIS_SSL")
    # If True, a Redis outage will cause auth to fail with 503; otherwise ignore blacklist check
    auth_fail_closed: bool = Field(default=False, alias="AUTH_FAIL_CLOSED")

    # Feature flags
    disable_otp: bool = Field(default=False, alias="DISABLE_OTP")
    # P2 Conversation core hardening
    llm_structured_extraction_enabled: bool = Field(default=True, alias="LLM_STRUCTURED_EXTRACTION_ENABLED")
    llm_deterministic_rephrase_enabled: bool = Field(default=False, alias="LLM_DETERMINISTIC_REPHRASE_ENABLED")

    # Public widget token (optional, recommended for /voice/token)
    widget_public_token: str | None = Field(default=None, alias="WIDGET_PUBLIC_TOKEN")
    # Enforce authenticated/dev-token access to potentially dangerous endpoints
    enable_dev_endpoints: bool = Field(default=False, alias="ENABLE_DEV_ENDPOINTS")
    default_tenant_id: str = Field(default="00000000-0000-0000-0000-000000000001", alias="DEFAULT_TENANT_ID")
    enforce_tenant_scope: bool = Field(default=True, alias="ENFORCE_TENANT_SCOPE")
    tenant_public_paths: str = Field(
        default=(
            "/health,"
            "/health/ready,/health/live,"
            "/auth/login,/auth/verify-otp,/auth/refresh,"
            "/chat/chat,/chat/message,"
            "/sms/incoming,/whatsapp/incoming,/webhooks/meta/whatsapp,"
            "/email/incoming,/voice/token,/voice/outbound,/voice/incoming,/voice/recording-status,/events/call-status,"
            "/school/public,/school/contact-requests,"
            "/uploads"
        ),
        alias="TENANT_PUBLIC_PATHS",
    )
    tenant_fail_closed_public_paths: str = Field(
        default=(
            "/sms/incoming,/whatsapp/incoming,/webhooks/meta/whatsapp,"
            "/email/incoming,/voice/incoming,/voice/recording-status,/events/call-status,"
            "/chat/chat,/chat/message,/voice/token,/voice/outbound,/school/contact-requests"
        ),
        alias="TENANT_FAIL_CLOSED_PUBLIC_PATHS",
    )

    # Token lifetimes (seconds)
    access_token_ttl: int = Field(default=15 * 60, alias="ACCESS_TOKEN_TTL")
    refresh_token_ttl: int = Field(default=7 * 24 * 3600, alias="REFRESH_TOKEN_TTL")
    # Conversation session TTL by channel (seconds)
    chat_session_ttl_sec: int = Field(default=8 * 3600, alias="CHAT_SESSION_TTL_SEC")
    voice_session_ttl_sec: int = Field(default=30 * 60, alias="VOICE_SESSION_TTL_SEC")
    email_session_ttl_sec: int = Field(default=72 * 3600, alias="EMAIL_SESSION_TTL_SEC")
    sms_session_ttl_sec: int = Field(default=12 * 3600, alias="SMS_SESSION_TTL_SEC")
    whatsapp_session_ttl_sec: int = Field(default=24 * 3600, alias="WHATSAPP_SESSION_TTL_SEC")
    default_session_ttl_sec: int = Field(default=8 * 3600, alias="DEFAULT_SESSION_TTL_SEC")

    # Webhook hardening
    webhook_replay_ttl_sec: int = Field(default=300, alias="WEBHOOK_REPLAY_TTL_SEC")
    webhook_fail_closed: bool = Field(default=True, alias="WEBHOOK_FAIL_CLOSED")
    email_webhook_secret: str | None = Field(default=None, alias="EMAIL_WEBHOOK_SECRET")
    email_webhook_signature_header: str = Field(default="X-Webhook-Signature", alias="EMAIL_WEBHOOK_SIGNATURE_HEADER")
    email_webhook_ip_allowlist: str | None = Field(default=None, alias="EMAIL_WEBHOOK_IP_ALLOWLIST")
    mailgun_webhook_signing_key: str | None = Field(default=None, alias="MAILGUN_WEBHOOK_SIGNING_KEY")

    # Login hardening
    auth_rate_limit_window_sec: int = Field(default=600, alias="AUTH_RATE_LIMIT_WINDOW_SEC")
    auth_rate_limit_ip_max: int = Field(default=30, alias="AUTH_RATE_LIMIT_IP_MAX")
    auth_rate_limit_identifier_max: int = Field(default=10, alias="AUTH_RATE_LIMIT_IDENTIFIER_MAX")
    auth_lock_threshold: int = Field(default=5, alias="AUTH_LOCK_THRESHOLD")
    auth_lock_base_sec: int = Field(default=60, alias="AUTH_LOCK_BASE_SEC")
    auth_lock_max_sec: int = Field(default=3600, alias="AUTH_LOCK_MAX_SEC")
    auth_jitter_min_ms: int = Field(default=220, alias="AUTH_JITTER_MIN_MS")
    auth_jitter_max_ms: int = Field(default=380, alias="AUTH_JITTER_MAX_MS")
    auth_security_fail_closed: bool = Field(default=False, alias="AUTH_SECURITY_FAIL_CLOSED")

    # Messaging cost hints for KPI computation
    sms_unit_cost: float = Field(default=0.0, alias="SMS_UNIT_COST")
    email_unit_cost: float = Field(default=0.0, alias="EMAIL_UNIT_COST")
    outbox_mode_enabled: bool = Field(default=False, alias="OUTBOX_MODE_ENABLED")
    outbox_batch_size: int = Field(default=50, alias="OUTBOX_BATCH_SIZE")
    outbox_retry_base_sec: int = Field(default=30, alias="OUTBOX_RETRY_BASE_SEC")

    # Monitoring & alerting
    sentry_dsn: str | None = Field(default=None, alias="SENTRY_DSN")
    sentry_env: str | None = Field(default=None, alias="SENTRY_ENV")
    sentry_traces_sample_rate: float = Field(default=0.0, alias="SENTRY_TRACES_SAMPLE_RATE")
    sentry_org: str | None = Field(default=None, alias="SENTRY_ORG")
    sentry_project: str | None = Field(default=None, alias="SENTRY_PROJECT")
    sentry_auth_token: str | None = Field(default=None, alias="SENTRY_AUTH_TOKEN")
    admin_alert_email: str | None = Field(default=None, alias="ADMIN_ALERT_EMAIL")
    alert_failed_logins_threshold: int = Field(default=8, alias="ALERT_FAILED_LOGINS_THRESHOLD")
    alert_failed_logins_window_min: int = Field(default=30, alias="ALERT_FAILED_LOGINS_WINDOW_MIN")
    alert_dedupe_ttl_sec: int = Field(default=900, alias="ALERT_DEDUPE_TTL_SEC")

    # Stripe billing (optional)
    stripe_secret_key: str | None = Field(default=None, alias="STRIPE_SECRET_KEY")
    stripe_webhook_secret: str | None = Field(default=None, alias="STRIPE_WEBHOOK_SECRET")
    stripe_publishable_key: str | None = Field(default=None, alias="STRIPE_PUBLISHABLE_KEY")

def _normalize_pem(maybe_pem: str) -> str:
    if not isinstance(maybe_pem, str):
        return maybe_pem
    # Convert escaped newlines ("\n") to real newlines and strip whitespace
    s = maybe_pem.replace("\\n", "\n").strip()
    return s

settings = Settings()
# Normalize JWT keys to proper PEM format in case they are provided on a single line
settings.jwt_private_key = _normalize_pem(settings.jwt_private_key)
settings.jwt_public_key = _normalize_pem(settings.jwt_public_key)
