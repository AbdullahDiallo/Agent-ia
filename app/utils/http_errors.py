from __future__ import annotations

from typing import Any
from uuid import uuid4

from ..logger import get_logger


def public_error_detail(
    *,
    code: str,
    exc: Exception,
    logger_name: str,
    context: dict[str, Any] | None = None,
) -> dict[str, str]:
    correlation_id = uuid4().hex
    logger = get_logger(logger_name)
    extra_fields = {"code": code, "correlation_id": correlation_id, "error": str(exc)}
    if context:
        extra_fields.update(context)
    logger.error(
        "Public error raised",
        extra={"extra_fields": extra_fields},
        exc_info=True,
    )
    return {"message": code, "correlation_id": correlation_id}
