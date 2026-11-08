from datetime import UTC, datetime, timedelta

from booking_bot.db.models import BookingStatus
from booking_bot.services.policies import cancellation_decision, reminder_backoff


def test_cancellation_rules() -> None:
    start = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    now = start - timedelta(hours=3)
    assert (
        cancellation_decision(
            status=BookingStatus.CONFIRMED,
            starts_at=start,
            now=now,
            cancel_before_hours=2,
            by_admin=False,
        )
        == "cancelled"
    )
    assert (
        cancellation_decision(
            status=BookingStatus.CONFIRMED,
            starts_at=start,
            now=start - timedelta(minutes=30),
            cancel_before_hours=2,
            by_admin=False,
        )
        == "too_late"
    )
    assert (
        cancellation_decision(
            status=BookingStatus.CONFIRMED,
            starts_at=start,
            now=start - timedelta(minutes=30),
            cancel_before_hours=2,
            by_admin=True,
        )
        == "cancelled"
    )
    for status in (BookingStatus.CANCELLED, BookingStatus.COMPLETED):
        assert (
            cancellation_decision(
                status=status,
                starts_at=start,
                now=now,
                cancel_before_hours=2,
                by_admin=True,
            )
            == "already"
        )


def test_reminder_backoff_steps() -> None:
    assert reminder_backoff(1) == timedelta(minutes=1)
    assert reminder_backoff(2) == timedelta(minutes=2)
    assert reminder_backoff(3) == timedelta(minutes=4)
    assert reminder_backoff(4) == timedelta(minutes=8)
