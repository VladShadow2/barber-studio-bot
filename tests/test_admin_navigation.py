import asyncio
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.base import StorageKey
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import Chat, Message, Update, User

from booking_bot.bot import build_dispatcher
from booking_bot.config import Settings
from booking_bot.handlers.admin.panel import AdminFlow
from booking_bot.texts import ADMIN_HOME, BTN_BOOK

TOKEN = "123456789:" + ("A" * 35)


class RecordingSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod] = []

    async def close(self) -> None:
        return None

    async def stream_content(self, *_args, **_kwargs):
        yield b""

    async def make_request(self, bot, method: TelegramMethod[TelegramType], timeout=None):
        self.calls.append(method)
        if isinstance(method, SendMessage):
            return Message(
                message_id=len(self.calls),
                date=datetime(2026, 11, 7, tzinfo=UTC),
                chat=Chat(id=1, type="private"),
                from_user=User(id=1, is_bot=False, first_name="Ada"),
                text=method.text,
            )
        raise AssertionError(type(method).__name__)


def _message(text: str, update_id: int) -> Update:
    user = User(id=1, is_bot=False, first_name="Ada")
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=datetime(2026, 11, 7, tzinfo=UTC),
            chat=Chat(id=1, type="private"),
            from_user=user,
            text=text,
        ),
    )


def test_menu_button_leaves_hour_editing_without_saving(monkeypatch) -> None:
    saved: list[object] = []

    async def reject_save(*_args, **_kwargs):
        saved.append("saved")

    async def opened(message, state):
        saved.append(message.text)
        saved.append(await state.get_state())

    settings = Settings(
        bot_token=TOKEN,
        admin_ids="1",
        database_url="postgresql+asyncpg://booking:booking@localhost/booking_test",
    )
    monkeypatch.setattr("booking_bot.handlers.admin.panel.load_settings", lambda: settings)
    monkeypatch.setattr("booking_bot.handlers.admin.panel.upsert_hours", reject_save)
    monkeypatch.setattr("booking_bot.handlers.customer.booking.begin_booking", opened)
    session = RecordingSession()
    dispatcher = build_dispatcher()

    async def go() -> None:
        async with Bot(token=TOKEN, session=session) as bot:
            key = StorageKey(bot_id=bot.id, chat_id=1, user_id=1)
            await dispatcher.storage.set_state(key, AdminFlow.text)
            await dispatcher.storage.set_data(key, {"kind": "salon_hours", "weekday": 2})
            await dispatcher.feed_update(bot, _message(BTN_BOOK, 1))
            saved.append(await dispatcher.storage.get_state(key))

    asyncio.run(go())
    assert saved == [BTN_BOOK, None, None]
    assert not any(isinstance(call, SendMessage) and call.text and "не підходить" in call.text for call in session.calls)  # noqa: E501


def test_cancel_and_admin_commands_exit_editing(monkeypatch) -> None:
    saved: list[object] = []

    async def reject_save(*_args, **_kwargs):
        saved.append("saved")

    settings = Settings(
        bot_token=TOKEN,
        admin_ids="1",
        database_url="postgresql+asyncpg://booking:booking@localhost/booking_test",
    )
    monkeypatch.setattr("booking_bot.handlers.admin.panel.load_settings", lambda: settings)
    monkeypatch.setattr("booking_bot.handlers.admin.panel.upsert_hours", reject_save)
    session = RecordingSession()
    dispatcher = build_dispatcher()
    texts: list[str] = []

    async def go() -> None:
        async with Bot(token=TOKEN, session=session) as bot:
            key = StorageKey(bot_id=bot.id, chat_id=1, user_id=1)
            await dispatcher.storage.set_state(key, AdminFlow.text)
            await dispatcher.storage.set_data(key, {"kind": "salon_hours", "weekday": 2})
            await dispatcher.feed_update(bot, _message("/cancel", 1))
            texts.append(str(await dispatcher.storage.get_state(key)))
            await dispatcher.storage.set_state(key, AdminFlow.confirm)
            await dispatcher.storage.set_data(
                key, {"kind": "salon_hours", "weekday": 2, "payload": "10:00-19:00"}
            )
            await dispatcher.feed_update(bot, _message("/admin", 2))
            texts.append(str(await dispatcher.storage.get_state(key)))

    asyncio.run(go())
    sent = [call.text for call in session.calls if isinstance(call, SendMessage)]
    assert any(text and "не збережено" in text for text in sent)
    assert ADMIN_HOME in sent
    assert texts == ["None", "None"]
    assert saved == []
