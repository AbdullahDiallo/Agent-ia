from __future__ import annotations

import asyncio
import signal

from app.db import SessionLocal
from app.logger import get_logger
from app.services.outbox import process_outbox_batch

logger = get_logger(__name__)
_running = True


def _stop(*_args):
    global _running
    _running = False


async def main():
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    logger.info("Outbox worker started")
    while _running:
        db = SessionLocal()
        try:
            stats = await process_outbox_batch(db)
            if stats["processed"] > 0:
                logger.info("Outbox worker tick", extra={"extra_fields": stats})
        except Exception as exc:
            logger.error("Outbox worker loop failed", extra={"extra_fields": {"error": str(exc)}}, exc_info=True)
        finally:
            db.close()
        await asyncio.sleep(5)
    logger.info("Outbox worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
