from datetime import date, time

from booking_bot.services.admin_edit import (
    parse_break,
    parse_exception,
    parse_hours,
    parse_service_line,
)


def test_admin_parsers_accept_the_documented_forms() -> None:
    parsed = parse_service_line("Чоловіча стрижка | 45 | 400 | Стрижка")
    assert parsed is not None
    assert parsed[1] == 45
    assert parse_hours("10:00-19:00") == (False, time(10, 0), time(19, 0))
    assert parse_hours("закрито") == (True, None, None)
    assert parse_hours("19:00-10:00") is None
    assert parse_break("Пн 14:00-15:00") == (0, time(14, 0), time(15, 0))
    assert parse_exception("2030-01-02 закрито") == (date(2030, 1, 2), True, None, None)
    assert parse_exception("not-a-date closed") is None
