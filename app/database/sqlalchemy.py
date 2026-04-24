from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Base(DeclarativeBase):
    pass


def _resolve_sqlite_path(database_url: str) -> Path | None:
    prefixes = ("sqlite+aiosqlite:///", "sqlite:///")
    if not database_url.startswith(prefixes):
        return None

    raw_path = database_url
    for prefix in prefixes:
        if raw_path.startswith(prefix):
            raw_path = raw_path[len(prefix) :]
            break

    if raw_path.startswith("file:"):
        raw_path = raw_path[5:]
    if raw_path.startswith("/") and len(raw_path) >= 3 and raw_path[2] == ":":
        raw_path = raw_path[1:]

    if raw_path.startswith("./"):
        return PROJECT_ROOT / raw_path[2:]

    path = Path(raw_path)
    if not path.is_absolute():
        return PROJECT_ROOT / raw_path
    return path


database_url = settings.database_url

db_path = _resolve_sqlite_path(database_url)
if db_path is not None:
    db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(database_url, echo=False, future=True)
session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
