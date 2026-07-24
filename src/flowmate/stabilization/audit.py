import re
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import AuditEvent

ActorKind = Literal["telegram", "pwa", "system", "operator"]
AuditOutcome = Literal["success", "rejected", "failed", "recovered"]

SAFE_METADATA_KEYS = {
    "attempt_count",
    "category",
    "count",
    "event_kind",
    "job_kind",
    "prompt_name",
    "prompt_version",
    "reason",
    "status",
    "workspace",
}
SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def validate_safe_metadata(values: dict[str, object] | None) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in (values or {}).items():
        if key not in SAFE_METADATA_KEYS:
            raise ValueError(f"unsafe audit metadata key: {key}")
        if isinstance(value, bool | int):
            result[key] = value
        elif isinstance(value, UUID):
            result[key] = str(value)
        elif isinstance(value, str) and SAFE_VALUE.fullmatch(value):
            result[key] = value
        else:
            raise ValueError(f"unsafe audit metadata value for: {key}")
    return result


async def record_audit_event(
    session: AsyncSession,
    *,
    actor_kind: ActorKind,
    action: str,
    outcome: AuditOutcome,
    user_id: UUID | None = None,
    entity_kind: str | None = None,
    entity_id: UUID | None = None,
    correlation_id: str | None = None,
    safe_metadata: dict[str, object] | None = None,
) -> AuditEvent:
    if SAFE_VALUE.fullmatch(action) is None:
        raise ValueError("audit action must be a safe identifier")
    if entity_kind is not None and SAFE_VALUE.fullmatch(entity_kind) is None:
        raise ValueError("audit entity kind must be a safe identifier")
    if correlation_id is not None and SAFE_VALUE.fullmatch(correlation_id) is None:
        raise ValueError("audit correlation ID must be a safe identifier")
    event = AuditEvent(
        user_id=user_id,
        actor_kind=actor_kind,
        action=action,
        entity_kind=entity_kind,
        entity_id=entity_id,
        outcome=outcome,
        correlation_id=correlation_id,
        safe_metadata=validate_safe_metadata(safe_metadata),
    )
    session.add(event)
    await session.flush()
    return event
