from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from booking_bot.config import Settings
from booking_bot.db.models import BookingDraft

STEPS = ("service", "master", "date", "time", "note", "summary")


def previous_step(step: str) -> str | None:
    if step not in STEPS or step == "service":
        return None
    return STEPS[STEPS.index(step) - 1]


async def get_draft(session: AsyncSession, telegram_id: int, now: datetime) -> BookingDraft | None:
    draft = await session.get(BookingDraft, telegram_id)
    if draft is None:
        return None
    if draft.expires_at <= now:
        await session.delete(draft)
        return None
    return draft


async def touch_draft(
    session: AsyncSession,
    telegram_id: int,
    step: str,
    settings: Settings,
    now: datetime,
    **fields: object,
) -> BookingDraft:
    draft = await session.get(BookingDraft, telegram_id)
    expires_at = now + timedelta(minutes=settings.draft_ttl_minutes)
    if draft is None:
        draft = BookingDraft(telegram_id=telegram_id, step=step,
                             expires_at=expires_at, updated_at=now)
        session.add(draft)
    draft.step = step
    draft.expires_at = expires_at
    draft.updated_at = now
    for key, value in fields.items():
        setattr(draft, key, value)
    return draft
