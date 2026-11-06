import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic_settings import BaseSettings, SettingsConfigDict

_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")


class Settings(BaseSettings):
    """Process settings loaded from the environment and an optional `.env` file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = ""
    admin_ids: str = ""
    database_url: str = "sqlite:///./booking.db"
    timezone: str = "Europe/Kyiv"
    currency: str = "UAH"
    slot_minutes: int = 30
    reminder_hours: str = "24,2"
    log_level: str = "INFO"


def load_settings() -> Settings:
    return Settings()


def require_bot_token(token: str) -> str:
    """Return a stripped token, or exit when it is missing or not a bot token.

    The error text never includes the supplied value.
    """

    cleaned = token.strip()
    if _TOKEN_RE.fullmatch(cleaned) is None:
        raise SystemExit(
            "BOT_TOKEN is missing or is not a Telegram bot token. "
            "Set it in the environment or in .env."
        )
    return cleaned


def parse_admin_ids(raw: str) -> tuple[int, ...]:
    """Parse a comma-separated list of Telegram user ids."""

    if not raw.strip():
        return ()
    parsed: list[int] = []
    for part in raw.split(","):
        item = part.strip()
        if not item.isdigit() or item == "0":
            raise SystemExit("ADMIN_IDS must be a comma-separated list of positive integers.")
        parsed.append(int(item))
    return tuple(parsed)


def parse_reminder_hours(raw: str) -> tuple[int, ...]:
    """Parse reminder offsets in hours, such as `24,2`."""

    if not raw.strip():
        raise SystemExit("REMINDER_HOURS must list one or more positive integers.")
    parsed: list[int] = []
    for part in raw.split(","):
        item = part.strip()
        if not item.isdigit() or item == "0":
            raise SystemExit("REMINDER_HOURS must be a comma-separated list of positive integers.")
        parsed.append(int(item))
    return tuple(parsed)


def check_runtime_settings(settings: Settings) -> str:
    """Validate the settings required to start the process and return the token."""

    token = require_bot_token(settings.bot_token)
    parse_admin_ids(settings.admin_ids)
    parse_reminder_hours(settings.reminder_hours)
    if settings.slot_minutes <= 0:
        raise SystemExit("SLOT_MINUTES must be a positive integer.")
    if not settings.currency.strip():
        raise SystemExit("CURRENCY must not be empty.")
    if not settings.database_url.startswith("sqlite:"):
        raise SystemExit("DATABASE_URL must be a SQLite URL. Bookings and reminders use SQLite.")
    try:
        ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        raise SystemExit("TIMEZONE must be an IANA timezone name, such as Europe/Kyiv.") from None
    return token
