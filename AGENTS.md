# FlowMate Agent Guide

## Architecture

FlowMate is a Python 3.12 application with three independent entrypoints: a
FastAPI HTTP API, an aiogram Telegram bot, and an APScheduler reminder worker.
All use shared configuration and async SQLAlchemy infrastructure backed by
PostgreSQL. Voice messages use a
small speech provider boundary and permission-restricted temporary files; audio
is never persisted, while text and transcriptions are stored as immutable notes.
The core Task Engine models and services are available, and confirmed drafts
are converted atomically into final WorkItems or Notes. Telegram work-item
management uses ownership-safe domain services and expiring action sessions.
Persistent reminders are claimed by a separate APScheduler worker; PostgreSQL,
not APScheduler, is their source of truth.

The authenticated PWA lives in `apps/web` and uses React, TypeScript, Vite,
TanStack Query, React Router, and Radix primitives. Its production Nginx
container proxies `/api` to FastAPI so browser sessions remain same-origin.
Stage 6.1 pages are shell placeholders and must not duplicate Task Engine
business logic.

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
- Every Telegram work-item mutation must remain idempotent and write a
  `WorkItemEvent` without embedding user note content in the event payload.
- Work-item cards must load notes, people, topics, events, and reminders through
  ownership-safe projections and keep user-generated text below Telegram limits.
- Mutating card callbacks must verify a compact WorkItem or Reminder revision
  under row lock; stale callbacks refresh the card without creating an event.
- Active work-item action sessions must intercept replies before Note or draft
  handlers and must expire according to configuration.
- Custom work-item date, reminder snooze, and linked-note replies must retain the
  originating card reference and refresh it after a successful transaction.
- Main-menu buttons and slash commands must use the same handlers and
  ownership-safe query services; list callback data must never contain user text.
- Search replies belong to an expiring action session and must never create a
  Note or AI draft. Completed search sessions remain readable only until TTL.
- AI search routing may produce only strict filters; all result selection must
  use deterministic ownership-safe PostgreSQL queries.
- Search defaults to open WorkItems. Completed or archived records require an
  explicit status filter, and search must never use external or vector indexes.
- Ambiguous management selection must preserve one intended action and mutate
  only the owned WorkItem selected from the expiring candidate session.
- Natural-language work-item matching and candidate resolution belong in the
  Task Engine intent service, not in aiogram handlers.
- Telegram management updates must be deduplicated before AI routing by their
  event or action-session origin update ID.
- Telegram never permanently deletes WorkItems.
- Reminder delivery must remain separate from due-reminder selection.
- Reminder claims must use row locking, a processing lease, and claim-token
  checks before updating delivery state.
- Reminder errors and logs may contain only safe categories, never Telegram
  exception text or custom reminder content.
- Sent, cancelled, and failed reminders remain as delivery history.
- WorkItem dates and managed reminders must change in the same transaction via
  the reminder synchronization service.
- Reminder callbacks must remain ownership-safe and idempotent by Telegram
  update ID; snooze must not change the WorkItem date.
- User notification scheduling must use the effective per-user IANA timezone.
- Daily digests must remain unique by user, type, and local date; empty digests
  are opt-in.
- Quiet-hour deferral must update the existing reminder before claim and must
  not consume a delivery attempt.
- Claimed ordinary reminders must recheck quiet hours before delivery and
  release their processing lease without consuming an attempt when deferred.
- Reminder processing leases must remain longer than delivery timeouts.
- Custom snooze voice replies may be transcribed only inside an active reminder
  action session and must never create a Note or AI draft.
- Do not add infrastructure or dependencies outside the current stage scope.
- PWA authentication must use server-side sessions and HttpOnly cookies; never
  store session credentials or authentication secrets in localStorage.
- Cookie-authenticated writes must validate both the CSRF token and exact
  configured Origin.
- The PWA service worker must never cache `/api`, authentication responses, or
  user data.
- Frontend API modules remain typed and must treat `401` as an expired session.

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
make scheduler
make up-worker
make web-dev
make web-test
make web-build
```

Run `make check` before completing every task. It checks formatting, Ruff,
strict mypy, unit tests, integration tests, frontend formatting, ESLint, strict
TypeScript, Vitest, and the production PWA build. Integration tests
use the test database from `docker-compose.test.yml`; any custom
`TEST_DATABASE_URL` must name a database ending in `_test`.
