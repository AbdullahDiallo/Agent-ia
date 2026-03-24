from __future__ import annotations
"""Service d'envoi de SMS via Twilio ou Orange SMS API."""
from typing import Optional
from ..config import settings
from ..logger import get_logger
import httpx
from dataclasses import dataclass

logger = get_logger(__name__)

try:
    from twilio.rest import Client as TwilioClient
except ImportError:
    TwilioClient = None  # type: ignore


class SMSService:
    def __init__(self):
        self.provider = settings.sms_provider  # "twilio" ou "orange"
        self.twilio_client = None
        
        # Initialiser Twilio si configuré
        if self.provider == "twilio" and TwilioClient:
            if settings.twilio_account_sid and settings.twilio_auth_token:
                try:
                    self.twilio_client = TwilioClient(
                        settings.twilio_account_sid,
                        settings.twilio_auth_token
                    )
                except Exception as e:
                    logger.error(f"Failed to initialize Twilio client: {e}")

    def is_configured(self) -> bool:
        """Vérifie si un provider SMS est configuré."""
        if self.provider == "twilio":
            return self.twilio_client is not None and bool(settings.twilio_phone_number)
        elif self.provider == "orange":
            return bool(
                settings.orange_sms_client_id
                and settings.orange_sms_client_secret
                and settings.orange_sms_sender_number
            )
        return False

    async def _send_via_twilio(self, to_number: str, message: str) -> bool:
        """Envoie un SMS via Twilio."""
        if self.twilio_client is None:
            logger.error("Twilio client not configured")
            return False
        try:
            msg = self.twilio_client.messages.create(
                body=message,
                from_=settings.twilio_phone_number,
                to=to_number
            )
            logger.info(
                f"SMS sent via Twilio",
                extra={"extra_fields": {"to": to_number, "sid": msg.sid}}
            )
            return True
        except Exception as e:
            logger.error(
                f"Failed to send SMS via Twilio",
                extra={"extra_fields": {"to": to_number, "error": str(e)}}
            )
            return False

    async def _send_via_orange(self, to_number: str, message: str) -> bool:
        """Envoie un SMS via Orange SMS API.
        
        Documentation: https://developer.orange.com/apis/sms-ci/
        """
        try:
            # 1. Obtenir le token d'accès OAuth2
            token_url = "https://api.orange.com/oauth/v3/token"
            token_data = {
                "grant_type": "client_credentials"
            }
            
            async with httpx.AsyncClient() as client:
                # Authentification
                token_response = await client.post(
                    token_url,
                    data=token_data,
                    auth=(settings.orange_sms_client_id, settings.orange_sms_client_secret),
                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                )
                
                if token_response.status_code != 200:
                    logger.error(f"Orange OAuth failed: {token_response.text}")
                    return False
                
                access_token = token_response.json().get("access_token")
                
                # 2. Envoyer le SMS
                sms_url = "https://api.orange.com/smsmessaging/v1/outbound/tel%3A%2B{sender}/requests"
                sms_url = sms_url.format(sender=settings.orange_sms_sender_number.replace("+", ""))
                
                sms_payload = {
                    "outboundSMSMessageRequest": {
                        "address": f"tel:{to_number}",
                        "senderAddress": f"tel:{settings.orange_sms_sender_number}",
                        "outboundSMSTextMessage": {
                            "message": message
                        }
                    }
                }
                
                sms_response = await client.post(
                    sms_url,
                    json=sms_payload,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json"
                    }
                )
                
                if sms_response.status_code in [200, 201]:
                    logger.info(
                        f"SMS sent via Orange",
                        extra={"extra_fields": {"to": to_number}}
                    )
                    return True
                else:
                    logger.error(
                        f"Failed to send SMS via Orange",
                        extra={"extra_fields": {"to": to_number, "status": sms_response.status_code, "error": sms_response.text}}
                    )
                    return False
                    
        except Exception as e:
            logger.error(
                f"Exception sending SMS via Orange",
                extra={"extra_fields": {"to": to_number, "error": str(e)}},
                exc_info=True
            )
            return False

    async def send_sms(self, to_number: str, message: str) -> bool:
        """Envoie un SMS via le provider configuré (Twilio ou Orange).
        
        Args:
            to_number: Numéro de téléphone du destinataire (format international, ex: +33612345678)
            message: Contenu du SMS (max 160 caractères recommandé)
        
        Returns:
            True si envoyé avec succès, False sinon
        """
        result = await self.send_sms_result(to_number, message)
        return bool(result.ok)

    async def send_sms_result(self, to_number: str, message: str) -> "SMSSendResult":
        if not self.is_configured():
            logger.warning(f"SMS provider '{self.provider}' not configured, cannot send SMS")
            return SMSSendResult(ok=False, provider=self.provider, error="provider_not_configured")

        # Normaliser le numéro (s'assurer qu'il commence par +)
        if not to_number.startswith("+"):
            to_number = f"+{to_number}"

        # Tronquer le message si trop long
        if len(message) > 160:
            message = message[:157] + "..."
            logger.warning(f"SMS message truncated to 160 characters")

        # Envoyer via le provider configuré
        if self.provider == "twilio":
            if self.twilio_client is None:
                return SMSSendResult(ok=False, provider=self.provider, error="twilio_client_unavailable")
            try:
                msg = self.twilio_client.messages.create(
                    body=message,
                    from_=settings.twilio_phone_number,
                    to=to_number
                )
                logger.info(
                    f"SMS sent via Twilio",
                    extra={"extra_fields": {"to": to_number, "sid": msg.sid}}
                )
                return SMSSendResult(ok=True, provider=self.provider, provider_id=getattr(msg, "sid", None))
            except Exception as e:
                logger.error(
                    f"Failed to send SMS via Twilio",
                    extra={"extra_fields": {"to": to_number, "error": str(e)}}
                )
                return SMSSendResult(ok=False, provider=self.provider, error=str(e))
        elif self.provider == "orange":
            ok = await self._send_via_orange(to_number, message)
            return SMSSendResult(ok=ok, provider=self.provider, error=(None if ok else "orange_send_failed"))
        else:
            logger.error(f"Unknown SMS provider: {self.provider}")
            return SMSSendResult(ok=False, provider=self.provider, error="unknown_sms_provider")


@dataclass
class SMSSendResult:
    ok: bool
    provider: Optional[str] = None
    provider_id: Optional[str] = None
    error: Optional[str] = None
