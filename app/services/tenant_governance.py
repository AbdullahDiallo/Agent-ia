from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from ..config import settings
from ..models import TenantQuotaUsage, TenantSettings


def _period_key(dt: Optional[datetime] = None) -> str:
    now = dt or datetime.utcnow()
    return f"{now.year:04d}-{now.month:02d}"


def _metric_limit(settings_row: Optional[TenantSettings], metric: str) -> int:
    if metric == "rendezvous":
        return int((settings_row.monthly_rdv_limit if settings_row else 500))
    if metric == "calls":
        return int((settings_row.monthly_call_limit if settings_row else 2000))
    return int((settings_row.monthly_message_limit if settings_row else 5000))


def check_and_increment_quota(
    db: Session,
    *,
    tenant_id: str,
    metric: str,
    increment: int = 1,
) -> bool:
    tenant_uuid = UUID(str(tenant_id))
    period = _period_key()
    settings_row = (
        db.query(TenantSettings)
        .filter(TenantSettings.tenant_id == tenant_uuid)
        .first()
    )
    limit = _metric_limit(settings_row, metric)

    usage = (
        db.query(TenantQuotaUsage)
        .filter(
            TenantQuotaUsage.tenant_id == tenant_uuid,
            TenantQuotaUsage.metric == metric,
            TenantQuotaUsage.period == period,
        )
        .first()
    )
    if not usage:
        usage = TenantQuotaUsage(
            tenant_id=tenant_uuid,
            metric=metric,
            period=period,
            used_count=0,
        )
        db.add(usage)
        db.flush()

    if int(usage.used_count or 0) + int(increment) > limit:
        db.rollback()
        return False

    usage.used_count = int(usage.used_count or 0) + int(increment)
    db.add(usage)
    db.commit()
    return True


def ensure_default_tenant_settings(db: Session, tenant_id: str) -> TenantSettings:
    tenant_uuid = UUID(str(tenant_id))
    row = db.query(TenantSettings).filter(TenantSettings.tenant_id == tenant_uuid).first()
    if row:
        return row
    row = TenantSettings(
        tenant_id=tenant_uuid,
        default_language="fr",
        enabled_channels="chat,email,sms,whatsapp,call",
        monthly_rdv_limit=500,
        monthly_message_limit=5000,
        monthly_call_limit=2000,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
