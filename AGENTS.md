# FlowMate Agent Guide

## Architecture

FlowMate is a Python 3.12 application with two independent entrypoints: a
FastAPI HTTP API and an aiogram Telegram bot. Both use shared configuration and
async SQLAlchemy infrastructure backed by PostgreSQL. Stage 0 does not contain
task-management domain logic or placeholder domain packages.

Configuration and logging live in `flowmate.core`. API authentication lives in
`flowmate.auth`. Database models live in `flowmate.db.models`, while schema
history is managed exclusively through Alembic migrations.

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
- Tests must not call external Telegram or AI APIs.
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
