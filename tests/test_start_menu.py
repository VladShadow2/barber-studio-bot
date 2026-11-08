import asyncio
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.base import StorageKey
from aiogram.methods import AnswerCallbackQuery, SendMessage, TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import CallbackQuery, Chat, Message, ReplyKeyboardMarkup, Update, User

from booking_bot.bot import build_dispatcher
from booking_bot.config import Settings
from booking_bot.handlers.customer.booking import BookFlow
from booking_bot.keyboards.start import CB_ABOUT, CB_BOOK, CB_BOOKINGS, CB_SERVICES, start_keyboard
from booking_bot.texts import (
    BTN_ABOUT,
    BTN_BOOK,
    BTN_BOOKINGS,
    BTN_SERVICES,
    NO_BOOKINGS,
    NO_SERVICES,
    START,
    UNFINISHED,
)

TOKEN = "123456789:" + ("A" * 35)


class RecordingSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod] = []

    async def close(self) -> None:
        return None

    async def stream_content(
        self,
        url: str,
        headers: dict | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ):
        yield b""

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,
    ) -> TelegramType:
        self.calls.append(method)
        if isinstance(method, SendMessage):
            return _message(message_id=len(self.calls), text=method.text)
        if isinstance(method, AnswerCallbackQuery):
            return True
        raise AssertionError(f"Unexpected API call: {type(method).__name__}")


def _user() -> User:
    return User(id=7, is_bot=False, first_name="Ada")


def _chat() -> Chat:
    return Chat(id=7, type="private")


def _message(*, message_id: int = 1, text: str | None = None) -> Message:
    return Message(
        message_id=message_id,
        date=datetime(2026, 11, 7, tzinfo=UTC),
        chat=_chat(),
        from_user=_user(),
        text=text,
    )


def _key(bot: Bot) -> StorageKey:
    return StorageKey(bot_id=bot.id, chat_id=7, user_id=7)


def _feed(update: Update, *, prepare=None, after=None) -> list[TelegramMethod]:
    session = RecordingSession()
    dispatcher = build_dispatcher()

    async def run() -> None:
        async with Bot(token=TOKEN, session=session) as bot:
            if prepare is not None:
                await prepare(dispatcher, bot)
            await dispatcher.feed_update(bot, update)
            if after is not None:
                await after(dispatcher, bot)

    asyncio.run(run())
    return session.calls


def test_start_keyboard_lists_the_menu_actions() -> None:
    markup = start_keyboard()
    assert isinstance(markup, ReplyKeyboardMarkup)
    assert markup.resize_keyboard is True
    assert markup.is_persistent is True
    assert [len(row) for row in markup.keyboard] == [1, 1, 1, 1]
    assert [row[0].text for row in markup.keyboard] == [
        BTN_BOOK,
        BTN_SERVICES,
        BTN_ABOUT,
        BTN_BOOKINGS,
    ]


def _settings(admin_ids: str) -> Settings:
    return Settings(
        bot_token=TOKEN,
        admin_ids=admin_ids,
        database_url="postgresql+asyncpg://booking:booking@localhost/booking_test",
    )


async def _not_a_master(_user_id: int) -> bool:
    return False


def test_start_command_sends_the_greeting_with_the_menu(monkeypatch) -> None:
    monkeypatch.setattr("booking_bot.keyboards.start.load_settings", lambda: _settings(""))
    monkeypatch.setattr("booking_bot.keyboards.start.linked_master", _not_a_master)
    calls = _feed(Update(update_id=1, message=_message(text="/start")))
    assert len(calls) == 1
    sent = calls[0]
    assert isinstance(sent, SendMessage)
    assert sent.text == START
    assert isinstance(sent.reply_markup, ReplyKeyboardMarkup)
    labels = [button.text for row in sent.reply_markup.keyboard for button in row]
    assert labels == [BTN_BOOK, BTN_SERVICES, BTN_ABOUT, BTN_BOOKINGS]


def test_start_keyboard_adds_the_admin_button_only_for_admins(monkeypatch) -> None:
    from booking_bot.keyboards.start import start_keyboard_for
    from booking_bot.texts import BTN_ADMIN

    customer = start_keyboard()
    assert [button.text for row in customer.keyboard for button in row] == [
        BTN_BOOK,
        BTN_SERVICES,
        BTN_ABOUT,
        BTN_BOOKINGS,
    ]
    monkeypatch.setattr("booking_bot.keyboards.start.load_settings", lambda: _settings("7"))
    monkeypatch.setattr("booking_bot.keyboards.start.linked_master", _not_a_master)
    admin = start_keyboard_for(7)
    labels = [button.text for row in admin.keyboard for button in row]
    assert labels == [BTN_BOOK, BTN_SERVICES, BTN_ABOUT, BTN_BOOKINGS, BTN_ADMIN]
    assert [button.text for row in start_keyboard_for(4).keyboard for button in row] == [
        BTN_BOOK,
        BTN_SERVICES,
        BTN_ABOUT,
        BTN_BOOKINGS,
    ]
    calls = _feed(Update(update_id=40, message=_message(text="/start")))
    sent = calls[0]
    assert isinstance(sent, SendMessage)
    assert [button.text for row in sent.reply_markup.keyboard for button in row][-1] == BTN_ADMIN


def test_admin_button_opens_the_existing_panel(monkeypatch) -> None:
    from booking_bot.texts import ADMIN_HOME, BTN_ADMIN

    monkeypatch.setattr("booking_bot.handlers.admin.panel.load_settings", lambda: _settings("7"))
    calls = _feed(Update(update_id=41, message=_message(text=BTN_ADMIN)))
    sent = calls[-1]
    assert isinstance(sent, SendMessage)
    assert sent.text == ADMIN_HOME
    labels = [button.text for row in sent.reply_markup.inline_keyboard for button in row]
    assert "🕐 Години салону" in labels
    assert "👤 Майстри" in labels


def test_admin_button_denies_someone_outside_admin_ids(monkeypatch) -> None:
    from booking_bot.texts import BTN_ADMIN

    monkeypatch.setattr("booking_bot.handlers.admin.panel.load_settings", lambda: _settings("1"))
    calls = _feed(Update(update_id=42, message=_message(text=BTN_ADMIN)))
    sent = calls[-1]
    assert isinstance(sent, SendMessage)
    assert sent.text == "Ця дія недоступна."
    assert sent.reply_markup is None


class _Empty:
    def __iter__(self):
        return iter(())


class _Begin:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class _Session:
    def begin(self):
        return _Begin()

    def add(self, _obj) -> None:
        return None

    async def get(self, *_args, **_kwargs):
        return None

    async def scalars(self, *_args, **_kwargs):
        return _Empty()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


def _factory():
    return _Session


def test_menu_callbacks_answer_the_query(monkeypatch) -> None:
    settings = Settings(
        bot_token=TOKEN,
        admin_ids="",
        database_url="postgresql+asyncpg://booking:booking@localhost/booking_test",
    )
    monkeypatch.setattr("booking_bot.handlers.customer.menu.get_sessionmaker", _factory)
    monkeypatch.setattr("booking_bot.handlers.customer.booking.get_sessionmaker", _factory)
    monkeypatch.setattr("booking_bot.handlers.customer.menu.load_settings", lambda: settings)
    monkeypatch.setattr("booking_bot.handlers.customer.booking.load_settings", lambda: settings)
    cases = (
        (CB_BOOK, NO_SERVICES),
        (CB_SERVICES, NO_SERVICES),
        (CB_ABOUT, "вигаданий"),
        (CB_BOOKINGS, NO_BOOKINGS),
    )
    for index, (data, expected) in enumerate(cases, start=1):
        callback = CallbackQuery(
            id=f"cb-{data}",
            from_user=_user(),
            chat_instance="chat-instance",
            data=data,
            message=_message(message_id=10 + index, text=START),
        )
        calls = _feed(Update(update_id=index, callback_query=callback))
        assert [type(call) for call in calls] == [AnswerCallbackQuery, SendMessage]
        answered, sent = calls
        assert isinstance(answered, AnswerCallbackQuery)
        assert answered.callback_query_id == f"cb-{data}"
        assert isinstance(sent, SendMessage)
        assert sent.text is not None
        assert expected in sent.text


def test_reply_menu_buttons_open_the_same_screens(monkeypatch) -> None:
    settings = Settings(
        bot_token=TOKEN,
        admin_ids="",
        database_url="postgresql+asyncpg://booking:booking@localhost/booking_test",
    )
    monkeypatch.setattr("booking_bot.handlers.customer.menu.get_sessionmaker", _factory)
    monkeypatch.setattr("booking_bot.handlers.customer.booking.get_sessionmaker", _factory)
    monkeypatch.setattr("booking_bot.handlers.customer.menu.load_settings", lambda: settings)
    monkeypatch.setattr("booking_bot.handlers.customer.booking.load_settings", lambda: settings)
    cases = (
        (BTN_BOOK, NO_SERVICES),
        (BTN_SERVICES, NO_SERVICES),
        (BTN_ABOUT, "вигаданий"),
        (BTN_BOOKINGS, NO_BOOKINGS),
    )
    for index, (text, expected) in enumerate(cases, start=1):
        calls = _feed(Update(update_id=20 + index, message=_message(text=text)))
        assert [type(call) for call in calls] == [SendMessage]
        sent = calls[0]
        assert isinstance(sent, SendMessage)
        assert sent.text is not None
        assert expected in sent.text


def test_menu_button_during_a_note_does_not_change_the_draft(monkeypatch) -> None:
    settings = Settings(
        bot_token=TOKEN,
        admin_ids="",
        database_url="postgresql+asyncpg://booking:booking@localhost/booking_test",
    )
    writes: list[object] = []

    async def reject_write(*_args, **_kwargs):
        writes.append("write")
        raise AssertionError("booking draft was rewritten")

    monkeypatch.setattr("booking_bot.handlers.customer.menu.get_sessionmaker", _factory)
    monkeypatch.setattr("booking_bot.handlers.customer.menu.load_settings", lambda: settings)
    monkeypatch.setattr("booking_bot.handlers.customer.booking.touch_draft", reject_write)
    seen: dict[str, str | None] = {}

    async def prepare(dispatcher, bot: Bot) -> None:
        await dispatcher.storage.set_state(_key(bot), BookFlow.note)

    async def after(dispatcher, bot: Bot) -> None:
        seen["state"] = await dispatcher.storage.get_state(_key(bot))

    calls = _feed(
        Update(update_id=30, message=_message(text=BTN_SERVICES)),
        prepare=prepare,
        after=after,
    )
    assert writes == []
    assert seen["state"] == BookFlow.note.state
    assert isinstance(calls[0], SendMessage)
    assert calls[0].text == NO_SERVICES


def test_book_button_keeps_an_existing_draft(monkeypatch) -> None:
    settings = Settings(
        bot_token=TOKEN,
        admin_ids="",
        database_url="postgresql+asyncpg://booking:booking@localhost/booking_test",
    )
    writes: list[object] = []

    async def existing_draft(*_args, **_kwargs):
        return object()

    async def reject_write(*_args, **_kwargs):
        writes.append("write")
        raise AssertionError("booking draft was rewritten")

    monkeypatch.setattr("booking_bot.handlers.customer.booking.get_sessionmaker", _factory)
    monkeypatch.setattr("booking_bot.handlers.customer.booking.load_settings", lambda: settings)
    monkeypatch.setattr("booking_bot.handlers.customer.booking.get_draft", existing_draft)
    monkeypatch.setattr("booking_bot.handlers.customer.booking.touch_draft", reject_write)
    calls = _feed(Update(update_id=31, message=_message(text=BTN_BOOK)))
    assert writes == []
    assert isinstance(calls[0], SendMessage)
    assert calls[0].text == UNFINISHED


def test_non_admin_cannot_open_the_desk(monkeypatch) -> None:
    monkeypatch.setattr(
        "booking_bot.handlers.admin.panel.load_settings",
        lambda: Settings(
            bot_token=TOKEN,
            admin_ids="1",
            database_url="postgresql+asyncpg://booking:booking@localhost/booking_test",
        ),
    )
    calls = _feed(Update(update_id=3, message=_message(text="/admin")))
    assert calls[-1].text == "Ця дія недоступна."


def test_unknown_callback_is_left_unanswered() -> None:
    callback = CallbackQuery(
        id="cb-other",
        from_user=_user(),
        chat_instance="chat-instance",
        data="start:unknown",
        message=_message(text=START),
    )
    assert _feed(Update(update_id=99, callback_query=callback)) == []
