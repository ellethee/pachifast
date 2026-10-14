"""The list contract: ``paginate()``/``paginate_sync()`` on top of fastapi-pagination."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi import Query
from sqlalchemy import String, create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool

from pachifast.database import Base
from pachifast.database.pagination import as_paginated_response, paginate, paginate_sync
from pachifast.errors import InvalidOrderByError
from pachifast.schemas.responses import PageParams, PaginatedResponse


class PagedInvoice(Base):
    """Throwaway table for the pagination tests."""

    __tablename__ = "test_paged_invoice"

    id: Mapped[int | None] = mapped_column(primary_key=True, default=None, nullable=False)
    number: Mapped[str] = mapped_column(String(30))
    customer_id: Mapped[int]
    status: Mapped[str] = mapped_column(String(10))


class InvoiceParams(PageParams):
    """List params with one searchable column and two exact filters."""

    search_fields = ["number"]
    customer_id: int | None = Query(None)
    status: str | None = Query(None)


def _rows() -> list[PagedInvoice]:
    return [
        PagedInvoice(number=f"INV-{i:03d}", customer_id=i % 3 + 1, status="paid" if i % 2 else "open")
        for i in range(25)
    ]


@pytest.fixture
async def async_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        session.add_all(_rows())
        await session.commit()
        yield session
    await engine.dispose()


@pytest.fixture
def sync_session() -> Iterator[Session]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        session.add_all(_rows())
        session.commit()
        yield session
    engine.dispose()


def _params(**values: Any) -> InvoiceParams:
    return InvoiceParams.model_validate(values)


async def test_page_and_page_size(async_session: AsyncSession) -> None:
    result = await paginate(async_session, PagedInvoice, _params(page=2, pageSize=10, orderBy="number"))
    assert isinstance(result, PaginatedResponse)
    assert [i.number for i in result.items][:2] == ["INV-010", "INV-011"]
    assert (len(result.items), result.total, result.page, result.pageSize) == (10, 25, 2, 10)
    assert result.model_dump().keys() == {"items", "total", "page", "pageSize", "pages"}
    assert result.pages == 3


async def test_without_paging_returns_everything_and_echoes_null(async_session: AsyncSession) -> None:
    result = await paginate(async_session, PagedInvoice, _params())
    assert (len(result.items), result.total, result.page, result.pageSize, result.pages) == (25, 25, None, None, 1)


@pytest.mark.parametrize("values", [{"page": 1, "pageSize": 0}, {"page": 2}, {"pageSize": 10}])
async def test_partial_or_zero_paging_returns_everything(async_session: AsyncSession, values: dict[str, int]) -> None:
    result = await paginate(async_session, PagedInvoice, _params(**values))
    assert (len(result.items), result.total, result.pages) == (25, 25, 1)
    assert (result.page, result.pageSize) == (values.get("page"), values.get("pageSize"))


async def test_multiple_order_with_descending_prefix(async_session: AsyncSession) -> None:
    result = await paginate(async_session, PagedInvoice, _params(page=1, pageSize=3, orderBy="-customerId,number"))
    assert [i.number for i in result.items] == ["INV-002", "INV-005", "INV-008"]


async def test_search_and_exact_filter_change_the_total(async_session: AsyncSession) -> None:
    searched = await paginate(async_session, PagedInvoice, _params(search="INV-01", page=1, pageSize=5))
    assert (len(searched.items), searched.total) == (5, 10)
    filtered = await paginate(async_session, PagedInvoice, _params(status="paid", page=1, pageSize=50))
    assert filtered.total == 12


async def test_as_model_returns_a_plain_list(async_session: AsyncSession) -> None:
    rows = await paginate(async_session, PagedInvoice, _params(status="open"), as_model=True)
    assert isinstance(rows, list) and len(rows) == 13


async def test_unknown_order_field_is_rejected(async_session: AsyncSession) -> None:
    with pytest.raises(InvalidOrderByError):
        await paginate(async_session, PagedInvoice, _params(orderBy="nope"))


def test_sync_session_gives_the_same_contract(sync_session: Session) -> None:
    result = paginate_sync(sync_session, PagedInvoice, _params(page=3, pageSize=10, orderBy="number"))
    assert isinstance(result, PaginatedResponse)
    assert (len(result.items), result.total, result.page, result.pageSize) == (5, 25, 3, 10)
    assert paginate_sync(sync_session, PagedInvoice, _params(status="paid"), as_model=True).__len__() == 12


async def test_pages_rounds_up_and_is_zero_when_empty(async_session: AsyncSession) -> None:
    assert (await paginate(async_session, PagedInvoice, _params(page=1, pageSize=10))).pages == 3
    assert (await paginate(async_session, PagedInvoice, _params(page=1, pageSize=25))).pages == 1
    empty = await paginate(async_session, PagedInvoice, _params(status="none", page=1, pageSize=10))
    assert (empty.total, empty.pages, empty.items) == (0, 0, [])


def test_in_memory_pages_match_the_database_ones() -> None:
    page = as_paginated_response(list(range(25)), PageParams.model_validate({"page": 3, "pageSize": 10}))
    assert (page.items, page.total, page.page, page.pageSize, page.pages) == ([20, 21, 22, 23, 24], 25, 3, 10, 3)
    assert as_paginated_response([1, 2, 3], PageParams()).items == [1, 2, 3]
