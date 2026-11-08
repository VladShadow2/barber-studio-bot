from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from booking_bot.texts import ADMIN_NO, ADMIN_YES, BTN_ADMIN_HOME, WEEKDAYS


def admin_home_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for label, data in (
        ("📅 Майбутні записи", "adm:up"),
        ("🗓️ Записи за датою", "adm:dates"),
        ("💈 Послуги", "adm:svc"),
        ("👤 Майстри", "adm:mst"),
        ("🕐 Години салону", "adm:hours"),
        ("☕ Перерви та вихідні", "adm:cal"),
        ("📊 Статистика", "adm:stats"),
    ):
        builder.button(text=label, callback_data=data)
    builder.adjust(1)
    return builder.as_markup()


def yes_no_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=ADMIN_YES, callback_data="adm:yes")
    builder.button(text=ADMIN_NO, callback_data="adm:no")
    builder.adjust(2)
    return builder.as_markup()


def weekday_keyboard(prefix: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, name in enumerate(WEEKDAYS):
        builder.button(text=name, callback_data=f"{prefix}:{index}")
    builder.adjust(1)
    return builder.as_markup()


def master_services_keyboard(
    services: list[tuple[int, str]],
    selected: set[int],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for service_id, name in services:
        mark = "✅" if service_id in selected else "⬜"
        label = f"{mark} {name}"
        if len(label) > 64:
            label = label[:63] + "…"
        builder.button(text=label, callback_data=f"adm:mls:{service_id}")
    builder.button(text="💾 Зберегти", callback_data="adm:mly")
    builder.button(text="❌ Скасувати", callback_data="adm:mln")
    builder.adjust(1)
    return builder.as_markup()


def rows_keyboard(rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for label, data in rows:
        builder.button(text=label, callback_data=data)
    builder.button(text=BTN_ADMIN_HOME, callback_data="adm:home")
    builder.adjust(1)
    return builder.as_markup()
