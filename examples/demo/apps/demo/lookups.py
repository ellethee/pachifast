"""Options for the remote selects of pachi-edit-dialog-fields: ``{items, total, page, pageSize}``.

The component only sends ``pageSize``; the dependent select (cities of a country) is a plain extra parameter,
``?countryId=2``, set by the app when the country changes.
"""

from fastapi import Query

from pachifast.routing.router_factory import get_router

from . import data

router = get_router(name="demo/lookups")


def _page(items: list[dict], page_size: int) -> dict:
    return {"items": items[:page_size], "total": len(items), "page": 1, "pageSize": page_size}


@router.get("/countries", summary="Countries")
async def countries(pageSize: int = Query(1000, ge=1)):
    return _page(data.COUNTRIES, pageSize)


@router.get("/cities", summary="Cities, optionally of one country")
async def cities(countryId: int | None = Query(None), pageSize: int = Query(1000, ge=1)):
    rows = [c for c in data.CITIES if countryId is None or c["countryId"] == countryId]
    return _page(rows, pageSize)


@router.get("/categories", summary="Item categories")
async def categories(pageSize: int = Query(1000, ge=1)):
    return _page(data.CATEGORIES, pageSize)


@router.get("/statuses", summary="Item statuses")
async def statuses(pageSize: int = Query(1000, ge=1)):
    return _page(data.STATUSES, pageSize)
