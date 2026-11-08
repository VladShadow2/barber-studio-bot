from unittest.mock import AsyncMock

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiohttp.test_utils import TestClient, TestServer

from booking_bot.bot import build_dispatcher
from booking_bot.web import build_web_app

TOKEN = "123456789:" + ("A" * 35)


class _QuietSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()

    async def close(self) -> None:
        return None

    async def make_request(self, bot, method, timeout=None):
        return None

    async def stream_content(
        self,
        url,
        headers=None,
        timeout=30,
        chunk_size=65536,
        raise_for_status=True,
    ):
        yield b""


class _QuietSession:
    async def close(self) -> None:
        return None


async def test_health_and_webhook_secret() -> None:
    bot = Bot(token=TOKEN, session=_QuietSession())
    app = build_web_app(
        bot,
        build_dispatcher(),
        "s" * 16,
        health_check=AsyncMock(return_value=True),
    )
    async with TestClient(TestServer(app)) as client:
        ready = await client.get("/health")
        assert ready.status == 200
        assert await ready.json() == {"status": "ok"}
        denied = await client.post("/telegram/webhook", json={"update_id": 1})
        assert denied.status == 401
        accepted = await client.post(
            "/telegram/webhook",
            json={"update_id": 1},
            headers={"X-Telegram-Bot-Api-Secret-Token": "s" * 16},
        )
        assert accepted.status != 401
    down = build_web_app(
        bot,
        build_dispatcher(),
        "s" * 16,
        health_check=AsyncMock(return_value=False),
    )
    async with TestClient(TestServer(down)) as client:
        response = await client.get("/health")
        assert response.status == 503
