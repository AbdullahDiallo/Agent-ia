#!/usr/bin/env python3

from __future__ import annotations

import os
import sys
from pathlib import Path

from watchfiles import PythonFilter, run_process


ROOT_DIR = Path(__file__).resolve().parent.parent


def main() -> int:
    os.chdir(ROOT_DIR)

    command = (
        f"{sys.executable} -m uvicorn app.main:app "
        "--host 0.0.0.0 "
        "--port 8000 "
        "--log-level info"
    )

    watch_filter = PythonFilter(
        ignore_paths=[
            ROOT_DIR / ".venv",
            ROOT_DIR / "front",
            ROOT_DIR / "logs",
            ROOT_DIR / ".git",
            ROOT_DIR / "__pycache__",
        ]
    )

    return run_process(
        ROOT_DIR / "app",
        ROOT_DIR / "tests",
        target=command,
        target_type="command",
        watch_filter=watch_filter,
        grace_period=0,
        debounce=400,
        step=50,
    )


if __name__ == "__main__":
    raise SystemExit(main())
