import asyncio
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.base import StorageKey
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage, TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from booking_bot.bot import build_dispatcher
from booking_bot.config import Settings
from booking_bot.db.models import Master
from booking_bot.handlers.admin.panel import AdminFlow
from booking_bot.keyboards.admin import master_services_keyboard
from booking_bot.texts import (
    ASSIGN_SERVICES,
    NOT_AVAILABLE,
    SERVICES_DISCARDED,
    SERVICES_SAVED,
)

TOKEN = "123456789:" + ("A" * 35)


class _Begin:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _Master:
    def __init__(self, master_id: int) -> None:
        self.id = master_id
        self.name = "Taras Melnyk"
        self.description = "Демо"
        self.is_active = True
        self.telegram_user_id = None


class _Service:
    def __init__(self, service_id: int, name: str) -> None:
        self.id = service_id
        self.name = name


class _Session:
    def __init__(self, masters: dict[int, _Master]) -> None:
        self.masters = masters

    def begin(self):
        return _Begin()

    async def get(self, model, ident):
        if model is Master:
            return self.masters.get(ident)
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class RecordingSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod] = []

    async def close(self) -> None:
        return None

    async def stream_content(self, *_args, **_kwargs):
        yield b""

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,
    ) -> TelegramType:
        self.calls.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            return _sent(message_id=len(self.calls), text=method.text)
        if isinstance(method, AnswerCallbackQuery):
            return True
        raise AssertionError(f"Unexpected API call: {type(method).__name__}")


def _user(user_id: int = 1) -> User:
    return User(id=user_id, is_bot=False, first_name="Ada")


def _sent(*, message_id: int, text: str | None) -> Message:
    return Message(
        message_id=message_id,
        date=datetime(2026, 11, 7, tzinfo=UTC),
        chat=Chat(id=1, type="private"),
        from_user=_user(),
        text=text,
    )


def _message(user_id: int = 1) -> Message:
    return Message(
        message_id=1,
        date=datetime(2026, 11, 7, tzinfo=UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=_user(user_id),
        text="admin",
    )


def _update(data: str, update_id: int, user_id: int = 1) -> Update:
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=f"cb-{update_id}",
            from_user=_user(user_id),
            chat_instance="chat",
            data=data,
            message=_message(user_id),
        ),
    )


def _labels(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def _visible(calls: list[TelegramMethod]) -> list[SendMessage | EditMessageText]:
    return [call for call in calls if isinstance(call, (SendMessage, EditMessageText))]


def _install(monkeypatch, masters: dict[int, _Master] | None = None) -> list[tuple[int, list[int]]]:
    writes: list[tuple[int, list[int]]] = []
    settings = Settings(
        bot_token=TOKEN,
        admin_ids="1",
        database_url="postgresql+asyncpg://booking:booking@localhost/booking_test",
    )

    async def services_query(_session):
        return [_Service(1, "Стрижка"), _Service(2, "Борода")]

    async def assigned_query(_session, _master_id):
        return [1]

    async def remember(_session, master_id, service_ids):
        writes.append((master_id, list(service_ids)))

    monkeypatch.setattr("booking_bot.handlers.admin.panel.load_settings", lambda: settings)
    monkeypatch.setattr(
        "booking_bot.handlers.admin.panel.get_sessionmaker",
        lambda: (lambda: _Session(masters if masters is not None else {1: _Master(1)})),
    )
    monkeypatch.setattr("booking_bot.handlers.admin.panel.list_services", services_query)
    monkeypatch.setattr("booking_bot.handlers.admin.panel.assigned_service_ids", assigned_query)
    monkeypatch.setattr("booking_bot.handlers.admin.panel.link_master_services", remember)
    return writes


def _run(updates: list[Update], *, prepare=None) -> tuple[list[TelegramMethod], dict]:
    session = RecordingSession()
    dispatcher = build_dispatcher()
    found: dict = {}

    async def go() -> None:
        async with Bot(token=TOKEN, session=session) as bot:
            if prepare is not None:
                await prepare(dispatcher, bot)
            for update in updates:
                await dispatcher.feed_update(bot, update)
            key = StorageKey(bot_id=bot.id, chat_id=1, user_id=1)
            found["state"] = await dispatcher.storage.get_state(key)
            found["data"] = await dispatcher.storage.get_data(key)

    asyncio.run(go())
    return session.calls, found


def test_service_keyboard_marks_current_assignments() -> None:
    markup = master_services_keyboard([(1, "Стрижка"), (2, "Борода")], {2})
    assert _labels(markup) == ["⬜ Стрижка", "✅ Борода", "💾 Зберегти", "❌ Скасувати"]
    assert markup.inline_keyboard[1][0].callback_data == "adm:mls:2"
    assert markup.inline_keyboard[2][0].callback_data == "adm:mly"
    assert markup.inline_keyboard[3][0].callback_data == "adm:mln"


def test_toggle_updates_the_keyboard_without_writing(monkeypatch) -> None:
    writes = _install(monkeypatch)
    calls, found = _run([_update("adm:ml:1", 1), _update("adm:mls:2", 2)])
    visible = _visible(calls)
    assert writes == []
    assert isinstance(visible[0], SendMessage)
    assert visible[0].text == ASSIGN_SERVICES
    assert _labels(visible[0].reply_markup)[:2] == ["✅ Стрижка", "⬜ Борода"]
    assert isinstance(visible[1], EditMessageText)
    assert _labels(visible[1].reply_markup)[:2] == ["✅ Стрижка", "✅ Борода"]
    assert found["data"]["service_ids"] == [1, 2]
    assert found["state"] == AdminFlow.links.state


def test_save_writes_the_pending_selection_and_returns_to_the_master(monkeypatch) -> None:
    writes = _install(monkeypatch)
    calls, found = _run(
        [
            _update("adm:ml:1", 1),
            _update("adm:mls:2", 2),
            _update("adm:mls:1", 3),
            _update("adm:mly", 4),
        ]
    )
    assert writes == [(1, [2])]
    sent = _visible(calls)[-1]
    assert isinstance(sent, SendMessage)
    assert sent.text is not None
    assert sent.text.startswith(SERVICES_SAVED)
    assert "Taras Melnyk" in sent.text
    assert "💈 Призначити послуги" in _labels(sent.reply_markup)
    assert found["state"] is None


def test_cancel_discards_pending_service_changes(monkeypatch) -> None:
    writes = _install(monkeypatch)
    calls, found = _run(
        [_update("adm:ml:1", 1), _update("adm:mls:2", 2), _update("adm:mln", 3)]
    )
    assert writes == []
    sent = _visible(calls)[-1]
    assert isinstance(sent, SendMessage)
    assert sent.text is not None
    assert sent.text.startswith(SERVICES_DISCARDED)
    assert "💈 Призначити послуги" in _labels(sent.reply_markup)
    assert found["state"] is None


def test_service_link_callbacks_require_an_admin(monkeypatch) -> None:
    writes = _install(monkeypatch)

    async def prepare(dispatcher, bot: Bot) -> None:
        key = StorageKey(bot_id=bot.id, chat_id=9, user_id=9)
        await dispatcher.storage.set_state(key, AdminFlow.links)
        await dispatcher.storage.set_data(
            key,
            {"kind": "master_links", "master_id": 1, "service_ids": [1]},
        )

    calls, _found = _run(
        [_update(data, index, user_id=9) for index, data in enumerate(
            ("adm:ml:1", "adm:mls:2", "adm:mly", "adm:mln"),
            start=1,
        )],
        prepare=prepare,
    )
    assert writes == []
    sent = [call for call in calls if isinstance(call, SendMessage)]
    assert [call.text for call in sent] == [NOT_AVAILABLE] * 4


def test_outdated_service_callbacks_do_not_write(monkeypatch) -> None:
    writes = _install(monkeypatch)

    async def prepare(dispatcher, bot: Bot) -> None:
        key = StorageKey(bot_id=bot.id, chat_id=1, user_id=1)
        await dispatcher.storage.set_state(key, AdminFlow.confirm)
        await dispatcher.storage.set_data(key, {"kind": "master_toggle", "master_id": 1})

    calls, found = _run(
        [
            _update(data, index)
            for index, data in enumerate(("adm:mls:2", "adm:mly", "adm:mln", "adm:mls:nope", "adm:ml:0"), start=1)  # noqa: E501
        ],
        prepare=prepare,
    )
    assert writes == []
    sent = [call for call in calls if isinstance(call, SendMessage)]
    assert [call.text for call in sent] == [NOT_AVAILABLE] * 5
    assert found["state"] == AdminFlow.confirm.state


def test_save_rejects_a_missing_master_without_writing(monkeypatch) -> None:
    writes = _install(monkeypatch, masters={})

    async def prepare(dispatcher, bot: Bot) -> None:
        key = StorageKey(bot_id=bot.id, chat_id=1, user_id=1)
        await dispatcher.storage.set_state(key, AdminFlow.links)
        await dispatcher.storage.set_data(
            key,
            {"kind": "master_links", "master_id": 1, "service_ids": [2]},
        )

    calls, found = _run([_update("adm:mly", 1)], prepare=prepare)
    assert writes == []
    sent = [call for call in calls if isinstance(call, SendMessage)]
    assert sent[-1].text == NOT_AVAILABLE
    assert found["state"] is None
