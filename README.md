# FlowMate

FlowMate is a Telegram-first personal assistant. Its foundation provides a
FastAPI API, an optional aiogram bot, PostgreSQL, Alembic migrations, tests, and
CI. Stage 1 adds voice transcription without task extraction, transcript
persistence, or other product-domain logic.

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
Create a bot through BotFather using `/newbot`, then place the issued token only
in the local `.env` file as `TELEGRAM_BOT_TOKEN`. Set
`TELEGRAM_ALLOWED_USER_IDS` to a comma-separated list of positive numeric
Telegram user IDs, for example `123456789,987654321`. Never commit either the
real token or local `.env` file.

Start PostgreSQL and the API without the bot:

```bash
make up
```

After configuring the bot variables, start PostgreSQL, API, and bot:

```bash
make up-all
```

The bot supports `/start`, `/help`, and `/status`. Other unsupported content
receives a short explanation. Only one bot replica may run for a Telegram
long-polling token.

### Voice transcription

Voice transcription is optional. To enable the OpenAI provider, configure all
of these values in `.env`:

```dotenv
SPEECH_PROVIDER=openai
OPENAI_API_KEY=replace-with-your-api-key
SPEECH_MODEL=replace-with-a-supported-transcription-model
SPEECH_LANGUAGE=ru
SPEECH_TIMEOUT_SECONDS=60
SPEECH_MAX_FILE_SIZE_BYTES=20000000
```

Choose `SPEECH_MODEL` from the current
[OpenAI transcription documentation](https://platform.openai.com/docs/guides/speech-to-text)
instead of relying on an application default. If speech configuration is
missing, commands remain available and voice messages receive a safe
configuration response.

For an accepted voice message, the bot immediately reports that processing has
started, downloads Telegram OGG/Opus audio into a unique temporary `.ogg` file,
and returns the transcription as plain text. The default limit is 20 MB and the
download plus transcription deadline is 60 seconds. Audio is not converted or
stored permanently, and both audio and transcript are never written to
PostgreSQL. The temporary file is removed before the transcription is sent,
including every failure path.

## Operations

```bash
make logs
make ps
make down
```

`make down` preserves PostgreSQL data. `make clean` removes application and test
volumes, so it permanently deletes local database data.

## Tests and Quality

Unit tests require neither Docker nor external network access:

```bash
make test-unit
```

Start the isolated PostgreSQL database before integration tests:

```bash
make test-db-up
make test-integration
make test
make check
make test-db-down
```

`make check` is the mandatory validation command. It checks formatting, Ruff,
strict mypy, unit tests, and integration tests. The default test URL is
`postgresql+asyncpg://flowmate_test:flowmate_test@localhost:5433/flowmate_test`;
custom test database names must end in `_test`.

Tests apply Alembic migrations and never call `metadata.create_all`. Database
tests use transactions for cleanup and never access development data. Telegram,
OpenAI, and other external APIs are not contacted.

## Architecture

```text
host -> api (FastAPI/Uvicorn) -> postgres
                    |
Telegram -> bot ----+  [optional Compose profile: bot]
              |
              +-> temporary OGG -> speech provider -> plain-text reply
```

Both application processes use the same non-root runtime image and shared async
SQLAlchemy infrastructure. The environment allowlist remains the source of
Telegram authorization; the minimal `users` table is reserved for future use.
Voice audio exists only in a permission-restricted temporary file while one
update is processed. Transcriptions are not persisted at this stage.
