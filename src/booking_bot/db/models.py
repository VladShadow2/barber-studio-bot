import enum
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    column,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from booking_bot.db.base import Base


class BookingStatus(enum.StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class ReminderStatus(enum.StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _pg_enum(enum_cls: type[enum.StrEnum], name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        values_callable=lambda choices: [item.value for item in choices],
    )


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    display_name: Mapped[str] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bookings: Mapped[list["Booking"]] = relationship(back_populates="customer")


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (
        UniqueConstraint("name", name="uq_services_name"),
        CheckConstraint("duration_minutes > 0", name="ck_services_duration"),
        CheckConstraint("price_uah >= 0", name="ck_services_price"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    duration_minutes: Mapped[int] = mapped_column(Integer)
    price_uah: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    masters: Mapped[list["Master"]] = relationship(
        secondary="master_services", back_populates="services"
    )


class Master(Base):
    __tablename__ = "masters"
    __table_args__ = (
        UniqueConstraint("name", name="uq_masters_name"),
        UniqueConstraint("telegram_user_id", name="uq_masters_telegram_user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger)

    services: Mapped[list[Service]] = relationship(
        secondary="master_services", back_populates="masters"
    )


class MasterService(Base):
    __tablename__ = "master_services"

    master_id: Mapped[int] = mapped_column(
        ForeignKey("masters.id", ondelete="CASCADE"), primary_key=True
    )
    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), primary_key=True
    )


class BusinessHours(Base):
    __tablename__ = "business_hours"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_business_hours_weekday"),
        CheckConstraint(
            "(is_closed AND open_time IS NULL AND close_time IS NULL) OR "
            "(NOT is_closed AND open_time IS NOT NULL AND close_time IS NOT NULL "
            "AND open_time < close_time)",
            name="ck_business_hours_window",
        ),
    )

    weekday: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False)
    open_time: Mapped[time | None] = mapped_column(Time)
    close_time: Mapped[time | None] = mapped_column(Time)


class MasterHours(Base):
    __tablename__ = "master_hours"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_master_hours_weekday"),
        CheckConstraint(
            "(is_closed AND open_time IS NULL AND close_time IS NULL) OR "
            "(NOT is_closed AND open_time IS NOT NULL AND close_time IS NOT NULL "
            "AND open_time < close_time)",
            name="ck_master_hours_window",
        ),
    )

    master_id: Mapped[int] = mapped_column(
        ForeignKey("masters.id", ondelete="CASCADE"), primary_key=True
    )
    weekday: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False)
    open_time: Mapped[time | None] = mapped_column(Time)
    close_time: Mapped[time | None] = mapped_column(Time)


class Break(Base):
    __tablename__ = "breaks"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_breaks_weekday"),
        CheckConstraint("start_time < end_time", name="ck_breaks_window"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    master_id: Mapped[int | None] = mapped_column(ForeignKey("masters.id", ondelete="CASCADE"))
    weekday: Mapped[int] = mapped_column(SmallInteger)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)


class ScheduleException(Base):
    __tablename__ = "schedule_exceptions"
    __table_args__ = (
        CheckConstraint(
            "(is_closed AND open_time IS NULL AND close_time IS NULL) OR "
            "(NOT is_closed AND open_time IS NOT NULL AND close_time IS NOT NULL "
            "AND open_time < close_time)",
            name="ck_schedule_exceptions_window",
        ),
        Index(
            "uq_salon_exception_date",
            "on_date",
            unique=True,
            postgresql_where=text("master_id IS NULL"),
        ),
        Index(
            "uq_master_exception_date",
            "master_id",
            "on_date",
            unique=True,
            postgresql_where=text("master_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    master_id: Mapped[int | None] = mapped_column(ForeignKey("masters.id", ondelete="CASCADE"))
    on_date: Mapped[date] = mapped_column(Date)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=True)
    open_time: Mapped[time | None] = mapped_column(Time)
    close_time: Mapped[time | None] = mapped_column(Time)
    note: Mapped[str] = mapped_column(Text, default="")


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_bookings_range"),
        ExcludeConstraint(
            ("master_id", "="),
            (func.tstzrange(column("starts_at"), column("ends_at"), "[)"), "&&"),
            where=text("status IN ('pending', 'confirmed')"),
            using="gist",
            name="bookings_no_overlap",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"))
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id", ondelete="RESTRICT"))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="RESTRICT"))
    service_name: Mapped[str] = mapped_column(String(120))
    service_duration_minutes: Mapped[int] = mapped_column(Integer)
    service_price_uah: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    master_name: Mapped[str] = mapped_column(String(120))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[BookingStatus] = mapped_column(_pg_enum(BookingStatus, "booking_status"))
    customer_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped[Customer] = relationship(back_populates="bookings")
    reminders: Mapped[list["Reminder"]] = relationship(back_populates="booking")


class BookingDraft(Base):
    __tablename__ = "booking_drafts"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    step: Mapped[str] = mapped_column(String(32))
    service_id: Mapped[int | None] = mapped_column(ForeignKey("services.id", ondelete="SET NULL"))
    master_id: Mapped[int | None] = mapped_column(ForeignKey("masters.id", ondelete="SET NULL"))
    any_master: Mapped[bool] = mapped_column(Boolean, default=False)
    appointment_date: Mapped[date | None] = mapped_column(Date)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    customer_note: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (
        UniqueConstraint("booking_id", "remind_at", name="uq_reminder_booking_time"),
        CheckConstraint("attempts >= 0", name="ck_reminders_attempts"),
        Index("ix_reminders_due", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"))
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[ReminderStatus] = mapped_column(_pg_enum(ReminderStatus, "reminder_status"))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    booking: Mapped[Booking] = relationship(back_populates="reminders")
