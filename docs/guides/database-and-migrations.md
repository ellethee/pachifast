# Database and Migrations

The core is asynchronous (SQLAlchemy 2.0 ORM on async drivers); your own code can use synchronous sessions too (see [Synchronous sessions](#synchronous-sessions)). A project usually talks to two kinds of databases: the **auth database**, which holds users, permissions and the other core tables, and any number of **project databases** that belong to your own modules. Schema changes are managed with Alembic through the `pachifast migrations` commands, one independent history per **migration target**.

## Databases and drivers

| Setting | Environment variable | Purpose |
| --- | --- | --- |
| `AUTH_DATABASE_URL` | `PACHIFAST_AUTH_DATABASE_URL` | The auth database (core tables). Required by the core. |
| `DATABASE_URL` | `PACHIFAST_DATABASE_URL` | Optional main database of your application. |
| Your own fields | for example `BILLING_DATABASE_URL` | One URL per module database, declared in the module's settings. |

URLs use SQLAlchemy syntax with an async driver:

| Database | Example URL | Driver |
| --- | --- | --- |
| SQLite | `sqlite+aiosqlite:///./demo.db` | `aiosqlite` (installed by default) |
| PostgreSQL | `postgresql+asyncpg://user:password@host:5432/dbname` | `asyncpg` (extra `pachifast[postgres]`) |

**Supported databases: SQLite and PostgreSQL.** They are the ones the test suite runs on. MySQL, MariaDB and other databases (SQL Server, for example) are neither supported nor tested by the core. They may come later as a separate package, or you can wire a driver in yourself: the core only needs an async SQLAlchemy URL, and `pachifast.database.sync.SYNC_DRIVERS` maps an async driver to its synchronous one for the migrations.

Engine details:

- Engines and session makers are created lazily and cached per URL.
- PostgreSQL uses a pool of 5 connections (10 overflow, `pool_pre_ping`, recycle after 30 minutes).
- SQLite files use no pool (`NullPool`); `:memory:` uses a single shared connection.
- `dbdebug=true` makes SQLAlchemy echo every SQL statement (see [Operations](operations.md#logging)).

> **Note**: put passwords in `.env` or in the environment, not in the YAML file or in version control. See [Configuration](configuration.md).

## Sessions in endpoints

pachifast provides ready-made dependencies for the auth database:

```python
from sqlalchemy import text

from pachifast.dependencies import CoreSessionDep


@router.get("/ping-db")
async def ping_db(session: CoreSessionDep):
    await session.execute(text("SELECT 1"))
    return {"ok": True}
```

| Name | Module | Description |
| --- | --- | --- |
| `CoreSessionDep` | `pachifast.dependencies`, `pachifast.database` | An `AsyncSession` bound to the auth database. Returns HTTP `503` if `AUTH_DATABASE_URL` is not configured. |
| `CurrentUserDep`, `ActiveUserDep`, `SettingsDep` | `pachifast.dependencies` | Current user, active user and settings. See [Authentication](authentication.md). |

For a project database, build a dependency from its URL:

```python
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from pachifast.database.engine import session_dependency_from_url

from .settings import settings

BillingSession = Annotated[AsyncSession, session_dependency_from_url(settings.billing_database_url)]
```

`session_dependency_from_url(url, echo=False, connect_args=None)` returns a FastAPI dependency; the database label is recorded on the request, so responses can report which database an endpoint used when `expose_response_database` is enabled. Outside of requests, `get_sessionmaker_from_url(url)` and `get_core_sessionmaker()` give you a session maker.

Other helpers in `pachifast.database`: `Base` (the declarative base of every model, see below), `AliasedBaseModel` (Pydantic base with camelCase aliases), `paginate` and `paginate_sync` for paginated queries, and ordering helpers.

## Models

Models are plain SQLAlchemy 2.0 ORM classes on the `pachifast.database.Base` declarative base. They are also keyword-only dataclasses, so Python defaults apply as soon as the instance is created:

```python
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from pachifast.database import Base


class Invoice(Base):
    __tablename__ = "billing_invoice"

    id: Mapped[int | None] = mapped_column(primary_key=True, default=None, nullable=False)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    paid: Mapped[bool] = mapped_column(default=False)


Invoice(number="INV-1")  # paid is already False, id is None until the flush
```

- Declare auto-incremented integer keys exactly like `id` above (`default=None` and `nullable=False`); SQLAlchemy needs the explicit `nullable=False` to insert several rows at once.
- All the models share `Base.metadata`, which the migrations compare with the database.
- A model is **not** an API schema. Return a Pydantic schema built from the row, for example `InvoiceRead.model_validate(row)`; `AliasedBaseModel` is configured with `from_attributes=True` for this (see [Routing and responses](routing-and-responses.md#camelcase-aliases)).
- `__auto_create__ = True` on a model class includes its table in the automatic creation done by `scan_models()`.

## Synchronous sessions

The core uses async sessions, but application code can use plain `Session` objects. `get_sync_sessionmaker_from_url(url)`, `get_sync_engine_from_url(url)` and `sync_session_dependency_from_url(url)` take the same URL as the async helpers and swap the async driver for its synchronous one (`sqlite+aiosqlite` to `sqlite`, `postgresql+asyncpg` to `postgresql+psycopg`). PostgreSQL needs the `pachifast[postgres-sync]` extra.

```python
from pachifast.database import sync_session_dependency_from_url
from sqlalchemy.orm import Session


@router.get("/count")
def count(session: Session = sync_session_dependency_from_url(settings.billing_database_url)): ...
```

For lists use `paginate_sync(session, Model, params)`; it has the same arguments and result as `paginate`.

## The core tables

The core module owns the following tables, all created in the auth database. Names are `table_prefix` (default `pachifast_`) plus a base name.

| Base name | Table (default) | Content |
| --- | --- | --- |
| `user` | `pachifast_user` | Users. |
| `user_settings` | `pachifast_user_settings` | Per-user settings. |
| `permission` | `pachifast_permission` | Permissions. |
| `group` | `pachifast_group` | Groups. |
| `group_permission` | `pachifast_group_permission` | Group to permission links. |
| `user_group` | `pachifast_user_group` | User to group links. |
| `user_permission` | `pachifast_user_permission` | User to permission links. |
| `mfa_config` | `pachifast_mfa_config` | Multi-factor configuration of each user. |
| `mfa_pending` | `pachifast_mfa_pending` | Pending MFA challenges. |
| `activity_log` | `pachifast_activity_log` | [Activity log](events.md#activity-log). |
| `ws_ticket` | `pachifast_ws_ticket` | [WebSocket tickets](events.md#one-time-tickets) (only used when `events_ticket_store=database`). |

Alembic keeps one version table per target: `<table_prefix>alembic_version_<target>` (for example `pachifast_alembic_version_auth`).

### Renaming tables

Change the prefix for all tables, or override a single table:

```env
PACHIFAST_TABLE_PREFIX=myapp_
PACHIFAST_USER_TABLE=accounts
```

Overrides exist for `user_table`, `user_settings_table`, `permission_table`, `group_table`, `group_permission_table`, `user_group_table`, `user_permission_table`, `activity_log_table`, `mfa_config_table` and `mfa_pending_table`. A non-empty override wins over the prefix. `ws_ticket` has no override and always follows the prefix.

Use `get_table_name` for tables you want to follow the same convention:

```python
from pachifast.config import get_settings

TABLE = get_settings().get_table_name("invoice")  # "pachifast_invoice" (or "<table_prefix>invoice")
```

> **Warning**: the table names are fixed when the models are imported. Change the prefix before the first migration; renaming tables of a live database requires a manual Alembic revision.

## Creating the schema

There are two ways to create the core tables.

| | `auto_create_auth_schema=true` | Alembic (recommended) |
| --- | --- | --- |
| How | At startup the core calls `create_all` for the core tables. | `pachifast migrations upgrade`. |
| Creates missing tables | Yes | Yes |
| Alters existing tables | **No** | Yes |
| History and rollback | No | Yes |
| Best for | Prototypes, tests, throw-away SQLite files | Anything you keep |

`auto_create_auth_schema` defaults to `false`. Do not mix the two on the same database: tables created by `create_all` are unknown to Alembic's history.

`pachifast init` sets everything up for a new project (`.env`, the first revision, the upgrade and the first superuser); see [Operations](operations.md#bootstrapping-a-project).

## Migration targets

A **migration target** is a named schema history bound to one database. It is declared on a module with `MigrationTarget`:

```python
from pachifast import AppModule, MigrationTarget

module = AppModule(
    name="billing",
    kind="app",
    router_modules=("apps.billing.root",),
    migration_targets=(
        MigrationTarget(
            name="billing",
            model_modules=("apps.billing.models",),
            database_url_resolver="apps.billing.settings:get_database_url",
        ),
    ),
)
```

| Field | Description |
| --- | --- |
| `name` | Unique target name, used as `--target-name`. Duplicates raise an error. |
| `model_modules` | Modules to import so their models are registered. Only tables found in these modules are managed (so autogenerate never touches tables of other targets). |
| `database_url_field` | Name of a settings field that holds the URL (for example `"AUTH_DATABASE_URL"`). |
| `database_url_resolver` | Alternative: an import reference `"package.module:function"` returning the URL. The function takes either no arguments or the settings object. |
| `version_table` | Optional name of the Alembic version table. Default: `<table_prefix>alembic_version_<name>`. |

A target must declare `database_url_field` or `database_url_resolver`. Each module can declare several targets, and `pachifast new app` / `pachifast new common` with a database generate one for you.

The built-in `auth` target (declared by the core module) covers `pachifast.auth.models`, `pachifast.mfa.models`, `pachifast.audit.models` and `pachifast.events.models`, that is every table in the previous section.

## The `migrations` commands

All subcommands read `project_modules.py` from the project root; run them from the project directory or pass `--base-dir`. Use `pachifast migrations --help` for the full help.

```bash
pachifast migrations list
pachifast migrations init
pachifast migrations revision "add invoices" --target-name billing --autogenerate
pachifast migrations upgrade --target-name billing
pachifast migrations downgrade -1 --target-name billing
```

| Command | Arguments and options | Description |
| --- | --- | --- |
| `list` | `--base-dir PATH` (default `.`) | Print the names of the declared targets. |
| `init` | `--base-dir PATH`, `--target-name NAME` (omit for all targets) | Create `migrations/<target>/` with `env.py`, `script.py.mako` and `versions/`. Prints each path. Safe to run again: `env.py` and the template are rewritten, revisions are kept. |
| `revision` | `MESSAGE`, `--target-name NAME` (**required**), `--autogenerate`, `--base-dir PATH` | Create a revision. Without `--autogenerate` it is empty and you write it by hand. |
| `upgrade` | `[REVISION]` (default `head`), `--target-name NAME` (**required**), `--base-dir PATH` | Apply revisions up to `REVISION`. |
| `downgrade` | `REVISION` (**required**, for example `-1` or `base`), `--target-name NAME` (**required**), `--base-dir PATH` | Roll back to `REVISION`. |

Resulting layout:

```text
migrations/
  auth/
    env.py
    script.py.mako
    versions/
  billing/
    env.py
    script.py.mako
    versions/
```

Details worth knowing:

- Commands other than `init` fail with a hint if `migrations/<target>/` does not exist.
- The database URL comes from the target, so `.env` or the environment must define it.
- Generated `env.py` files work in sync and async mode (the async markers are `+asyncpg` and `+aiosqlite`) and compare types and server defaults.
- Review autogenerated revisions before applying them.

A typical flow for a new target:

```bash
pachifast migrations init --target-name billing
pachifast migrations revision "init billing" --target-name billing --autogenerate
pachifast migrations upgrade --target-name billing
```

See [CLI reference](../reference/cli.md) for every command of the tool.

## Upgrading an existing project

The `auth` target creates, besides the user and permission tables, the **activity log**, **MFA** and **WebSocket ticket** tables. A project that was bootstrapped with an older version has no revision for them. Generate one and apply it:

```bash
pachifast migrations revision "activity log, mfa and ws tickets" --target-name auth --autogenerate
pachifast migrations upgrade --target-name auth
```

Check the generated file in `migrations/auth/versions/` before upgrading: it should only contain `create_table` operations for `<prefix>mfa_config`, `<prefix>mfa_pending`, `<prefix>activity_log` and `<prefix>ws_ticket`.

Which table is needed by which feature:

| Feature | Table | If it is missing |
| --- | --- | --- |
| `activity_log_enabled` | `activity_log` | Writes to the log fail and are logged as errors; the request itself is not affected. |
| Superuser snapshot of the event stream | `activity_log` | Empty snapshot and one warning per process; the connection stays open. |
| `MFA_ENABLED` | `mfa_config`, `mfa_pending` | Login and MFA enrolment queries fail. |
| `events_ticket_store=database` (default) | `ws_ticket` | Issuing a ticket fails with a server error. |

Projects that rely on `auto_create_auth_schema=true` get any missing table at the next startup, without a revision.

## See also

- [Configuration](configuration.md): where URLs and settings are read from.
- [Modules](modules.md): declaring modules and migration targets.
- [Operations](operations.md): `pachifast init`, running and health checks.
- [Deployment](deployment.md): applying migrations in a release pipeline.
