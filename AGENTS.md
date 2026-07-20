# FlowMate Agent Guide

## Architecture

FlowMate is a Python 3.12 application with two independent entrypoints: a
FastAPI HTTP API and an aiogram Telegram bot. Both use shared application
services and async SQLAlchemy infrastructure backed by PostgreSQL. The
`flowmate.application` package is reserved for future use cases; Stage 0 does
not contain task-management domain logic.

Configuration and logging live in `flowmate.core`. API authentication lives in
`flowmate.auth`. Database models live in `flowmate.db.models`, while schema
history is managed exclusively through Alembic migrations.

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
make check
make test-db-up
make migrate
make api
make bot
```

Run `make check` before completing every task. Integration tests use the test
database from `docker-compose.test.yml`; set `FLOWMATE_TEST_DATABASE_URL` when
using a different PostgreSQL instance.
