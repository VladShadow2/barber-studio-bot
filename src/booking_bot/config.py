import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic_settings import BaseSettings, SettingsConfigDict

_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")
_POSTGRES_PREFIX = "postgresql+asyncpg://"


class Settings(BaseSettings):
    """Process settings loaded from the environment and an optional `.env` file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = ""
    admin_ids: str = ""
    database_url: str = "postgresql+asyncpg://booking:booking@localhost:5432/booking"
    migration_database_url: str = ""
    timezone: str = "Europe/Kyiv"
    currency: str = "UAH"
    slot_minutes: int = 30
    reminder_hours: str = "24"
    log_level: str = "INFO"
    bot_mode: str = "polling"
    webhook_base_url: str = ""
    webhook_secret: str = ""
    port: int = 8080
    cancel_before_hours: int = 2
    booking_days_ahead: int = 14
    min_lead_minutes: int = 30
    draft_ttl_minutes: int = 60
    reminder_poll_seconds: int = 30


def load_settings() -> Settings:
    return Settings()


def migration_database_url(settings: Settings) -> str:
    """Direct URL for Alembic. Falls back to the runtime URL."""

    direct = settings.migration_database_url.strip()
    return direct or settings.database_url.strip()


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
    """Parse reminder offsets in hours, such as `24` or `24,2`."""

    if not raw.strip():
        raise SystemExit("REMINDER_HOURS must list one or more positive integers.")
    parsed: list[int] = []
    for part in raw.split(","):
        item = part.strip()
        if not item.isdigit() or item == "0":
            raise SystemExit("REMINDER_HOURS must be a comma-separated list of positive integers.")
        parsed.append(int(item))
    return tuple(parsed)


def normalized_bot_mode(settings: Settings) -> str:
    return settings.bot_mode.strip().lower()


def check_runtime_settings(settings: Settings) -> str:
    """Validate the settings required to start the process and return the token."""

    token = require_bot_token(settings.bot_token)
    parse_admin_ids(settings.admin_ids)
    parse_reminder_hours(settings.reminder_hours)
    if settings.slot_minutes <= 0:
        raise SystemExit("SLOT_MINUTES must be a positive integer.")
    if settings.cancel_before_hours < 0:
        raise SystemExit("CANCEL_BEFORE_HOURS must be zero or a positive integer.")
    if settings.booking_days_ahead <= 0:
        raise SystemExit("BOOKING_DAYS_AHEAD must be a positive integer.")
    if settings.min_lead_minutes < 0:
        raise SystemExit("MIN_LEAD_MINUTES must be zero or a positive integer.")
    if settings.draft_ttl_minutes <= 0:
        raise SystemExit("DRAFT_TTL_MINUTES must be a positive integer.")
    if settings.reminder_poll_seconds <= 0:
        raise SystemExit("REMINDER_POLL_SECONDS must be a positive integer.")
    if settings.port <= 0:
        raise SystemExit("PORT must be a positive integer.")
    if not settings.currency.strip():
        raise SystemExit("CURRENCY must not be empty.")
    database_url = settings.database_url.strip()
    if not database_url.startswith(_POSTGRES_PREFIX):
        raise SystemExit(
            "DATABASE_URL must be a PostgreSQL URL that starts with postgresql+asyncpg://. "
            "SQLite files are not used."
        )
    migration_url = settings.migration_database_url.strip()
    if migration_url and not migration_url.startswith(_POSTGRES_PREFIX):
        raise SystemExit("MIGRATION_DATABASE_URL must start with postgresql+asyncpg://.")
    mode = normalized_bot_mode(settings)
    if mode not in {"polling", "webhook"}:
        raise SystemExit("BOT_MODE must be polling or webhook.")
    if mode == "webhook":
        base = settings.webhook_base_url.strip()
        if not base.startswith("https://"):
            raise SystemExit("WEBHOOK_BASE_URL must be an https URL in webhook mode.")
        if len(settings.webhook_secret.strip()) < 16:
            raise SystemExit("WEBHOOK_SECRET must be at least 16 characters in webhook mode.")
    try:
        ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        raise SystemExit("TIMEZONE must be an IANA timezone name, such as Europe/Kyiv.") from None
    return token
