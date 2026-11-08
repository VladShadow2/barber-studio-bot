import pytest
from aiogram.fsm.storage.memory import MemoryStorage

from booking_bot.bot import build_dispatcher
from booking_bot.config import (
    Settings,
    check_runtime_settings,
    parse_admin_ids,
    parse_reminder_hours,
    require_bot_token,
)

VALID_TOKEN = "123456789:" + ("A" * 35)


def test_require_bot_token_accepts_a_botfather_shape() -> None:
    assert require_bot_token(f"  {VALID_TOKEN}  ") == VALID_TOKEN


def test_require_bot_token_rejects_blank_or_malformed_without_echoing_it() -> None:
    supplied = "secret-but-not-a-token"
    with pytest.raises(SystemExit) as caught:
        require_bot_token(supplied)
    message = str(caught.value)
    assert "BOT_TOKEN" in message
    assert supplied not in message


def test_parse_admin_ids() -> None:
    assert parse_admin_ids("") == ()
    assert parse_admin_ids(" 42, 1001 ") == (42, 1001)


def test_parse_admin_ids_rejects_zero_and_words() -> None:
    with pytest.raises(SystemExit):
        parse_admin_ids("0")
    with pytest.raises(SystemExit):
        parse_admin_ids("abc")


def test_parse_reminder_hours() -> None:
    assert parse_reminder_hours("24,2") == (24, 2)


def test_check_runtime_settings_accepts_defaults() -> None:
    settings = Settings(
        bot_token=VALID_TOKEN,
        admin_ids="7",
        database_url="postgresql+asyncpg://booking:booking@localhost:5432/booking",
        timezone="Europe/Kyiv",
        currency="UAH",
        slot_minutes=30,
        reminder_hours="24,2",
    )
    assert check_runtime_settings(settings) == VALID_TOKEN


def test_check_runtime_settings_rejects_sqlite_and_hides_the_url() -> None:
    supplied = "sqlite:///./secret-booking.db"
    settings = Settings(bot_token=VALID_TOKEN, database_url=supplied)
    with pytest.raises(SystemExit) as caught:
        check_runtime_settings(settings)
    message = str(caught.value)
    assert "postgresql+asyncpg://" in message
    assert supplied not in message


def test_check_runtime_settings_rejects_webhook_without_https_or_secret() -> None:
    settings = Settings(
        bot_token=VALID_TOKEN,
        database_url="postgresql+asyncpg://booking:booking@localhost/booking",
        bot_mode="webhook",
        webhook_base_url="http://example.test",
        webhook_secret="short",
    )
    with pytest.raises(SystemExit, match="https"):
        check_runtime_settings(settings)


def test_check_runtime_settings_rejects_an_unknown_timezone() -> None:
    settings = Settings(
        bot_token=VALID_TOKEN,
        database_url="postgresql+asyncpg://booking:booking@localhost/booking",
        timezone="Not/AZone",
    )
    with pytest.raises(SystemExit, match="TIMEZONE"):
        check_runtime_settings(settings)


def test_dispatcher_uses_memory_storage() -> None:
    first = build_dispatcher()
    second = build_dispatcher()
    assert isinstance(first.storage, MemoryStorage)
    assert isinstance(second.storage, MemoryStorage)
    assert first is not second
