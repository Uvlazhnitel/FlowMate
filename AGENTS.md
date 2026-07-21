# FlowMate Agent Guide

## Architecture

FlowMate is a Python 3.12 application with two independent entrypoints: a
FastAPI HTTP API and an aiogram Telegram bot. Both use shared configuration and
async SQLAlchemy infrastructure backed by PostgreSQL. Voice messages use a
small speech provider boundary and permission-restricted temporary files; audio
is never persisted, while text and transcriptions are stored as immutable notes.
The core Task Engine models and services are available, and confirmed drafts
are converted atomically into final WorkItems or Notes.

Configuration and logging live in `flowmate.core`. API authentication lives in
`flowmate.auth`. Database models live in `flowmate.db.models`, while schema
history is managed exclusively through Alembic migrations. Speech provider,
transcription, and temporary-file code lives in `flowmate.speech`. Structured
draft schemas, provider boundaries, and parsing services live in `flowmate.ai`;
draft state persistence lives in `flowmate.db.drafts`, and clarification policy
lives in `flowmate.drafts`. Task Engine enums and ownership-safe services live
in `flowmate.task_engine`.

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
- Never log full AI input or provider responses.
- AI providers must not receive audio, execute tools, or write database records.
- AI readiness is assigned by backend thresholds, never by the provider.
- Temporal candidates must retain their original phrase and use aware datetimes.
- Telegram draft cancellation must not delete the source Note.
- Draft conversion must remain atomic and idempotent by source draft item ID.
- A confirmed draft must preserve its source Note and must not create partial
  final records.
- Clarification answers must update the existing draft and must not create Notes.
- Expired drafts and drafts owned by another user must reject all mutations.
- Telegram Note creation must remain idempotent by Telegram update ID; manual
  Notes must have a null Telegram update ID.
- Task Engine services must require `user_id`, filter reads by ownership, and
  validate ownership before creating cross-entity links.
- Task Engine services flush changes but leave commit and rollback to callers.
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
