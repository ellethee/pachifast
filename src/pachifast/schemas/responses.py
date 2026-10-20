from logging import getLogger
from typing import Any, ClassVar, Generic, TypeVar

from fastapi import Query
from fastapi_pagination.bases import AbstractPage, AbstractParams, RawParams
from pydantic import BaseModel, model_validator

from pachifast.schemas.aliased import AliasedBaseModel

log = getLogger(__name__)
T = TypeVar("T")


def _coerce_csv_list(v: Any) -> Any:
    """Normalize a list with possible CSV strings into a flat list.

    Allows both ?x=1&x=2 (repeated) and ?x=1,2,3 (CSV) as a query param.
    ['1,2', '3'] → ['1', '2', '3']
    """
    if not isinstance(v, list):
        return v
    out: list = []
    for item in v:
        if isinstance(item, str) and "," in item:
            out.extend(i.strip() for i in item.split(",") if i.strip())
        else:
            out.append(item)
    return out


def ListQuery(default: Any = None, **kwargs: Any) -> Any:
    """Wrapper for Query() on List[T] fields.

    Accepts both repeated parameters (?status=1&status=2) and CSV (?status=1,2,3)
    when used together with BeforeValidator(_coerce_csv_list):

        from typing import Annotated
        from pydantic import BeforeValidator
        from pachifast.schemas.responses import ListQuery, _coerce_csv_list

        class MyParams(PageParams):
            status: Annotated[Optional[List[int]], BeforeValidator(_coerce_csv_list)] = ListQuery(
                None, description="Filter by status"
            )
    """
    return Query(default, **kwargs)


class StandardResponse(BaseModel, Generic[T]):
    success: bool = True
    message: str | None = None
    database: list[str] | None = None
    data: T | None = None


class PageParams(AliasedBaseModel):
    # the upper bounds keep limit/offset inside a 64-bit integer (a larger OFFSET is a database error, not a 4xx)
    page: int | None = Query(None, ge=1, le=1_000_000_000, description="Page number")
    pageSize: int | None = Query(None, ge=0, le=1_000_000, description="Number of items per page")
    orderBy: str | None = Query(None, description="Field to order by")
    search: str | None = Query(None, description="Search term to filter the results")

    search_fields: ClassVar[list[str]] = []
    #: columns a client may sort by; ``None`` means every mapped column
    order_fields: ClassVar[list[str] | None] = None


class PageWindow(AbstractParams):
    """The requested window (``page`` and ``pageSize``) in the form fastapi-pagination understands.

    Rows are paginated only when both values are set; otherwise the window is unbounded.
    """

    def __init__(self, page: int | None = None, page_size: int | None = None) -> None:
        self.page = page
        self.page_size = page_size

    def to_raw_params(self) -> RawParams:
        """Return the ``limit``/``offset`` pair; both ``None`` means every row."""
        if self.page and self.page_size:
            return RawParams(limit=self.page_size, offset=(self.page - 1) * self.page_size)
        return RawParams()


class PaginatedResponse(AbstractPage[T], Generic[T]):
    """The list contract: ``{items, total, page, pageSize, pages}``.

    A fastapi-pagination page, so the engine (count, limit/offset, sync or async) is shared with the
    library while the wire format stays the documented one.
    """

    items: list[T]
    total: int
    page: int | None = None
    pageSize: int | None = None
    pages: int = 0

    __params_type__ = PageWindow

    @model_validator(mode="after")
    def _fill_pages(self) -> "PaginatedResponse[T]":
        """Derive ``pages`` from ``total`` and ``pageSize`` unless it was given.

        Without paging every row is in one page; an empty result has no pages.
        """
        if "pages" not in self.model_fields_set:
            if not self.total:
                self.pages = 0
            elif self.page and self.pageSize:
                self.pages = -(-self.total // self.pageSize)
            else:
                self.pages = 1
        return self

    @classmethod
    def create(cls, items: Any, params: AbstractParams, *, total: int | None = None, **kwargs: Any) -> Any:
        """Build the page from the rows and the total computed by fastapi-pagination."""
        window = params if isinstance(params, PageWindow) else PageWindow()
        return cls(
            items=items,
            total=total if total is not None else len(items),
            page=window.page,
            pageSize=window.page_size,
        )
