from aiogram import Router

from app.handlers.order import router as order_router
from app.handlers.start import router as start_router


def get_routers() -> list[Router]:
    return [start_router, order_router]
