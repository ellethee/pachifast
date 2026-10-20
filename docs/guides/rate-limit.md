# Rate limit

pachifast limits by itself the routes that anyone can reach (login, registration, the second-factor check: `auth.auth_rate_limit`). For **your own routes**, the expensive ones above all, use the `rate_limit()` dependency.

```python
from fastapi import Depends
from pachifast.ratelimit import rate_limit

@router.post(
    "/convert",
    dependencies=[Depends(rate_limit(30, 60, key="user", scope="convert"))],
)
async def convert(...): ...
```

That allows each user 30 requests per 60 seconds to the route. The 31st answers `429` until the window ends.

## Parameters

| Parameter | Meaning |
| --- | --- |
| `limit` | Requests allowed per window. `None` (default): the setting `rate_limit.default`, which is `0`, so **nothing is limited unless you ask**. `0` switches the route off. |
| `window` | Window in seconds. `None` (default): the setting `rate_limit.window_seconds` (60). |
| `key` | What the requests are counted by: `"ip"` (default, the client address; an IPv6 address counts as its /64 network), `"user"` (the user of a valid access token; the address for an anonymous request or a bad token) or a function `(request) -> str \| None`, sync or async (`None` falls back to the address). |
| `default` | The limit of an app that wants the route limited without anyone setting `rate_limit.default`: it applies only when neither `limit` nor the setting gives a limit, so an operator can still switch it off with `rate_limit.default: 0`. Default `0`. |
| `scope` | Name of the counter. Routes with the same scope share one budget; different scopes are independent. Default `"default"`: give each limit its own name. |

A number given to the route wins over the settings. To limit a whole group of routes with one value, set `rate_limit.default` in `pachifast.yaml` and use `rate_limit()` with no limit on those routes:

```yaml
rate_limit:
  default: 120
  window_seconds: 60
```

A library or an app that wants its expensive routes limited out of the box writes `rate_limit(default=60, key="user", scope="convert")`: the limit is 60 until the operator sets `rate_limit.default` (a number to change it for every route that gives no limit of its own, `0` to switch them off).

Use `key="user"` on authenticated routes: the token is only read (its signature and expiry are checked, the database is not queried), so the count is per account even behind a shared address. Authentication itself is still done by the route as usual.

## What is counted

- **The dependency runs after the router's authentication.** On a route of an authenticated router, a request without a valid token is answered `401` before the dependency runs, so it is not counted. That is usually what you want (a rejected request costs nothing), but it means the limit does not protect the authentication step itself: login, registration and the second-factor check have their own limit, `auth.auth_rate_limit`. On a public route the dependency counts everybody.
- **`key="user"` trusts the token, not the database.** The signature, the expiry and the token type are checked, but the user is not loaded: a token of a user that was deleted or disabled after the token was issued still gets its own counter until it expires. The route's own authentication refuses such a token anyway; the counter only decides whose budget a request spends.
- **Two error codes.** The login, registration and second-factor check answer `auth.rate_limited` ("too many requests from this address"); `rate_limit()` answers `api.rate_limited`. Clients that translate by `code` need both.

## The answer

Every response of a limited route carries:

| Header | Value |
| --- | --- |
| `X-RateLimit-Limit` | Requests allowed per window. |
| `X-RateLimit-Remaining` | Requests left in this window. |
| `X-RateLimit-Reset` | Seconds until the window ends. |

Past the limit the route answers `429` with these headers plus `Retry-After`, and the usual error body with the code `api.rate_limited`:

```json
{ "detail": [{ "loc": [], "msg": "Too many requests: try again later", "type": "value_error", "code": "api.rate_limited" }] }
```

The headers are listed in the CORS `Access-Control-Expose-Headers`, so a browser client can read them.

## Where the count lives, and proxies

The counter is kept in the shared [cache](cache.md): in the memory of each process by default, in Redis when `cache.url` is set. **With several workers or servers, use Redis**, or each process counts apart and the real limit is multiplied.

The window is fixed (it starts at multiples of its length), so a client can use the limit at the end of a window and again at the start of the next one.

Behind a reverse proxy every request looks like it comes from the proxy unless uvicorn trusts the forwarded address: run it with `--proxy-headers` (and `--forwarded-allow-ips` for your proxy); `pachifast serve` does it for you. Without that, `key="ip"` puts all clients in one budget.
