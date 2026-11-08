from datetime import date, time
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from booking_bot.db.models import (
    Break,
    BusinessHours,
    Master,
    MasterHours,
    MasterService,
    ScheduleException,
    Service,
)

SAMPLE_DAY_OFF = date(2030, 1, 1)

_SERVICES = (
    (
        "Чоловіча стрижка",
        "Men's haircut",
        "Класична стрижка й укладка. Демонстраційна послуга.",
        45,
        Decimal("400.00"),
    ),
    (
        "Стрижка бороди",
        "Beard trim",
        "Форма та охайний контур бороди. Демонстраційна послуга.",
        30,
        Decimal("250.00"),
    ),
    (
        "Стрижка та борода",
        "Haircut and beard",
        "Стрижка разом із борідкою. Демонстраційна послуга.",
        75,
        Decimal("600.00"),
    ),
    (
        "Укладка волосся",
        "Hair styling",
        "Лише укладка, без стрижки. Демонстраційна послуга.",
        30,
        Decimal("300.00"),
    ),
)

_MASTERS = (
    ("Taras Melnyk", "Вигаданий барбер. Цей профіль існує лише для демонстрації."),
    ("Andrii Kovalenko", "Вигаданий барбер із коротшою зміною. Лише демонстраційний профіль."),
)


async def seed_demo(session: AsyncSession) -> None:
    """Insert Barber Studio demo rows. Running it again updates the same names."""

    services: dict[str, Service] = {}
    for name, legacy_name, description, duration, price in _SERVICES:
        service = await session.scalar(select(Service).where(Service.name == name))
        if service is None:
            service = await session.scalar(select(Service).where(Service.name == legacy_name))
        if service is None:
            service = Service(name=name)
            session.add(service)
        service.name = name
        service.description = description
        service.duration_minutes = duration
        service.price_uah = price
        service.is_active = True
        services[name] = service

    masters: dict[str, Master] = {}
    for name, description in _MASTERS:
        master = await session.scalar(select(Master).where(Master.name == name))
        if master is None:
            master = Master(name=name)
            session.add(master)
        master.description = description
        master.is_active = True
        masters[name] = master
    await session.flush()

    for master in masters.values():
        for service in services.values():
            link = await session.get(MasterService, (master.id, service.id))
            if link is None:
                session.add(MasterService(master_id=master.id, service_id=service.id))

    for weekday in range(7):
        closed = weekday == 6
        row = await session.get(BusinessHours, weekday)
        if row is None:
            row = BusinessHours(weekday=weekday)
            session.add(row)
        row.is_closed = closed
        row.open_time = None if closed else time(10, 0)
        row.close_time = None if closed else time(19, 0)

    andrii = masters["Andrii Kovalenko"]
    for weekday in range(6):
        row = await session.get(MasterHours, (andrii.id, weekday))
        if row is None:
            row = MasterHours(master_id=andrii.id, weekday=weekday)
            session.add(row)
        row.is_closed = False
        row.open_time = time(12, 0)
        row.close_time = time(18, 0)

    for weekday in range(6):
        existing = await session.scalar(
            select(Break).where(
                Break.master_id.is_(None),
                Break.weekday == weekday,
                Break.start_time == time(14, 0),
                Break.end_time == time(15, 0),
            )
        )
        if existing is None:
            session.add(Break(master_id=None, weekday=weekday,
                        start_time=time(14, 0), end_time=time(15, 0)))

    day_off = await session.scalar(
        select(ScheduleException).where(
            ScheduleException.master_id.is_(None),
            ScheduleException.on_date == SAMPLE_DAY_OFF,
        )
    )
    if day_off is None:
        day_off = ScheduleException(master_id=None, on_date=SAMPLE_DAY_OFF)
        session.add(day_off)
    day_off.is_closed = True
    day_off.open_time = None
    day_off.close_time = None
    day_off.note = "Вигаданий приклад вихідного. Це не реальне закриття."
