from aiogram import Router

from booking_bot.handlers.customer.booking import register_booking
from booking_bot.handlers.customer.menu import register_fallback, register_menu


def create_customer_router() -> Router:
    router = Router(name="customer")
    register_menu(router)
    register_booking(router)
    register_fallback(router)
    return router
