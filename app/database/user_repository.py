from sqlalchemy import func, select, update

from app.database.models import User
from app.database.sqlalchemy import session_factory


class UserRepository:
    async def upsert_user(
        self,
        *,
        user_id: int,
        phone: str,
        full_name: str,
        is_active: bool = True,
    ) -> User:
        async with session_factory() as session:
            result = await session.execute(select(User).where(User.user_id == user_id))
            user = result.scalar_one_or_none()
            if user is None:
                user = User(
                    user_id=user_id,
                    phone=phone,
                    full_name=full_name,
                    is_active=is_active,
                )
                session.add(user)
            else:
                user.phone = phone
                user.full_name = full_name
                user.is_active = is_active

            await session.commit()
            await session.refresh(user)
            return user

    async def set_is_active(self, user_id: int, is_active: bool) -> None:
        async with session_factory() as session:
            await session.execute(
                update(User).where(User.user_id == user_id).values(is_active=is_active),
            )
            await session.commit()

    async def list_active_user_ids(self) -> list[int]:
        async with session_factory() as session:
            result = await session.execute(
                select(User.user_id).where(User.is_active.is_(True)).order_by(User.user_id.asc()),
            )
            return [int(row[0]) for row in result.all()]

    async def count_users(self) -> int:
        async with session_factory() as session:
            result = await session.execute(select(func.count()).select_from(User))
            return int(result.scalar_one() or 0)

    async def count_active_users(self) -> int:
        async with session_factory() as session:
            result = await session.execute(
                select(func.count()).select_from(User).where(User.is_active.is_(True)),
            )
            return int(result.scalar_one() or 0)
