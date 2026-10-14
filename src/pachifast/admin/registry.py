"""Registry of the models that appear in the generic model admin (``register_admin``)."""

from __future__ import annotations

import importlib
import logging
import re
from pathlib import Path
from typing import Any, ClassVar, TypeVar

from sqlalchemy import inspect as sa_inspect

from pachifast.database.base import Base

log = logging.getLogger(__name__)

ACTIONS = ("view", "add", "change", "delete")
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

A = TypeVar("A", bound="ModelAdmin")

#: The bulk action every model has; it needs the ``delete`` permission.
DELETE_SELECTED = "delete_selected"
MAX_ACTION_IDS = 500


def admin_action(*, label: str | None = None, permission: str = "change", confirm: bool = True) -> Any:
    """Mark a method of a :class:`ModelAdmin` as a bulk action: ``async def name(self, session, rows)``.

    ``rows`` are the selected ORM objects; the method changes them (or deletes them with ``session.delete``) and may
    return a text for the user. It must not commit: the admin commits once, together with the history rows.
    ``permission`` is the action of the model the user needs (``change`` by default).
    """
    if permission not in ACTIONS:
        raise ValueError(f"permission must be one of {ACTIONS}")

    def decorator(func: Any) -> Any:
        func._admin_action = {"label": label, "permission": permission, "confirm": confirm}
        return func

    return decorator


class ModelAdmin:
    """Declaration of how one model is administered; subclass it and register it with :func:`register_admin`.

    Every attribute is optional: with none of them the admin lists every column, searches nothing and
    orders by the primary key.

    Attributes:
        app:             prefix of the routes and of the permissions (``<app>.<model>.view``); by default the
                         package that holds the ``admin`` module (``apps.billing.admin`` gives ``billing``)
        name:            name of the model in routes and permissions; default: the table name
        title:           human title; default: the class name spaced out
        list_display:    columns of the table (default: all, primary key first)
        search_fields:   columns searched by the ``search`` text (case insensitive, substring)
        list_filter:     columns that can be filtered by exact value (``?status=paid``)
        ordering:        default order, ``"-id"`` style (default: the primary key, descending)
        order_fields:    columns a client may sort by (default: ``list_display``)
        readonly_fields: columns shown but never written through the admin
        exclude:         columns that never leave the server (not listed, not read, not written)
        fields_meta:     per column overrides of the schema: ``{"status": {"label": "State"}}``
        label_field:     column used as the name of a row (default: the first of ``list_display`` that is text)
        actions:         names of the methods marked with :func:`admin_action` offered as bulk actions
                         (``delete_selected`` is always available to those who can delete)
    """

    model: ClassVar[type[Base]]
    app: ClassVar[str | None] = None
    name: ClassVar[str | None] = None
    title: ClassVar[str | None] = None
    list_display: ClassVar[list[str]] = []
    search_fields: ClassVar[list[str]] = []
    list_filter: ClassVar[list[str]] = []
    ordering: ClassVar[list[str]] = []
    order_fields: ClassVar[list[str] | None] = None
    readonly_fields: ClassVar[list[str]] = []
    exclude: ClassVar[list[str]] = []
    fields_meta: ClassVar[dict[str, dict[str, Any]]] = {}
    label_field: ClassVar[str | None] = None
    actions: ClassVar[list[str]] = []


_registry: dict[tuple[str, str], type[ModelAdmin]] = {}


def _default_app(admin_cls: type[ModelAdmin]) -> str:
    """The package that holds the admin module: ``apps.billing.admin`` gives ``billing``."""
    parts = admin_cls.__module__.split(".")
    if len(parts) >= 2 and parts[-1] == "admin":
        return parts[-2]
    return "default"


def register_admin(model: type[Base]) -> Any:
    """Class decorator: administer ``model`` with the decorated :class:`ModelAdmin`.

    ``app`` and ``name`` must be lowercase identifiers: they end up in URLs and in permission codenames, and
    two models cannot share the same pair.
    """

    def decorator(admin_cls: type[A]) -> type[A]:
        mapper = sa_inspect(model).mapper
        if len(mapper.primary_key) != 1:
            raise ValueError(f"{model.__name__}: the model admin needs a single primary key column")
        admin_cls.model = model
        admin_cls.app = admin_cls.app or _default_app(admin_cls)
        admin_cls.name = admin_cls.name or model.__tablename__  # type: ignore[attr-defined]
        for what, value in (("app", admin_cls.app), ("name", admin_cls.name)):
            if not _NAME.match(value or ""):
                raise ValueError(f"{admin_cls.__name__}.{what} {value!r} must be a lowercase identifier")
        columns = {a.key for a in mapper.column_attrs}
        for attr in ("list_display", "search_fields", "list_filter", "ordering", "readonly_fields", "exclude"):
            unknown = {c.lstrip("-") for c in getattr(admin_cls, attr)} - columns
            if unknown:
                raise ValueError(f"{admin_cls.__name__}.{attr}: unknown column(s) {sorted(unknown)}")
        hidden = set(admin_cls.exclude)
        for attr in ("list_display", "search_fields", "list_filter", "ordering", "readonly_fields"):
            leaked = {c.lstrip("-") for c in getattr(admin_cls, attr)} & hidden
            if leaked:
                raise ValueError(
                    f"{admin_cls.__name__}.{attr}: {sorted(leaked)} are in exclude, so they never leave the server"
                )
        label = admin_cls.label_field
        if label is not None and (label not in columns or label in hidden):
            raise ValueError(f"{admin_cls.__name__}.label_field {label!r} must be a column that is not in exclude")
        for column in admin_cls.order_fields or []:
            if column not in columns or column in hidden:
                raise ValueError(
                    f"{admin_cls.__name__}.order_fields: {column!r} must be a column that is not in exclude"
                )
        for action in admin_cls.actions:
            method = getattr(admin_cls, action, None)
            if action == DELETE_SELECTED or not hasattr(method, "_admin_action"):
                raise ValueError(f"{admin_cls.__name__}.actions: {action!r} is not a method marked with @admin_action")
        key = (admin_cls.app, admin_cls.name)
        existing = _registry.get(key)
        if existing is not None and existing is not admin_cls:
            raise ValueError(f"{key[0]}.{key[1]} is already registered by {existing.__name__}")
        _registry[key] = admin_cls
        return admin_cls

    return decorator


def get_admin(app: str, name: str) -> type[ModelAdmin] | None:
    """The admin registered for ``<app>.<name>``, or ``None``."""
    return _registry.get((app, name))


def all_admins() -> list[type[ModelAdmin]]:
    """Every registered admin, ordered by app and name."""
    return [_registry[key] for key in sorted(_registry)]


def clear_registry() -> None:
    """Forget every registration (for tests)."""
    _registry.clear()


def permission_codename(admin_cls: type[ModelAdmin], action: str) -> str:
    """``<app>.<name>.<action>`` for ``action`` in :data:`ACTIONS`."""
    return f"{admin_cls.app}.{admin_cls.name}.{action}"


def discover_admin_modules(root: Path | None = None) -> None:
    """Import ``apps/**/admin.py`` and ``common/**/admin.py`` so that their ``@register_admin`` run."""
    rootpath = Path(root or Path.cwd())
    for folder in ("apps", "common"):
        folder_path = rootpath / folder
        if not folder_path.exists():
            continue
        for path in sorted(folder_path.rglob("admin.py")):
            module_path = path.relative_to(rootpath).with_suffix("").as_posix().replace("/", ".")
            try:
                importlib.import_module(module_path)
            except Exception:
                log.exception("Error importing the admin module %s", module_path)
