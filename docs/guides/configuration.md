# Configuration

pachifast is configured through typed settings (pydantic-settings) that can come from command-line arguments, environment variables, a `.env` file, an optional YAML file and built-in defaults. This page explains where values come from, which one wins, and the settings you are most likely to change. The exhaustive list is in the [Settings reference](../reference/settings.md).

## Where settings come from

When the settings are built, sources are consulted in this order. The first one that defines a value wins:

| Priority | Source | Example |
| --- | --- | --- |
| 1 (highest) | explicit arguments | `pachifast dev --port 9000` |
| 2 | environment variables | `PACHIFAST_PORT=9000` |
| 3 | the `.env` file | `PACHIFAST_PORT=9000` in `.env` |
| 4 | the YAML file | `port: 9000` in the `server` section of `pachifast.yaml` |
| 5 (lowest) | defaults | `8000` |

Environment variables and `.env` entries carry the `PACHIFAST_` prefix and are case-insensitive: `PACHIFAST_ACCESS_TOKEN_EXPIRE_MINUTES=60` and `pachifast_access_token_expire_minutes=60` are the same. Keys in the YAML file are grouped in sections (see below) and are also case-insensitive.

A sensible split follows from the priority:

- put **secrets and per-environment values** in `.env` or in real environment variables (`secret_key`, database URLs with passwords);
- put **non-secret, versioned project defaults** in `pachifast.yaml` (`server.port`, `app.default_locale`, `routes.disable_core_routers`...);
- let the deployment override anything with environment variables, with no file edits.

> **Note**: the `.env` file is looked up in the current directory each time the settings are built, so `pachifast` commands run with `--base-dir` from another directory still find the project's file. Only a file named exactly `.env` is read; there is no `.env.<name>` lookup at runtime.

### Example `.env`

```env
PACHIFAST_SECRET_KEY=<long-random-string>
PACHIFAST_AUTH_DATABASE_URL=sqlite+aiosqlite:///./demo.db
BILLING_DATABASE_URL=postgresql+asyncpg://<user>:<password>@localhost:5432/billing
```

`pachifast init` creates this file for you (secret key and SQLite URL) and never overwrites values already in it. It also skips values that are already set in `pachifast.yaml`. A generated project ships an `.env.example` with an empty `PACHIFAST_SECRET_KEY` as a reminder; `.env` itself is excluded by the generated `.gitignore`.

## The YAML file

Besides `.env`, a project can keep its non-secret configuration in a YAML file next to `main.py`. The settings are grouped in **sections**. `pachifast new project` generates a file like this (every key is optional):

```yaml
# yaml-language-server: $schema=./pachifast.schema.json
server:
  host: 127.0.0.1
  port: 8000
  # debug: true                  # development only

# app:
#   default_locale: it
#   use_accept_language: true
# routes:
#   disable_core_routers: [permissions]
```

A fuller example:

```yaml
app:
  app_name: My API
  default_locale: it
server:
  host: 0.0.0.0
  port: 8000
  docs_enabled: false
log:
  log_format: json
  log_level: INFO
routes:
  disable_core_routers: [permissions]
cache:
  url: redis://localhost:6379/0     # cache_url
auth:
  access_token_expire_minutes: 30
  registration_enabled: false
mfa:
  enabled: true                     # mfa_enabled
activity_log:
  enabled: true                     # activity_log_enabled
  retention_days: 90
events:
  broadcast_enabled: true           # events_broadcast_enabled
  public_types: [login]
```

### Sections

| Section | Contents |
| --- | --- |
| `app` | name, project identity, language: `app_name`, `project_name`, `description`, `version`, `default_locale`, `use_accept_language`, `api_v1_prefix` |
| `server` | `host`, `port`, `debug`, `docs_enabled`, `envelope_enabled`, `request_id_enabled`, `expose_response_database`, `wrap_auth_token_response` |
| `dev` | development-only switches: `global_noauth`, `swagger_noauth` |
| `log` | `log_format`, `log_level` |
| `routes` | `disable_core_routers`, `disable_core_routes`, `disable_routers`, `disable_routes` |
| `database` | `database_url`, `dbdebug`, `db_pool_*`, `table_prefix`, `auto_create_auth_schema` |
| `cache` | `url`, `prefix`, `max_entries` |
| `auth` | `auth_database_url`, `secret_key`, `algorithm`, token lifetimes and rotation, `registration_enabled`, password and login limits, `auth_rate_*` |
| `mfa` | `enabled`, `encryption_key`, `max_attempts`, `otp_expire_minutes`, `send_cooldown_seconds`, `totp_issuer` |
| `tables` | names of the tables of the core (`user_table`, `group_table`, `mfa_config_table`...) |
| `activity_log` | `enabled`, `exclude_paths`, `retention_days`, `table` |
| `events` | `broadcast_enabled`, `allow_query_token`, `public_types`, `ticket_store` |

The rule is simple: a key is the name of the setting, and in the `cache`, `mfa`, `activity_log` and `events` sections the section name replaces the prefix of the setting: `cache.url` is `cache_url` (`PACHIFAST_CACHE_URL`), `mfa.enabled` is `mfa_enabled`, `activity_log.table` is `activity_log_table`, `events.broadcast_enabled` is `events_broadcast_enabled`. The [Settings reference](../reference/settings.md) shows the location of every setting next to its environment variable. The tables below list settings by their field name (the name of the environment variable without the `PACHIFAST_` prefix).

Mistakes are caught early: a section that is not a mapping, an unknown key inside a section (the message suggests the closest valid key), or the same setting given twice, stop the startup with an error that names the file. Keys at the top level that are not sections are left alone, so a project or an app can keep its own settings there.

> The flat format of 0.15 (`port: 9000` at the top level of the file) is no longer accepted: the startup stops with a message that lists the `section.key` to use instead. The conversion table is in [Upgrading](upgrading.md#upgrade-notes).

### How the file is found

At startup pachifast looks in the project directory (the current directory) and loads the **first existing** file of `pachifast.yaml`, `pachifast.yml`. Other names (`config.yaml`, `<directory-name>.yaml`) are not read, so a file of another tool in the same folder is never taken for settings.

To use a specific file, set `PACHIFAST_CONFIG_FILE` (a leading `~` is expanded).

| Situation | Result |
| --- | --- |
| no file found in the lookup list | startup continues without a YAML file |
| `PACHIFAST_CONFIG_FILE` points to a missing file | error (`FileNotFoundError`) |
| file exists but is not valid YAML, or its top level is not a mapping | error that names the file |
| empty file | treated as no configuration |

Top-level keys that are not sections are allowed in the file: apps can read their own settings from it (see below).

## Per-app settings

A module can define its own settings class and register it with `settings_modules` (see [Modules](modules.md#settings-modules)). Derive it from `PachifastSettingsBase`, which gives it the same sources and priority as the core settings (arguments, environment, `.env`, YAML):

```python
# apps/billing/settings.py
from pydantic import Field

from pachifast.config import PachifastSettingsBase


class Settings(PachifastSettingsBase):
    _env_prefix = "BILLING_"  # BILLING_CURRENCY, BILLING_RETRIES, ...

    currency: str = "EUR"
    retries: int = 3
    database_url: str = Field(default="", validation_alias="BILLING_DATABASE_URL")


settings = Settings()


def get_database_url() -> str:
    return settings.database_url
```

There are two ways to name the environment variable of a field:

| Technique | Variable read | Use it when |
| --- | --- | --- |
| `_env_prefix = "BILLING_"` on the class | `BILLING_<FIELD>` | every field of the app shares a prefix |
| `validation_alias="BILLING_DATABASE_URL"` on the field | exactly the alias | you want a specific name (the scaffolding generates this form) |

Without `_env_prefix` the inherited prefix `PACHIFAST_` applies: a field `retries` is read from `PACHIFAST_RETRIES`. A field with a `validation_alias` ignores the prefix. Class attribute `_env_prefix` replaces the prefix; you do not need to write `model_config` for it.

The same names work in `pachifast.yaml` (lower-case): `billing_database_url: sqlite:///billing.db` for the alias above, `currency: USD` for the prefixed field (top-level keys, outside the core sections). A real environment variable still beats the file.

Once registered through `settings_modules`, the values are copied onto the global settings object, so they are reachable from the code as `get_settings().currency`. See [Modules](modules.md#settings-modules) for the exact rules.

> **Note**: for per-app classes the `.env` file is filtered by prefix: entries that do not start with the class's prefix (such as another app's variables) are ignored. Fields with an explicit `validation_alias` are read under that exact name, whatever the prefix.

## Secret key

`SECRET_KEY` signs every JWT. It is the only setting without a usable default:

- there is **no default**; the application refuses to start (a validation error at settings creation) if it is empty;
- it must be at least **32 characters**; a shorter value is rejected at startup;
- set it with `PACHIFAST_SECRET_KEY` in the environment or in `.env`, never in `pachifast.yaml` (`auth.secret_key`) or in source code.

Generate one:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
# or
openssl rand -hex 32
```

Changing the key invalidates every token already issued, so rotate it deliberately. See [Authentication](authentication.md).

## Most important settings

Defaults below are read from `src/pachifast/config/settings.py`. In the environment, prefix each name with `PACHIFAST_`.

### Server

| Setting | Default | Description |
| --- | --- | --- |
| `host` | `127.0.0.1` | bind address for `pachifast dev` and `pachifast serve` |
| `port` | `8000` | bind port (1-65535) |
| `app_name` | `PachiFast` | application title shown in the OpenAPI documentation |
| `project_name` | empty | takes precedence over `app_name` for the title; also reported by `/api/health/` |
| `version` | empty | application version reported by the health endpoint; the OpenAPI version falls back to `0.1.0` |
| `description` | empty | description reported by the health endpoint |

### Security and tokens

| Setting | Default | Description |
| --- | --- | --- |
| `secret_key` | none, required | JWT signing key, at least 32 characters |
| `algorithm` | `HS256` | JWT signing algorithm |
| `access_token_expire_minutes` | `30` | access token lifetime |
| `refresh_token_expire_days` | `7` | refresh token lifetime |
| `registration_enabled` | `false` | allow `POST /auth/register` (anyone can create an account); closed by default, so it answers `403 auth.registration_disabled` |
| `refresh_token_rotation` | `true` | every refresh issues a new single-use refresh token; a reused one revokes the login |
| `refresh_reuse_grace_seconds` | `10` | seconds after a rotation in which a second use of the spent token is refused without revoking the login (two tabs refreshing together); `0` revokes at once |
| `password_min_length` | `12` | minimum password length |
| `login_max_attempts` | `5` | failed logins before an account is locked out |
| `login_lockout_minutes` | `15` | lockout duration |
| `global_noauth` | `false` | disables authentication on every router: development only |
| `docs_enabled` | `true` | serve the Swagger UI (`/docs`, `/docs/core`) and the OpenAPI schemas (`/api/v1/openapi*.json`); `false` removes all of them (plain `404`): the right choice for a public API, whose full route list (admin included) is otherwise readable by anyone |
| `auth_rate_limit` | `0` | requests per client address and window on `POST /auth/token`, `POST /auth/register` and `POST /mfa/verify`; above it `429 auth.rate_limited` with `Retry-After`. `0` is no limit. Run behind a proxy that forwards the client address (`pachifast serve` honours the proxy headers) before turning it on, otherwise everyone shares one budget; with several workers set `cache_url` so they share the count |
| `auth_rate_window_seconds` | `60` | length of the window of `auth_rate_limit` |
| `mfa_send_cooldown_seconds` | `30` | seconds a user waits between two codes sent by `/mfa/email/setup` and `/mfa/sms/setup`; `0` is no wait |
| `swagger_noauth` | `false` | lets the Swagger UI act as the first superuser without a token, for requests that come from the loopback with the docs page as referer: development only, never behind a reverse proxy |

### Databases

| Setting | Default | Description |
| --- | --- | --- |
| `auth_database_url` | empty | SQLAlchemy URL (async driver) of the authentication database, for example `postgresql+asyncpg://...` or `sqlite+aiosqlite:///./demo.db` |
| `database_url` | empty | declared for projects that want a main application database URL; the framework itself uses only `auth_database_url` |
| `auto_create_auth_schema` | `false` | create the auth tables at startup instead of using migrations |
| `dbdebug` | `false` | log every SQL statement |
| `table_prefix` | `pachifast_` | prefix of the framework's table names (see below) |

### Responses and routing

| Setting | Default | Description |
| --- | --- | --- |
| `envelope_enabled` | `true` | wrap 2xx JSON responses in `{success, message, data}`; `false` returns plain bodies for every route |
| `expose_response_database` | `false` | add a `database` field to the envelope |
| `wrap_auth_token_response` | `false` | wrap the token endpoints in the envelope too |
| `disable_routers` | `[]` | routers of any module to leave unmounted |
| `disable_routes` | `[]` | single routes of any module to leave unmounted |
| `disable_core_routers` | `[]` | same, for built-in routers only |
| `disable_core_routes` | `[]` | same, for built-in routes only |

The four `disable_*` settings are explained in [Modules](modules.md#disabling-routers-and-routes). Envelope behaviour is covered in [Routing and responses](routing-and-responses.md).

### Optional features

| Setting | Default | Description |
| --- | --- | --- |
| `activity_log_enabled` | `false` | audit log of requests (mounts the audit router) |
| `activity_log_exclude_paths` | `refresh-token` | paths left out of the audit log |
| `events_broadcast_enabled` | `false` | WebSocket events endpoint |
| `events_allow_query_token` | `false` | accept the access token in `/ws?token=` (it ends up in access logs); the default requires one-time tickets |
| `events_ticket_store` | `database` | where WebSocket tickets live: `database` or `cache` |
| `events_public_types` | `[]` | event types sent to every connected client |
| `MFA_ENABLED` | `false` | TOTP multi-factor authentication |
| `MFA_TOTP_ISSUER` | empty | issuer name shown in authenticator apps |
| `MFA_OTP_EXPIRE_MINUTES` | `10` | validity of one-time codes |
| `MFA_MAX_ATTEMPTS` | `5` | wrong codes allowed per challenge |

See [Events](events.md) and [Authentication](authentication.md).

### Cache

| Setting | Default | Description |
| --- | --- | --- |
| `cache_url` | empty | empty means in-process memory; `redis://`, `rediss://` or `unix://` selects Redis (needs `pachifast[redis]`); anything else is rejected at startup |
| `cache_prefix` | `pachifast:` | key prefix, to separate several apps on one Redis |
| `cache_max_entries` | `10000` | capacity of the in-memory cache |

See [Cache](cache.md).

### Language

| Setting | Default | Description |
| --- | --- | --- |
| `default_locale` | `en` | language of the API error messages; a language tag such as `en`, `it`, `pt-BR` |
| `use_accept_language` | `false` | choose the language per request from the `Accept-Language` header |

`default_locale` must be a well-formed language tag, otherwise startup fails with a clear message. A tag with no message catalog falls back to English. See [Errors and i18n](errors-and-i18n.md).

## Debug mode and log level

| Setting | Default | Description |
| --- | --- | --- |
| `debug` | `false` | with `true`, an unhandled error shows the traceback page, with server paths, to the client |
| `log_level` | empty | one of `CRITICAL`, `ERROR`, `WARNING`, `INFO`, `DEBUG`; case-insensitive |

An empty `log_level` means `DEBUG` when `debug` is on and `INFO` otherwise. An explicit `log_level` always wins, so `debug: true` with `log_level: WARNING` shows tracebacks but logs warnings only. Any other value is rejected at startup.

> **Warning**: never enable `debug` in production. An unhandled error then exposes the traceback and the server's file paths to whoever triggered it. With `debug` off, a 500 returns a generic message and the details go to the server log only.

For more operational advice (log format, health checks) see [Operations](operations.md).

## Table names

The framework's tables share a prefix, `table_prefix` (default `pachifast_`), so they do not collide with your own tables. The base names are `user`, `user_settings`, `permission`, `group`, `group_permission`, `user_group`, `user_permission`, `activity_log`, `mfa_config` and `mfa_pending`.

The effective name of a table is its override when set, otherwise `table_prefix` plus the base name:

| Setting | Empty value resolves to |
| --- | --- |
| `table_prefix: app_` | `app_user`, `app_group`, ... |
| `user_table: accounts` | `accounts` (other tables keep the prefix) |

The override settings are `user_table`, `user_settings_table`, `permission_table`, `group_table`, `group_permission_table`, `user_group_table`, `user_permission_table`, `activity_log_table`, `mfa_config_table` and `mfa_pending_table`.

> **Warning**: table names are baked into migrations. Decide on the prefix before generating the first migration; changing it later means renaming tables yourself. See [Database and migrations](database-and-migrations.md).

## JSON Schema for editors

pachifast can generate a JSON Schema of `pachifast.yaml` from the settings model. With a YAML language server (VS Code with the Red Hat YAML extension, JetBrains IDEs, Neovim with `yaml-language-server`) you get completion, validation and hover help for every key.

```bash
pachifast config schema -o pachifast.schema.json     # without -o, prints to the terminal
```

Link it with the first line of the YAML file:

```yaml
# yaml-language-server: $schema=./pachifast.schema.json
```

`pachifast new project` already writes both the schema file and that line. In an existing project, generate the file once and regenerate it after upgrading pachifast to pick up new settings. A copy of the schema as shipped with this repository is committed at [`docs/reference/pachifast.schema.json`](../reference/pachifast.schema.json).

Properties of the schema:

- one object per section (`additionalProperties: false`: a misspelled key is flagged), keys in lower case, matching how the file is read (case-insensitive);
- top-level keys that are not sections are accepted, because projects and apps add their own;
- secrets (`secret_key`, database URLs) appear in the schema so editors know them, but they belong in `.env`, not in the file.

## Using settings in code

Import `get_settings` for the process-wide settings object, or the `SettingsDep` dependency in a route:

```python
from pachifast.config import get_settings
from pachifast.dependencies import SettingsDep

from pachifast.router_factory import get_router

router = get_router()


@router.get("/info")
async def info(settings: SettingsDep):
    return {"locale": settings.default_locale}
```

`get_settings()` is cached for the lifetime of the process. Projects that need a different settings class altogether can register a factory with `pachifast.config.configure_settings(factory)` before creating the app.

## Related pages

- [Settings reference](../reference/settings.md): every setting with type and default
- [Modules](modules.md): `settings_modules` and disabling routers
- [Deployment](deployment.md): configuring a container through the environment
