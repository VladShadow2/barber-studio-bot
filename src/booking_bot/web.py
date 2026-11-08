import logging

from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import web
from sqlalchemy import text

from booking_bot.db.session import get_sessionmaker

log = logging.getLogger(__name__)


async def database_ready() -> bool:
    try:
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        log.warning("Health check could not reach the database")
        return False
    return True


def build_web_app(
    bot: Bot,
    dispatcher: Dispatcher,
    secret: str,
    health_check=database_ready,
) -> web.Application:
    app = web.Application()

    async def health(_request: web.Request) -> web.Response:
        if await health_check():
            return web.json_response({"status": "ok"})
        return web.json_response({"status": "unavailable"}, status=503)

    app.router.add_get("/health", health)
    SimpleRequestHandler(dispatcher=dispatcher, bot=bot, secret_token=secret).register(
        app, path="/telegram/webhook"
    )
    return app
