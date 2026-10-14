---
template: home.html
hide:
  - navigation
  - toc
---

# pachifast

pachifast is an open-source framework for building [FastAPI](https://fastapi.tiangolo.com/) services with a modular
structure. It ships a CLI that generates projects and modules, built-in authentication with roles and permissions,
multi-database Alembic migrations, a uniform API contract (response envelope, stable error codes, localized messages)
and the tooling to share that contract with a frontend.

Version: **<!-- version -->0.15.0<!-- /version -->** - Author: **ilPachi** - License: **MIT** - Status: alpha (0.x, minor releases may contain breaking changes).

## Where to start

| I want to... | Read |
| --- | --- |
| install pachifast and run a first project | [Getting started](getting-started.md) |
| configure the application | [Configuration](guides/configuration.md), [Settings reference](reference/settings.md) |
| split my code into modules | [Modules](guides/modules.md) |
| understand users, tokens, permissions and MFA | [Authentication](guides/authentication.md) |
| know what my endpoints return and how errors look | [Routing and responses](guides/routing-and-responses.md), [Errors and i18n](guides/errors-and-i18n.md) |
| push live events to a frontend | [Events and activity log](guides/events.md) |
| manage databases and migrations | [Database and migrations](guides/database-and-migrations.md) |
| run it in production, build the image and pipeline | [Operations](guides/operations.md), [Deployment](guides/deployment.md) |
| connect a frontend | [Frontend integration](guides/frontend-integration.md) |
| look up a command, a setting or a message code | [CLI](reference/cli.md), [Settings](reference/settings.md), [Message codes](reference/error-codes.md) |

## Concepts in one minute

- A **project** is a directory with `main.py`, `project_modules.py` and the `apps/` and `common/` packages.
- A **module** (`AppModule`) bundles routers, settings, migration targets, startup hooks and message catalogs. The
  built-in `core` module provides authentication, administration, health and version endpoints.
- Every successful JSON response is wrapped in `{success, message, data}`; every error is a non-2xx response with
  `{"detail": [{loc, msg, type, code, params?}]}` where `code` is a stable identifier clients translate from.
- Settings come from environment variables (`PACHIFAST_*`), `.env` and an optional `pachifast.yaml`.

See the [Changelog](changelog.md) for what changed in each release.
