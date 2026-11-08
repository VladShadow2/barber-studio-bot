from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from booking_bot.texts import WEEKDAYS


def master_keyboard(rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for label, data in rows:
        builder.button(text=label, callback_data=data)
    builder.adjust(1)
    return builder.as_markup()


def hours_home_keyboard() -> InlineKeyboardMarkup:
    return master_keyboard(
        [
            ("🕐 Години роботи", "mst:hours"),
            ("☕ Мої перерви", "mst:br"),
            ("📌 Мої вихідні", "mst:ex"),
        ]
    )


def weekday_choice_keyboard() -> InlineKeyboardMarkup:
    rows = [(name, f"mst:hw:{index}") for index, name in enumerate(WEEKDAYS)]
    rows.append(("◀️ Назад", "mst:home"))
    return master_keyboard(rows)


def confirm_keyboard() -> InlineKeyboardMarkup:
    return master_keyboard(
        [
            ("✅ Так, зберегти", "mst:yes"),
            ("❌ Ні", "mst:no"),
        ]
    )
