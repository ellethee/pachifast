# Extending the core

Two hooks let a project change what the core does without patching it: **signals**, to react to what happens, and the **permission provider**, to say where permissions come from. Both are registered once, at startup (a module hook, see [Modules](modules.md)), and live in the process.

## Signals

`pachifast.signals` calls your handlers when the core finishes an action. A handler is a function or a coroutine function that takes keyword arguments; ignore the ones you do not need with `**_`.

```python
from pachifast import signals


@signals.connect(signals.USER_CREATED)
async def welcome(*, user, **_) -> None: ...  # send the welcome mail, create the rows the user needs


signals.connect(signals.LOGIN_FAILED, lambda *, username, **_: print("failed login for", username))
```

| Signal | When | Keyword arguments |
| --- | --- | --- |
| `USER_CREATED` | a user was saved (registration, admin, `pachifast users create`) | `user` |
| `USER_LOGGED_IN` | a login finished, with the second factor if the user has one | `user` |
| `LOGIN_FAILED` | `POST /auth/token` refused the credentials | `username`, as typed: the account may not exist |
| `PASSWORD_CHANGED` | a password was saved (own change, admin, CLI) | `user` |
| `USER_LOGGED_OUT` | `POST /auth/logout` | `user` |

Rules:

- Handlers run one after the other, in the order they were connected, **inside the request** that emitted the signal. Keep them short; for slow work start a task with `pachifast.utils.tasks.spawn`.
- A handler that raises is logged and skipped: it cannot fail the request or stop the other handlers.
- Signals are per process. With several workers each runs the handlers of the requests it serves. For a push to the browsers use [events](events.md).
- `signals.disconnect(signal, handler)` removes a handler; `signals.clear()` removes all (tests).

## Permission provider

By default the permissions of a user are the codenames of the groups and direct permissions stored in the database. A project that keeps them elsewhere registers a provider:

```python
from pachifast.auth.permission_provider import set_permission_provider


async def from_claims(session, user_id):
    return {"reports.view"}  # any iterable of codenames


set_permission_provider(from_claims)
```

The provider replaces the lookup behind `permissions=[...]` on routes, the checks of the [model admin](model-admin.md) and the `permissions` of `GET /auth/user`. Superusers still bypass every check. The screens that edit groups and permissions in the database (`/admin/permissions`) keep showing the database. `set_permission_provider(None)` goes back to the default.

## A profile for your own user columns

The user table of the core is fixed, so that upgrading pachifast never alters a table that holds your data. Put your columns in a one-to-one table of your project that points at the user, and register it:

```python
import uuid

from pydantic import Field
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from pachifast.auth.profile import register_profile
from pachifast.database import AliasedBaseModel, Base


class Profile(Base):  # your model: your migration (`pachifast migrations revision --autogenerate`)
    __tablename__ = "profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pachifast_user.id", ondelete="CASCADE"), primary_key=True)
    phone: Mapped[str | None] = mapped_column(default=None)


class ProfileSchema(AliasedBaseModel):  # what the API accepts and shows
    phone: str | None = Field(default=None, max_length=30)


register_profile(Profile, ProfileSchema)
```

`GET /auth/user` then has a `profile` object, and `PATCH /auth/user` accepts `{"profile": {"phone": "..."}}`: only the fields that are sent change, the schema validates them (`422` like any body, `loc` starts with `body.profile`) and the row is created on the first save. A user without a row sees the defaults of the schema. Without `register_profile`, `profile` is `null` and a PATCH that sends one is `422 user.profile_not_configured`. Every field of the schema must be a column of the model: `register_profile` checks it at startup.

## Setup hooks: middleware and exception handlers

`AppModule.setup_hooks` are plain functions (not coroutines) that receive the `FastAPI` app while it is built, before it serves anything. A middleware must be added there: Starlette refuses `add_middleware` once the app has started, so a `startup_hook` is too late. A middleware added by a setup hook wraps the core ones except the request id, which stays the outermost.

```python
def install(app) -> None:
    app.add_middleware(MyMiddleware)
    app.add_exception_handler(MyError, handle_my_error)


module = AppModule(name="mine", setup_hooks=(install,))
```

## Packages that extend pachifast

An extension is an ordinary Python package that exports an `AppModule` (routers, settings, hooks, migration targets), which a project lists in its `project_modules.py` like its own modules. By convention:

- the distribution is called `pachifast-<name>` and the module lives in `pachifast_<name>` (`pachifast_<name>.module:module`);
- the module sets `requires_pachifast`, a PEP 440 specifier such as `">=0.16,<0.17"`: at startup pachifast refuses a module that needs another version and says which, instead of failing later in some route;
- it takes its settings from the `PACHIFAST_*` environment and `pachifast.yaml` like the rest, and documents them.

`pachifast-metrics` (a `/metrics` endpoint for Prometheus, with a request middleware) is the first one and the model to copy. It is installed from PyPI, and listed in `project_modules.py`:

```bash
uv add pachifast-metrics
```

```python
from pachifast.presets import core_module
from pachifast_metrics import module as metrics_module

MODULES = (core_module, metrics_module)
```

To write your own, copy its layout: `module.py` builds the `AppModule` (router modules, `setup_hooks` for a middleware, `requires_pachifast`), `root.py` holds the router (`router.pachifast_mount_prefix = ""` mounts it at the root instead of under `/api`), and the tests build a real app with `create_app((core_module, module))`.

## What stays outside the core

SMS delivery, TOTP and email codes are in the core. A database other than SQLite and PostgreSQL (MySQL, MariaDB, SQL Server) is not supported; a driver package would have to bring its own column types and migration tests. Both hooks above and the profile are alpha: names may change in a minor release (see [Stability](stability.md)).
