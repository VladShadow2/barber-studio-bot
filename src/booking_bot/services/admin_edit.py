import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from booking_bot.db.models import (
    Booking,
    BookingStatus,
    Break,
    BusinessHours,
    MasterHours,
    MasterService,
    ScheduleException,
    Service,
)
from booking_bot.texts import WEEKDAYS

_RANGE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")
_WEEKDAY = {name.lower(): index for index, name in enumerate(WEEKDAYS)}
_WEEKDAY.update(
    {
        "пн": 0,
        "пон": 0,
        "вт": 1,
        "вів": 1,
        "ср": 2,
        "сер": 2,
        "чт": 3,
        "чет": 3,
        "пт": 4,
        "сб": 5,
        "суб": 5,
        "нд": 6,
        "нед": 6,
        "mon": 0,
        "monday": 0,
        "tue": 1,
        "tuesday": 1,
        "wed": 2,
        "wednesday": 2,
        "thu": 3,
        "thursday": 3,
        "fri": 4,
        "friday": 4,
        "sat": 5,
        "saturday": 5,
        "sun": 6,
        "sunday": 6,
    }
)


def parse_price(raw: str) -> Decimal | None:
    try:
        value = Decimal(raw.strip()).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    if value < 0:
        return None
    return value


def parse_duration(raw: str) -> int | None:
    text = raw.strip()
    if not text.isdigit() or int(text) <= 0:
        return None
    return int(text)


def parse_service_line(raw: str) -> tuple[str, int, Decimal, str] | None:
    parts = [part.strip() for part in raw.split("|")]
    if len(parts) != 4:
        return None
    duration = parse_duration(parts[1])
    price = parse_price(parts[2])
    if not parts[0] or duration is None or price is None:
        return None
    return parts[0], duration, price, parts[3]


def parse_master_line(raw: str) -> tuple[str, str] | None:
    parts = [part.strip() for part in raw.split("|", 1)]
    if len(parts) != 2 or not parts[0]:
        return None
    return parts[0], parts[1]


def parse_hours(raw: str) -> tuple[bool, time | None, time | None] | None:
    text = raw.strip().lower()
    if text in {"closed", "закрито"}:
        return True, None, None
    match = _RANGE.fullmatch(raw.strip())
    if match is None:
        return None
    try:
        start = time(int(match.group(1)), int(match.group(2)))
        end = time(int(match.group(3)), int(match.group(4)))
    except ValueError:
        return None
    if start >= end:
        return None
    return False, start, end


def parse_break(raw: str) -> tuple[int, time, time] | None:
    pieces = raw.strip().split(maxsplit=1)
    if len(pieces) != 2:
        return None
    weekday = _WEEKDAY.get(pieces[0].lower())
    hours = parse_hours(pieces[1])
    if weekday is None or hours is None or hours[0]:
        return None
    return weekday, hours[1], hours[2]


def parse_exception(raw: str) -> tuple[date, bool, time | None, time | None] | None:
    pieces = raw.strip().split(maxsplit=1)
    if len(pieces) != 2:
        return None
    try:
        on_date = date.fromisoformat(pieces[0])
    except ValueError:
        return None
    hours = parse_hours(pieces[1])
    if hours is None:
        return None
    return on_date, hours[0], hours[1], hours[2]


async def booking_stats(session: AsyncSession, now: datetime, tz) -> dict[str, int]:
    local_today = now.astimezone(tz).date()
    start = datetime.combine(local_today, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    week_ago = now - timedelta(days=7)
    upcoming = await session.scalar(
        select(func.count())
        .select_from(Booking)
        .where(
            Booking.status.in_((BookingStatus.PENDING, BookingStatus.CONFIRMED)),
            Booking.starts_at >= now,
        )
    )
    today = await session.scalar(
        select(func.count())
        .select_from(Booking)
        .where(Booking.starts_at >= start, Booking.starts_at < end)
    )
    cancelled = await session.scalar(
        select(func.count())
        .select_from(Booking)
        .where(Booking.status == BookingStatus.CANCELLED, Booking.cancelled_at >= week_ago)
    )
    return {"upcoming": upcoming or 0, "today": today or 0, "cancelled_7d": cancelled or 0}


async def upsert_hours(session: AsyncSession, weekday: int, closed: bool, open_time, close_time) -> None:  # noqa: E501
    row = await session.get(BusinessHours, weekday)
    if row is None:
        row = BusinessHours(weekday=weekday)
        session.add(row)
    row.is_closed = closed
    row.open_time = open_time
    row.close_time = close_time


async def upsert_master_hours(
    session: AsyncSession,
    master_id: int,
    weekday: int,
    closed: bool,
    open_time,
    close_time,
) -> None:
    row = await session.get(MasterHours, (master_id, weekday))
    if row is None:
        row = MasterHours(master_id=master_id, weekday=weekday)
        session.add(row)
    row.is_closed = closed
    row.open_time = open_time
    row.close_time = close_time


def break_overlaps_booking(booking, weekday: int, start: time, end: time, master_id: int | None, tz) -> bool:  # noqa: E501
    """Half-open clock overlap on one weekday. A salon break applies to every master."""

    if master_id is not None and booking.master_id != master_id:
        return False
    if booking.status not in {BookingStatus.PENDING, BookingStatus.CONFIRMED}:
        return False
    local_start = booking.starts_at.astimezone(tz)
    local_end = booking.ends_at.astimezone(tz)
    if local_start.date() != local_end.date() or local_start.weekday() != weekday:
        return False
    return local_start.time() < end and local_end.time() > start


async def add_break(session: AsyncSession, master_id: int | None, weekday: int, start: time, end: time) -> None:  # noqa: E501
    session.add(Break(master_id=master_id, weekday=weekday, start_time=start, end_time=end))


async def add_exception(
    session: AsyncSession,
    master_id: int | None,
    on_date: date,
    closed: bool,
    open_time,
    close_time,
    note: str = "Оновлено адміністратором.",
) -> None:
    query = select(ScheduleException).where(ScheduleException.on_date == on_date)
    if master_id is None:
        query = query.where(ScheduleException.master_id.is_(None))
    else:
        query = query.where(ScheduleException.master_id == master_id)
    row = await session.scalar(query)
    if row is None:
        row = ScheduleException(master_id=master_id, on_date=on_date)
        session.add(row)
    row.is_closed = closed
    row.open_time = open_time
    row.close_time = close_time
    row.note = note


async def list_services(session: AsyncSession) -> list[Service]:
    rows = await session.scalars(select(Service).order_by(Service.name, Service.id))
    return list(rows)


async def assigned_service_ids(session: AsyncSession, master_id: int) -> list[int]:
    rows = await session.scalars(
        select(MasterService.service_id).where(MasterService.master_id == master_id)
    )
    return [int(service_id) for service_id in rows]


async def link_master_services(session: AsyncSession, master_id: int, service_ids: list[int]) -> None:  # noqa: E501
    existing = list(
        await session.scalars(select(MasterService).where(MasterService.master_id == master_id))
    )
    for row in existing:
        await session.delete(row)
    await session.flush()
    for service_id in service_ids:
        session.add(MasterService(master_id=master_id, service_id=service_id))
