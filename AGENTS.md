# FlowMate Agent Guide

## Architecture

FlowMate is a Python 3.12 application with two independent entrypoints: a
FastAPI HTTP API and an aiogram Telegram bot. Both use shared configuration and
async SQLAlchemy infrastructure backed by PostgreSQL. Voice messages use a
small speech provider boundary and permission-restricted temporary files; audio
is never persisted, while text and transcriptions are stored as immutable notes.
The project does not yet contain task-management domain logic.

Configuration and logging live in `flowmate.core`. API authentication lives in
`flowmate.auth`. Database models live in `flowmate.db.models`, while schema
history is managed exclusively through Alembic migrations. Speech provider,
transcription, and temporary-file code lives in `flowmate.speech`.

Runtime code uses the `src/flowmate` package. Fast unit tests live in
`tests/unit`; PostgreSQL-backed tests live in `tests/integration` and use only
the isolated database configured by `TEST_DATABASE_URL`.

## Conventions

- Use English for identifiers, comments, and docstrings.
- Keep modules small and prefer direct implementations over generic abstractions.
- Use async APIs for database and network operations.
- Keep Telegram user-facing messages in Russian when appropriate.
- Never commit secrets, real tokens, passwords, or local `.env` files.
- Every database schema change requires an Alembic migration.
- Runtime and test code must never call `metadata.create_all`.
- Tests must not call external Telegram, speech, or AI APIs.
- Never log or persist voice audio, API keys, or full note/transcription text.
- Note creation must remain idempotent by Telegram update ID.
- Do not add infrastructure or dependencies outside the current stage scope.

## Commands

```bash
make sync
make test-unit
make test-integration
make check
make test-db-up
make migrate
make api
make bot
```

Run `make check` before completing every task. It checks formatting, Ruff,
strict mypy, unit tests, and integration tests in that order. Integration tests
use the test database from `docker-compose.test.yml`; any custom
`TEST_DATABASE_URL` must name a database ending in `_test`.
