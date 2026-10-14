# Routing and responses

pachifast builds on FastAPI routers and adds three things on top: automatic prefixes, tags and authentication; declarative access control on every route; and a uniform response contract. This page explains how to define routes, what the clients receive, how to paginate lists and how the generated OpenAPI documentation is organised.

- [Creating a router with `get_router`](#creating-a-router-with-get_router)
- [Route options](#route-options)
- [The response envelope](#the-response-envelope)
- [Pagination](#pagination)
- [camelCase aliases](#camelcase-aliases)
- [OpenAPI documentation](#openapi-documentation)
- [Authenticated WebSockets](#authenticated-websockets)

## Creating a router with `get_router`

Every router module of an app exposes a module-level variable named `router`, built with `get_router()`. The framework mounts it under `/api`.

```python
# apps/billing/root.py
from pachifast.router_factory import get_router

router = get_router()


@router.get("/ping")
async def ping() -> dict:
    return {"pong": True}
```

`get_router()` derives the router name from the module path: it keeps the last `parts` segments (default `2`) and drops `root`, `main` and `__init__`. The router prefix is `/<name>` and its tags are the name's segments.

| Module | Name | Final path of `/ping` |
| --- | --- | --- |
| `apps/billing/root.py` | `billing` | `/api/billing/ping` |
| `apps/billing/invoices.py` | `billing/invoices` | `/api/billing/invoices/ping` |
| any module, with `name="reports/monthly"` | `reports/monthly` | `/api/reports/monthly/ping` |

The router must be declared in the `router_modules` of an `AppModule` (see [Modules](modules.md)).

### `get_router` parameters

| Parameter | Default | Description |
| --- | --- | --- |
| `name` | derived from the module | Router name, used as prefix and tags. |
| `parts` | `2` | How many trailing module segments form the derived name. |
| `deps` | `[]` | Dependency switches (see below) or ready-made `Depends(...)` objects. |
| `permissions` | `None` | Permission codenames required by **every** route of the router. |
| `require_staff` | `False` | Every route requires a staff user. |
| `require_superuser` | `False` | Every route requires a superuser. |
| `api_route` | `PermissionedRouter` | Router class. `AutoResponseRouter` wraps every declared `response_model` into the router-level `response_model` (default `StandardResponse`) in the OpenAPI schema. |
| `response_model` | `StandardResponse` | Envelope model used by `AutoResponseRouter` only. |
| `**kwargs` | | Passed on to `APIRouter` (for example `tags`, `responses`, `prefix`, which override the defaults). |

By default a router adds the dependency that resolves the current **active** user, so every route needs a valid bearer token (see [Authentication](authentication.md)). The `deps` list changes that:

| `deps` value | Effect |
| --- | --- |
| `"noauth"` | No authentication on the whole router (public routes, or routes that authenticate themselves such as WebSockets). |
| `"nouser"` | Same as `"noauth"` at the moment; use it to signal that no user is needed. |
| a `Depends(...)` object | Added to every route of the router. |

> **Warning**: The settings `global_noauth` and `swagger_noauth` switch authentication off globally or for the Swagger UI. They exist for local development only; never enable them in production. See the [settings reference](../reference/settings.md).

## Route options

`PermissionedRouter` is the router class behind `get_router()`. Its `get`, `post`, `put`, `patch` and `delete` decorators accept these extra keyword arguments, in addition to the usual FastAPI ones (`response_model`, `status_code`, `summary`, ...):

| Option | Type | Description |
| --- | --- | --- |
| `permissions` | `list[str]` | The user must have **all** these permission codenames. |
| `require_staff` | `bool` | The user must be staff. |
| `require_superuser` | `bool` | The user must be a superuser. |
| `skip_auth` | `bool` | Remove the router's authentication for this route, making it public. |
| `skip_envelope` | `bool` | Return the body as is, without the [envelope](#the-response-envelope). |
| `log_as` | `str` | Human-readable description stored in the activity log for this route. |

```python
from pachifast.router_factory import get_router

router = get_router(name="billing/invoices")


@router.get("/public-terms", skip_auth=True)
async def terms() -> dict:
    return {"version": 3}


@router.get("/report", permissions=["billing.report_view"])
async def report() -> dict: ...


@router.delete("/{invoice_id}", require_superuser=True)
async def delete_invoice(invoice_id: int) -> dict: ...


@router.patch(
    "/{invoice_id}",
    log_as="<b>{username}</b> edited invoice #{invoice_id}",
)
async def edit_invoice(invoice_id: int) -> dict: ...
```

Notes on how the options combine:

- Permission options set on the router and on a route **add up**: both must pass. Superusers pass every check. The evaluation order and the error codes are listed in [Authentication](authentication.md#protecting-routes).
- `skip_auth=True` removes only the authentication dependency added by `get_router()`. Permission checks configured on the **router** stay in place, because they need a user themselves. Keep public routes on a router without `permissions`, `require_staff` or `require_superuser`.
- `log_as` is a template interpolated with the path parameters and the `{username}` of the caller. It is used by the activity log (`activity_log_enabled`); see [Operations](operations.md).
- The `skip_envelope` and `log_as` options are stored as `x-skip-envelope` and `x-log-as` in the route's `openapi_extra`, so they survive `include_router`.

## The response envelope

Every successful JSON response is wrapped in the same envelope. The endpoint returns its natural data and the framework wraps it **after** serialization, so handlers do not need to declare a wrapping model.

```python
@router.get("/42")
async def get_invoice() -> dict:
    return {"id": 42, "total": 120.5}
```

```json
{
  "success": true,
  "message": null,
  "data": { "id": 42, "total": 120.5 }
}
```

### The contract

1. Every JSON response with a **2xx** status is `{"success": true, "message": ..., "data": ...}`. `success` is always `true`.
2. Every error is a **non-2xx** response with `{"detail": [...]}` (see [Errors and i18n](errors-and-i18n.md)). The backend never answers 2xx with `success: false`.
3. A JSON response with a non-2xx status that a handler returns directly (for example `JSONResponse(..., status_code=400)`) is **not** wrapped.
4. Non-JSON responses (file downloads, streams, HTML) are never wrapped.

A client can therefore unwrap `data` on every 2xx and treat every non-2xx as an error, with no other checks. See [Frontend integration](frontend-integration.md).

If the setting `expose_response_database` is `true`, the envelope also includes a `database` list naming the databases used by the request. It is meant for debugging.

### Routes without envelope

A route declared with `skip_envelope=True` answers with its raw body. This is for contracts that you do not control, such as the OAuth2 token endpoints, and for empty responses:

```python
from fastapi import Response, status


@router.post("/notify", status_code=status.HTTP_204_NO_CONTENT, skip_envelope=True)
async def notify() -> Response:
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

In the built-in routes, `/auth/token`, `/auth/refresh-token` (unless `wrap_auth_token_response` is on), `/auth/logout` (204) and the events ticket endpoint use `skip_envelope`.

### Turning the envelope off for the whole application

```env
PACHIFAST_ENVELOPE_ENABLED=false
```

(In YAML: `envelope_enabled: false`. The default is `true`.) With the envelope off, **all** routes answer with the plain body and `expose_response_database` has no effect. Errors are unchanged: still a non-2xx status with `{"detail": [...]}`.

> **Warning**: This is a decision for the whole application, not a switch to flip on a running system. The frontend must be configured the same way: it must no longer expect, or unwrap, `{success, message, data}`. To exclude a single route use `skip_envelope=True` instead.

## Pagination

`PageParams` and `paginate()` implement the common list contract: page, page size, ordering, text search and exact filters, all as query parameters.

```python
from typing import Annotated

from fastapi import Query
from pachifast.database import CoreSessionDep
from pachifast.database.pagination import paginate
from pachifast.router_factory import get_router
from pachifast.schemas.responses import PageParams, PaginatedResponse

from .models import Invoice

router = get_router(name="billing/invoices")


class InvoicePageParams(PageParams):
    search_fields = ["number", "customer_name"]  # columns used by ?search=
    customer_id: int | None = Query(None)  # exact filter
    status: str | None = Query(None)  # exact filter


@router.get("", response_model=PaginatedResponse[Invoice])
async def list_invoices(
    params: Annotated[InvoicePageParams, Query()],
    session: CoreSessionDep,
):
    return await paginate(session, Invoice, params)
```

`session` can be any SQLAlchemy `AsyncSession`, not only the core one. For a synchronous `Session` use `paginate_sync` from the same module; it takes the same arguments and returns the same contract.

Counting and `LIMIT`/`OFFSET` are delegated to [fastapi-pagination](https://uriyyo-fastapi-pagination.netlify.app/); the query parameters, the filters, the search, the ordering and the response format described here are pachifast's. `PaginatedResponse` is a fastapi-pagination page, so it can be used with the library's other tools.

### Query parameters

| Parameter | Description |
| --- | --- |
| `page` | Page number, starting at `1`. |
| `pageSize` | Rows per page. `0` or missing means "no limit". |
| `orderBy` | Comma-separated list of fields (only mapped columns; a params class can narrow them with `order_fields = [...]`, as the user list does so that the password hash cannot be used as a sort key); prefix a field with `-` for descending order: `orderBy=-createdAt,number`. Both camelCase and snake_case field names are accepted. |
| `search` | Case-insensitive substring searched in every column listed in `search_fields`. |
| *any other field of your params class* | Exact-match filter on the model column with the same name. |

Rows are paginated only when **both** `page` and `pageSize` are set; otherwise every matching row is returned (`page` and `pageSize` are echoed back as received). Filters are matched by field name, so `customerId=1` and `customer_id=1` are both accepted. Fields of the params class that are not columns of the model are ignored.

### Response

```json
{
  "success": true,
  "message": null,
  "data": {
    "items": [{ "id": 11, "number": "INV-010", "customerId": 1, "status": "paid" }],
    "total": 25,
    "page": 2,
    "pageSize": 10,
    "pages": 3
  }
}
```

`total` is the number of rows matching the filters and search, before pagination. `pages` is `ceil(total / pageSize)`; it is `1` when the list is not paginated and `0` when nothing matches.

An `orderBy` that names a field the model does not have is answered with `422`, code `api.invalid_order_by`, `params: {"field": "..."}` and `loc: ["query", "orderBy"]`.

> **Note**: The message of the `orderBy` error is currently a fixed text without a stable `code`, so it is not translated. Match on `loc`, not on the text.

### `paginate()` arguments

| Argument | Description |
| --- | --- |
| `session` | The async session to query. |
| `model` | The ORM model class to select. |
| `params` | A `PageParams` subclass instance. |
| `extra_filters` | List of callables `(params, filter_kwargs, stmt, model) -> (filter_kwargs, stmt)` to add custom clauses (ranges, joins...). |
| `as_model` | `True` returns the plain list of rows instead of a `PaginatedResponse`. |
| `defer_fields` | Columns not to load (heavy columns). |
| `select_options` | Extra SQLAlchemy loader options, for example `selectinload(...)`. |

For list-valued query filters, `ListQuery` and `_coerce_csv_list` in `pachifast.schemas.responses` let a parameter accept both `?status=1&status=2` and `?status=1,2`.

## camelCase aliases

The JSON API speaks camelCase while Python code stays snake_case. `AliasedBaseModel` (from `pachifast.database`) configures this for the Pydantic request and response schemas. It accepts the field by name or by alias on input, always serializes by alias, and can be built from an ORM row (`from_attributes=True`):

```python
from pachifast.database import AliasedBaseModel


class InvoiceCreate(AliasedBaseModel):
    customer_id: int
    due_date: str


# accepts {"customerId": 1, "dueDate": "..."} and {"customer_id": 1, ...}
# serializes as {"customerId": 1, "dueDate": "..."}
```

`PageParams` is also an `AliasedBaseModel`, which is why filters such as `customer_id` are exposed as `customerId`.

> **Note**: Some built-in schemas are plain Pydantic models and keep snake_case: the OAuth2 token response, the refresh-token request body and the MFA requests and responses (`access_token`, `refresh_token`, `mfa_token`).

## OpenAPI documentation

The application builds its documentation from the mounted routers, split by module kind, so you can read the framework routes and the product routes separately.

| URL | Content |
| --- | --- |
| `/docs` | Swagger UI of the **apps and common** modules (empty until you add one). |
| `/docs/core` | Swagger UI of the built-in **core** routes: auth, admin, health, version. |
| `/api/v1/openapi.json` | Full schema, for code generators. |
| `/api/v1/openapi-apps.json` | Schema behind `/docs`. |
| `/api/v1/openapi-core.json` | Schema behind `/docs/core`. |

What you can customise:

- **Title and version**: the title comes from the `project_name` setting (falling back to `app_name`), the version from `version`. Set them in `.env` or `pachifast.yaml`.
- **Sections**: the tags are the segments of the router name, so `billing/invoices` appears as the tags `billing` and `invoices`. Pass `tags=[...]` to `get_router()` to override.
- **Operations**: use the usual FastAPI arguments (`summary`, `description`, `response_model`, `responses`, `openapi_extra`) on the route decorators.
- **Authorize button**: the OAuth2 password flow points to `/api/auth/token`, so you can log in from the Swagger UI.
- **What is documented**: routes switched off with `disable_routers` or `disable_routes` disappear from the documentation as well (see [Modules](modules.md)).
- **WebSockets**: WebSocket routes appear as paths flagged `x-websocket: true`, because OpenAPI has no native WebSocket support.

The schemas are built once and cached for the life of the process.

## Authenticated WebSockets

A WebSocket route cannot carry the browser's `Authorization` header, so the router must not apply the default HTTP authentication. Declare the router with `deps=["noauth"]` and authenticate inside the handler. This example accepts the access token in the `token` query parameter and requires the `logs.read` permission (superusers pass):

```python
from fastapi import WebSocket, WebSocketException, status

from pachifast.auth.cruds import get_user_by_id, get_user_permissions
from pachifast.auth.security import (
    decode_access_payload,
    ensure_token_not_revoked,
    subject_to_user_id,
)
from pachifast.database import get_core_sessionmaker
from pachifast.router_factory import get_router

router = get_router(name="ws/logs", deps=["noauth"])


@router.websocket("/stream")
async def stream_logs(ws: WebSocket) -> None:
    token = ws.query_params.get("token", "")
    allowed = False
    async with get_core_sessionmaker()() as session:
        try:
            payload = decode_access_payload(token)
            user = await get_user_by_id(session, subject_to_user_id(payload))
            if user is not None and user.is_active:
                ensure_token_not_revoked(payload, user)
                allowed = user.is_superuser or "logs.read" in await get_user_permissions(session, user.id)
        except Exception:
            allowed = False
    if not allowed:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)

    await ws.accept()
    await ws.send_text("connected")
```

A rejected client sees the handshake fail with close code `1008`.

> **Warning**: The URL of a WebSocket ends up in proxy and access logs, so an access token in the query string is exposed there. The one-time ticket flow (`POST /api/core/events/ticket`, then `?ticket=`) is built into the events endpoint only, see [Events](events.md). For your own routes keep access tokens short-lived, or read the `Authorization` header in the handler for clients that can send it (non-browser clients).

> **Note**: `PermissionedRouter.websocket()` also accepts `permissions`, `require_staff` and `require_superuser` and converts them into WebSocket-aware dependencies, and `get_current_active_ws_user` can be used as a dependency of your own WebSocket routes (it reads the token from `?token=` or the `Authorization` header and closes with code `1008` when it is missing or invalid). Before 0.1.7 these dependencies failed with a `TypeError`; on older versions authenticate in the handler as shown above.
