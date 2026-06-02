.PHONY: up down logs test clean

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

test:
	cd backend && uv run pytest

clean:
	docker compose down -v
