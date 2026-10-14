import asyncio

import fakeredis
import pytest

from pachifast.cache import MemoryCache, RedisCache, get_cache, reset_cache
from pachifast.config import get_settings


def _run(coro):
    return asyncio.run(coro)


def test_memory_cache_expires_and_pops(monkeypatch: pytest.MonkeyPatch) -> None:
    from pachifast.cache import memory

    now = [100.0]
    monkeypatch.setattr(memory.time, "monotonic", lambda: now[0])

    async def scenario() -> None:
        cache = MemoryCache()
        await cache.set("a", "1", 10)
        assert await cache.get("a") == "1"
        assert await cache.pop("a") == "1"
        assert await cache.pop("a") is None
        await cache.set("b", "2", 10)
        now[0] += 11
        assert await cache.get("b") is None

    _run(scenario())


def test_memory_cache_is_bounded_and_drops_the_oldest() -> None:
    async def scenario() -> None:
        cache = MemoryCache(max_entries=3)
        for index in range(5):
            await cache.set(f"k{index}", "v", 60)
        assert [await cache.get(f"k{index}") for index in range(5)] == [None, None, "v", "v", "v"]

    _run(scenario())


def test_redis_cache_uses_prefix_expiry_and_getdel() -> None:
    async def scenario() -> None:
        client = fakeredis.FakeAsyncRedis(decode_responses=True)
        cache = RedisCache("redis://unused", prefix="app:", client=client)
        await cache.set("k", "v", 5)
        assert await client.get("app:k") == "v"
        assert 0 < await client.pttl("app:k") <= 5000
        assert await cache.get("k") == "v"
        assert await cache.pop("k") == "v"
        assert await cache.pop("k") is None
        await cache.delete("missing")

    _run(scenario())


def test_factory_picks_the_backend_from_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PACHIFAST_CACHE_URL", raising=False)
    get_settings.cache_clear()
    reset_cache()
    assert isinstance(get_cache(), MemoryCache)
    monkeypatch.setenv("PACHIFAST_CACHE_URL", "redis://localhost:6379/0")
    get_settings.cache_clear()
    assert isinstance(get_cache(), RedisCache)
    monkeypatch.delenv("PACHIFAST_CACHE_URL")
    get_settings.cache_clear()
    reset_cache()


def test_cache_url_must_be_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PACHIFAST_CACHE_URL", "http://nope")
    get_settings.cache_clear()
    with pytest.raises(Exception, match="cache_url"):
        get_settings()
    monkeypatch.delenv("PACHIFAST_CACHE_URL")
    get_settings.cache_clear()
