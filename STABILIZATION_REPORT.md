# Stage 8 Stabilization Report

## Issues found and fixed

- Telegram deduplication was bounded to recent JSON update IDs; it now uses
  permanent PostgreSQL receipts with reclaimable processing leases.
- Draft parsing, clarification answers, Meeting captures, and review generation
  could remain stale after a process crash; durable retry jobs now recover them.
- A draft clarification claim had no start timestamp; stale claims now expire
  after five minutes without losing the persisted answer job.
- Reminder timeout could cause an unsafe automatic resend; network-started
  deliveries now become `delivery_unknown` and require audited manual retry.
- Cleanup, prompt versions, general safe audit, backup/restore, and offline AI
  regression evaluation were missing and are now operational.
- Final migration testing found an audit FK that conflicted with append-only
  history. Audit user IDs are now immutable UUID snapshots without a mutable FK.

## Duplicate protection

- Telegram updates use permanent PostgreSQL receipts with processing leases.
- Draft and Meeting conversion retain row locks, source uniqueness, domain
  events, and transaction-level rollback.
- Reminder and digest creation retain unique deduplication keys; uncertain
  Telegram delivery becomes `delivery_unknown` without automatic retry.

## Recovery

- PostgreSQL AI jobs cover draft parsing, Meeting capture parsing, deferred
  refinement storage, and Meeting review generation.
- APScheduler claims pending or stale jobs with row locks and leases, retries
  safe failures up to three times, and records recovery audit events.
- PostgreSQL remains the only durable source of truth.

## Backup status

- `make backup` creates compressed custom-format dumps, SHA-256 manifests, and
  rotates seven daily plus four weekly copies.
- `make restore-check backup=...` restores only into `*_restore_test` and checks
  checksum, schema revision, and basic table readability.
- Off-host replication and encryption remain deployment responsibilities.

## Audit and cleanup

- Authentication, Task Engine mutations, conversion, settings, recovery,
  cleanup, and operator retry write safe audit events without user content.
- Terminal voice transcripts: 30 days; unresolved transcripts: 90 days.
- Final structured records, links, source IDs, and event history remain.
- Expired authentication/dialog records: 30 days; orphan audio: one hour.

## AI evaluation and edge cases

- Draft, routing, refinement, snooze, and Meeting review prompts are versioned.
- Nine offline anonymized fixtures cover date-only reminders, invalid dates,
  year rollover, DST, multi-item extraction, Meeting context, strict provider
  validation, and short voice commands without network calls.
- Restart-safe leases, unknown Telegram delivery, payload cleanup, reduced
  routing prompt, and orphan-audio recovery are covered by regression tests.

## Verification results

- `make check`: passed (`284` unit, `104` integration, `37` frontend tests,
  Ruff, mypy, Prettier, ESLint, TypeScript, and production PWA build).
- `make ai-eval`: `9/9` fixtures passed; network calls: `0`.
- Alembic: upgrade to `0020`, downgrade to `0019`, and upgrade to `0020`
  passed; there is one head.
- Production and test `docker compose config --quiet`: passed.
- `/health/live` and `/health/ready`: passed through API and PWA proxy.
- One-shot cleanup and scheduler registration smoke passed; the scheduler has
  `process_due_reminders`, `recover_ai_jobs`, and `database_cleanup` jobs.
- Production frontend secret canary scan: clean.
- Backup created as compressed custom format with mode `0600` in a `0700`
  directory; checksum and isolated restore into `flowmate_restore_test` passed.

## Manual real-voice status

The automated suite uses only fakes and does not call Telegram, speech, or AI
providers. The release checklist for newly recorded real audio remains manual:
short/noisy/empty/oversized audio, date without time, multi-item capture, voice
clarification, duplicate delivery, Meeting capture/review, and restart during
processing. No real audio or transcript is stored in the repository.

## Remaining limitations

- Telegram has no idempotency key for `sendMessage`; avoiding duplicates means
  an unknown delivery may require explicit operator retry.
- Offline fixtures detect regressions but do not measure current hosted-model
  quality; the documented real-voice smoke remains required for release.

## MVP readiness

All automated, migration, build, health, worker, cleanup, and backup/restore
gates pass. The code is ready for an MVP stabilization candidate; production
release approval remains conditional on completing the manual real-voice
checklist and configuring protected off-host backup replication.
