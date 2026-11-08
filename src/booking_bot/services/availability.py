from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Window:
    open_time: time
    close_time: time


@dataclass(frozen=True)
class DayOverride:
    is_closed: bool
    open_time: time | None = None
    close_time: time | None = None


@dataclass
class Calendar:
    """Salon and master schedule used to build slots. Missing master hours inherit the salon."""

    salon: dict[int, Window | None] = field(default_factory=dict)
    master_hours: dict[int, dict[int, Window | None]] = field(default_factory=dict)
    salon_breaks: dict[int, list[tuple[time, time]]] = field(default_factory=dict)
    master_breaks: dict[int, dict[int, list[tuple[time, time]]]] = field(default_factory=dict)
    salon_exceptions: dict[date, DayOverride] = field(default_factory=dict)
    master_exceptions: dict[int, dict[date, DayOverride]] = field(default_factory=dict)
    busy: list[tuple[int, datetime, datetime]] = field(default_factory=list)


def _override_window(override: DayOverride) -> Window | None:
    if override.is_closed or override.open_time is None or override.close_time is None:
        return None
    if override.open_time >= override.close_time:
        return None
    return Window(override.open_time, override.close_time)


def resolve_window(calendar: Calendar, master_id: int, day: date) -> Window | None:
    """Salon hours, then the master's hours, then that date's exceptions."""

    salon = calendar.salon.get(day.weekday())
    salon_override = calendar.salon_exceptions.get(day)
    if salon_override is not None:
        salon = _override_window(salon_override)
    if salon is None:
        return None

    weekly = calendar.master_hours.get(master_id, {})
    if day.weekday() in weekly:
        master = weekly[day.weekday()]
    else:
        master = salon
    master_override = calendar.master_exceptions.get(master_id, {}).get(day)
    if master_override is not None:
        master = _override_window(master_override)
    if master is None:
        return None

    open_time = max(salon.open_time, master.open_time)
    close_time = min(salon.close_time, master.close_time)
    if open_time >= close_time:
        return None
    return Window(open_time, close_time)


def _breaks(calendar: Calendar, master_id: int, weekday: int) -> list[tuple[time, time]]:
    rows = list(calendar.salon_breaks.get(weekday, []))
    rows.extend(calendar.master_breaks.get(master_id, {}).get(weekday, []))
    return rows


def _overlaps(start: datetime, end: datetime, other_start: datetime, other_end: datetime) -> bool:
    return start < other_end and other_start < end


def slots_for_master(
    calendar: Calendar,
    master_id: int,
    day: date,
    duration_minutes: int,
    step_minutes: int,
    tz: ZoneInfo,
    now: datetime,
    lead_minutes: int,
) -> list[datetime]:
    window = resolve_window(calendar, master_id, day)
    if window is None:
        return []
    earliest = now.astimezone(tz) + timedelta(minutes=lead_minutes)
    cursor = datetime.combine(day, window.open_time, tzinfo=tz)
    close_at = datetime.combine(day, window.close_time, tzinfo=tz)
    duration = timedelta(minutes=duration_minutes)
    step = timedelta(minutes=step_minutes)
    breaks = [
        (datetime.combine(day, start, tzinfo=tz), datetime.combine(day, end, tzinfo=tz))
        for start, end in _breaks(calendar, master_id, day.weekday())
    ]
    busy = [
        (start.astimezone(tz), end.astimezone(tz))
        for owner, start, end in calendar.busy
        if owner == master_id
    ]
    slots: list[datetime] = []
    while cursor + duration <= close_at:
        slot_end = cursor + duration
        blocked = any(_overlaps(cursor, slot_end, start, end) for start, end in breaks)
        blocked = blocked or any(_overlaps(cursor, slot_end, start, end) for start, end in busy)
        if cursor >= earliest and not blocked:
            slots.append(cursor)
        cursor += step
    return slots


def slots_for_any_master(
    calendar: Calendar,
    master_ids: list[int],
    day: date,
    duration_minutes: int,
    step_minutes: int,
    tz: ZoneInfo,
    now: datetime,
    lead_minutes: int,
) -> list[datetime]:
    found: set[datetime] = set()
    for master_id in master_ids:
        found.update(
            slots_for_master(
                calendar,
                master_id,
                day,
                duration_minutes,
                step_minutes,
                tz,
                now,
                lead_minutes,
            )
        )
    return sorted(found)


def free_master_ids(
    calendar: Calendar,
    master_ids: list[int],
    start: datetime,
    duration_minutes: int,
    step_minutes: int,
    tz: ZoneInfo,
    now: datetime,
    lead_minutes: int,
) -> list[int]:
    """Masters who can start this service at `start`, in ascending id order."""

    local_day = start.astimezone(tz).date()
    available: list[int] = []
    for master_id in sorted(master_ids):
        slots = slots_for_master(
            calendar,
            master_id,
            local_day,
            duration_minutes,
            step_minutes,
            tz,
            now,
            lead_minutes,
        )
        if start.astimezone(tz) in slots:
            available.append(master_id)
    return available
