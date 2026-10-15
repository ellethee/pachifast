# Changelog

All notable changes to pachifast are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [semantic versioning](https://semver.org/)
(while the major version is 0, minor releases may contain breaking changes).

## [Unreleased]

## [0.16.0] - 2026-10-15

### Changed

- **`pachifast.yaml` is organised in sections** (`app`, `server`, `dev`, `log`, `routes`, `database`, `cache`, `auth`, `mfa`, `tables`, `activity_log`, `events`), for example `server: {port: 9000}`. The flat keys of 0.15 are still read but deprecated: one warning per file lists the `section.key` to use. An unknown key inside a section, a section that is not a mapping, or the same setting given twice (flat and nested) is now an error. The JSON Schema (`pachifast config schema`, new projects, `docs/reference`) is regenerated with one object per section, and the settings reference shows the YAML location of every setting. See [Upgrade notes](https://pachifast.ilpachi.it/guides/upgrading/#upgrade-notes).

## [0.15.0] - 2026-10-14

First public release. pachifast is alpha software: see [Stability and support](https://pachifast.ilpachi.it/guides/stability/).

### Added

- **Application factory and modules**: `create_app`, an explicit module registry (`project_modules.py`) and `AppModule`, with routers, settings, migrations, startup and setup hooks, message catalogs, and `requires_pachifast` to declare the pachifast versions a module works with.
- **Authentication and authorization**: JWT access and refresh tokens, Argon2id passwords, login lockout and rate limiting, users, groups and permissions, a pluggable permission provider, optional MFA (TOTP, email and SMS codes), per-user settings with a UI schema, and a one-to-one user profile for your own columns (`register_profile`).
- **A uniform API contract**: the response envelope `{success, message, data}`, errors as `{"detail": [{loc, msg, type, code, params?}]}` with stable message codes, camelCase JSON and built-in pagination.
- **Localized messages**: catalogs per module and per project, and an exporter that produces the JSON files a vue-i18n frontend needs, with a check-only mode for CI.
- **Generic model admin**, an **activity log** with an index and a retention command (`pachifast audit purge`), lifecycle **signals** (`pachifast.signals`), and live **events** over an authenticated WebSocket with one-time tickets.
- **Database and migrations**: SQLAlchemy 2.0 with SQLite and PostgreSQL, one Alembic target per database declared by the modules, and a configurable connection pool (`db_pool_*`).
- **Operations**: `serve` for production, health endpoints (`/`, `/live`, `/ready`), request ids, JSON logs, an optional Redis cache, UDP listeners, and a generated Dockerfile and CI/CD pipelines for GitHub, GitLab and Gitea.
- **Scaffolding CLI**: `pachifast new` for projects, modules and pipelines, plus commands for migrations, users, settings, i18n and the audit log.
- **Configuration**: environment variables, `.env` and a `pachifast.yaml` with a JSON Schema.
- **Extensions**: separate packages named `pachifast-<name>` that export an `AppModule` (for example `pachifast-metrics`).
- Documentation site with generated reference pages for the CLI, the settings and the message codes.
