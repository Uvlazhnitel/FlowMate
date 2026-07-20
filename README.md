# FlowMate

Technical foundation for Voice PM Assistant, a Telegram-first personal assistant.
Stage 0 provides the API, Telegram bot, PostgreSQL integration, migrations, tests,
and CI. Task management, transcription, AI, and the PWA are intentionally out of
scope.

## Requirements

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Docker with Docker Compose

## Local setup

```bash
cp .env.example .env
uv sync --group dev
docker compose up -d db
uv run alembic upgrade head
uv run uvicorn flowmate.api.app:create_app --factory --reload
```

Run the Telegram bot in a second terminal:

```bash
uv run python -m flowmate.bot
```

The API exposes:

- `GET /health/live` for process liveness;
- `GET /health/ready` for database readiness;
- `GET /api/v1/status` protected by `Authorization: Bearer <token>`.

OpenAPI documentation is available at `/docs` when
`FLOWMATE_API_DOCS_ENABLED=true`.

## Docker Compose

Set real secrets in `.env`, then start the complete stack:

```bash
docker compose up --build
```

Compose starts PostgreSQL, applies migrations once, and then starts the API and
bot. Only one bot replica may run because Telegram long polling does not support
multiple consumers for the same token.

## Quality checks

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
```

Integration tests require PostgreSQL. By default they use
`postgresql+asyncpg://flowmate:flowmate@localhost:5433/flowmate_test`. Start the
isolated, ephemeral test database or override `FLOWMATE_TEST_DATABASE_URL`:

```bash
docker compose --profile test up -d db-test
```

## Migrations

Create and apply migrations with:

```bash
uv run alembic revision --autogenerate -m "description"
uv run alembic upgrade head
```

The initial migration is deliberately empty because Stage 0 has no domain
tables.

## Configuration

Application variables use the `FLOWMATE_` prefix. See `.env.example` for the
complete list. The API and bot validate their own required secrets at startup,
so either process can be operated independently.

Production deployments should use strong unique secrets, disable API docs,
terminate TLS in a reverse proxy, and back up the PostgreSQL volume. Compose
does not provide secret management or backups.

## Architecture

```text
Telegram -> aiogram bot -> shared application services -> PostgreSQL
Future PWA -> FastAPI   -> shared application services -> PostgreSQL
```

`flowmate.application` is currently an empty boundary for future shared use
cases. Stage 0 deliberately avoids repositories, generic service abstractions,
queues, caches, and domain models.
