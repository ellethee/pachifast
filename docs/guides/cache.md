# Cache

pachifast ships a small key/value cache with expiry, used internally for short-lived data. By default it lives in the memory of the process; point it at Redis to share it between workers and hosts.

## What uses the cache today

| Feature | Key (before prefix) | Lifetime |
| --- | --- | --- |
| Activity log: the per-superuser choice "do not record my requests" (`audit.disable_activity_log`) | `activity-skip:<user id>` | 30 seconds |
| WebSocket tickets, only when `events_ticket_store=cache` | `ws-ticket:<sha-256 of the ticket>` | 30 seconds |

See [Events and activity log](events.md). The cache is a good fit for small, short-lived, disposable values; do not use it as a database.

## Choose a backend

| | `MemoryCache` (default) | `RedisCache` |
| --- | --- | --- |
| Selected when | `cache_url` is empty | `cache_url` is set |
| Shared between workers and hosts | No, one cache per process | Yes |
| Bounded | Yes, `cache_max_entries` | By Redis |
| Expiry | Per entry, checked on access | Redis' own (millisecond precision) |
| Extra dependency | None | `pachifast[redis]` |

### In memory

No configuration is required. The cache holds at most `cache_max_entries` entries (default `10000`). When it is full, expired entries are dropped first, then the least recently used ones (LRU).

```env
PACHIFAST_CACHE_MAX_ENTRIES=5000
```

### Redis

Install the optional extra and set the URL:

```bash
uv add "pachifast[redis]"
```

```env
PACHIFAST_CACHE_URL=redis://localhost:6379/0
PACHIFAST_CACHE_PREFIX=myapp:
```

| Setting | Default | Description |
| --- | --- | --- |
| `cache_url` | empty | Empty means in-memory. Otherwise a `redis://`, `rediss://` (TLS) or `unix://` URL. Any other value is rejected at startup. |
| `cache_prefix` | `pachifast:` | Prepended to every key. Use a different prefix per application when several share one Redis. |
| `cache_max_entries` | `10000` | Size limit of the in-memory cache. Ignored with Redis. |

Requirements and notes:

- **Any Redis version**: atomic read-and-delete is a `MULTI`/`EXEC` transaction (no `GETDEL`, which needs Redis 6.2).
- If `cache_url` is a Redis URL but the `redis` package is not installed, the first call to `get_cache()` raises an error that tells you to install `pachifast[redis]`.
- Put credentials in the URL through an environment variable or `.env`, not in a committed YAML file.

All settings can also be given in the YAML file (`cache_url`, `cache_prefix`, `cache_max_entries`). See [Configuration](configuration.md) and the [settings reference](../reference/settings.md).

## Use it from your code

```python
from pachifast.cache import get_cache

cache = get_cache()

await cache.set("report:42", "ready", ttl=60)  # seconds
value = await cache.get("report:42")  # "ready", or None when missing or expired
once = await cache.pop("job:7")  # read and delete atomically
await cache.delete("report:42")  # no error if missing
```

`get_cache()` returns the shared instance. It is rebuilt automatically if `cache_url`, `cache_prefix` or `cache_max_entries` change. `reset_cache()` forgets the instance (useful in tests).

The interface, `pachifast.cache.Cache`, is deliberately small:

| Method | Description |
| --- | --- |
| `await get(key) -> str \| None` | The value, or `None` if missing or expired. |
| `await set(key, value, ttl)` | Store a string for `ttl` seconds. |
| `await delete(key)` | Forget the key. |
| `await pop(key) -> str \| None` | Atomic read and delete: with concurrent callers only one gets the value. |
| `await close()` | Release connections (closes the Redis pool; does nothing in memory). |

Notes:

- Keys and values are **strings**. Serialise structured data yourself (for example with `json.dumps`).
- Keys are namespaced by the backend: with Redis the stored key is `<cache_prefix><key>`. Choose your own keys so they do not collide with the ones listed above.
- `get_cache()` is the only supported way to obtain a cache; the classes `MemoryCache` and `RedisCache` are exported for tests and advanced use.

## Failure behaviour

| Situation | Behaviour |
| --- | --- |
| Redis unreachable while reading or writing the activity-log choice | A warning is logged; the setting is read from the database instead, and the request is not affected. |
| Redis unreachable while issuing or redeeming a WebSocket ticket (`events_ticket_store=cache`) | The request fails with a server error. Use `events_ticket_store=database` if you cannot depend on the cache. |
| Invalid `cache_url` scheme | The application refuses to start with a validation error. |
| Your own code | Calls raise the Redis client's exceptions: handle them as you would for any network call. |

## Several workers

With the in-memory backend every worker has its own cache. That is harmless for the activity-log choice (worst case, each worker reads the database once every 30 seconds), but it breaks WebSocket tickets stored in the cache: a ticket issued by one worker cannot be redeemed by another. The simplest options are:

1. keep the default `events_ticket_store=database`, which works across workers and hosts without Redis; or
2. set `cache_url` to a Redis server and use `events_ticket_store=cache`.

See [Deployment](deployment.md) for running several workers.
