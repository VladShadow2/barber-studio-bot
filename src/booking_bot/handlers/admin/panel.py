from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from booking_bot.config import load_settings, parse_admin_ids
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
from booking_bot.db.session import get_sessionmaker
from booking_bot.keyboards.admin import (
    admin_home_keyboard,
    master_services_keyboard,
    rows_keyboard,
    weekday_keyboard,
    yes_no_keyboard,
)
from booking_bot.keyboards.start import NAV_TEXTS
from booking_bot.services.admin_edit import (
    add_break,
    add_exception,
    assigned_service_ids,
    booking_stats,
    break_overlaps_booking,
    link_master_services,
    list_services,
    parse_break,
    parse_exception,
    parse_hours,
    parse_master_line,
    parse_price,
    parse_service_line,
    upsert_hours,
    upsert_master_hours,
)
from booking_bot.services.booking import cancel_booking, cancellation_notices, deliver
from booking_bot.texts import (
    ADMIN_HOME,
    ASSIGN_SERVICES,
    BTN_ADMIN,
    NO_SERVICES_TO_ASSIGN,
    NOT_AVAILABLE,
    SERVICES_DISCARDED,
    SERVICES_SAVED,
    WEEKDAY_SHORT,
    current_hours_text,
    format_day,
    format_when,
    saved_hours_text,
    status_label,
)


class AdminFlow(StatesGroup):
    text = State()
    confirm = State()
    links = State()


def create_admin_router() -> Router:
    router = Router(name="admin")
    router.message(Command("admin"))(admin_home)
    router.message(Command("cancel"))(cancel_editing)
    router.callback_query(F.data.startswith("adm:"))(admin_callback)
    router.message(AdminFlow.text, F.text)(admin_text)
    router.message(AdminFlow.confirm, F.text)(admin_confirm_text)
    return router


def _is_admin(user_id: int) -> bool:
    return user_id in parse_admin_ids(load_settings().admin_ids)


async def _send(target, text: str, markup=None) -> None:
    if isinstance(target, CallbackQuery):
        await target.answer()
        if isinstance(target.message, Message):
            await target.message.answer(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


async def admin_home(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id):
        await message.answer(NOT_AVAILABLE)
        return
    await state.clear()
    await message.answer(ADMIN_HOME, reply_markup=admin_home_keyboard())


async def cancel_editing(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    await state.clear()
    from booking_bot.handlers.master.schedule import MasterFlow

    editing = current in {
        AdminFlow.text.state,
        AdminFlow.confirm.state,
        AdminFlow.links.state,
        MasterFlow.text.state,
        MasterFlow.confirm.state,
    }
    if editing:
        await message.answer("❌ Редагування скасовано. Зміни не збережено.")
    else:
        await message.answer("Активного редагування немає.")
    if message.from_user is not None and _is_admin(message.from_user.id):
        await message.answer(ADMIN_HOME, reply_markup=admin_home_keyboard())


async def follow_navigation(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = message.text or ""
    if text == BTN_ADMIN:
        await admin_home(message, state)
        return
    from booking_bot.handlers.customer.booking import begin_booking
    from booking_bot.handlers.customer.menu import (
        show_about_message,
        show_bookings_message,
        show_schedule_message,
        show_services_message,
    )
    from booking_bot.texts import (
        BTN_ABOUT,
        BTN_BOOK,
        BTN_BOOKINGS,
        BTN_HOURS,
        BTN_SCHEDULE,
        BTN_SERVICES,
    )

    if text == BTN_BOOK:
        await begin_booking(message, state)
    elif text == BTN_SERVICES:
        await show_services_message(message)
    elif text == BTN_ABOUT:
        await show_about_message(message)
    elif text == BTN_BOOKINGS:
        await show_bookings_message(message)
    elif text == BTN_SCHEDULE:
        await show_schedule_message(message)
    elif text == BTN_HOURS:
        from booking_bot.handlers.master.schedule import open_my_hours

        await open_my_hours(message, state)


async def admin_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.data is None or not _is_admin(callback.from_user.id):
        await _send(callback, NOT_AVAILABLE)
        return
    data = callback.data
    settings = load_settings()
    now = datetime.now(UTC)
    tz = ZoneInfo(settings.timezone)
    if data == "adm:home":
        await state.clear()
        await _send(callback, ADMIN_HOME, admin_home_keyboard())
    elif data == "adm:up":
        await _upcoming(callback, now, tz)
    elif data == "adm:dates":
        days = [now.astimezone(tz).date() + timedelta(days=offset) for offset in range(7)]
        rows = [(format_day(day), f"adm:d:{day.strftime('%Y%m%d')}") for day in days]
        await _send(callback, "🗓️ Оберіть дату.", rows_keyboard(rows))
    elif data.startswith("adm:d:"):
        await _by_date(callback, data.split(":")[2], tz)
    elif data.startswith("adm:b:"):
        await _booking_detail(callback, int(data.split(":")[2]), tz, settings.currency)
    elif data.startswith("adm:bx:"):
        await state.set_state(AdminFlow.confirm)
        await state.update_data(kind="booking_cancel", booking_id=int(data.split(":")[2]))
        await _send(callback, "Скасувати цей запис?", yes_no_keyboard())
    elif data == "adm:svc":
        await _services(callback)
    elif data == "adm:svc:add":
        await state.set_state(AdminFlow.text)
        await state.update_data(kind="service_add")
        await _send(callback, "Надішліть: Назва | хвилини | ціна | опис")
    elif data.startswith("adm:s:"):
        await _service_menu(callback, int(data.split(":")[2]))
    elif data.startswith("adm:sp:"):
        await state.set_state(AdminFlow.text)
        await state.update_data(kind="service_price", service_id=int(data.split(":")[2]))
        await _send(callback, "Надішліть нову ціну в гривнях.")
    elif data.startswith("adm:sd:"):
        await state.set_state(AdminFlow.text)
        await state.update_data(kind="service_duration", service_id=int(data.split(":")[2]))
        await _send(callback, "Надішліть нову тривалість у хвилинах.")
    elif data.startswith("adm:st:"):
        await state.set_state(AdminFlow.confirm)
        await state.update_data(kind="service_toggle", service_id=int(data.split(":")[2]))
        await _send(callback, "Змінити, чи активна ця послуга?", yes_no_keyboard())
    elif data == "adm:mst":
        await _masters(callback)
    elif data == "adm:mst:add":
        await state.set_state(AdminFlow.text)
        await state.update_data(kind="master_add")
        await _send(callback, "Надішліть: Ім’я | опис")
    elif data.startswith("adm:m:"):
        await _master_menu(callback, int(data.split(":")[2]))
    elif data.startswith("adm:mt:"):
        await state.set_state(AdminFlow.confirm)
        await state.update_data(kind="master_toggle", master_id=int(data.split(":")[2]))
        await _send(callback, "Змінити, чи активний цей майстер?", yes_no_keyboard())
    elif data.startswith("adm:mu:"):
        master_id = _positive_id(data, 2)
        if master_id is None:
            await _send(callback, NOT_AVAILABLE)
            return
        await state.set_state(AdminFlow.text)
        await state.update_data(kind="master_telegram", master_id=master_id)
        await _send(callback, "Надішліть Telegram ID майстра або напишіть «очистити».")
    elif data == "adm:mly":
        await _save_master_services(callback, state)
    elif data == "adm:mln":
        await _cancel_master_services(callback, state)
    elif data.startswith("adm:mls:"):
        await _toggle_master_service(callback, state, data)
    elif data.startswith("adm:ml:"):
        await _open_master_services(callback, state, data)
    elif data.startswith("adm:mh:"):
        prefix = f"adm:mhw:{data.split(':')[2]}"
        await _send(callback, "Оберіть день тижня.", weekday_keyboard(prefix))
    elif data.startswith("adm:mhw:"):
        await _ask_master_hours(callback, state, data)
    elif data == "adm:hours":
        await _send(callback, "Оберіть день тижня.", weekday_keyboard("adm:hw"))
    elif data.startswith("adm:hw:"):
        await _ask_salon_hours(callback, state, data)
    elif data == "adm:cal":
        await _calendar(callback)
    elif data == "adm:br:add":
        await state.set_state(AdminFlow.text)
        await state.update_data(kind="break_add", master_id=None)
        await _send(callback, "Надішліть перерву салону, наприклад: Пн 14:00-15:00")
    elif data == "adm:ex:add":
        await state.set_state(AdminFlow.text)
        await state.update_data(kind="exception_add", master_id=None)
        await _send(callback, "Надішліть виняток на дату, наприклад: 2030-01-02 закрито")
    elif data.startswith("adm:brx:"):
        await _ask_delete_break(callback, state, data)
    elif data == "adm:stats":
        await _stats(callback, now, tz)
    elif data == "adm:yes":
        await _apply(callback, state, settings)
    elif data == "adm:no":
        await state.clear()
        await _send(callback, ADMIN_HOME, admin_home_keyboard())
    else:
        await _send(callback, NOT_AVAILABLE)


async def admin_text(message: Message, state: FSMContext) -> None:
    if message.from_user is None or message.text is None or not _is_admin(message.from_user.id):
        await message.answer(NOT_AVAILABLE)
        return
    if message.text in NAV_TEXTS:
        await follow_navigation(message, state)
        return
    data = await state.get_data()
    kind = data.get("kind")
    ok = _valid_text(kind, message.text)
    if not ok:
        await message.answer(
            "Це значення не підходить. Надішліть його знову або /admin, щоб зупинитись."
        )
        return
    await state.update_data(payload=message.text.strip())
    await state.set_state(AdminFlow.confirm)
    warning = ""
    if kind == "break_add":
        warning = await _break_warning(message.text, data.get("master_id"))
    await message.answer(
        f"Зберегти цю зміну?\n{message.text.strip()}{warning}",
        reply_markup=yes_no_keyboard(),
    )


async def admin_confirm_text(message: Message, state: FSMContext) -> None:
    if message.from_user is None or message.text is None or not _is_admin(message.from_user.id):
        await message.answer(NOT_AVAILABLE)
        return
    if message.text in NAV_TEXTS or message.text == "/cancel":
        await follow_navigation(message, state) if message.text in NAV_TEXTS else await cancel_editing(message, state)  # noqa: E501
        return
    await message.answer("Оберіть «Так, зберегти» або «Ні». Зміну ще не збережено.")


def _valid_text(kind: str | None, raw: str) -> bool:
    if kind == "service_add":
        return parse_service_line(raw) is not None
    if kind == "service_price":
        return parse_price(raw) is not None
    if kind == "service_duration":
        return raw.strip().isdigit() and int(raw.strip()) > 0
    if kind == "master_add":
        return parse_master_line(raw) is not None
    if kind == "master_telegram":
        text = raw.strip().casefold()
        return text == "очистити" or (text.isdigit() and text != "0")
    if kind in {"salon_hours", "master_hours"}:
        return parse_hours(raw) is not None
    if kind == "break_add":
        return parse_break(raw) is not None
    if kind == "exception_add":
        return parse_exception(raw) is not None
    return False


async def _apply(callback: CallbackQuery, state: FSMContext, settings) -> None:
    if callback.from_user is None or not _is_admin(callback.from_user.id):
        await _send(callback, NOT_AVAILABLE)
        return
    data = await state.get_data()
    kind = data.get("kind")
    notices: tuple = ()
    async with get_sessionmaker()() as session:
        async with session.begin():
            if kind == "booking_cancel":
                outcome = await cancel_booking(
                    session,
                    booking_id=int(data["booking_id"]),
                    telegram_id=None,
                    by_admin=True,
                    settings=settings,
                    now=datetime.now(UTC),
                )
                if outcome == "cancelled":
                    notices = await cancellation_notices(
                        session,
                        int(data["booking_id"]),
                        callback.from_user.id,
                        settings,
                    )
                text = (
                    "❌ Запис скасовано." if outcome == "cancelled" else "Цей запис не скасовано."
                )
            elif kind == "service_add":
                name, duration, price, description = parse_service_line(data["payload"])
                session.add(
                    Service(
                        name=name,
                        description=description,
                        duration_minutes=duration,
                        price_uah=price,
                        is_active=True,
                    )
                )
                text = "Послугу збережено. Старі записи лишають попередню ціну."
            elif kind == "service_price":
                service = await session.get(Service, int(data["service_id"]))
                service.price_uah = parse_price(data["payload"])
                text = "Ціну збережено. Минулі записи не змінено."
            elif kind == "service_duration":
                service = await session.get(Service, int(data["service_id"]))
                service.duration_minutes = int(data["payload"])
                text = "Тривалість збережено. Минулі записи не змінено."
            elif kind == "service_toggle":
                service = await session.get(Service, int(data["service_id"]))
                service.is_active = not service.is_active
                text = "Активна." if service.is_active else "Неактивна."
            elif kind == "master_add":
                name, description = parse_master_line(data["payload"])
                session.add(Master(name=name, description=description, is_active=True))
                text = "Майстра збережено."
            elif kind == "master_telegram":
                master = await session.get(Master, int(data["master_id"]))
                if master is None:
                    text = NOT_AVAILABLE
                else:
                    raw = data["payload"].strip().casefold()
                    new_id = None if raw == "очистити" else int(raw)
                    taken = None
                    if new_id is not None:
                        taken = await session.scalar(
                            select(Master).where(
                                Master.telegram_user_id == new_id,
                                Master.id != master.id,
                            )
                        )
                    if taken is not None:
                        text = "Цей Telegram ID вже прив’язаний до іншого майстра."
                    else:
                        master.telegram_user_id = new_id
                        text = "🔗 Telegram майстра оновлено."
            elif kind == "master_toggle":
                master = await session.get(Master, int(data["master_id"]))
                master.is_active = not master.is_active
                text = "Активний." if master.is_active else "Неактивний."
            elif kind == "salon_hours":
                closed, open_time, close_time = parse_hours(data["payload"])
                weekday = int(data["weekday"])
                await upsert_hours(session, weekday, closed, open_time, close_time)
                text = saved_hours_text(weekday, closed, open_time, close_time, master=False)
            elif kind == "master_hours":
                closed, open_time, close_time = parse_hours(data["payload"])
                weekday = int(data["weekday"])
                await upsert_master_hours(
                    session, int(data["master_id"]), weekday, closed, open_time, close_time
                )
                text = saved_hours_text(weekday, closed, open_time, close_time, master=True)
            elif kind == "break_add":
                weekday, start, end = parse_break(data["payload"])
                await add_break(session, data.get("master_id"), weekday, start, end)
                text = "☕ Перерву збережено. Існуючі записи не скасовано."
            elif kind == "exception_add":
                on_date, closed, open_time, close_time = parse_exception(data["payload"])
                await add_exception(session, data.get("master_id"), on_date, closed, open_time, close_time)  # noqa: E501
                text = "Виняток збережено."
            elif kind == "break_delete":
                row = await session.get(Break, int(data["break_id"]))
                if row is not None:
                    await session.delete(row)
                text = "🗑️ Перерву видалено."
            else:
                text = NOT_AVAILABLE
    await state.clear()
    await _send(callback, text, admin_home_keyboard())
    await deliver(callback.bot, notices)


async def _upcoming(callback: CallbackQuery, now: datetime, tz: ZoneInfo) -> None:
    async with get_sessionmaker()() as session:
        rows = list(
            await session.scalars(
                select(Booking)
                .where(
                    Booking.status.in_((BookingStatus.PENDING, BookingStatus.CONFIRMED)),
                    Booking.starts_at >= now,
                )
                .order_by(Booking.starts_at)
                .limit(10)
            )
        )
    if not rows:
        await _send(callback, "Немає майбутніх записів.", admin_home_keyboard())
        return
    labels = [
        (f"{format_when(row.starts_at.astimezone(tz))} {row.service_name}", f"adm:b:{row.id}")
        for row in rows
    ]
    await _send(callback, "📅 Майбутні записи", rows_keyboard(labels))


async def _by_date(callback: CallbackQuery, token: str, tz: ZoneInfo) -> None:
    day = datetime.strptime(token, "%Y%m%d").date()
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    end = start + timedelta(days=1)
    async with get_sessionmaker()() as session:
        rows = list(
            await session.scalars(
                select(Booking).where(Booking.starts_at >= start,
                                      Booking.starts_at < end).order_by(Booking.starts_at)
            )
        )
    if not rows:
        await _send(callback, "На цю дату записів немає.", admin_home_keyboard())
        return
    labels = [
        (
            f"{row.starts_at.astimezone(tz):%H:%M} {row.service_name} ({status_label(row.status)})",
            f"adm:b:{row.id}",
        )
        for row in rows
    ]
    await _send(callback, day.isoformat(), rows_keyboard(labels))


async def _booking_detail(callback: CallbackQuery, booking_id: int, tz: ZoneInfo, currency: str) -> None:  # noqa: E501
    async with get_sessionmaker()() as session:
        booking = await session.get(Booking, booking_id)
    if booking is None:
        await _send(callback, NOT_AVAILABLE)
        return
    text = (
        f"{booking.service_name}, майстер {booking.master_name}\n"
        f"{format_when(booking.starts_at.astimezone(tz))}\n"
        f"{booking.service_duration_minutes} хв · {booking.service_price_uah} {currency}\n"
        f"Статус: {status_label(booking.status)}\n"
        f"Збережена ціна: {booking.service_price_uah} {currency}"
    )
    rows = [("Скасувати запис", f"adm:bx:{booking.id}")]
    await _send(callback, text, rows_keyboard(rows))


async def _services(callback: CallbackQuery) -> None:
    async with get_sessionmaker()() as session:
        rows = list(await session.scalars(select(Service).order_by(Service.name)))
    labels = [(f"{row.name} ({'увімк.' if row.is_active else 'вимк.'})", f"adm:s:{row.id}")
              for row in rows]
    labels.append(("Додати послугу", "adm:svc:add"))
    await _send(callback, "💈 Послуги", rows_keyboard(labels))


async def _service_menu(callback: CallbackQuery, service_id: int) -> None:
    async with get_sessionmaker()() as session:
        service = await session.get(Service, service_id)
    if service is None:
        await _send(callback, NOT_AVAILABLE)
        return
    text = (
        f"{service.name}\n{service.description}\n"
        f"{service.duration_minutes} хв · {service.price_uah}\n"
        f"Активна: {'так' if service.is_active else 'ні'}"
    )
    rows = [
        ("Змінити ціну", f"adm:sp:{service.id}"),
        ("Змінити тривалість", f"adm:sd:{service.id}"),
        ("Увімкнути або вимкнути", f"adm:st:{service.id}"),
    ]
    await _send(callback, text, rows_keyboard(rows))


async def _masters(callback: CallbackQuery) -> None:
    async with get_sessionmaker()() as session:
        rows = list(await session.scalars(select(Master).order_by(Master.name)))
    labels = [(f"{row.name} ({'увімк.' if row.is_active else 'вимк.'})", f"adm:m:{row.id}")
              for row in rows]
    labels.append(("Додати майстра", "adm:mst:add"))
    await _send(callback, "👤 Майстри", rows_keyboard(labels))


def _weekday(token: str) -> int | None:
    if not token.isdigit():
        return None
    weekday = int(token)
    if weekday > 6:
        return None
    return weekday


async def _ask_salon_hours(callback: CallbackQuery, state: FSMContext, data: str) -> None:
    parts = data.split(":")
    weekday = _weekday(parts[2]) if len(parts) == 3 else None
    if weekday is None:
        await _send(callback, NOT_AVAILABLE)
        return
    async with get_sessionmaker()() as session:
        row = await session.get(BusinessHours, weekday)
    await state.set_state(AdminFlow.text)
    await state.update_data(kind="salon_hours", weekday=weekday)
    await _send(callback, current_hours_text(weekday, row))


async def _ask_master_hours(callback: CallbackQuery, state: FSMContext, data: str) -> None:
    parts = data.split(":")
    if len(parts) != 4 or not parts[2].isdigit() or parts[2] == "0":
        await _send(callback, NOT_AVAILABLE)
        return
    weekday = _weekday(parts[3])
    if weekday is None:
        await _send(callback, NOT_AVAILABLE)
        return
    master_id = int(parts[2])
    async with get_sessionmaker()() as session:
        row = await session.get(MasterHours, (master_id, weekday))
    await state.set_state(AdminFlow.text)
    await state.update_data(kind="master_hours", master_id=master_id, weekday=weekday)
    await _send(callback, current_hours_text(weekday, row))


def _positive_id(data: str, index: int) -> int | None:
    parts = data.split(":")
    if len(parts) <= index or not parts[index].isdigit() or parts[index] == "0":
        return None
    return int(parts[index])


def _pending_ids(data: dict) -> list[int]:
    chosen: list[int] = []
    for item in data.get("service_ids") or []:
        if isinstance(item, int) and item > 0:
            chosen.append(item)
        elif isinstance(item, str) and item.isdigit() and item != "0":
            chosen.append(int(item))
    return chosen


async def _links_ready(state: FSMContext) -> dict | None:
    if await state.get_state() != AdminFlow.links.state:
        return None
    data = await state.get_data()
    master_id = data.get("master_id")
    if data.get("kind") != "master_links" or not isinstance(master_id, int) or master_id <= 0:
        return None
    return data


async def _edit(callback: CallbackQuery, text: str, markup) -> None:
    await callback.answer()
    message = callback.message
    if not isinstance(message, Message):
        return
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        await message.answer(text, reply_markup=markup)


async def _service_choices(session) -> list[tuple[int, str]]:
    return [(row.id, row.name) for row in await list_services(session)]


async def _open_master_services(callback: CallbackQuery, state: FSMContext, data: str) -> None:
    master_id = _positive_id(data, 2)
    if master_id is None:
        await _send(callback, NOT_AVAILABLE)
        return
    async with get_sessionmaker()() as session:
        master = await session.get(Master, master_id)
        services = await _service_choices(session)
        assigned = set(await assigned_service_ids(session, master_id))
    if master is None:
        await _send(callback, NOT_AVAILABLE)
        return
    if not services:
        await state.clear()
        await _send(callback, NO_SERVICES_TO_ASSIGN)
        return
    known = {service_id for service_id, _name in services}
    selected = sorted(service_id for service_id in assigned if service_id in known)
    await state.set_state(AdminFlow.links)
    await state.update_data(kind="master_links", master_id=master_id, service_ids=selected)
    await _send(callback, ASSIGN_SERVICES, master_services_keyboard(services, set(selected)))


async def _toggle_master_service(callback: CallbackQuery, state: FSMContext, data: str) -> None:
    pending = await _links_ready(state)
    service_id = _positive_id(data, 2)
    if pending is None or service_id is None:
        await _send(callback, NOT_AVAILABLE)
        return
    async with get_sessionmaker()() as session:
        services = await _service_choices(session)
    known = {row_id for row_id, _name in services}
    if service_id not in known:
        await _send(callback, NOT_AVAILABLE)
        return
    selected = set(_pending_ids(pending))
    if service_id in selected:
        selected.remove(service_id)
    else:
        selected.add(service_id)
    chosen = sorted(selected)
    await state.update_data(service_ids=chosen)
    await _edit(callback, ASSIGN_SERVICES, master_services_keyboard(services, set(chosen)))


async def _save_master_services(callback: CallbackQuery, state: FSMContext) -> None:
    pending = await _links_ready(state)
    if pending is None:
        await _send(callback, NOT_AVAILABLE)
        return
    master_id = int(pending["master_id"])
    requested = _pending_ids(pending)
    saved = False
    async with get_sessionmaker()() as session:
        async with session.begin():
            master = await session.get(Master, master_id)
            services = await _service_choices(session)
            if master is not None:
                known = {service_id for service_id, _name in services}
                await link_master_services(
                    session,
                    master_id,
                    [service_id for service_id in requested if service_id in known],
                )
                saved = True
    await state.clear()
    if not saved:
        await _send(callback, NOT_AVAILABLE)
        return
    view = await _master_menu_view(master_id)
    if view is None:
        await _send(callback, SERVICES_SAVED)
        return
    text, markup = view
    await _send(callback, f"{SERVICES_SAVED}\n\n{text}", markup)


async def _cancel_master_services(callback: CallbackQuery, state: FSMContext) -> None:
    pending = await _links_ready(state)
    if pending is None:
        await _send(callback, NOT_AVAILABLE)
        return
    master_id = int(pending["master_id"])
    await state.clear()
    view = await _master_menu_view(master_id)
    if view is None:
        await _send(callback, SERVICES_DISCARDED)
        return
    text, markup = view
    await _send(callback, f"{SERVICES_DISCARDED}\n\n{text}", markup)


async def _master_menu_view(master_id: int):
    async with get_sessionmaker()() as session:
        master = await session.get(Master, master_id)
    if master is None:
        return None
    linked = master.telegram_user_id if master.telegram_user_id is not None else "не прив’язано"
    text = (
        f"👤 {master.name}\n{master.description}\n"
        f"Активний: {'так' if master.is_active else 'ні'}\n"
        f"Telegram: {linked}"
    )
    rows = [
        ("🔘 Увімкнути або вимкнути", f"adm:mt:{master.id}"),
        ("💈 Призначити послуги", f"adm:ml:{master.id}"),
        ("🕐 Години роботи", f"adm:mh:{master.id}"),
        ("🔗 Telegram майстра", f"adm:mu:{master.id}"),
    ]
    return text, rows_keyboard(rows)


async def _master_menu(callback: CallbackQuery, master_id: int) -> None:
    view = await _master_menu_view(master_id)
    if view is None:
        await _send(callback, NOT_AVAILABLE)
        return
    text, markup = view
    await _send(callback, text, markup)


def _break_label(row: Break) -> str:
    return (
        f"{WEEKDAY_SHORT[row.weekday]} "
        f"{row.start_time.strftime('%H:%M')}–{row.end_time.strftime('%H:%M')}"
    )


async def _ask_delete_break(callback: CallbackQuery, state: FSMContext, data: str) -> None:
    break_id = _positive_id(data, 2)
    if break_id is None:
        await _send(callback, NOT_AVAILABLE)
        return
    async with get_sessionmaker()() as session:
        row = await session.get(Break, break_id)
    if row is None:
        await _send(callback, NOT_AVAILABLE)
        return
    await state.set_state(AdminFlow.confirm)
    await state.update_data(kind="break_delete", break_id=row.id)
    await _send(callback, f"Видалити перерву {_break_label(row)}?", yes_no_keyboard())


async def _break_warning(raw: str, master_id: int | None) -> str:
    parsed = parse_break(raw)
    if parsed is None:
        return ""
    weekday, start, end = parsed
    settings = load_settings()
    tz = ZoneInfo(settings.timezone)
    async with get_sessionmaker()() as session:
        bookings = list(
            await session.scalars(
                select(Booking).where(
                    Booking.status.in_((BookingStatus.PENDING, BookingStatus.CONFIRMED))
                )
            )
        )
    conflicts = [
        booking
        for booking in bookings
        if break_overlaps_booking(booking, weekday, start, end, master_id, tz)
    ]
    if not conflicts:
        return ""
    lines = [
        "",
        "⚠️ Ця перерва перетинається з існуючими записами. Бот їх не скасує.",
    ]
    for booking in conflicts[:10]:
        when = format_when(booking.starts_at.astimezone(tz))
        lines.append(f"• {when} — {booking.service_name}, {booking.master_name}")
    if len(conflicts) > 10:
        lines.append(f"• і ще {len(conflicts) - 10}")
    return "\n".join(lines)


async def _calendar(callback: CallbackQuery) -> None:
    async with get_sessionmaker()() as session:
        breaks = list(await session.scalars(select(Break).order_by(Break.weekday, Break.start_time)))  # noqa: E501
        exceptions = list(await session.scalars(select(ScheduleException).order_by(ScheduleException.on_date)))  # noqa: E501
    lines = ["☕ Перерви"]
    labels = []
    for row in breaks:
        who = "салон" if row.master_id is None else f"майстер {row.master_id}"
        label = _break_label(row)
        lines.append(f"{label} ({who})")
        labels.append((f"🗑️ {label}", f"adm:brx:{row.id}"))
    lines.append("\n📌 Винятки")
    for row in exceptions:
        who = "салон" if row.master_id is None else f"майстер {row.master_id}"
        window = "закрито" if row.is_closed else f"{row.open_time}-{row.close_time}"
        lines.append(f"{row.on_date.isoformat()} {window} ({who})")
    labels.extend(
        [("Додати перерву салону", "adm:br:add"), ("Додати виняток салону", "adm:ex:add")]
    )
    await _send(callback, "\n".join(lines), rows_keyboard(labels))


async def _stats(callback: CallbackQuery, now: datetime, tz: ZoneInfo) -> None:
    async with get_sessionmaker()() as session:
        counts = await booking_stats(session, now, tz)
    text = (
        f"Майбутні: {counts['upcoming']}\n"
        f"Починаються сьогодні: {counts['today']}\n"
        f"Скасовані за 7 днів: {counts['cancelled_7d']}"
    )
    await _send(callback, text, admin_home_keyboard())
