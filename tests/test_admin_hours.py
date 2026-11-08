import asyncio
from datetime import UTC, datetime, time

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, SendMessage, TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from booking_bot.bot import build_dispatcher
from booking_bot.config import Settings
from booking_bot.db.models import BusinessHours, MasterHours
from booking_bot.texts import NOT_AVAILABLE

TOKEN = "123456789:" + ("A" * 35)


class _Begin:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _Session:
    def __init__(self) -> None:
        self.salon = {
            2: BusinessHours(
                weekday=2,
                is_closed=False,
                open_time=time(10, 0),
                close_time=time(21, 0),
            ),
            6: BusinessHours(weekday=6, is_closed=True, open_time=None, close_time=None),
        }
        self.master = {
            (5, 0): MasterHours(
                master_id=5,
                weekday=0,
                is_closed=False,
                open_time=time(12, 0),
                close_time=time(18, 0),
            )
        }

    def begin(self):
        return _Begin()

    def add(self, obj) -> None:
        if isinstance(obj, BusinessHours):
            self.salon[obj.weekday] = obj
        elif isinstance(obj, MasterHours):
            self.master[(obj.master_id, obj.weekday)] = obj

    async def get(self, model, ident):
        if model is BusinessHours:
            return self.salon.get(ident)
        if model is MasterHours:
            return self.master.get(ident)
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
        if isinstance(method, SendMessage):
            return Message(
                message_id=len(self.calls),
                date=datetime(2026, 11, 7, tzinfo=UTC),
                chat=Chat(id=1, type="private"),
                from_user=User(id=1, is_bot=False, first_name="Ada"),
                text=method.text,
            )
        if isinstance(method, AnswerCallbackQuery):
            return True
        raise AssertionError(type(method).__name__)


def _user(user_id: int = 1) -> User:
    return User(id=user_id, is_bot=False, first_name="Ada")


def _update_callback(data: str, update_id: int, user_id: int = 1) -> Update:
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=f"cb-{update_id}",
            from_user=_user(user_id),
            chat_instance="chat",
            data=data,
            message=Message(
                message_id=1,
                date=datetime(2026, 11, 7, tzinfo=UTC),
                chat=Chat(id=user_id, type="private"),
                from_user=_user(user_id),
                text="hours",
            ),
        ),
    )


def _update_message(text: str, update_id: int, user_id: int = 1) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=datetime(2026, 11, 7, tzinfo=UTC),
            chat=Chat(id=user_id, type="private"),
            from_user=_user(user_id),
            text=text,
        ),
    )


def _texts(calls: list[TelegramMethod]) -> list[str]:
    return [call.text for call in calls if isinstance(call, SendMessage) and call.text is not None]


def _run(
    monkeypatch,
    updates: list[Update],
    session: _Session | None = None,
) -> tuple[_Session, list[str]]:
    store = session or _Session()
    settings = Settings(
        bot_token=TOKEN,
        admin_ids="1",
        database_url="postgresql+asyncpg://booking:booking@localhost/booking_test",
    )
    monkeypatch.setattr("booking_bot.handlers.admin.panel.load_settings", lambda: settings)
    monkeypatch.setattr(
        "booking_bot.handlers.admin.panel.get_sessionmaker",
        lambda: (lambda: store),
    )
    recorder = RecordingSession()
    dispatcher = build_dispatcher()

    async def go() -> None:
        async with Bot(token=TOKEN, session=recorder) as bot:
            for update in updates:
                await dispatcher.feed_update(bot, update)

    asyncio.run(go())
    return store, _texts(recorder.calls)


def test_salon_hours_prompt_shows_the_saved_day(monkeypatch) -> None:
    _store, texts = _run(
        monkeypatch,
        [_update_callback("adm:hw:2", 1), _update_callback("adm:hw:6", 2)],
    )
    assert "📅 Середа" in texts[0]
    assert "Поточний графік: 10:00–21:00" in texts[0]
    assert "ГГ:ХХ-ГГ:ХХ" in texts[0]
    assert "10:00-19:00" not in texts[0]
    assert "📅 Неділя" in texts[1]
    assert "Поточний графік: закрито" in texts[1]


def test_saving_salon_hours_confirms_and_redisplays_the_new_value(monkeypatch) -> None:
    store, texts = _run(
        monkeypatch,
        [
            _update_callback("adm:hw:2", 1),
            _update_message("11:00-18:00", 2),
            _update_callback("adm:yes", 3),
            _update_callback("adm:hw:2", 4),
        ],
    )
    assert "Графік збережено: 11:00–18:00" in texts[2]
    assert "Поточний графік: 11:00–18:00" in texts[3]
    assert store.salon[2].open_time == time(11, 0)
    assert store.salon[2].close_time == time(18, 0)
    assert store.salon[2].is_closed is False


def test_cancel_leaves_saved_salon_hours_unchanged(monkeypatch) -> None:
    store, texts = _run(
        monkeypatch,
        [
            _update_callback("adm:hw:2", 1),
            _update_message("12:00-16:00", 2),
            _update_callback("adm:no", 3),
            _update_callback("adm:hw:2", 4),
        ],
    )
    assert store.salon[2].open_time == time(10, 0)
    assert store.salon[2].close_time == time(21, 0)
    assert "Поточний графік: 10:00–21:00" in texts[-1]
    assert "12:00–16:00" not in texts[-1]


def test_master_hours_show_saved_values_and_a_missing_day(monkeypatch) -> None:
    store, texts = _run(
        monkeypatch,
        [
            _update_callback("adm:mhw:5:0", 1),
            _update_message("13:00-17:00", 2),
            _update_callback("adm:yes", 3),
            _update_callback("adm:mhw:5:0", 4),
            _update_callback("adm:mhw:5:1", 5),
        ],
    )
    assert "📅 Понеділок" in texts[0]
    assert "Поточний графік: 12:00–18:00" in texts[0]
    assert "Графік майстра збережено: 13:00–17:00" in texts[2]
    assert "Поточний графік: 13:00–17:00" in texts[3]
    assert "Поточний графік: не збережено" in texts[4]
    assert store.master[(5, 0)].open_time == time(13, 0)


def test_hours_callbacks_reject_invalid_values_and_non_admins(monkeypatch) -> None:
    store, texts = _run(
        monkeypatch,
        [_update_callback("adm:hw:9", 1), _update_callback("adm:hw:2", 2, user_id=9)],
    )
    assert texts == [NOT_AVAILABLE, NOT_AVAILABLE]
    assert store.salon[2].open_time == time(10, 0)
