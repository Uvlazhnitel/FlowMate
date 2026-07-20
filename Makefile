.DEFAULT_GOAL := help
revision ?= -1

.PHONY: help setup sync format lint typecheck test check migrate migration downgrade migration-current migration-history api bot up up-all down logs ps test-db-up test-db-down clean

help: ## Show available commands
	@awk 'BEGIN {FS = ":.*## "; printf "FlowMate commands:\n"} /^[a-zA-Z_-]+:.*## / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Create .env if needed and install development dependencies
	@test -f .env || cp .env.example .env
	uv sync --frozen --group dev

sync: ## Install locked development dependencies
	uv sync --frozen --group dev

format: ## Format Python code with Ruff
	uv run ruff format .

lint: ## Run Ruff lint checks
	uv run ruff check .

typecheck: ## Run strict mypy checks
	uv run mypy src tests

test: ## Run the complete pytest suite
	uv run pytest

check: ## Run formatting, linting, type checking, and tests
	sh scripts/run_checks.sh

migrate: ## Apply Alembic migrations using the application image
	docker compose build api
	docker compose run --rm api alembic upgrade head

migration: ## Create an Alembic migration: make migration name="description"
	@test -n "$(name)" || (echo 'Usage: make migration name="description"' >&2; exit 2)
	docker compose build api
	docker compose run --rm --volume "$(CURDIR)/migrations:/app/migrations" api alembic revision --autogenerate -m "$(name)"

downgrade: ## Downgrade Alembic by one revision or pass revision=base
	docker compose build api
	docker compose run --rm api alembic downgrade "$(revision)"

migration-current: ## Show the current database migration revision
	docker compose build api
	docker compose run --rm api alembic current

migration-history: ## Show the complete Alembic migration history
	uv run alembic history --verbose

api: ## Run the API locally with reload enabled
	uv run uvicorn flowmate.api.app:create_app --factory --reload

bot: ## Run the Telegram bot locally
	uv run python -m flowmate.bot

up: ## Build and start PostgreSQL and API
	docker compose up -d --build postgres api

up-all: ## Build and start PostgreSQL, API, and the bot profile
	docker compose --profile bot up -d --build

down: ## Stop application containers without deleting database data
	docker compose --profile bot down

logs: ## Follow logs from all application services
	docker compose --profile bot logs -f

ps: ## Show application container status
	docker compose --profile bot ps

test-db-up: ## Start the isolated integration-test PostgreSQL database
	docker compose -f docker-compose.test.yml up -d --wait

test-db-down: ## Stop test PostgreSQL and delete its isolated data volume
	docker compose -f docker-compose.test.yml down --volumes

clean: ## Remove project containers, volumes, and local test caches
	docker compose --profile bot down --volumes --remove-orphans
	docker compose -f docker-compose.test.yml down --volumes --remove-orphans
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov
	rm -f .coverage coverage.xml
