from aiogram import Router

from app.handlers.start import router as start_router


def get_routers() -> list[Router]:
    return [start_router]
