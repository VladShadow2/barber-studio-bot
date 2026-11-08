import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from booking_bot.config import Settings, parse_reminder_hours
from booking_bot.db.models import (
    Booking,
    BookingDraft,
    BookingStatus,
    Customer,
    Master,
    Reminder,
    ReminderStatus,
    Service,
)
from booking_bot.repositories.schedule import load_calendar, masters_for_service
from booking_bot.services.availability import free_master_ids
from booking_bot.services.policies import cancellation_decision
from booking_bot.texts import format_when


@dataclass(frozen=True)
class Outgoing:
    telegram_id: int
    text: str


class SlotTaken(Exception):
    """Two bookings hit the same master at the same time."""


@dataclass(frozen=True)
class ConfirmResult:
    code: str
    booking: Booking | None = None
    messages: tuple[Outgoing, ...] = ()


def master_needs_separate_notice(
    master_telegram_id: int | None,
    customer_telegram_id: int,
    admin_ids: tuple[int, ...] | list[int],
) -> bool:
    if master_telegram_id is None or master_telegram_id == customer_telegram_id:
        return False
    return master_telegram_id not in admin_ids


async def deliver(bot, messages: tuple[Outgoing, ...]) -> None:
    """Send notices after the database commit. A blocked chat does not undo the booking."""

    log = logging.getLogger(__name__)
    for item in messages:
        try:
            await bot.send_message(item.telegram_id, item.text)
        except TelegramAPIError:
            log.warning("Notification was not delivered to telegram id %s", item.telegram_id)


def _is_overlap(exc: IntegrityError) -> bool:
    detail = str(getattr(exc, "orig", exc)).lower()
    return "bookings_no_overlap" in detail or "exclusion" in detail or "conflicting key" in detail


async def upsert_customer(
    session: AsyncSession,
    telegram_id: int,
    display_name: str,
    username: str | None,
) -> Customer:
    customer = await session.scalar(select(Customer).where(Customer.telegram_id == telegram_id))
    if customer is None:
        customer = Customer(telegram_id=telegram_id, display_name=display_name, username=username)
        session.add(customer)
        await session.flush()
        return customer
    customer.display_name = display_name
    customer.username = username
    return customer


async def confirm_draft(
    session: AsyncSession,
    *,
    telegram_id: int,
    display_name: str,
    username: str | None,
    settings: Settings,
    now: datetime,
    admin_ids: tuple[int, ...],
) -> ConfirmResult:
    """Re-check the slot, insert one booking, and store reminder rows in this transaction."""

    draft = await session.get(BookingDraft, telegram_id, with_for_update=True)
    if (
        draft is None
        or draft.expires_at <= now
        or draft.service_id is None
        or draft.starts_at is None
    ):
        return ConfirmResult("expired")
    service = await session.get(Service, draft.service_id)
    if service is None or not service.is_active:
        return ConfirmResult("unavailable")
    if not draft.any_master and draft.master_id is None:
        return ConfirmResult("unavailable")
    masters = await masters_for_service(session, service.id)
    candidate_ids = [draft.master_id] if not draft.any_master else [master.id for master in masters]
    if not draft.any_master and draft.master_id not in {master.id for master in masters}:
        return ConfirmResult("unavailable")
    locked = list(
        await session.scalars(
            select(Master)
            .where(Master.id.in_(candidate_ids), Master.is_active.is_(True))
            .order_by(Master.id)
            .with_for_update()
        )
    )
    tz = ZoneInfo(settings.timezone)
    horizon_end = now + timedelta(days=settings.booking_days_ahead + 1)
    calendar = await load_calendar(session, now - timedelta(days=1), horizon_end)
    free_ids = free_master_ids(
        calendar,
        [master.id for master in locked],
        draft.starts_at,
        service.duration_minutes,
        settings.slot_minutes,
        tz,
        now,
        settings.min_lead_minutes,
    )
    if not free_ids:
        return ConfirmResult("taken")
    chosen = next(master for master in locked if master.id == free_ids[0])
    customer = await upsert_customer(session, telegram_id, display_name, username)
    ends_at = draft.starts_at + timedelta(minutes=service.duration_minutes)
    booking = Booking(
        customer_id=customer.id,
        master_id=chosen.id,
        service_id=service.id,
        service_name=service.name,
        service_duration_minutes=service.duration_minutes,
        service_price_uah=service.price_uah,
        master_name=chosen.name,
        starts_at=draft.starts_at,
        ends_at=ends_at,
        status=BookingStatus.CONFIRMED,
        customer_note=draft.customer_note,
    )
    session.add(booking)
    try:
        await session.flush()
    except IntegrityError as exc:
        if _is_overlap(exc):
            raise SlotTaken from exc
        raise
    await _insert_reminders(session, booking, settings, now)
    await session.delete(draft)
    when = format_when(booking.starts_at.astimezone(tz))
    note = f"\nПримітка: {booking.customer_note}" if booking.customer_note else ""
    customer_text = (
        "✅ Ви записані в Barber Studio.\n"
        f"{booking.service_name}, майстер {booking.master_name}\n"
        f"{when}\n"
        f"{booking.service_duration_minutes} хв · {booking.service_price_uah} {settings.currency}"
        f"{note}"
    )
    admin_text = (
        "📅 Новий запис у Barber Studio.\n"
        f"{display_name}"
        + (f" (@{username})" if username else "")
        + f"\n{booking.service_name}, майстер {booking.master_name}\n{when}{note}"
    )
    master_text = (
        "📅 Новий запис до вас.\n"
        f"{display_name}"
        + (f" (@{username})" if username else "")
        + f"\n{booking.service_name}\n{when}{note}"
    )
    messages = [Outgoing(telegram_id, customer_text)]
    messages.extend(
        Outgoing(admin_id, admin_text) for admin_id in admin_ids if admin_id != telegram_id
    )
    if master_needs_separate_notice(chosen.telegram_user_id, telegram_id, admin_ids):
        messages.append(Outgoing(chosen.telegram_user_id, master_text))
    return ConfirmResult("confirmed", booking, tuple(messages))


async def _insert_reminders(
    session: AsyncSession,
    booking: Booking,
    settings: Settings,
    now: datetime,
) -> None:
    for hours in parse_reminder_hours(settings.reminder_hours):
        remind_at = booking.starts_at - timedelta(hours=hours)
        if remind_at <= now:
            continue
        statement = (
            pg_insert(Reminder)
            .values(
                booking_id=booking.id,
                remind_at=remind_at,
                next_attempt_at=remind_at,
                status=ReminderStatus.PENDING,
                attempts=0,
            )
            .on_conflict_do_nothing(constraint="uq_reminder_booking_time")
        )
        await session.execute(statement)


async def cancel_booking(
    session: AsyncSession,
    *,
    booking_id: int,
    telegram_id: int | None,
    by_admin: bool,
    settings: Settings,
    now: datetime,
) -> str:
    booking = await session.scalar(select(Booking).where(Booking.id == booking_id).with_for_update())  # noqa: E501
    if booking is None:
        return "missing"
    if not by_admin:
        customer = await session.get(Customer, booking.customer_id)
        if customer is None or customer.telegram_id != telegram_id:
            return "forbidden"
    decision = cancellation_decision(
        status=booking.status,
        starts_at=booking.starts_at,
        now=now,
        cancel_before_hours=settings.cancel_before_hours,
        by_admin=by_admin,
    )
    if decision != "cancelled":
        return decision
    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = now
    await session.execute(
        update(Reminder)
        .where(
            Reminder.booking_id == booking.id,
            Reminder.status.in_((ReminderStatus.PENDING, ReminderStatus.PROCESSING)),
        )
        .values(status=ReminderStatus.CANCELLED)
    )
    return "cancelled"


async def cancellation_notices(
    session: AsyncSession,
    booking_id: int,
    actor_id: int | None,
    settings: Settings,
) -> tuple[Outgoing, ...]:
    booking = await session.get(Booking, booking_id)
    if booking is None:
        return ()
    master = await session.get(Master, booking.master_id)
    if master is None or master.telegram_user_id is None or master.telegram_user_id == actor_id:
        return ()
    customer = await session.get(Customer, booking.customer_id)
    name = customer.display_name if customer is not None else "Клієнт"
    username = f" (@{customer.username})" if customer is not None and customer.username else ""
    when = format_when(booking.starts_at.astimezone(ZoneInfo(settings.timezone)))
    text = (
        "❌ Запис скасовано.\n"
        f"{booking.service_name}\n{when}\n"
        f"👤 {name}{username}"
    )
    return (Outgoing(master.telegram_user_id, text),)
