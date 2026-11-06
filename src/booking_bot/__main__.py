import asyncio

from booking_bot.bot import run_polling


def main() -> None:
    asyncio.run(run_polling())


if __name__ == "__main__":
    main()
