"""The cache interface every backend implements."""

from typing import Protocol


class Cache(Protocol):
    """A small key/value cache with expiry. Values are strings; keys are namespaced by the backend."""

    async def get(self, key: str) -> str | None:
        """The value of ``key``, or ``None`` if it is missing or expired."""

    async def set(self, key: str, value: str, ttl: float) -> None:
        """Store ``value`` for ``ttl`` seconds."""

    async def add(self, key: str, value: str, ttl: float) -> bool:
        """Store ``value`` for ``ttl`` seconds only if ``key`` is absent; ``True`` if it was stored.

        Atomic: of several concurrent callers with the same key exactly one gets ``True``.
        """

    async def incr(self, key: str, ttl: float) -> int:
        """Add one to the counter ``key`` (it starts at 0) and return the new value; the counter lives ``ttl`` seconds
        from its first increment.

        Atomic: concurrent callers get different values.
        """

    async def delete(self, key: str) -> None:
        """Forget ``key`` (no error if it is missing)."""

    async def pop(self, key: str) -> str | None:
        """Atomically read and delete ``key``: of several concurrent callers only one gets the value."""

    async def close(self) -> None:
        """Release the connections, if any."""
