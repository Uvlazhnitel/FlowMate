# FlowMate

FlowMate is the technical foundation for Voice PM Assistant, a Telegram-first
personal assistant. Stage 0 provides a FastAPI API, an optional aiogram bot,
PostgreSQL, Alembic migrations, tests, and CI. Task management, transcription,
AI, and the PWA are intentionally out of scope.

The repository and Python package retain the technical name `flowmate`.

## Requirements

- Docker with Docker Compose
- Python 3.12 and [uv](https://docs.astral.sh/uv/) for local development

## Setup

Create the local environment file and install locked development dependencies:

```bash
make setup
```

Replace the placeholder values in `.env` before starting services. In
particular, set a strong `APP_API_KEY` and `POSTGRES_PASSWORD`; keep the
credentials embedded in `DATABASE_URL` consistent with PostgreSQL settings.

## Start the API

Build and start PostgreSQL and the API:

```bash
make up
make ps
```

The API startup applies Alembic migrations before starting Uvicorn. PostgreSQL
is available only on the internal Compose network and is not published to the
host.

Configuration is loaded from environment variables through Pydantic Settings.
`APP_ENV` supports `development`, `test`, and `production`. Production rejects
debug mode, placeholder credentials, short API keys, and wildcard CORS origins.
Leave `CORS_ORIGINS` empty to disable CORS, or provide comma-separated origins.

Verify the API:

```bash
set -a; . ./.env; set +a
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
curl -H "Authorization: Bearer $APP_API_KEY" \
  http://localhost:8000/api/v1/me
curl -H "Authorization: Bearer $APP_API_KEY" \
  http://localhost:8000/api/v1/status
```

Use the value configured by `APP_PORT` instead of `8000` when it is changed.
OpenAPI documentation is available at `/docs` only when `APP_DEBUG=true`.
It is always disabled in production because production configuration rejects
debug mode.

`/health/live` only checks the API process. `/health/ready` additionally checks
PostgreSQL and returns HTTP `503` without database details when unavailable.

## Database and Migrations

All schema changes are managed through Alembic. Runtime code never calls
`metadata.create_all`.

```bash
make migrate
make migration name="add example field"
make migration-current
make migration-history
make downgrade
make downgrade revision=base
```

`make migration` rebuilds the application image and bind-mounts `migrations/`
so the generated revision is written to the repository. Downgrading `0003` to
`0002` requires every user to have a Telegram ID because the old schema used a
`NOT NULL` constraint; Alembic does not delete incompatible rows automatically.

## Start the Bot

The bot is disabled by default, so `make up` does not require a Telegram token.
After configuring `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_USER_IDS`, start all
services with:

```bash
make up-all
```

Only one bot replica may run for a Telegram long-polling token.

## Operations

```bash
make logs
make ps
make down
```

`make down` preserves PostgreSQL data. `make clean` removes application and test
volumes, so it permanently deletes local database data.

## Tests and Quality

Start the isolated test database and run all checks:

```bash
make test-db-up
TEST_DATABASE_URL=postgresql+asyncpg://flowmate_test:flowmate_test@localhost:5433/flowmate_test make check
make test-db-down
```

Tests apply Alembic migrations and never call `metadata.create_all`. They do not
contact Telegram or AI APIs.

## Architecture

```text
host -> api (FastAPI/Uvicorn) -> postgres
                    |
Telegram -> bot ----+  [optional Compose profile: bot]
```

Both application processes use the same non-root runtime image and shared async
SQLAlchemy infrastructure. The environment allowlist remains the source of
Telegram authorization; the minimal `users` table is reserved for future use.
