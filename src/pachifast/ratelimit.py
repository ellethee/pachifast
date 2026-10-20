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

import inspect
import ipaddress
import time
from collections.abc import Awaitable, Callable
from typing import Literal

import jwt
from fastapi import Request, Response, status

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


KeyFunction = Callable[[Request], "str | None | Awaitable[str | None]"]
RateLimitKey = Literal["ip", "user"] | KeyFunction


def _bearer_subject(request: Request) -> str | None:
    """The user id of a valid access token in the ``Authorization`` header, without touching the database."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    settings = get_settings()
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM], options={"require": ["exp", "sub"]}
        )
    except jwt.PyJWTError:
        return None
    return str(payload["sub"]) if payload.get("type") == "access" else None


async def _who(request: Request, key: RateLimitKey) -> str:
    """The name a request is counted under: ``key:<value>``, ``user:<id>`` or ``ip:<address>``."""
    if callable(key):
        value = key(request)
        value = await value if inspect.isawaitable(value) else value
        if value:
            return f"key:{value}"
    elif key == "user" and (subject := _bearer_subject(request)):
        return f"user:{subject}"
    return f"ip:{_client_key(request.client.host if request.client else None)}"


def rate_limit(
    limit: int | None = None,
    window: int | None = None,
    *,
    key: RateLimitKey = "ip",
    scope: str = "default",
    default: int = 0,
) -> Callable[[Request, Response], Awaitable[None]]:
    """Build a FastAPI dependency that allows ``limit`` requests per ``window`` seconds to a route.

    Use it on the routes that are expensive or that anyone can reach::

        @router.post("/convert", dependencies=[Depends(rate_limit(30, 60, key="user", scope="convert"))])

    - ``limit`` and ``window`` default to the settings ``rate_limit_default`` (``0``: no limit, which is the default)
      and ``rate_limit_window_seconds``; a number given here wins for that route, and ``0`` switches the route off.
    - ``default`` is the limit of an app that wants the route limited without anyone setting ``rate_limit_default``:
      it applies only when neither ``limit`` nor the setting (environment, ``.env``, ``pachifast.yaml``) gives one,
      so an operator can still switch the limit off with ``rate_limit.default: 0``.
    - ``key`` is what the requests are counted by: ``"ip"`` (the client address, an IPv6 address as its /64 network),
      ``"user"`` (the user of a valid access token, the address when the request is anonymous) or a function
      ``(request) -> str | None`` (sync or async; ``None`` falls back to the address).
    - ``scope`` names the counter: routes with the same scope share one budget, different scopes are independent.

    The count lives in the shared cache (:mod:`pachifast.cache`): the memory of the process by default, Redis when
    ``cache_url`` is set, which several workers need to share one count. Every answer carries ``X-RateLimit-Limit``,
    ``X-RateLimit-Remaining`` and ``X-RateLimit-Reset`` (seconds to the end of the window); past the limit the route
    answers ``429 api.rate_limited`` with ``Retry-After``. Behind a reverse proxy run uvicorn with the proxy headers
    (``pachifast serve`` does), or every client looks like the proxy.
    """

    async def check(request: Request, response: Response) -> None:
        """Count the request and refuse it once the budget of the window is spent."""
        settings = get_settings()
        if limit is not None:
            max_requests = limit
        elif "rate_limit_default" in settings.model_fields_set:
            max_requests = settings.rate_limit_default
        else:
            max_requests = default
        if max_requests <= 0:
            return
        seconds = window or settings.rate_limit_window_seconds
        who = await _who(request, key)
        now = time.time()
        bucket = int(now // seconds)
        count = await get_cache().incr(f"ratelimit:{scope}:{who}:{bucket}", seconds)
        reset = max(1, int((bucket + 1) * seconds - now) + 1)
        headers = {
            "X-RateLimit-Limit": str(max_requests),
            "X-RateLimit-Remaining": str(max(0, max_requests - count)),
            "X-RateLimit-Reset": str(reset),
        }
        if count > max_requests:
            raise api_error(
                status.HTTP_429_TOO_MANY_REQUESTS, "api.rate_limited", headers={**headers, "Retry-After": str(reset)}
            )
        response.headers.update(headers)

    return check
