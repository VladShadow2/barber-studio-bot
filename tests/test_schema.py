from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from booking_bot.db.models import Booking


def test_booking_overlap_constraint_is_declared() -> None:
    names = {constraint.name for constraint in Booking.__table__.constraints}
    assert "bookings_no_overlap" in names
    ddl = str(CreateTable(Booking.__table__).compile(dialect=postgresql.dialect()))
    assert "EXCLUDE" in ddl
    assert "tstzrange" in ddl
    assert "pending" in ddl
    assert "confirmed" in ddl
