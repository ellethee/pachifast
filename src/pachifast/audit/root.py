import uuid
from datetime import datetime
from typing import Annotated

from fastapi import Depends, Query
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import String, cast, select

from pachifast.database.engine import CoreSessionDep
from pachifast.database.pagination import bound_page_params, paginate
from pachifast.routing.router_factory import get_router
from pachifast.schemas.responses import PageParams, PaginatedResponse

from .models import ActivityLog

router = get_router(name="admin/audit", require_superuser=True)


class ActivityLogRead(BaseModel):
    """An activity log entry as returned by the API (snake_case keys, as always)."""

    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    timestamp: datetime
    user_id: uuid.UUID | None = None
    username: str | None = None
    method: str
    path: str
    status_code: int
    duration_ms: int | None = None
    description: str | None = None


class ActivityLogPageParams(PageParams):
    search_fields = ["username", "path"]

    orderBy: str | None = Query("-timestamp", description="Campo per l'ordinamento (default: -timestamp)")
    user_id: uuid.UUID | None = Query(None, description="Filtra per ID utente")
    method: str | None = Query(None, description="Metodo HTTP: POST, PUT, PATCH, DELETE")
    status_code: int | None = Query(None, description="Filtra per status code HTTP")
    date_from: datetime | None = Query(None, description="Timestamp minimo (incluso)")
    date_to: datetime | None = Query(None, description="Timestamp massimo (incluso)")
    path_contains: str | None = Query(None, description="Filtra percorsi che contengono la stringa")
    exclude_paths: str | None = Query(
        None,
        description="Pattern da escludere (virgola-separati). Assente: il setting activity_log_exclude_paths. "
        "Stringa vuota per disabilitare.",
    )

    @model_validator(mode="after")
    def _apply_settings_default(self) -> "ActivityLogPageParams":
        if self.exclude_paths is None:
            from pachifast.config import get_settings

            self.exclude_paths = get_settings().activity_log_exclude_paths or None
        return self


class ActivityFilters(ActivityLogPageParams):
    """Filters of ``/activity``: the same as ``/logs``."""


def _activity_log_filter(params: ActivityLogPageParams, filter_kwargs: dict, stmt, model):
    filter_kwargs.pop("date_from", None)
    filter_kwargs.pop("date_to", None)
    filter_kwargs.pop("path_contains", None)
    filter_kwargs.pop("exclude_paths", None)
    if params.date_from:
        stmt = stmt.where(model.timestamp >= params.date_from)
    if params.date_to:
        stmt = stmt.where(model.timestamp <= params.date_to)
    if params.path_contains:
        stmt = stmt.where(model.path.contains(params.path_contains, autoescape=True))
    if params.exclude_paths:
        for pattern in params.exclude_paths.split(","):
            pattern = pattern.strip()
            if pattern:
                stmt = stmt.where(~model.path.contains(pattern, autoescape=True))
    return filter_kwargs, stmt


@router.get("/logs", response_model=PaginatedResponse[ActivityLogRead], summary="Lista log attività")
async def list_activity_logs(
    session: CoreSessionDep,
    params: Annotated[ActivityLogPageParams, Query()],
):
    return await paginate(
        session,
        ActivityLog,
        bound_page_params(params),
        extra_filters=[_activity_log_filter],
    )


@router.get("/activity", response_model=list[ActivityLogRead], summary="Attività recenti")
async def recent_activity(
    session: CoreSessionDep,
    filters: Annotated[ActivityFilters, Depends()],
    limit: int = Query(10, ge=1, le=500, description="Numero di record da restituire"),
):
    from pachifast.database.helpers import get_order_by_clauses

    stmt = select(ActivityLog)

    # filtri range, path_contains, exclude_paths
    _, stmt = _activity_log_filter(filters, filters.model_dump(exclude_none=True), stmt, ActivityLog)

    # full-text search su username e path
    if filters.search:
        term = f"%{filters.search}%"
        stmt = stmt.where(cast(ActivityLog.username, String).ilike(term) | cast(ActivityLog.path, String).ilike(term))

    # filtri esatti
    if filters.user_id is not None:
        stmt = stmt.where(ActivityLog.user_id == filters.user_id)
    if filters.method is not None:
        stmt = stmt.where(ActivityLog.method == filters.method)
    if filters.status_code is not None:
        stmt = stmt.where(ActivityLog.status_code == filters.status_code)

    clauses = get_order_by_clauses(ActivityLog, filters.orderBy or "-timestamp")
    if clauses:
        stmt = stmt.order_by(*clauses)
    stmt = stmt.limit(limit)

    result = await session.execute(stmt)
    return list(result.scalars().all())
