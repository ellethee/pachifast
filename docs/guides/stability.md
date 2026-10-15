# Stability and support

pachifast is **alpha** software. The API is still settling: this page says what you can rely on, what may change and how a change is announced.

pachifast is provided "as is", without warranty of any kind (see the [MIT license](https://github.com/ellethee/pachifast/blob/main/LICENSE)). The stability levels below describe intent, not a guarantee: review, test and secure your application before running it in production.

## What is stable

| Area | Level | What it means |
| --- | --- | --- |
| Response envelope `{success, message, data}` and the error body `{"detail": [{loc, msg, type, code, params?}]}` | Stable | Protected by `tests/test_envelope_contract.py`. A change is a breaking change and is listed in the changelog. |
| Message codes (`auth.not_authenticated`, ...) and their `params` | Stable | A code, or a placeholder of a code, is never removed or renamed outside a major release. New codes are added. The contract is `tests/contracts/error_codes.json`, enforced by `tests/test_error_code_contract.py`. The text of a message is **not** part of the contract: translate from the `code`. |
| `pachifast.yaml` format (sections) and the settings | Mostly stable | A setting can be added at any time. Renaming or removing one, or changing a default, happens in a minor release and is announced under *Changed* or *Removed* in the changelog. |
| CLI commands and options | Mostly stable | Same rule as the settings. The output text of a command is not a contract; the exit code is. |
| Python API | Stable at the top level | What `pachifast/__init__.py` exports (`create_app`, `AppModule`, `MigrationTarget`, `ModuleKind`, `__version__`) plus the modules the guides document (`pachifast.router_factory`, `pachifast.database`, `pachifast.presets`, and so on). A module or a name that no guide mentions is internal, even without an underscore. |
| Database schema and migrations of the core | Unstable | A minor release may add or change columns and tables of the core (users, groups, permissions, MFA, refresh tokens, activity log). Every change is listed in the [changelog](../changelog.md) and in [Upgrading](upgrading.md); autogenerate a revision for the `auth` target after each upgrade. |

Anything not listed (the layout of the package, private helpers, the log format, the generated project files) can change in any release.

## Versioning

pachifast follows [semantic versioning](https://semver.org/), with the usual allowance for `0.x`:

- a **patch** release (`0.15.0` to `0.15.1`) contains fixes only and never breaks anything above;
- a **minor** release (`0.15.x` to `0.16.0`) may break: it is the only moment where a setting, a route, a default or the schema changes in an incompatible way;
- `1.0` will freeze the stable rows of the table and stop breaking changes outside a major release.

Pin the minor in your project (`pachifast>=0.15,<0.16`) and read [Upgrading](upgrading.md) and the [changelog](../changelog.md) before moving it.

## Deprecation

When something that the table above calls stable has to go:

1. the change is announced under **Deprecated** in the changelog, in a release at least **one minor** before the removal;
2. where Python allows it, the old name raises a `DeprecationWarning` (run your tests with `-W error::DeprecationWarning` to find the uses);
3. the removal is listed under **Removed** (or **Changed**, marked **Breaking**) and, when it needs an action from you, is described in [Upgrading](upgrading.md).

Security fixes are the exception: they may break a behaviour in a minor release without a deprecation period (see `SECURITY.md`).

## Supported platforms

| Component | Supported |
| --- | --- |
| Python | 3.11 or later |
| SQLAlchemy | 2.0. 2.1 is exercised in CI in a non-blocking job: it is expected to work, but is not guaranteed |
| Databases | **SQLite** and **PostgreSQL** (the test suite runs on both). MySQL and MariaDB are neither supported nor tested |
| Cache | in-process memory, or Redis through the `redis` extra |

Only the latest minor release receives fixes, including security fixes.
