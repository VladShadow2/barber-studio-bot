from datetime import UTC, datetime

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage

from booking_bot.config import Settings
from booking_bot.db.models import Booking, BookingStatus, Customer, Master
from booking_bot.services.booking import (
    Outgoing,
    cancellation_notices,
    deliver,
    master_needs_separate_notice,
)


def test_master_notification_is_skipped_when_it_would_duplicate_an_admin() -> None:
    assert master_needs_separate_notice(5, 7, (5, 8)) is False
    assert master_needs_separate_notice(9, 7, (5,)) is True
    assert master_needs_separate_notice(7, 7, ()) is False
    assert master_needs_separate_notice(None, 7, (5,)) is False


class _Session:
    def __init__(self, rows: dict) -> None:
        self.rows = rows

    async def get(self, model, ident):
        return self.rows.get(model)


def _settings() -> Settings:
    return Settings(
        bot_token="123456789:" + ("A" * 35),
        admin_ids="1",
        database_url="postgresql+asyncpg://booking:booking@localhost/booking_test",
        timezone="Europe/Kyiv",
    )


@pytest.mark.asyncio
async def test_cancellation_notifies_only_the_assigned_master() -> None:
    booking = Booking(
        id=3,
        customer_id=1,
        master_id=2,
        service_id=1,
        service_name="Стрижка",
        service_duration_minutes=30,
        service_price_uah=100,
        master_name="Taras Melnyk",
        starts_at=datetime(2026, 11, 2, 12, 0, tzinfo=UTC),
        ends_at=datetime(2026, 11, 2, 12, 30, tzinfo=UTC),
        status=BookingStatus.CANCELLED,
    )
    master = Master(id=2, name="Taras Melnyk", telegram_user_id=40)
    customer = Customer(id=1, telegram_id=7, display_name="Олена", username="olena")
    session = _Session({Booking: booking, Master: master, Customer: customer})
    notices = await cancellation_notices(session, 3, actor_id=7, settings=_settings())
    assert [item.telegram_id for item in notices] == [40]
    assert "Олена" in notices[0].text
    assert "Стрижка" in notices[0].text
    silent = await cancellation_notices(session, 3, actor_id=40, settings=_settings())
    assert silent == ()


@pytest.mark.asyncio
async def test_delivery_failure_does_not_raise() -> None:
    sent: list[int] = []

    class _Bot:
        async def send_message(self, telegram_id: int, text: str) -> None:
            if telegram_id == 40:
                raise TelegramForbiddenError(
                    method=SendMessage(chat_id=telegram_id, text=text),
                    message="bot can't initiate conversation",
                )
            sent.append(telegram_id)

    await deliver(
        _Bot(),
        (Outgoing(7, "клієнту"), Outgoing(40, "майстру")),
    )
    assert sent == [7]
