.PHONY: sync format lint typecheck test check migrate api bot compose-up compose-down test-db-up test-db-down

sync:
	uv sync --frozen --group dev

format:
	uv run ruff format .

lint:
	uv run ruff check .

typecheck:
	uv run mypy src tests

test:
	uv run pytest

check:
	sh scripts/run_checks.sh

migrate:
	uv run alembic upgrade head

api:
	uv run uvicorn flowmate.api.app:create_app --factory --reload

bot:
	uv run python -m flowmate.bot

compose-up:
	docker compose up --build

compose-down:
	docker compose down

test-db-up:
	docker compose -f docker-compose.test.yml up -d --wait

test-db-down:
	docker compose -f docker-compose.test.yml down
