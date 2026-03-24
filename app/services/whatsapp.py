import httpx
from typing import Any, Dict, List, Optional

try:
    from twilio.rest import Client as TwilioClient
except Exception:  # pragma: no cover - dependance optionnelle
    TwilioClient = None

from ..config import settings
from ..logger import get_logger
from .provider_config import get_whatsapp_provider_status, resolve_whatsapp_provider

logger = get_logger(__name__)


class WhatsAppService:
    def __init__(self):
        self.provider = resolve_whatsapp_provider(settings)
        self._enabled = False
        self.twilio_client: Optional[TwilioClient] = None
        provider_status = get_whatsapp_provider_status(settings)
        if not provider_status["configured"]:
            missing = provider_status["missing_required"]
            if self.provider == "twilio" and "TWILIO_WHATSAPP_NUMBER" in missing:
                logger.warning(
                    "Twilio WhatsApp sender not configured: set TWILIO_WHATSAPP_NUMBER (whatsapp:+E164)"
                )
            elif self.provider == "twilio":
                logger.warning("Twilio WhatsApp credentials not configured")
            else:
                logger.warning("Meta WhatsApp Cloud credentials not configured")
            logger.warning(
                "Selected WhatsApp provider is not fully configured",
                extra={
                    "extra_fields": {
                        "provider": self.provider,
                        "missing_required": missing,
                    }
                },
            )
            return

        if self.provider == "twilio":
            if not TwilioClient:
                logger.warning("Twilio SDK not installed; WhatsApp Twilio disabled")
                return
            if not settings.twilio_account_sid or not settings.twilio_auth_token:
                logger.warning("Twilio WhatsApp credentials not configured")
                return
            if not settings.twilio_whatsapp_number:
                logger.warning("Twilio WhatsApp sender not configured: set TWILIO_WHATSAPP_NUMBER (whatsapp:+E164)")
                return
            try:
                self.twilio_client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)
                self._enabled = bool(self._twilio_sender())
            except Exception:
                logger.exception("Failed to initialize Twilio WhatsApp client")
                self._enabled = False
            if not self._enabled:
                logger.warning("Twilio WhatsApp sender not configured")
            return

        if (
            not settings.meta_whatsapp_phone_number_id
            or not settings.meta_whatsapp_access_token
            or not settings.meta_whatsapp_verify_token
            or not settings.meta_whatsapp_app_secret
        ):
            logger.warning("Meta WhatsApp Cloud credentials not configured")
            self._enabled = False
        else:
            self._enabled = True

    def is_configured(self) -> bool:
        return self._enabled

    @staticmethod
    def _normalize_twilio_whatsapp_number(phone: str) -> str:
        raw = (phone or "").strip()
        if not raw:
            return raw
        return raw if raw.startswith("whatsapp:") else f"whatsapp:{raw}"

    def _twilio_sender(self) -> Optional[str]:
        sender = settings.twilio_whatsapp_number
        if not sender:
            return None
        return self._normalize_twilio_whatsapp_number(sender)

    async def _send_via_twilio(self, to_number: str, message: str) -> bool:
        if not self.twilio_client:
            logger.error("Twilio WhatsApp client unavailable")
            return False
        sender = self._twilio_sender()
        if not sender:
            logger.error("Twilio WhatsApp sender number missing")
            return False
        recipient = self._normalize_twilio_whatsapp_number(to_number)
        try:
            msg = self.twilio_client.messages.create(
                body=message,
                from_=sender,
                to=recipient,
            )
            logger.info(
                "Message WhatsApp envoye via Twilio",
                extra={
                    "extra_fields": {
                        "to": recipient,
                        "sid": getattr(msg, "sid", None),
                    }
                },
            )
            return True
        except Exception as e:
            if getattr(e, "code", None) == 63007:
                logger.error(
                    "Erreur envoi WhatsApp Twilio: sender WhatsApp invalide/non connecte (63007). "
                    "Configurez TWILIO_WHATSAPP_NUMBER avec un sender WhatsApp Twilio actif (sandbox ou numero approuve).",
                    exc_info=True,
                )
                return False
            logger.error(f"Erreur envoi WhatsApp Twilio: {e}", exc_info=True)
            return False

    async def _send_via_meta(self, to_number: str, message: str) -> bool:
        if not settings.meta_whatsapp_phone_number_id or not settings.meta_whatsapp_access_token:
            logger.error("WhatsApp Meta service not configured")
            return False

        url = f"https://graph.facebook.com/{settings.meta_api_version}/{settings.meta_whatsapp_phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {settings.meta_whatsapp_access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "text",
            "text": {"body": message},
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(url, headers=headers, json=payload)

            if response.status_code not in (200, 201):
                logger.error(
                    "Erreur envoi WhatsApp Meta",
                    extra={
                        "extra_fields": {
                            "status_code": response.status_code,
                            "response_text": response.text,
                            "to": to_number,
                        }
                    },
                )
                return False

            data = response.json()
            logger.info(
                "Message WhatsApp envoyé via Meta",
                extra={
                    "extra_fields": {
                        "to": to_number,
                        "meta_response": data,
                    }
                },
            )

            return True

        except Exception as e:
            logger.error(f"Erreur envoi WhatsApp Meta: {e}", exc_info=True)
            return False

    async def send_message(self, to_number: str, message: str) -> bool:
        """Envoyer un message WhatsApp via le provider configure."""
        if not self._enabled:
            logger.error("WhatsApp service not configured")
            return False
        if self.provider == "twilio":
            return await self._send_via_twilio(to_number, message)
        return await self._send_via_meta(to_number, message)

    # ----------------------------------------------------------------
    # Attachments (media messages)
    # ----------------------------------------------------------------

    async def send_media(
        self,
        to_number: str,
        media_url: str,
        *,
        caption: Optional[str] = None,
        media_type: str = "document",
        filename: Optional[str] = None,
    ) -> bool:
        """Send a media message (document, image, audio, video) via WhatsApp.

        Args:
            to_number: Recipient phone number (E.164 format)
            media_url: Public URL of the media file
            caption: Optional caption text
            media_type: One of 'document', 'image', 'audio', 'video'
            filename: Optional filename for documents
        """
        if not self._enabled:
            logger.error("WhatsApp service not configured")
            return False
        if self.provider == "twilio":
            return await self._send_media_twilio(to_number, media_url, caption=caption)
        return await self._send_media_meta(
            to_number, media_url, caption=caption, media_type=media_type, filename=filename,
        )

    async def _send_media_twilio(
        self, to_number: str, media_url: str, *, caption: Optional[str] = None,
    ) -> bool:
        if not self.twilio_client:
            return False
        sender = self._twilio_sender()
        if not sender:
            return False
        recipient = self._normalize_twilio_whatsapp_number(to_number)
        try:
            kwargs: Dict[str, Any] = {
                "from_": sender,
                "to": recipient,
                "media_url": [media_url],
            }
            if caption:
                kwargs["body"] = caption
            msg = self.twilio_client.messages.create(**kwargs)
            logger.info(
                "WhatsApp media sent via Twilio",
                extra={"extra_fields": {"to": recipient, "sid": getattr(msg, "sid", None)}},
            )
            return True
        except Exception as e:
            logger.error(f"WhatsApp Twilio media send failed: {e}", exc_info=True)
            return False

    async def _send_media_meta(
        self,
        to_number: str,
        media_url: str,
        *,
        caption: Optional[str] = None,
        media_type: str = "document",
        filename: Optional[str] = None,
    ) -> bool:
        if not settings.meta_whatsapp_phone_number_id or not settings.meta_whatsapp_access_token:
            return False
        url = (
            f"https://graph.facebook.com/{settings.meta_api_version}"
            f"/{settings.meta_whatsapp_phone_number_id}/messages"
        )
        headers = {
            "Authorization": f"Bearer {settings.meta_whatsapp_access_token}",
            "Content-Type": "application/json",
        }
        media_object: Dict[str, Any] = {"link": media_url}
        if caption:
            media_object["caption"] = caption
        if filename and media_type == "document":
            media_object["filename"] = filename
        payload: Dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": media_type,
            media_type: media_object,
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(url, headers=headers, json=payload)
            if response.status_code not in (200, 201):
                logger.error(
                    "WhatsApp Meta media send failed",
                    extra={"extra_fields": {"status": response.status_code, "body": response.text[:500]}},
                )
                return False
            logger.info("WhatsApp media sent via Meta", extra={"extra_fields": {"to": to_number, "type": media_type}})
            return True
        except Exception as e:
            logger.error(f"WhatsApp Meta media send error: {e}", exc_info=True)
            return False

    # ----------------------------------------------------------------
    # WhatsApp Business Templates (proactive messaging)
    # ----------------------------------------------------------------

    async def send_template(
        self,
        to_number: str,
        template_name: str,
        *,
        language_code: str = "fr",
        components: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """Send a WhatsApp Business template message (proactive, outside 24h window).

        Args:
            to_number: Recipient phone number (E.164)
            template_name: Approved template name (e.g. 'rdv_confirmation', 'admission_reminder')
            language_code: Template language code (fr, en, etc.)
            components: Optional template components (header, body, button parameters)
        """
        if not self._enabled:
            logger.error("WhatsApp service not configured for templates")
            return False
        if self.provider == "twilio":
            return await self._send_template_twilio(
                to_number, template_name, language_code=language_code, components=components,
            )
        return await self._send_template_meta(
            to_number, template_name, language_code=language_code, components=components,
        )

    async def _send_template_twilio(
        self,
        to_number: str,
        template_name: str,
        *,
        language_code: str = "fr",
        components: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """Send template via Twilio Content API."""
        if not self.twilio_client:
            return False
        sender = self._twilio_sender()
        if not sender:
            return False
        recipient = self._normalize_twilio_whatsapp_number(to_number)
        try:
            # Twilio uses content_sid for templates
            # Build body parameters from components
            body_params: Dict[str, str] = {}
            if components:
                for comp in components:
                    if comp.get("type") == "body":
                        for i, param in enumerate(comp.get("parameters", []), 1):
                            body_params[str(i)] = str(param.get("text", ""))

            kwargs: Dict[str, Any] = {
                "from_": sender,
                "to": recipient,
            }
            # If we have a content_sid mapping, use it; otherwise fallback to body text
            content_sid = getattr(settings, "twilio_template_content_sids", {}).get(template_name)
            if content_sid:
                kwargs["content_sid"] = content_sid
                if body_params:
                    kwargs["content_variables"] = str(body_params)
            else:
                # Fallback: send as regular message with template name reference
                fallback_body = f"[Template: {template_name}]"
                if body_params:
                    fallback_body += " " + " | ".join(body_params.values())
                kwargs["body"] = fallback_body

            msg = self.twilio_client.messages.create(**kwargs)
            logger.info(
                "WhatsApp template sent via Twilio",
                extra={"extra_fields": {"to": recipient, "template": template_name, "sid": getattr(msg, "sid", None)}},
            )
            return True
        except Exception as e:
            logger.error(f"WhatsApp Twilio template send failed: {e}", exc_info=True)
            return False

    async def _send_template_meta(
        self,
        to_number: str,
        template_name: str,
        *,
        language_code: str = "fr",
        components: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """Send template via Meta WhatsApp Cloud API."""
        if not settings.meta_whatsapp_phone_number_id or not settings.meta_whatsapp_access_token:
            return False
        url = (
            f"https://graph.facebook.com/{settings.meta_api_version}"
            f"/{settings.meta_whatsapp_phone_number_id}/messages"
        )
        headers = {
            "Authorization": f"Bearer {settings.meta_whatsapp_access_token}",
            "Content-Type": "application/json",
        }
        template_obj: Dict[str, Any] = {
            "name": template_name,
            "language": {"code": language_code},
        }
        if components:
            template_obj["components"] = components
        payload: Dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "template",
            "template": template_obj,
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(url, headers=headers, json=payload)
            if response.status_code not in (200, 201):
                logger.error(
                    "WhatsApp Meta template send failed",
                    extra={"extra_fields": {"status": response.status_code, "body": response.text[:500], "template": template_name}},
                )
                return False
            logger.info(
                "WhatsApp template sent via Meta",
                extra={"extra_fields": {"to": to_number, "template": template_name}},
            )
            return True
        except Exception as e:
            logger.error(f"WhatsApp Meta template send error: {e}", exc_info=True)
            return False
