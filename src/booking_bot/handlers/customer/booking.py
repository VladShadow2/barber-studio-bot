from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from booking_bot.config import Settings, load_settings, parse_admin_ids
from booking_bot.db.models import Master, Service
from booking_bot.db.session import get_sessionmaker
from booking_bot.keyboards.booking import (
    any_master_choice,
    choice_keyboard,
    note_keyboard,
    resume_keyboard,
    slot_keyboard,
    summary_keyboard,
)
from booking_bot.keyboards.start import CB_BOOK, NAV_TEXTS, main_menu_markup
from booking_bot.repositories.schedule import load_calendar, masters_for_service
from booking_bot.services.availability import slots_for_any_master, slots_for_master
from booking_bot.services.booking import SlotTaken, confirm_draft, deliver
from booking_bot.services.drafts import get_draft, previous_step, touch_draft
from booking_bot.texts import (
    ANY_MASTER_LABEL,
    BOOKED,
    BOOKING_CANCELLED,
    CHOOSE_DATE,
    CHOOSE_MASTER,
    CHOOSE_SERVICE,
    CHOOSE_TIME,
    CONFIRM_SUMMARY,
    EXPIRED,
    NO_DAYS,
    NO_MASTERS,
    NO_SERVICES,
    NO_SLOTS,
    NOT_SELECTED,
    NOTE_PROMPT,
    STALE,
    TAKEN,
    UNFINISHED,
    format_day,
    format_when,
)


class BookFlow(StatesGroup):
    service = State()
    master = State()
    date = State()
    time = State()
    note = State()
    summary = State()


_STATES = {
    "service": BookFlow.service,
    "master": BookFlow.master,
    "date": BookFlow.date,
    "time": BookFlow.time,
    "note": BookFlow.note,
    "summary": BookFlow.summary,
}


def register_booking(router: Router) -> None:
    router.callback_query(F.data == CB_BOOK)(begin_booking)
    router.callback_query(F.data == "book:resume")(resume_book)
    router.callback_query(F.data == "book:restart")(restart_book)
    router.callback_query(F.data == "book:cancel")(cancel_book)
    router.callback_query(F.data == "book:back")(back_book)
    router.callback_query(F.data.startswith("book:svc:"))(pick_service)
    router.callback_query(F.data.startswith("book:mst:"))(pick_master)
    router.callback_query(F.data.startswith("book:day:"))(pick_day)
    router.callback_query(F.data.startswith("book:time:"))(pick_time)
    router.callback_query(F.data == "book:skip")(skip_note)
    router.callback_query(F.data == "book:ok")(confirm_book)
    router.message(BookFlow.note, F.text, ~F.text.in_(NAV_TEXTS))(save_note)


async def _send(callback: CallbackQuery, text: str, markup=None) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(text, reply_markup=markup)


async def _show_step(message_target, state: FSMContext, step: str, text: str, markup) -> None:
    await state.set_state(_STATES[step])
    if isinstance(message_target, CallbackQuery):
        await _send(message_target, text, markup)
    else:
        await message_target.answer(text, reply_markup=markup)


async def begin_booking(target: CallbackQuery | Message, state: FSMContext) -> None:
    if target.from_user is None:
        if isinstance(target, CallbackQuery):
            await target.answer()
        return
    settings = load_settings()
    now = datetime.now(UTC)
    async with get_sessionmaker()() as session:
        async with session.begin():
            draft = await get_draft(session, target.from_user.id, now)
            if draft is not None:
                await state.set_state(None)
                await _reply(target, UNFINISHED, resume_keyboard())
                return
            await touch_draft(session, target.from_user.id, "service", settings, now, any_master=False)  # noqa: E501
            text, markup = await _service_screen(session, target.from_user.id)
    await _show_step(target, state, "service", text, markup)


async def _reply(target: CallbackQuery | Message, text: str, markup=None) -> None:
    if isinstance(target, CallbackQuery):
        await _send(target, text, markup)
    else:
        await target.answer(text, reply_markup=markup)


async def resume_book(callback: CallbackQuery, state: FSMContext) -> None:
    await _render_current(callback, state)


async def restart_book(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None:
        await callback.answer()
        return
    settings = load_settings()
    now = datetime.now(UTC)
    async with get_sessionmaker()() as session:
        async with session.begin():
            await touch_draft(
                session,
                callback.from_user.id,
                "service",
                settings,
                now,
                service_id=None,
                master_id=None,
                any_master=False,
                appointment_date=None,
                starts_at=None,
                customer_note=None,
            )
            text, markup = await _service_screen(session, callback.from_user.id)
    await _show_step(callback, state, "service", text, markup)


async def cancel_book(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None:
        await callback.answer()
        return
    from booking_bot.db.models import BookingDraft

    async with get_sessionmaker()() as session:
        async with session.begin():
            draft = await session.get(BookingDraft, callback.from_user.id)
            if draft is not None:
                await session.delete(draft)
    await state.clear()
    await _send(callback, BOOKING_CANCELLED, await main_menu_markup(callback.from_user.id))


async def back_book(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None:
        await callback.answer()
        return
    settings = load_settings()
    now = datetime.now(UTC)
    async with get_sessionmaker()() as session:
        async with session.begin():
            draft = await get_draft(session, callback.from_user.id, now)
            if draft is None:
                await _send(callback, EXPIRED, await main_menu_markup(callback.from_user.id))
                return
            step = previous_step(draft.step)
            if step is None:
                await _send(callback, EXPIRED, await main_menu_markup(callback.from_user.id))
                return
            clears = {}
            if step == "service":
                clears = {"master_id": None, "any_master": False,
                          "appointment_date": None, "starts_at": None}
            elif step == "master":
                clears = {"appointment_date": None, "starts_at": None}
            elif step == "date":
                clears = {"starts_at": None}
            await touch_draft(session, callback.from_user.id, step, settings, now, **clears)
    await _render_current(callback, state)


async def pick_service(callback: CallbackQuery, state: FSMContext) -> None:
    await _advance(callback, state, expected="service", field="service")


async def pick_master(callback: CallbackQuery, state: FSMContext) -> None:
    await _advance(callback, state, expected="master", field="master")


async def pick_day(callback: CallbackQuery, state: FSMContext) -> None:
    await _advance(callback, state, expected="date", field="date")


async def pick_time(callback: CallbackQuery, state: FSMContext) -> None:
    await _advance(callback, state, expected="time", field="time")


async def skip_note(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None:
        await callback.answer()
        return
    settings = load_settings()
    now = datetime.now(UTC)
    async with get_sessionmaker()() as session:
        async with session.begin():
            draft = await get_draft(session, callback.from_user.id, now)
            if draft is None or draft.step != "note":
                await _send(callback, STALE)
                return
            await touch_draft(session, callback.from_user.id, "summary", settings, now, customer_note=None)  # noqa: E501
    await _render_current(callback, state)


async def save_note(message: Message, state: FSMContext) -> None:
    if message.from_user is None or message.text is None:
        return
    settings = load_settings()
    now = datetime.now(UTC)
    async with get_sessionmaker()() as session:
        async with session.begin():
            draft = await get_draft(session, message.from_user.id, now)
            if draft is None or draft.step != "note":
                await message.answer(STALE)
                return
            await touch_draft(
                session,
                message.from_user.id,
                "summary",
                settings,
                now,
                customer_note=message.text.strip()[:300],
            )
    await _render_current(message, state)


async def confirm_book(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None:
        await callback.answer()
        return
    settings = load_settings()
    now = datetime.now(UTC)
    user = callback.from_user
    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                draft = await get_draft(session, user.id, now)
                if draft is None or draft.step != "summary":
                    await _send(callback, STALE)
                    return
                result = await confirm_draft(
                    session,
                    telegram_id=user.id,
                    display_name=user.full_name,
                    username=user.username,
                    settings=settings,
                    now=now,
                    admin_ids=parse_admin_ids(settings.admin_ids),
                )
    except SlotTaken:
        await _send(callback, TAKEN)
        await _render_current(callback, state)
        return
    if result.code == "confirmed":
        await state.clear()
        await _send(callback, BOOKED, await main_menu_markup(callback.from_user.id))
        await deliver(callback.bot, result.messages)
        return
    if result.code == "taken":
        await _send(callback, TAKEN)
        await _render_current(callback, state)
        return
    await _send(callback, EXPIRED, await main_menu_markup(callback.from_user.id))


async def _advance(callback: CallbackQuery, state: FSMContext, *, expected: str, field: str) -> None:  # noqa: E501
    if callback.from_user is None or callback.data is None:
        await callback.answer()
        return
    settings = load_settings()
    now = datetime.now(UTC)
    async with get_sessionmaker()() as session:
        async with session.begin():
            draft = await get_draft(session, callback.from_user.id, now)
            if draft is None or draft.step != expected:
                await _send(callback, STALE if draft is not None else EXPIRED)
                return
            if field == "service":
                service_id = int(callback.data.split(":")[2])
                service = await session.get(Service, service_id)
                if service is None or not service.is_active:
                    await _send(callback, STALE)
                    return
                await touch_draft(
                    session,
                    callback.from_user.id,
                    "master",
                    settings,
                    now,
                    service_id=service_id,
                    master_id=None,
                    any_master=False,
                    appointment_date=None,
                    starts_at=None,
                    customer_note=None,
                )
            elif field == "master":
                token = callback.data.split(":")[2]
                if token == "any":
                    fields = {"master_id": None, "any_master": True}
                else:
                    master = await session.get(Master, int(token))
                    if master is None or not master.is_active:
                        await _send(callback, STALE)
                        return
                    fields = {"master_id": master.id, "any_master": False}
                await touch_draft(
                    session,
                    callback.from_user.id,
                    "date",
                    settings,
                    now,
                    appointment_date=None,
                    starts_at=None,
                    **fields,
                )
            elif field == "date":
                from datetime import date

                picked = date.fromisoformat(callback.data.split(":")[2])
                days = await _open_days(session, draft, settings, now)
                if picked not in days:
                    await _send(callback, STALE)
                    return
                await touch_draft(
                    session,
                    callback.from_user.id,
                    "time",
                    settings,
                    now,
                    appointment_date=picked,
                    starts_at=None,
                )
            else:
                slots = await _slots_for_draft(session, draft, settings, now)
                label = callback.data.split(":")[2]
                match = next((slot for slot in slots if slot.strftime("%H%M") == label), None)
                if match is None:
                    await _send(callback, STALE)
                    return
                await touch_draft(
                    session,
                    callback.from_user.id,
                    "note",
                    settings,
                    now,
                    starts_at=match.astimezone(UTC),
                )
    await _render_current(callback, state)


async def _render_current(target, state: FSMContext) -> None:
    user = target.from_user
    if user is None:
        if isinstance(target, CallbackQuery):
            await target.answer()
        return
    settings = load_settings()
    now = datetime.now(UTC)
    async with get_sessionmaker()() as session:
        async with session.begin():
            draft = await get_draft(session, user.id, now)
            if draft is None:
                if isinstance(target, CallbackQuery):
                    await _send(target, EXPIRED, await main_menu_markup(user.id))
                else:
                    await target.answer(EXPIRED, reply_markup=await main_menu_markup(user.id))
                await state.clear()
                return
            text, markup = await _screen(session, draft, settings, now, user.id)
            step = draft.step
    await _show_step(target, state, step, text, markup)


async def _screen(session, draft, settings: Settings, now: datetime, user_id: int):
    if draft.step == "service":
        return await _service_screen(session, user_id)
    if draft.step == "master":
        masters = await masters_for_service(session, draft.service_id)
        if not masters:
            return NO_MASTERS, await main_menu_markup(user_id)
        return CHOOSE_MASTER, any_master_choice([(master.id, master.name) for master in masters])
    if draft.step == "date":
        days = await _open_days(session, draft, settings, now)
        if not days:
            return NO_DAYS, choice_keyboard([], back=True)
        choices = [(format_day(day), f"book:day:{day.isoformat()}") for day in days]
        return CHOOSE_DATE, choice_keyboard(choices, back=True)
    if draft.step == "time":
        slots = await _slots_for_draft(session, draft, settings, now)
        if not slots:
            return NO_SLOTS, choice_keyboard([], back=True)
        return CHOOSE_TIME, slot_keyboard(slots)
    if draft.step == "note":
        return NOTE_PROMPT, note_keyboard()
    service = await session.get(Service, draft.service_id)
    master_name = ANY_MASTER_LABEL
    if not draft.any_master and draft.master_id is not None:
        master = await session.get(Master, draft.master_id)
        master_name = master.name if master is not None else master_name
    tz = ZoneInfo(settings.timezone)
    when = format_when(draft.starts_at.astimezone(tz)) if draft.starts_at else NOT_SELECTED
    note = f"\nПримітка: {draft.customer_note}" if draft.customer_note else ""
    price = service.price_uah if service is not None else ""
    duration = service.duration_minutes if service is not None else ""
    name = service.name if service is not None else "Послуга"
    text = (
        f"{CONFIRM_SUMMARY}\n"
        f"{name}, майстер {master_name}\n{when}\n{duration} хв · {price} {settings.currency}{note}"
    )
    return text, summary_keyboard()


async def _service_screen(session, user_id: int):
    rows = list(await session.scalars(select(Service).where(Service.is_active.is_(True)).order_by(Service.name)))  # noqa: E501
    if not rows:
        return NO_SERVICES, await main_menu_markup(user_id)
    choices = [(row.name, f"book:svc:{row.id}") for row in rows]
    return CHOOSE_SERVICE, choice_keyboard(choices, back=False)


async def _master_ids(session, draft) -> list[int]:
    masters = await masters_for_service(session, draft.service_id)
    if draft.any_master:
        return [master.id for master in masters]
    if draft.master_id is None:
        return []
    return [draft.master_id]


async def _open_days(session, draft, settings: Settings, now: datetime):
    tz = ZoneInfo(settings.timezone)
    service = await session.get(Service, draft.service_id)
    if service is None:
        return []
    calendar = await load_calendar(session, now - timedelta(days=1), now + timedelta(days=settings.booking_days_ahead + 1))  # noqa: E501
    master_ids = await _master_ids(session, draft)
    today = now.astimezone(tz).date()
    days = []
    for offset in range(settings.booking_days_ahead):
        day = today + timedelta(days=offset)
        slots = _day_slots(calendar, master_ids, draft.any_master, day,
                           service.duration_minutes, settings, now)
        if slots:
            days.append(day)
    return days


async def _slots_for_draft(session, draft, settings: Settings, now: datetime):
    service = await session.get(Service, draft.service_id)
    if service is None or draft.appointment_date is None:
        return []
    calendar = await load_calendar(session, now - timedelta(days=1), now + timedelta(days=settings.booking_days_ahead + 1))  # noqa: E501
    master_ids = await _master_ids(session, draft)
    return _day_slots(
        calendar,
        master_ids,
        draft.any_master,
        draft.appointment_date,
        service.duration_minutes,
        settings,
        now,
    )


def _day_slots(calendar, master_ids, any_master, day, duration, settings: Settings, now: datetime):
    tz = ZoneInfo(settings.timezone)
    if any_master:
        return slots_for_any_master(
            calendar, master_ids, day, duration, settings.slot_minutes, tz, now, settings.min_lead_minutes  # noqa: E501
        )
    if not master_ids:
        return []
    return slots_for_master(
        calendar,
        master_ids[0],
        day,
        duration,
        settings.slot_minutes,
        tz,
        now,
        settings.min_lead_minutes,
    )
