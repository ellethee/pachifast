"""In-process cache: bounded (least recently used entries go first) and with expiry. One per process."""

import time
from collections import OrderedDict


class MemoryCache:
    """Cache kept in the memory of the process; not shared between workers."""

    def __init__(self, max_entries: int = 10_000) -> None:
        self._max_entries = max(1, max_entries)
        self._items: OrderedDict[str, tuple[str, float]] = OrderedDict()

    def _purge_expired(self, now: float) -> None:
        """Drop every expired entry."""
        for key in [key for key, (_, expires_at) in self._items.items() if expires_at <= now]:
            del self._items[key]

    async def get(self, key: str) -> str | None:
        """The value of ``key``, or ``None`` if it is missing or expired."""
        item = self._items.get(key)
        if item is None:
            return None
        if item[1] <= time.monotonic():
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return item[0]

    async def set(self, key: str, value: str, ttl: float) -> None:
        """Store ``value`` for ``ttl`` seconds; when the cache is full expired entries, then the oldest, go."""
        now = time.monotonic()
        self._items[key] = (value, now + ttl)
        self._items.move_to_end(key)
        if len(self._items) > self._max_entries:
            self._purge_expired(now)
        while len(self._items) > self._max_entries:
            self._items.popitem(last=False)

    async def add(self, key: str, value: str, ttl: float) -> bool:
        """Store ``value`` only if ``key`` is absent (atomic: there is no ``await`` between check and store)."""
        if await self.get(key) is not None:
            return False
        await self.set(key, value, ttl)
        return True

    async def incr(self, key: str, ttl: float) -> int:
        """Add one to the counter ``key`` (atomic: there is no ``await`` between read and write)."""
        item = self._items.get(key)
        now = time.monotonic()
        if item is None or item[1] <= now:
            await self.set(key, "1", ttl)
            return 1
        count = int(item[0]) + 1
        self._items[key] = (str(count), item[1])  # the expiry stays the one of the first increment
        self._items.move_to_end(key)
        return count

    async def delete(self, key: str) -> None:
        """Forget ``key``."""
        self._items.pop(key, None)

    async def pop(self, key: str) -> str | None:
        """Read and delete ``key`` (atomic: there is no ``await`` between the two steps)."""
        value = await self.get(key)
        self._items.pop(key, None)
        return value

    async def close(self) -> None:
        """Nothing to release."""
