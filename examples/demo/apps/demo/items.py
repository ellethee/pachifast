"""Sample records for pachi-table2 / edit-dialog: list, create, update, delete and the bulk operations."""

from datetime import datetime
from typing import Annotated

from fastapi import Body, Query, status
from sqlalchemy import select

from pachifast.database import AliasedBaseModel
from pachifast.database.engine import CoreSessionDep
from pachifast.database.pagination import paginate
from pachifast.i18n import api_error
from pachifast.routing.router_factory import get_router
from pachifast.schemas.responses import PageParams, PaginatedResponse

from .models import DemoItem

router = get_router(name="demo/items")


class ItemPageParams(PageParams):
    """``page``, ``pageSize``, ``orderBy`` (``name,-price``), ``search`` (name, notes) plus these exact filters."""

    search_fields = ["name", "notes"]

    category: str | None = Query(None, description="Exact category")
    status: str | None = Query(None, description="draft, active or archived")
    active: bool | None = Query(None, description="Active flag")
    country_id: int | None = Query(None, description="Country id")
    city_id: int | None = Query(None, description="City id")


class ItemWrite(AliasedBaseModel):
    """Body of create and update (the table sends the whole record on update; unknown keys are ignored)."""

    name: str
    category: str
    status: str = "draft"
    price: float = 0.0
    quantity: int = 0
    active: bool = True
    country_id: int | None = None
    city_id: int | None = None
    notes: str | None = None


class ItemRead(ItemWrite):
    """An item as returned by the API."""

    id: int
    created_at: datetime


class BulkUpdate(AliasedBaseModel):
    """Body of bulk-update: ``{"items": [{"id": 1, ...}]}``."""

    items: list[dict]


async def _get(session: CoreSessionDep, item_id: int) -> DemoItem:
    item = await session.get(DemoItem, item_id)
    if item is None:
        raise api_error(status.HTTP_404_NOT_FOUND, "demo.item_not_found", loc=("path", "id"), id=item_id)
    return item


@router.get("", response_model=PaginatedResponse[ItemRead], permissions=["demo.view"], summary="List items")
async def list_items(session: CoreSessionDep, params: Annotated[ItemPageParams, Query()]):
    """Paginated list: ``{items, total, page, pageSize}``; default order: newest first."""
    if not params.orderBy:
        params.orderBy = "-id"
    return await paginate(session, DemoItem, params)


@router.post("", response_model=ItemRead, status_code=201, permissions=["demo.edit"], summary="Create an item")
async def create_item(body: ItemWrite, session: CoreSessionDep):
    """Create a record (also reachable with a trailing slash)."""
    item = DemoItem(**body.model_dump(by_alias=False))
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


@router.post("/", response_model=ItemRead, status_code=201, permissions=["demo.edit"], include_in_schema=False)
async def create_item_slash(body: ItemWrite, session: CoreSessionDep):
    """Same as ``POST ""``: pachi-table2 posts to ``{url}/``."""
    return await create_item(body, session)


@router.post("/bulk-delete", permissions=["demo.delete"], summary="Delete several items")
async def bulk_delete(ids: Annotated[list[int], Body()], session: CoreSessionDep):
    """Body: a raw JSON array of ids. Returns how many were deleted; ids that do not exist are ignored."""
    rows = (await session.execute(select(DemoItem).where(DemoItem.id.in_(ids)))).scalars().all()
    for row in rows:
        await session.delete(row)
    await session.commit()
    return {"deleted": len(rows)}


@router.post("/bulk-update", permissions=["demo.edit"], summary="Update several items")
async def bulk_update(body: BulkUpdate, session: CoreSessionDep):
    """Body: ``{"items": [{"id": 1, "status": "active"}]}``; only the keys present are changed."""
    updated = 0
    names = {field.alias or name: name for name, field in ItemWrite.model_fields.items()}
    names.update({name: name for name in ItemWrite.model_fields})  # camelCase or snake_case keys
    for fields in body.items:
        item = await _get(session, int(fields.get("id", 0)))
        for key, value in fields.items():
            if key in names:
                setattr(item, names[key], value)
        updated += 1
    await session.commit()
    return {"updated": updated}


@router.get("/{item_id}", response_model=ItemRead, permissions=["demo.view"], summary="Get an item")
async def get_item(item_id: int, session: CoreSessionDep):
    return await _get(session, item_id)


@router.put("/{item_id}", response_model=ItemRead, permissions=["demo.edit"], summary="Replace an item")
async def update_item(item_id: int, body: ItemWrite, session: CoreSessionDep):
    """Full update: the table sends the whole record."""
    item = await _get(session, item_id)
    for key, value in body.model_dump(by_alias=False).items():
        setattr(item, key, value)
    await session.commit()
    await session.refresh(item)
    return item


@router.delete("/{item_id}", status_code=204, skip_envelope=True, permissions=["demo.delete"], summary="Delete an item")
async def delete_item(item_id: int, session: CoreSessionDep):
    item = await _get(session, item_id)
    await session.delete(item)
    await session.commit()
