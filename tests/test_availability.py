from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from booking_bot.services.availability import (
    Calendar,
    DayOverride,
    Window,
    free_master_ids,
    slots_for_any_master,
    slots_for_master,
)

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)  # Monday 08:00 in Kyiv
MONDAY = date(2026, 11, 2)


def _salon() -> Calendar:
    calendar = Calendar()
    for weekday in range(6):
        calendar.salon[weekday] = Window(time(10, 0), time(19, 0))
    calendar.salon[6] = None
    for weekday in range(6):
        calendar.salon_breaks[weekday] = [(time(14, 0), time(15, 0))]
    return calendar


def test_sunday_and_past_slots_are_hidden() -> None:
    calendar = _salon()
    assert slots_for_master(calendar, 1, date(2026, 11, 8), 30, 30, KYIV, NOW, 30) == []
    monday = slots_for_master(calendar, 1, MONDAY, 30, 30, KYIV, NOW, 30)
    assert monday[0] == datetime(2026, 11, 2, 10, 0, tzinfo=KYIV)
    assert datetime(2026, 11, 2, 8, 0, tzinfo=KYIV) not in monday


def test_break_and_duration_remove_slots() -> None:
    calendar = _salon()
    slots = slots_for_master(calendar, 1, MONDAY, 30, 30, KYIV, NOW, 0)
    assert datetime(2026, 11, 2, 14, 0, tzinfo=KYIV) not in slots
    assert datetime(2026, 11, 2, 13, 30, tzinfo=KYIV) in slots
    long_slots = slots_for_master(calendar, 1, MONDAY, 75, 30, KYIV, NOW, 0)
    assert datetime(2026, 11, 2, 13, 0, tzinfo=KYIV) not in long_slots
    assert datetime(2026, 11, 2, 18, 0, tzinfo=KYIV) not in long_slots
    assert datetime(2026, 11, 2, 15, 0, tzinfo=KYIV) in long_slots


def test_master_hours_and_exceptions() -> None:
    calendar = _salon()
    calendar.master_hours[2] = {weekday: Window(time(12, 0), time(18, 0)) for weekday in range(6)}
    short = slots_for_master(calendar, 2, MONDAY, 30, 30, KYIV, NOW, 0)
    assert short[0] == datetime(2026, 11, 2, 12, 0, tzinfo=KYIV)
    assert datetime(2026, 11, 2, 18, 0, tzinfo=KYIV) not in short

    calendar.salon_exceptions[MONDAY] = DayOverride(True)
    assert slots_for_master(calendar, 1, MONDAY, 30, 30, KYIV, NOW, 0) == []

    custom = date(2026, 11, 3)
    calendar.salon_exceptions[custom] = DayOverride(False, time(16, 0), time(18, 0))
    custom_slots = slots_for_master(calendar, 1, custom, 30, 30, KYIV, NOW, 0)
    assert custom_slots[0] == datetime(2026, 11, 3, 16, 0, tzinfo=KYIV)
    assert custom_slots[-1] == datetime(2026, 11, 3, 17, 30, tzinfo=KYIV)


def test_busy_interval_and_any_master() -> None:
    calendar = _salon()
    start = datetime(2026, 11, 2, 10, 0, tzinfo=KYIV)
    calendar.busy.append((1, start, start + timedelta(minutes=30)))
    slots = slots_for_master(calendar, 1, MONDAY, 30, 30, KYIV, NOW, 0)
    assert start not in slots
    any_slots = slots_for_any_master(calendar, [1, 2], MONDAY, 30, 30, KYIV, NOW, 0)
    assert start in any_slots
    assert free_master_ids(calendar, [2, 1], start, 30, 30, KYIV, NOW, 0) == [2]
