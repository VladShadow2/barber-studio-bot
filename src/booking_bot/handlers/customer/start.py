from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from booking_bot.texts import START

router = Router(name="customer-start")


@router.message(CommandStart())
async def command_start(message: Message) -> None:
    await message.answer(START)
