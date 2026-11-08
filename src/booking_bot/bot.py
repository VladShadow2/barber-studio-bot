import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

from booking_bot.config import check_runtime_settings, load_settings, normalized_bot_mode
from booking_bot.db.session import get_sessionmaker, init_engine
from booking_bot.handlers.admin.panel import create_admin_router
from booking_bot.handlers.customer.start import create_customer_router
from booking_bot.handlers.master.schedule import create_master_router
from booking_bot.log_setup import configure_logging
from booking_bot.services.reminders import run_reminder_loop
from booking_bot.web import build_web_app

log = logging.getLogger(__name__)


def build_dispatcher() -> Dispatcher:
    """Build a new dispatcher. Each call gets its own routers and memory storage."""

    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(create_admin_router())
    dispatcher.include_router(create_master_router())
    dispatcher.include_router(create_customer_router())
    return dispatcher


async def run() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    token = check_runtime_settings(settings)
    init_engine(settings.database_url)
    if normalized_bot_mode(settings) == "webhook":
        await run_webhook(settings, token)
    else:
        await run_polling(settings, token)


async def run_polling(settings, token: str) -> None:
    dispatcher = build_dispatcher()
    stop = asyncio.Event()
    async with Bot(token=token) as bot:
        await bot.delete_webhook(drop_pending_updates=False)
        worker = asyncio.create_task(_remind(bot, settings, stop))
        log.info("Token check passed. Starting long polling.")
        try:
            await dispatcher.start_polling(bot)
        finally:
            stop.set()
            worker.cancel()


async def run_webhook(settings, token: str) -> None:
    bot = Bot(token=token)
    dispatcher = build_dispatcher()
    stop = asyncio.Event()
    app = build_web_app(bot, dispatcher, settings.webhook_secret.strip())
    worker = asyncio.create_task(_remind(bot, settings, stop))

    async def start_webhook(_app: web.Application) -> None:
        url = settings.webhook_base_url.strip().rstrip("/") + "/telegram/webhook"
        await bot.set_webhook(url=url, secret_token=settings.webhook_secret.strip(), drop_pending_updates=False)  # noqa: E501
        log.info("Webhook mode is listening on port %s.", settings.port)

    async def stop_webhook(_app: web.Application) -> None:
        stop.set()
        worker.cancel()
        await bot.delete_webhook(drop_pending_updates=False)

    app.on_startup.append(start_webhook)
    app.on_cleanup.append(stop_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=settings.port)
    await site.start()
    log.info("Token check passed. Starting webhook server.")
    try:
        await asyncio.Event().wait()
    finally:
        stop.set()
        worker.cancel()
        await runner.cleanup()


async def _remind(bot: Bot, settings, stop: asyncio.Event) -> None:
    async def send(telegram_id: int, text: str) -> None:
        await bot.send_message(telegram_id, text)

    await run_reminder_loop(get_sessionmaker(), send, settings, stop)
