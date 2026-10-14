# pachifast demo

A complete, self-contained backend built on pachifast. It needs no frontend, and no external database: everything runs on SQLite from this folder.

It shows:

- the core (authentication, users, permissions, refresh tokens), MFA and the generic model admin;
- an app module (`apps/demo`) with its own tables and migration target, a startup seed, lookups, a paginated and filterable items API with bulk actions, file upload and download, per-user settings and message catalogs in English and Italian.

## Run it

From the repository root:

```bash
cd examples/demo
uv sync
uv run pachifast init --username admin --password change-me-please   # .env, auth migrations, first superuser
uv run pachifast migrations upgrade --target-name demo               # tables of the demo app
uv run pachifast dev
```

Open <http://127.0.0.1:8000/docs> for the API of the demo app and <http://127.0.0.1:8000/docs/core> for the built-in routes. Log in with the superuser you just created:

```bash
curl -X POST http://127.0.0.1:8000/api/auth/token -d "username=admin" -d "password=change-me-please"
```

`init` prepares the auth database only; every other migration target (here `demo`) is applied with `migrations upgrade`. Both are safe to run again. The sample records are inserted at startup, only when the table is empty.

## Use a published release

The `pyproject.toml` points at the local sources so the demo always matches the repository. To try a released version from PyPI, delete the `[tool.uv.sources]` section; `uv sync` then installs `pachifast` from PyPI.

## A frontend

Any frontend that follows the pachifast contract (response envelope, errors, token refresh, server-side tables) works against this backend: see the [frontend integration guide](../../docs/guides/frontend-integration.md).
