from collections.abc import MutableMapping
from typing import Any


class InMemoryStorage:
    def __init__(self) -> None:
        self._storage: MutableMapping[str, Any] = {}

    def set(self, key: str, value: Any) -> None:
        self._storage[key] = value

    def get(self, key: str) -> Any | None:
        return self._storage.get(key)

    def set_user_authorization(self, user_id: int, status: str) -> None:
        self.set(f"user:{user_id}:authorization", status)

    def get_user_authorization(self, user_id: int) -> str | None:
        value = self.get(f"user:{user_id}:authorization")
        return value if isinstance(value, str) else None


auth_storage = InMemoryStorage()
