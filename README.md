# FlowMate

FlowMate is a Telegram-first personal assistant. Its foundation provides a
FastAPI API, an optional aiogram bot, PostgreSQL, Alembic migrations, tests, and
CI. Stage 1 adds text and voice notes. Stage 2 begins with optional structured
AI drafts, without creating tasks or other product-domain records.

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
`APP_TIMEZONE` is a validated IANA timezone such as `UTC` or `Europe/Riga` and
is used as the reference timezone for relative dates in AI drafts.

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
Migration `0004` creates immutable notes. Migration `0005` adds persistent AI
draft sessions and draft items; it can be downgraded to `0004` without removing
source notes.

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

The bot supports `/start`, `/help`, `/status`, `/notes`, `/draft`, and
`/cancel`. Any non-empty text
that does not begin with `/` is stored as a note. `/notes` returns previews of
the 10 most recent notes belonging to the current Telegram user. Telegram
update IDs make repeated delivery idempotent, so one update creates at most one
note. Only one bot replica may run for a Telegram long-polling token.

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
transcribes it, stores the transcript as a voice note, and returns it as plain
text. The default limit is 20 MB and the download plus transcription deadline
is 60 seconds. Audio is not converted or stored permanently. The temporary file
is removed before the transcript is written to PostgreSQL or returned to the
user, including every failure path.

### Structured AI drafts

Structured parsing is optional and does not affect note creation when disabled.
To enable the OpenAI provider, configure these values in `.env`:

```dotenv
APP_TIMEZONE=Europe/Riga
APP_ACTIVE_WORKSPACE=personal
AI_PROVIDER=openai
OPENAI_API_KEY=replace-with-your-api-key
AI_MODEL=replace-with-a-structured-outputs-model
AI_TIMEOUT_SECONDS=60
AI_HIGH_CONFIDENCE_THRESHOLD=0.80
AI_CLARIFICATION_CONFIDENCE_THRESHOLD=0.50
DRAFT_TTL_HOURS=24
```

Choose `AI_MODEL` from the current
[OpenAI Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs)
instead of relying on an application default. The same `OPENAI_API_KEY` setting
is shared with optional speech transcription.

For a new text or voice note, the bot creates the Note and a parsing draft in
one database transaction, then sends only the note text to the configured AI
provider. Context includes the current local time, IANA
timezone, active workspace, Telegram channel, and text or voice source. One
message may produce multiple validated items with people, roles, topics, dates,
supporting notes, and dependencies.

Every temporal candidate retains its original phrase. Resolved values are
timezone-aware ISO datetimes; date-only due dates use `23:59:59` in the user's
timezone, while reminders without a time require clarification. Impossible or
materially ambiguous dates are never silently normalized.

Items at or above the high-confidence threshold are marked ready only when no
fields or dates need clarification. Items between the two thresholds require
clarification; lower-confidence or invalid-date items remain unresolved. The
summary is plain text and includes every detected item. Ready drafts show
Confirm, Change, and Cancel buttons. Drafts that need clarification ask one
question at a time; text and voice answers must be sent as a Telegram Reply to
that question. Clear options are shown as buttons.

Only one open draft is kept per user. `/draft` shows it and `/cancel` cancels
it. The phrases `сохрани как есть` and `отмена` confirm or cancel the active
draft; corrections such as `не Антон, а Мария`, `это заметка`, or a corrected
date refine the same session. Accepted answers refresh the default 24-hour TTL.
Expired drafts reject further answers. New ordinary messages are not saved as
unrelated notes while a draft dialog is active.

Draft sessions and their validated items are persisted, but confirmation only
changes the session status to `confirmed`. No Task, Person, Topic, or follow-up
record is created yet. Cancelling a draft never deletes its source Note. Audio
is never sent to the AI flow or persisted. If parsing or refinement fails, the
saved Note remains available and the bot returns a short safe error. Repeated
Telegram updates do not invoke AI again.

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
              +-> temporary OGG -> speech provider -> note
                                                 |
                         persistent AI draft <-+-> clarification dialog
```

Both application processes use the same non-root runtime image and shared async
SQLAlchemy infrastructure. The environment allowlist remains the source of
Telegram authorization; the `users` table owns each immutable note. Voice audio
exists only in a permission-restricted temporary file while one update is
processed. Text and transcribed voice content are stored as notes owned by that
user; original audio is never persisted. The optional AI boundary receives Note
text only and returns validated Pydantic data. Application persistence stores
the validated draft and its state; the provider has no database access or
tools.
