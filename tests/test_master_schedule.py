from datetime import UTC, datetime

import pytest

from booking_bot.config import Settings
from booking_bot.db.models import Booking, BookingStatus, Customer, Master
from booking_bot.handlers.customer.menu import show_schedule_message
from booking_bot.texts import NOT_AVAILABLE


class _Rows:
    def __init__(self, rows) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _Session:
    def __init__(self, master, bookings=(), customers=()) -> None:
        self.master = master
        self.bookings = bookings
        self.customers = customers
        self.scalar_calls = 0

    async def scalar(self, *_args, **_kwargs):
        self.scalar_calls += 1
        return self.master

    async def scalars(self, *_args, **_kwargs):
        if self.scalar_calls == 1 and not hasattr(self, "listed"):
            self.listed = True
            return _Rows(self.bookings)
        return _Rows(self.customers)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _Message:
    def __init__(self, user_id: int) -> None:
        self.from_user = type("User", (), {"id": user_id})()
        self.sent: list[str] = []

    async def answer(self, text: str, reply_markup=None) -> None:
        self.sent.append(text)


def _install(monkeypatch, session: _Session) -> None:
    monkeypatch.setattr(
        "booking_bot.handlers.customer.menu.load_settings",
        lambda: Settings(
            bot_token="123456789:" + ("A" * 35),
            admin_ids="1",
            database_url="postgresql+asyncpg://booking:booking@localhost/booking_test",
            timezone="Europe/Kyiv",
        ),
    )
    monkeypatch.setattr(
        "booking_bot.handlers.customer.menu.get_sessionmaker",
        lambda: (lambda: session),
    )


@pytest.mark.asyncio
async def test_master_sees_only_the_loaded_personal_schedule(monkeypatch) -> None:
    master = Master(id=2, name="Taras Melnyk", telegram_user_id=7)
    booking = Booking(
        id=8,
        customer_id=3,
        master_id=2,
        service_id=1,
        service_name="Стрижка",
        service_duration_minutes=45,
        service_price_uah=400,
        master_name="Taras Melnyk",
        starts_at=datetime(2026, 11, 10, 8, 0, tzinfo=UTC),
        ends_at=datetime(2026, 11, 10, 8, 45, tzinfo=UTC),
        status=BookingStatus.CONFIRMED,
        customer_note="Коротше з боків",
    )
    customer = Customer(id=3, telegram_id=15, display_name="Іван", username="ivan")
    session = _Session(master, [booking], [customer])
    _install(monkeypatch, session)
    message = _Message(7)
    await show_schedule_message(message)
    text = message.sent[-1]
    assert "Taras Melnyk" in text
    assert "Стрижка" in text
    assert "Іван" in text
    assert "@ivan" in text
    assert "Коротше з боків" in text


@pytest.mark.asyncio
async def test_unlinked_user_cannot_open_a_master_schedule(monkeypatch) -> None:
    session = _Session(None)
    _install(monkeypatch, session)
    message = _Message(99)
    await show_schedule_message(message)
    assert message.sent == [NOT_AVAILABLE]
    assert session.scalar_calls == 1
