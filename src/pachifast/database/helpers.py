from collections.abc import Collection

from sqlalchemy import asc, desc, inspect
from sqlalchemy.sql import ColumnElement

from pachifast.errors import InvalidOrderByError
from pachifast.schemas.responses import T
from pachifast.utils.helpers import to_snake

from .base import Base


def _sortable_columns(model: type[T], allowed: Collection[str] | None) -> set[str]:
    """Names of the mapped columns of ``model`` that a client may sort by (``allowed`` narrows them)."""
    columns = {a.key for a in inspect(model).mapper.column_attrs if not a.key.startswith("_")}
    return columns if allowed is None else columns & set(allowed)


def get_order_by_clause(
    model: type[T], order_str: str | None, allowed: Collection[str] | None = None
) -> ColumnElement | None:
    """``ORDER BY`` clause for ``"field"`` or ``"-field"``; camelCase and snake_case are accepted.

    Only mapped columns can be used (never a method or a relationship), and ``allowed`` can restrict them further.
    """
    if not order_str:
        return None

    desc_order = order_str.startswith("-")
    field_name = order_str.lstrip("-")
    sortable = _sortable_columns(model, allowed)

    if field_name not in sortable:
        snake_name = to_snake(field_name)
        if snake_name not in sortable:
            raise InvalidOrderByError(field_name)
        field_name = snake_name

    field = getattr(model, field_name)
    return desc(field) if desc_order else asc(field)


def get_order_by_clauses(
    model: type[T], order_strs: str | None, allowed: Collection[str] | None = None
) -> list[ColumnElement]:
    """Clauses for a comma separated ``orderBy`` such as ``-createdAt,number``."""
    if not order_strs:
        return []

    clauses = []
    for order_str in order_strs.split(","):
        clause = get_order_by_clause(model, order_str.strip(), allowed)
        if clause is not None:
            clauses.append(clause)
    return clauses


def get_model_resolver(model_repository: dict[str, type[Base]]):
    """Resolve the ORM model from its name."""

    def resolve_class(name: str) -> type:
        model = model_repository.get(name.lower())
        if not model:
            raise ValueError(f"Unsupported class: {name}")
        return model

    return resolve_class


def pop_non_fields_from_dict_by_model(model: type[T], data: dict, fields: list[str] | None = None) -> dict:
    """
    Drop the fields not present on the ORM model and return the remaining ones.
    """
    if fields is None:
        fields = [a.key for a in inspect(model).mapper.column_attrs if not a.key.startswith("_")]

    valid_fields = {field: data[field] for field in fields if field in data}
    # invalid_fields = {field: data[field] for field in data if field not in valid_fields}

    return valid_fields
