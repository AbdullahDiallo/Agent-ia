from __future__ import annotations

from typing import Optional, Dict, Any
from datetime import datetime
from uuid import UUID
from jinja2 import Environment, BaseLoader, select_autoescape, StrictUndefined
from sqlalchemy.orm import Session

from ..config import settings
from ..models import EmailTemplate


_env = Environment(
    loader=BaseLoader(),
    autoescape=select_autoescape(enabled_extensions=("html",)),
    undefined=StrictUndefined,
)


def _format_datetime(value, fmt: str = "%d/%m/%Y %H:%M") -> str:
    """Filtre Jinja pour formater une date/heure.

    Accepte un objet datetime ou une chaîne ISO.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except Exception:
            return str(value)
    return dt.strftime(fmt)


def _format_time(value, fmt: str = "%H:%M") -> str:
    """Filtre Jinja pour formater seulement l'heure.

    Utilisé par certains templates de rendez-vous.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except Exception:
            return str(value)
    return dt.strftime(fmt)


_env.filters["format_datetime"] = _format_datetime
_env.filters["format_time"] = _format_time


def upsert_email_template(
    db: Session,
    *,
    name: str,
    subject_template: str,
    html_template: str,
    text_template: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> EmailTemplate:
    tenant_uuid = UUID(str(tenant_id or settings.default_tenant_id))
    tpl = (
        db.query(EmailTemplate)
        .filter(EmailTemplate.tenant_id == tenant_uuid, EmailTemplate.name == name)
        .first()
    )
    if tpl is None:
        tpl = EmailTemplate(
            tenant_id=tenant_uuid,
            name=name,
            subject_template=subject_template,
            html_template=html_template,
            text_template=text_template,
        )
        db.add(tpl)
    else:
        tpl.subject_template = subject_template
        tpl.html_template = html_template
        tpl.text_template = text_template
    db.commit()
    db.refresh(tpl)
    return tpl


def get_email_template(db: Session, *, name_or_id: str, tenant_id: Optional[str] = None) -> Optional[EmailTemplate]:
    tenant_uuid = UUID(str(tenant_id or settings.default_tenant_id))
    # Try by name first, else by id
    tpl = (
        db.query(EmailTemplate)
        .filter(EmailTemplate.tenant_id == tenant_uuid, EmailTemplate.name == name_or_id)
        .first()
    )
    if tpl:
        return tpl
    try:
        uid = UUID(name_or_id)
        return (
            db.query(EmailTemplate)
            .filter(EmailTemplate.tenant_id == tenant_uuid, EmailTemplate.id == uid)
            .first()
        )
    except Exception:
        return None


def render_email_template(
    tpl: EmailTemplate,
    context: Dict[str, Any],
) -> Dict[str, str]:
    subject = _env.from_string(tpl.subject_template).render(**context)
    html = _env.from_string(tpl.html_template).render(**context)
    text = _env.from_string(tpl.text_template).render(**context) if tpl.text_template else ""
    return {"subject": subject, "html": html, "text": text}
