"""What the model admin knows about a model: its fields, the schemas to read and write it, its list parameters."""

from __future__ import annotations

import dataclasses
import datetime as dt
import decimal
import enum
import typing
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Literal, Optional, get_args, get_origin

from pydantic import BaseModel, Field, TypeAdapter, create_model
from pydantic_core import to_jsonable_python
from sqlalchemy import LargeBinary, String
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Mapped
from sqlalchemy.orm.base import LoaderCallableStatus

from pachifast.i18n import lookup
from pachifast.schemas.aliased import AliasedBaseModel
from pachifast.schemas.responses import PageParams

from .registry import ModelAdmin

__all__ = ["RANGE_TYPES", "AdminFields", "FieldSpec", "describe", "humanize", "to_naive_utc"]


#: Data types that can be filtered by a range (``<column>__gte`` and ``<column>__lte``).
RANGE_TYPES = frozenset({"integer", "number", "date", "datetime", "time"})


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p.capitalize() for p in rest)


def to_naive_utc(value: Any) -> Any:
    """An aware datetime as naive UTC (the content of a column without time zone); other values unchanged."""
    if isinstance(value, dt.datetime) and value.tzinfo is not None:
        return value.astimezone(dt.UTC).replace(tzinfo=None)
    return value


def humanize(name: str) -> str:
    """``created_at`` becomes ``Created at``."""
    return name.replace("_", " ").strip().capitalize()


@dataclass
class FieldSpec:
    """One column of the administered model."""

    name: str
    annotation: Any
    nullable: bool
    required: bool
    readonly: bool
    primary_key: bool
    component: str
    maxlength: int | None = None
    options: list[Any] | None = None
    type: str = "string"
    fk_table: str | None = None
    naive_datetime: bool = False
    default: Any = None
    meta: dict[str, Any] = field(default_factory=dict)


def _inner_annotation(model: type, name: str, column: Any) -> Any:
    """The Python type of a column: the ``Mapped[...]`` annotation when it can be resolved, else the SQL type's."""
    try:
        hint = typing.get_type_hints(model).get(name)
    except Exception:  # forward references that cannot be resolved
        hint = None
    if hint is not None and get_origin(hint) is Mapped:
        hint = get_args(hint)[0]
    if hint is None:
        try:
            hint = column.type.python_type
        except NotImplementedError:
            hint = Any
    return hint


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """``(X, True)`` for ``X | None`` and ``(annotation, False)`` otherwise."""
    import types

    if get_origin(annotation) is typing.Union or isinstance(annotation, getattr(types, "UnionType", ())):
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) < len(get_args(annotation)) and len(args) == 1:
            return args[0], True
    return annotation, False


def _component(annotation: Any, column: Any) -> tuple[str, list[Any] | None]:
    """Component and options that fit a Python type."""
    if get_origin(annotation) is Literal:
        return "select", list(get_args(annotation))
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return "select", [m.value for m in annotation]
    if annotation is bool:
        return "toggle", None
    if annotation in (int, float, decimal.Decimal):
        return "number", None
    if annotation is dt.datetime:
        return "datetime", None
    if annotation is dt.date:
        return "date", None
    if annotation is dt.time:
        return "time", None
    if annotation in (dict, list) or get_origin(annotation) in (dict, list):
        return "json", None
    if annotation is str and getattr(column.type, "length", 1) is None:
        return "textarea", None
    return "input", None


def _has_default(model: type, name: str, column: Any) -> bool:
    """True when a row can be created without a value for the column."""
    if dataclasses.is_dataclass(model):
        for f in dataclasses.fields(model):
            if f.name == name:
                return f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING
    return column.default is not None or column.server_default is not None or bool(column.autoincrement is True)


def _data_type(annotation: Any, options: list[Any] | None) -> str:
    """The JSON data type of a column: string, integer, number, boolean, date, datetime, time or json."""
    if options:
        annotation = type(options[0])
    if annotation is bool:
        return "boolean"
    if annotation is int:
        return "integer"
    if annotation in (float, decimal.Decimal):
        return "number"
    if annotation is dt.datetime:
        return "datetime"
    if annotation is dt.date:
        return "date"
    if annotation is dt.time:
        return "time"
    if annotation in (dict, list) or get_origin(annotation) in (dict, list):
        return "json"
    return "string"


def _static_default(model: type, name: str, column: Any) -> Any:
    """The default a new row gets when it is a plain value (JSON safe); ``None`` for factories and server defaults."""
    value: Any = None
    if dataclasses.is_dataclass(model):
        for f in dataclasses.fields(model):
            if f.name == name and f.default is not dataclasses.MISSING:
                # SQLAlchemy 2.1 marks "no Python default" with an enum member: the column default decides then
                value = None if isinstance(f.default, LoaderCallableStatus) else f.default
                break
        else:
            value = None
    if value is None and column.default is not None and getattr(column.default, "is_scalar", False):
        value = column.default.arg
    try:
        return to_jsonable_python(value)
    except Exception:
        return None


@dataclass
class AdminFields:
    """The fields of one registered model and the schemas derived from them (built once, see :func:`describe`)."""

    admin: type[ModelAdmin]
    fields: dict[str, FieldSpec]
    pk: FieldSpec
    read_model: type[BaseModel]
    create_model_: type[BaseModel]
    update_model: type[BaseModel]
    params_model: type[PageParams]
    pk_adapter: TypeAdapter[Any]
    writable: set[str]
    ranges: dict[str, tuple[str, str]]

    @property
    def listed(self) -> list[str]:
        """Names of the table columns, in order."""
        wanted = self.admin.list_display or list(self.fields)
        names = [n for n in wanted if n in self.fields]
        if not self.admin.list_display and self.pk.name in names:
            names.remove(self.pk.name)
            names.insert(0, self.pk.name)
        return names


def _label_text(admin: type[ModelAdmin], name: str, default: str) -> tuple[str, str]:
    """``(text, key)`` of a label: the catalog entry ``admin.<app>.<model>.<name>.label`` or the default."""
    key = f"admin.{admin.app}.{admin.name}.{name}.label"
    return lookup(key) or default, key


def label_for(admin: type[ModelAdmin], name: str, default: str) -> tuple[str, str]:
    """Text and key of a label, public for the schema builder."""
    return _label_text(admin, name, default)


@cache
def describe(admin: type[ModelAdmin]) -> AdminFields:
    """Inspect the administered model and build the pydantic models the routes use."""
    model = admin.model
    mapper = sa_inspect(model).mapper
    fields: dict[str, FieldSpec] = {}
    for attr in mapper.column_attrs:
        name = attr.key
        column = attr.columns[0]
        if name.startswith("_") or name in admin.exclude or isinstance(column.type, LargeBinary):
            continue
        annotation, nullable_hint = _unwrap_optional(_inner_annotation(model, name, column))
        component, options = _component(annotation, column)
        meta = dict(admin.fields_meta.get(name, {}))
        pk = bool(column.primary_key)
        fields[name] = FieldSpec(
            name=name,
            annotation=annotation,
            nullable=bool(column.nullable) or nullable_hint,
            required=not pk and not column.nullable and not _has_default(model, name, column),
            readonly=pk or name in admin.readonly_fields,
            primary_key=pk,
            component=meta.pop("component", component),
            maxlength=getattr(column.type, "length", None) if isinstance(column.type, String) else None,
            options=options,
            type=_data_type(annotation, options),
            default=_static_default(model, name, column),
            naive_datetime=annotation is dt.datetime and not getattr(column.type, "timezone", False),
            fk_table=next((fk.column.table.name for fk in column.foreign_keys), None),
            meta=meta,
        )
    pk_name = mapper.primary_key[0].key
    pk_spec = fields[pk_name]

    read_fields: dict[str, Any] = {}
    create_fields: dict[str, Any] = {}
    update_fields: dict[str, Any] = {}
    for name, spec in fields.items():
        shown = Optional[spec.annotation] if spec.nullable else spec.annotation  # noqa: UP045 - dynamic type
        read_fields[name] = (shown, None if spec.nullable else ...)
        if spec.readonly:
            continue
        constraint = {"max_length": spec.maxlength} if spec.maxlength and spec.annotation is str else {}
        create_fields[name] = (shown, Field(... if spec.required else None, **constraint))
        update_fields[name] = (shown, Field(None, **constraint))

    title = f"{admin.app}_{admin.name}"
    read_model = create_model(f"{title}_read", __base__=AliasedBaseModel, **read_fields)
    create_model_ = create_model(f"{title}_create", __base__=AliasedBaseModel, **create_fields)
    update_model = create_model(f"{title}_update", __base__=AliasedBaseModel, **update_fields)

    filter_fields = {
        name: (Optional[fields[name].annotation], None)  # noqa: UP045
        for name in admin.list_filter
        if name in fields
    }
    ranges: dict[str, tuple[str, str]] = {}
    for name in admin.list_filter:
        spec = fields.get(name)
        if spec is None or spec.type not in RANGE_TYPES or spec.options:
            continue
        for op in ("gte", "lte"):
            param = f"{name}__{op}"
            alias = f"{_camel(name)}__{op}"
            filter_fields[param] = (Optional[spec.annotation], Field(None, alias=alias))  # noqa: UP045
            ranges[param] = (name, op)
    params_model = create_model(f"{title}_params", __base__=PageParams, **filter_fields)
    params_model.search_fields = list(admin.search_fields)
    params_model.order_fields = list(
        admin.order_fields or [n for n in dict.fromkeys([*(admin.list_display or fields), pk_name]) if n in fields]
    )

    return AdminFields(
        admin=admin,
        fields=fields,
        pk=pk_spec,
        read_model=read_model,
        create_model_=create_model_,
        update_model=update_model,
        params_model=params_model,
        pk_adapter=TypeAdapter(pk_spec.annotation),
        writable={n for n, s in fields.items() if not s.readonly},
        ranges=ranges,
    )


__all__ += ["label_for"]
