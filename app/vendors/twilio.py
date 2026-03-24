import base64
import hashlib
import hmac
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException, Request, status
from ..config import settings


def _build_signature_base(url: str, params: dict[str, str]) -> bytes:
    parsed = urlsplit(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if parsed.query:
        base_url = f"{base_url}?{parsed.query}"
    items = sorted((k, v) for k, v in params.items())
    concatenated = base_url + "".join([f"{k}{v}" for k, v in items])
    return concatenated.encode()


def _effective_public_url(request: Request, fallback_url: str) -> str:
    parsed = urlsplit(fallback_url)
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip()
    forwarded_host = (request.headers.get("x-forwarded-host") or "").split(",", 1)[0].strip()
    host = (request.headers.get("host") or "").split(",", 1)[0].strip()
    scheme = forwarded_proto or parsed.scheme
    netloc = forwarded_host or host or parsed.netloc
    return urlunsplit((scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def verify_twilio_request(request: Request, url: str, params: dict[str, str]) -> None:
    sig = request.headers.get("X-Twilio-Signature")
    if not sig:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_signature")
    token = settings.twilio_auth_token or ""
    effective_url = _effective_public_url(request, url)
    mac = hmac.new(token.encode(), _build_signature_base(effective_url, params), hashlib.sha1)
    expected = base64.b64encode(mac.digest()).decode()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_signature")
