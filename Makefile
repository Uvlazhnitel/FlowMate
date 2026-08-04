.DEFAULT_GOAL := help
revision ?= -1
TEST_DATABASE_URL ?= postgresql+asyncpg://flowmate_test:flowmate_test@localhost:5433/flowmate_test
export TEST_DATABASE_URL

.PHONY: help setup sync env-check check-build-context format format-check lint typecheck test test-unit test-integration check web-setup web-dev web-format web-format-check web-lint web-typecheck web-test web-build migrate migration downgrade migration-current migration-history api bot scheduler maintenance-once ai-eval backup backup-offsite restore-check restore-offsite-check backup-restore-test reminder-retry up up-all up-worker down logs ps test-db-up test-db-down clean

help: ## Show available commands
	@awk 'BEGIN {FS = ":.*## "; printf "FlowMate commands:\n"} /^[a-zA-Z_-]+:.*## / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Create .env if needed and install development dependencies
	python3 scripts/env_file.py ensure --template .env.example --target .env
	uv sync --frozen --group dev
	npm ci --prefix apps/web

sync: ## Install locked development dependencies
	uv sync --frozen --group dev
	npm ci --prefix apps/web

env-check: ## Fail if an existing .env is not an owned regular 0600 file
	python3 scripts/env_file.py check --target .env

check-build-context: ## Verify private files are excluded from Docker contexts
	bash scripts/check_docker_context.sh

migrate migration downgrade migration-current api bot scheduler maintenance-once backup reminder-retry up up-all up-worker: env-check

format: ## Format Python code with Ruff
	uv run ruff format .

format-check: ## Check Python formatting without changing files
	uv run ruff format --check .

lint: ## Run Ruff lint checks
	uv run ruff check .

typecheck: ## Run strict mypy checks
	uv run mypy src tests

test: ## Run unit and PostgreSQL integration tests
	uv run pytest tests/unit
	uv run pytest tests/integration

test-unit: ## Run network-independent unit tests
	uv run pytest tests/unit

test-integration: ## Run tests against the dedicated PostgreSQL test database
	uv run pytest tests/integration

check: ## Run the complete mandatory validation suite
	sh scripts/run_checks.sh

web-setup: ## Install locked frontend dependencies
	npm ci --prefix apps/web

web-dev: ## Run the Vite development server
	npm run dev --prefix apps/web

web-format: ## Format frontend source files
	npm run format --prefix apps/web

web-format-check: ## Check frontend formatting
	npm run format:check --prefix apps/web

web-lint: ## Run frontend ESLint checks
	npm run lint --prefix apps/web

web-typecheck: ## Run strict frontend TypeScript checks
	npm run typecheck --prefix apps/web

web-test: ## Run frontend unit tests
	npm test --prefix apps/web

web-build: ## Build the production PWA bundle
	npm run build --prefix apps/web

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

scheduler: ## Run the reminder scheduler locally
	uv run python -m flowmate.scheduler

maintenance-once: ## Run database cleanup once
	uv run python -m flowmate.maintenance cleanup

ai-eval: ## Run offline anonymized AI regression evaluation
	uv run python -m flowmate.ai.eval

backup: ## Create and rotate a compressed PostgreSQL backup
	bash scripts/backup_postgres.sh

backup-offsite: ## Encrypt and upload backup=... with age and rclone
	@test -n "$(backup)" || (echo 'Usage: make backup-offsite backup=backups/file.dump' >&2; exit 2)
	bash scripts/upload_backup_offsite.sh "$(backup)"

restore-check: ## Restore backup into an isolated *_restore_test database
	@test -n "$(backup)" || (echo 'Usage: make restore-check backup=backups/file.dump' >&2; exit 2)
	bash scripts/restore_postgres.sh "$(backup)"

restore-offsite-check: ## Download and restore backup=... into an isolated database
	@test -n "$(backup)" || (echo 'Usage: make restore-offsite-check backup=REMOTE/file.tar.age' >&2; exit 2)
	bash scripts/restore_backup_offsite.sh "$(backup)"

backup-restore-test: ## Run destructive isolated backup/restore and off-site tests
	RUN_BACKUP_RESTORE_TESTS=1 uv run pytest tests/ops

reminder-retry: ## Manually retry a delivery_unknown reminder
	@test -n "$(id)" || (echo 'Usage: make reminder-retry id=UUID' >&2; exit 2)
	uv run python -m flowmate.maintenance retry-reminder "$(id)"

up: ## Build and start PostgreSQL, API, and PWA
	docker compose up -d --build postgres api web

up-all: ## Build and start PostgreSQL, API, bot, and scheduler
	docker compose --profile bot --profile scheduler up -d --build

up-worker: ## Build and start the optional reminder scheduler
	docker compose --profile scheduler up -d --build scheduler

down: ## Stop application containers without deleting database data
	docker compose --profile bot --profile scheduler down

logs: ## Follow logs from all application services
	docker compose --profile bot --profile scheduler logs -f

ps: ## Show application container status
	docker compose --profile bot --profile scheduler ps

test-db-up: ## Start the isolated integration-test PostgreSQL database
	docker compose -f docker-compose.test.yml up -d --wait

test-db-down: ## Stop test PostgreSQL and delete its isolated data volume
	docker compose -f docker-compose.test.yml down --volumes

clean: ## Remove project containers, volumes, and local test caches
	docker compose --profile bot --profile scheduler down --volumes --remove-orphans
	docker compose -f docker-compose.test.yml down --volumes --remove-orphans
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov
	rm -rf apps/web/coverage apps/web/dist
	rm -f .coverage coverage.xml
