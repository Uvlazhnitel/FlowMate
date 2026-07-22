import argparse
import asyncio
from datetime import UTC, datetime
from uuid import UUID

from flowmate.core.config import get_settings
from flowmate.core.logging import configure_logging
from flowmate.db.session import create_engine, create_session_factory, session_scope
from flowmate.reminders.service import retry_unknown_reminder
from flowmate.stabilization.audit import record_audit_event
from flowmate.stabilization.cleanup import run_database_cleanup


async def run(command: str, reminder_id: UUID | None) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, structured=settings.app_env == "production")
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    try:
        async with session_scope(session_factory) as session:
            if command == "cleanup":
                await run_database_cleanup(
                    session,
                    terminal_transcript_days=(
                        settings.terminal_transcript_retention_days
                    ),
                    unresolved_transcript_days=(
                        settings.unresolved_transcript_retention_days
                    ),
                    expired_record_days=settings.expired_record_retention_days,
                )
                return
            if reminder_id is None:
                raise ValueError("reminder ID is required")
            reminder = await retry_unknown_reminder(
                session, reminder_id, now=datetime.now(UTC)
            )
            if reminder is None:
                raise ValueError("delivery_unknown reminder not found")
            await record_audit_event(
                session,
                actor_kind="operator",
                action="reminder.manual_retry",
                outcome="success",
                user_id=reminder.user_id,
                entity_kind="reminder",
                entity_id=reminder.id,
                safe_metadata={"status": "pending"},
            )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="FlowMate maintenance")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("cleanup")
    retry = subcommands.add_parser("retry-reminder")
    retry.add_argument("reminder_id", type=UUID)
    args = parser.parse_args()
    asyncio.run(run(args.command, getattr(args, "reminder_id", None)))


if __name__ == "__main__":
    main()
