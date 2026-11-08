from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from booking_bot.db.models import BookingStatus
from booking_bot.services.admin_edit import break_overlaps_booking

KYIV = ZoneInfo("Europe/Kyiv")


class _Booking:
    def __init__(self, start: datetime, end: datetime, master_id: int = 1) -> None:
        self.starts_at = start
        self.ends_at = end
        self.master_id = master_id
        self.status = BookingStatus.CONFIRMED


def test_break_overlap_uses_half_open_local_time() -> None:
    overlapping = _Booking(
        datetime(2026, 11, 2, 12, 30, tzinfo=UTC),
        datetime(2026, 11, 2, 13, 0, tzinfo=UTC),
    )
    ending_at_break_start = _Booking(
        datetime(2026, 11, 2, 11, 0, tzinfo=UTC),
        datetime(2026, 11, 2, 12, 0, tzinfo=UTC),
    )
    starting_when_break_ends = _Booking(
        datetime(2026, 11, 2, 13, 0, tzinfo=UTC),
        datetime(2026, 11, 2, 13, 30, tzinfo=UTC),
    )
    other_master = _Booking(
        datetime(2026, 11, 2, 12, 30, tzinfo=UTC),
        datetime(2026, 11, 2, 13, 0, tzinfo=UTC),
        master_id=2,
    )
    window = (0, time(14, 0), time(15, 0))
    assert break_overlaps_booking(overlapping, *window, None, KYIV) is True
    assert break_overlaps_booking(ending_at_break_start, *window, None, KYIV) is False
    assert break_overlaps_booking(starting_when_break_ends, *window, None, KYIV) is False
    assert break_overlaps_booking(other_master, *window, 1, KYIV) is False
    assert break_overlaps_booking(other_master, *window, None, KYIV) is True


def test_adding_a_break_warns_and_keeps_the_booking(monkeypatch) -> None:
    import asyncio

    from aiogram import Bot
    from aiogram.client.session.base import BaseSession
    from aiogram.methods import AnswerCallbackQuery, SendMessage
    from aiogram.types import CallbackQuery, Chat, Message, Update, User

    from booking_bot.bot import build_dispatcher
    from booking_bot.config import Settings
    from booking_bot.db.models import BookingStatus as Status

    booking = _Booking(
        datetime(2026, 11, 2, 12, 30, tzinfo=UTC),
        datetime(2026, 11, 2, 13, 0, tzinfo=UTC),
    )
    booking.service_name = "Стрижка"
    booking.master_name = "Taras Melnyk"
    booking.status = Status.CONFIRMED
    added: list[object] = []

    class _Rows:
        def __iter__(self):
            return iter((booking,))

    class _Begin:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class _Store:
        def begin(self):
            return _Begin()

        def add(self, obj) -> None:
            added.append(obj)

        async def scalars(self, *_args, **_kwargs):
            return _Rows()

        async def get(self, *_args, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class _Recorder(BaseSession):
        def __init__(self) -> None:
            super().__init__()
            self.calls = []

        async def close(self) -> None:
            return None

        async def stream_content(self, *_args, **_kwargs):
            yield b""

        async def make_request(self, bot, method, timeout=None):
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

    settings = Settings(
        bot_token="123456789:" + ("A" * 35),
        admin_ids="1",
        database_url="postgresql+asyncpg://booking:booking@localhost/booking_test",
        timezone="Europe/Kyiv",
    )
    monkeypatch.setattr("booking_bot.handlers.admin.panel.load_settings", lambda: settings)
    monkeypatch.setattr(
        "booking_bot.handlers.admin.panel.get_sessionmaker",
        lambda: (lambda: _Store()),
    )
    user = User(id=1, is_bot=False, first_name="Ada")
    recorder = _Recorder()
    dispatcher = build_dispatcher()

    def callback(data: str, update_id: int) -> Update:
        return Update(
            update_id=update_id,
            callback_query=CallbackQuery(
                id=f"cb-{update_id}",
                from_user=user,
                chat_instance="chat",
                data=data,
                message=Message(
                    message_id=1,
                    date=datetime(2026, 11, 7, tzinfo=UTC),
                    chat=Chat(id=1, type="private"),
                    from_user=user,
                    text="breaks",
                ),
            ),
        )

    def text(body: str, update_id: int) -> Update:
        return Update(
            update_id=update_id,
            message=Message(
                message_id=update_id,
                date=datetime(2026, 11, 7, tzinfo=UTC),
                chat=Chat(id=1, type="private"),
                from_user=user,
                text=body,
            ),
        )

    async def go() -> None:
        async with Bot(token=settings.bot_token, session=recorder) as bot:
            await dispatcher.feed_update(bot, callback("adm:br:add", 1))
            await dispatcher.feed_update(bot, text("Пн 14:00-15:00", 2))
            await dispatcher.feed_update(bot, callback("adm:yes", 3))

    asyncio.run(go())
    sent = [call.text for call in recorder.calls if isinstance(call, SendMessage)]
    assert any(item and "не скасує" in item and "Стрижка" in item for item in sent)
    assert booking.status == Status.CONFIRMED
    assert added and added[0].weekday == 0
