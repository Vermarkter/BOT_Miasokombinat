import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeDefault

from app.database import init_db
from app.handlers import get_routers
from app.utils.logger import setup_logging
from config import get_settings


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    await init_db()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Головна / Авторизація"),
            BotCommand(command="order", description="Нова заявка"),
            BotCommand(command="history", description="Останні замовлення"),
            BotCommand(command="cart", description="Мій кошик"),
            BotCommand(command="support", description="Зв'язок з офісом"),
        ],
        scope=BotCommandScopeDefault(),
    )

    dp = Dispatcher()

    for router in get_routers():
        dp.include_router(router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
