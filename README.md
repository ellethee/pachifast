<p align="center"><img src="https://raw.githubusercontent.com/ellethee/pachifast/main/docs/assets/logo.svg" alt="pachifast" width="160"></p>

# pachifast

[Italiano](README.it.md)

An open-source framework for building [FastAPI](https://fastapi.tiangolo.com/) services with a modular structure, a CLI
that scaffolds projects and modules, built-in authentication with roles and permissions, and multi-database Alembic
migrations.

Version: **<!-- version -->0.15.0<!-- /version -->** - Author: **ilPachi** - License: **MIT** - Status: **alpha** (0.x: minor releases may contain breaking changes).

## Features

- **Modular by design**: an explicit registry of modules (`project_modules.py`), `apps/` for application modules and
  `common/` for shared ones; each module brings its routers, settings, migrations, hooks and message catalogs.
- **Authentication and authorization**: JWT access and refresh tokens, Argon2id passwords, login lockout, users, groups
  and permissions, optional MFA (TOTP, email and SMS codes), per-user settings with a UI schema.
- **A uniform API contract**: every success is `{success, message, data}`, every error is a non-2xx response with
  `{"detail": [{loc, msg, type, code, params?}]}` and a stable `code`; camelCase JSON; pagination built in.
- **Localized messages**: stable message codes, catalogs per module and per project, and an exporter that produces the
  JSON files a vue-i18n frontend needs (with a check-only mode for CI).
- **Live events**: an authenticated WebSocket with one-time tickets, an activity log, and automatic disconnection of
  users who are deleted, deactivated or demoted.
- **Migrations**: one Alembic target per database declared by the modules.
- **Operations**: `serve` for production, generated Dockerfile and CI/CD pipelines (GitHub, GitLab, Gitea), health and
  version endpoints, optional Redis cache.
- **Editor-friendly configuration**: environment variables, `.env` and a `pachifast.yaml` with a JSON Schema.

## Install

Python 3.11 or later and [uv](https://docs.astral.sh/uv/) are required:

```bash
uv tool install pachifast
pachifast --version
```

In a project, `uv add pachifast` does the same. Or install from a release tag of the source (replace `vX.Y.Z` with a tag
of the repository):

```bash
uv tool install "git+https://github.com/ellethee/pachifast@vX.Y.Z"
```

Redis cache support is an extra: `pachifast[redis]`; PostgreSQL is `pachifast[postgres]`. Supported databases: SQLite and
PostgreSQL. See [Getting started](docs/getting-started.md) for more installation options.

## Stability

pachifast is **alpha**: the API contract (response envelope, message codes) is protected by tests, but settings, CLI and
the database schema of the core may change in any 0.x minor release, always announced in the [changelog](CHANGELOG.md)
and in [Upgrading](docs/guides/upgrading.md). Details and the deprecation policy: [Stability](docs/guides/stability.md).

## Quick start

```bash
pachifast new project demo && cd demo   # scaffold a project
pachifast new app hello                 # add an application module
pachifast init                          # .env with a secret key, SQLite database, migrations, first superuser
pachifast dev                           # run with auto-reload
```

Then open <http://127.0.0.1:8000/docs> (application endpoints) or <http://127.0.0.1:8000/docs/core> (authentication,
administration, health, version). Application routes require a bearer token by default: log in at
`POST /api/auth/token`.

Want to see a complete project first? [`examples/demo`](examples/demo) is a working backend (MFA, model admin, a module with
tables, uploads, bulk actions and per-user settings) that runs on SQLite with no external services.

A project looks like this:

```text
demo/
  apps/            # application modules
  common/          # shared modules
  main.py          # the ASGI app
  project_modules.py
  pachifast.yaml   # optional configuration (with a JSON Schema for editors)
  pyproject.toml
```

## Documentation

The documentation is published at <https://pachifast.ilpachi.it/> and lives in [`docs/`](docs/index.md):

- [Getting started](docs/getting-started.md)
- Guides: [configuration](docs/guides/configuration.md), [modules](docs/guides/modules.md),
  [authentication](docs/guides/authentication.md), [routing and responses](docs/guides/routing-and-responses.md),
  [errors and i18n](docs/guides/errors-and-i18n.md), [events](docs/guides/events.md), [cache](docs/guides/cache.md),
  [database and migrations](docs/guides/database-and-migrations.md), [operations](docs/guides/operations.md),
  [deployment](docs/guides/deployment.md), [publishing](docs/guides/publishing.md),
  [frontend integration](docs/guides/frontend-integration.md)
- Reference: [CLI](docs/reference/cli.md), [settings](docs/reference/settings.md),
  [message codes](docs/reference/error-codes.md)
- [Changelog](CHANGELOG.md) and [contributing](docs/contributing.md)

## License

MIT, see [LICENSE](LICENSE).

**Disclaimer.** pachifast is provided "as is", without warranty of any kind. It is alpha software: it can contain bugs
and change incompatibly between minor releases. You are responsible for reviewing, testing and securing the
applications you build with it before using them in production. See the [stability policy](docs/guides/stability.md).
