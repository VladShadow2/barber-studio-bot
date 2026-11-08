from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from booking_bot.config import Settings
from booking_bot.db.models import (
    Booking,
    BookingDraft,
    BookingStatus,
    Break,
    BusinessHours,
    Customer,
    Master,
    MasterHours,
    MasterService,
    Reminder,
    ReminderStatus,
    ScheduleException,
    Service,
)
from booking_bot.repositories.schedule import load_calendar
from booking_bot.services.availability import slots_for_any_master, slots_for_master
from booking_bot.services.booking import cancel_booking, confirm_draft
from booking_bot.services.reminders import (
    claim_due,
    mark_failed,
    mark_sent,
    process_one,
    reclaim_expired,
)
from booking_bot.services.seed import seed_demo

KYIV_NOW = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(
        bot_token="123456789:" + ("A" * 35),
        admin_ids="99",
        database_url="postgresql+asyncpg://booking:booking@localhost/booking_test",
        timezone="Europe/Kyiv",
        reminder_hours="24",
        slot_minutes=30,
        cancel_before_hours=2,
        booking_days_ahead=14,
        min_lead_minutes=0,
        draft_ttl_minutes=60,
    )


async def test_seed_is_idempotent(postgres) -> None:
    async with postgres() as session:
        async with session.begin():
            await seed_demo(session)
            await seed_demo(session)
        services = await session.scalar(select(func.count()).select_from(Service))
        masters = await session.scalar(select(func.count()).select_from(Master))
    assert services == 4
    assert masters == 2


async def test_schedule_exceptions_and_master_availability(postgres) -> None:
    settings = _settings()
    async with postgres() as session:
        async with session.begin():
            await seed_demo(session)
        service = await session.scalar(select(Service).where(Service.name == "Стрижка бороди"))
        taras = await session.scalar(select(Master).where(Master.name == "Taras Melnyk"))
        andrii = await session.scalar(select(Master).where(Master.name == "Andrii Kovalenko"))
        assert service is not None and taras is not None and andrii is not None
        calendar = await load_calendar(session, KYIV_NOW, KYIV_NOW + timedelta(days=40))
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Europe/Kyiv")
        monday = date(2026, 11, 2)
        closed = date(2030, 1, 1)
        taras_slots = slots_for_master(calendar, taras.id, monday, 30, 30, tz, KYIV_NOW, 0)
        andrii_slots = slots_for_master(calendar, andrii.id, monday, 30, 30, tz, KYIV_NOW, 0)
        assert taras_slots[0].hour == 10
        assert andrii_slots[0].hour == 12
        assert datetime(2026, 11, 2, 14, 0, tzinfo=tz) not in taras_slots
        assert slots_for_master(calendar, taras.id, closed, 30, 30, tz, KYIV_NOW, 0) == []
        custom = date(2026, 11, 4)
        session.add(
            ScheduleException(
                master_id=None,
                on_date=custom,
                is_closed=False,
                open_time=time(16, 0),
                close_time=time(18, 0),
            )
        )
        await session.commit()
        calendar = await load_calendar(session, KYIV_NOW, KYIV_NOW + timedelta(days=40))
        custom_slots = slots_for_master(calendar, taras.id, custom, 30, 30, tz, KYIV_NOW, 0)
        assert custom_slots[0] == datetime(2026, 11, 4, 16, 0, tzinfo=tz)
        start = datetime(2026, 11, 2, 10, 0, tzinfo=tz)
        customer = Customer(telegram_id=5, display_name="Ada")
        session.add(customer)
        await session.flush()
        session.add(
            Booking(
                customer_id=customer.id,
                master_id=taras.id,
                service_id=service.id,
                service_name=service.name,
                service_duration_minutes=30,
                service_price_uah=service.price_uah,
                master_name=taras.name,
                starts_at=start,
                ends_at=start + timedelta(minutes=30),
                status=BookingStatus.CONFIRMED,
            )
        )
        await session.commit()
        calendar = await load_calendar(session, KYIV_NOW, KYIV_NOW + timedelta(days=3))
        assert start not in slots_for_master(calendar, taras.id, monday, 30, 30, tz, KYIV_NOW, 0)
        assert start in slots_for_any_master(
            calendar, [taras.id, andrii.id], monday, 30, 30, tz, KYIV_NOW, 0)
        assert settings.min_lead_minutes == 0


async def test_confirm_keeps_price_snapshot_and_rejects_overlap(postgres) -> None:
    settings = _settings()
    start = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
    async with postgres() as session:
        async with session.begin():
            service = Service(
                name="Cut",
                description="",
                duration_minutes=30,
                price_uah=Decimal("400.00"),
                is_active=True,
            )
            master = Master(name="Taras", description="", is_active=True)
            session.add_all([service, master])
            await session.flush()
            session.add(MasterService(master_id=master.id, service_id=service.id))
            session.add(BusinessHours(weekday=start.weekday(), is_closed=False,
                        open_time=time(0, 0), close_time=time(23, 0)))
            session.add(
                BookingDraft(
                    telegram_id=7,
                    step="summary",
                    service_id=service.id,
                    master_id=master.id,
                    any_master=False,
                    appointment_date=start.date(),
                    starts_at=start,
                    expires_at=start + timedelta(hours=1),
                    updated_at=start,
                )
            )
        async with session.begin():
            result = await confirm_draft(
                session,
                telegram_id=7,
                display_name="Ada",
                username="ada",
                settings=settings,
                now=start - timedelta(hours=2),
                admin_ids=(99,),
            )
            assert result.code == "confirmed"
            assert result.booking is not None
            booking_id = result.booking.id
        service.price_uah = Decimal("999.00")
        await session.commit()
        stored = await session.get(Booking, booking_id)
        assert stored is not None
        assert stored.service_price_uah == Decimal("400.00")
        session.add(
            BookingDraft(
                telegram_id=8,
                step="summary",
                service_id=service.id,
                master_id=master.id,
                any_master=False,
                appointment_date=start.date(),
                starts_at=start,
                expires_at=start + timedelta(days=1),
                updated_at=start,
            )
        )
        await session.commit()
        async with session.begin():
            second = await confirm_draft(
                session,
                telegram_id=8,
                display_name="Bea",
                username=None,
                settings=settings,
                now=start - timedelta(hours=2),
                admin_ids=(),
            )
        assert second.code == "taken"
        count = await session.scalar(select(func.count()).select_from(Booking))
        assert count == 1


async def test_exclusion_constraint_rejects_a_second_insert(postgres) -> None:
    start = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    async with postgres() as session:
        async with session.begin():
            customer = Customer(telegram_id=1, display_name="Ada")
            other = Customer(telegram_id=2, display_name="Bea")
            master = Master(name="Taras", description="", is_active=True)
            service = Service(name="Cut", description="", duration_minutes=30,
                              price_uah=Decimal("10"), is_active=True)
            session.add_all([customer, other, master, service])
            await session.flush()
            session.add(_booking(customer.id, master.id, service, start))
        other_booking = _booking(other.id, master.id, service, start)
        session.add(other_booking)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        count = await session.scalar(select(func.count()).select_from(Booking))
        assert count == 1


def _booking(customer_id: int, master_id: int, service: Service, start: datetime) -> Booking:
    return Booking(
        customer_id=customer_id,
        master_id=master_id,
        service_id=service.id,
        service_name=service.name,
        service_duration_minutes=30,
        service_price_uah=service.price_uah,
        master_name="Taras",
        starts_at=start,
        ends_at=start + timedelta(minutes=30),
        status=BookingStatus.CONFIRMED,
    )


async def test_cancel_clears_pending_reminders_and_blocks_repeat(postgres) -> None:
    settings = _settings()
    start = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)
    async with postgres() as session:
        async with session.begin():
            customer = Customer(telegram_id=7, display_name="Ada")
            master = Master(name="Taras", description="", is_active=True)
            service = Service(name="Cut", description="", duration_minutes=30,
                              price_uah=Decimal("10"), is_active=True)
            session.add_all([customer, master, service])
            await session.flush()
            booking = _booking(customer.id, master.id, service, start)
            session.add(booking)
            await session.flush()
            session.add(
                Reminder(
                    booking_id=booking.id,
                    remind_at=start - timedelta(hours=24),
                    next_attempt_at=start - timedelta(hours=24),
                    status=ReminderStatus.PENDING,
                    attempts=0,
                )
            )
            booking_id = booking.id
        async with session.begin():
            assert (
                await cancel_booking(
                    session,
                    booking_id=booking_id,
                    telegram_id=7,
                    by_admin=False,
                    settings=settings,
                    now=start - timedelta(hours=5),
                )
                == "cancelled"
            )
        reminder = await session.scalar(select(Reminder).where(Reminder.booking_id == booking_id))
        assert reminder is not None
        assert reminder.status == ReminderStatus.CANCELLED
        async with session.begin():
            again = await cancel_booking(
                session,
                booking_id=booking_id,
                telegram_id=7,
                by_admin=True,
                settings=settings,
                now=start,
            )
        assert again == "already"


async def test_reminder_claim_retry_and_lease(postgres) -> None:
    settings = _settings()
    now = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    start = now + timedelta(days=2)
    sent: list[str] = []

    async def send(_telegram_id: int, text: str) -> None:
        async with postgres() as check:
            reminder = await check.scalar(select(Reminder))
            assert reminder is not None
            assert reminder.status == ReminderStatus.PROCESSING
        sent.append(text)

    async with postgres() as session:
        async with session.begin():
            customer = Customer(telegram_id=7, display_name="Ada")
            master = Master(name="Taras", description="", is_active=True)
            service = Service(name="Cut", description="", duration_minutes=30,
                              price_uah=Decimal("10"), is_active=True)
            session.add_all([customer, master, service])
            await session.flush()
            booking = _booking(customer.id, master.id, service, start)
            session.add(booking)
            await session.flush()
            session.add(
                Reminder(
                    booking_id=booking.id,
                    remind_at=now - timedelta(minutes=1),
                    next_attempt_at=now - timedelta(minutes=1),
                    status=ReminderStatus.PENDING,
                    attempts=0,
                )
            )
    assert await process_one(postgres, send, settings, now) is True
    assert sent
    async with postgres() as session:
        reminder = await session.scalar(select(Reminder))
        assert reminder is not None
        assert reminder.status == ReminderStatus.SENT
    sent.clear()
    assert await process_one(postgres, send, settings, now) is False
    assert sent == []

    async with postgres() as session:
        async with session.begin():
            reminder = await session.scalar(select(Reminder))
            assert reminder is not None
            reminder.status = ReminderStatus.PENDING
            reminder.sent_at = None
            reminder.attempts = 0
            reminder.next_attempt_at = now

    async def fail(_telegram_id: int, _text: str) -> None:
        raise ConnectionError("telegram down")

    await process_one(postgres, fail, settings, now)
    async with postgres() as session:
        reminder = await session.scalar(select(Reminder))
        assert reminder is not None
        assert reminder.status == ReminderStatus.PENDING
        assert reminder.attempts == 1
        assert reminder.next_attempt_at == now + timedelta(minutes=1)

    async with postgres() as session:
        async with session.begin():
            reminder = await session.scalar(select(Reminder))
            assert reminder is not None
            reminder.attempts = 4
            reminder.status = ReminderStatus.PENDING
            reminder.next_attempt_at = now
    await process_one(postgres, fail, settings, now)
    async with postgres() as session:
        reminder = await session.scalar(select(Reminder))
        assert reminder is not None
        assert reminder.status == ReminderStatus.FAILED

    async with postgres() as session:
        async with session.begin():
            reminder = await session.scalar(select(Reminder))
            assert reminder is not None
            reminder.status = ReminderStatus.PROCESSING
            reminder.attempts = 1
            reminder.processing_started_at = now - timedelta(minutes=10)
            await reclaim_expired(session, now)
        await session.refresh(reminder)
        assert reminder.status == ReminderStatus.PENDING

    async with postgres() as session:
        async with session.begin():
            reminder = await session.scalar(select(Reminder))
            assert reminder is not None
            reminder.status = ReminderStatus.PROCESSING
            reminder.attempts = 5
            reminder.processing_started_at = now - timedelta(minutes=10)
            await reclaim_expired(session, now)
        await session.refresh(reminder)
        assert reminder.status == ReminderStatus.FAILED
        claimed = await claim_due(session, now, settings)
        assert claimed is None
        await mark_sent(session, reminder.id, now)
        await mark_failed(session, reminder.id, 1, "ignored", now)
        await session.commit()
        await session.refresh(reminder)
        assert reminder.status == ReminderStatus.FAILED


async def test_draft_survives_a_new_session(postgres) -> None:
    now = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
    async with postgres() as session:
        async with session.begin():
            session.add(
                BookingDraft(
                    telegram_id=7,
                    step="master",
                    expires_at=now + timedelta(minutes=30),
                    updated_at=now,
                )
            )
    async with postgres() as session:
        draft = await session.get(BookingDraft, 7)
        assert draft is not None
        assert draft.step == "master"


async def test_master_break_is_loaded(postgres) -> None:
    async with postgres() as session:
        async with session.begin():
            master = Master(name="Taras", description="", is_active=True)
            session.add(master)
            await session.flush()
            session.add(BusinessHours(weekday=0, is_closed=False,
                        open_time=time(10, 0), close_time=time(19, 0)))
            session.add(MasterHours(master_id=master.id, weekday=0, is_closed=False,
                        open_time=time(12, 0), close_time=time(18, 0)))
            session.add(Break(master_id=master.id, weekday=0,
                        start_time=time(16, 0), end_time=time(17, 0)))
        calendar = await load_calendar(
            session,
            datetime(2026, 11, 2, tzinfo=UTC),
            datetime(2026, 11, 3, tzinfo=UTC),
        )
        assert calendar.master_breaks[master.id][0] == [(time(16, 0), time(17, 0))]
        assert calendar.master_hours[master.id][0] is not None
