# Modules

A pachifast application is a list of **modules**. Each module is a small declarative object (`AppModule`) that says which routers it contributes, which settings it needs, which databases it migrates, and what to run at startup and shutdown. `create_app` reads the list and builds the FastAPI application from it. This page explains how to declare and compose modules, how to switch parts of the API off, and where to find things in the codebase.

## The module registry

Every project has a `project_modules.py` with a `MODULES` tuple, and `main.py` passes it to `create_app`:

```python
# project_modules.py
from pachifast.presets import core_module

from apps.hello.module import module as hello_module
from apps.billing.module import module as billing_module

MODULES = (
    core_module,
    hello_module,
    billing_module,
)
```

```python
# main.py
from pachifast import create_app

from project_modules import MODULES

app = create_app(MODULES, cors_origins=["http://localhost"])
```

Nothing is discovered implicitly: a module that is not in `MODULES` is not mounted, its settings are not loaded and its migrations do not exist. `pachifast new app` and `pachifast new common` add the new module to this file for you.

`core_module` (from `pachifast.presets`) is the built-in module. It provides authentication, user and permission administration, an informational `infos` router, health and version, and declares the `auth` migration target. Keep it first in the tuple.

Order matters in two places: startup hooks, lifespan hooks and catalog layers run in registration order, and shutdown hooks run in reverse order.

## Declaring a module

`AppModule` is a frozen dataclass. Only `name` is required.

```python
from pachifast import AppModule, MigrationTarget

module = AppModule(
    name="billing",
    kind="app",
    router_modules=("apps.billing.root",),
    settings_modules=("apps.billing.settings",),
    locale_package="apps.billing.locale",
    migration_targets=(
        MigrationTarget(
            name="billing",
            database_url_resolver="apps.billing.settings:get_database_url",
            model_modules=("apps.billing.models",),
        ),
    ),
)
```

| Field | Type | Purpose |
| --- | --- | --- |
| `name` | `str` | unique label of the module, also stored on its routers |
| `kind` | `"core"`, `"app"`, `"common"` or `None` | logical kind (see below); inferred when omitted |
| `router_modules` | `tuple[str, ...]` | dotted paths of modules that define a `router` |
| `scan_dirs` | `tuple[str, ...]` | folders to scan for routers, as an alternative to `router_modules` |
| `settings_modules` | `tuple[str, ...]` | dotted paths of modules whose settings are merged into the global settings |
| `migration_targets` | `tuple[MigrationTarget, ...]` | databases this module owns, one Alembic target each |
| `startup_hooks` | `tuple` | callables (or `"module:function"` strings) run before the app serves requests |
| `shutdown_hooks` | `tuple` | callables run when the app stops |
| `lifespan_hooks` | `tuple` | callables returning a context manager held open for the app's lifetime |
| `locale_package` | `str` or `None` | package with the module's message catalogs |

### Module kinds

`kind` classifies the module and decides where its routes appear in the documentation and which `disable_*` settings apply:

| Kind | Meant for | Documentation | `disable_core_*` applies |
| --- | --- | --- | --- |
| `core` | the built-in framework module | `/docs/core` | yes |
| `app` | an application feature, usually owning its data | `/docs` | no |
| `common` | code shared by several apps (helpers, shared endpoints) | `/docs` | no |

If you leave `kind` as `None`, pachifast infers it: the name `core` gives `core`; a name starting with `common.` gives `common`; otherwise, if any router or settings module path starts with `pachifast.` the kind is `core`, and if it starts with `common.` it is `common`; anything else is `app`. Being explicit is clearer, and the scaffolding always writes `kind`.

The generated layout follows the kinds: `apps/<name>/` for `app` modules (`pachifast new app`) and `common/<name>/` for `common` modules (`pachifast new common`, named `common.<name>`).

### Routers: `router_modules` and `scan_dirs`

`router_modules` is the explicit, recommended way. Each entry is the dotted path of a Python module that exposes a module-level `router`, usually created with [`get_router`](routing-and-responses.md):

```python
# apps/billing/root.py
from pachifast.router_factory import get_router

router = get_router()  # mounted at /api/billing


@router.get("")
async def list_invoices():
    return []
```

`get_router` derives the URL prefix from the module path (the last two parts, ignoring `root`, `main` and `__init__`), so `apps/billing/root.py` mounts at `/api/billing`. A module can list several router modules. Set `disable_router = True` at module level in a router module to skip it without touching the registry.

`scan_dirs` is the alternative: give folder names relative to the project root and pachifast imports every `*.py` file in them that contains `router = get_router` (or imports a `router`):

```python
module = AppModule(
    name="reports",
    kind="app",
    scan_dirs=("apps/reports",),
)
```

Use `router_modules` when you want a precise, reviewable list; use `scan_dirs` when a folder keeps growing with new routers. A missing folder is logged as a warning and skipped. A router module that fails to import is logged with its traceback and skipped, so check the startup log if a router seems to be missing. Both options can be combined in one module.

All routers are mounted under `/api` (a router can set `pachifast_mount_prefix` to mount somewhere else, for example `""` for OAuth2 callbacks).

### Settings modules

Each entry of `settings_modules` is imported when the global settings are built, and what it exposes is merged into the global settings object. In order of precedence for each imported module:

1. if it defines `register_settings(settings)`, that function is called and nothing else is merged; use it for full control;
2. otherwise every module-level name that is `settings` or ends in `_settings`, and that is an object with attributes, has its public attributes copied onto the global settings;
3. every `UPPERCASE` module-level name is copied too, lower-cased (`BILLING_RETRIES = 3` becomes `settings.billing_retries`).

With the usual per-app class (see [Configuration](configuration.md#per-app-settings)):

```python
from pachifast.config import get_settings

get_settings().billing_database_url
```

Values merged this way come from the app's own settings class, so they honour `.env`, environment and YAML like any other setting. A module with no settings simply omits `settings_modules`.

### Migration targets

A `MigrationTarget` describes one database that Alembic should manage:

| Field | Purpose |
| --- | --- |
| `name` | unique target name, used by `--target-name` in the CLI; duplicates raise an error |
| `model_modules` | dotted paths of the modules that define the ORM models |
| `database_url_field` | name of a global setting that holds the database URL (the core uses `AUTH_DATABASE_URL`) |
| `database_url_resolver` | `"module:function"` returning the URL; use it when the URL lives in the app's settings class |
| `version_table` | Alembic version table; default `<table_prefix>alembic_version_<name>` |

Exactly one of `database_url_field` and `database_url_resolver` is required; a target with neither is rejected. `pachifast new app <name> --with-db` generates a settings class, a `models.py` and a target wired like the example above. Workflow and commands are in [Database and migrations](database-and-migrations.md).

### Startup, shutdown and lifespan hooks

Hooks let a module open and close resources without touching the application factory:

```python
from fastapi import FastAPI

from pachifast import AppModule


async def connect(app: FastAPI) -> None:
    app.state.client = await make_client()


async def disconnect(app: FastAPI) -> None:
    await app.state.client.aclose()


module = AppModule(
    name="integration",
    kind="app",
    startup_hooks=(connect,),
    shutdown_hooks=(disconnect,),
)
```

| Hook | Called | Order |
| --- | --- | --- |
| `startup_hooks` | once at startup | registration order |
| `lifespan_hooks` | entered after the startup hooks, exited at shutdown | registration order, exited in reverse |
| `shutdown_hooks` | once at shutdown | reverse registration order |

Rules that apply to all hook kinds:

- a hook may be a callable or a `"package.module:function"` string, which is imported lazily (a string without `:` raises `ValueError`);
- it may be `async` or a plain function;
- it receives the `FastAPI` app when its first parameter is positional, and no arguments when it takes none.

A lifespan hook returns an async or sync context manager and is the right tool when a resource must stay open for the whole life of the app and be cleaned up on exit:

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pachifast import AppModule


@asynccontextmanager
async def pool(app: FastAPI):
    app.state.pool = await create_pool()
    try:
        yield
    finally:
        await app.state.pool.close()


module = AppModule(name="pooled", kind="app", lifespan_hooks=(pool,))
```

The `udp_registry` (`pachifast.udp`) is a ready-made example of lifecycle-bound resources: start a listener in a startup hook and stop it in a shutdown hook, or inside a lifespan hook.

### Message catalogs: `locale_package`

`locale_package` names a package of per-language files (`en.py`, `it.py`, ...), each defining `MESSAGES`, a dict of stable dotted codes to message templates. The scaffolding creates `locale/en.py` for each module and sets `locale_package="apps.<name>.locale"`. Catalog layers are merged in this order, a later layer redefining the codes of the earlier ones: the core, each module's `locale_package` in registration order, then the project's own `locale/` directory. See [Errors and i18n](errors-and-i18n.md).

## Disabling routers and routes

Routers and single routes can be switched off from configuration, with no code change. This doubles as an emergency switch: set the variable and restart, or redeploy the image with one more variable.

```env
# any module: core, apps and common
PACHIFAST_DISABLE_ROUTERS=billing,admin
PACHIFAST_DISABLE_ROUTES=POST /billing/pay,/user/settings

# built-in (core) routers only
PACHIFAST_DISABLE_CORE_ROUTERS=permissions
PACHIFAST_DISABLE_CORE_ROUTES=/user/settings
```

In `pachifast.yaml` the keys are the same names in lower case, and values can be real lists:

```yaml
disable_routers: [billing, admin]
disable_core_routes: [/user/settings]
```

| Setting | Applies to | Matches |
| --- | --- | --- |
| `disable_routers` | every module (core, apps, common) | router keys |
| `disable_routes` | every module | route entries |
| `disable_core_routers` | `core` module only | router keys |
| `disable_core_routes` | `core` module only | route entries |

For a core router both pairs apply (their union). For `app` and `common` routers only `disable_routers` and `disable_routes` apply. Each router or route switched off is logged at *warning* level at startup.

### Router keys (`disable_routers`, `disable_core_routers`)

Every router has a dotted key derived from the module that declares it, ignoring the segments `root`, `main` and `__init__`. Built-in routers are relative to the package; project routers keep their full path:

| Declaring module | Key |
| --- | --- |
| `pachifast.auth.root` | `auth` |
| `pachifast.auth.user` | `auth.user` |
| `pachifast.infos.root` | `infos` |
| `pachifast.admin.user` | `admin.user` |
| `pachifast.admin.permissions` | `admin.permissions` |
| `pachifast.health.health` | `health` |
| `pachifast.version.root` | `version` |
| `apps.billing.root` | `apps.billing` |
| `common.tools.invoices` | `common.tools.invoices` |

A name disables a router when it equals the key or a run of whole dot-separated segments of it (matching ignores case; `/` is accepted as a separator):

| Name | Switches off |
| --- | --- |
| `admin.permissions` | `admin.permissions` only |
| `admin` | `admin.user` and `admin.permissions` |
| `permissions` | `admin.permissions` |
| `billing` | `apps.billing` and `apps.billing.invoices` |
| `user` | both `auth.user` and `admin.user` |

> **Warning**: a short name can match more than you expect (`user` hits two routers). Use the full key when you need precision. Also note that `auth` switches off the login endpoint (`/api/auth/token`) along with everything else in that router.

### Route entries (`disable_routes`, `disable_core_routes`)

Each entry is `[METHOD ]/path`, with the path relative to `/api`, for example `/billing/pay`. An entry matches a route when its path equals the route's path or one of its trailing sub-paths, and it also covers everything below. `*` is a wildcard. Without a method the entry applies to every method.

| Entry | Switches off |
| --- | --- |
| `POST /billing/pay` | only the POST route at `/api/billing/pay` |
| `/user/settings` | `/auth/user/settings` and everything under it, such as `/auth/user/settings/{category}/{key}` |
| `PUT /auth/user/settings/{category}/{key}` | one specific route |
| `/user` | the routes of both `auth/user` and `admin/user` |

Method-specific entries never apply to WebSocket routes. Disabled routes are not mounted and disappear from `/docs` and `/docs/core`, and requests to them get a 404. Routes that stay in a router keep all their access controls.

Optional routers such as the audit log and the events endpoint have their own switches (`activity_log_enabled`, `events_broadcast_enabled`) rather than being listed in the module registry; see [Configuration](configuration.md).

## Public API

Import from these places; everything else is implementation detail that may change.

| Import | Provides |
| --- | --- |
| `from pachifast import create_app, AppModule, MigrationTarget, ModuleKind` | application factory and the module contract |
| `from pachifast.presets import core_module` | the built-in module for `project_modules.py` |
| `from pachifast.router_factory import get_router` | create a router with automatic prefix, tags and authentication |
| `from pachifast.dependencies import ...` | `ActiveUserDep`, `CurrentUserDep`, `ActiveWsUserDep`, `CurrentWsUserDep`, `TokenDep`, `CoreSessionDep`, `SettingsDep` and the matching `get_*` functions |
| `from pachifast.config import ...` | `PachifastSettings`, `PachifastSettingsBase`, `ServerSettings`, `SettingsDep`, `get_settings`, `configure_settings`, `default_env_file` |
| `from pachifast.database import ...` | `Base`, `AliasedBaseModel`, session helpers (`SessionDep`, `CoreSessionDep`, `get_engine_from_url`, `session_dependency_from_url`), `new_uuid`, `utcnow`, `UtcDateTime`, `JsonType` |
| `from pachifast.i18n import ...` | `api_error`, `t`, `translate`, `current_locale`, `configure_catalogs`, `supported_locales` |
| `from pachifast.events import broadcast_event` | publish a WebSocket event |
| `from pachifast.cache import get_cache` | in-memory or Redis cache |
| `from pachifast.udp import udp_registry, UdpListenerConfig` | UDP listeners |
| `from pachifast.mfa import MfaModule` | optional module with the multi-factor authentication routes |

The guides cover each of these in context: [Routing and responses](routing-and-responses.md), [Authentication](authentication.md), [Errors and i18n](errors-and-i18n.md), [Events](events.md) and [Cache](cache.md).

## Finding your way in the codebase

The source lives under `src/pachifast/`. If you need to extend or debug the framework, start here:

| Area | Package or file | What it does |
| --- | --- | --- |
| Bootstrap | `app_factory.py` | `create_app`: settings, logging, hooks, router mounting, OpenAPI |
| Module contract | `modules.py` | `AppModule`, `MigrationTarget`, hook runners, target resolution |
| Built-in module | `module.py`, `presets.py` | `core_module` and its router list |
| Configuration | `config/` | settings classes, `.env` and YAML sources, JSON Schema |
| Database | `database/` | engines, sessions, base models, pagination, model discovery |
| Auth | `auth/`, `admin/` | users, groups, permissions, JWT, password hashing, admin routes |
| Routing | `routing/`, `router_factory.py` | `get_router`, permissioned and envelope routers, router discovery, `core_filters.py` (disable switches) |
| OpenAPI | `openapi/` | the split `/docs` and `/docs/core` schemas |
| Errors and i18n | `errors.py`, `i18n/` | error handlers, stable codes, catalogs |
| Migrations | `migrations/` | Alembic manager used by the CLI, runtime shared by generated `env.py` files |
| CLI | `cli/` | the `typer` application, one file per command group |
| Scaffolding | `scaffold/` | project, app and pipeline generators and templates |
| Optional features | `audit/`, `events/`, `mfa/`, `udp/`, `cache/` | activity log, WebSocket events, TOTP, UDP listeners, cache backends |
| Small services | `health/`, `version/`, `infos/`, `logging/`, `utils/` | `/api/health/`, `/api/version/`, `/api/infos/`, log formatter, helpers |

A reading order that works well: `__init__.py`, `app_factory.py`, `modules.py`, `module.py`, then `routing/`, `database/engine.py`, `auth/` and `migrations/manager.py`. To add something, the usual entry points are:

| You want to... | Touch |
| --- | --- |
| change bootstrap behaviour | `app_factory.py`, `modules.py` |
| add a CLI command | `cli/` (and `scaffold/` for generators) |
| add an always-on built-in router | `module.py` |
| add a migration target or database behaviour | `modules.py`, `migrations/`, `database/` |
| change authentication or permissions | `auth/`, `admin/` |
| change routing or documentation | `routing/`, `openapi/` |

See [Contributing](../contributing.md) for the development workflow.

## Related pages

- [Configuration](configuration.md): settings sources and per-app settings
- [Routing and responses](routing-and-responses.md): writing routers
- [Database and migrations](database-and-migrations.md): migration targets in practice
- [Getting started](../getting-started.md): the generated project layout
