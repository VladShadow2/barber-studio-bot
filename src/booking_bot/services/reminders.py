from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from booking_bot.config import Settings
from booking_bot.db.models import Booking, BookingStatus, Customer, Reminder, ReminderStatus
from booking_bot.services.policies import REMINDER_LEASE, REMINDER_MAX_ATTEMPTS, reminder_backoff
from booking_bot.texts import format_when


@dataclass(frozen=True)
class ClaimedReminder:
    id: int
    attempts: int
    telegram_id: int
    text: str


async def reclaim_expired(session: AsyncSession, now: datetime) -> None:
    """Move stale processing rows back to pending, or to failed after the last attempt."""

    cutoff = now - REMINDER_LEASE
    stale = (
        Reminder.status == ReminderStatus.PROCESSING,
        Reminder.processing_started_at.is_not(None),
        Reminder.processing_started_at < cutoff,
    )
    await session.execute(
        update(Reminder)
        .where(*stale, Reminder.attempts >= REMINDER_MAX_ATTEMPTS)
        .values(
            status=ReminderStatus.FAILED,
            last_error=func.coalesce(Reminder.last_error, "processing lease expired"),
        )
    )
    await session.execute(
        update(Reminder)
        .where(*stale, Reminder.attempts < REMINDER_MAX_ATTEMPTS)
        .values(
            status=ReminderStatus.PENDING,
            next_attempt_at=now,
            last_error=func.coalesce(Reminder.last_error, "processing lease expired"),
        )
    )


async def complete_past_bookings(session: AsyncSession, now: datetime) -> None:
    await session.execute(
        update(Booking)
        .where(Booking.status == BookingStatus.CONFIRMED, Booking.ends_at <= now)
        .values(status=BookingStatus.COMPLETED)
    )


async def claim_due(session: AsyncSession, now: datetime, settings: Settings) -> ClaimedReminder | None:  # noqa: E501
    """Commit the caller after this returns. The Telegram call happens outside the transaction."""

    reminder = await session.scalar(
        select(Reminder)
        .where(
            Reminder.status == ReminderStatus.PENDING,
            Reminder.next_attempt_at <= now,
        )
        .order_by(Reminder.next_attempt_at, Reminder.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if reminder is None:
        return None
    booking = await session.get(Booking, reminder.booking_id)
    if booking is None or booking.status != BookingStatus.CONFIRMED:
        reminder.status = ReminderStatus.CANCELLED
        return None
    customer = await session.get(Customer, booking.customer_id)
    if customer is None:
        reminder.status = ReminderStatus.FAILED
        reminder.last_error = "customer missing"
        return None
    reminder.status = ReminderStatus.PROCESSING
    reminder.processing_started_at = now
    reminder.attempts += 1
    local = booking.starts_at.astimezone(ZoneInfo(settings.timezone))
    text = (
        "🔔 Нагадування від Barber Studio.\n"
        f"{booking.service_name}, майстер {booking.master_name}\n"
        f"{format_when(local)}"
    )
    return ClaimedReminder(reminder.id, reminder.attempts, customer.telegram_id, text)


async def mark_sent(session: AsyncSession, reminder_id: int, now: datetime) -> bool:
    result = await session.execute(
        update(Reminder)
        .where(Reminder.id == reminder_id, Reminder.status == ReminderStatus.PROCESSING)
        .values(status=ReminderStatus.SENT, sent_at=now, last_error=None)
    )
    return result.rowcount == 1


async def mark_failed(session: AsyncSession, reminder_id: int, attempts: int, error: str, now: datetime) -> None:  # noqa: E501
    if attempts >= REMINDER_MAX_ATTEMPTS:
        values: dict = {"status": ReminderStatus.FAILED, "last_error": error[:500]}
    else:
        values = {
            "status": ReminderStatus.PENDING,
            "next_attempt_at": now + reminder_backoff(attempts),
            "last_error": error[:500],
        }
    await session.execute(
        update(Reminder)
        .where(Reminder.id == reminder_id, Reminder.status == ReminderStatus.PROCESSING)
        .values(**values)
    )


async def process_one(
    factory: async_sessionmaker[AsyncSession],
    send,
    settings: Settings,
    now: datetime,
) -> bool:
    """Claim and commit, send outside the transaction, then store the result."""

    async with factory() as session:
        async with session.begin():
            await reclaim_expired(session, now)
            await complete_past_bookings(session, now)
            claimed = await claim_due(session, now, settings)
    if claimed is None:
        return False
    try:
        await send(claimed.telegram_id, claimed.text)
    except Exception as exc:
        async with factory() as session:
            async with session.begin():
                await mark_failed(session, claimed.id, claimed.attempts, str(exc), now)
        return True
    async with factory() as session:
        async with session.begin():
            await mark_sent(session, claimed.id, now)
    return True


async def run_reminder_loop(factory, send, settings: Settings, stop) -> None:
    import asyncio

    while not stop.is_set():
        try:
            worked = True
            while worked and not stop.is_set():
                worked = await process_one(factory, send, settings, datetime.now(tz=ZoneInfo("UTC")))  # noqa: E501
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.reminder_poll_seconds)
            except TimeoutError:
                continue
        except Exception as exc:
            import logging

            logging.getLogger(__name__).error("Reminder loop failed: %s", type(exc).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.reminder_poll_seconds)
            except TimeoutError:
                continue
