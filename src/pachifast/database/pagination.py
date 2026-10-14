from logging import getLogger
from typing import TypeVar

from fastapi_pagination.api import set_page
from fastapi_pagination.ext.sqlalchemy import apaginate
from fastapi_pagination.ext.sqlalchemy import paginate as sync_paginate
from sqlalchemy import Select, String, cast, inspect, or_, select
from sqlalchemy.orm import Session, defer

from pachifast.database.engine import SessionDep
from pachifast.schemas.responses import PageParams, PageWindow, PaginatedResponse, T

from .helpers import get_order_by_clauses, pop_non_fields_from_dict_by_model
from .types import ExtraFilter

P = TypeVar("P", bound=PageParams)

log = getLogger(__name__)

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 200


def bound_page_params(params: P, default: int = DEFAULT_PAGE_SIZE, maximum: int = MAX_PAGE_SIZE) -> P:
    """Give a list request a window: ``page`` 1 and ``pageSize`` ``default`` when missing, at most ``maximum`` rows.

    :func:`paginate` itself leaves a request without ``page``/``pageSize`` unbounded, which suits the lists of a
    project that knows its tables; the routes of pachifast over tables that only grow (users, activity log) use this.
    """
    params.page = params.page or 1
    params.pageSize = min(params.pageSize or default, maximum)
    return params


def as_dict_list(models):
    return [{c.key: getattr(m, c.key) for c in inspect(type(m)).mapper.column_attrs} for m in models]


def build_statement(
    model: type[T],
    params: PageParams,
    extra_filters: list[ExtraFilter] | None = None,
    defer_fields: list[str] | None = None,
    select_options: list | None = None,
) -> Select:
    """Build the filtered, searched and ordered ``SELECT`` for a list endpoint (no paging)."""
    # 1. exact filters from the params (excludes page, pageSize, orderBy, search)
    filter_kwargs = params.model_dump(
        exclude={"page", "pageSize", "orderBy", "search"}, exclude_none=True, by_alias=False
    )
    extra_filters = extra_filters or []
    defer_fields = defer_fields or []
    select_options = select_options or []
    stmt = select(model)

    # defer heavy columns + any eager loads
    defer_options = [defer(getattr(model, field), raiseload=True) for field in defer_fields if hasattr(model, field)]
    all_options = defer_options + select_options
    if all_options:
        stmt = stmt.options(*all_options)

    for ef in extra_filters:
        filter_kwargs, stmt = ef(params, filter_kwargs, stmt, model)
    filter_kwargs = pop_non_fields_from_dict_by_model(model, filter_kwargs)
    stmt = stmt.filter_by(**filter_kwargs)

    # 2. text search, if requested
    if params.search and getattr(params, "search_fields", None):
        clauses = []
        for field in params.search_fields:
            if hasattr(model, field):
                column = getattr(model, field)
                clauses.append(cast(column, String).icontains(params.search, autoescape=True))
        if clauses:
            stmt = stmt.filter(or_(*clauses))

    # 3. ordering
    order_by_clauses = get_order_by_clauses(model, params.orderBy, getattr(params, "order_fields", None))
    if order_by_clauses:
        stmt = stmt.order_by(*order_by_clauses)
    return stmt


async def paginate(
    session: SessionDep,
    model: type[T],
    params: PageParams,
    extra_filters: list[ExtraFilter] | None = None,
    as_model: bool = False,
    defer_fields: list[str] | None = None,
    select_options: list | None = None,
) -> PaginatedResponse[T] | list[T]:
    """Run a list query on an ``AsyncSession``: filters, search, ordering, count and paging.

    Counting and limit/offset are delegated to fastapi-pagination; the query parameters and the
    ``{items, total, page, pageSize}`` format are pachifast's. With ``as_model=True`` the rows are
    returned as a plain list (no paging, no count).
    """
    stmt = build_statement(model, params, extra_filters, defer_fields, select_options)
    if as_model:
        result = await session.execute(stmt)
        return list(result.scalars().all())
    window = PageWindow(params.page, params.pageSize)
    with set_page(PaginatedResponse):
        return await apaginate(session, stmt, window)


def paginate_sync(
    session: Session,
    model: type[T],
    params: PageParams,
    extra_filters: list[ExtraFilter] | None = None,
    as_model: bool = False,
    defer_fields: list[str] | None = None,
    select_options: list | None = None,
) -> PaginatedResponse[T] | list[T]:
    """Same as :func:`paginate` for a synchronous SQLAlchemy ``Session``."""
    stmt = build_statement(model, params, extra_filters, defer_fields, select_options)
    if as_model:
        return list(session.execute(stmt).scalars().all())
    window = PageWindow(params.page, params.pageSize)
    with set_page(PaginatedResponse):
        return sync_paginate(session, stmt, window)


def as_paginated_response(items: list[T], params: PageParams) -> PaginatedResponse[T]:
    total = len(items)
    paginated_items = items
    raw = PageWindow(params.page, params.pageSize).to_raw_params()
    if raw.limit:
        paginated_items = items[raw.offset : raw.offset + raw.limit]
    return PaginatedResponse(
        items=paginated_items,
        total=total,
        page=params.page,
        pageSize=params.pageSize,
    )
