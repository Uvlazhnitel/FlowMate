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
make sync
docker compose up -d db
make migrate
make api
```

Run the Telegram bot in a second terminal:

```bash
make bot
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
make check
```

Integration tests require PostgreSQL. By default they use
`postgresql+asyncpg://flowmate:flowmate@localhost:5433/flowmate_test`. Start the
isolated, ephemeral test database or override `FLOWMATE_TEST_DATABASE_URL`:

```bash
make test-db-up
make test
```

## Migrations

Create and apply migrations with:

```bash
uv run alembic revision --autogenerate -m "description"
uv run alembic upgrade head
```

The initial migration is deliberately empty. Stage 0 adds only a minimal
`users` table in the next migration; Telegram access still comes from the
environment allowlist rather than this table.

## Configuration

Application variables use the `FLOWMATE_` prefix. See `.env.example` for the
complete list. The API and bot validate their own required secrets at startup,
so either process can be operated independently.

`POSTGRES_PORT` and `API_PORT` control host port bindings when their defaults
conflict with other local services.

Production deployments should use strong unique secrets, disable API docs,
terminate TLS in a reverse proxy, and back up the PostgreSQL volume. Compose
does not provide secret management or backups.

## Architecture

```text
Telegram -> aiogram bot -> shared application services -> PostgreSQL
Future PWA -> FastAPI   -> shared application services -> PostgreSQL
```

The repository remains named FlowMate and uses `flowmate` as its technical
Python package name. `flowmate.application` is currently an empty boundary for
future shared use cases. Stage 0 deliberately avoids repositories, generic
service abstractions, queues, caches, and task-management domain models.
