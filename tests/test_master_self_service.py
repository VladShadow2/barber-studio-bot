import asyncio
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, Update, User

from booking_bot.bot import build_dispatcher
from booking_bot.config import Settings
from booking_bot.db.models import BookingStatus
from booking_bot.keyboards.start import start_keyboard
from booking_bot.services.availability import Calendar, DayOverride, Window, slots_for_master
from booking_bot.services.master_schedule import LinkedMaster
from booking_bot.services.schedule_guard import (
    BreakEdit,
    ExceptionEdit,
    HoursEdit,
    conflicting_visits,
)
from booking_bot.texts import BTN_ADMIN, BTN_BOOK, BTN_HOURS, BTN_SCHEDULE, NOT_AVAILABLE

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)
MONDAY = date(2026, 11, 2)
TOKEN = "123456789:" + ("A" * 35)


def _salon() -> Calendar:
    calendar = Calendar()
    for weekday in range(6):
        calendar.salon[weekday] = Window(time(10, 0), time(19, 0))
    calendar.salon[6] = None
    return calendar


def _visit(start: datetime, minutes: int = 45, master_id: int = 1, status=BookingStatus.CONFIRMED):
    return SimpleNamespace(
        starts_at=start,
        ends_at=start + timedelta(minutes=minutes),
        master_id=master_id,
        status=status,
        service_name="Стрижка",
    )


def test_hours_conflict_follows_the_existing_slot_rules() -> None:
    calendar = _salon()
    start = datetime(2026, 11, 2, 10, 0, tzinfo=KYIV)
    visit = _visit(start)
    assert start in slots_for_master(calendar, 1, MONDAY, 45, 30, KYIV, NOW, 0)

    blocked = conflicting_visits(
        calendar, [visit], 1, HoursEdit(0, False, time(12, 0), time(18, 0)), KYIV, NOW
    )
    assert blocked == [visit]
    assert visit.status is BookingStatus.CONFIRMED
    assert calendar.master_hours == {}
    assert start in slots_for_master(calendar, 1, MONDAY, 45, 30, KYIV, NOW, 0)

    allowed = conflicting_visits(
        calendar, [visit], 1, HoursEdit(0, False, time(10, 0), time(16, 0)), KYIV, NOW
    )
    assert allowed == []
    calendar.master_hours[1] = {0: Window(time(10, 0), time(16, 0))}
    assert start in slots_for_master(calendar, 1, MONDAY, 45, 30, KYIV, NOW, 0)

    calendar.master_hours[1] = {0: None}
    assert slots_for_master(calendar, 1, MONDAY, 45, 30, KYIV, NOW, 0) == []


def test_conflicts_ignore_other_masters_cancelled_visits_and_untouched_days() -> None:
    calendar = _salon()
    monday = _visit(datetime(2026, 11, 2, 10, 0, tzinfo=KYIV))
    tuesday = _visit(datetime(2026, 11, 3, 10, 0, tzinfo=KYIV))
    cancelled = _visit(
        datetime(2026, 11, 2, 10, 0, tzinfo=KYIV), status=BookingStatus.CANCELLED
    )
    other = _visit(datetime(2026, 11, 2, 10, 0, tzinfo=KYIV), master_id=2)
    visits = [monday, tuesday, cancelled, other]
    assert conflicting_visits(calendar, visits, 1, HoursEdit(0, True, None, None), KYIV, NOW) == [
        monday
    ]
    assert (
        conflicting_visits(calendar, visits, 1, HoursEdit(1, True, None, None), KYIV, NOW)
        == [tuesday]
    )


def test_break_and_day_off_conflicts_stay_with_the_master() -> None:
    calendar = _salon()
    visit = _visit(datetime(2026, 11, 2, 14, 30, tzinfo=KYIV))
    adjacent = _visit(datetime(2026, 11, 2, 13, 0, tzinfo=KYIV), minutes=60)
    overlap = conflicting_visits(
        calendar, [visit], 1, BreakEdit(0, time(14, 0), time(15, 0)), KYIV, NOW
    )
    assert overlap == [visit]
    assert conflicting_visits(
        calendar, [adjacent], 1, BreakEdit(0, time(14, 0), time(15, 0)), KYIV, NOW
    ) == []
    assert conflicting_visits(
        calendar, [visit], 2, BreakEdit(0, time(14, 0), time(15, 0)), KYIV, NOW
    ) == []

    closed = conflicting_visits(
        calendar,
        [visit],
        1,
        ExceptionEdit(MONDAY, True, None, None),
        KYIV,
        NOW,
    )
    assert closed == [visit]
    later = _visit(datetime(2026, 11, 3, 14, 30, tzinfo=KYIV))
    assert conflicting_visits(
        calendar, [later], 1, ExceptionEdit(MONDAY, True, None, None), KYIV, NOW
    ) == []


def test_removing_a_wider_day_off_is_blocked_when_the_weekly_hours_do_not_cover_it() -> None:
    calendar = _salon()
    calendar.master_hours[1] = {0: Window(time(12, 0), time(18, 0))}
    calendar.master_exceptions[1] = {MONDAY: DayOverride(False, time(10, 0), time(18, 0))}
    early = _visit(datetime(2026, 11, 2, 10, 0, tzinfo=KYIV))
    assert conflicting_visits(
        calendar, [early], 1, ExceptionEdit(MONDAY, True, None, None, remove=True), KYIV, NOW
    ) == [early]
    noon = _visit(datetime(2026, 11, 2, 12, 0, tzinfo=KYIV))
    assert conflicting_visits(
        calendar, [noon], 1, ExceptionEdit(MONDAY, True, None, None, remove=True), KYIV, NOW
    ) == []


def test_linked_master_keyboard_adds_schedule_buttons_without_admin() -> None:
    rows = start_keyboard(include_schedule=True).keyboard
    labels = [button.text for row in rows for button in row]
    assert BTN_SCHEDULE in labels
    assert BTN_HOURS in labels
    assert BTN_ADMIN not in labels


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
                chat=Chat(id=7, type="private"),
                from_user=User(id=7, is_bot=False, first_name="Taras"),
                text=method.text,
            )
        if isinstance(method, AnswerCallbackQuery):
            return True
        raise AssertionError(type(method).__name__)


def _user() -> User:
    return User(id=7, is_bot=False, first_name="Taras")


def _message(text: str, update_id: int) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=datetime(2026, 11, 7, tzinfo=UTC),
            chat=Chat(id=7, type="private"),
            from_user=_user(),
            text=text,
        ),
    )


def _callback(data: str, update_id: int) -> Update:
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=f"cb-{update_id}",
            from_user=_user(),
            chat_instance="chat",
            data=data,
            message=Message(
                message_id=1,
                date=datetime(2026, 11, 7, tzinfo=UTC),
                chat=Chat(id=7, type="private"),
                from_user=_user(),
                text="графік",
            ),
        ),
    )


def _settings() -> Settings:
    return Settings(
        bot_token=TOKEN,
        admin_ids="1",
        database_url="postgresql+asyncpg://booking:booking@localhost/booking_test",
        timezone="Europe/Kyiv",
    )


def _patch_master(monkeypatch, conflicts):
    saved = {"hours": [], "breaks": [], "deleted": []}

    async def master(_user_id: int):
        return LinkedMaster(5, "Taras Melnyk")

    async def home(_master):
        return "HOME"

    async def day(_master_id: int, _weekday: int):
        return None

    async def preview(_master_id: int, _change, _now=None):
        return list(conflicts)

    async def commit_hours(master_id, weekday, closed, open_time, close_time):
        saved["hours"].append((master_id, weekday, closed, open_time, close_time))

    async def commit_break(master_id, weekday, start, end):
        saved["breaks"].append((master_id, weekday, start, end))

    async def missing(*_args, **_kwargs):
        return None

    async def not_deleted(*_args, **_kwargs):
        saved["deleted"].append("called")
        return False

    monkeypatch.setattr("booking_bot.handlers.master.schedule.find_master", master)
    monkeypatch.setattr("booking_bot.handlers.master.schedule.home_text", home)
    monkeypatch.setattr("booking_bot.handlers.master.schedule.day_hours", day)
    monkeypatch.setattr("booking_bot.handlers.master.schedule.preview_conflicts", preview)
    monkeypatch.setattr("booking_bot.handlers.master.schedule.commit_hours", commit_hours)
    monkeypatch.setattr("booking_bot.handlers.master.schedule.commit_break", commit_break)
    monkeypatch.setattr("booking_bot.handlers.master.schedule.break_for_master", missing)
    monkeypatch.setattr("booking_bot.handlers.master.schedule.commit_break_delete", not_deleted)
    monkeypatch.setattr("booking_bot.handlers.master.schedule.load_settings", _settings)
    monkeypatch.setattr("booking_bot.handlers.admin.panel.load_settings", _settings)
    return saved


def _run(updates: list[Update]) -> list:
    recorder = _Recorder()
    dispatcher = build_dispatcher()

    async def go() -> None:
        async with Bot(token=TOKEN, session=recorder) as bot:
            for update in updates:
                await dispatcher.feed_update(bot, update)

    asyncio.run(go())
    return recorder.calls


def _texts(calls) -> list[str]:
    return [call.text for call in calls if isinstance(call, SendMessage) and call.text]


def test_unlinked_user_cannot_open_a_master_schedule(monkeypatch) -> None:
    async def nobody(_user_id: int):
        return None

    monkeypatch.setattr("booking_bot.handlers.master.schedule.find_master", nobody)
    texts = _texts(_run([_message(BTN_HOURS, 1)]))
    assert texts == [NOT_AVAILABLE]


def test_master_can_save_own_hours_and_cannot_use_admin_tools(monkeypatch) -> None:
    saved = _patch_master(monkeypatch, [])
    calls = _run(
        [
            _callback("mst:hw:0", 1),
            _message("10:00-19:00", 2),
            _callback("mst:yes", 3),
            _callback("adm:hours", 4),
            _message(BTN_ADMIN, 5),
        ]
    )
    assert saved["hours"] == [(5, 0, False, time(10, 0), time(19, 0))]
    assert any("Графік майстра збережено" in text for text in _texts(calls))
    assert _texts(calls).count(NOT_AVAILABLE) == 2
    keyboards = [call.reply_markup for call in calls if isinstance(call, SendMessage)]
    assert any(isinstance(markup, InlineKeyboardMarkup) for markup in keyboards)
    for markup in keyboards:
        if not isinstance(markup, InlineKeyboardMarkup):
            continue
        for row in markup.inline_keyboard:
            for button in row:
                assert button.callback_data is None or button.callback_data.startswith("mst:")


def test_conflicting_hours_and_breaks_are_blocked(monkeypatch) -> None:
    visit = _visit(datetime(2026, 11, 2, 10, 0, tzinfo=KYIV), master_id=5)
    saved = _patch_master(monkeypatch, [visit])
    texts = _texts(
        _run(
            [
                _callback("mst:hw:0", 1),
                _message("закрито", 2),
                _callback("mst:br:add", 3),
                _message("Пн 14:00-15:00", 4),
            ]
        )
    )
    assert saved["hours"] == []
    assert saved["breaks"] == []
    assert visit.status is BookingStatus.CONFIRMED
    blocked = [text for text in texts if "не збережено" in text and "Стрижка" in text]
    assert len(blocked) == 2
    assert all("не скасовано" in text for text in blocked)


def test_master_cannot_delete_someone_elses_break(monkeypatch) -> None:
    saved = _patch_master(monkeypatch, [])
    texts = _texts(_run([_callback("mst:brx:3", 1)]))
    assert texts == [NOT_AVAILABLE]
    assert saved["deleted"] == []


def test_menu_button_and_cancel_leave_master_editing_without_saving(monkeypatch) -> None:
    saved = _patch_master(monkeypatch, [])
    opened: list[str] = []

    async def begin(message, state):
        opened.append(message.text or "")
        opened.append(str(await state.get_state()))

    monkeypatch.setattr("booking_bot.handlers.customer.booking.begin_booking", begin)
    texts = _texts(
        _run(
            [
                _callback("mst:hw:2", 1),
                _message(BTN_BOOK, 2),
                _callback("mst:hw:2", 3),
                _message("/cancel", 4),
            ]
        )
    )
    assert saved["hours"] == []
    assert opened == [BTN_BOOK, "None"]
    assert any("не збережено" in text for text in texts)
    assert all("Графік майстра збережено" not in text for text in texts)
