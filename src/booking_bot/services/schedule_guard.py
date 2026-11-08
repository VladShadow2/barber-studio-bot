"""Decide whether a master's own schedule edit would leave an active booking outside the grid."""

from dataclasses import dataclass
from datetime import date, datetime, time

from booking_bot.db.models import BookingStatus
from booking_bot.services.admin_edit import break_overlaps_booking
from booking_bot.services.availability import Calendar, DayOverride, Window, resolve_window

_ACTIVE = {BookingStatus.PENDING, BookingStatus.CONFIRMED}


@dataclass(frozen=True)
class HoursEdit:
    weekday: int
    closed: bool
    open_time: time | None
    close_time: time | None


@dataclass(frozen=True)
class BreakEdit:
    weekday: int
    start: time
    end: time


@dataclass(frozen=True)
class ExceptionEdit:
    on_date: date
    closed: bool
    open_time: time | None
    close_time: time | None
    remove: bool = False


def _copy_calendar(calendar: Calendar) -> Calendar:
    return Calendar(
        salon=dict(calendar.salon),
        master_hours={master_id: dict(days) for master_id, days in calendar.master_hours.items()},
        salon_breaks={day: list(rows) for day, rows in calendar.salon_breaks.items()},
        master_breaks={
            master_id: {day: list(rows) for day, rows in days.items()}
            for master_id, days in calendar.master_breaks.items()
        },
        salon_exceptions=dict(calendar.salon_exceptions),
        master_exceptions={
            master_id: dict(days) for master_id, days in calendar.master_exceptions.items()
        },
    )


def _window(closed: bool, open_time: time | None, close_time: time | None) -> Window | None:
    if closed or open_time is None or close_time is None or open_time >= close_time:
        return None
    return Window(open_time, close_time)


def _active(visit, now: datetime) -> bool:
    return visit.status in _ACTIVE and visit.ends_at > now


def visit_inside(visit, window: Window | None, tz) -> bool:
    if window is None:
        return False
    start = visit.starts_at.astimezone(tz)
    end = visit.ends_at.astimezone(tz)
    if start.date() != end.date():
        return False
    return start.time() >= window.open_time and end.time() <= window.close_time


def conflicting_visits(calendar, visits, master_id: int, change, tz, now: datetime) -> list:
    """Return active visits the change would no longer cover. The calendar is not modified."""

    if isinstance(change, BreakEdit):
        return [
            visit
            for visit in visits
            if visit.master_id == master_id
            and _active(visit, now)
            and break_overlaps_booking(
                visit, change.weekday, change.start, change.end, master_id, tz
            )
        ]

    updated = _copy_calendar(calendar)
    if isinstance(change, HoursEdit):
        updated.master_hours.setdefault(master_id, {})[change.weekday] = _window(
            change.closed, change.open_time, change.close_time
        )
    elif isinstance(change, ExceptionEdit):
        days = updated.master_exceptions.setdefault(master_id, {})
        if change.remove:
            days.pop(change.on_date, None)
        else:
            days[change.on_date] = DayOverride(change.closed, change.open_time, change.close_time)
    else:
        return []

    conflicts = []
    for visit in visits:
        if visit.master_id != master_id or not _active(visit, now):
            continue
        start = visit.starts_at.astimezone(tz)
        if isinstance(change, HoursEdit):
            if start.weekday() != change.weekday:
                continue
            if start.date() in calendar.master_exceptions.get(master_id, {}):
                continue
        elif start.date() != change.on_date:
            continue
        if not visit_inside(visit, resolve_window(updated, master_id, start.date()), tz):
            conflicts.append(visit)
    return conflicts
