import asyncio
import sys

from booking_bot.bot import run
from booking_bot.config import load_settings
from booking_bot.db.session import dispose_engine, init_engine
from booking_bot.services.seed import seed_demo


async def seed() -> None:
    settings = load_settings()
    if not settings.database_url.strip().startswith("postgresql+asyncpg://"):
        raise SystemExit("DATABASE_URL must start with postgresql+asyncpg://.")
    factory = init_engine(settings.database_url)
    async with factory() as session:
        async with session.begin():
            await seed_demo(session)
    await dispose_engine()


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "run"
    if command == "seed":
        asyncio.run(seed())
    elif command == "run":
        asyncio.run(run())
    else:
        raise SystemExit("Usage: booking-bot [run|seed]")


if __name__ == "__main__":
    main()
