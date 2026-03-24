#!/bin/sh

set -eu

PORT="${PORT:-8000}"
WEB_CONCURRENCY="${WEB_CONCURRENCY:-1}"
LOG_LEVEL_RAW="${LOG_LEVEL:-info}"
LOG_LEVEL="$(printf '%s' "${LOG_LEVEL_RAW}" | tr '[:upper:]' '[:lower:]')"

case "${LOG_LEVEL}" in
  critical|error|warning|info|debug|trace) ;;
  *) LOG_LEVEL="info" ;;
esac

exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --workers "${WEB_CONCURRENCY}" \
  --loop uvloop \
  --http httptools \
  --log-level "${LOG_LEVEL}"
