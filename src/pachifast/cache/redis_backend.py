"""Redis cache, shared between workers and hosts. Needs the optional ``redis`` package (``pachifast[redis]``)."""

from typing import Any


class RedisCache:
    """Cache stored in Redis under ``<prefix><key>``; the expiry is Redis' own."""

    def __init__(self, url: str, prefix: str = "pachifast:", client: Any = None) -> None:
        if client is None:
            try:
                from redis.asyncio import Redis
            except ImportError as exc:  # pragma: no cover - depends on the installation
                raise RuntimeError(
                    "cache_url is a Redis URL but the 'redis' package is missing: install pachifast[redis]"
                ) from exc
            client = Redis.from_url(url, decode_responses=True)
        self._client = client
        self._prefix = prefix

    def _key(self, key: str) -> str:
        """The key as stored in Redis."""
        return f"{self._prefix}{key}"

    async def get(self, key: str) -> str | None:
        """The value of ``key``, or ``None`` if it is missing or expired."""
        return await self._client.get(self._key(key))

    async def set(self, key: str, value: str, ttl: float) -> None:
        """Store ``value`` for ``ttl`` seconds (millisecond precision)."""
        await self._client.set(self._key(key), value, px=max(1, int(ttl * 1000)))

    async def add(self, key: str, value: str, ttl: float) -> bool:
        """``SET key value NX PX ttl``: stored only if the key is absent."""
        return bool(await self._client.set(self._key(key), value, px=max(1, int(ttl * 1000)), nx=True))

    async def incr(self, key: str, ttl: float) -> int:
        """``INCR`` and, on the first increment, ``PEXPIRE``: the counter lives ``ttl`` seconds."""
        count = int(await self._client.incr(self._key(key)))
        if count == 1:
            await self._client.pexpire(self._key(key), max(1, int(ttl * 1000)))
        return count

    async def delete(self, key: str) -> None:
        """Forget ``key``."""
        await self._client.delete(self._key(key))

    async def pop(self, key: str) -> str | None:
        """Atomic read and delete: a ``MULTI``/``EXEC`` transaction, so it works on every Redis version (``GETDEL``
        needs 6.2)."""
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.get(self._key(key))
            pipe.delete(self._key(key))
            value, _ = await pipe.execute()
        return value

    async def close(self) -> None:
        """Close the connection pool."""
        await self._client.aclose()
