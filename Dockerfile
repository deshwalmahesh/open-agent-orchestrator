# Single-image production build: frontend (static) + backend (FastAPI serves it).
# Deploys on any Docker host. SQLite + no Redis works out of the box; for real
# load swap DATABASE_URL to Postgres and add REDIS_URL.

# ── Stage 1: build frontend ──────────────────────────────────────────────────
FROM node:20-alpine AS frontend
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
ENV VITE_API_URL=""
RUN npm run build

# ── Stage 2: backend + static files ──────────────────────────────────────────
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.8.12 /uv /usr/local/bin/uv
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev

COPY backend/app ./app
COPY --from=frontend /app/dist ./static

EXPOSE 8000
# Shell form so $PORT expands. Most managed hosts inject PORT; falls back to
# 8000 for plain `docker run`. Matches EXPOSE for hosts that route by EXPOSE.
CMD ["sh", "-c", "uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
