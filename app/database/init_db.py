from sqlalchemy import inspect, text

from app.database import models  # noqa: F401
from app.database.sqlalchemy import Base, engine


def _ensure_user_columns(connection: object) -> None:
    inspector = inspect(connection)
    if "users" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("users")}
    if "agent_id" not in existing_columns:
        connection.execute(text("ALTER TABLE users ADD COLUMN agent_id VARCHAR(64)"))
    if "agent_name" not in existing_columns:
        connection.execute(text("ALTER TABLE users ADD COLUMN agent_name VARCHAR(255)"))


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_user_columns)
