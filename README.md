# FlowMate

FlowMate is a Telegram-first personal assistant. Its foundation provides a
FastAPI API, an optional aiogram bot, PostgreSQL, Alembic migrations, tests, and
CI. The Stage 6 foundation adds an installable authenticated PWA shell. Stage 1 adds text and voice notes, Stage 2 adds structured AI drafts and
clarification dialogs, and Stage 3 converts confirmed drafts into Task Engine
records.

The repository and Python package retain the technical name `flowmate`.

## Requirements

- Docker with Docker Compose
- Python 3.12 and [uv](https://docs.astral.sh/uv/) for local development
- Node.js 22 and npm for frontend development

## Setup

Create the local environment file and install locked development dependencies:

```bash
make setup
```

Replace the placeholder values in `.env` before starting services. In
particular, set a strong `APP_API_KEY` and `POSTGRES_PASSWORD`; keep the
credentials embedded in `DATABASE_URL` consistent with PostgreSQL settings.

## Start the application

Build and start PostgreSQL, the API, and the PWA:

```bash
make up
make ps
```

The API startup applies Alembic migrations before starting Uvicorn. PostgreSQL
is available only on the internal Compose network and is not published to the
host. The PWA is available at `http://localhost:8080` by default and proxies
`/api` to FastAPI on the same origin. The Compose technical API remains
available on `API_HOST_PORT` for local diagnostics; its container port stays at
`8000` for the Nginx upstream.

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

Use the value configured by `API_HOST_PORT` instead of `8000` for Compose.
Standalone API runs continue to use `APP_PORT`.
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
source notes. Migration `0006` adds Task Engine records and allows immutable
manual notes without Telegram update IDs. Downgrading `0006` requires removing
manual notes first because the Stage 2 schema requires every Note to have a
Telegram update ID. Migration `0007` adds unique draft-item provenance to final
Notes so draft conversion remains idempotent.
Migration `0008` adds idempotent work-item events and expiring Telegram action
sessions used for record selection and free-form action input. Both mutations
and action sessions retain their originating Telegram update ID, so a repeated
delivery cannot create a second event, Note, draft, or input dialog.
Migration `0009` adds persistent reminders, delivery attempts, retry timing,
and processing leases. PostgreSQL is the reminder source of truth; APScheduler
only triggers periodic due-reminder scans.
Migration `0010` connects WorkItem dates to exact, pre-deadline, and snoozed
reminders and extends WorkItem history for reminder actions.
Migration `0011` adds per-user notification preferences and digest scheduling.
Migration `0012` adds the expiring search action used by Telegram pagination.
Migration `0013` adds hashed, expiring PWA login codes and revocable server-side
sessions. Raw login codes, session tokens, and CSRF secrets are never stored in
PostgreSQL.
Migration `0014` adds per-user PWA action idempotency keys to WorkItem events.
Migration `0015_pwa_remaining_screens` adds manual Planner state and transfer
timestamps, Note inbox disposition, display preferences, and ownership-safe
exact topic/person selections for draft items. It also adds the
`planner_status_changed` history event used by Timeline.
Migration `0016_meeting_mode_foundation` adds ownership-safe meetings,
participants, topics, captured-note links, state history, and persistent
Telegram setup sessions. A partial unique index permits only one active meeting
per user.
Migration `0017_meeting_fast_capture` links independent DraftSessions to active
meetings, preserves immutable source Notes and context snapshots, and adds
stable per-meeting capture numbering and non-destructive review state.
Migration `0018_meeting_review_completion` adds validated meeting reviews,
agenda outcomes, idempotent result links, and meeting-to-WorkItem provenance.
Migration `0019_stage7_stabilization` indexes ownership-scoped Meeting events
for stable unified Timeline pagination.
Migration `0020_stage8_stabilization` adds persistent Telegram receipts,
PostgreSQL-backed AI recovery jobs, safe audit events, prompt versions,
transcript redaction metadata, and an explicit unknown-delivery reminder state.
Migration `0021_workspace_separation` persists `personal` and `work` on users,
topics, work items, notes, drafts, meetings, reminders, and dialog sessions.
Existing data is assigned to `personal`; topic names are unique within each
workspace rather than across the whole user account.

## PWA and Login

The PWA lives in `apps/web` and uses React, TypeScript, Vite, TanStack Query,
React Router, Radix primitives, and a generated service worker. It provides the
responsive application shell and protected routes for Dashboard, Today,
Topics, People, Agenda, Inbox, Planner Queue, Timeline, Settings, and Meetings.
Dashboard, Today, Topics, People, and Agenda provide ownership-safe operational
views and reuse Task Engine services for work-item actions. Inbox supports
explicit review and atomic conversion of uncertain drafts, Planner Queue tracks
manual transfer state without Microsoft integration, Timeline combines
redacted WorkItem and Meeting history, and Settings manages notification preferences, active topics,
people, aliases, and provider readiness booleans without returning secrets.
Meetings provides an active Meeting Mode card, manual start/end/cancel actions,
participant and topic selection, paginated recent meetings, and chronological
fast captures. Text and voice captures are acknowledged immediately and kept as
editable drafts; unresolved AI fields do not interrupt an active meeting. After
`/meeting_end`, structured review proposes decisions and next actions without
writing final records. The user can clarify, exclude, send incomplete items to
Inbox, opt individual actions into the manual Planner Queue, and confirm ready
items through the existing Task Engine and reminder services.
Text or voice clarification answers update only the referenced structured
capture item and its review projection. Failed parsing leaves that item pending;
successful confirmation remains atomic and idempotent.

### Work and Personal

FlowMate has two database-backed spaces: `Личное` (`personal`) and `Работа`
(`work`). Select the current space in the PWA shell or with Telegram command
`/workspace`. Dashboard, Today, Topics, Agenda, Inbox, Planner Queue, Timeline,
Meetings, notes, search, and new captures use only that space. People and
notification preferences are shared, while each person's operational counts
and history are calculated for the current space.

Draft conversion keeps the draft's space, and meeting results keep the
meeting's space. Reminders retain their record's space and continue to fire
after switching; daily digests are generated separately and include the space
name. Switching is rejected while a meeting, draft, clarification, setup, or
free-form action is active so a reply cannot cross contexts. Existing final
records cannot yet be moved between spaces. `APP_ACTIVE_WORKSPACE` accepts only
`personal` or `work` and is used as the default for a newly created user; the
database value becomes authoritative afterwards.

Typical Telegram flow:

```text
/meeting
<send text or voice captures>
/meeting_notes
/meeting_end
/meeting_review
```

The PWA meeting detail at `/meetings/{id}` shows metadata, chronological
captures, summary, agenda outcomes, decisions, resulting records, unresolved
items, next actions, and safe meeting history.

Configure the single PWA owner with a Telegram user ID that is also present in
`TELEGRAM_ALLOWED_USER_IDS`:

```dotenv
PWA_TELEGRAM_USER_ID=123456789
PWA_AUTH_SECRET=replace-with-at-least-32-random-characters
PWA_PUBLIC_ORIGIN=http://localhost:8080
PWA_COOKIE_SECURE=false
WEB_PORT=8080
```

The login screen requests a six-digit, ten-minute code through the existing
Telegram bot. A code is single-use, stored only as an HMAC digest, and limited
to five verification attempts and three requests per 15 minutes. Successful
verification creates a 30-day server-side session. At most five active device
sessions are retained.

The session cookie is `HttpOnly` and `SameSite=Lax`; production additionally
requires `Secure=true` and an HTTPS `PWA_PUBLIC_ORIGIN`. A separate readable
CSRF cookie is checked against `X-CSRF-Token` and the exact request Origin on
cookie-authenticated writes. Authentication values are never stored in
`localStorage`. The service worker caches only the application shell and static
assets, never `/api` responses or user data.

Run the frontend development server against a local API:

```bash
make api
make web-dev
```

The Vite app is then available at `http://localhost:5173`. Frontend checks can
also be run independently with `make web-format-check`, `make web-lint`,
`make web-typecheck`, `make web-test`, and `make web-build`.

For a production Compose deployment, configure `APP_ENV=production`, a strong
`APP_API_KEY`, database credentials, `PWA_AUTH_SECRET`, an HTTPS
`PWA_PUBLIC_ORIGIN`, and `PWA_COOKIE_SECURE=true`, then run:

```bash
docker compose up -d --build postgres api web
docker compose ps
docker compose logs -f api web
```

For a private Tailscale deployment, expose only the local reverse-proxy targets
and let Tailscale Serve terminate HTTPS:

```dotenv
FLOWMATE_BIND_ADDRESS=127.0.0.1
API_HOST_PORT=8001
WEB_BIND_HOST=127.0.0.1
PWA_PUBLIC_ORIGIN=https://homeserver.example-tailnet.ts.net:8443
PWA_COOKIE_SECURE=true
CORS_ORIGINS=
APP_DEBUG=false
```

```bash
tailscale serve --https=8443 http://127.0.0.1:8080
```

Every client must be signed into the same tailnet with Tailscale DNS enabled.
The hostname must resolve to the server's Tailscale IP; remove any custom split
DNS rule for `ts.net` that overrides MagicDNS. Open the HTTPS URL directly on
each device, request the login code through Telegram, and install that origin as
a new PWA. A previously installed localhost or SSH-tunnel PWA has a different
origin and cannot be reused.

Add `--profile bot --profile scheduler` only when the Telegram bot and reminder
worker are configured. API startup applies Alembic migrations through
`0020_stage8_stabilization` before serving requests; Nginx serves the built PWA
and proxies `/api` to FastAPI on the same origin.

Authentication endpoints are:

```text
POST   /api/v1/auth/login-code
POST   /api/v1/auth/session
GET    /api/v1/auth/me
DELETE /api/v1/auth/session
```

API startup does not require PWA or Telegram configuration. If it is absent,
login-code requests fail safely while health and technical Bearer endpoints
remain available.

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

Start only the optional reminder worker and its API/PostgreSQL dependencies:

```bash
make up-worker
```

The bot supports `/start`, `/menu`, `/help`, `/status`, `/notes`, `/search`,
`/draft`, `/cancel`, `/today`, `/tasks`, `/followups`, `/waiting`, `/questions`,
`/topics`, and `/people`. `/start` and `/menu` show the persistent main menu;
the menu buttons call the same handlers as their slash-command equivalents.
Without AI routing, non-command text is stored as a Note. With AI
routing enabled, text is classified as either a new Note/draft or management of
one existing WorkItem; management input does not create an unrelated Note.
`/notes` returns previews of the 10 most recent notes belonging to the current
Telegram user. Telegram update IDs make repeated delivery idempotent. Only one
bot replica may run for a Telegram long-polling token.

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

For Telegram text, the bot uses one structured AI request to choose between a
new draft and management of an existing WorkItem. A `new_draft` result creates
the Note and parsing draft; a management result does not create an unrelated
Note. Voice input creates its Note/draft after transcription and remains a
draft-only flow. The provider receives text only. Context includes the current
local time, IANA timezone, active workspace, Telegram channel, and source. One
new-draft message may produce multiple validated items with people, roles,
topics, dates, supporting notes, and dependencies.

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

Draft sessions and their validated items are persisted. Confirmation converts
all actionable items into WorkItems and `note` items into immutable manual
Notes in one transaction, then marks the draft `confirmed`. Cancelling a draft
never deletes its source Note. Audio is never sent to the AI flow or persisted.
If parsing, refinement, or conversion fails, the saved Note and draft remain
available and the bot returns a short safe error. Repeated Telegram updates and
repeated confirmation do not create duplicate records.

### Task Engine data model

The Python service layer in `flowmate.task_engine` supports user-owned work
items, topics, people, directed work-item relations, people associations,
linked notes, and basic work-item events. Work items use validated types,
statuses, priorities, and timezone-aware scheduling candidates. Topic names are
unique per user without regard to case; people may share a display name.

Notes remain separate immutable records. Existing Telegram notes can be linked
to any number of work items, people, or topics. A domain service can also create
a `manual` Note and its link in the caller's transaction. Source Note and source
draft item fields preserve provenance. People and topics are matched
case-insensitively by name or alias; clear new candidates are created, while
vague or ambiguous candidates are skipped. Draft dependencies become directed
WorkItem relations when both referenced items are actionable.

### Telegram work-item management

`/today` shows overdue and due-today open records. `/tasks`, `/followups`,
`/waiting`, and `/questions` show compact lists of the corresponding active
records; `/topics` and `/people` include open-record counts. Lists use pages of
five records with `Назад`, `Вперёд`, and `Главное меню` inline actions. Each
WorkItem summary includes its date, people, topic, status, and a details button.

The main reply keyboard contains `🎙 Записать`, `📅 Сегодня`, `✅ Задачи`,
`🔁 Follow-up`, `⏳ Ждём`, `❓ Вопросы`, `👥 Люди`, `🗂 Темы`, `🔍 Поиск`, and
`⚙️ Настройки`. `🎙 Записать` explains how to send text or voice; it cannot open
Telegram's microphone directly. `/search query` searches the current user's
open WorkItems by title, description, person, topic, type, or status. `/search`
without a query and `🔍 Поиск` open a short-lived Reply prompt. Search replies
are not stored as Notes or AI drafts. Completed records are included only with
an explicit status filter or `status:all`.

Search supports partial case-insensitive names and aliases plus deterministic
operators. Quote values containing spaces:

```text
/search release person:"Антон Иванов"
/search topic:Testing type:follow-up overdue
/search status:done,archived Budget
/search from:2026-07-01 to:2026-07-31 status:all
```

With AI enabled, ordinary questions such as `Что у меня осталось по Антону?`,
`Что просрочено по теме Budget?`, or `Кого я давно не пинал?` are converted to
strict search filters. PostgreSQL, not the model, selects every result. One
clear conversational result opens directly; multiple results use the same
five-item pagination. Search uses `ILIKE`/JSONB alias matching and does not
provide fuzzy, semantic, vector, or standalone Note search.

Open a record from a list to see its description, dates, people, topic, up to
three relevant notes, recent history, and context-sensitive actions. Active
tasks can be completed, snoozed, rescheduled, annotated, or cancelled;
follow-ups also support **Ответ получен**; waiting records support **Получено**
and creation of one linked follow-up. Completed records can be reopened.

Actions update the existing card. Each button carries a compact revision, so a
repeated or stale click cannot repeat a state transition and instead refreshes
the current card. Snooze changes only the nearest active Reminder; reschedule
changes `due_at` or `next_follow_up_at` and synchronizes reminders. Reschedule
presets include later today, tomorrow morning, and the next working day. Custom
dates and linked notes use an expiring Reply prompt; their text or voice input
does not create an unrelated Note or AI draft. Every significant mutation is
recorded as a `WorkItemEvent`. `/cancel` cancels an active work-item input
session before it cancels an AI draft.

When AI is configured, ordinary Telegram text is routed once as a new
Note/draft, a search, or a management intent. A high-confidence intent with one
owned match can be applied directly. Multiple matches require an explicit
button selection showing type, title, people, topic, and date; the intended
action is applied only after selection. The selection can be cancelled and
expires with the standard action-session TTL. Ambiguous dates or missing values
start one expiring input session.
Reply references such as "эта задача" work only when replying to a WorkItem
message. Voice messages normally remain Note input; the only management
exception is a voice date inside an active custom-snooze prompt.

This stage does not expose new HTTP endpoints, permanently delete records, or
run a calendar integration. Dated WorkItems synchronize PostgreSQL reminders in
the same transaction as creation or rescheduling.

### Reminder scheduler

The optional `scheduler` Compose profile runs `python -m flowmate.scheduler` in
the shared non-root application image. It requires `TELEGRAM_BOT_TOKEN`, but
the default PostgreSQL/API stack still starts without Telegram configuration.

```dotenv
SCHEDULER_INTERVAL_SECONDS=30
REMINDER_BATCH_SIZE=50
REMINDER_MAX_ATTEMPTS=3
REMINDER_RETRY_DELAY_SECONDS=60
REMINDER_PROCESSING_TIMEOUT_SECONDS=300
REMINDER_DELIVERY_TIMEOUT_SECONDS=15
DEADLINE_REMINDER_LEAD_MINUTES=0
DEFAULT_MORNING_DIGEST_TIME=09:00
DEFAULT_EVENING_DIGEST_TIME=18:00
DEFAULT_QUIET_HOURS_START=22:00
DEFAULT_QUIET_HOURS_END=08:00
DEFAULT_SNOOZE_MINUTES=60
```

Due reminders are claimed with PostgreSQL row locks and a processing token.
Temporary Telegram failures are retried up to the configured limit; permanent
failures remain in history with a safe error category. Delivery is at-most-once
after the network attempt starts. A process or network failure after that point
stores `delivery_unknown` and never retries automatically, avoiding duplicate
reminders. An operator can deliberately retry with
`make reminder-retry id=UUID`; this action is audited.
`REMINDER_PROCESSING_TIMEOUT_SECONDS` must be greater than
`REMINDER_DELIVERY_TIMEOUT_SECONDS`, preventing an active delivery from losing
its processing lease.
Enabled morning and evening digests are created at most once per user and local
day. They summarize current WorkItems immediately before delivery, remain
idempotent across worker restarts, and handle local daylight-saving changes.
Empty digests are suppressed unless the user explicitly enables them.

An open follow-up uses `next_follow_up_at`, a waiting record uses `due_at`, and
other dated records use `due_at` for an exact reminder. Set
`DEADLINE_REMINDER_LEAD_MINUTES` to a positive number to add one earlier
deadline notification; `0` disables it. Completion, cancellation, archiving,
and receiving a waiting result cancel active reminders. Before delivery, the
worker verifies that the WorkItem is still open and its date has not changed.

Deadline notifications provide **Выполнено**, **Отложить**, and **Перенести**.
Follow-ups also provide **Ответ получен**; waiting notifications provide
**Получено** and **Follow-up**. Snooze updates the same reminder without changing
the WorkItem date. Presets are 15 minutes, one hour, three hours, and tomorrow
morning; a custom text or voice answer can provide another date. Reschedule
changes the WorkItem date and event history.

Ordinary notifications due during quiet hours move to the local end of the
quiet period with a small deterministic spread. The worker checks quiet hours
again after claiming and immediately before delivery; releasing such a claim
does not consume a delivery attempt. Digests keep their configured schedule.
Configure preferences in Telegram:

```text
/reminders
/reminders timezone Europe/Riga
/reminders morning 09:00
/reminders evening 18:00
/reminders snooze 60
/reminders empty off
/quiet 22:00 08:00
/quiet off
```

Use `/snooze` as a reply to a reminder message. Digest actions can open today's
items, atomically move unfinished actionable records and follow-ups to tomorrow,
or review up to ten records one by one. Waiting dates are never moved by the
bulk action. Use `make up-all` to run the bot and scheduler; `make up-worker`
starts the worker independently.

## Operations

```bash
make logs
make ps
make down
make maintenance-once
make ai-eval
```

`make down` preserves PostgreSQL data. `make clean` removes application and test
volumes, so it permanently deletes local database data.

### Stabilization, cleanup and recovery

Telegram update IDs are claimed in PostgreSQL before handler execution. A
completed receipt is permanent; a stale processing lease may be reclaimed after
a restart. Draft parsing, Meeting captures, and Meeting review generation also
have unique PostgreSQL jobs. The bot normally completes them synchronously, and
the scheduler retries pending or stale jobs up to three times without relying on
process memory.

The scheduler runs cleanup hourly. Completed/converted voice transcripts are
redacted after 30 days; unresolved transcripts are redacted after 90 days.
Structured records, Meeting links, events, source IDs, and idempotency records
remain. Expired login codes, revoked sessions, and expired dialog sessions are
removed after 30 days. Old inactive ordinary drafts lose bulky AI payloads but
retain stable provenance where required. Orphan `flowmate-*.ogg` files older
than one hour are removed at bot startup and before creating a new temp file.

Audit events contain only actor/action/outcome, entity IDs, safe categories,
counts, and prompt versions. They never contain note text, transcripts, raw AI
payloads, provider errors, cookies, tokens, or API keys. Audit has no public PWA
endpoint.

### Backup and restore

Create a compressed PostgreSQL custom-format backup:

```bash
BACKUP_DIR=/var/backups/flowmate make backup
```

The directory receives mode `0700`, dump and manifest files receive `0600`, and
the manifest contains SHA-256 and size only. Daily runs retain seven daily and
four Sunday weekly copies. Configure host cron to run `make backup`;
application APScheduler intentionally does not own backups.

Verify an isolated restore using the test PostgreSQL container:

```bash
make restore-check backup=/var/backups/flowmate/flowmate-daily-TIMESTAMP.dump
```

The command refuses targets not ending in `_restore_test`, verifies checksum
and Alembic head, and never replaces production. Backups are not encrypted or
copied off-host by FlowMate; production storage must provide both separately.

### Offline AI evaluation and real voice smoke

`make ai-eval` validates anonymized recorded structured responses, critical date
rules, prompt versions, and routing prompt size without network access. Fixtures
use fictional names and contain no corporate data.

For a release candidate, send newly recorded non-sensitive Telegram voice clips
covering: a short action; `7 августа` without time; an explicit date and time;
multiple items; noisy speech; silence; oversized audio; voice clarification;
Meeting capture/review; duplicate update delivery; and restart during parsing.
Verify acknowledgement, dates, deferred clarification, idempotency, recovery,
final records/reminders, and absence of `.ogg` files. Never commit recordings or
their real transcripts.

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

`make check` is the mandatory validation command. It checks Python and frontend
formatting, Ruff/ESLint, strict mypy/TypeScript, unit and integration tests, and
the production PWA build. The default test URL is
`postgresql+asyncpg://flowmate_test:flowmate_test@localhost:5433/flowmate_test`;
custom test database names must end in `_test`.

Tests apply Alembic migrations and never call `metadata.create_all`. Database
tests use transactions for cleanup and never access development data. Telegram,
OpenAI, and other external APIs are not contacted.

## Architecture

```text
browser -> web (Nginx/PWA) -> api (FastAPI/Uvicorn) -> postgres
                    |
Telegram -> bot ----+  [optional Compose profile: bot]
              |
              +-> temporary OGG -> speech provider -> note
                                                 |
                         persistent AI draft <-+-> clarification dialog
                                      |
                                      +-> atomic confirmed records
                                          (Task Engine services)
                                                   |
                              Telegram lists/actions/history

PostgreSQL <- scheduler worker -> Telegram notification service
              [optional Compose profile: scheduler]
```

All application processes use the same non-root runtime image and shared async
SQLAlchemy infrastructure. The environment allowlist remains the source of
Telegram authorization; the `users` table owns each immutable note. Voice audio
exists only in a permission-restricted temporary file while one update is
processed. Text and transcribed voice content are stored as notes owned by that
user; original audio is never persisted. The optional AI boundary receives Note
text only and returns validated Pydantic data. Application persistence stores
the validated draft and its state; the provider has no database access or
tools.
