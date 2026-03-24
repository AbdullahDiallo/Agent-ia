from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request, status

from ..config import settings
from ..logger import get_logger
from ..redis_client import get_redis
from ..vendors.twilio import verify_twilio_request

logger = get_logger(__name__)


@dataclass
class WebhookVerification:
    provider: str
    event_id: str
    nonce: str
    timestamp: int
    signature_valid: bool
    tenant: str = "default"


def _as_int(value: Any, default: int) -> int:
    try:
        return int(str(value))
    except Exception:
        return default


def _extract_client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return str(request.client.host)
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return "unknown"


def _extract_tenant(request: Request) -> str:
    tenant = getattr(getattr(request, "state", None), "tenant_id", None)
    if tenant:
        return str(tenant)
    return str(getattr(settings, "default_tenant_id", "default") or "default")


def _signature_without_prefix(raw_sig: str) -> str:
    sig = (raw_sig or "").strip()
    if sig.startswith("sha256="):
        return sig.split("=", 1)[1].strip()
    return sig


def _validate_timestamp(timestamp: int) -> None:
    ttl = max(60, int(settings.webhook_replay_ttl_sec))
    now = int(time.time())
    if abs(now - timestamp) > ttl:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="stale_webhook")


def _safe_redis():
    try:
        return get_redis()
    except Exception as exc:
        if settings.webhook_fail_closed:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="webhook_store_unavailable",
            ) from exc
        logger.warning("Webhook replay store unavailable", extra={"extra_fields": {"error": str(exc)}})
        return None


def _mark_replay(provider: str, event_id: str, nonce: str) -> None:
    redis_client = _safe_redis()
    if redis_client is None:
        return
    ttl = max(60, int(settings.webhook_replay_ttl_sec))
    replay_key = f"webhook:replay:{provider}:{event_id}:{nonce}"
    created = redis_client.set(replay_key, b"1", ex=ttl, nx=True)
    if not created:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="replay_detected")


def _enforce_ip_allowlist(request: Request) -> None:
    allowlist_raw = (settings.email_webhook_ip_allowlist or "").strip()
    if not allowlist_raw:
        return
    client_ip = _extract_client_ip(request)
    try:
        ip_obj = ip_address(client_ip)
    except Exception:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="webhook_ip_blocked")

    networks = []
    for item in [x.strip() for x in allowlist_raw.split(",") if x.strip()]:
        try:
            if "/" in item:
                networks.append(ip_network(item, strict=False))
            else:
                networks.append(ip_network(f"{item}/32", strict=False))
        except Exception:
            continue
    if not networks:
        return
    if not any(ip_obj in net for net in networks):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="webhook_ip_blocked")


def _verify_meta_whatsapp(raw_body: bytes, request: Request, payload: Dict[str, Any]) -> WebhookVerification:
    app_secret = (settings.meta_whatsapp_app_secret or "").strip()
    if not app_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="meta_secret_not_configured",
        )
    signature = request.headers.get("X-Hub-Signature-256") or request.headers.get("x-hub-signature-256")
    if not signature:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_signature")

    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    incoming = _signature_without_prefix(signature)
    if not hmac.compare_digest(incoming, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_signature")

    message_id = ""
    message_ts = int(time.time())
    entries = payload.get("entry") or []
    for entry in entries:
        for change in (entry.get("changes") or []):
            value = change.get("value") or {}
            messages = value.get("messages") or []
            if not messages:
                continue
            first = messages[0]
            message_id = str(first.get("id") or "")
            message_ts = _as_int(first.get("timestamp"), message_ts)
            if message_id:
                break
        if message_id:
            break
    if not message_id:
        message_id = request.headers.get("X-Request-Id") or f"meta-{hashlib.sha256(raw_body).hexdigest()[:24]}"

    _validate_timestamp(message_ts)
    nonce = message_id
    return WebhookVerification(
        provider="meta_whatsapp",
        event_id=message_id,
        nonce=nonce,
        timestamp=message_ts,
        signature_valid=True,
    )


def _verify_email_inbound(raw_body: bytes, request: Request, form_data: Dict[str, Any]) -> WebhookVerification:
    _enforce_ip_allowlist(request)
    now = int(time.time())

    # Mailgun native signature: HMAC_SHA256(signing_key, timestamp + token)
    mg_key = (settings.mailgun_webhook_signing_key or "").strip()
    mg_timestamp = str(form_data.get("timestamp") or "").strip()
    mg_token = str(form_data.get("token") or "").strip()
    mg_signature = str(form_data.get("signature") or "").strip()
    if mg_key and mg_timestamp and mg_token and mg_signature:
        expected = hmac.new(
            mg_key.encode("utf-8"),
            f"{mg_timestamp}{mg_token}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, mg_signature):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_signature")
        timestamp = _as_int(mg_timestamp, now)
        _validate_timestamp(timestamp)
        event_id = (
            str(form_data.get("message-id") or "")
            or str(form_data.get("Message-Id") or "")
            or mg_token
        )
        nonce = mg_token
        return WebhookVerification(
            provider="email_inbound",
            event_id=event_id,
            nonce=nonce,
            timestamp=timestamp,
            signature_valid=True,
        )

    secret = (settings.email_webhook_secret or "").strip()
    if not secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="email_webhook_not_configured")

    timestamp_header = request.headers.get("X-Webhook-Timestamp", "").strip()
    nonce_header = request.headers.get("X-Webhook-Nonce", "").strip()
    event_id = (
        request.headers.get("X-Webhook-Event-Id", "").strip()
        or str(form_data.get("Message-Id") or form_data.get("message-id") or "").strip()
        or nonce_header
    )
    if not event_id:
        event_id = f"email-{hashlib.sha256(raw_body).hexdigest()[:24]}"

    if not timestamp_header or not nonce_header:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_replay_headers")

    timestamp = _as_int(timestamp_header, 0)
    if timestamp <= 0:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_timestamp")
    _validate_timestamp(timestamp)

    signature_header_name = settings.email_webhook_signature_header or "X-Webhook-Signature"
    signature = request.headers.get(signature_header_name, "").strip()
    if signature:
        base = f"{timestamp_header}.{nonce_header}.".encode("utf-8") + raw_body
        expected = hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
        incoming = _signature_without_prefix(signature)
        if not hmac.compare_digest(incoming, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_signature")
    else:
        # Fallback secret header (when provider cannot compute HMAC)
        secret_header = request.headers.get("X-Webhook-Secret", "").strip()
        if not hmac.compare_digest(secret_header, secret):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_secret")

    return WebhookVerification(
        provider="email_inbound",
        event_id=event_id,
        nonce=nonce_header,
        timestamp=timestamp,
        signature_valid=True,
    )


def _verify_twilio(provider: str, request: Request, url: str, form_data: Dict[str, str], raw_body: bytes) -> WebhookVerification:
    verify_twilio_request(request, url, form_data)
    now = int(time.time())
    header_ts = _as_int(request.headers.get("X-Twilio-Request-Timestamp"), now)
    _validate_timestamp(header_ts)
    event_id = (
        form_data.get("MessageSid")
        or form_data.get("SmsSid")
        or form_data.get("CallSid")
        or form_data.get("RecordingSid")
        or form_data.get("EventSid")
        or f"twilio-{hashlib.sha256(raw_body).hexdigest()[:24]}"
    )
    nonce = event_id
    return WebhookVerification(
        provider=provider,
        event_id=event_id,
        nonce=nonce,
        timestamp=header_ts,
        signature_valid=True,
    )


def _best_effort_event_id(
    provider: str,
    request: Request,
    raw_body: bytes,
    form_data: Optional[Dict[str, Any]],
    payload: Optional[Dict[str, Any]],
) -> str:
    provider_name = (provider or "").strip().lower()
    data = form_data or {}
    if provider_name == "meta_whatsapp":
        entries = (payload or {}).get("entry") or []
        for entry in entries:
            for change in (entry.get("changes") or []):
                value = change.get("value") or {}
                messages = value.get("messages") or []
                if messages and messages[0].get("id"):
                    return str(messages[0]["id"])
    if provider_name == "email_inbound":
        candidate = (
            request.headers.get("X-Webhook-Event-Id")
            or str(data.get("message-id") or "")
            or str(data.get("Message-Id") or "")
            or request.headers.get("X-Request-Id")
        )
        if candidate:
            return str(candidate)
    if provider_name in {"twilio_sms", "twilio_whatsapp", "twilio_voice", "twilio_recording", "twilio_events"}:
        for key in ("MessageSid", "SmsSid", "CallSid", "RecordingSid", "EventSid"):
            value = data.get(key)
            if value:
                return str(value)
    req_id = request.headers.get("X-Request-Id")
    if req_id:
        return str(req_id)
    return f"{provider_name}-{hashlib.sha256(raw_body).hexdigest()[:24]}"


def verify_webhook(
    provider: str,
    *,
    request: Request,
    raw_body: bytes,
    form_data: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
    url: Optional[str] = None,
) -> WebhookVerification:
    provider_name = (provider or "").strip().lower()
    if not provider_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_provider")
    tenant = _extract_tenant(request)
    event_id = _best_effort_event_id(provider_name, request, raw_body, form_data, payload)
    try:
        if provider_name == "meta_whatsapp":
            parsed_payload = payload
            if parsed_payload is None:
                try:
                    parsed_payload = json.loads(raw_body.decode("utf-8"))
                except Exception:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_payload")
            verification = _verify_meta_whatsapp(raw_body, request, parsed_payload)
        elif provider_name == "email_inbound":
            verification = _verify_email_inbound(raw_body, request, form_data or {})
        elif provider_name in {"twilio_sms", "twilio_whatsapp", "twilio_voice", "twilio_recording", "twilio_events"}:
            data = {str(k): str(v) for k, v in (form_data or {}).items()}
            verification = _verify_twilio(provider_name, request, url or str(request.url), data, raw_body)
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unsupported_provider")

        verification.tenant = tenant
        event_id = verification.event_id
        _mark_replay(verification.provider, verification.event_id, verification.nonce)
        logger.info(
            "Webhook verified",
            extra={
                "extra_fields": {
                    "provider": verification.provider,
                    "event_id": verification.event_id,
                    "signature_valid": verification.signature_valid,
                    "tenant": verification.tenant,
                    "ip": _extract_client_ip(request),
                }
            },
        )
        return verification
    except HTTPException as exc:
        logger.warning(
            "Webhook rejected",
            extra={
                "extra_fields": {
                    "provider": provider_name,
                    "event_id": event_id,
                    "signature_valid": False,
                    "tenant": tenant,
                    "ip": _extract_client_ip(request),
                    "reason": str(exc.detail),
                    "status_code": exc.status_code,
                }
            },
        )
        raise
