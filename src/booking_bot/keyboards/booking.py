from datetime import datetime

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from booking_bot.texts import (
    BTN_ANY_MASTER,
    BTN_BACK,
    BTN_CANCEL,
    BTN_CONFIRM,
    BTN_NO,
    BTN_RESTART,
    BTN_RESUME,
    BTN_SKIP,
    BTN_YES_CANCEL,
)


def resume_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=BTN_RESUME, callback_data="book:resume")
    builder.button(text=BTN_RESTART, callback_data="book:restart")
    builder.adjust(1)
    return builder.as_markup()


def choice_keyboard(choices: list[tuple[str, str]], *, back: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for label, data in choices:
        builder.button(text=label, callback_data=data)
    if back:
        builder.button(text=BTN_BACK, callback_data="book:back")
    builder.button(text=BTN_CANCEL, callback_data="book:cancel")
    builder.adjust(1)
    return builder.as_markup()


def any_master_choice(masters: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = [(name, f"book:mst:{master_id}") for master_id, name in masters]
    rows.append((BTN_ANY_MASTER, "book:mst:any"))
    return choice_keyboard(rows, back=True)


def slot_keyboard(slots: list[datetime]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for slot in slots:
        builder.button(text=slot.strftime("%H:%M"),
                       callback_data=f"book:time:{slot.strftime('%H%M')}")
    builder.adjust(3)
    builder.row()
    builder.button(text=BTN_BACK, callback_data="book:back")
    builder.button(text=BTN_CANCEL, callback_data="book:cancel")
    return builder.as_markup()


def note_keyboard() -> InlineKeyboardMarkup:
    return choice_keyboard([(BTN_SKIP, "book:skip")], back=True)


def summary_keyboard() -> InlineKeyboardMarkup:
    return choice_keyboard([(BTN_CONFIRM, "book:ok")], back=True)


def booking_list_keyboard(rows: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for booking_id, label in rows:
        builder.button(text=label, callback_data=f"me:{booking_id}")
    builder.adjust(1)
    return builder.as_markup()


def booking_detail_keyboard(booking_id: int, can_cancel: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if can_cancel:
        builder.button(text=BTN_CANCEL, callback_data=f"me:x:{booking_id}")
    builder.adjust(1)
    return builder.as_markup()


def confirm_cancel_keyboard(booking_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=BTN_YES_CANCEL, callback_data=f"me:yes:{booking_id}")
    builder.button(text=BTN_NO, callback_data=f"me:{booking_id}")
    builder.adjust(1)
    return builder.as_markup()
