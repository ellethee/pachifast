# Getting started

pachifast is a modular framework for building [FastAPI](https://fastapi.tiangolo.com/) services, plus a `typer`-based CLI that generates projects and modules. It gives you the parts almost every service needs, already wired together:

- an explicit module registry (`project_modules.py`) that decides what the application mounts
- JWT authentication, Argon2id password hashing and RBAC (users, groups, permissions)
- a standard JSON response envelope and a stable error format with machine-readable codes
- Alembic migrations with one target per database
- optional building blocks: activity log, WebSocket events, TOTP multi-factor authentication, UDP listeners

This page takes you from nothing to a running service you can log in to.

## Requirements

- Python 3.11 or later
- [uv](https://docs.astral.sh/uv/), the only package manager the documentation and the generated projects assume

SQLite works out of the box (`aiosqlite` is a core dependency). Every other database driver is an extra, so you install only what you use:

| Extra | Install | Adds |
| --- | --- | --- |
| `postgres` | `pachifast[postgres]` | PostgreSQL (`asyncpg`) |
| `postgres-sync` | `pachifast[postgres-sync]` | PostgreSQL for synchronous sessions (`psycopg`) |
| `redis` | `pachifast[redis]` | shared cache backend (see [Cache](guides/cache.md)) |

## Installation

pachifast is published on [PyPI](https://pypi.org/project/pachifast/). With [uv](https://docs.astral.sh/uv/):

```bash
uv tool install pachifast
pachifast --version
```

To run the CLI once without installing it, use `uvx pachifast new project demo`. In a project, `uv add pachifast` adds it as a dependency.

**Private registry (optional).** If you host a private fork of pachifast on your own package registry, point uv at its `simple` index (for example `https://<host>/<path>/simple`). `--index` adds it **in addition to** PyPI:

```bash
uv tool install pachifast --index private=https://<host>/<path>/simple
```

If it requires authentication, give uv the credentials through environment variables named after the index (`UV_INDEX_PRIVATE_USERNAME`, `UV_INDEX_PRIVATE_PASSWORD`) and never write them in a committed file. For a project, declare the index in `pyproject.toml` without credentials (`[[tool.uv.index]]` with `name` and `url`). If the registry uses a certificate from a private certificate authority, set `UV_SYSTEM_CERTS=1`. The CI/CD side is described in [Deployment](guides/deployment.md#private-package-index).

## Quick start

Create a project, add an application module, then let `init` prepare everything else:

```bash
# 1. a new project (a uv project; git is initialised, no commit is made)
pachifast new project demo --no-interactive
cd demo

# 2. a module: an app with no database of its own...
pachifast new app hello
#    ...or one with its own database and migrations
pachifast new app billing --with-db

# 3. .env (secret key + SQLite database), migrations and the first superuser
uv sync
uv run pachifast init

# 4. run the development server with auto-reload
uv run pachifast dev
```

Without `--no-interactive`, `new project` asks (in a terminal) for the `pyproject.toml` properties you did not pass as options. The full option list is in the [CLI reference](reference/cli.md).

`pachifast init` is safe to run again: it never overwrites a value already in `.env`, does not recreate migrations that exist, and leaves an existing user alone. It prompts for the superuser password unless you pass `--password`. Useful options:

| Option | Meaning |
| --- | --- |
| `--username`, `-u` | superuser username (default `admin`) |
| `--password` | superuser password; prompted if omitted |
| `--email` | superuser email |
| `--database-url` | URL of the auth database; default is a `db.sqlite3` file in the project |
| `--no-user` | do not create the superuser |
| `--target-name` | migration target of the auth tables (default `auth`) |

Prefer to do it by hand? Set `PACHIFAST_SECRET_KEY` and `PACHIFAST_AUTH_DATABASE_URL`, then run `pachifast migrations init`, `pachifast migrations revision --target-name auth --autogenerate init`, `pachifast migrations upgrade --target-name auth` and `pachifast users create admin --superuser`. See [Database and migrations](guides/database-and-migrations.md).

## Project layout

`pachifast new project demo` followed by `new app hello`, `new app billing --with-db` and `new common tools` produces:

```text
demo/
  apps/
    hello/
      module.py          # the AppModule declaration
      root.py            # a router exposing GET /api/hello
      locale/            # message catalogs (en.py)
    billing/
      module.py
      root.py
      settings.py        # per-app settings (BILLING_DATABASE_URL, ...)
      models.py          # SQLAlchemy models of the billing database
      locale/
  common/
    tools/               # a shared module (kind="common")
      module.py
      settings.py
      locale/
  main.py                # create_app(MODULES, cors_origins=[...])
  project_modules.py     # the MODULES registry
  pachifast.yaml         # optional configuration, with the editor schema hook
  pachifast.schema.json  # JSON Schema for pachifast.yaml
  .env.example           # PACHIFAST_SECRET_KEY placeholder
  pyproject.toml
  README.md
```

After `pachifast init` you also get `.env` (never commit it: the generated `.gitignore` already excludes it), `db.sqlite3` and a `migrations/` folder with one subfolder per migration target (`auth`, plus `billing` once you create its migrations).

The two files that matter most are small. `main.py`:

```python
from pachifast import create_app

from project_modules import MODULES

app = create_app(
    MODULES,
    cors_origins=[
        "http://localhost",
        "http://127.0.0.1:8000",
    ],
)
```

and `project_modules.py`, the single place that decides what the application is made of:

```python
from pachifast.presets import core_module

from apps.hello.module import module as hello_module
from apps.billing.module import module as billing_module

MODULES = (
    core_module,
    hello_module,
    billing_module,
)
```

`new app` and `new common` add their module to this file for you. How modules are declared and composed is covered in [Modules](guides/modules.md).

## Your first requests

`pachifast dev` listens on `127.0.0.1:8000` by default (loopback only: use `--host 0.0.0.0` to reach it from another machine) (override with `--host`/`--port`, `PACHIFAST_HOST`/`PACHIFAST_PORT`, or `pachifast.yaml`). Everything is mounted under `/api`.

The health endpoint is public:

```bash
curl http://127.0.0.1:8000/api/health/
```

```json
{"success": true, "message": null, "data": {"name": "", "version": "", "description": "", "database": true}}
```

Application routes require authentication. Without a token you get a 401 in the standard [error format](guides/errors-and-i18n.md):

```bash
curl http://127.0.0.1:8000/api/hello
```

```json
{"detail": [{"loc": ["header", "Authorization"], "msg": "Not authenticated", "type": "value_error", "code": "auth.not_authenticated"}]}
```

Log in with the superuser created by `init`. The token endpoint follows the OAuth2 password flow, so it takes **form data**, not JSON:

```bash
curl -X POST http://127.0.0.1:8000/api/auth/token \
  -d 'username=admin' \
  -d 'password=<your-password>'
```

```json
{"access_token": "<jwt>", "token_type": "bearer", "refresh_token": "<jwt>"}
```

Use the access token as a Bearer token:

```bash
curl http://127.0.0.1:8000/api/hello -H "Authorization: Bearer <access_token>"
```

```json
{"success": true, "message": null, "data": {"module": "hello", "status": "ok"}}
```

Successful responses are wrapped in the `{success, message, data}` envelope; the token endpoints are the exception and answer with the plain token object. When the access token expires, exchange the refresh token at `POST /api/auth/refresh-token`. Details are in [Authentication](guides/authentication.md) and [Routing and responses](guides/routing-and-responses.md).

### Interactive documentation

Open the Swagger UIs in a browser:

| URL | Shows |
| --- | --- |
| `http://127.0.0.1:8000/docs` | the routes of `app` and `common` modules (yours) |
| `http://127.0.0.1:8000/docs/core` | the built-in routes only: auth, admin, health, version |

Use the **Authorize** button with the superuser credentials to try protected routes.

## Without the CLI

The CLI only generates files: a service is plain Python. This is the smallest one that runs (checked with `TestClient` and `uvicorn`), in a folder of your choice:

```text
hello/
  __init__.py       # empty
  root.py
main.py
pachifast.yaml
```

`hello/root.py`, a router mounted at `/api/hello` (the prefix comes from the module path):

```python
from pachifast.router_factory import get_router

router = get_router()


@router.get("")
async def say_hello() -> dict:
    return {"message": "hello"}
```

`main.py`, the registry and the ASGI app:

```python
from pachifast import AppModule, create_app
from pachifast.presets import core_module

hello = AppModule(name="hello", kind="app", router_modules=("hello.root",))

app = create_app((core_module, hello))
```

`pachifast.yaml` (or the same value in the environment as `PACHIFAST_AUTH_DATABASE_URL`):

```yaml
auth_database_url: sqlite+aiosqlite:///./db.sqlite3
```

Run it with a secret key in the environment (the key is required: the app refuses to start without it):

```bash
export PACHIFAST_SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
uv run uvicorn main:app
curl http://127.0.0.1:8000/api/health/    # {"success": true, ..., "data": {"database": true, ...}}
curl http://127.0.0.1:8000/api/hello      # 401 auth.not_authenticated: routes are protected by default
```

Without the migrations and a user nobody can log in yet, which is why the route answers `401`; create the tables and the first superuser with `pachifast migrations ...` and `pachifast users create` as described above, or see [Database and migrations](guides/database-and-migrations.md).

## Next steps

- [Configuration](guides/configuration.md): environment variables, `.env`, `pachifast.yaml` and per-app settings
- [Modules](guides/modules.md): compose the application, add hooks, switch routers off
- [Authentication](guides/authentication.md): users, groups, permissions and tokens
- [Routing and responses](guides/routing-and-responses.md): declare routes and permissions
- [Database and migrations](guides/database-and-migrations.md): one migration target per database
- [Deployment](guides/deployment.md): `pachifast serve`, container image and CI pipeline
- [CLI reference](reference/cli.md) and [Settings reference](reference/settings.md)
