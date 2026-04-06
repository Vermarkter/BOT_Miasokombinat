from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
database_url = settings.database_url

# Ensure local sqlite directory exists for default relative path.
if database_url.startswith("sqlite+aiosqlite:///./"):
    db_file = database_url.replace("sqlite+aiosqlite:///./", "", 1)
    db_path = Path(db_file)
    db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(database_url, echo=False, future=True)
session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

