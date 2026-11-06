import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from booking_bot.config import check_runtime_settings, load_settings
from booking_bot.handlers.customer.start import router as start_router
from booking_bot.log_setup import configure_logging

log = logging.getLogger(__name__)


def build_dispatcher() -> Dispatcher:
    """Build the dispatcher. Conversation state stays in MemoryStorage."""

    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(start_router)
    return dispatcher


async def run_polling() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    token = check_runtime_settings(settings)
    dispatcher = build_dispatcher()
    log.info("Token check passed. Starting long polling.")
    async with Bot(token=token) as bot:
        await dispatcher.start_polling(bot)
