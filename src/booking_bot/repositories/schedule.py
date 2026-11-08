from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from booking_bot.db.models import (
    Booking,
    BookingStatus,
    Break,
    BusinessHours,
    Master,
    MasterHours,
    ScheduleException,
    Service,
)
from booking_bot.services.availability import Calendar, DayOverride, Window


def _window(is_closed: bool, open_time, close_time) -> Window | None:
    if is_closed or open_time is None or close_time is None:
        return None
    return Window(open_time, close_time)


async def active_services(session: AsyncSession) -> list[Service]:
    rows = await session.scalars(select(Service).where(Service.is_active.is_(True)).order_by(Service.name))  # noqa: E501
    return list(rows)


async def masters_for_service(session: AsyncSession, service_id: int) -> list[Master]:
    rows = await session.scalars(
        select(Master)
        .join(Master.services)
        .where(Master.is_active.is_(True), Service.id == service_id)
        .order_by(Master.id)
        .options(selectinload(Master.services))
    )
    return list(rows)


async def load_calendar(
    session: AsyncSession,
    range_start: datetime,
    range_end: datetime,
) -> Calendar:
    calendar = Calendar()
    for row in await session.scalars(select(BusinessHours)):
        calendar.salon[row.weekday] = _window(row.is_closed, row.open_time, row.close_time)
    for row in await session.scalars(select(MasterHours)):
        calendar.master_hours.setdefault(row.master_id, {})[row.weekday] = _window(
            row.is_closed, row.open_time, row.close_time
        )
    for row in await session.scalars(select(Break)):
        if row.master_id is None:
            calendar.salon_breaks.setdefault(row.weekday, []).append((row.start_time, row.end_time))
        else:
            calendar.master_breaks.setdefault(row.master_id, {}).setdefault(row.weekday, []).append(
                (row.start_time, row.end_time)
            )
    for row in await session.scalars(select(ScheduleException)):
        override = DayOverride(row.is_closed, row.open_time, row.close_time)
        if row.master_id is None:
            calendar.salon_exceptions[row.on_date] = override
        else:
            calendar.master_exceptions.setdefault(row.master_id, {})[row.on_date] = override
    busy = await session.execute(
        select(Booking.master_id, Booking.starts_at, Booking.ends_at).where(
            Booking.status.in_((BookingStatus.PENDING, BookingStatus.CONFIRMED)),
            Booking.starts_at < range_end,
            Booking.ends_at > range_start,
        )
    )
    calendar.busy = [(master_id, starts_at, ends_at) for master_id, starts_at, ends_at in busy]
    return calendar
