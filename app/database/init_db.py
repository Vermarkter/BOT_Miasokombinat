from app.database import models  # noqa: F401
from app.database.sqlalchemy import Base, engine


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

