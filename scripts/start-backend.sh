#!/bin/sh

set -eu

PORT="${PORT:-8000}"
WEB_CONCURRENCY="${WEB_CONCURRENCY:-1}"
LOG_LEVEL="${LOG_LEVEL:-info}"

exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --workers "${WEB_CONCURRENCY}" \
  --loop uvloop \
  --http httptools \
  --log-level "${LOG_LEVEL}"
