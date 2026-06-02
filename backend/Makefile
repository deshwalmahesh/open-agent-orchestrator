.PHONY: install dev test lint format up down logs

COMPOSE := docker compose -f ../docker-compose.yml

install:
	uv sync

dev:
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	uv run pytest

lint:
	uv run ruff check app tests

format:
	uv run ruff format app tests

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f backend
