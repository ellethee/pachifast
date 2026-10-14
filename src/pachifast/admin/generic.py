"""Routes of the generic model admin: ``/api/admin/models``.

One router serves every model registered with ``@register_admin`` (see :mod:`pachifast.admin.registry`): the
model is looked up from the URL, so there is no code generated per model. Each action needs the permission
``<app>.<model>.<view|add|change|delete>`` (a superuser has them all).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Body, Depends, Request, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import IntegrityError

from pachifast.auth.dependencies import get_current_active_user
from pachifast.auth.models import User
from pachifast.auth.permission_provider import resolve_user_permissions
from pachifast.database.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, paginate
from pachifast.dependencies import CoreSessionDep
from pachifast.i18n import api_error, translate
from pachifast.routing.router_factory import get_router
from pachifast.schemas.responses import PageParams, PaginatedResponse

from .fields import AdminFields, _camel, describe, to_naive_utc
from .history import diff, record
from .models import AdminChange
from .registry import (
    DELETE_SELECTED,
    MAX_ACTION_IDS,
    ModelAdmin,
    discover_admin_modules,
    get_admin,
    permission_codename,
)
from .schema import allowed_actions, model_schema, model_summary

router = get_router(name="admin/models")

discover_admin_modules()


class Target:
    """The model a request is about, the admin of the user over it and the fields of the model."""

    def __init__(self, admin: type[ModelAdmin], user: User, permissions: dict[str, bool]) -> None:
        self.admin = admin
        self.user = user
        self.permissions = permissions
        self.info: AdminFields = describe(admin)


async def _target(app: str, model: str, user: User, session: CoreSessionDep) -> Target:
    admin = get_admin(app, model)
    if admin is None:
        raise api_error(status.HTTP_404_NOT_FOUND, "admin.model_not_found", loc=("path", "model"), app=app, model=model)
    perms = allowed_actions(admin, user, set(await resolve_user_permissions(session, user.id)))
    return Target(admin, user, perms)


def require(action: str | None):
    """Dependency: resolve ``{app}/{model}`` from the URL and demand the permission of ``action`` (or none)."""

    async def dependency(
        app: str, model: str, session: CoreSessionDep, user: Annotated[User, Depends(get_current_active_user)]
    ) -> Target:
        target = await _target(app, model, user, session)
        if action is not None and not target.permissions[action]:
            raise api_error(
                status.HTTP_403_FORBIDDEN,
                "access.permissions_missing",
                loc=("header", "Authorization"),
                permissions=permission_codename(target.admin, action),
            )
        return target

    return dependency


def _errors(info: AdminFields, exc: ValidationError, where: str) -> RequestValidationError:
    """The uniform 422 for a pydantic error: ``loc`` is ``[where, <camelCase name of the schema>]`` even when the client
    sent the snake_case one."""
    errors = exc.errors(include_url=False, include_context=False, include_input=False)
    return RequestValidationError(
        [
            {**e, "loc": (where, *(_camel(p) if isinstance(p, str) and p in info.fields else p for p in e["loc"]))}
            for e in errors
        ]
    )


def _validated(model: type[BaseModel], payload: dict[str, Any], info: AdminFields) -> dict[str, Any]:
    """Validate a body against the dynamic model and return only what was sent, by field name.

    A datetime with an offset sent to a column without time zone is converted to naive UTC.
    """
    try:
        data = model.model_validate(payload).model_dump(exclude_unset=True, by_alias=False)
        return {k: to_naive_utc(v) if info.fields[k].naive_datetime else v for k, v in data.items()}
    except ValidationError as exc:
        raise _errors(info, exc, "body") from exc


def _page_params(info: AdminFields, query: dict[str, str]) -> PageParams:
    """The list parameters of a request: ``page`` 1 and ``pageSize`` 25 by default, at most 200 rows, 422 if invalid."""
    query.setdefault("page", "1")
    query.setdefault("pageSize", str(DEFAULT_PAGE_SIZE))
    try:
        params: PageParams = info.params_model.model_validate(query)
    except ValidationError as exc:
        raise _errors(info, exc, "query") from exc
    params.pageSize = min(params.pageSize or DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE)
    return params


def _range_filter(info: AdminFields):
    """Extra filter of the list: ``<column>__gte`` and ``<column>__lte`` of the date, time and number columns."""

    def apply(params: Any, kwargs: dict[str, Any], stmt: Any, model: Any) -> tuple[dict[str, Any], Any]:
        for param, (column, op) in info.ranges.items():
            value = kwargs.pop(param, None)
            if value is None:
                continue
            if info.fields[column].naive_datetime:
                value = to_naive_utc(value)
            col = getattr(model, column)
            stmt = stmt.where(col >= value if op == "gte" else col <= value)
        return kwargs, stmt

    return apply


def _dump(info: AdminFields, row: Any) -> dict[str, Any]:
    """A row as the JSON the client sees: only the columns the admin exposes, keys in camelCase."""
    return info.read_model.model_validate(row, from_attributes=True).model_dump(mode="json", by_alias=True)


async def _get_row(target: Target, session: CoreSessionDep, pk: str) -> Any:
    try:
        key = target.info.pk_adapter.validate_python(pk)
    except ValidationError:
        key = None
    row = await session.get(target.admin.model, key) if key is not None else None
    if row is None:
        raise api_error(status.HTTP_404_NOT_FOUND, "admin.object_not_found", loc=("path", "pk"), pk=pk)
    return row


def _who(target: Target, row: Any) -> dict[str, Any]:
    """Arguments of :func:`record` that identify the row and the user."""
    return {
        "app": target.admin.app,
        "model": target.admin.name,
        "pk": getattr(row, target.info.pk.name),
        "user": target.user,
    }


def _integrity(exc: IntegrityError) -> Any:
    """409 for a write refused by a database constraint (unique, foreign key, not null)."""
    return api_error(status.HTTP_409_CONFLICT, "admin.integrity_error", loc=("body",))


@router.get("", summary="List the models the user can administer")
async def list_models(session: CoreSessionDep, user: Annotated[User, Depends(get_current_active_user)]):
    """The administered models of which the user may see at least the list (the menu of the admin)."""
    from .registry import all_admins

    perms = set(await resolve_user_permissions(session, user.id))
    result = []
    for admin in all_admins():
        allowed = allowed_actions(admin, user, perms)
        if allowed["view"]:
            result.append(model_summary(admin, allowed))
    return result


@router.get("/{app}/{model}/_schema", summary="Schema of a model: table, filters and form fields")
async def model_schema_route(target: Annotated[Target, Depends(require("view"))]):
    """What a generic frontend needs to draw the table and the form (needs ``view``; permissions are the user's)."""
    return model_schema(target.admin, target.permissions)


@router.get("/{app}/{model}/_options", summary="Rows of a model as value/label pairs (for selects)")
async def model_options(request: Request, session: CoreSessionDep, target: Annotated[Target, Depends(require("view"))]):
    """``{items: [{value, label}], total, page, pageSize, pages}`` for a select that points at this model.

    ``search`` looks into ``search_fields``; ``ids=1,2`` returns just those rows (to show the label of a value
    already chosen). Needs ``view`` on the model. The label is the ``label_field`` column.
    """
    from sqlalchemy import String, cast, or_, select

    info, admin = target.info, target.admin
    label_name = admin.label_field or next(
        (n for n in info.listed if n != info.pk.name and info.fields[n].type == "string"), info.pk.name
    )
    pk_col, label_col = getattr(admin.model, info.pk.name), getattr(admin.model, label_name)
    stmt = select(pk_col, label_col)
    ids = request.query_params.get("ids")
    if ids:
        try:
            stmt = stmt.where(pk_col.in_([info.pk_adapter.validate_python(i) for i in ids.split(",") if i]))
        except ValidationError as exc:
            errors = exc.errors(include_url=False, include_context=False, include_input=False)
            raise RequestValidationError([{**e, "loc": ("query", "ids")} for e in errors]) from exc
    search = request.query_params.get("search")
    if search and admin.search_fields:
        stmt = stmt.where(
            or_(
                *(cast(getattr(admin.model, f), String).icontains(search, autoescape=True) for f in admin.search_fields)
            )
        )
    stmt = stmt.order_by(label_col, pk_col)
    from fastapi_pagination.api import set_page
    from fastapi_pagination.ext.sqlalchemy import apaginate

    from pachifast.schemas.responses import PageWindow

    params = _page_params(info, dict(request.query_params))
    window = PageWindow(params.page, params.pageSize)
    with set_page(PaginatedResponse):
        page = await apaginate(session, stmt, window)
    return PaginatedResponse(
        items=[
            {"value": _json_value(pk), "label": str(label) if label is not None else str(pk)}
            for pk, label in page.items
        ],
        total=page.total,
        page=page.page,
        pageSize=page.pageSize,
    )


def _json_value(value: Any) -> Any:
    """A primary key as JSON (UUIDs become strings)."""
    from pydantic_core import to_jsonable_python

    return to_jsonable_python(value)


@router.get("/{app}/{model}", summary="List the rows of a model")
async def list_rows(request: Request, session: CoreSessionDep, target: Annotated[Target, Depends(require("view"))]):
    """Paginated rows: ``page``, ``pageSize`` (default 25, at most 200), ``orderBy``, ``search`` and the filters."""
    info, admin = target.info, target.admin
    params = _page_params(info, dict(request.query_params))
    if not params.orderBy:
        params.orderBy = ",".join(admin.ordering) or f"-{info.pk.name}"
    page = await paginate(session, admin.model, params, extra_filters=[_range_filter(info)])
    assert isinstance(page, PaginatedResponse)
    return PaginatedResponse(
        items=[_dump(info, row) for row in page.items], total=page.total, page=page.page, pageSize=page.pageSize
    )


@router.get("/{app}/{model}/{pk}", summary="One row of a model")
async def get_row(pk: str, session: CoreSessionDep, target: Annotated[Target, Depends(require("view"))]):
    return _dump(target.info, await _get_row(target, session, pk))


@router.get("/{app}/{model}/{pk}/history", summary="Changes made to a row through the admin")
async def row_history(
    pk: str, request: Request, session: CoreSessionDep, target: Annotated[Target, Depends(require("view"))]
):
    """Newest first, paginated: ``{id, timestamp, userId, username, action, changes}``.

    ``action`` is ``add``, ``change``, ``delete`` or ``action:<name>``; ``changes`` maps each changed column to
    ``{old, new}``. It works for a deleted row too (the history is kept).
    """
    from pachifast.database.pagination import paginate as paginate_rows

    params = _page_params(target.info, dict(request.query_params))
    params.orderBy = "-id"
    stmt_params = PageParams.model_validate({"page": params.page, "pageSize": params.pageSize, "orderBy": "-id"})
    page = await paginate_rows(
        session,
        AdminChange,
        stmt_params,
        extra_filters=[
            lambda _p, kw, stmt, model: (
                kw,
                stmt.where(model.app == target.admin.app, model.model == target.admin.name, model.pk == pk),
            )
        ],
    )
    assert isinstance(page, PaginatedResponse)
    items = [
        {
            "id": c.id,
            "timestamp": c.timestamp.isoformat(),
            "userId": str(c.user_id) if c.user_id else None,
            "username": c.username,
            "action": c.action,
            "changes": c.changes or {},
        }
        for c in page.items
    ]
    return PaginatedResponse(items=items, total=page.total, page=page.page, pageSize=page.pageSize)


@router.post("/{app}/{model}/_actions/{name}", summary="Run a bulk action on selected rows")
async def run_action(
    name: str,
    body: Annotated[dict[str, Any], Body()],
    session: CoreSessionDep,
    target: Annotated[Target, Depends(require(None))],
):
    """Run the action ``name`` (``delete_selected`` or one declared with ``@admin_action``) on ``{"ids": [...]}``.

    All or nothing: an id that does not exist answers 404 and nothing changes. At most 500 ids. Needs the
    permission of the action (``delete`` for ``delete_selected``, ``change`` unless the action says otherwise).
    """
    admin, info = target.admin, target.info
    if name == DELETE_SELECTED:
        needed, method = "delete", None
    elif name in admin.actions:
        needed, method = getattr(admin, name)._admin_action["permission"], getattr(admin(), name)
    else:
        raise api_error(status.HTTP_404_NOT_FOUND, "admin.action_not_found", loc=("path", "name"), name=name)
    if not target.permissions[needed]:
        raise api_error(
            status.HTTP_403_FORBIDDEN,
            "access.permissions_missing",
            loc=("header", "Authorization"),
            permissions=permission_codename(admin, needed),
        )
    raw_ids = body.get("ids")
    if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > MAX_ACTION_IDS:
        raise RequestValidationError(
            [{"loc": ("body", "ids"), "msg": f"1 to {MAX_ACTION_IDS} ids", "type": "value_error"}]
        )
    try:
        ids = list(dict.fromkeys(info.pk_adapter.validate_python(i) for i in raw_ids))
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        raise RequestValidationError([{**e, "loc": ("body", "ids")} for e in errors]) from exc
    from sqlalchemy import select

    pk_col = getattr(admin.model, info.pk.name)
    rows = list((await session.execute(select(admin.model).where(pk_col.in_(ids)))).scalars())
    if len(rows) != len(ids):
        found = {getattr(r, info.pk.name) for r in rows}
        missing = next(i for i in ids if i not in found)
        raise api_error(status.HTTP_404_NOT_FOUND, "admin.object_not_found", loc=("body", "ids"), pk=str(missing))
    message: str | None = None
    try:
        if method is None:
            for row in rows:
                record(session, **_who(target, row), action="delete", changes=diff(_dump(info, row), None))
                await session.delete(row)
            message = translate("admin.text.deleted_selected", {"count": len(rows)})
        else:
            before = {getattr(r, info.pk.name): _dump(info, r) for r in rows}
            result = method(session, rows)
            if hasattr(result, "__await__"):
                result = await result
            message = result if isinstance(result, str) else None
            await session.flush()
            for row in rows:
                changes = diff(before[getattr(row, info.pk.name)], _dump(info, row))
                record(session, **_who(target, row), action=f"action:{name}", changes=changes)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _integrity(exc) from exc
    return {"affected": len(rows), "message": message}


@router.post(
    "/{app}/{model}",
    status_code=status.HTTP_201_CREATED,
    summary="Create a row",
    log_as="{username} created a row of {app}.{model}",
)
async def create_row(
    body: Annotated[dict[str, Any], Body()],
    session: CoreSessionDep,
    target: Annotated[Target, Depends(require("add"))],
):
    """Create a row; only the writable columns are read from the body (the others are ignored)."""
    data = _validated(target.info.create_model_, body, target.info)
    row = target.admin.model(**data)
    session.add(row)
    try:
        await session.flush()
        record(session, **_who(target, row), action="add", changes=diff(None, _dump(target.info, row)))
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _integrity(exc) from exc
    await session.refresh(row)
    return _dump(target.info, row)


@router.patch("/{app}/{model}/{pk}", summary="Update a row", log_as="{username} changed {app}.{model} #{pk}")
async def update_row(
    pk: str,
    body: Annotated[dict[str, Any], Body()],
    session: CoreSessionDep,
    target: Annotated[Target, Depends(require("change"))],
):
    """Update only the columns sent in the body; read-only and excluded columns are ignored."""
    row = await _get_row(target, session, pk)
    before = _dump(target.info, row)
    for name, value in _validated(target.info.update_model, body, target.info).items():
        setattr(row, name, value)
    try:
        await session.flush()
        changes = diff(before, _dump(target.info, row))
        if changes:
            record(session, **_who(target, row), action="change", changes=changes)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _integrity(exc) from exc
    await session.refresh(row)
    return _dump(target.info, row)


@router.delete(
    "/{app}/{model}/{pk}",
    status_code=status.HTTP_204_NO_CONTENT,
    skip_envelope=True,
    summary="Delete a row",
    log_as="{username} deleted {app}.{model} #{pk}",
)
async def delete_row(pk: str, session: CoreSessionDep, target: Annotated[Target, Depends(require("delete"))]):
    row = await _get_row(target, session, pk)
    record(session, **_who(target, row), action="delete", changes=diff(_dump(target.info, row), None))
    await session.delete(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _integrity(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
