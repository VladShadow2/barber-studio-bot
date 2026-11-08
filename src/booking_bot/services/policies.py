from datetime import datetime, timedelta

from booking_bot.db.models import BookingStatus

REMINDER_LEASE = timedelta(minutes=5)
REMINDER_MAX_ATTEMPTS = 5
_BACKOFF_MINUTES = {1: 1, 2: 2, 3: 4, 4: 8}


def reminder_backoff(attempts: int) -> timedelta:
    """Delay before the next try. Attempts 1–4 map to 1, 2, 4, and 8 minutes."""

    return timedelta(minutes=_BACKOFF_MINUTES.get(attempts, 8))


def cancellation_decision(
    *,
    status: BookingStatus,
    starts_at: datetime,
    now: datetime,
    cancel_before_hours: int,
    by_admin: bool,
) -> str:
    """Return cancelled, already, or too_late. The caller checks ownership first."""

    if status in {BookingStatus.CANCELLED, BookingStatus.COMPLETED}:
        return "already"
    if status not in {BookingStatus.PENDING, BookingStatus.CONFIRMED}:
        return "already"
    if by_admin:
        return "cancelled"
    if starts_at - now < timedelta(hours=cancel_before_hours):
        return "too_late"
    return "cancelled"
