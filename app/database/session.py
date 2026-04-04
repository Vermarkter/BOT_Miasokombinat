from collections.abc import MutableMapping
from typing import Any


class InMemoryStorage:
    def __init__(self) -> None:
        self._storage: MutableMapping[str, Any] = {}

    def set(self, key: str, value: Any) -> None:
        self._storage[key] = value

    def get(self, key: str) -> Any | None:
        return self._storage.get(key)
