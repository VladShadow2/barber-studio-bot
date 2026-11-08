from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from booking_bot.config import load_settings, parse_admin_ids
from booking_bot.texts import (
    BTN_ABOUT,
    BTN_ADMIN,
    BTN_BOOK,
    BTN_BOOKINGS,
    BTN_HOURS,
    BTN_SCHEDULE,
    BTN_SERVICES,
)

CB_BOOK = "start:book"
CB_SERVICES = "start:services"
CB_ABOUT = "start:about"
CB_BOOKINGS = "start:bookings"
MENU_TEXTS = (BTN_BOOK, BTN_SERVICES, BTN_ABOUT, BTN_BOOKINGS)
NAV_TEXTS = (*MENU_TEXTS, BTN_ADMIN, BTN_SCHEDULE, BTN_HOURS)


def start_keyboard(
    *,
    include_admin: bool = False,
    include_schedule: bool = False,
) -> ReplyKeyboardMarkup:
    labels = list(MENU_TEXTS)
    if include_schedule:
        labels.append(BTN_SCHEDULE)
        labels.append(BTN_HOURS)
    if include_admin:
        labels.append(BTN_ADMIN)
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label)] for label in labels],
        resize_keyboard=True,
        is_persistent=True,
    )


def start_keyboard_for(user_id: int | None) -> ReplyKeyboardMarkup:
    include_admin = user_id is not None and user_id in parse_admin_ids(load_settings().admin_ids)
    return start_keyboard(include_admin=include_admin)


async def linked_master(user_id: int) -> bool:
    from sqlalchemy import select

    from booking_bot.db import session as db_session
    from booking_bot.db.models import Master

    if db_session._sessionmaker is None:
        return False
    async with db_session.get_sessionmaker()() as session:
        found = await session.scalar(select(Master.id).where(Master.telegram_user_id == user_id))
    return found is not None


async def main_menu_markup(user_id: int | None) -> ReplyKeyboardMarkup:
    include_admin = user_id is not None and user_id in parse_admin_ids(load_settings().admin_ids)
    include_schedule = user_id is not None and await linked_master(user_id)
    return start_keyboard(include_admin=include_admin, include_schedule=include_schedule)
