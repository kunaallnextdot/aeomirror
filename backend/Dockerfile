FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code (includes alembic/ and alembic.ini for migrations on startup).
COPY . .
RUN chmod +x entrypoint.sh

# Run as a non-root user.
RUN adduser --disabled-password --gecos "" appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

# Liveness check (no curl in slim image; use python stdlib).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://localhost:8000/health').status==200 else sys.exit(1)" || exit 1

# Migrations + server. DATABASE_URL / REDIS_URL / CORS_ORIGINS come from the env.
ENTRYPOINT ["./entrypoint.sh"]
