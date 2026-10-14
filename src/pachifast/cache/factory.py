"""The cache selected by the settings: Redis when ``cache_url`` is set, otherwise in the memory of the process."""

from pachifast.config import get_settings

from .base import Cache
from .memory import MemoryCache
from .redis_backend import RedisCache

_current: tuple[tuple[str, str, int], Cache] | None = None


def get_cache() -> Cache:
    """The shared cache; it is built again if the settings it depends on changed (tests, ``cache_clear``)."""
    global _current
    settings = get_settings()
    config = (settings.cache_url, settings.cache_prefix, settings.cache_max_entries)
    if _current is None or _current[0] != config:
        url, prefix, max_entries = config
        cache: Cache = RedisCache(url, prefix) if url else MemoryCache(max_entries)
        _current = (config, cache)
    return _current[1]


def reset_cache() -> None:
    """Forget the cache instance (the next ``get_cache`` builds a new one)."""
    global _current
    _current = None
