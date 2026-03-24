from __future__ import annotations

import argparse
import asyncio

from app.scheduler import send_email_reminders_job
from app.db import SessionLocal
from app.services.outbox import process_outbox_batch


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run school automation jobs (appointments reminders/follow-up).",
    )
    parser.add_argument(
        "--job",
        choices=["email_reminders", "outbox", "all"],
        default="email_reminders",
        help="Job to run",
    )
    args = parser.parse_args()

    if args.job in {"email_reminders", "all"}:
        asyncio.run(send_email_reminders_job())
        print({"job": "email_reminders", "status": "ok"})
    if args.job in {"outbox", "all"}:
        db = SessionLocal()
        try:
            stats = asyncio.run(process_outbox_batch(db))
            print({"job": "outbox", "status": "ok", **stats})
        finally:
            db.close()


if __name__ == "__main__":
    main()
