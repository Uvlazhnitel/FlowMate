from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import TelegramOperationReceipt


@dataclass(frozen=True, slots=True)
class ReceiptClaim:
    accepted: bool
    duplicate: bool
    receipt: TelegramOperationReceipt


def receipt_now() -> datetime:
    return datetime.now(UTC)


async def claim_telegram_update(
    session: AsyncSession,
    *,
    update_id: int,
    telegram_user_id: int | None,
    event_kind: str,
    lease_seconds: int = 300,
    now: datetime | None = None,
) -> ReceiptClaim:
    if update_id <= 0 or lease_seconds <= 0:
        raise ValueError("update ID and lease duration must be positive")
    current = now or receipt_now()
    created = (
        await session.execute(
            insert(TelegramOperationReceipt)
            .values(
                update_id=update_id,
                telegram_user_id=telegram_user_id,
                event_kind=event_kind,
                status="processing",
                lease_expires_at=current + timedelta(seconds=lease_seconds),
            )
            .on_conflict_do_nothing(index_elements=["update_id"])
            .returning(TelegramOperationReceipt.update_id)
        )
    ).scalar_one_or_none()
    receipt = await session.scalar(
        select(TelegramOperationReceipt)
        .where(TelegramOperationReceipt.update_id == update_id)
        .with_for_update()
    )
    if receipt is None:
        raise RuntimeError("Telegram receipt could not be loaded")
    if created is not None:
        await session.flush()
        return ReceiptClaim(True, False, receipt)
    if receipt.status == "completed":
        return ReceiptClaim(False, True, receipt)
    if receipt.status == "processing" and receipt.lease_expires_at is not None:
        if receipt.lease_expires_at > current:
            return ReceiptClaim(False, False, receipt)
    receipt.status = "processing"
    receipt.attempt_count += 1
    receipt.lease_expires_at = current + timedelta(seconds=lease_seconds)
    receipt.last_error_code = None
    receipt.completed_at = None
    await session.flush()
    return ReceiptClaim(True, False, receipt)


async def complete_telegram_update(
    session: AsyncSession,
    update_id: int,
    *,
    now: datetime | None = None,
) -> None:
    receipt = await session.get(TelegramOperationReceipt, update_id)
    if receipt is None:
        return
    receipt.status = "completed"
    receipt.completed_at = now or receipt_now()
    receipt.lease_expires_at = None
    receipt.last_error_code = None
    await session.flush()


async def fail_telegram_update(
    session: AsyncSession,
    update_id: int,
    *,
    error_code: str,
) -> None:
    receipt = await session.get(TelegramOperationReceipt, update_id)
    if receipt is None:
        return
    receipt.status = "retryable_failed"
    receipt.lease_expires_at = None
    receipt.last_error_code = error_code[:64]
    await session.flush()
