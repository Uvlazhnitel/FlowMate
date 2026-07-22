from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import AIProcessingJob


@dataclass(frozen=True, slots=True)
class ClaimedAIJob:
    id: UUID
    lease_token: UUID


def jobs_now() -> datetime:
    return datetime.now(UTC)


async def enqueue_ai_job(
    session: AsyncSession,
    *,
    user_id: UUID,
    job_kind: str,
    entity_id: UUID,
    operation_key: str,
    prompt_name: str,
    prompt_version: str,
    input_text: str | None = None,
    input_source: str | None = None,
    now: datetime | None = None,
) -> AIProcessingJob:
    current = now or jobs_now()
    job_id = uuid4()
    created = (
        await session.execute(
            insert(AIProcessingJob)
            .values(
                id=job_id,
                user_id=user_id,
                job_kind=job_kind,
                entity_id=entity_id,
                operation_key=operation_key,
                status="pending",
                prompt_name=prompt_name,
                prompt_version=prompt_version,
                input_text=input_text,
                input_source=input_source,
                next_attempt_at=current,
            )
            .on_conflict_do_nothing(constraint="uq_ai_processing_job")
            .returning(AIProcessingJob.id)
        )
    ).scalar_one_or_none()
    target_id = created or await session.scalar(
        select(AIProcessingJob.id).where(
            AIProcessingJob.job_kind == job_kind,
            AIProcessingJob.entity_id == entity_id,
            AIProcessingJob.operation_key == operation_key,
        )
    )
    if target_id is None:
        raise RuntimeError("AI job could not be loaded")
    job = await session.get(AIProcessingJob, target_id)
    if job is None:
        raise RuntimeError("AI job could not be loaded")
    return job


async def claim_due_ai_jobs(
    session: AsyncSession,
    *,
    limit: int,
    max_attempts: int,
    lease_seconds: int,
    now: datetime | None = None,
) -> list[ClaimedAIJob]:
    current = now or jobs_now()
    if limit <= 0 or max_attempts <= 0 or lease_seconds <= 0:
        raise ValueError("AI job claim limits must be positive")
    jobs = list(
        await session.scalars(
            select(AIProcessingJob)
            .where(
                AIProcessingJob.attempt_count < max_attempts,
                or_(
                    (AIProcessingJob.status == "pending")
                    & (AIProcessingJob.next_attempt_at <= current),
                    (AIProcessingJob.status == "processing")
                    & (AIProcessingJob.lease_expires_at <= current),
                ),
            )
            .order_by(AIProcessingJob.next_attempt_at, AIProcessingJob.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    claims: list[ClaimedAIJob] = []
    for job in jobs:
        token = uuid4()
        job.status = "processing"
        job.attempt_count += 1
        job.lease_token = token
        job.lease_expires_at = current + timedelta(seconds=lease_seconds)
        job.last_error_code = None
        claims.append(ClaimedAIJob(job.id, token))
    await session.flush()
    return claims


async def complete_ai_job(
    session: AsyncSession,
    claim: ClaimedAIJob,
    *,
    now: datetime | None = None,
) -> bool:
    job = await session.scalar(
        select(AIProcessingJob)
        .where(
            AIProcessingJob.id == claim.id,
            AIProcessingJob.status == "processing",
            AIProcessingJob.lease_token == claim.lease_token,
        )
        .with_for_update()
    )
    if job is None:
        return False
    job.status = "completed"
    job.completed_at = now or jobs_now()
    job.input_text = None
    job.input_source = None
    job.lease_token = None
    job.lease_expires_at = None
    await session.flush()
    return True


async def complete_entity_ai_jobs(
    session: AsyncSession,
    *,
    entity_id: UUID,
    job_kinds: tuple[str, ...],
    now: datetime | None = None,
) -> None:
    current = now or jobs_now()
    jobs = list(
        await session.scalars(
            select(AIProcessingJob).where(
                AIProcessingJob.entity_id == entity_id,
                AIProcessingJob.job_kind.in_(job_kinds),
                AIProcessingJob.status.in_(("pending", "processing")),
            )
        )
    )
    for job in jobs:
        job.status = "completed"
        job.completed_at = current
        job.input_text = None
        job.input_source = None
        job.lease_token = None
        job.lease_expires_at = None
        job.last_error_code = None
    if jobs:
        await session.flush()


async def fail_ai_job(
    session: AsyncSession,
    claim: ClaimedAIJob,
    *,
    error_code: str,
    max_attempts: int,
    retry_delay: timedelta,
    now: datetime | None = None,
) -> bool:
    current = now or jobs_now()
    job = await session.scalar(
        select(AIProcessingJob)
        .where(
            AIProcessingJob.id == claim.id,
            AIProcessingJob.status == "processing",
            AIProcessingJob.lease_token == claim.lease_token,
        )
        .with_for_update()
    )
    if job is None:
        return False
    job.last_error_code = error_code[:64]
    job.lease_token = None
    job.lease_expires_at = None
    if job.attempt_count >= max_attempts:
        job.status = "failed"
        job.input_text = None
        job.input_source = None
    else:
        job.status = "pending"
        job.next_attempt_at = current + retry_delay
    await session.flush()
    return True
