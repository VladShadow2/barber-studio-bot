from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from booking_bot.config import load_settings
from booking_bot.db.models import (
    Booking,
    BookingStatus,
    Break,
    Master,
    MasterHours,
    ScheduleException,
)
from booking_bot.db.session import get_sessionmaker
from booking_bot.repositories.schedule import load_calendar
from booking_bot.services.admin_edit import add_break, add_exception, upsert_master_hours
from booking_bot.services.schedule_guard import conflicting_visits
from booking_bot.texts import WEEKDAY_SHORT, WEEKDAYS, saved_hours_label


@dataclass(frozen=True)
class LinkedMaster:
    id: int
    name: str


@dataclass(frozen=True)
class DayHours:
    is_closed: bool
    open_time: time | None
    close_time: time | None


@dataclass(frozen=True)
class BreakView:
    id: int
    weekday: int
    start_time: time
    end_time: time


@dataclass(frozen=True)
class VisitView:
    starts_at: datetime
    ends_at: datetime
    master_id: int
    status: BookingStatus
    service_name: str


@dataclass(frozen=True)
class ExceptionView:
    id: int
    on_date: date
    is_closed: bool
    open_time: time | None
    close_time: time | None


def _tz():
    return ZoneInfo(load_settings().timezone)


async def find_master(user_id: int) -> LinkedMaster | None:
    async with get_sessionmaker()() as session:
        row = await session.scalar(select(Master).where(Master.telegram_user_id == user_id))
        if row is None:
            return None
        return LinkedMaster(row.id, row.name)


async def home_text(master: LinkedMaster) -> str:
    async with get_sessionmaker()() as session:
        rows = list(
            await session.scalars(select(MasterHours).where(MasterHours.master_id == master.id))
        )
        by_day = {
            row.weekday: DayHours(row.is_closed, row.open_time, row.close_time) for row in rows
        }
    lines = [f"🕒 Мій графік — {master.name}"]
    for weekday, name in enumerate(WEEKDAYS):
        row = by_day.get(weekday)
        if row is None:
            current = "як у салону"
        else:
            current = saved_hours_label(row.is_closed, row.open_time, row.close_time)
        lines.append(f"{WEEKDAY_SHORT[weekday]} {name}: {current}")
    lines.append("\nТут змінюється лише ваш графік. Записи клієнтів не скасовуються.")
    return "\n".join(lines)


async def day_hours(master_id: int, weekday: int) -> DayHours | None:
    async with get_sessionmaker()() as session:
        row = await session.get(MasterHours, (master_id, weekday))
        if row is None:
            return None
        return DayHours(row.is_closed, row.open_time, row.close_time)


async def list_breaks(master_id: int) -> list[BreakView]:
    async with get_sessionmaker()() as session:
        rows = list(
            await session.scalars(
                select(Break)
                .where(Break.master_id == master_id)
                .order_by(Break.weekday, Break.start_time, Break.id)
            )
        )
        return [BreakView(row.id, row.weekday, row.start_time, row.end_time) for row in rows]


async def list_exceptions(master_id: int) -> list[ExceptionView]:
    async with get_sessionmaker()() as session:
        rows = list(
            await session.scalars(
                select(ScheduleException)
                .where(ScheduleException.master_id == master_id)
                .order_by(ScheduleException.on_date, ScheduleException.id)
            )
        )
        return [
            ExceptionView(row.id, row.on_date, row.is_closed, row.open_time, row.close_time)
            for row in rows
        ]


async def preview_conflicts(master_id: int, change, now: datetime | None = None) -> list:
    moment = now or datetime.now(UTC)
    async with get_sessionmaker()() as session:
        calendar = await load_calendar(
            session, moment - timedelta(days=1), moment + timedelta(days=370)
        )
        rows = list(
            await session.scalars(
                select(Booking).where(
                    Booking.master_id == master_id,
                    Booking.status.in_((BookingStatus.PENDING, BookingStatus.CONFIRMED)),
                    Booking.ends_at > moment,
                )
            )
        )
        visits = [
            VisitView(row.starts_at, row.ends_at, row.master_id, row.status, row.service_name)
            for row in rows
        ]
    return conflicting_visits(calendar, visits, master_id, change, _tz(), moment)


async def commit_hours(master_id: int, weekday: int, closed: bool, open_time, close_time) -> None:
    async with get_sessionmaker()() as session:
        async with session.begin():
            await upsert_master_hours(session, master_id, weekday, closed, open_time, close_time)


async def commit_break(master_id: int, weekday: int, start: time, end: time) -> None:
    async with get_sessionmaker()() as session:
        async with session.begin():
            await add_break(session, master_id, weekday, start, end)


async def commit_exception(
    master_id: int, on_date: date, closed: bool, open_time, close_time
) -> None:
    async with get_sessionmaker()() as session:
        async with session.begin():
            await add_exception(
                session,
                master_id,
                on_date,
                closed,
                open_time,
                close_time,
                note="Оновлено майстром.",
            )


async def _owned_break(session: AsyncSession, master_id: int, break_id: int) -> Break | None:
    row = await session.get(Break, break_id)
    if row is None or row.master_id != master_id:
        return None
    return row


async def _owned_exception(
    session: AsyncSession, master_id: int, exception_id: int
) -> ScheduleException | None:
    row = await session.get(ScheduleException, exception_id)
    if row is None or row.master_id != master_id:
        return None
    return row


async def break_for_master(master_id: int, break_id: int) -> BreakView | None:
    async with get_sessionmaker()() as session:
        row = await _owned_break(session, master_id, break_id)
        if row is None:
            return None
        return BreakView(row.id, row.weekday, row.start_time, row.end_time)


async def exception_for_master(master_id: int, exception_id: int) -> ExceptionView | None:
    async with get_sessionmaker()() as session:
        row = await _owned_exception(session, master_id, exception_id)
        if row is None:
            return None
        return ExceptionView(row.id, row.on_date, row.is_closed, row.open_time, row.close_time)


async def commit_break_delete(master_id: int, break_id: int) -> bool:
    async with get_sessionmaker()() as session:
        async with session.begin():
            row = await _owned_break(session, master_id, break_id)
            if row is None:
                return False
            await session.delete(row)
    return True


async def commit_exception_delete(master_id: int, exception_id: int) -> bool:
    async with get_sessionmaker()() as session:
        async with session.begin():
            row = await _owned_exception(session, master_id, exception_id)
            if row is None:
                return False
            await session.delete(row)
    return True
