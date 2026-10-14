"""Rate limit per client address for the routes that anyone can reach: login, registration, second-factor check.

The account lockout (``login_max_attempts``) protects one account from guessing; this protects the server from a
single client that tries many accounts, or registers many. It counts the requests of each address in a fixed
window, in the shared cache (:mod:`pachifast.cache`): in the memory of the process by default, in Redis when
``cache_url`` is set, which is what several workers need to share one count.

It is off by default (``auth_rate_limit=0``): behind a reverse proxy that does not forward the client address every
request would look like it comes from the proxy and share one budget. Run uvicorn with the proxy headers
(``pachifast serve`` does) before turning it on.
"""

from __future__ import annotations

import ipaddress
import time

from fastapi import Request, status

from pachifast.cache import get_cache
from pachifast.config import get_settings
from pachifast.i18n import api_error


def _client_key(host: str | None) -> str:
    """The key a client address is counted under: the /64 network for IPv6, the address itself otherwise."""
    if not host:
        return "unknown"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(address, ipaddress.IPv6Address):
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


async def enforce_auth_rate_limit(request: Request) -> None:
    """FastAPI dependency: ``429 auth.rate_limited`` (with ``Retry-After``) once the address used its requests.

    One atomic counter per address and window (``cache.incr``, also in Redis), so parallel requests cannot all slip
    under the limit. An IPv6 address counts as its /64 network: one client owns a whole /64, and would otherwise
    change address at every request.
    """
    settings = get_settings()
    limit = settings.auth_rate_limit
    if limit <= 0:
        return
    window = settings.auth_rate_window_seconds
    address = _client_key(request.client.host if request.client else None)
    now = time.time()
    bucket = int(now // window)
    if await get_cache().incr(f"ratelimit:{address}:{bucket}", window) <= limit:
        return
    retry_after = max(1, int((bucket + 1) * window - now) + 1)
    raise api_error(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "auth.rate_limited",
        loc=("body",),
        headers={"Retry-After": str(retry_after)},
    )
