from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def resolve_local_datetime(
    local_date: date,
    local_time: time,
    timezone: ZoneInfo,
) -> datetime:
    """Resolve a wall time, choosing the first fold or next valid DST instant."""
    naive = datetime.combine(local_date, local_time.replace(tzinfo=None))
    for minute_offset in range(181):
        candidate = naive + timedelta(minutes=minute_offset)
        for fold in (0, 1):
            aware = candidate.replace(tzinfo=timezone, fold=fold)
            round_trip = aware.astimezone(UTC).astimezone(timezone)
            if round_trip.replace(tzinfo=None) == candidate:
                return aware
    raise ValueError("local datetime could not be resolved")


def quiet_hours_end(
    now: datetime,
    *,
    timezone: ZoneInfo,
    start: time,
    end: time,
) -> datetime | None:
    local_now = now.astimezone(timezone)
    wall_time = local_now.time().replace(tzinfo=None)
    if start < end:
        if not start <= wall_time < end:
            return None
        end_date = local_now.date()
    else:
        if wall_time >= start:
            end_date = local_now.date() + timedelta(days=1)
        elif wall_time < end:
            end_date = local_now.date()
        else:
            return None
    return resolve_local_datetime(end_date, end, timezone).astimezone(UTC)


def tomorrow_at(
    now: datetime,
    *,
    timezone: ZoneInfo,
    local_time: time,
) -> datetime:
    tomorrow = now.astimezone(timezone).date() + timedelta(days=1)
    return resolve_local_datetime(tomorrow, local_time, timezone).astimezone(UTC)
