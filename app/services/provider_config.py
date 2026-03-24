from __future__ import annotations

from typing import Any, Iterable

from ..config import settings
from ..logger import get_logger

logger = get_logger(__name__)

WHATSAPP_PROVIDER_CHOICES = ("twilio", "meta")
EMAIL_PROVIDER_CHOICES = ("brevo", "smtp", "gmail", "sendgrid")


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _normalize_provider(value: Any, *, env_name: str, allowed: Iterable[str]) -> str:
    normalized = str(value or "").strip().lower()
    allowed_values = tuple(allowed)
    if normalized not in allowed_values:
        joined = ", ".join(allowed_values)
        raise RuntimeError(f"{env_name} must be one of: {joined}")
    return normalized


def resolve_whatsapp_provider(current_settings=settings) -> str:
    return _normalize_provider(
        getattr(current_settings, "whatsapp_provider", None),
        env_name="WHATSAPP_PROVIDER",
        allowed=WHATSAPP_PROVIDER_CHOICES,
    )


def resolve_email_provider(current_settings=settings) -> str:
    return _normalize_provider(
        getattr(current_settings, "email_provider", None),
        env_name="EMAIL_PROVIDER",
        allowed=EMAIL_PROVIDER_CHOICES,
    )


def get_whatsapp_provider_status(current_settings=settings) -> dict[str, Any]:
    provider = resolve_whatsapp_provider(current_settings)
    required_by_provider = {
        "twilio": (
            ("TWILIO_ACCOUNT_SID", getattr(current_settings, "twilio_account_sid", None)),
            ("TWILIO_AUTH_TOKEN", getattr(current_settings, "twilio_auth_token", None)),
            ("TWILIO_WHATSAPP_NUMBER", getattr(current_settings, "twilio_whatsapp_number", None)),
        ),
        "meta": (
            ("META_WHATSAPP_PHONE_NUMBER_ID", getattr(current_settings, "meta_whatsapp_phone_number_id", None)),
            ("META_WHATSAPP_ACCESS_TOKEN", getattr(current_settings, "meta_whatsapp_access_token", None)),
            ("META_WHATSAPP_VERIFY_TOKEN", getattr(current_settings, "meta_whatsapp_verify_token", None)),
            ("META_WHATSAPP_APP_SECRET", getattr(current_settings, "meta_whatsapp_app_secret", None)),
        ),
    }
    inactive_fields = {
        "twilio": (
            ("META_WHATSAPP_PHONE_NUMBER_ID", getattr(current_settings, "meta_whatsapp_phone_number_id", None)),
            ("META_WHATSAPP_ACCESS_TOKEN", getattr(current_settings, "meta_whatsapp_access_token", None)),
            ("META_WHATSAPP_VERIFY_TOKEN", getattr(current_settings, "meta_whatsapp_verify_token", None)),
            ("META_WHATSAPP_APP_SECRET", getattr(current_settings, "meta_whatsapp_app_secret", None)),
        ),
        "meta": (
            ("TWILIO_ACCOUNT_SID", getattr(current_settings, "twilio_account_sid", None)),
            ("TWILIO_AUTH_TOKEN", getattr(current_settings, "twilio_auth_token", None)),
            ("TWILIO_WHATSAPP_NUMBER", getattr(current_settings, "twilio_whatsapp_number", None)),
        ),
    }
    missing = [name for name, value in required_by_provider[provider] if not _present(value)]
    ignored = [name for name, value in inactive_fields[provider] if _present(value)]
    return {
        "service": "whatsapp",
        "provider": provider,
        "configured": not missing,
        "missing_required": missing,
        "ignored_credentials": ignored,
    }


def get_email_provider_status(current_settings=settings) -> dict[str, Any]:
    provider = resolve_email_provider(current_settings)
    required_by_provider = {
        "brevo": (
            ("FROM_EMAIL", getattr(current_settings, "from_email", None)),
            ("BREVO_API_KEY", getattr(current_settings, "brevo_api_key", None)),
        ),
        "smtp": (
            ("FROM_EMAIL", getattr(current_settings, "from_email", None)),
            ("MAIL_HOST", getattr(current_settings, "mail_host", None)),
            ("MAIL_USERNAME", getattr(current_settings, "mail_username", None)),
            ("MAIL_PASSWORD", getattr(current_settings, "mail_password", None)),
        ),
        "gmail": (
            ("FROM_EMAIL", getattr(current_settings, "from_email", None)),
            ("GMAIL_SMTP_USER", getattr(current_settings, "gmail_smtp_user", None)),
            ("GMAIL_SMTP_PASS", getattr(current_settings, "gmail_smtp_pass", None)),
        ),
        "sendgrid": (
            ("FROM_EMAIL", getattr(current_settings, "from_email", None)),
            ("SENDGRID_API_KEY", getattr(current_settings, "sendgrid_api_key", None)),
        ),
    }
    inactive_fields = {
        "brevo": (
            ("MAIL_HOST", getattr(current_settings, "mail_host", None)),
            ("MAIL_USERNAME", getattr(current_settings, "mail_username", None)),
            ("MAIL_PASSWORD", getattr(current_settings, "mail_password", None)),
            ("GMAIL_SMTP_USER", getattr(current_settings, "gmail_smtp_user", None)),
            ("GMAIL_SMTP_PASS", getattr(current_settings, "gmail_smtp_pass", None)),
            ("SENDGRID_API_KEY", getattr(current_settings, "sendgrid_api_key", None)),
        ),
        "smtp": (
            ("BREVO_API_KEY", getattr(current_settings, "brevo_api_key", None)),
            ("GMAIL_SMTP_USER", getattr(current_settings, "gmail_smtp_user", None)),
            ("GMAIL_SMTP_PASS", getattr(current_settings, "gmail_smtp_pass", None)),
            ("SENDGRID_API_KEY", getattr(current_settings, "sendgrid_api_key", None)),
        ),
        "gmail": (
            ("BREVO_API_KEY", getattr(current_settings, "brevo_api_key", None)),
            ("MAIL_HOST", getattr(current_settings, "mail_host", None)),
            ("MAIL_USERNAME", getattr(current_settings, "mail_username", None)),
            ("MAIL_PASSWORD", getattr(current_settings, "mail_password", None)),
            ("SENDGRID_API_KEY", getattr(current_settings, "sendgrid_api_key", None)),
        ),
        "sendgrid": (
            ("BREVO_API_KEY", getattr(current_settings, "brevo_api_key", None)),
            ("MAIL_HOST", getattr(current_settings, "mail_host", None)),
            ("MAIL_USERNAME", getattr(current_settings, "mail_username", None)),
            ("MAIL_PASSWORD", getattr(current_settings, "mail_password", None)),
            ("GMAIL_SMTP_USER", getattr(current_settings, "gmail_smtp_user", None)),
            ("GMAIL_SMTP_PASS", getattr(current_settings, "gmail_smtp_pass", None)),
        ),
    }
    missing = [name for name, value in required_by_provider[provider] if not _present(value)]
    ignored = [name for name, value in inactive_fields[provider] if _present(value)]
    return {
        "service": "email",
        "provider": provider,
        "configured": not missing,
        "missing_required": missing,
        "ignored_credentials": ignored,
        "mail_mailer_ignored": _present(getattr(current_settings, "mail_mailer", None)),
    }


def validate_provider_configuration(current_settings=settings) -> dict[str, dict[str, Any]]:
    whatsapp = get_whatsapp_provider_status(current_settings)
    email = get_email_provider_status(current_settings)
    report = {"whatsapp": whatsapp, "email": email}

    for status in (whatsapp, email):
        logger.info(
            "provider_configuration_validated",
            extra={
                "extra_fields": {
                    "service": status["service"],
                    "provider": status["provider"],
                    "configured": status["configured"],
                }
            },
        )
        if status["ignored_credentials"]:
            logger.warning(
                "provider_configuration_ignored_credentials",
                extra={
                    "extra_fields": {
                        "service": status["service"],
                        "provider": status["provider"],
                        "ignored_credentials": status["ignored_credentials"],
                    }
                },
            )

    if email["mail_mailer_ignored"]:
        logger.warning(
            "email_provider_mail_mailer_ignored",
            extra={
                "extra_fields": {
                    "service": "email",
                    "provider": email["provider"],
                    "mail_mailer_present": True,
                }
            },
        )

    invalid = [status for status in report.values() if not status["configured"]]
    if invalid:
        details = "; ".join(
            f"{status['service']}[{status['provider']}] missing {', '.join(status['missing_required'])}"
            for status in invalid
        )
        logger.error(
            "provider_configuration_invalid",
            extra={"extra_fields": {"details": details}},
        )
        raise RuntimeError(f"Provider configuration invalid: {details}")

    return report
