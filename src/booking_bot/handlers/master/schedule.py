from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from booking_bot.config import load_settings
from booking_bot.handlers.admin.panel import follow_navigation
from booking_bot.keyboards.master import (
    confirm_keyboard,
    hours_home_keyboard,
    master_keyboard,
    weekday_choice_keyboard,
)
from booking_bot.keyboards.start import NAV_TEXTS
from booking_bot.services.admin_edit import parse_break, parse_exception, parse_hours
from booking_bot.services.master_schedule import (
    break_for_master,
    commit_break,
    commit_break_delete,
    commit_exception,
    commit_exception_delete,
    commit_hours,
    day_hours,
    exception_for_master,
    find_master,
    home_text,
    list_breaks,
    list_exceptions,
    preview_conflicts,
)
from booking_bot.services.schedule_guard import BreakEdit, ExceptionEdit, HoursEdit
from booking_bot.texts import (
    BTN_HOURS,
    NOT_AVAILABLE,
    WEEKDAY_SHORT,
    current_hours_text,
    format_when,
    saved_hours_label,
    saved_hours_text,
)


class MasterFlow(StatesGroup):
    text = State()
    confirm = State()


def create_master_router() -> Router:
    router = Router(name="master")
    router.callback_query(F.data.startswith("mst:"))(master_callback)
    router.message(MasterFlow.text, F.text)(master_text)
    router.message(MasterFlow.confirm, F.text)(master_confirm_text)
    router.message(F.text == BTN_HOURS)(open_my_hours)
    return router


def _break_label(row) -> str:
    return (
        f"{WEEKDAY_SHORT[row.weekday]} "
        f"{row.start_time.strftime('%H:%M')}–{row.end_time.strftime('%H:%M')}"
    )


def _exception_label(row) -> str:
    if row.is_closed:
        window = "закрито"
    else:
        window = saved_hours_label(False, row.open_time, row.close_time)
    return f"{row.on_date.isoformat()} {window}"


def blocked_text(visits) -> str:
    tz = ZoneInfo(load_settings().timezone)
    lines = ["⚠️ Цю зміну не збережено. Вона перетинається з існуючими записами."]
    for visit in visits[:10]:
        when = format_when(visit.starts_at.astimezone(tz))
        lines.append(f"• {when} — {visit.service_name}")
    if len(visits) > 10:
        lines.append(f"• і ще {len(visits) - 10}")
    lines.append("Існуючі записи не скасовано.")
    return "\n".join(lines)


async def _reply(target, text: str, markup=None) -> None:
    if isinstance(target, CallbackQuery):
        await target.answer()
        if isinstance(target.message, Message):
            await target.message.answer(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


async def open_my_hours(message: Message, state: FSMContext) -> None:
    await state.clear()
    if message.from_user is None:
        return
    master = await find_master(message.from_user.id)
    if master is None:
        await message.answer(NOT_AVAILABLE)
        return
    await message.answer(await home_text(master), reply_markup=hours_home_keyboard())


async def _show_home(target, master) -> None:
    await _reply(target, await home_text(master), hours_home_keyboard())


async def master_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.data is None:
        await _reply(callback, NOT_AVAILABLE)
        return
    master = await find_master(callback.from_user.id)
    if master is None:
        await state.clear()
        await _reply(callback, NOT_AVAILABLE)
        return
    data = callback.data
    if data == "mst:home":
        await state.clear()
        await _show_home(callback, master)
    elif data == "mst:hours":
        await state.clear()
        await _reply(callback, "🕐 Оберіть день тижня.", weekday_choice_keyboard())
    elif data.startswith("mst:hw:"):
        await _ask_hours(callback, state, master.id, data)
    elif data == "mst:br":
        await state.clear()
        await _show_breaks(callback, master.id)
    elif data == "mst:br:add":
        await state.set_state(MasterFlow.text)
        await state.update_data(kind="break_add")
        await _reply(callback, "Надішліть свою перерву, наприклад: Пн 14:00-15:00")
    elif data.startswith("mst:brx:"):
        await _ask_delete_break(callback, state, master.id, data)
    elif data == "mst:ex":
        await state.clear()
        await _show_exceptions(callback, master.id)
    elif data == "mst:ex:add":
        await state.set_state(MasterFlow.text)
        await state.update_data(kind="exception_add")
        await _reply(callback, "Надішліть вихідний, наприклад: 2030-01-02 закрито")
    elif data.startswith("mst:exx:"):
        await _ask_delete_exception(callback, state, master.id, data)
    elif data == "mst:yes":
        await _apply(callback, state, master)
    elif data == "mst:no":
        await state.clear()
        await _reply(
            callback,
            f"❌ Зміну не збережено.\n\n{await home_text(master)}",
            hours_home_keyboard(),
        )
    else:
        await _reply(callback, NOT_AVAILABLE)


def _weekday(token: str) -> int | None:
    if not token.isdigit():
        return None
    weekday = int(token)
    if weekday > 6:
        return None
    return weekday


async def _ask_hours(callback: CallbackQuery, state: FSMContext, master_id: int, data: str) -> None:
    parts = data.split(":")
    weekday = _weekday(parts[2]) if len(parts) == 3 else None
    if weekday is None:
        await _reply(callback, NOT_AVAILABLE)
        return
    row = await day_hours(master_id, weekday)
    await state.set_state(MasterFlow.text)
    await state.update_data(kind="hours", weekday=weekday)
    await _reply(callback, current_hours_text(weekday, row))


async def _show_breaks(callback: CallbackQuery, master_id: int) -> None:
    rows = await list_breaks(master_id)
    lines = ["☕ Мої перерви"]
    labels = []
    if not rows:
        lines.append("Перерв ще немає.")
    for row in rows:
        label = _break_label(row)
        lines.append(label)
        labels.append((f"🗑️ {label}", f"mst:brx:{row.id}"))
    labels.append(("➕ Додати перерву", "mst:br:add"))
    labels.append(("◀️ Назад", "mst:home"))
    await _reply(callback, "\n".join(lines), master_keyboard(labels))


async def _show_exceptions(callback: CallbackQuery, master_id: int) -> None:
    rows = await list_exceptions(master_id)
    lines = ["📌 Мої вихідні"]
    labels = []
    if not rows:
        lines.append("Окремих днів ще немає.")
    for row in rows:
        label = _exception_label(row)
        lines.append(label)
        labels.append((f"🗑️ {label}", f"mst:exx:{row.id}"))
    labels.append(("➕ Додати вихідний", "mst:ex:add"))
    labels.append(("◀️ Назад", "mst:home"))
    await _reply(callback, "\n".join(lines), master_keyboard(labels))


async def _ask_delete_break(callback, state, master_id: int, data: str) -> None:
    break_id = _record_id(data)
    row = None if break_id is None else await break_for_master(master_id, break_id)
    if row is None:
        await _reply(callback, NOT_AVAILABLE)
        return
    await state.set_state(MasterFlow.confirm)
    await state.update_data(kind="break_delete", break_id=row.id)
    await _reply(callback, f"Видалити перерву {_break_label(row)}?", confirm_keyboard())


async def _ask_delete_exception(callback, state, master_id: int, data: str) -> None:
    exception_id = _record_id(data)
    row = None if exception_id is None else await exception_for_master(master_id, exception_id)
    if row is None:
        await _reply(callback, NOT_AVAILABLE)
        return
    await state.set_state(MasterFlow.confirm)
    await state.update_data(
        kind="exception_delete",
        exception_id=row.id,
        on_date=row.on_date.isoformat(),
    )
    await _reply(callback, f"Видалити вихідний {_exception_label(row)}?", confirm_keyboard())


def _record_id(data: str) -> int | None:
    parts = data.split(":")
    if len(parts) != 3 or not parts[2].isdigit() or parts[2] == "0":
        return None
    return int(parts[2])


async def master_text(message: Message, state: FSMContext) -> None:
    if message.from_user is None or message.text is None:
        return
    if message.text in NAV_TEXTS:
        await follow_navigation(message, state)
        return
    master = await find_master(message.from_user.id)
    if master is None:
        await state.clear()
        await message.answer(NOT_AVAILABLE)
        return
    data = await state.get_data()
    kind = data.get("kind")
    parsed = _parse(kind, message.text)
    if parsed is None:
        await message.answer(
            "Це значення не підходить. Надішліть його знову або /cancel, щоб зупинитись."
        )
        return
    change = _change(kind, parsed, data)
    conflicts = await preview_conflicts(master.id, change, datetime.now(UTC))
    if conflicts:
        await state.clear()
        await message.answer(blocked_text(conflicts))
        await message.answer(await home_text(master), reply_markup=hours_home_keyboard())
        return
    await state.update_data(payload=message.text.strip())
    await state.set_state(MasterFlow.confirm)
    await message.answer(
        f"Зберегти цю зміну?\n{message.text.strip()}",
        reply_markup=confirm_keyboard(),
    )


async def master_confirm_text(message: Message, state: FSMContext) -> None:
    if message.from_user is None or message.text is None:
        return
    if message.text in NAV_TEXTS:
        await follow_navigation(message, state)
        return
    await message.answer("Оберіть «Так, зберегти» або «Ні». Зміну ще не збережено.")


def _parse(kind, raw: str):
    if kind == "hours":
        return parse_hours(raw)
    if kind == "break_add":
        return parse_break(raw)
    if kind == "exception_add":
        return parse_exception(raw)
    return None


def _change(kind, parsed, data):
    if kind == "hours":
        closed, open_time, close_time = parsed
        return HoursEdit(int(data["weekday"]), closed, open_time, close_time)
    if kind == "break_add":
        weekday, start, end = parsed
        return BreakEdit(weekday, start, end)
    on_date, closed, open_time, close_time = parsed
    return ExceptionEdit(on_date, closed, open_time, close_time)


async def _apply(callback: CallbackQuery, state: FSMContext, master) -> None:
    if await state.get_state() != MasterFlow.confirm.state:
        await _reply(callback, NOT_AVAILABLE)
        return
    data = await state.get_data()
    kind = data.get("kind")
    if kind == "break_delete":
        removed = await commit_break_delete(master.id, int(data["break_id"]))
        await state.clear()
        text = "🗑️ Перерву видалено." if removed else NOT_AVAILABLE
        await _reply(callback, text, hours_home_keyboard())
        return
    if kind == "exception_delete":
        await _apply_exception_delete(callback, state, master, data)
        return
    parsed = _parse(kind, data.get("payload", ""))
    if parsed is None:
        await state.clear()
        await _reply(callback, NOT_AVAILABLE)
        return
    change = _change(kind, parsed, data)
    conflicts = await preview_conflicts(master.id, change, datetime.now(UTC))
    if conflicts:
        await state.clear()
        await _reply(callback, blocked_text(conflicts))
        return
    if kind == "hours":
        closed, open_time, close_time = parsed
        weekday = int(data["weekday"])
        await commit_hours(master.id, weekday, closed, open_time, close_time)
        text = saved_hours_text(weekday, closed, open_time, close_time, master=True)
    elif kind == "break_add":
        weekday, start, end = parsed
        await commit_break(master.id, weekday, start, end)
        text = "☕ Перерву збережено. Існуючі записи не скасовано."
    elif kind == "exception_add":
        on_date, closed, open_time, close_time = parsed
        await commit_exception(master.id, on_date, closed, open_time, close_time)
        text = "📌 Вихідний збережено. Існуючі записи не скасовано."
    else:
        text = NOT_AVAILABLE
    await state.clear()
    await _reply(callback, text, hours_home_keyboard())


async def _apply_exception_delete(callback, state, master, data) -> None:
    on_date = date.fromisoformat(data["on_date"])
    change = ExceptionEdit(on_date, True, None, None, remove=True)
    conflicts = await preview_conflicts(master.id, change, datetime.now(UTC))
    if conflicts:
        await state.clear()
        await _reply(callback, blocked_text(conflicts))
        return
    removed = await commit_exception_delete(master.id, int(data["exception_id"]))
    await state.clear()
    text = "🗑️ Вихідний видалено." if removed else NOT_AVAILABLE
    await _reply(callback, text, hours_home_keyboard())
