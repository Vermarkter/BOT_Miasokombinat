from sqlalchemy import delete, func, select

from app.database.models import CartItem
from app.database.sqlalchemy import session_factory


class CartRepository:
    async def list_items(self, user_id: int) -> list[CartItem]:
        async with session_factory() as session:
            result = await session.execute(
                select(CartItem).where(CartItem.user_id == user_id).order_by(CartItem.id.asc()),
            )
            return list(result.scalars().all())

    async def get_item(self, user_id: int, product_id: str) -> CartItem | None:
        async with session_factory() as session:
            result = await session.execute(
                select(CartItem).where(
                    CartItem.user_id == user_id,
                    CartItem.product_id == product_id,
                ),
            )
            return result.scalar_one_or_none()

    async def upsert_item(
        self,
        user_id: int,
        product_id: str,
        product_name: str,
        quantity: float,
        price: float,
        unit: str,
    ) -> CartItem:
        async with session_factory() as session:
            result = await session.execute(
                select(CartItem).where(
                    CartItem.user_id == user_id,
                    CartItem.product_id == product_id,
                ),
            )
            cart_item = result.scalar_one_or_none()
            if cart_item is None:
                cart_item = CartItem(
                    user_id=user_id,
                    product_id=product_id,
                    product_name=product_name,
                    quantity=quantity,
                    price=price,
                    unit=unit,
                )
                session.add(cart_item)
            else:
                cart_item.product_name = product_name
                cart_item.quantity = quantity
                cart_item.price = price
                cart_item.unit = unit

            await session.commit()
            await session.refresh(cart_item)
            return cart_item

    async def delete_item(self, user_id: int, product_id: str) -> None:
        async with session_factory() as session:
            await session.execute(
                delete(CartItem).where(
                    CartItem.user_id == user_id,
                    CartItem.product_id == product_id,
                ),
            )
            await session.commit()

    async def clear_cart(self, user_id: int) -> None:
        async with session_factory() as session:
            await session.execute(delete(CartItem).where(CartItem.user_id == user_id))
            await session.commit()

    async def count_items(self) -> int:
        async with session_factory() as session:
            result = await session.execute(select(func.count()).select_from(CartItem))
            return int(result.scalar_one() or 0)
