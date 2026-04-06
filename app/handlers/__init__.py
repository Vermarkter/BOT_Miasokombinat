from aiogram import Router

from app.handlers.admin import router as admin_router
from app.handlers.order import router as order_router
from app.handlers.start import router as start_router
from app.handlers.status import router as status_router


def get_routers() -> list[Router]:
    return [start_router, admin_router, order_router, status_router]
