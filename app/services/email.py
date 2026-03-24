from __future__ import annotations

import asyncio
import base64
import os
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from ..config import settings
from .provider_config import get_email_provider_status, resolve_email_provider

# Utiliser le même namespace que le reste de l'app pour que les logs
# soient bien captés par la configuration logging centrale.
logger = logging.getLogger("agentia.app.services.email")


@dataclass
class EmailAttachment:
    """Represents an email attachment."""
    filename: str
    content: bytes  # Raw file bytes
    content_type: str = "application/octet-stream"  # MIME type


@dataclass
class EmailSendResult:
    ok: bool
    provider: Optional[str] = None
    provider_id: Optional[str] = None
    error: Optional[str] = None

try:
    import sendgrid
    from sendgrid.helpers.mail import Mail, Email, To, Content
except Exception:  # keep optional import to allow running without package installed immediately
    sendgrid = None
    Mail = Email = To = Content = object  # type: ignore


class EmailService:
    def __init__(self) -> None:
        self.provider = resolve_email_provider(settings)
        self.from_email: Optional[str] = settings.from_email
        self.from_name: Optional[str] = settings.from_name or settings.mail_from_name or "AgentIA"
        self.api_key: Optional[str] = None
        provider_status = get_email_provider_status(settings)

        if not provider_status["configured"]:
            logger.warning(
                "Selected email provider is not fully configured",
                extra={
                    "extra_fields": {
                        "provider": self.provider,
                        "missing_required": provider_status["missing_required"],
                    }
                },
            )

        if self.provider == "brevo":
            self.api_key = settings.brevo_api_key
        elif self.provider == "sendgrid":
            self.api_key = settings.sendgrid_api_key

    def is_configured(self) -> bool:
        if self.provider == "smtp":
            return bool(settings.mail_host and settings.mail_username and settings.mail_password and self.from_email)
        if self.provider == "gmail":
            return bool(settings.gmail_smtp_user and settings.gmail_smtp_pass and self.from_email)
        if self.provider == "brevo":
            return bool(settings.brevo_api_key and self.from_email)
        if self.provider == "sendgrid":
            return bool(settings.sendgrid_api_key and self.from_email)
        return False

    async def send_email(self, to_email: str, subject: str, html_body: str, text_body: Optional[str] = None) -> bool:
        """Envoie un email via le provider configuré.

        Args:
            to_email: Adresse email du destinataire
            subject: Sujet de l'email
            html_body: Corps de l'email en HTML
            text_body: Corps de l'email en texte brut (optionnel)

        Returns:
            True si l'email a été envoyé avec succès, False sinon
        """
        result = await self.send_email_result(to_email, subject, html_body, text_body)
        return bool(result.ok)

    async def send_email_result(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: Optional[str] = None,
    ) -> EmailSendResult:
        return await asyncio.to_thread(self.send_followup_result, to_email, subject, html_body, text_body)

    async def send_email_with_attachments(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: Optional[str] = None,
        attachments: Optional[List[EmailAttachment]] = None,
    ) -> bool:
        """Send an email with optional file attachments.

        Supports attachments via SendGrid, Brevo, and SMTP/Gmail providers.
        """
        if not attachments:
            return await self.send_email(to_email, subject, html_body, text_body)
        return await asyncio.to_thread(
            self._send_with_attachments_sync, to_email, subject, html_body, text_body, attachments,
        )

    def _send_with_attachments_sync(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str],
        attachments: List[EmailAttachment],
    ) -> bool:
        """Synchronous send with attachments via SMTP (works for all SMTP-based providers)."""
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.base import MIMEBase
        from email import encoders

        # Determine SMTP settings based on provider
        if self.provider == "gmail":
            host = "smtp.gmail.com"
            port = 587
            username = settings.gmail_smtp_user
            password = settings.gmail_smtp_pass
        elif self.provider == "smtp":
            host = settings.mail_host
            port = settings.mail_port or 587
            username = settings.mail_username
            password = settings.mail_password
        elif self.provider in ("sendgrid", "brevo"):
            # For API-based providers, fall back to sending without attachments
            # (attachments via API require provider-specific handling)
            return self._send_with_attachments_api(to_email, subject, html_content, text_content, attachments)
        else:
            logger.warning(f"Attachments not supported for provider: {self.provider}")
            return False

        if not host or not username or not password:
            logger.error("SMTP credentials missing for attachment email")
            return False

        try:
            msg = MIMEMultipart("mixed")
            msg["From"] = f"{self.from_name} <{self.from_email}>" if self.from_name else self.from_email
            msg["To"] = to_email
            msg["Subject"] = subject

            # Body
            body_part = MIMEMultipart("alternative")
            if text_content:
                body_part.attach(MIMEText(text_content, "plain", "utf-8"))
            body_part.attach(MIMEText(html_content, "html", "utf-8"))
            msg.attach(body_part)

            # Attachments
            for att in attachments:
                part = MIMEBase(*att.content_type.split("/", 1)) if "/" in att.content_type else MIMEBase("application", "octet-stream")
                part.set_payload(att.content)
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f'attachment; filename="{att.filename}"')
                msg.attach(part)

            with smtplib.SMTP(host, int(port)) as server:
                server.starttls()
                server.login(username, password)
                server.send_message(msg)

            logger.info(f"Email with {len(attachments)} attachment(s) sent to {to_email}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email with attachments: {e}", exc_info=True)
            return False

    def _send_with_attachments_api(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str],
        attachments: List[EmailAttachment],
    ) -> bool:
        """Send with attachments via SendGrid or Brevo API."""
        if self.provider == "sendgrid" and sendgrid and self.api_key:
            try:
                from sendgrid.helpers.mail import Attachment, FileContent, FileName, FileType, Disposition
                sg = sendgrid.SendGridAPIClient(api_key=self.api_key)
                mail = Mail(
                    from_email=Email(self.from_email, self.from_name),
                    to_emails=To(to_email),
                    subject=subject,
                    html_content=Content("text/html", html_content),
                )
                for att in attachments:
                    sg_att = Attachment(
                        FileContent(base64.b64encode(att.content).decode()),
                        FileName(att.filename),
                        FileType(att.content_type),
                        Disposition("attachment"),
                    )
                    mail.add_attachment(sg_att)
                response = sg.client.mail.send.post(request_body=mail.get())
                return response.status_code in (200, 201, 202)
            except Exception as e:
                logger.error(f"SendGrid attachment send failed: {e}", exc_info=True)
                return False

        if self.provider == "brevo" and self.api_key:
            try:
                att_list = [
                    {"name": att.filename, "content": base64.b64encode(att.content).decode()}
                    for att in attachments
                ]
                payload = {
                    "sender": {"email": self.from_email, "name": self.from_name or "Admissions"},
                    "to": [{"email": to_email}],
                    "subject": subject,
                    "htmlContent": html_content,
                    "attachment": att_list,
                }
                import httpx
                resp = httpx.post(
                    "https://api.brevo.com/v3/smtp/email",
                    headers={"api-key": self.api_key, "Content-Type": "application/json"},
                    json=payload,
                    timeout=15,
                )
                return resp.status_code in (200, 201, 202)
            except Exception as e:
                logger.error(f"Brevo attachment send failed: {e}", exc_info=True)
                return False

        logger.warning(f"Attachment send not implemented for provider: {self.provider}")
        return False

    def send_followup(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None
    ) -> bool:
        return self.send_followup_result(to_email, subject, html_content, text_content).ok

    def send_followup_result(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
    ) -> EmailSendResult:
        if self.provider == "smtp":
            return self._send_via_smtp(
                to_email,
                subject,
                html_content,
                text_content,
                host=settings.mail_host,
                port=settings.mail_port or 587,
                username=settings.mail_username,
                password=settings.mail_password,
            )
        if self.provider == "brevo":
            return self._send_via_brevo(to_email, subject, html_content, text_content)
        if self.provider == "gmail":
            return self._send_via_gmail_smtp(to_email, subject, html_content, text_content)
        if self.provider == "sendgrid":
            return self._send_via_sendgrid(to_email, subject, html_content)
        return EmailSendResult(ok=False, provider=self.provider, error="unknown_email_provider")

    def _send_via_sendgrid(self, to_email: str, subject: str, html_content: str) -> EmailSendResult:
        if not self.is_configured():
            return EmailSendResult(ok=False, provider=self.provider, error="provider_not_configured")
        if sendgrid is None:
            return EmailSendResult(ok=False, provider=self.provider, error="sendgrid_sdk_missing")
        try:
            sg = sendgrid.SendGridAPIClient(api_key=self.api_key)
            message = Mail(
                from_email=Email(self.from_email),
                to_emails=To(to_email),
                subject=subject,
                html_content=Content("text/html", html_content),
            )
            resp = sg.client.mail.send.post(request_body=message.get())
            ok = 200 <= getattr(resp, "status_code", 500) < 300
            headers = dict(getattr(resp, "headers", {}) or {})
            provider_id = headers.get("X-Message-Id") or headers.get("x-message-id")
            return EmailSendResult(ok=ok, provider=self.provider, provider_id=provider_id)
        except Exception as exc:
            return EmailSendResult(ok=False, provider=self.provider, error=str(exc))

    def _send_via_brevo(self, to_email: str, subject: str, html_content: str, text_content: Optional[str] = None) -> EmailSendResult:
        """Envoie un email via l'API HTTP Brevo (contourne les restrictions SMTP port 587).

        Utilise l'API REST de Brevo au lieu de SMTP, ce qui permet de fonctionner
        sur Render et autres plateformes qui bloquent le port 587.
        """
        if not self.is_configured():
            logger.warning("Brevo email service not configured")
            return EmailSendResult(ok=False, provider=self.provider, error="provider_not_configured")

        try:
            # Construction du payload comme dans Laravel
            payload = {
                "sender": {
                    "email": self.from_email,
                    "name": self.from_name
                },
                "to": [
                    {"email": to_email}
                ],
                "subject": subject,
                "htmlContent": html_content
            }

            # Ajouter le contenu texte si fourni
            if text_content:
                payload["textContent"] = text_content

            logger.info(f"Sending email via Brevo HTTP API to {to_email}", extra={
                "to": to_email,
                "subject": subject,
                "provider": "brevo"
            })

            # Appel HTTP à l'API Brevo (pas de SMTP !)
            response = httpx.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={
                    "api-key": self.api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                },
                json=payload,
                timeout=30.0
            )

            if response.status_code >= 200 and response.status_code < 300:
                provider_id = None
                try:
                    provider_id = (response.json() or {}).get("messageId")
                except Exception:
                    provider_id = None
                logger.info(f"Email sent successfully via Brevo to {to_email}", extra={
                    "status_code": response.status_code,
                    "message_id": provider_id
                })
                return EmailSendResult(ok=True, provider=self.provider, provider_id=provider_id)
            else:
                logger.error(f"Brevo API error: {response.status_code}", extra={
                    "status_code": response.status_code,
                    "response_body": response.text,
                    "to": to_email
                })
                return EmailSendResult(ok=False, provider=self.provider, error=f"status_{response.status_code}")

        except httpx.TimeoutException as e:
            logger.error(f"Brevo API timeout: {e}", extra={"to": to_email})
            return EmailSendResult(ok=False, provider=self.provider, error=str(e))
        except httpx.HTTPError as e:
            logger.error(f"Brevo HTTP error: {e}", extra={"to": to_email})
            return EmailSendResult(ok=False, provider=self.provider, error=str(e))
        except Exception as e:
            logger.error(f"Brevo unexpected error: {e}", extra={"to": to_email}, exc_info=True)
            return EmailSendResult(ok=False, provider=self.provider, error=str(e))

    def _send_via_gmail_smtp(self, to_email: str, subject: str, html_content: str, text_content: Optional[str] = None) -> EmailSendResult:
        return self._send_via_smtp(
            to_email,
            subject,
            html_content,
            text_content,
            host=settings.gmail_smtp_host,
            port=settings.gmail_smtp_port,
            username=settings.gmail_smtp_user,
            password=settings.gmail_smtp_pass,
        )

    def _send_via_smtp(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str],
        *,
        host: Optional[str],
        port: int,
        username: Optional[str],
        password: Optional[str],
    ) -> EmailSendResult:
        try:
            import smtplib
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText
        except Exception:
            return EmailSendResult(ok=False, provider=self.provider, error="smtp_runtime_unavailable")
        if not host or not username or not password or not self.from_email:
            return EmailSendResult(ok=False, provider=self.provider, error="provider_not_configured")
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.from_email or ""
            msg["To"] = to_email
            if text_content:
                msg.attach(MIMEText(text_content, "plain", _charset="utf-8"))
            msg.attach(MIMEText(html_content, "html", _charset="utf-8"))

            with smtplib.SMTP(host, port) as server:
                server.starttls()
                server.login(username, password)
                server.sendmail(self.from_email, [to_email], msg.as_string())
            return EmailSendResult(ok=True, provider=self.provider, provider_id=msg.get("Message-ID"))
        except Exception as exc:
            return EmailSendResult(ok=False, provider=self.provider, error=str(exc))
