"""The metadata a generic frontend needs to draw the table and the form of an administered model."""

from __future__ import annotations

import re
import uuid
from typing import Any

from pachifast.i18n import lookup

from .fields import RANGE_TYPES, AdminFields, FieldSpec, describe, humanize, label_for
from .registry import ACTIONS, DELETE_SELECTED, ModelAdmin, all_admins, permission_codename

_KEY_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p.capitalize() for p in rest)


def _options(admin: type[ModelAdmin], spec: FieldSpec) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """``(options, optionKeys)`` of a select: the values, a readable label and the key a client can translate."""
    options: list[dict[str, Any]] = []
    keys: dict[str, str] = {}
    for value in spec.options or []:
        key = f"admin.{admin.app}.{admin.name}.{spec.name}.options.{value}"
        default = str(value).replace("_", " ").capitalize()
        options.append({"label": lookup(key) or default, "value": value})
        if isinstance(value, str) and _KEY_PART.match(value):
            keys[value] = key
    return options, keys


def _lookup_target(spec: FieldSpec) -> type[ModelAdmin] | None:
    """The registered admin a foreign key column points at (same table), or ``None``."""
    if spec.fk_table is None or spec.primary_key:
        return None
    return next((a for a in all_admins() if a.model.__table__.name == spec.fk_table), None)  # type: ignore[attr-defined]


def field_schema(admin: type[ModelAdmin], spec: FieldSpec) -> dict[str, Any]:
    """Schema of one field: component, label (and its key), constraints and select options."""
    label, label_key = label_for(admin, spec.name, spec.meta.get("label") or humanize(spec.name))
    entry: dict[str, Any] = {
        "component": spec.component,
        "label": label,
        "labelKey": label_key,
        "required": spec.required,
        "readonly": spec.readonly,
        "nullable": spec.nullable,
        "primaryKey": spec.primary_key,
        "type": spec.type,
        "default": spec.default,
    }
    if spec.maxlength:
        entry["maxlength"] = spec.maxlength
    target = _lookup_target(spec)
    if target is not None:
        entry["component"] = "lookup"
        entry["lookup"] = {"app": target.app, "model": target.name}
    if spec.options is not None:
        options, keys = _options(admin, spec)
        entry["properties"] = {"options": options, "emitValue": True, "mapOptions": True}
        if keys:
            entry["optionKeys"] = keys
    elif spec.component == "number":
        entry["properties"] = {"step": 1 if spec.type == "integer" else "any"}
    elif spec.annotation is bool:
        entry["properties"] = {}
    if isinstance(spec.meta.get("properties"), dict):
        entry["properties"] = {**entry.get("properties", {}), **spec.meta["properties"]}
    entry.update({k: v for k, v in spec.meta.items() if k not in entry and k not in ("label", "properties")})
    return entry


def _title(admin: type[ModelAdmin]) -> tuple[str, str]:
    """Title of the model and its key."""
    key = f"admin.{admin.app}.{admin.name}.title"
    default = admin.title or humanize(re.sub(r"(?<!^)(?=[A-Z])", "_", admin.model.__name__).lower())
    return lookup(key) or default, key


def allowed_actions(admin: type[ModelAdmin], user: Any, user_permissions: set[str]) -> dict[str, bool]:
    """What the user may do with the model: a superuser everything, others by ``<app>.<model>.<action>``."""
    if getattr(user, "is_superuser", False):
        return dict.fromkeys(ACTIONS, True)
    return {a: permission_codename(admin, a) in user_permissions for a in ACTIONS}


def model_summary(admin: type[ModelAdmin], permissions: dict[str, bool]) -> dict[str, Any]:
    """One line of the list of administered models (the menu of the admin)."""
    title, title_key = _title(admin)
    return {
        "app": admin.app,
        "model": admin.name,
        "title": title,
        "titleKey": title_key,
        "permissions": permissions,
    }


def _pk_type(pk: FieldSpec) -> str:
    """``int``, ``uuid`` or ``str``: how a client builds the URL of a row."""
    if pk.annotation is uuid.UUID:
        return "uuid"
    return "int" if pk.type == "integer" else "str"


def _action_label(admin: type[ModelAdmin], name: str, label: str | None) -> tuple[str, str]:
    """Text and key of a bulk action's label."""
    if name == DELETE_SELECTED:
        key = "admin.text.action.delete_selected"
        return lookup(key) or "Delete selected", key
    key = f"admin.{admin.app}.{admin.name}.actions.{name}.label"
    return lookup(key) or label or humanize(name), key


def actions_schema(admin: type[ModelAdmin], permissions: dict[str, bool]) -> list[dict[str, Any]]:
    """The bulk actions of a model: the built-in delete and the declared ones, each with whether the user may run it."""
    items = [(DELETE_SELECTED, {"label": None, "permission": "delete", "confirm": True})]
    items += [(n, getattr(admin, n)._admin_action) for n in admin.actions]
    result = []
    for name, meta in items:
        label, key = _action_label(admin, name, meta["label"])
        result.append(
            {
                "name": name,
                "label": label,
                "labelKey": key,
                "permission": meta["permission"],
                "confirm": meta["confirm"],
                "allowed": permissions[meta["permission"]],
            }
        )
    return result


def model_schema(admin: type[ModelAdmin], permissions: dict[str, bool]) -> dict[str, Any]:
    """Full schema of a model: permissions, table, filters and fields (keys in camelCase like the data)."""
    info: AdminFields = describe(admin)
    fields = {_camel(name): field_schema(admin, spec) for name, spec in info.fields.items()}
    filters = []
    for name in admin.list_filter:
        spec = info.fields.get(name)
        if spec is None:
            continue
        entry = field_schema(admin, spec)
        item = {
            "field": _camel(name),
            **{
                k: entry[k]
                for k in ("component", "label", "labelKey", "properties", "optionKeys", "lookup")
                if k in entry
            },
        }
        if spec.type in RANGE_TYPES and not spec.options and item.get("component") != "lookup":
            item["range"] = True
        filters.append(item)
    ordering = list(admin.ordering) or [f"-{info.pk.name}"]
    return {
        **model_summary(admin, permissions),
        "primaryKey": _camel(info.pk.name),
        "primaryKeyType": _pk_type(info.pk),
        "list": {
            "display": [_camel(n) for n in info.listed],
            "search": bool(admin.search_fields),
            "ordering": [("-" if o.startswith("-") else "") + _camel(o.lstrip("-")) for o in ordering],
            "orderFields": [_camel(n) for n in (info.params_model.order_fields or [])],
            "filters": filters,
        },
        "fields": fields,
        "actions": actions_schema(admin, permissions),
        "history": True,
    }


def iter_text_keys(admins: list[type[ModelAdmin]] | None = None) -> list[tuple[str, str]]:
    """Every translatable text of the model admin as ``(key, default text)``: titles, labels, options, actions.

    Used by ``pachifast locale admin``; the keys are the ones the schema names (``titleKey``, ``labelKey``,
    ``optionKeys``).
    """
    rows: list[tuple[str, str]] = []
    for admin in admins if admins is not None else all_admins():
        base = f"admin.{admin.app}.{admin.name}"
        rows.append((f"{base}.title", admin.title or humanize(admin.model.__name__)))
        for name, spec in describe(admin).fields.items():
            rows.append((f"{base}.{name}.label", spec.meta.get("label") or humanize(name)))
            for value in spec.options or []:
                if isinstance(value, str) and _KEY_PART.match(value):
                    rows.append((f"{base}.{name}.options.{value}", str(value).replace("_", " ").capitalize()))
        for action in admin.actions:
            meta = getattr(admin, action)._admin_action
            rows.append((f"{base}.actions.{action}.label", meta["label"] or humanize(action)))
    return rows
