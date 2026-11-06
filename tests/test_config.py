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
        database_url="sqlite:///./booking.db",
        timezone="Europe/Kyiv",
        currency="UAH",
        slot_minutes=30,
        reminder_hours="24,2",
    )
    assert check_runtime_settings(settings) == VALID_TOKEN


def test_check_runtime_settings_rejects_a_non_sqlite_url() -> None:
    settings = Settings(bot_token=VALID_TOKEN, database_url="postgresql://localhost/booking")
    with pytest.raises(SystemExit, match="SQLite"):
        check_runtime_settings(settings)


def test_check_runtime_settings_rejects_an_unknown_timezone() -> None:
    settings = Settings(bot_token=VALID_TOKEN, timezone="Not/AZone")
    with pytest.raises(SystemExit, match="TIMEZONE"):
        check_runtime_settings(settings)


def test_dispatcher_uses_memory_storage() -> None:
    dispatcher = build_dispatcher()
    assert isinstance(dispatcher.storage, MemoryStorage)
