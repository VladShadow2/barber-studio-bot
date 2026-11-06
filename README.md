# Salon booking bot

Telegram bot for one salon. Customers will browse services, choose a staff member, and reserve a time. Admins will manage the catalog, the calendar, and bookings. This revision is the process shell: it checks configuration and answers `/start`.

Conversation state uses aiogram `MemoryStorage`. There is no custom SQLite FSM storage. An unfinished conversation is lost when the process stops. SQLite will persist bookings and reminders only; those rows are what a restart recovers. This revision does not create that database yet.

## Requirements

- Python 3.12 or newer
- A Telegram bot token from BotFather

## Run locally

```bash
uv sync
cp .env.example .env
```

Set `BOT_TOKEN` in `.env`, then:

```bash
uv run python -m booking_bot
```

The process long-polls Telegram. It does not open an HTTP port. Stop it with Ctrl+C.

`/start` replies with a short greeting. Other booking commands are not wired yet.

## Configuration

| Variable | Purpose |
| --- | --- |
| `BOT_TOKEN` | BotFather token. The process refuses to start when it is missing or malformed, and it never writes the token to the log. |
| `ADMIN_IDS` | Comma-separated Telegram user ids for the admin desk. |
| `DATABASE_URL` | SQLite URL for bookings and reminders. |
| `TIMEZONE` | IANA timezone for appointment times. Default `Europe/Kyiv`. |
| `CURRENCY` | Label shown with prices. Default `UAH`. |
| `SLOT_MINUTES` | Length of the booking grid. |
| `REMINDER_HOURS` | Hours before a visit to send reminders, such as `24,2`. |
| `LOG_LEVEL` | Python log level. Default `INFO`. |

## Tests

```bash
uv run pytest
uv run ruff check
```
