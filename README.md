# Barber Studio booking bot

Telegram appointment bot for one salon. This repository is a fictional demo called **Barber Studio**. It is a portfolio project. No real salon is using it, and the address and phone in the bot are demo data.

Customers can read services, book a master and a time, review upcoming visits, and cancel inside a configurable window. Administrators, identified by Telegram user id, manage the catalog, hours, and bookings. Appointments, unfinished booking drafts, and reminders live in PostgreSQL, so they survive a process restart.

## Features

Customers:

- `/start` menu: book, services, about the salon, and my bookings
- Service, master (or any available master), date, and time selection
- Back and Cancel throughout the booking
- Summary, optional note, and confirmation
- Upcoming appointments, details, status, and cancellation

Administrators (`/admin`, only `ADMIN_IDS`):

- Upcoming bookings and bookings by date
- Booking details and cancellation, with confirmation
- Add and edit services, including price, duration, and active status
- Add and edit masters, the services they perform, and their hours
- Salon hours, weekly breaks, and date exceptions
- Counts for upcoming visits, visits starting today, and cancellations in the last 7 days

Every admin callback checks the Telegram user id again. Callback data is not treated as authorization.

## Architecture

```mermaid
flowchart TD
  telegram[Telegram] --> mode{BOT_MODE}
  mode -->|polling| dispatcher
  mode -->|webhook| web[aiohttp health and webhook]
  web --> dispatcher
  dispatcher --> customer[Customer handlers]
  dispatcher --> admin[Admin handlers]
  customer --> domain[Booking and schedule services]
  admin --> domain
  domain --> repos[Repositories]
  repos --> postgres[(PostgreSQL)]
  worker[Reminder loop] --> repos
  worker --> telegram
```

aiogram `MemoryStorage` only remembers which question the current process asked. The selected service, master, date, and time are stored in `booking_drafts`. After a restart the memory state is gone. The next **Book an appointment** tap reads the draft and offers Resume or Start over.

Reminders are rows, not in-memory jobs. The loop claims a due row and commits that claim before calling Telegram. A successful send is stored in a second transaction. This is at-least-once delivery. If Telegram accepts the message and the process dies before the `sent` status is saved, recovery can send that reminder again. Rows already marked `sent` are not sent again. A `processing` row older than five minutes is returned to `pending`, or to `failed` after five attempts, so a crash cannot leave it stuck.

Overlapping active bookings for one master are rejected by a PostgreSQL exclusion constraint. The confirm step also locks the candidate masters and checks the slot again. The button the customer pressed is not trusted on its own.

## Folder structure

```text
src/booking_bot/
  bot.py            polling, webhook, reminder loop
  config.py         environment settings
  web.py            /health and the Telegram webhook
  texts.py          customer and admin copy
  db/               SQLAlchemy models and the async session
  repositories/     queries
  services/         availability, booking, reminders, seed, admin edits
  handlers/         customer and admin Telegram handlers
  keyboards/        persistent reply menu and inline booking keyboards
alembic/            migrations
tests/              pytest
docker-compose.yml  optional local Postgres
```

## Requirements

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- PostgreSQL 15 or newer
- A Telegram bot token from BotFather

Docker is optional. It is only a way to run PostgreSQL.

## Local installation

```bash
uv sync
cp .env.example .env
```

Put `BOT_TOKEN` in `.env`. Do not commit `.env`.

### PostgreSQL with Docker

```bash
docker compose up -d
```

The compose file publishes `postgresql+asyncpg://booking:booking@localhost:5432/booking`. Create a second database for tests:

```bash
docker compose exec postgres psql -U booking -d booking -c "CREATE DATABASE booking_test;"
```

### PostgreSQL without Docker

Install PostgreSQL from the operating system or from the official installer, or use a server that is already running. Create a dedicated role and two databases:

```sql
CREATE USER booking WITH PASSWORD 'booking';
CREATE DATABASE booking OWNER booking;
CREATE DATABASE booking_test OWNER booking;
```

Set `DATABASE_URL` and `TEST_DATABASE_URL` to `postgresql+asyncpg://booking:booking@localhost:5432/booking` and `.../booking_test`. The test database name must contain `test`. Pytest drops and recreates the `public` schema there. Do not point `TEST_DATABASE_URL` at a database you care about, and do not point it at Supabase.

## Environment

| Variable | Purpose |
| --- | --- |
| `BOT_TOKEN` | BotFather token. The process refuses a bad token and does not write the token to the log. |
| `DATABASE_URL` | Runtime URL. It must start with `postgresql+asyncpg://`. |
| `MIGRATION_DATABASE_URL` | Optional direct URL for Alembic. Use this when `DATABASE_URL` is a pooler. |
| `ADMIN_IDS` | Comma-separated Telegram user ids. |
| `BOT_MODE` | `polling` or `webhook`. Run only one mode for a token. |
| `WEBHOOK_BASE_URL` | Public `https` origin. Required for webhook mode. |
| `WEBHOOK_SECRET` | At least 16 characters. Required for webhook mode. |
| `PORT` | HTTP port for webhook mode. Render sets this. |
| `TIMEZONE` | IANA name. Default `Europe/Kyiv`. Database timestamps stay in UTC. |
| `CURRENCY` | Label next to prices. Default `UAH`. |
| `SLOT_MINUTES` | Grid step. Default `30`. |
| `REMINDER_HOURS` | Hours before a visit, such as `24` or `24,2`. |
| `REMINDER_POLL_SECONDS` | How often a running process looks for due reminders. |
| `CANCEL_BEFORE_HOURS` | Customer cancellation window. Admins can cancel later. |
| `BOOKING_DAYS_AHEAD` | How far ahead dates are offered. |
| `MIN_LEAD_MINUTES` | Hide slots that start sooner than this. |
| `DRAFT_TTL_MINUTES` | Unfinished booking lifetime. |
| `LOG_LEVEL` | Default `INFO`. |
| `TEST_DATABASE_URL` | Disposable Postgres URL for integration tests. |

## Supabase

Use Supabase as hosted PostgreSQL for the demo.

1. Create a project and copy the direct connection string (port 5432) into `MIGRATION_DATABASE_URL`.
2. Put the runtime URL in `DATABASE_URL`. If you use the transaction pooler, keep `postgresql+asyncpg://` and expect prepared statements to be disabled in the client (`statement_cache_size=0` is already set).
3. Run Alembic with the direct URL. The pooler is a poor fit for migrations.
4. The free tier can pause, and it has connection and storage limits. That is acceptable for this demo. A salon that depends on the bot needs a database plan that stays available.

Do not run the demo seed against a database that already has real customers.

## Migrations and seed

```bash
uv run alembic upgrade head
uv run booking-bot seed
```

`booking-bot seed` upserts the fictional catalog: Чоловіча стрижка, Стрижка бороди, Стрижка та борода, Укладка волосся, and two fictional masters. If an older database still has the English names, the same rows are renamed in place. Running it again does not duplicate those names. The sample day off is 1 January 2030, so the current week stays bookable.

Alembic's first revision creates `btree_gist` and the tables defined on the SQLAlchemy models, including the overlap constraint on active bookings.

## Local polling

```bash
uv run booking-bot
```

`BOT_MODE=polling` long-polls Telegram and does not open an HTTP port. On startup it deletes any webhook for this token so polling and webhook are not active together. Stop the process with Ctrl+C.

## Webhook mode

`BOT_MODE=webhook` binds `0.0.0.0:$PORT`, serves `GET /health`, and accepts `POST /telegram/webhook`. Startup calls `set_webhook` with the secret and `drop_pending_updates=false`. Shutdown deletes that webhook. Requests without the matching `X-Telegram-Bot-Api-Secret-Token` header are rejected.

Do not run a polling process for the same token at the same time.

## Render

The free Render web service is a **demo host**, not reliable production hosting. It sleeps after a period without traffic. While it sleeps:

- the reminder loop is not running, so a reminder that becomes due can be late
- the next Telegram webhook is what usually wakes the process
- that first request can time out, and Telegram retries the update later

An always-on paid web service keeps the reminder loop alive and answers webhooks immediately. Paying for hosting removes the sleep delay. It still does not make Telegram delivery exactly-once. Do not tell a salon that the free demo bot is their live booking system.

Demo setup, only after you choose to deploy:

1. Create a Render web service from this repo.
2. Build with uv, then start `uv run booking-bot`.
3. Set `BOT_MODE=webhook`, `PORT` from Render, `WEBHOOK_BASE_URL` to the service `https` origin, and a long `WEBHOOK_SECRET`.
4. Set the database URLs from Supabase. Do not commit them.
5. Health check path: `/health`.
6. Run `uv run alembic upgrade head` and `uv run booking-bot seed` against that database before inviting people to the demo.
7. Confirm the webhook in the logs without printing the token. Add the public bot username to this README only after that check.

## Tests

```bash
uv run pytest
uv run pytest -q
```

Tests that do not need a database always run. Schedule exceptions, master availability, overlapping bookings, draft rows, and reminder recovery run only when `TEST_DATABASE_URL` is set to a disposable database whose name contains `test`. Otherwise those tests skip and pytest says why. They do not send real Telegram messages.

Docker is one way to get that database. A native PostgreSQL install is the other. See the installation section.

## Ruff

```bash
uv run ruff check src tests alembic
uv run ruff check --fix src tests alembic
```

## Backup

```bash
pg_dump "$MIGRATION_DATABASE_URL" --format=custom --file=barber-studio.dump
```

Use the direct database URL, not a transaction-pooler URL. Restore with `pg_restore` into an empty database. Do not put dumps in git.

## Security

- Tokens, webhook secrets, and database passwords stay in the environment. `.env` is gitignored.
- Error text for a bad token or database URL does not echo the supplied secret.
- Admin handlers check `ADMIN_IDS` on every update.
- Booking confirmation reloads the draft and the calendar inside a transaction.
- Health-check failures are logged without the exception text, because a driver error can contain the database URL.
- Portfolio screenshots must not include real customer chats or credentials.

## Free-hosting limits

Render's free web service sleeps. Reminders and the first webhook after sleep can be delayed. Supabase's free project can pause and has tight connection limits. This demo does not promise uninterrupted service. A production salon deploy needs an always-on app process and a database that does not pause.

## Troubleshooting

- The process exits on `DATABASE_URL`: the URL must start with `postgresql+asyncpg://`. SQLite is not used.
- Alembic fails through the pooler: set `MIGRATION_DATABASE_URL` to the direct 5432 URL.
- Buttons spin forever: an inline handler must call `callback.answer()`. The main menu is a persistent reply keyboard. Older inline menu messages still use the same callback ids.
- No slots: check salon hours, the master's hours, the 14:00–15:00 demo break, and that the service fits before closing.
- Two local processes for one token: stop the extra one. Polling and webhook must not run together.
- Integration tests skip: export `TEST_DATABASE_URL` for a database whose name contains `test`.

## Portfolio demonstration

What to show:

1. `/start` and the four buttons.
2. Services and the About screen, including the fictional address.
3. Book «Чоловіча стрижка» with a chosen master, then the summary and confirmation.
4. My bookings, then cancel outside the restricted window, or cancel as an admin.
5. `/admin`: upcoming list, a price change (old bookings keep the snapshot), and the schedule screen.

Capture those screens from the demo bot only. Do not include tokens or real customer data.

Customizing this for another barbershop, beauty salon, or nail studio means changing the seed data, hours, currency label, cancellation window, and the About copy. The booking, overlap, and reminder code stays the same.

Screenshot list: start menu, services, about, slot list, summary, confirmation, my bookings, admin upcoming list. A two-minute recording can follow the five steps above. Add the public demo username here only after a webhook deploy has been checked.
