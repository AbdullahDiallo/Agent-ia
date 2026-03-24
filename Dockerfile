# ============================================
# Multi-stage Dockerfile for Agentia Scolaire
# ============================================
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Security: run as non-root user
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

# System dependencies for psycopg, scipy, audioop
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini .
COPY scripts/ ./scripts/

# Create necessary directories
RUN mkdir -p /app/logs /app/uploads \
    && chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f\"http://localhost:{os.getenv('PORT', '8000')}/health/live\")" || exit 1

# Expose port
EXPOSE 8000

# Ensure the startup script remains executable for the non-root user.
RUN chmod +x /app/scripts/start-backend.sh

# Run with uvicorn using Render-compatible PORT and a single worker by default.
CMD ["/app/scripts/start-backend.sh"]
