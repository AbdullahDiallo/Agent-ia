"""Media attachment storage service.

Downloads, stores, and tracks incoming media files from WhatsApp, Email, etc.
Supports local filesystem storage with S3 upgrade path.
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..logger import get_logger
from ..models import MediaAttachment

logger = get_logger(__name__)

UPLOAD_DIR = Path(getattr(settings, "upload_dir", None) or "uploads") / "media"
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB

ALLOWED_CONTENT_TYPES = {
    # Documents
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    # Images
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    # Audio
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    # Video
    "video/mp4",
    "video/quicktime",
}


def _safe_filename(original: Optional[str], content_type: str) -> str:
    """Generate a safe filename from original name or content type."""
    ext_map = {
        "application/pdf": ".pdf",
        "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "audio/mpeg": ".mp3",
        "video/mp4": ".mp4",
    }
    if original:
        # Sanitize: keep only alphanumeric, dots, hyphens, underscores
        import re
        safe = re.sub(r"[^\w.\-]", "_", original)
        return safe[:200]
    ext = ext_map.get(content_type, ".bin")
    return f"attachment_{int(time.time())}{ext}"


async def download_and_store(
    db: Session,
    *,
    tenant_id: str,
    source_url: str,
    content_type: str,
    channel: str,
    direction: str = "inbound",
    original_filename: Optional[str] = None,
    conversation_id: Optional[str] = None,
    person_id: Optional[str] = None,
    auth_user: Optional[str] = None,
    auth_password: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Download a file from a URL and store it locally.

    Used for incoming WhatsApp media (Twilio URLs) and email attachments.
    Returns attachment metadata dict or None on failure.
    """
    if content_type and content_type.split(";")[0].strip() not in ALLOWED_CONTENT_TYPES:
        logger.warning(
            "Media type not allowed",
            extra={"extra_fields": {"content_type": content_type, "channel": channel}},
        )
        return None

    # Ensure upload directory exists
    tenant_dir = UPLOAD_DIR / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)

    filename = _safe_filename(original_filename, content_type)
    unique_prefix = uuid4().hex[:8]
    storage_filename = f"{unique_prefix}_{filename}"
    storage_path = tenant_dir / storage_filename

    try:
        # Download the file
        auth = (auth_user, auth_password) if auth_user and auth_password else None
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.get(source_url, auth=auth)
            response.raise_for_status()

        content = response.content
        if len(content) > MAX_FILE_SIZE:
            logger.warning("Media file too large", extra={"extra_fields": {"size": len(content), "max": MAX_FILE_SIZE}})
            return None

        # Write to disk
        storage_path.write_bytes(content)

        # Persist metadata to DB
        attachment = MediaAttachment(
            tenant_id=UUID(str(tenant_id)),
            conversation_id=UUID(str(conversation_id)) if conversation_id else None,
            person_id=UUID(str(person_id)) if person_id else None,
            channel=channel,
            direction=direction,
            original_filename=original_filename or filename,
            content_type=content_type.split(";")[0].strip(),
            file_size_bytes=len(content),
            storage_path=str(storage_path),
            storage_backend="local",
            source_url=source_url[:2000] if source_url else None,
        )
        db.add(attachment)
        db.commit()
        db.refresh(attachment)

        logger.info(
            "Media attachment stored",
            extra={
                "extra_fields": {
                    "attachment_id": str(attachment.id),
                    "tenant_id": tenant_id,
                    "channel": channel,
                    "content_type": content_type,
                    "size_bytes": len(content),
                    "filename": filename,
                }
            },
        )

        return {
            "attachment_id": str(attachment.id),
            "filename": original_filename or filename,
            "content_type": content_type,
            "size_bytes": len(content),
            "storage_path": str(storage_path),
        }

    except httpx.HTTPStatusError as e:
        logger.error(f"Media download failed (HTTP {e.response.status_code}): {source_url[:200]}")
        return None
    except Exception as e:
        logger.error(f"Media storage failed: {e}", exc_info=True)
        # Clean up partial file
        if storage_path.exists():
            storage_path.unlink(missing_ok=True)
        return None


def store_raw_bytes(
    db: Session,
    *,
    tenant_id: str,
    content: bytes,
    content_type: str,
    channel: str,
    direction: str = "inbound",
    original_filename: Optional[str] = None,
    conversation_id: Optional[str] = None,
    person_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Store raw bytes (e.g., from email attachment parsing) directly."""
    if len(content) > MAX_FILE_SIZE:
        return None

    tenant_dir = UPLOAD_DIR / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)

    filename = _safe_filename(original_filename, content_type)
    unique_prefix = uuid4().hex[:8]
    storage_filename = f"{unique_prefix}_{filename}"
    storage_path = tenant_dir / storage_filename

    try:
        storage_path.write_bytes(content)

        attachment = MediaAttachment(
            tenant_id=UUID(str(tenant_id)),
            conversation_id=UUID(str(conversation_id)) if conversation_id else None,
            person_id=UUID(str(person_id)) if person_id else None,
            channel=channel,
            direction=direction,
            original_filename=original_filename or filename,
            content_type=content_type.split(";")[0].strip(),
            file_size_bytes=len(content),
            storage_path=str(storage_path),
            storage_backend="local",
        )
        db.add(attachment)
        db.commit()
        db.refresh(attachment)

        return {
            "attachment_id": str(attachment.id),
            "filename": original_filename or filename,
            "content_type": content_type,
            "size_bytes": len(content),
            "storage_path": str(storage_path),
        }
    except Exception as e:
        logger.error(f"Raw media storage failed: {e}", exc_info=True)
        if storage_path.exists():
            storage_path.unlink(missing_ok=True)
        return None


def get_attachment(db: Session, attachment_id: str) -> Optional[MediaAttachment]:
    try:
        return db.query(MediaAttachment).filter(MediaAttachment.id == UUID(str(attachment_id))).first()
    except Exception:
        return None


def list_attachments_for_conversation(db: Session, conversation_id: str) -> list[MediaAttachment]:
    try:
        return (
            db.query(MediaAttachment)
            .filter(MediaAttachment.conversation_id == UUID(str(conversation_id)))
            .order_by(MediaAttachment.created_at.asc())
            .all()
        )
    except Exception:
        return []


def list_attachments_for_person(db: Session, person_id: str) -> list[MediaAttachment]:
    try:
        return (
            db.query(MediaAttachment)
            .filter(MediaAttachment.person_id == UUID(str(person_id)))
            .order_by(MediaAttachment.created_at.desc())
            .all()
        )
    except Exception:
        return []
