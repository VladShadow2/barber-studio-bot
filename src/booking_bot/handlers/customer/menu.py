from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from booking_bot.config import load_settings
from booking_bot.db.models import Booking, BookingStatus, BusinessHours, Customer, Master, Service
from booking_bot.db.session import get_sessionmaker
from booking_bot.handlers.admin.panel import admin_home
from booking_bot.handlers.customer.booking import begin_booking
from booking_bot.keyboards.booking import (
    booking_detail_keyboard,
    booking_list_keyboard,
    confirm_cancel_keyboard,
)
from booking_bot.keyboards.start import CB_ABOUT, CB_BOOKINGS, CB_SERVICES, main_menu_markup
from booking_bot.services.booking import cancel_booking, cancellation_notices, deliver
from booking_bot.services.policies import cancellation_decision
from booking_bot.texts import (
    ABOUT_INTRO,
    ALREADY_CANCELLED,
    BTN_ABOUT,
    BTN_ADMIN,
    BTN_BOOK,
    BTN_BOOKINGS,
    BTN_SCHEDULE,
    BTN_SERVICES,
    CANCEL_PROMPT,
    CANCELLED,
    CLOSED,
    DEMO_BREAK,
    NO_BOOKINGS,
    NO_SERVICES,
    NOT_AVAILABLE,
    OPENING_HOURS,
    OUR_SERVICES,
    START,
    TOO_LATE,
    UPCOMING,
    USE_BUTTONS,
    WEEKDAYS,
    format_when,
    status_label,
)


def register_menu(router: Router) -> None:
    router.message(CommandStart())(command_start)
    router.message(F.text == BTN_BOOK)(begin_booking)
    router.message(F.text == BTN_SERVICES)(show_services_message)
    router.message(F.text == BTN_ABOUT)(show_about_message)
    router.message(F.text == BTN_BOOKINGS)(show_bookings_message)
    router.message(F.text == BTN_ADMIN)(admin_home)
    router.message(F.text == BTN_SCHEDULE)(show_schedule_message)
    router.callback_query(F.data == CB_SERVICES)(show_services)
    router.callback_query(F.data == CB_ABOUT)(show_about)
    router.callback_query(F.data == CB_BOOKINGS)(show_my_bookings)
    router.callback_query(F.data.regexp(r"^me:\d+$"))(show_booking)
    router.callback_query(F.data.regexp(r"^me:x:\d+$"))(ask_cancel)
    router.callback_query(F.data.regexp(r"^me:yes:\d+$"))(do_cancel)


def register_fallback(router: Router) -> None:
    router.message(F.text.startswith("/"))(unknown_command)
    router.message(F.text)(use_buttons)


async def command_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id if message.from_user is not None else None
    await message.answer(START, reply_markup=await main_menu_markup(user_id))


async def _answer(callback: CallbackQuery, text: str, markup=None) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(text, reply_markup=markup)


def _money(amount: Decimal, currency: str) -> str:
    value = Decimal(amount).quantize(Decimal("0.01"))
    if value == value.to_integral():
        return f"{int(value)} {currency}"
    return f"{value} {currency}"


async def _services_text() -> str:
    settings = load_settings()
    async with get_sessionmaker()() as session:
        rows = list(
            await session.scalars(select(Service).where(Service.is_active.is_(True)).order_by(Service.name))  # noqa: E501
        )
    if not rows:
        return NO_SERVICES
    lines = [OUR_SERVICES]
    for row in rows:
        price = _money(row.price_uah, settings.currency)
        lines.append(f"\n{row.name} — {row.duration_minutes} хв — {price}\n{row.description}")
    return "\n".join(lines)


async def show_services_message(message: Message) -> None:
    await message.answer(await _services_text())


async def show_services(callback: CallbackQuery) -> None:
    await _answer(callback, await _services_text())


async def _about_text() -> str:
    settings = load_settings()
    async with get_sessionmaker()() as session:
        hours = list(await session.scalars(select(BusinessHours).order_by(BusinessHours.weekday)))
    lines = [ABOUT_INTRO, OPENING_HOURS]
    by_day = {row.weekday: row for row in hours}
    for weekday, name in enumerate(WEEKDAYS):
        row = by_day.get(weekday)
        if row is None or row.is_closed or row.open_time is None or row.close_time is None:
            lines.append(f"{name}: {CLOSED}")
        else:
            lines.append(
                f"{name}: {row.open_time.strftime('%H:%M')}–{row.close_time.strftime('%H:%M')}"
            )
    lines.append(DEMO_BREAK)
    lines.append(f"Час показано для поясу {settings.timezone}.")
    return "\n".join(lines)


async def show_about_message(message: Message) -> None:
    await message.answer(await _about_text())


async def show_about(callback: CallbackQuery) -> None:
    await _answer(callback, await _about_text())


async def _owned_upcoming(telegram_id: int) -> list[Booking]:
    now = datetime.now(ZoneInfo("UTC"))
    async with get_sessionmaker()() as session:
        rows = await session.scalars(
            select(Booking)
            .join(Customer)
            .where(
                Customer.telegram_id == telegram_id,
                Booking.status.in_((BookingStatus.PENDING, BookingStatus.CONFIRMED)),
                Booking.starts_at >= now,
            )
            .order_by(Booking.starts_at)
        )
        bookings = list(rows)
    return bookings


def _when(booking: Booking, timezone_name: str) -> str:
    return format_when(booking.starts_at.astimezone(ZoneInfo(timezone_name)))


async def _bookings_view(telegram_id: int) -> tuple[str, object | None]:
    settings = load_settings()
    bookings = await _owned_upcoming(telegram_id)
    if not bookings:
        return NO_BOOKINGS, None
    labels = [
        (booking.id, f"{_when(booking, settings.timezone)} · {booking.service_name}") for booking in bookings  # noqa: E501
    ]
    return UPCOMING, booking_list_keyboard(labels)


async def show_bookings_message(message: Message) -> None:
    if message.from_user is None:
        return
    text, markup = await _bookings_view(message.from_user.id)
    await message.answer(text, reply_markup=markup)


async def show_my_bookings(callback: CallbackQuery) -> None:
    if callback.from_user is None:
        await callback.answer()
        return
    text, markup = await _bookings_view(callback.from_user.id)
    await _answer(callback, text, markup)


async def _load_owned(telegram_id: int, booking_id: int) -> Booking | None:
    async with get_sessionmaker()() as session:
        booking = await session.scalar(
            select(Booking).join(Customer).where(Booking.id ==
                                                 booking_id, Customer.telegram_id == telegram_id)
        )
        return booking


def _detail_text(booking: Booking, timezone_name: str, currency: str) -> str:
    note = f"\nПримітка: {booking.customer_note}" if booking.customer_note else ""
    return (
        f"{booking.service_name}, майстер {booking.master_name}\n"
        f"{_when(booking, timezone_name)}\n"
        f"{booking.service_duration_minutes} хв · {_money(booking.service_price_uah, currency)}\n"
        f"Статус: {status_label(booking.status)}"
        f"{note}"
    )


async def show_booking(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.data is None:
        await callback.answer()
        return
    settings = load_settings()
    booking = await _load_owned(callback.from_user.id, int(callback.data.split(":")[1]))
    if booking is None:
        await _answer(callback, NOT_AVAILABLE)
        return
    now = datetime.now(ZoneInfo("UTC"))
    can_cancel = (
        cancellation_decision(
            status=booking.status,
            starts_at=booking.starts_at,
            now=now,
            cancel_before_hours=settings.cancel_before_hours,
            by_admin=False,
        )
        == "cancelled"
    )
    await _answer(
        callback,
        _detail_text(booking, settings.timezone, settings.currency),
        booking_detail_keyboard(booking.id, can_cancel),
    )


async def ask_cancel(callback: CallbackQuery) -> None:
    if callback.data is None:
        await callback.answer()
        return
    booking_id = int(callback.data.split(":")[2])
    await _answer(callback, CANCEL_PROMPT, confirm_cancel_keyboard(booking_id))


async def do_cancel(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.data is None:
        await callback.answer()
        return
    settings = load_settings()
    booking_id = int(callback.data.split(":")[2])
    async with get_sessionmaker()() as session:
        async with session.begin():
            outcome = await cancel_booking(
                session,
                booking_id=booking_id,
                telegram_id=callback.from_user.id,
                by_admin=False,
                settings=settings,
                now=datetime.now(ZoneInfo("UTC")),
            )
    text = {
        "cancelled": CANCELLED,
        "already": ALREADY_CANCELLED,
        "too_late": TOO_LATE,
        "forbidden": NOT_AVAILABLE,
        "missing": NOT_AVAILABLE,
    }[outcome]
    notices: tuple = ()
    if outcome == "cancelled":
        async with get_sessionmaker()() as session:
            notices = await cancellation_notices(
                session,
                booking_id,
                callback.from_user.id,
                settings,
            )
    await _answer(callback, text, await main_menu_markup(callback.from_user.id))
    await deliver(callback.bot, notices)


async def show_schedule_message(message: Message) -> None:
    if message.from_user is None:
        return
    settings = load_settings()
    now = datetime.now(ZoneInfo("UTC"))
    async with get_sessionmaker()() as session:
        master = await session.scalar(
            select(Master).where(Master.telegram_user_id == message.from_user.id)
        )
        if master is None:
            await message.answer(NOT_AVAILABLE)
            return
        bookings = list(
            await session.scalars(
                select(Booking)
                .where(
                    Booking.master_id == master.id,
                    Booking.status.in_((BookingStatus.PENDING, BookingStatus.CONFIRMED)),
                    Booking.starts_at >= now,
                )
                .order_by(Booking.starts_at)
            )
        )
        customers = {}
        if bookings:
            rows = await session.scalars(
                select(Customer).where(Customer.id.in_([row.customer_id for row in bookings]))
            )
            customers = {row.id: row for row in rows}
    if not bookings:
        await message.answer("📅 У вас немає майбутніх записів.")
        return
    lines = [f"📅 Мій розклад — {master.name}"]
    for booking in bookings:
        customer = customers.get(booking.customer_id)
        name = customer.display_name if customer is not None else "Клієнт"
        username = f" (@{customer.username})" if customer is not None and customer.username else ""
        note = f"\n📝 {booking.customer_note}" if booking.customer_note else ""
        lines.append(
            f"\n{format_when(booking.starts_at.astimezone(ZoneInfo(settings.timezone)))}\n"
            f"💈 {booking.service_name}\n"
            f"👤 {name}{username}{note}"
        )
    await message.answer("\n".join(lines))


async def unknown_command(message: Message) -> None:
    await message.answer(NOT_AVAILABLE)


async def use_buttons(message: Message) -> None:
    await message.answer(USE_BUTTONS)
